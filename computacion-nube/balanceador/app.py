"""Balanceador de carga para los nodos de inferencia + dashboard web."""

import asyncio
import ast
import csv
import os
import statistics
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

# ============================================================
# CONFIGURACIÓN DEL BALANCEADOR
# ============================================================

NODOS = [
    u.strip()
    for u in os.getenv(
        "NODOS",
        "http://localhost:8001"
    ).split(",")
    if u.strip()
]

INTERVALO_HC = float(os.getenv("INTERVALO_HC", "1.0"))
TIMEOUT_INFERENCIA = float(os.getenv("TIMEOUT_INFERENCIA", "60"))
MAX_REINTENTOS = int(os.getenv("MAX_REINTENTOS", "2"))

config = {
    "algoritmo": os.getenv("ALGORITMO", "rr")
}

nodos = {
    url: {
        "sano": True,
        "activas": 0,
        "atendidas": 0,
        "fallos": 0
    }
    for url in NODOS
}

eventos = []
rr = {"i": 0}

cliente: httpx.AsyncClient


# ============================================================
# REGISTRO DE EVENTOS
# ============================================================

def log(msg):
    linea = f"{time.strftime('%H:%M:%S')} {msg}"
    eventos.append(linea)

    # Evita que la lista de eventos crezca indefinidamente.
    if len(eventos) > 200:
        del eventos[:-200]

    print(linea, flush=True)


# ============================================================
# HEALTH CHECKS
# ============================================================

async def health_checks():
    while True:
        for url, nodo in nodos.items():
            try:
                respuesta = await cliente.get(
                    f"{url}/health",
                    timeout=0.8
                )

                ok = respuesta.status_code == 200

            except httpx.HTTPError:
                ok = False

            if ok and not nodo["sano"]:
                log(f"RECUPERADO {url}: vuelve al pool")

            if not ok and nodo["sano"]:
                log(
                    f"CAIDO {url}: health check falló, "
                    "sale del pool"
                )

            nodo["sano"] = ok

        await asyncio.sleep(INTERVALO_HC)


# ============================================================
# CICLO DE VIDA DE FASTAPI
# ============================================================

@asynccontextmanager
async def lifespan(app):
    global cliente

    cliente = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=1000,
            max_keepalive_connections=200
        )
    )

    tarea = asyncio.create_task(health_checks())

    try:
        yield
    finally:
        tarea.cancel()

        try:
            await tarea
        except asyncio.CancelledError:
            pass

        await cliente.aclose()


app = FastAPI(
    title="Balanceador de carga - Asistente IA",
    lifespan=lifespan
)


# ============================================================
# RUTAS Y ARCHIVOS DEL DASHBOARD
# ============================================================

# Estructura esperada:
#
# proyecto/
# ├── iniciar.py
# ├── balanceador/
# │   └── app.py
# ├── nodo/
# │   └── app.py
# ├── cliente/
# │   └── simular.py
# ├── resultados/
# │   └── archivos CSV
# └── web/
#     └── index.html

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DASHBOARD_FILE = PROJECT_ROOT / "web" / "index.html"
RESULTS_DIR = PROJECT_ROOT / "resultados"


@app.get(
    "/dashboard",
    response_class=HTMLResponse,
    include_in_schema=False
)
async def dashboard():
    """Entrega la página web del dashboard."""

    if not DASHBOARD_FILE.is_file():
        return HTMLResponse(
            (
                "No se encontró web/index.html. "
                "Crea la carpeta web en la raíz del proyecto "
                "y coloca allí el dashboard."
            ),
            status_code=500
        )

    return HTMLResponse(
        DASHBOARD_FILE.read_text(encoding="utf-8")
    )


# ============================================================
# ESCENARIOS DE PRUEBA
# ============================================================

SCENARIOS = [
    (
        "A_1nodo_100",
        "A · Ráfaga, 1 nodo",
        "A · 1 nodo"
    ),
    (
        "B_3nodos_rr_100",
        "B · Ráfaga, 3 nodos RR",
        "B · 3 nodos RR"
    ),
    (
        "C_3nodos_lc_100",
        "C · Ráfaga, 3 nodos LC",
        "C · 3 nodos LC"
    ),
    (
        "D_caida_nodo2_100",
        "D · Caída de nodo 2",
        "D · Caída nodo 2"
    ),
    (
        "E_1nodo_poisson6",
        "E1 · Poisson, 1 nodo",
        "E1 · Poisson 1 nodo"
    ),
    (
        "E_3nodos_rr_poisson6",
        "E2 · Poisson, 3 nodos RR",
        "E2 · Poisson RR"
    ),
    (
        "E_3nodos_lc_poisson6",
        "E3 · Poisson, 3 nodos LC",
        "E3 · Poisson LC"
    ),
]


