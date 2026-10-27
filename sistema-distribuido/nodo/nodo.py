import argparse
import hashlib
import os
import socket

from flask import Flask, jsonify, request, send_file
from flasgger import Swagger


# http://100.126.64.74:3000/apidocs/
# python nodo/nodo.py --id nodo1 --ip 100.126.64.74 --puerto 3000
# python nodo/nodo.py --id nodo2 --ip 100.78.158.11 --puerto 3000
# python nodo/nodo.py --id nodo3 --ip 100.80.123.59 --puerto 3000
#
# También se pueden usar variables de entorno en lugar de --id y --puerto:
#   $env:NODE_ID = "nodo1"; $env:PORT = "3000"; py nodo/nodo.py
# STORAGE_DIR cambia la carpeta de archivos (por defecto: nodo/storage).


app = Flask(__name__)

swagger = Swagger(
    app,
    template={
        "info": {
            "title": "API Nodo de Almacenamiento",
            "description": (
                "API de un nodo perteneciente al sistema distribuido. "
                "Permite consultar la disponibilidad del nodo y guardar, "
                "listar, descargar y borrar archivos."
            ),
            "version": "1.0.0",
        },
        "schemes": ["http"],
    },
)

# Configuración por variables de entorno (con valores por defecto)
NODE_ID = os.getenv("NODE_ID", "nodo-local")
PORT = int(os.getenv("PORT", "3000"))
STORAGE_DIR = os.path.abspath(
    os.getenv("STORAGE_DIR", os.path.join(os.path.dirname(__file__), "storage"))
)
os.makedirs(STORAGE_DIR, exist_ok=True)


def file_path(nombre):
    """Arma la ruta dentro de storage/ o devuelve None si el nombre no es válido."""
    nombre = os.path.basename(nombre)
    if nombre in ("", ".", ".."):
        return None
    return os.path.join(STORAGE_DIR, nombre)


def sha256_of(ruta):
    digest = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(64 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def invalid_name():
    return jsonify({"error": "Nombre de archivo inválido"}), 400


def not_found(nombre):
    return jsonify({"error": f"El archivo {nombre} no existe"}), 404


@app.get("/health")
def health():
    """
    Consulta el estado y la información del nodo.
    ---
    tags:
      - Estado
    produces:
      - application/json
    responses:
      200:
        description: El nodo está disponible y responde correctamente.
        schema:
          type: object
          properties:
            status:
              type: string
              example: ok
            service:
              type: string
              example: nodo
            node_id:
              type: string
              example: nodo1
            hostname:
              type: string
              example: EQUIPO-NODO-1
            port:
              type: integer
              example: 3000
    """
    return {
        "status": "ok",
        "service": "nodo",
        "node_id": NODE_ID,
        "hostname": socket.gethostname(),
        "port": PORT,
    }


@app.put("/files/<nombre>")
def put_file(nombre):
    """
    Guarda un archivo en storage/. Si ya existe, lo reemplaza.
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
        description: Archivo guardado.
      400:
        description: Nombre de archivo inválido.
    """
    ruta = file_path(nombre)
    if ruta is None:
        return invalid_name()

    datos = request.get_data()
    with open(ruta, "wb") as archivo:
        archivo.write(datos)

    return jsonify(
        {
            "name": os.path.basename(ruta),
            "size": len(datos),
            "sha256": hashlib.sha256(datos).hexdigest(),
            "node_id": NODE_ID,
        }
    ), 201


@app.get("/files/<nombre>")
def get_file(nombre):
    """
    Descarga un archivo guardado en el nodo.
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
      404:
        description: El archivo no existe.
    """
    ruta = file_path(nombre)
    if ruta is None:
        return invalid_name()
    if not os.path.isfile(ruta):
        return not_found(nombre)

    return send_file(ruta, as_attachment=True)


@app.get("/files")
def list_files():
    """
    Lista los archivos del nodo con su tamaño y sha256.
    ---
    tags:
      - Archivos
    produces:
      - application/json
    responses:
      200:
        description: Archivos guardados en el nodo.
        schema:
          type: object
          properties:
            node_id:
              type: string
              example: nodo1
            files:
              type: array
              items:
                type: object
                properties:
                  name:
                    type: string
                    example: notas.txt
                  size:
                    type: integer
                    example: 128
                  sha256:
                    type: string
    """
    archivos = []
    for nombre in sorted(os.listdir(STORAGE_DIR)):
        ruta = os.path.join(STORAGE_DIR, nombre)
        if os.path.isfile(ruta):
            archivos.append(
                {
                    "name": nombre,
                    "size": os.path.getsize(ruta),
                    "sha256": sha256_of(ruta),
                }
            )

    return jsonify({"node_id": NODE_ID, "files": archivos})


@app.delete("/files/<nombre>")
def delete_file(nombre):
    """
    Borra un archivo del nodo.
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
        description: Archivo borrado.
      404:
        description: El archivo no existe.
    """
    ruta = file_path(nombre)
    if ruta is None:
        return invalid_name()
    if not os.path.isfile(ruta):
        return not_found(nombre)

    os.remove(ruta)
    return jsonify({"deleted": os.path.basename(ruta), "node_id": NODE_ID})


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Inicia un nodo de almacenamiento"
    )
    parser.add_argument(
        "--id",
        default=NODE_ID,
        help="Identificador del nodo (por defecto: variable NODE_ID o nodo-local)",
    )
    parser.add_argument(
        "--ip",
        default="0.0.0.0",
        help="Interfaz donde escucha el nodo",
    )
    parser.add_argument(
        "--puerto",
        type=int,
        default=PORT,
        help="Puerto del nodo (por defecto: variable PORT o 3000)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    NODE_ID = args.id
    PORT = args.puerto

    print(f"Iniciando {NODE_ID} en {args.ip}, puerto {PORT}")
    print(f"Archivos en {STORAGE_DIR}")
    app.run(host=args.ip, port=args.puerto)
