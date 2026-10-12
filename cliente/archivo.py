import os
import sys

import requests


COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:4000")
REQUEST_TIMEOUT = float(os.getenv("COORDINATOR_REQUEST_TIMEOUT", "5"))


def check_coordinator():
    response = requests.get(
        f"{COORDINATOR_URL.rstrip('/')}/health", timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def main():
    try:
        health = check_coordinator()
    except requests.RequestException as error:
        print(f"No fue posible consultar al coordinador: {error}", file=sys.stderr)
        return 1

    print(health)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
