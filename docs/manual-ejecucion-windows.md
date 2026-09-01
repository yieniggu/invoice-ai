# Manual de ejecución de InvoiceOps en Windows

Este manual permite iniciar InvoiceOps en su equipo, primero de forma local y también con Docker. Está dirigido a personas sin experiencia técnica.

> Antes de continuar, complete la guía de instalación de herramientas correspondiente a Windows. No avance hasta contar con las herramientas que esa guía indique.

## Resultado esperado

Al terminar cualquiera de los dos métodos podrá abrir la pantalla de acceso de InvoiceOps, iniciar sesión con una cuenta de demostración y comprobar que la aplicación responde correctamente.

| Dato | Valor |
|---|---|
| Acceso local | `http://127.0.0.1:8000/login` |
| Acceso con Docker | `http://localhost:8000/login` |
| Credenciales demo con `uv` | Las variables del proceso local; `.env` aplica solo a este modo local. |
| Credenciales demo con Docker Compose | `analyst` / `demo-password`, fijadas en `compose.yml`; Compose no consume `.env` para estas variables. |
| Salud | `/api/health` |

## Antes de empezar

1. Abra **PowerShell** en Windows.
2. Vaya a la carpeta del proyecto. Sustituya la ruta por la ubicación real de `invoice-ai` si es diferente:

```powershell
cd "C:\ruta\a\invoice-ai"
```

3. Compruebe que está en la carpeta correcta:

```powershell
Get-Location
Get-ChildItem
```

**Qué debe observar:** la ruta mostrada termina en `invoice-ai` y en la lista aparece el archivo `README.md`.

> No copie, imprima ni suba secretos, claves privadas o mnemonic. Las credenciales demo no son secretos reales: el Portal local iniciado con `uv` toma sus variables del entorno local, mientras Docker Compose usa los valores demo fijados en `compose.yml`.

## Método 1: ejecución local

Use este método para ejecutar la aplicación directamente en Windows.

### 1. Instalar las dependencias del proyecto

En PowerShell, desde `invoice-ai`, ejecute:

```powershell
uv sync --all-groups
Copy-Item .env.example .env
```

Espere a que el comando termine y vuelva a aparecer el indicador de PowerShell.

**Qué debe observar:** no aparece un mensaje de error y se crean o actualizan los archivos del entorno local del proyecto.

### 2. Instalar Chromium solo si usará el bot opcional

La aplicación web NO necesita Chromium para abrirse. Ejecute este paso únicamente si después utilizará el bot de automatización Playwright:

```powershell
uv run playwright install chromium
```

**Qué debe observar:** el comando descarga o confirma la disponibilidad del navegador Chromium y termina sin errores.

### 3. Reiniciar la base local de demostración

Ejecute este bloque antes de abrir el portal si necesita partir con las ocho facturas de demostración y sin decisiones ni auditorías anteriores:

```powershell
uv run python scripts/reset_demo.py --demo-root var/local-demo
# Solo después de revisar el dry run:
uv run python scripts/bootstrap_local_demo.py --initialize-demo-root
uv run python scripts/reset_demo.py --demo-root var/local-demo --confirm-reset-local-demo
```

> **Advertencia: reset destructivo local.** La confirmación elimina `invoiceops.db`, `invoiceops.db-shm`, `invoiceops.db-wal`, `mlflow.db`, `mlflow-artifacts/`, `notebook-state/state.json` y el dataset canónico `data/invoice-risk-v1` bajo `var/local-demo`; recrea SQLite. Ejecute bootstrap después para recrear el dataset aislado. Úselo solo con datos locales de este laboratorio, nunca con datos reales, de producción o cloud.

**Qué debe observar:** el comando informa la ruta de la base reiniciada y muestra ocho facturas. No lo ejecute durante una actividad cuya auditoría quiera conservar.

### 4. Iniciar la aplicación en modo demostración

En PowerShell, la variable de entorno se define antes de ejecutar el servidor. Copie y pegue estas dos líneas:

