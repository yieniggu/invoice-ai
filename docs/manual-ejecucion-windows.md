# Manual de ejecución de InvoiceOps en Windows

Este manual permite iniciar InvoiceOps en su equipo, primero de forma local y también con Docker. Está dirigido a personas sin experiencia técnica.

> Antes de continuar, complete la guía de instalación de herramientas correspondiente a Windows. No avance hasta contar con las herramientas que esa guía indique.

## Resultado esperado

Al terminar cualquiera de los dos métodos podrá abrir la pantalla de acceso de InvoiceOps, iniciar sesión con una cuenta de demostración y comprobar que la aplicación responde correctamente.

| Dato | Valor |
|---|---|
| Acceso local | `http://127.0.0.1:8000/login` |
| Acceso con Docker | `http://localhost:8000/login` |
| Usuario demo | `analyst` |
| Contraseña demo | `demo-password` |
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

## Método 1: ejecución local

Use este método para ejecutar la aplicación directamente en Windows.

### 1. Instalar las dependencias del proyecto

En PowerShell, desde `invoice-ai`, ejecute:

```powershell
uv sync --all-groups
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
$env:INVOICEOPS_MODE = "demo"
$env:INVOICEOPS_DB_PATH = "var/invoiceops.db"
uv run python scripts/reset_demo.py --confirm
```

> **Advertencia: reset destructivo local.** Este comando borra permanentemente las facturas, decisiones y `model_evaluations` de la SQLite indicada por `INVOICEOPS_DB_PATH`, y luego vuelve a sembrar las facturas de demostración. Úselo solo con datos locales de este laboratorio, nunca con datos reales, de producción o cloud.

**Qué debe observar:** el comando informa la ruta de la base reiniciada y muestra ocho facturas. No lo ejecute durante una actividad cuya auditoría quiera conservar.

### 4. Iniciar la aplicación en modo demostración

En PowerShell, la variable de entorno se define antes de ejecutar el servidor. Copie y pegue estas dos líneas:

```powershell
$env:INVOICEOPS_MODE = "demo"
$env:INVOICEOPS_DB_PATH = "var/invoiceops.db"
uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

No cierre esta ventana de PowerShell mientras use InvoiceOps.

**Qué debe observar:** aparecen mensajes de Uvicorn y uno similar a `Uvicorn running on http://127.0.0.1:8000`.

### 5. Abrir y comprobar la aplicación

1. Abra un navegador web.
2. Escriba `http://127.0.0.1:8000/login` en la barra de direcciones.
3. Ingrese `analyst` como usuario y `demo-password` como contraseña.
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

Por defecto, InvoiceOps usa `var/invoiceops.db`. En PowerShell, asigne cada variable con `$env:` antes de iniciar el servidor:

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

El portal no llama al Model API. Notebook 05 escribe `model_evaluations` directamente en la SQLite operacional; para ver esas auditorías en el portal, ambos procesos deben usar exactamente `INVOICEOPS_DB_PATH=var/invoiceops.db`.

La primera vez, registre el kernel que usarán los notebooks:

```powershell
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
```

Abra tres ventanas de PowerShell desde `invoice-ai` después de ejecutar el reset local. Mantenga cada una abierta:

```powershell
# Terminal A: MLflow compartido
uv run mlflow server --backend-store-uri sqlite:///var/mlflow.db --default-artifact-root ./var/mlflow-artifacts --host 127.0.0.1 --port 5000
```

```powershell
# Terminal B: portal con la SQLite operacional compartida
$env:INVOICEOPS_MODE = "demo"
$env:INVOICEOPS_DB_PATH = "var/invoiceops.db"
uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal C: JupyterLab con MLflow y la SQLite compartidos
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
$env:INVOICEOPS_DB_PATH = "var/invoiceops.db"
uv run jupyter lab
```

Abra la URL tokenizada de JupyterLab, seleccione el kernel **InvoiceOps Python 3.12** y ejecute los notebooks en orden `01 -> 02 -> 03 -> 04 -> 05`. MLflow es el único backend de Tracking y Registry; no inicie un segundo servidor. Consulte `INV-10029` o `INV-10030` en el portal tras Notebook 05. Para el flujo docente y la recuperación, use el [runbook de Clase 2](class-02-mlops-runbook.md).

## Método 2: ejecución con Docker

Use este método si prefiere que Docker ejecute la aplicación en un contenedor.

### 1. Iniciar el contenedor

Desde la carpeta `invoice-ai`, ejecute:

```powershell
docker compose up --build
```

La primera ejecución puede tardar varios minutos porque Docker crea la imagen.

**Qué debe observar:** Docker muestra la construcción de la imagen y después mensajes del servidor. Mantenga esta ventana abierta mientras use la aplicación.

### 2. Abrir y comprobar la aplicación con Docker

1. Abra `http://localhost:8000/login` en el navegador.
2. Inicie sesión con `analyst` y `demo-password`.

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
| `uv` o `docker` no se reconoce | Complete la guía de instalación de herramientas de Windows y cierre/abra PowerShell antes de reintentar. |
| El navegador no abre la página | Confirme que la ventana donde inició Uvicorn o Docker sigue abierta y pruebe la URL indicada para el método elegido. |
| El puerto 8000 ya está ocupado | Detenga otra ejecución de InvoiceOps con `Control + C` o cierre el proceso que usa ese puerto antes de iniciar de nuevo. |
| La comprobación de salud no muestra `ok` | Espere unos segundos y repita el comando. Si continúa, revise los mensajes de error en la ventana del servidor. |
| El bot informa que no encuentra Chromium | Ejecute `uv run playwright install chromium`. Esto solo afecta al bot opcional, no al acceso web normal. |

## Lista final de comprobación

- [ ] Pude abrir la URL de acceso del método elegido.
- [ ] Pude iniciar sesión con `analyst` y `demo-password`.
- [ ] Vi el estado `ok` al consultar `/api/health`.
- [ ] Detuve la aplicación con `Control + C` o `docker compose down`.
