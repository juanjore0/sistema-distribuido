# Laboratorio Grupo 6: asistente de IA en la nube (sin Docker)

Cinco servicios que corren como procesos separados en tu máquina y se comunican por HTTP:

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
`iniciar.py` hace de orquestador: levanta todo y reinicia un nodo si se cae solo.
El balanceador también sirve un **panel web** en `http://localhost:8080/dashboard` (y `:8081/dashboard` para el de 1 nodo).

```
proyecto-nube-ia/
  iniciar.py              # orquestador: levanta y supervisa los 5 procesos
  requirements.txt
  nodo/app.py             # nodo de inferencia (FastAPI)
  balanceador/app.py      # balanceador (FastAPI + httpx) + API del panel
  web/index.html          # panel web
  cliente/simular.py      # generador de carga + CSV
  cliente/graficas.py     # gráficas para las diapositivas
  resultados/             # CSV y PNG de cada escenario
```

## 1. Instalar (una vez)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Levantar el laboratorio (terminal 1)
```bash
python iniciar.py
```
Déjala abierta: ahí aparecen los eventos CAIDO / RECUPERADO del balanceador y del orquestador.
Comandos que puedes escribir en ella: `parar 2`, `iniciar 2`, `estado`, `salir`.

## 3. Escenarios (terminal 2)
```bash
cd cliente
# A. Un solo servidor
python simular.py --url http://localhost:8081 --n 100 --etiqueta A_1nodo_100
# B. Tres servidores, Round Robin
python simular.py --n 100 --etiqueta B_3nodos_rr_100
# C. Tres servidores, Least Connections
curl -X POST localhost:8080/config -H "Content-Type: application/json" -d "{\"algoritmo\":\"lc\"}"
python simular.py --n 100 --etiqueta C_3nodos_lc_100
curl -X POST localhost:8080/config -H "Content-Type: application/json" -d "{\"algoritmo\":\"rr\"}"
# D. Caída de un nodo a los 2 s (el proceso muere y el orquestador lo reinicia)
python simular.py --n 100 --tumbar http://localhost:8002 --a-los 2 --etiqueta D_caida_nodo2_100
# E. Llegadas escalonadas (Poisson, 6 solicitudes/s)
python simular.py --url http://localhost:8081 --n 100 --llegadas poisson --tasa 6 --etiqueta E_1nodo_poisson6
python simular.py --n 100 --llegadas poisson --tasa 6 --etiqueta E_3nodos_rr_poisson6
# Gráficas
python graficas.py
```
Para 50 solicitudes, cambia `--n 50`. Si no tienes `curl` en Windows, abre http://localhost:8080/docs y usa `/config` desde ahí.

## 4. Panel web
Abre `http://localhost:8080/dashboard` mientras corre `iniciar.py`. Muestra:
- **En vivo** (cada 2 s): nodos disponibles, solicitudes activas y atendidas por nodo, fallos y eventos CAIDO / RECUPERADO.
- **Configuración:** cambiar el algoritmo (RR o LC) sin borrar contadores, y un botón para reiniciarlos.
- **Tumbar un nodo:** botón en cada tarjeta de nodo. Hace lo mismo que `--tumbar`: el nodo muere, el balanceador lo saca del pool y el orquestador lo reinicia.
- **Resultados históricos:** lee los CSV de `resultados/`, compara la latencia (promedio y p95) y el throughput de todos los escenarios, y muestra el reparto por nodo y cada solicitud. Si falta un CSV, el escenario aparece sin datos.

## 5. Caída manual en vivo (la más vistosa)
Terminal 2: `python simular.py --n 100 --llegadas poisson --tasa 6 --etiqueta vivo`
Terminal 1, mientras corre: `parar 2` → el health check lo saca del pool (CAIDO).
Luego `iniciar 2` → vuelve al pool (RECUPERADO).
También se puede hacer desde el panel con el botón **Tumbar ia-node-2**, y ver todo en la misma pantalla.

## 6. Parámetros
| Variable | Servicio | Qué hace |
|---|---|---|
| `CAPACIDAD` | nodo | inferencias simultáneas por nodo (default 2, en `iniciar.py`) |
| `ALGORITMO` | balanceador | `rr` o `lc` (también se cambia en vivo con `/config` o desde el panel) |
| `INTERVALO_HC` | balanceador | segundos entre health checks |
| `--prop-largas` | cliente | fracción de preguntas largas (default 0.3) |

Los resultados quedan en `resultados/` (un CSV por escenario + `resumen.csv` + gráficas PNG): esa es la evidencia verificable.
Los tiempos varían un poco entre corridas porque la duración de cada inferencia es aleatoria.

## 7. Rutas HTTP del balanceador
| Ruta | Qué hace |
|---|---|
| `POST /inferir` | Recibe la consulta y la reenvía a un nodo sano (con reintento si falla) |
| `GET /estado` | Estado de cada nodo, algoritmo activo y últimos eventos |
| `POST /config` | `{"algoritmo": "rr"}` o `"lc"`; `{"reiniciar": true}` pone los contadores en cero |
| `POST /simular-caida` | `{"nodo": 2}` tumba ese nodo (lo usa el botón del panel) |
| `GET /dashboard` | Panel web |
| `GET /dashboard-api/resultados` | Métricas calculadas de los CSV de `resultados/` |

Desde esta versión, `simular.py` guarda también la columna `inferencia_s`. Los CSV anteriores no la tienen: el panel oculta esa columna hasta que se vuelva a ejecutar el escenario.
