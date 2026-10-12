import argparse
import json
import os
from pathlib import Path

import requests
from flask import Flask, jsonify

#python coordinador/coordinador.py --ip 100.90.6.28 --puerto 4000

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

    for node in nodes:
        node_id = node.get("id")
        node_url = node.get("url")

        if not node_id or not node_url:
            nodes_status.append(
                {
                    "id": node_id,
                    "status": "error",
                    "error": "Configuración inválida",
                }
            )
            continue

        try:
            response = requests.get(
                f"{node_url.rstrip('/')}/health",
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            nodes_status.append(
                {
                    "id": node_id,
                    "status": "ok",
                    "response": response.json(),
                }
            )
        except (requests.RequestException, ValueError) as error:
            nodes_status.append(
                {
                    "id": node_id,
                    "status": "error",
                    "error": str(error),
                }
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
    app.run(host=args.ip, port=args.puerto)