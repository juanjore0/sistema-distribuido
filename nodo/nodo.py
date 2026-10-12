"""Nodo de almacenamiento.

Cómo ejecutarlo (desde la raíz del proyecto):

    Windows (PowerShell):
        $env:NODE_ID = "nodo1"
        $env:PORT = "3000"
        py nodo/nodo.py

    macOS / Linux:
        NODE_ID=nodo1 PORT=3000 python3 nodo/nodo.py

Variables de entorno:
    NODE_ID  Nombre del nodo, igual al "id" en coordinador/nodes.json (por defecto: nodo-local)
    PORT     Puerto en el que escucha el nodo (por defecto: 3000)

Prueba: abrir http://localhost:3000/health
"""

import os
import socket

from flask import Flask


# Configuración por variables de entorno (con valores por defecto)
NODE_ID = os.getenv("NODE_ID", "nodo-local")
PORT = int(os.getenv("PORT", "3000"))
HOSTNAME = socket.gethostname()

app = Flask(__name__)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "nodo",
        "node_id": NODE_ID,
        "hostname": HOSTNAME,
        "port": PORT,
    }


if __name__ == "__main__":
    print(f"Iniciando {NODE_ID} en {HOSTNAME}, puerto {PORT}")
    app.run(host="0.0.0.0", port=PORT)
