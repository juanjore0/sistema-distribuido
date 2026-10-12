import argparse
import socket

from flask import Flask

#python nodo/nodo.py --id nodo1 --ip 100.126.64.74 --puerto 3000
#python nodo/nodo.py --id nodo2 --ip 100.78.158.11 --puerto 3000
#python nodo/nodo.py --id nodo3 --ip 100.80.123.59 --puerto 3000

app = Flask(__name__)

NODE_ID = "nodo-local"
PORT = 3000


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "nodo",
        "node_id": NODE_ID,
        "hostname": socket.gethostname(),
        "port": PORT,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicia un nodo de almacenamiento")
    parser.add_argument("--id", default="nodo-local", help="Identificador del nodo")
    parser.add_argument("--ip", default="0.0.0.0", help="Interfaz donde escucha el nodo")
    parser.add_argument("--puerto", type=int, required=True, help="Puerto del nodo")
    args = parser.parse_args()

    NODE_ID = args.id
    PORT = args.puerto

    print(f"Iniciando {NODE_ID} en {args.ip}, puerto {PORT}")
    app.run(host=args.ip, port=args.puerto)