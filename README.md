Instala las dependencias:

```powershell
py -m pip install -r requirements.txt
```

Inicia un nodo:

```powershell
flask --app nodo.archivo run --host 0.0.0.0 --port 3000
```

Inicia el coordinador en otra terminal:

```powershell
flask --app coordinador.archivo run --host 0.0.0.0 --port 5000
```

El coordinador consulta el endpoint `GET /health` de cada nodo configurado en
`coordinador/nodes.json`. El cliente puede consultar el estado agregado:

```powershell
py cliente/archivo.py
```

Para usar otro coordinador:

```powershell
$env:COORDINATOR_URL = "http://localhost:5000"
py cliente/archivo.py
```
