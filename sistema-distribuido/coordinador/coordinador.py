import argparse
import hashlib
import json
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request, send_file
from flasgger import Swagger

# Gestor de archivos (usuario): http://100.90.6.28:4000/
# Panel del coordinador:        http://100.90.6.28:4000/panel
# http://100.90.6.28:4000/apidocs/
# python coordinador/coordinador.py --ip 100.90.6.28 --puerto 4000
#
# Subir, listar, descargar, borrar y sincronizar desde la terminal:
#   curl -X PUT --data-binary @archivo.pdf http://100.90.6.28:4000/api/files/archivo.pdf
#   curl http://100.90.6.28:4000/api/files
#   curl -i -o archivo.pdf http://100.90.6.28:4000/api/files/archivo.pdf
#   curl -X DELETE http://100.90.6.28:4000/api/files/archivo.pdf
#   curl -X POST http://100.90.6.28:4000/api/rebuild

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
USER_PAGE = Path(__file__).resolve().parent.parent / "cliente" / "index.html"
PANEL_FILE = Path(__file__).with_name("panel.html")
REQUEST_TIMEOUT = float(os.getenv("NODE_REQUEST_TIMEOUT", "2"))
UPLOAD_TIMEOUT = float(os.getenv("NODE_UPLOAD_TIMEOUT", "30"))
REPLICAS = int(os.getenv("REPLICAS", "2"))
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "3"))
SYNC_INTERVAL = float(os.getenv("SYNC_INTERVAL", "30"))

# Protege metadata.json: Flask atiende varias peticiones a la vez
metadata_lock = threading.Lock()

# Estado de los nodos en memoria: {id: {"alive", "last_seen", "hostname"}}
node_state = {}
# Últimos eventos, para mostrarlos en el panel
events = deque(maxlen=50)


def log_event(mensaje):
    linea = f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}"
    events.append(linea)
    print(linea)


def set_node_alive(node_id, alive, hostname=None):
    """Actualiza el estado del nodo y registra cuando pasa de ACTIVO a CAÍDO o al revés."""
    previous = node_state.get(node_id, {}).get("alive")
    state = node_state.setdefault(node_id, {})
    state["alive"] = alive
    if alive:
        state["last_seen"] = datetime.now(timezone.utc).isoformat()
        if hostname:
            state["hostname"] = hostname
    if previous is not None and previous != alive:
        log_event(f"{node_id} {'ACTIVO' if alive else 'CAÍDO'}")


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
        node_health = response.json()
        set_node_alive(node_id, True, node_health.get("hostname"))
        return {"id": node_id, "status": "ok", "response": node_health}
    except (requests.RequestException, ValueError) as error:
        set_node_alive(node_id, False)
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


def rank_nodes(candidates, nodes, metadata):
    """Ordena los nodos por cantidad de archivos (menos primero).

    Empate en cantidad de archivos → el que tenga menos bytes → orden de nodes.json.
    """
    counts = files_per_node(metadata)
    order = {node["id"]: index for index, node in enumerate(nodes)}
    return sorted(
        candidates,
        key=lambda node: (
            counts.get(node["id"], 0),
            sum(
                info["size"]
                for info in metadata.values()
                if node["id"] in info["nodes"]
            ),
            order[node["id"]],
        ),
    )


def known_alive_ids(nodes):
    """Nodos vivos según el último heartbeat; consulta en el momento si falta alguno."""
    if any(node["id"] not in node_state for node in nodes):
        check_nodes(nodes)
    return {
        node["id"] for node in nodes if node_state.get(node["id"], {}).get("alive")
    }


def choose_nodes(nodes, metadata):
    """Balanceador: nodos vivos ordenados por cantidad de archivos (menos primero).

    Devuelve todos los candidatos en ese orden; quien llama usa los primeros
    REPLICAS y, si alguno falla al subir, sigue con el siguiente.
    """
    statuses = check_nodes(nodes)
    alive_ids = {status["id"] for status in statuses if status["status"] == "ok"}
    candidates = [node for node in nodes if node.get("id") in alive_ids]
    return rank_nodes(candidates, nodes, metadata), alive_ids


@app.get("/")
def user_page():
    """Gestor de archivos para el usuario (cliente/index.html)."""
    return send_file(USER_PAGE)