```powershell
$env:INVOICEOPS_MODE = "demo"
$env:INVOICEOPS_DB_PATH = "var/local-demo/invoiceops.db"
uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

No cierre esta ventana de PowerShell mientras use InvoiceOps.

**Qué debe observar:** aparecen mensajes de Uvicorn y uno similar a `Uvicorn running on http://127.0.0.1:8000`.

### 5. Abrir y comprobar la aplicación

1. Abra un navegador web.
2. Escriba `http://127.0.0.1:8000/login` en la barra de direcciones.
3. Ingrese las credenciales demo correspondientes a las variables del entorno del Portal local.
4. Seleccione el botón para iniciar sesión.

**Qué debe observar:** aparece el portal de facturas después de iniciar sesión. Si vuelve a ver la pantalla de acceso, revise que las credenciales se hayan escrito exactamente como se muestran.

### 6. Comprobar la salud de la aplicación

Deje la aplicación ejecutándose y abra una segunda ventana de PowerShell. En esa segunda ventana ejecute:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

**Qué debe observar:** PowerShell muestra una respuesta cuyo campo `status` tiene el valor `ok`. Esto confirma que el endpoint `/api/health` está disponible.

### 7. Detener la ejecución local

Vuelva a la primera ventana, donde Uvicorn sigue ejecutándose, y presione:

```text
Control + C
```

**Qué debe observar:** PowerShell muestra que el servidor se está cerrando y vuelve a mostrar el indicador de comandos. La URL de la aplicación deja de cargar al actualizarla.

### Variable de entorno opcional: otra base de datos local

La configuración local de ejemplo usa `var/local-demo/invoiceops.db`. En PowerShell, asigne cada variable con `$env:` antes de iniciar el servidor:

```powershell
$env:INVOICEOPS_DB_PATH = "$HOME\Desktop\invoiceops-prueba.db"
$env:INVOICEOPS_MODE = "demo"
uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

**Qué debe observar:** la aplicación inicia como antes. La ruta indicada será el archivo de datos usado en esa ejecución.

Esta variable queda definida solo en la ventana actual de PowerShell. Si desea quitarla antes de iniciar otra ejecución en esa misma ventana, ejecute:

```powershell
Remove-Item Env:INVOICEOPS_DB_PATH
```

### Usar el flujo MLOps con el portal

El portal puede generar una evaluación ML. Defina `INVOICEOPS_MODEL_API_URL` con la URL del Model API (por ejemplo, `http://127.0.0.1:8001`) al iniciar el portal o en `.env` en la raíz del proyecto. **Generate model evaluation** llama a `/predict`, aplica la Policy y guarda `model_evaluations`; Rule v1 y la decisión final permanecen separados.

La primera vez, registre el kernel que usarán los notebooks:

```powershell
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
```

Abra tres ventanas de PowerShell desde `invoice-ai` después de ejecutar el reset local. Mantenga cada una abierta:

```powershell
# Terminal A: MLflow compartido
uv run mlflow server --backend-store-uri sqlite:///var/local-demo/mlflow.db --default-artifact-root ./var/local-demo/mlflow-artifacts --host 127.0.0.1 --port 5000
```

```powershell
# Terminal B: Model API; reinícielo después de una promotion.
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
uv run uvicorn invoiceops.model_api.app:app --host 127.0.0.1 --port 8001
```

```powershell
# Terminal C: Portal con SQLite y Model API compartidos.
$env:INVOICEOPS_MODE = "demo"
$env:INVOICEOPS_DB_PATH = "var/local-demo/invoiceops.db"
$env:INVOICEOPS_MODEL_API_URL = "http://127.0.0.1:8001"
uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal D: JupyterLab con MLflow y la SQLite compartidos
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
$env:INVOICEOPS_DB_PATH = "var/local-demo/invoiceops.db"
uv run jupyter lab
```

