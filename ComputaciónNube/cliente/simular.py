"""Cliente que simula estudiantes consultando el asistente en matrículas.

Ejemplos:
  python simular.py --url http://localhost:8080 --n 100 --etiqueta 3nodos_rr
  python simular.py --url http://localhost:8081 --n 100 --etiqueta 1nodo
  python simular.py --n 100 --llegadas poisson --tasa 15 --etiqueta poisson
  python simular.py --n 100 --tumbar http://localhost:8002 --a-los 2 --etiqueta caida
"""
import argparse
import asyncio
import csv
import random
import statistics
import time
from collections import Counter
from pathlib import Path

import httpx

PREGUNTAS = {
    "corta": "¿Hasta qué fecha puedo pagar la matrícula?",
    "larga": "Revisa mis prerrequisitos y arma un horario sin cruces para 6 materias",
}


async def una_consulta(cliente, url, i, tipo, t0, resultados):
    envio = time.perf_counter() - t0
    try:
        r = await cliente.post(f"{url}/inferir", json={"id": i, "tipo": tipo, "pregunta": PREGUNTAS[tipo]})
        fin = time.perf_counter() - t0
        d = r.json() if r.status_code == 200 else {}
        resultados.append({"id": i, "tipo": tipo, "envio_s": round(envio, 3), "latencia_s": round(fin - envio, 3),
                           "ok": r.status_code == 200, "nodo": d.get("nodo", "-"),
                           "espera_cola_s": d.get("espera_cola_s", ""), "reintentos": d.get("reintentos", "")})
    except httpx.HTTPError as e:
        fin = time.perf_counter() - t0
        resultados.append({"id": i, "tipo": tipo, "envio_s": round(envio, 3), "latencia_s": round(fin - envio, 3),
                           "ok": False, "nodo": "-", "espera_cola_s": "", "reintentos": type(e).__name__})


async def tumbar(cliente, url_nodo, segundos):
    await asyncio.sleep(segundos)
    try:
        await cliente.post(f"{url_nodo}/crash", timeout=2)
    except httpx.HTTPError:
        pass
    print(f"  >> t={segundos}s: se tumbó {url_nodo}")


async def main(a):
    random.seed(a.semilla)
    tipos = ["larga" if random.random() < a.prop_largas else "corta" for _ in range(a.n)]
    resultados = []
    async with httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=1000)) as cliente:
        t0 = time.perf_counter()
        extra = [asyncio.create_task(tumbar(cliente, a.tumbar, a.a_los))] if a.tumbar else []
        tareas = []
        for i, tipo in enumerate(tipos, 1):
            tareas.append(asyncio.create_task(una_consulta(cliente, a.url, i, tipo, t0, resultados)))
            if a.llegadas == "poisson":
                await asyncio.sleep(random.expovariate(a.tasa))
        await asyncio.gather(*tareas, *extra)
        total = time.perf_counter() - t0

    ok = [r for r in resultados if r["ok"]]
    lat = sorted(r["latencia_s"] for r in ok)
    p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0
    dist = Counter(r["nodo"] for r in ok)
    reint = sum(1 for r in ok if r["reintentos"] not in ("", 0))
    print(f"\n=== {a.etiqueta}: {a.n} solicitudes ({a.llegadas}) ===")
    print(f"Exitosas            : {len(ok)}/{a.n} ({100 * len(ok) / a.n:.0f} %)")
    print(f"Tiempo total        : {total:.2f} s")
    if lat:
        print(f"Latencia promedio   : {statistics.mean(lat):.2f} s")
        print(f"Latencia p95 / máx  : {p95:.2f} s / {lat[-1]:.2f} s")
    print(f"Throughput          : {len(ok) / total:.2f} solicitudes/s")
    print(f"Recuperadas (retry) : {reint}")
    print("Distribución        : " + ", ".join(f"{k}={v}" for k, v in sorted(dist.items())))

    Path(a.salida).mkdir(parents=True, exist_ok=True)
    archivo = Path(a.salida) / f"{a.etiqueta}.csv"
    with open(archivo, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(resultados[0].keys()))
        w.writeheader()
        w.writerows(sorted(resultados, key=lambda r: r["id"]))
    with open(Path(a.salida) / "resumen.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([a.etiqueta, a.n, a.llegadas, len(ok), round(total, 2),
                                round(statistics.mean(lat), 2) if lat else "", p95, lat[-1] if lat else "",
                                round(len(ok) / total, 2), reint, dict(sorted(dist.items()))])
    print(f"Detalle guardado en {archivo}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--llegadas", choices=["rafaga", "poisson"], default="rafaga")
    p.add_argument("--tasa", type=float, default=15, help="solicitudes por segundo (poisson)")
    p.add_argument("--prop-largas", type=float, default=0.3)
    p.add_argument("--tumbar", help="URL del nodo a tumbar, ej. http://localhost:8002")
    p.add_argument("--a-los", type=float, default=2.0)
    p.add_argument("--etiqueta", default="prueba")
    p.add_argument("--salida", default="../resultados")
    p.add_argument("--semilla", type=int, default=42)
    asyncio.run(main(p.parse_args()))
