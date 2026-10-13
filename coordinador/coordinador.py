import argparse
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request
from flasgger import Swagger

# http://100.90.6.28:4000/apidocs/
# python coordinador/coordinador.py --ip 100.90.6.28 --puerto 4000
#
# Subir, listar y borrar desde la terminal:
#   curl -X PUT --data-binary @archivo.pdf http://100.90.6.28:4000/api/files/archivo.pdf
#   curl http://100.90.6.28:4000/api/files
#   curl -X DELETE http://100.90.6.28:4000/api/files/archivo.pdf

app = Flask(__name__)

swagger = Swagger(
    app,
    template={
        "info": {
            "title": "API del Coordinador",
            "description": (
                "Coordina los nodos del sistema distribuido: consulta su "
                "disponibilidad, reparte los archivos y mantiene las réplicas."
            ),
            "version": "1.0.0",
        },
        "schemes": ["http"],
    },
)

NODES_FILE = Path(__file__).with_name("nodes.json")
METADATA_FILE = Path(__file__).with_name("metadata.json")
REQUEST_TIMEOUT = float(os.getenv("NODE_REQUEST_TIMEOUT", "2"))
UPLOAD_TIMEOUT = float(os.getenv("NODE_UPLOAD_TIMEOUT", "30"))
REPLICAS = int(os.getenv("REPLICAS", "2"))

# Protege metadata.json: Flask atiende varias peticiones a la vez
metadata_lock = threading.Lock()


def load_nodes():
    with NODES_FILE.open(encoding="utf-8") as nodes_file:
        nodes = json.load(nodes_file)

    if not isinstance(nodes, list):
        raise ValueError("nodes.json debe contener una lista de nodos")

    return nodes


def load_metadata():
    if not METADATA_FILE.exists():
        return {}
    with METADATA_FILE.open(encoding="utf-8") as metadata_file:
        return json.load(metadata_file)


def save_metadata(metadata):
    # Se escribe en un archivo temporal y luego se reemplaza, para no dejar
    # metadata.json a medio escribir si el coordinador se cae
    temp_file = METADATA_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2, ensure_ascii=False)
    os.replace(temp_file, METADATA_FILE)


def node_file_url(node, nombre):
    return f"{node['url'].rstrip('/')}/files/{quote(nombre)}"