Compruebe `Invoke-RestMethod http://127.0.0.1:8001/health` y `Invoke-RestMethod http://127.0.0.1:8000/api/health`. Abra JupyterLab, seleccione **InvoiceOps Python 3.12** y ejecute `01 -> 02 -> 03 -> 04 -> 05 -> 06`. Después de 05, continúe con Evidence Batches: inicial 2+, sucesor acumulativo 1+ y anchor Anvil `31337` en dos pasos. Consulte el [runbook de Clase 2](class-02-mlops-runbook.md) y el [de Clase 3](class-03-continuity-runbook.md); no inicie un segundo MLflow/Anvil.

## Método 2: ejecución con Docker

Use este método si prefiere que Docker ejecute la aplicación en un contenedor.

> **Diferencia con el Portal local por `uv`.** Este comando no usa las credenciales de su `.env`: `compose.yml` fija `analyst` / `demo-password` para el contenedor. Esas credenciales son solo de demostración, no secretos reales.

### 1. Iniciar el contenedor

Desde la carpeta `invoice-ai`, ejecute:

```powershell
docker compose up --build
```

La primera ejecución puede tardar varios minutos porque Docker crea la imagen.

**Qué debe observar:** Docker muestra la construcción de la imagen y después mensajes del servidor. Mantenga esta ventana abierta mientras use la aplicación.

### 2. Abrir y comprobar la aplicación con Docker

1. Abra `http://localhost:8000/login` en el navegador.
2. Inicie sesión con `analyst` y `demo-password`, los valores demo fijados por Docker Compose.

**Qué debe observar:** se muestra el portal de facturas. La aplicación se está ejecutando dentro de Docker.

### 3. Comprobar la salud con Docker

Abra una segunda ventana de PowerShell y ejecute:

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

**Qué debe observar:** PowerShell muestra una respuesta cuyo campo `status` tiene el valor `ok`.

### 4. Detener Docker

En la ventana donde ejecutó `docker compose up --build`, presione `Control + C`. Después, desde la carpeta del proyecto, ejecute:

```powershell
docker compose down
```

**Qué debe observar:** Docker detiene y elimina los contenedores de la aplicación. El volumen `invoice-data` se conserva, por lo que los datos SQLite persisten para la próxima ejecución.

Para borrar también los datos de demostración guardados por Docker, use únicamente si desea un reinicio completo:

```powershell
docker compose down -v
```

**Qué debe observar:** Docker también elimina el volumen `invoice-data`. Esta acción descarta permanentemente las decisiones y eventos locales guardados en el contenedor.

## Problemas habituales

| Situación | Qué hacer |
|---|---|
| Model API devuelve `503` | Confirme `invoice-review@champion` en MLflow, ejecute bootstrap/Registry y reinicie Model API. |
| RPC, manifest o deployment falla | Use solo Anvil local `31337`, revise `contracts/deployments/local.json` y despliegue antes de anclar. |
| Anchor `ambiguous` o `failed` | No reenvíe: use `batch-status` y `batch-reconcile` del runbook de Clase 3. |
| `uv` o `docker` no se reconoce | Complete la guía de instalación de herramientas de Windows y cierre/abra PowerShell antes de reintentar. |
| El navegador no abre la página | Confirme que la ventana donde inició Uvicorn o Docker sigue abierta y pruebe la URL indicada para el método elegido. |
| El puerto 8000 ya está ocupado | Detenga otra ejecución de InvoiceOps con `Control + C` o cierre el proceso que usa ese puerto antes de iniciar de nuevo. |
| La comprobación de salud no muestra `ok` | Espere unos segundos y repita el comando. Si continúa, revise los mensajes de error en la ventana del servidor. |
| El bot informa que no encuentra Chromium | Ejecute `uv run playwright install chromium`. Esto solo afecta al bot opcional, no al acceso web normal. |

## Lista final de comprobación

- [ ] Pude abrir la URL de acceso del método elegido.
- [ ] Pude iniciar sesión con las credenciales demo locales.
- [ ] Vi el estado `ok` al consultar `/api/health`.
- [ ] Detuve la aplicación con `Control + C` o `docker compose down`.
