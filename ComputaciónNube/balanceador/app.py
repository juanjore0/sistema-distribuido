"""Balanceador de carga para los nodos de inferencia.

- Algoritmos: Round Robin ("rr") o Least Connections ("lc").
- Health checks activos cada INTERVALO_HC segundos a GET /health.
- Failover: si un nodo falla (error de conexión, timeout o 5xx), se marca
  como caído y la solicitud se reintenta en otro nodo sano.
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request

NODOS = [u.strip() for u in os.getenv("NODOS", "http://ia-node-1:8000").split(",") if u.strip()]
INTERVALO_HC = float(os.getenv("INTERVALO_HC", "1.0"))
TIMEOUT_INFERENCIA = float(os.getenv("TIMEOUT_INFERENCIA", "60"))
MAX_REINTENTOS = int(os.getenv("MAX_REINTENTOS", "2"))

config = {"algoritmo": os.getenv("ALGORITMO", "rr")}
nodos = {u: {"sano": True, "activas": 0, "atendidas": 0, "fallos": 0} for u in NODOS}
eventos = []          # bitácora de caídas y recuperaciones
rr = {"i": 0}
cliente: httpx.AsyncClient


def log(msg):
    linea = f"{time.strftime('%H:%M:%S')} {msg}"
    eventos.append(linea)
    print(linea, flush=True)


async def health_checks():
    while True:
        for url, n in nodos.items():
            try:
                r = await cliente.get(f"{url}/health", timeout=0.8)
                ok = r.status_code == 200
            except httpx.HTTPError:
                ok = False
            if ok and not n["sano"]:
                log(f"RECUPERADO {url}: vuelve al pool")
            if not ok and n["sano"]:
                log(f"CAIDO {url}: health check falló, sale del pool")
            n["sano"] = ok
        await asyncio.sleep(INTERVALO_HC)


@asynccontextmanager
async def lifespan(app):
    global cliente
    cliente = httpx.AsyncClient(limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200))
    tarea = asyncio.create_task(health_checks())
    yield
    tarea.cancel()
    await cliente.aclose()


app = FastAPI(title="Balanceador de carga - Asistente IA", lifespan=lifespan)


def elegir(excluir):
    sanos = [u for u, n in nodos.items() if n["sano"] and u not in excluir]
    if not sanos:
        return None
    if config["algoritmo"] == "lc":
        return min(sanos, key=lambda u: nodos[u]["activas"])
    url = sanos[rr["i"] % len(sanos)]
    rr["i"] += 1
    return url


@app.post("/inferir")
async def inferir(request: Request):
    cuerpo = await request.json()
    intentados = []
    for intento in range(MAX_REINTENTOS + 1):
        url = elegir(intentados)
        if url is None:
            break
        n = nodos[url]
        n["activas"] += 1
        try:
            r = await cliente.post(f"{url}/inferir", json=cuerpo, timeout=TIMEOUT_INFERENCIA)
            if r.status_code >= 500:
                raise httpx.HTTPStatusError("5xx", request=r.request, response=r)
            n["atendidas"] += 1
            datos = r.json()
            datos["reintentos"] = intento
            return datos
        except httpx.HTTPError:
            n["fallos"] += 1
            if n["sano"]:
                n["sano"] = False
                log(f"CAIDO {url}: falló una solicitud en curso, failover a otro nodo")
            intentados.append(url)
        finally:
            n["activas"] -= 1
    raise HTTPException(status_code=503, detail="Ningún nodo de inferencia disponible")


@app.get("/estado")
async def estado():
    return {"algoritmo": config["algoritmo"], "nodos": nodos, "eventos": eventos[-20:]}


@app.post("/config")
async def cambiar(request: Request):
    datos = await request.json()
    if datos.get("algoritmo") in ("rr", "lc"):
        config["algoritmo"] = datos["algoritmo"]
    for n in nodos.values():
        n["atendidas"] = n["fallos"] = 0
    rr["i"] = 0
    return config
