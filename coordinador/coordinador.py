"""Coordinador: consulta GET /health de cada nodo listado en nodes.json.

Cómo ejecutarlo (desde la raíz del proyecto):

    Windows (PowerShell):
        py coordinador/coordinador.py

    macOS / Linux:
        python3 coordinador/coordinador.py

Escucha en el puerto 4000. Antes de iniciarlo, revisar que coordinador/nodes.json
tenga la IP y el puerto de cada nodo.

Variables de entorno (opcionales):
    NODE_REQUEST_TIMEOUT  Segundos de espera por cada nodo (por defecto: 2)

Prueba: abrir http://localhost:4000/health
"""

import json
import os
from pathlib import Path

import requests
from flask import Flask, jsonify


app = Flask(__name__)
NODES_FILE = Path(__file__).with_name("nodes.json")
REQUEST_TIMEOUT = float(os.getenv("NODE_REQUEST_TIMEOUT", "2"))


def load_nodes():
    with NODES_FILE.open(encoding="utf-8") as nodes_file:
        nodes = json.load(nodes_file)

    if not isinstance(nodes, list):
        raise ValueError("nodes.json debe contener una lista de nodos")
    return nodes


@app.get("/health")
def health():
    nodes_status = []

    for node in load_nodes():
        node_id = node.get("id")
        node_url = node.get("url")
        if not node_id or not node_url:
            nodes_status.append(
                {"id": node_id, "status": "error", "error": "Configuración inválida"}
            )
            continue

        try:
            response = requests.get(
                f"{node_url.rstrip('/')}/health", timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            node_health = response.json()
            nodes_status.append(
                {"id": node_id, "status": "ok", "response": node_health}
            )
        except (requests.RequestException, ValueError) as error:
            nodes_status.append(
                {"id": node_id, "status": "error", "error": str(error)}
            )

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000)
