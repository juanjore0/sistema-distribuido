# Laboratorio Grupo 6: asistente de IA en la nube

Cinco servicios en Docker que se comunican por HTTP:

```
estudiantes (cliente/simular.py)
        │  HTTP POST /inferir
        ▼
balanceador :8080  (Round Robin / Least Connections, health checks, failover)
   │        │        │
   ▼        ▼        ▼
ia-node-1  ia-node-2  ia-node-3   (FastAPI, máx. 2 inferencias a la vez por nodo)
 :8001      :8002      :8003
balanceador-1nodo :8081 → solo ia-node-1  (escenario de comparación)
```

## 1. Levantar
```bash
docker compose up --build -d
docker compose ps                       # todos deben quedar "healthy" / "running"
curl localhost:8080/estado              # estado del balanceador
```

## 2. Cliente (en tu máquina)
```bash
cd cliente
pip install httpx matplotlib
```

## 3. Escenarios
```bash
# A. Un solo servidor
python simular.py --url http://localhost:8081 --n 100 --etiqueta A_1nodo_100
# B. Tres servidores, Round Robin
python simular.py --n 100 --etiqueta B_3nodos_rr_100
# C. Tres servidores, Least Connections
curl -X POST localhost:8080/config -H "Content-Type: application/json" -d '{"algoritmo":"lc"}'
python simular.py --n 100 --etiqueta C_3nodos_lc_100
curl -X POST localhost:8080/config -H "Content-Type: application/json" -d '{"algoritmo":"rr"}'
# D. Caída de un nodo a los 2 s (el proceso muere y Docker lo reinicia)
python simular.py --n 100 --tumbar http://localhost:8002 --a-los 2 --etiqueta D_caida_nodo2_100
docker compose logs balanceador        # muestra CAIDO / RECUPERADO
# E. Llegadas escalonadas (Poisson, 6 solicitudes/s)
python simular.py --url http://localhost:8081 --n 100 --llegadas poisson --tasa 6 --etiqueta E_1nodo_poisson6
python simular.py --n 100 --llegadas poisson --tasa 6 --etiqueta E_3nodos_rr_poisson6
# Gráficas
python graficas.py
```
Para 50 solicitudes, cambia `--n 50`.

## 4. Caída manual en vivo (opcional, la más vistosa)
En una terminal: `python simular.py --n 100 --llegadas poisson --tasa 6 --etiqueta vivo`
En otra, mientras corre: `docker stop lab-nube-ia-ia-node-2-1` → el balanceador lo saca del pool.
Luego `docker start lab-nube-ia-ia-node-2-1` → el health check lo devuelve al pool.
(El nombre exacto del contenedor sale en `docker compose ps`.)

## 5. Parámetros
| Variable | Servicio | Qué hace |
|---|---|---|
| `CAPACIDAD` | nodo | inferencias simultáneas por nodo (default 2) |
| `ALGORITMO` | balanceador | `rr` o `lc` |
| `INTERVALO_HC` | balanceador | segundos entre health checks |
| `--prop-largas` | cliente | fracción de preguntas largas (default 0.3) |

Los resultados quedan en `resultados/` (un CSV por escenario + `resumen.csv` + gráficas PNG): esa es la evidencia verificable.