def check_node(node):
    """Consulta GET /health de un nodo y devuelve su estado."""
    node_id = node.get("id")
    node_url = node.get("url")

    if not node_id or not node_url:
        return {"id": node_id, "status": "error", "error": "Configuración inválida"}

    try:
        response = requests.get(
            f"{node_url.rstrip('/')}/health",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return {"id": node_id, "status": "ok", "response": response.json()}
    except (requests.RequestException, ValueError) as error:
        return {"id": node_id, "status": "error", "error": str(error)}


def check_nodes(nodes):
    """Consulta todos los nodos en paralelo (máximo REQUEST_TIMEOUT en total)."""
    if not nodes:
        return []
    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        return list(pool.map(check_node, nodes))


def files_per_node(metadata):
    counts = {}
    for info in metadata.values():
        for node_id in info["nodes"]:
            counts[node_id] = counts.get(node_id, 0) + 1
    return counts


def choose_nodes(nodes, metadata):
    """Balanceador: nodos vivos ordenados por cantidad de archivos (menos primero).

    Devuelve todos los candidatos en ese orden; quien llama usa los primeros
    REPLICAS y, si alguno falla al subir, sigue con el siguiente.
    """
    statuses = check_nodes(nodes)
    alive_ids = {status["id"] for status in statuses if status["status"] == "ok"}
    counts = files_per_node(metadata)
    order = {node["id"]: index for index, node in enumerate(nodes)}

    candidates = [node for node in nodes if node.get("id") in alive_ids]
    # Empate en cantidad de archivos → el que tenga menos bytes → orden de nodes.json
    candidates.sort(
        key=lambda node: (
            counts.get(node["id"], 0),
            sum(
                info["size"]
                for info in metadata.values()
                if node["id"] in info["nodes"]
            ),
            order[node["id"]],
        )
    )
    return candidates, alive_ids


@app.get("/health")
def health():
    """
    Consulta el estado del coordinador y de todos los nodos configurados.
    ---
    tags:
      - Estado
    produces:
      - application/json
    responses:
      200:
        description: Estado consolidado del coordinador y los nodos.
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
            service:
              type: string
              example: coordinador
            nodes:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                    example: nodo1
                  status:
                    type: string
                    example: ok
                  response:
                    type: object
      500:
        description: No fue posible leer o interpretar nodes.json.
    """
    try:
        nodes = load_nodes()
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return jsonify(
            {
                "status": "error",
                "service": "coordinador",
                "error": f"No fue posible leer nodes.json: {error}",
                "nodes": [],
            }
        ), 500

    nodes_status = check_nodes(nodes)

    all_healthy = bool(nodes_status) and all(
        node["status"] == "ok" for node in nodes_status
    )

    return jsonify(
        {
            "status": "ok" if all_healthy else "degraded",
            "service": "coordinador",
            "nodes": nodes_status,
        }
    ), 200


@app.put("/api/files/<nombre>")
def upload_file(nombre):
    """
    Sube un archivo y lo replica en REPLICAS nodos (por defecto 2).
    Elige los nodos vivos con menos archivos para mantener la carga pareja.
    ---
    tags:
      - Archivos
    consumes:
      - application/octet-stream
    parameters:
      - name: nombre
        in: path
        type: string
        required: true
      - name: body
        in: body
        required: true
        description: Contenido del archivo (bytes).
        schema:
          type: string
          format: binary
    responses:
      201:
        description: Archivo guardado. Devuelve los nodos donde quedó.
        schema:
          type: object
          properties:
            name:
              type: string
              example: informe.pdf
            size:
              type: integer
              example: 250000
            sha256:
              type: string
            nodes:
              type: array
              items:
                type: string
              example: ["nodo1", "nodo3"]
      400:
        description: Nombre de archivo inválido.
      409:
        description: Ya existe un archivo con ese nombre.
      503:
        description: No hay nodos disponibles o ninguno pudo guardar el archivo.
    """
    nombre = os.path.basename(nombre)
    if nombre in ("", ".", ".."):
        return jsonify({"error": "Nombre de archivo inválido"}), 400

    datos = request.get_data()
    sha256 = hashlib.sha256(datos).hexdigest()

    with metadata_lock:
        metadata = load_metadata()
        if nombre in metadata:
            return jsonify(
                {
                    "error": f"El archivo {nombre} ya existe",
                    "nodes": metadata[nombre]["nodes"],
                }
            ), 409

        candidates, _ = choose_nodes(load_nodes(), metadata)
        if not candidates:
            return jsonify({"error": "No hay nodos disponibles"}), 503

        saved = []
        failed = []
        # Se recorren los candidatos en orden hasta lograr REPLICAS copias:
        # si uno falla al subir, se intenta con el siguiente
        for node in candidates:
            if len(saved) == REPLICAS:
                break
            try:
                response = requests.put(
                    node_file_url(node, nombre),
                    data=datos,
                    timeout=UPLOAD_TIMEOUT,
                )
                response.raise_for_status()
                if response.json().get("sha256") != sha256:
                    raise ValueError("el sha256 del nodo no coincide")
                saved.append(node["id"])
            except (requests.RequestException, ValueError) as error:
                failed.append({"id": node["id"], "error": str(error)})

        if not saved:
            return jsonify(
                {"error": "Ningún nodo pudo guardar el archivo", "failed": failed}
            ), 503

        metadata[nombre] = {
            "size": len(datos),
            "sha256": sha256,
            "nodes": saved,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        save_metadata(metadata)

    print(f"{nombre} guardado en {', '.join(saved)}")
    if len(saved) < REPLICAS:
        print(
            f"ADVERTENCIA: {nombre} quedó en {len(saved)} nodo(s), "
            f"se esperaban {REPLICAS}"
        )

    return jsonify(
        {
            "name": nombre,
            "size": len(datos),
            "sha256": sha256,
            "nodes": saved,
            "failed": failed,
        }
    ), 201


@app.get("/api/files")
def list_files():
    """
    Lista los archivos con sus nodos y si cada nodo está vivo.
    También muestra cuántos archivos tiene cada nodo (carga del balanceador).
    ---
    tags:
      - Archivos
    produces:
      - application/json
    responses:
      200:
        description: Archivos registrados en metadata.json.
        schema:
          type: object
          properties:
            files:
              type: array
              items:
                type: object
                properties:
                  name:
                    type: string
                    example: informe.pdf
                  size:
                    type: integer
                  sha256:
                    type: string
                  uploaded_at:
                    type: string
                  available:
                    type: boolean
                  nodes:
                    type: array
                    items:
                      type: object
                      properties:
                        id:
                          type: string
                          example: nodo1
                        alive:
                          type: boolean
            load:
              type: object
              example: {"nodo1": 2, "nodo2": 1, "nodo3": 1}
    """
    nodes = load_nodes()
    with metadata_lock:
        metadata = load_metadata()

    statuses = check_nodes(nodes)
    alive_ids = {status["id"] for status in statuses if status["status"] == "ok"}
    counts = files_per_node(metadata)

    files = []
    for nombre, info in sorted(metadata.items()):
        file_nodes = [
            {"id": node_id, "alive": node_id in alive_ids}
            for node_id in info["nodes"]
        ]
        files.append(
            {
                "name": nombre,
                "size": info["size"],
                "sha256": info["sha256"],
                "uploaded_at": info.get("uploaded_at"),
                "available": any(node["alive"] for node in file_nodes),
                "nodes": file_nodes,
            }
        )

    return jsonify(
        {
            "files": files,
            "load": {node["id"]: counts.get(node["id"], 0) for node in nodes},
        }
    )


@app.delete("/api/files/<nombre>")
def delete_file(nombre):
    """
    Borra un archivo de todos los nodos donde está y lo quita de los metadatos.
    Si algún nodo no responde, el archivo queda registrado solo con esos nodos
    para poder repetir el DELETE cuando vuelvan.
    ---
    tags:
      - Archivos
    parameters:
      - name: nombre
        in: path
        type: string
        required: true
    responses:
      200:
        description: Resultado del borrado en cada nodo.
        schema:
          type: object
          properties:
            name:
              type: string
            deleted_from:
              type: array
              items:
                type: string
              example: ["nodo1", "nodo3"]
            pending:
              type: array
              items:
                type: object
            complete:
              type: boolean
      404:
        description: El archivo no existe.
    """
    nombre = os.path.basename(nombre)
    nodes_by_id = {node["id"]: node for node in load_nodes()}

    with metadata_lock:
        metadata = load_metadata()
        if nombre not in metadata:
            return jsonify({"error": f"El archivo {nombre} no existe"}), 404

        deleted_from = []
        pending = []
        for node_id in metadata[nombre]["nodes"]:
            node = nodes_by_id.get(node_id)
            if node is None:
                pending.append({"id": node_id, "error": "No está en nodes.json"})
                continue
            try:
                response = requests.delete(
                    node_file_url(node, nombre),
                    timeout=REQUEST_TIMEOUT,
                )
                # 404 en el nodo = ya no lo tenía, se cuenta como borrado
                if response.status_code != 404:
                    response.raise_for_status()
                deleted_from.append(node_id)
            except requests.RequestException as error:
                pending.append({"id": node_id, "error": str(error)})

        if pending:
            metadata[nombre]["nodes"] = [node["id"] for node in pending]
        else:
            del metadata[nombre]
        save_metadata(metadata)

    print(f"{nombre} borrado de {', '.join(deleted_from) or 'ningún nodo'}")
    if pending:
        print(
            f"ADVERTENCIA: {nombre} sigue en "
            f"{', '.join(node['id'] for node in pending)}"
        )

    return jsonify(
        {
            "name": nombre,
            "deleted_from": deleted_from,
            "pending": pending,
            "complete": not pending,
        }
    ), 200


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Inicia el coordinador de nodos"
    )
    parser.add_argument(
        "--ip",
        default="0.0.0.0",
        help="Interfaz donde escuchará el coordinador (por defecto: 0.0.0.0)",
    )
    parser.add_argument(
        "--puerto",
        type=int,
        required=True,
        help="Puerto donde escuchará el coordinador",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    print(f"Iniciando coordinador en {args.ip}, puerto {args.puerto}")
    print(f"Réplicas por archivo: {REPLICAS}")
    app.run(host=args.ip, port=args.puerto)
