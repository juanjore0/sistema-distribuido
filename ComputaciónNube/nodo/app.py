"""Nodo de inferencia de IA (simulado).

Cada nodo representa un servidor con GPU. La GPU solo puede procesar
CAPACIDAD inferencias a la vez (un "lote"); las demás esperan en cola.
El tiempo de inferencia depende del tipo de pregunta:
  - corta  ("¿Cuándo cierran matrículas?")        ~0.3-0.5 s
  - larga  ("Arma mi horario sin cruces...")      ~1.0-1.6 s
"""
import asyncio
import os
import random
import time

from fastapi import FastAPI
from pydantic import BaseModel

NODE_ID = os.getenv("NODE_ID", "ia-node-1")
CAPACIDAD = int(os.getenv("CAPACIDAD", "2"))

app = FastAPI(title=f"Nodo de inferencia {NODE_ID}")
gpu = asyncio.Semaphore(CAPACIDAD)
estado = {"en_curso": 0, "en_cola": 0, "atendidas": 0}


class Consulta(BaseModel):
    id: int
    tipo: str = "corta"          # "corta" | "larga"
    pregunta: str = ""


@app.get("/health")
async def health():
    return {"nodo": NODE_ID, "estado": "UP", **estado}


@app.post("/inferir")
async def inferir(c: Consulta):
    llegada = time.perf_counter()
    estado["en_cola"] += 1
    async with gpu:                       # espera turno en la GPU
        estado["en_cola"] -= 1
        estado["en_curso"] += 1
        espera = time.perf_counter() - llegada
        duracion = random.uniform(1.0, 1.6) if c.tipo == "larga" else random.uniform(0.3, 0.5)
        try:
            await asyncio.sleep(duracion)  # aquí iría model.generate(...)
        finally:
            estado["en_curso"] -= 1
    estado["atendidas"] += 1
    return {
        "id": c.id,
        "nodo": NODE_ID,
        "espera_cola_s": round(espera, 3),
        "inferencia_s": round(duracion, 3),
        "respuesta": f"[{NODE_ID}] respuesta simulada a la consulta {c.id}",
    }


@app.post("/crash")
async def crash():
    """Simula una caída abrupta del proceso (Docker lo reinicia por la política restart)."""
    asyncio.get_running_loop().call_later(0.05, os._exit, 1)
    return {"nodo": NODE_ID, "estado": "cayendo"}
