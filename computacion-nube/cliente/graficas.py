"""Genera las gráficas de la exposición a partir de los CSV de resultados."""
import csv
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

R = Path(__file__).resolve().parent.parent / "resultados"
GRIS, AZUL, ROJO = "#9aa0a6", "#1a73e8", "#d93025"


def leer(nombre):
    with open(R / f"{nombre}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def lat(filas):
    return [float(r["latencia_s"]) for r in filas if r["ok"] == "True"]


def barras(escenarios, titulo, archivo):
    nombres = list(escenarios)
    prom = [statistics.mean(lat(leer(e))) for e in escenarios.values()]
    maxi = [max(lat(leer(e))) for e in escenarios.values()]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    x = range(len(nombres))
    ax.bar([i - 0.2 for i in x], prom, 0.4, color=AZUL, label="Promedio")
    ax.bar([i + 0.2 for i in x], maxi, 0.4, color=GRIS, label="Máximo")
    for i, (p, m) in enumerate(zip(prom, maxi)):
        ax.text(i - 0.2, p + 0.3, f"{p:.1f}", ha="center")
        ax.text(i + 0.2, m + 0.3, f"{m:.1f}", ha="center")
    ax.set_xticks(list(x), nombres)
    ax.set_ylabel("Tiempo de respuesta (s)")
    ax.set_title(titulo, loc="left", fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(R / archivo, dpi=160)


def linea_tiempo(nombre, titulo, archivo, caida=None):
    filas = [r for r in leer(nombre) if r["ok"] == "True"]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    colores = {"ia-node-1": "#1a73e8", "ia-node-2": ROJO, "ia-node-3": "#188038"}
    for nodo, c in colores.items():
        pts = [(float(r["envio_s"]) + float(r["latencia_s"]), float(r["latencia_s"])) for r in filas if r["nodo"] == nodo]
        if pts:
            ax.scatter(*zip(*pts), s=14, color=c, label=nodo)
    if caida is not None:
        ax.axvline(caida, color=ROJO, ls="--", lw=1)
        ax.text(caida + 0.2, ax.get_ylim()[1] * 0.92, "cae ia-node-2", color=ROJO)
    ax.set_xlabel("Momento en que llega la respuesta (s)")
    ax.set_ylabel("Tiempo de respuesta (s)")
    ax.set_title(titulo, loc="left", fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(R / archivo, dpi=160)


if __name__ == "__main__":
    barras({"1 nodo": "A_1nodo_100", "3 nodos RR": "B_3nodos_rr_100", "3 nodos LC": "C_3nodos_lc_100",
            "3 nodos, cae 1": "D_caida_nodo2_100"},
           "Ráfaga de 100 solicitudes: 3 nodos responden casi 3 veces más rápido", "g1_rafaga_100.png")
    barras({"1 nodo": "E_1nodo_poisson6", "3 nodos RR": "E_3nodos_rr_poisson6", "3 nodos LC": "E_3nodos_lc_poisson6"},
           "Llegadas escalonadas (6 sol/s): 1 nodo se satura, 3 nodos no", "g2_poisson.png")
    linea_tiempo("D_caida_nodo2_100", "Cae ia-node-2 y ninguna solicitud se pierde", "g3_caida.png", caida=2.0)
    print("Gráficas guardadas en", R)