@app.get("/panel")
def panel():
    """Panel del coordinador: nodos, réplicas y eventos (coordinador/panel.html)."""
    return send_file(PANEL_FILE)


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
            if metadata[nombre].get("deleting"):
                return jsonify(
                    {
                        "error": (
                            f"{nombre} tiene un borrado pendiente en "
                            f"{', '.join(metadata[nombre]['nodes'])}; "
                            "inténtelo cuando ese nodo vuelva"
                        ),
                    }
                ), 409
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

    log_event(f"{nombre} guardado en {', '.join(saved)}")
    if len(saved) < REPLICAS:
        log_event(
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

    alive_ids = known_alive_ids(nodes)
    counts = files_per_node(metadata)

    files = []
    for nombre, info in sorted(metadata.items()):
        if info.get("deleting"):
            continue
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


@app.get("/api/files/<nombre>")
def download_file(nombre):
    """
    Descarga un archivo desde alguna réplica disponible (failover).
    Prueba primero los nodos que se sabe que están vivos; si uno no responde,
    no tiene el archivo o lo tiene dañado, pasa a la siguiente réplica.
    El encabezado X-Served-By indica qué nodo respondió.
    ---
    tags:
      - Archivos
    produces:
      - application/octet-stream
    parameters:
      - name: nombre
        in: path
        type: string
        required: true
    responses:
      200:
        description: Contenido del archivo.
        headers:
          X-Served-By:
            type: string
            description: Nodo que entregó el archivo.
          X-Failed-Nodes:
            type: string
            description: Nodos que fallaron antes (separados por coma).
      404:
        description: El archivo no existe.
      503:
        description: Ninguna réplica está disponible.
    """
    nombre = os.path.basename(nombre)
    nodes_by_id = {node["id"]: node for node in load_nodes()}

    with metadata_lock:
        info = load_metadata().get(nombre)
    if info is None or info.get("deleting"):
        return jsonify({"error": f"El archivo {nombre} no existe"}), 404

    # Los nodos vivos primero; los que no sabemos o están caídos, después
    order = sorted(
        info["nodes"],
        key=lambda node_id: not node_state.get(node_id, {}).get("alive", True),
    )

    failed = []
    for node_id in order:
        node = nodes_by_id.get(node_id)
        if node is None:
            failed.append(node_id)
            continue
        try:
            response = requests.get(
                node_file_url(node, nombre),
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                log_event(f"{node_id} no tiene {nombre}, probando otra réplica")
                failed.append(node_id)
                continue
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != info["sha256"]:
                log_event(f"{node_id} tiene {nombre} dañado, probando otra réplica")
                failed.append(node_id)
                continue
        except requests.RequestException:
            log_event(f"{node_id} no respondió, probando otra réplica")
            set_node_alive(node_id, False)
            failed.append(node_id)
            continue

        set_node_alive(node_id, True)
        detalle = f" (fallaron antes: {', '.join(failed)})" if failed else ""
        log_event(f"{nombre} servido por {node_id}{detalle}")
        return Response(
            response.content,
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}",
                "X-Served-By": node_id,
                "X-Failed-Nodes": ",".join(failed),
            },
        )

    log_event(f"{nombre}: ninguna réplica disponible")
    return jsonify(
        {"error": f"Ninguna réplica de {nombre} está disponible", "failed": failed}
    ), 503


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
        if nombre not in metadata or metadata[nombre].get("deleting"):
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
            # Queda marcado para que la sincronización termine de borrarlo
            # cuando esos nodos vuelvan (y no lo re-replique)
            metadata[nombre]["nodes"] = [node["id"] for node in pending]
            metadata[nombre]["deleting"] = True
        else:
            del metadata[nombre]
        save_metadata(metadata)

    log_event(f"{nombre} borrado de {', '.join(deleted_from) or 'ningún nodo'}")
    if pending:
        log_event(
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


@app.get("/api/status")
def status():
    """
    Estado de cada nodo (según el heartbeat) y eventos recientes.
    ---
    tags:
      - Estado
    produces:
      - application/json
    responses:
      200:
        description: Nodos y eventos.
        schema:
          type: object
          properties:
            replicas:
              type: integer
              example: 2
            nodes:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                    example: nodo1
                  url:
                    type: string
                  alive:
                    type: boolean
                  hostname:
                    type: string
                  last_seen:
                    type: string
                  files:
                    type: integer
            events:
              type: array
              items:
                type: string
    """
    nodes = load_nodes()
    alive_ids = known_alive_ids(nodes)
    with metadata_lock:
        counts = files_per_node(load_metadata())

    return jsonify(
        {
            "replicas": REPLICAS,
            "nodes": [
                {
                    "id": node["id"],
                    "url": node["url"],
                    "alive": node["id"] in alive_ids,
                    "hostname": node_state.get(node["id"], {}).get("hostname"),
                    "last_seen": node_state.get(node["id"], {}).get("last_seen"),
                    "files": counts.get(node["id"], 0),
                }
                for node in nodes
            ],
            "events": list(reversed(events)),
        }
    )


def fetch_inventory(node):
    """Pide GET /files a un nodo. Devuelve {nombre: {size, sha256}} o None si falla."""
    try:
        response = requests.get(
            f"{node['url'].rstrip('/')}/files",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return {item["name"]: item for item in response.json()["files"]}
    except (requests.RequestException, ValueError, KeyError):
        return None


def copy_between_nodes(nombre, info, sources, targets, report):
    """Copia un archivo desde una réplica sana hacia otros nodos hasta tener REPLICAS."""
    datos = None
    for source in sources:
        try:
            response = requests.get(
                node_file_url(source, nombre), timeout=UPLOAD_TIMEOUT
            )
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() == info["sha256"]:
                datos, source_id = response.content, source["id"]
                break
        except requests.RequestException:
            continue
    if datos is None:
        return

    for target in targets:
        if len(info["nodes"]) >= REPLICAS:
            break
        try:
            response = requests.put(
                node_file_url(target, nombre), data=datos, timeout=UPLOAD_TIMEOUT
            )
            response.raise_for_status()
            if response.json().get("sha256") != info["sha256"]:
                continue
        except (requests.RequestException, ValueError):
            continue
        info["nodes"].append(target["id"])
        report["replicated"].append(
            {"name": nombre, "from": source_id, "to": target["id"]}
        )
        log_event(f"{nombre} re-replicado de {source_id} a {target['id']}")


def sync_metadata():
    """Compara metadata.json con lo que realmente tiene cada nodo y lo repara.

    - Archivos que un nodo tiene y metadata.json no conoce → se agregan.
    - Archivos que metadata.json dice que un nodo tiene y ya no están → se quita el nodo.
    - Mismo nombre con sha256 distinto → conflicto (esa copia no cuenta como réplica).
    - Archivos con menos de REPLICAS copias → se copian a otro nodo vivo.
    - Archivos que ya no están en ningún nodo → se quitan y se reportan como perdidos.
    - Borrados pendientes (el nodo estaba caído) → se terminan cuando el nodo vuelve.

    Los nodos caídos no se tocan: que no respondan no significa que perdieron sus archivos.
    """
    nodes = load_nodes()
    report = {
        "added": [],
        "removed": [],
        "conflicts": [],
        "lost": [],
        "replicated": [],
        "under_replicated": [],
        "unreachable": [],
        "deleted": [],
    }

    with metadata_lock:
        check_nodes(nodes)
        alive = [node for node in nodes if node_state.get(node["id"], {}).get("alive")]
        with ThreadPoolExecutor(max_workers=max(len(alive), 1)) as pool:
            results = list(pool.map(fetch_inventory, alive))
        inventories = {}
        for node, inventory in zip(alive, results):
            if inventory is None:
                report["unreachable"].append(node["id"])
            else:
                inventories[node["id"]] = inventory
        for node in nodes:
            if node["id"] not in inventories and node["id"] not in report["unreachable"]:
                report["unreachable"].append(node["id"])

        metadata = load_metadata()
        nodes_by_id = {node["id"]: node for node in nodes}

        # 0. Terminar borrados pendientes en los nodos que volvieron
        for nombre, info in list(metadata.items()):
            if not info.get("deleting"):
                continue
            for node_id in list(info["nodes"]):
                if node_id not in inventories:
                    continue
                try:
                    response = requests.delete(
                        node_file_url(nodes_by_id[node_id], nombre),
                        timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code != 404:
                        response.raise_for_status()
                except requests.RequestException:
                    continue
                info["nodes"].remove(node_id)
                inventories[node_id].pop(nombre, None)
                log_event(f"{nombre} borrado de {node_id} (borrado pendiente)")
            if not info["nodes"]:
                del metadata[nombre]
                report["deleted"].append(nombre)

        # 1. Lo que dicen los metadatos vs. lo que hay en cada nodo
        for nombre, info in metadata.items():
            if info.get("deleting"):
                continue
            for node_id in list(info["nodes"]):
                if node_id not in inventories:
                    continue
                real = inventories[node_id].get(nombre)
                if real is None:
                    info["nodes"].remove(node_id)
                    report["removed"].append({"name": nombre, "node": node_id})
                    log_event(f"{node_id} ya no tiene {nombre}")
                elif real["sha256"] != info["sha256"]:
                    info["nodes"].remove(node_id)
                    report["conflicts"].append({"name": nombre, "node": node_id})
                    log_event(f"{node_id} tiene {nombre} con otro sha256 (dañado)")

        # 2. Archivos en los nodos que los metadatos no conocen
        for node_id, inventory in inventories.items():
            for nombre, real in inventory.items():
                info = metadata.get(nombre)
                if info is not None and info.get("deleting"):
                    continue
                if info is None:
                    metadata[nombre] = {
                        "size": real["size"],
                        "sha256": real["sha256"],
                        "nodes": [node_id],
                        "uploaded_at": None,
                    }
                    report["added"].append({"name": nombre, "node": node_id})
                    log_event(f"{nombre} encontrado en {node_id}, agregado a metadatos")
                elif node_id in info["nodes"]:
                    continue
                elif real["sha256"] == info["sha256"]:
                    info["nodes"].append(node_id)
                    report["added"].append({"name": nombre, "node": node_id})
                    log_event(f"{nombre} encontrado en {node_id}, agregado como réplica")
                elif {"name": nombre, "node": node_id} not in report["conflicts"]:
                    report["conflicts"].append({"name": nombre, "node": node_id})
                    log_event(f"{node_id} tiene {nombre} con otro sha256 (conflicto)")

        # 3. Archivos que ya no están en ningún nodo
        for nombre in [nombre for nombre, info in metadata.items() if not info["nodes"]]:
            del metadata[nombre]
            report["lost"].append(nombre)
            log_event(f"{nombre} PERDIDO: no queda ninguna réplica")

        # 4. Re-replicar lo que quedó con menos copias de las esperadas
        for nombre, info in metadata.items():
            if info.get("deleting") or len(info["nodes"]) >= REPLICAS:
                continue
            sources = [
                nodes_by_id[node_id]
                for node_id in info["nodes"]
                if node_id in inventories
            ]
            targets = rank_nodes(
                [
                    node
                    for node in nodes
                    if node["id"] in inventories and node["id"] not in info["nodes"]
                ],
                nodes,
                metadata,
            )
            copy_between_nodes(nombre, info, sources, targets, report)
            if len(info["nodes"]) < REPLICAS:
                report["under_replicated"].append(
                    {"name": nombre, "nodes": list(info["nodes"])}
                )

        save_metadata(metadata)

    return report


@app.post("/api/rebuild")
def rebuild():
    """
    Sincroniza metadata.json con lo que realmente tiene cada nodo.
    Agrega archivos desconocidos, quita réplicas que ya no existen,
    detecta copias dañadas y vuelve a replicar lo que tenga menos de 2 copias.
    ---
    tags:
      - Mantenimiento
    produces:
      - application/json
    responses:
      200:
        description: Resumen de los cambios.
        schema:
          type: object
          properties:
            added:
              type: array
              items:
                type: object
            removed:
              type: array
              items:
                type: object
            conflicts:
              type: array
              items:
                type: object
            lost:
              type: array
              items:
                type: string
            replicated:
              type: array
              items:
                type: object
            under_replicated:
              type: array
              items:
                type: object
            unreachable:
              type: array
              items:
                type: string
            deleted:
              type: array
              items:
                type: string
    """
    return jsonify(sync_metadata())


def heartbeat_loop():
    """Consulta /health de todos los nodos cada HEARTBEAT_INTERVAL segundos."""
    while True:
        try:
            check_nodes(load_nodes())
        except Exception as error:  # el hilo no debe morir por un error puntual
            log_event(f"Error en el heartbeat: {error}")
        time.sleep(HEARTBEAT_INTERVAL)


def sync_loop():
    """Sincroniza al arrancar y luego cada SYNC_INTERVAL segundos."""
    while True:
        try:
            sync_metadata()
        except Exception as error:  # el hilo no debe morir por un error puntual
            log_event(f"Error al sincronizar: {error}")
        time.sleep(SYNC_INTERVAL)


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
    print(f"Heartbeat cada {HEARTBEAT_INTERVAL:g} s")
    print(f"Sincronización con los nodos cada {SYNC_INTERVAL:g} s")
    check_nodes(load_nodes())
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=sync_loop, daemon=True).start()
    app.run(host=args.ip, port=args.puerto)