# ============================================================
# LECTURA DE CSV
# ============================================================

def _read_csv_rows(path):
    """Lee un CSV con encabezados y devuelve sus filas."""

    if not path.is_file():
        return []

    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig"
    ) as archivo:
        return list(csv.DictReader(archivo))


def _summary_rows():
    """
    Lee resumen.csv.

    El cliente simular.py agrega filas sin encabezado con el orden:
    etiqueta,n,llegadas,exitosas,total,media,p95,max,
    throughput,reintentos,distribución
    """

    path = RESULTS_DIR / "resumen.csv"

    if not path.is_file():
        return []

    resultado = []

    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig"
    ) as archivo:
        lector = csv.reader(archivo)

        for fila in lector:
            if len(fila) >= 10 and fila[0] not in (
                "etiqueta",
                ""
            ):
                resultado.append(fila)

    return resultado


def _to_float(value, fallback=0.0):
    """Convierte un valor a float de forma segura."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ============================================================
# API PARA LOS RESULTADOS HISTÓRICOS Y LAS GRÁFICAS
# ============================================================

@app.get("/dashboard-api/resultados")
async def dashboard_resultados():
    """
    Lee los CSV de resultados/ y entrega datos para el dashboard.

    Todos los valores salen de los CSV generados por cliente/simular.py.
    Si falta el CSV de un escenario, se marca como no disponible.
    """

    summaries = _summary_rows()
    scenarios = []

    for filename, title, short in SCENARIOS:
        rows = _read_csv_rows(
            RESULTS_DIR / f"{filename}.csv"
        )

        # Busca la última ejecución de ese escenario.
        summary = next(
            (
                row
                for row in reversed(summaries)
                if row[0] == filename
            ),
            None
        )

        # ----------------------------------------------------
        # No existe CSV: marcar como no disponible
        # ----------------------------------------------------

        if not rows:
            # Sin CSV no se muestran números: el escenario debe ejecutarse.
            scenarios.append({
                "file": f"{filename}.csv",
                "title": title,
                "short": short,
                "available": False,
                "source": "missing",
                "detailAvailable": False,
                "n": 0,
                "success": 0,
                "successRate": 0,
                "total": None,
                "avg": None,
                "p95": None,
                "max": None,
                "throughput": None,
                "retries": None,
                "distribution": {}
            })
            continue

        # ----------------------------------------------------
        # CSV existente: calcular resultados reales
        # ----------------------------------------------------

        successful = [
            row
            for row in rows
            if str(
                row.get("ok", "")
            ).strip().lower() in (
                "true",
                "1",
                "yes"
            )
        ]

        latencies = sorted(
            _to_float(row.get("latencia_s"))
            for row in successful
        )

        n = len(rows)
        good = len(successful)

        end_times = [
            _to_float(row.get("envio_s"))
            + _to_float(row.get("latencia_s"))
            for row in successful
        ]

        derived_total = max(end_times, default=0.0)

        derived_avg = (
            statistics.mean(latencies)
            if latencies
            else 0.0
        )

        derived_p95 = (
            latencies[int(0.95 * (len(latencies) - 1))]
            if latencies
            else 0.0
        )

        derived_max = (
            max(latencies)
            if latencies
            else 0.0
        )

        derived_throughput = (
            good / derived_total
            if derived_total > 0
            else 0.0
        )

        # Distribución real por nodo.
        distribution = {}

        for row in successful:
            node = row.get("nodo", "-") or "-"

            distribution[node] = (
                distribution.get(node, 0) + 1
            )

        # Si existe resumen.csv, sus métricas prevalecen.
        total = (
            _to_float(summary[4], derived_total)
            if summary
            else derived_total
        )

        avg = (
            _to_float(summary[5], derived_avg)
            if summary and len(summary) > 5
            else derived_avg
        )

        p95 = (
            _to_float(summary[6], derived_p95)
            if summary and len(summary) > 6
            else derived_p95
        )

        maxlat = (
            _to_float(summary[7], derived_max)
            if summary and len(summary) > 7
            else derived_max
        )

        throughput = (
            _to_float(summary[8], derived_throughput)
            if summary and len(summary) > 8
            else derived_throughput
        )

        retries = (
            int(_to_float(summary[9], 0))
            if summary and len(summary) > 9
            else sum(
                1
                for row in successful
                if str(
                    row.get("reintentos", "0")
                ) not in ("", "0", "None")
            )
        )

        # Intenta leer la distribución guardada en resumen.csv.
        if summary and len(summary) > 10:
            try:
                parsed = ast.literal_eval(summary[10])

                if isinstance(parsed, dict):
                    distribution = {
                        str(key): int(value)
                        for key, value in parsed.items()
                    }

            except (
                ValueError,
                SyntaxError,
                TypeError
            ):
                pass

        scenarios.append({
            "file": f"{filename}.csv",
            "title": title,
            "short": short,
            "available": True,
            "source": "csv",
            "detailAvailable": True,
            "n": n,
            "success": good,
            "successRate": (
                good / n * 100
                if n
                else 0
            ),
            "total": total,
            "avg": avg,
            "p95": p95,
            "max": maxlat,
            "throughput": throughput,
            "retries": retries,
            "distribution": distribution,
            "rows": rows
        })

    return {
        "source": str(RESULTS_DIR),
        "scenarios": scenarios
    }


# ============================================================
# SELECCIÓN DEL NODO
# ============================================================

def elegir(excluir):
    sanos = [
        url
        for url, nodo in nodos.items()
        if nodo["sano"] and url not in excluir
    ]

    if not sanos:
        return None

    if config["algoritmo"] == "lc":
        return min(
            sanos,
            key=lambda url: nodos[url]["activas"]
        )

    url = sanos[rr["i"] % len(sanos)]
    rr["i"] += 1

    return url


# ============================================================
# ENDPOINT DE INFERENCIA
# ============================================================

@app.post("/inferir")
async def inferir(request: Request):
    cuerpo = await request.json()
    intentados = []

    for intento in range(MAX_REINTENTOS + 1):
        url = elegir(intentados)

        if url is None:
            break

        nodo = nodos[url]
        nodo["activas"] += 1

        try:
            respuesta = await cliente.post(
                f"{url}/inferir",
                json=cuerpo,
                timeout=TIMEOUT_INFERENCIA
            )

            if respuesta.status_code >= 500:
                raise httpx.HTTPStatusError(
                    "5xx",
                    request=respuesta.request,
                    response=respuesta
                )

            nodo["atendidas"] += 1

            datos = respuesta.json()
            datos["reintentos"] = intento

            return datos

        except httpx.HTTPError:
            nodo["fallos"] += 1

            if nodo["sano"]:
                nodo["sano"] = False

                log(
                    f"CAIDO {url}: falló una solicitud "
                    "en curso, failover a otro nodo"
                )

            intentados.append(url)

        finally:
            nodo["activas"] -= 1

    raise HTTPException(
        status_code=503,
        detail="Ningún nodo de inferencia disponible"
    )


# ============================================================
# ESTADO ACTUAL DE LOS NODOS
# ============================================================

@app.get("/estado")
async def estado():
    return {
        "algoritmo": config["algoritmo"],
        "nodos": nodos,
        "eventos": eventos[-20:]
    }


# ============================================================
# CONFIGURAR ALGORITMO
# ============================================================

@app.post("/config")
async def cambiar(request: Request):
    """Cambia el algoritmo. Los contadores solo se reinician si se pide
    explícitamente con {"reiniciar": true}."""
    datos = await request.json()

    algoritmo = datos.get("algoritmo")

    if algoritmo in ("rr", "lc"):
        config["algoritmo"] = algoritmo

    if datos.get("reiniciar"):
        for nodo in nodos.values():
            nodo["atendidas"] = 0
            nodo["fallos"] = 0

        rr["i"] = 0
        log("Contadores reiniciados desde el panel")

    return config


# ============================================================
# SIMULAR LA CAÍDA DE UN NODO DESDE EL PANEL
# ============================================================

@app.post("/simular-caida")
async def simular_caida(request: Request):
    """Pide al nodo indicado ({"nodo": 2}) que termine su proceso.

    Es lo mismo que hace simular.py --tumbar: el nodo muere, el
    balanceador lo detecta y el orquestador (iniciar.py) lo reinicia.
    """
    datos = await request.json()

    try:
        indice = int(datos.get("nodo", 0)) - 1
    except (TypeError, ValueError):
        indice = -1

    if not 0 <= indice < len(NODOS):
        raise HTTPException(
            status_code=400,
            detail=f"Nodo inválido: este balanceador tiene {len(NODOS)} nodo(s)"
        )

    url = NODOS[indice]

    try:
        await cliente.post(f"{url}/crash", timeout=2)
    except httpx.HTTPError:
        # El nodo puede cortar la conexión al morir; no es un error.
        pass

    log(f"Caída simulada desde el panel: {url}")

    return {"nodo": indice + 1, "url": url, "estado": "cayendo"}
