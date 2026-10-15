import argparse
import os
import sys

import requests

#python cliente/cliente.py --ip 100.90.6.28 --puerto 4000

DEFAULT_IP = os.getenv("COORDINATOR_IP", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("COORDINATOR_PORT", "4000"))
REQUEST_TIMEOUT = float(os.getenv("COORDINATOR_REQUEST_TIMEOUT", "5"))

def check_coordinator(url):
    response = requests.get(
        f"{url.rstrip('/')}/health",
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(
        description="Consulta el estado del coordinador."
    )
    parser.add_argument("--ip", default=DEFAULT_IP, help="IP del coordinador")
    parser.add_argument(
        "--puerto",
        type=int,
        default=DEFAULT_PORT,
        help="Puerto del coordinador",
    )
    args = parser.parse_args()

    coordinator_url = f"http://{args.ip}:{args.puerto}"

    try:
        health = check_coordinator(coordinator_url)
    except requests.RequestException as error:
        print(
            f"No fue posible consultar al coordinador en "
            f"{coordinator_url}: {error}",
            file=sys.stderr,
        )
        return 1

    print(health)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())