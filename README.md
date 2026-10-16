Instala las dependencias:

```powershell
py -m pip install -r requirements.txt
```

Inicia un nodo. El nombre y el puerto se leen de variables de entorno
(por defecto `NODE_ID=nodo-local` y `PORT=3000`):

| Variable  | Descripción                          | Valor por defecto |
|-----------|--------------------------------------|-------------------|
| `NODE_ID` | Nombre del nodo (debe coincidir con `coordinador/nodes.json`) | `nodo-local` |
| `PORT`    | Puerto en el que escucha el nodo     | `3000`            |

```powershell
$env:NODE_ID = "nodo1"
$env:PORT = "3000"
py nodo/nodo.py
```

En macOS/Linux:

```bash
NODE_ID=nodo1 PORT=3000 python3 nodo/nodo.py
```

`GET /health` responde con el nombre del nodo y el del equipo, por ejemplo:

```json
{"status": "ok", "service": "nodo", "node_id": "nodo1", "hostname": "PC-VALERIA", "port": 3000}
```

Puedes comprobar el endpoint directamente desde PowerShell:

```powershell
Invoke-RestMethod http://localhost:3000/health
```

Inicia el coordinador en otra terminal:

```powershell
py coordinador/coordinador.py
```

El coordinador consulta el endpoint `GET /health` de cada nodo configurado en
`coordinador/nodes.json`. El cliente puede consultar el estado agregado:

```powershell
py cliente/cliente.py
```

Para usar otro coordinador:

```powershell
$env:COORDINATOR_URL = "http://localhost:4000"
py cliente/cliente.py
```
