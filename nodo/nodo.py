import argparse
import socket

from flask import Flask
from flasgger import Swagger


# http://100.126.64.74:3000/apidocs/
# python nodo/nodo.py --id nodo1 --ip 100.126.64.74 --puerto 3000
# python nodo/nodo.py --id nodo2 --ip 100.78.158.11 --puerto 3000
# python nodo/nodo.py --id nodo3 --ip 100.80.123.59 --puerto 3000


app = Flask(__name__)

swagger = Swagger(
    app,
    template={
        "info": {
            "title": "API Nodo de Almacenamiento",
            "description": (
                "API de un nodo perteneciente al sistema distribuido. "
                "Permite consultar la disponibilidad e información del nodo."
            ),
            "version": "1.0.0",
        },
        "schemes": ["http"],
    },
)

NODE_ID = "nodo-local"
PORT = 3000


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


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Inicia un nodo de almacenamiento"
    )
    parser.add_argument(
        "--id",
        default="nodo-local",
        help="Identificador del nodo",
    )
    parser.add_argument(
        "--ip",
        default="0.0.0.0",
        help="Interfaz donde escucha el nodo",
    )
    parser.add_argument(
        "--puerto",
        type=int,
        required=True,
        help="Puerto del nodo",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    NODE_ID = args.id
    PORT = args.puerto

    print(f"Iniciando {NODE_ID} en {args.ip}, puerto {PORT}")
    app.run(host=args.ip, port=args.puerto)