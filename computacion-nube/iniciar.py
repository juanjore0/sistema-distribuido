"""Lanzador del laboratorio (sin Docker).

Levanta los 5 servicios como procesos separados en tu máquina:
  ia-node-1 :8001   ia-node-2 :8002   ia-node-3 :8003
  balanceador :8080 (3 nodos)   balanceador-1nodo :8081 (solo ia-node-1)

Hace de "orquestador": si un nodo se cae solo (por ejemplo con /crash),
lo vuelve a levantar a los 1 s, como haría Kubernetes o un grupo de autoescalado.

Comandos mientras corre:
  parar 2     apaga ia-node-2 a propósito (no se reinicia)
  iniciar 2   lo vuelve a encender
  estado      muestra qué procesos están vivos
  salir       apaga todo

Uso:  python iniciar.py
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
NODOS = {1: 8001, 2: 8002, 3: 8003}

procesos = {}          # nombre -> Popen
parados = set()        # nodos apagados a propósito
candado = threading.Lock()


def lanzar(nombre, carpeta, puerto, extra_env):
    env = {**os.environ, **extra_env}
    cmd = [sys.executable, "-m", "uvicorn", "app:app", "--app-dir", str(RAIZ / carpeta),
           "--host", "127.0.0.1", "--port", str(puerto), "--log-level", "warning"]
    procesos[nombre] = subprocess.Popen(cmd, env=env)


def lanzar_nodo(i):
    lanzar(f"ia-node-{i}", "nodo", NODOS[i], {"NODE_ID": f"ia-node-{i}", "CAPACIDAD": "2"})


def vigilar():
    while True:
        time.sleep(0.5)
        with candado:
            for i in NODOS:
                nombre = f"ia-node-{i}"
                p = procesos.get(nombre)
                if p and p.poll() is not None and i not in parados:
                    print(f"[orquestador] {nombre} murió (código {p.returncode}); reiniciando en 1 s...", flush=True)
                    time.sleep(1)
                    lanzar_nodo(i)
                    print(f"[orquestador] {nombre} de nuevo arriba", flush=True)


def apagar_todo():
    for p in procesos.values():
        if p.poll() is None:
            p.terminate()
    for p in procesos.values():
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def main():
    for i in NODOS:
        lanzar_nodo(i)
    time.sleep(1.5)
    lanzar("balanceador", "balanceador", 8080,
           {"NODOS": ",".join(f"http://127.0.0.1:{p}" for p in NODOS.values()), "ALGORITMO": "rr"})
    lanzar("balanceador-1nodo", "balanceador", 8081, {"NODOS": "http://127.0.0.1:8001"})
    time.sleep(1.5)
    print("\nLaboratorio arriba: balanceador en http://localhost:8080 (3 nodos) y :8081 (1 nodo)")
    print("Comandos: parar N | iniciar N | estado | salir\n", flush=True)
    threading.Thread(target=vigilar, daemon=True).start()

    try:
        for linea in sys.stdin:
            partes = linea.strip().lower().split()
            if not partes:
                continue
            cmd, arg = partes[0], (partes[1] if len(partes) > 1 else "")
            if cmd == "salir":
                break
            if cmd == "estado":
                for n, p in procesos.items():
                    print(f"  {n:18} {'vivo' if p.poll() is None else 'apagado'}")
            elif cmd in ("parar", "iniciar") and arg.isdigit() and int(arg) in NODOS:
                i = int(arg)
                with candado:
                    if cmd == "parar":
                        parados.add(i)
                        procesos[f"ia-node-{i}"].terminate()
                        print(f"[orquestador] ia-node-{i} apagado a propósito")
                    else:
                        parados.discard(i)
                        if procesos[f"ia-node-{i}"].poll() is not None:
                            lanzar_nodo(i)
                        print(f"[orquestador] ia-node-{i} encendido")
            else:
                print("Comandos: parar N | iniciar N | estado | salir")
    except KeyboardInterrupt:
        pass
    finally:
        print("Apagando todo...")
        apagar_todo()


if __name__ == "__main__":
    main()
