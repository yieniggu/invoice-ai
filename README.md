# InvoiceOps

InvoiceOps es un laboratorio local para aprender operaciones de facturas y el ciclo de vida de un modelo de riesgo: datos, entrenamiento, tracking, Registry, Gate, serving, Policy y auditoría.

## Ruta rápida

Desde la raíz del proyecto:

```bash
uv sync --all-groups
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
INVOICEOPS_MODE=demo INVOICEOPS_DB_PATH=var/invoiceops.db uv run python scripts/reset_demo.py --confirm
```

Después, inicia MLflow, el portal y JupyterLab en terminales distintas siguiendo [Levantar el laboratorio desde cero](#levantar-el-laboratorio-desde-cero).

> **Advertencia: reset destructivo local.** `scripts/reset_demo.py --confirm` borra permanentemente las facturas, decisiones y evaluaciones de modelo de la SQLite seleccionada. El borrado de `var/mlflow.db` y `var/mlflow-artifacts` elimina runs, versiones, aliases y artifacts locales. Úsalo solo con datos locales de este laboratorio, nunca contra datos reales, de producción o cloud.

## Propósito y arquitectura

El laboratorio separa las responsabilidades para que cada evidencia tenga un lugar claro:

```text
Facturas -> Rule v1 -> portal -> decisión explícita -> auditoría
                   \
Datos sintéticos -> entrenamiento -> MLflow Tracking -> Gate -> Registry
                                                   -> champion -> Notebook 05
                                                                  -> Policy -> auditoría
```

| Componente | Ruta o servicio | Responsabilidad |
|---|---|---|
| SQLite operacional | `var/invoiceops.db` | Base compartida por el portal y el Notebook 05. Guarda facturas, decisiones y `model_evaluations`. |
| MLflow único | `var/mlflow.db` + `var/mlflow-artifacts/` | Backend local común para experimentos, runs, Model Versions, aliases y artifacts. |
| Estado técnico de notebooks | `var/t23_5_demo/` | Solo estado auxiliar de Notebooks 04 y 05. No contiene una base de auditoría ni una copia de MLflow. |
| Portal | `http://127.0.0.1:8000` | Aplica Rule v1, permite decisiones y muestra auditoría; no llama al Model API. |
| Notebook 05 | puerto local libre | Demuestra Model API, Policy y persistencia de evaluaciones en la SQLite operacional. |

La recomendación de la Policy no reemplaza la decisión final: el modelo calcula una probabilidad, la Policy la transforma en recomendación y la operación deja la decisión explícita y auditable.

## Requisitos e instalación

- Python 3.12; el proyecto requiere `>=3.12,<3.13`.
- [uv](https://docs.astral.sh/uv/).
- JupyterLab se instala con el grupo `teaching` incluido en `--all-groups`.
- Chromium solo si se utilizará el bot Playwright: `uv run playwright install chromium`.

Instala todas las dependencias y registra el kernel para los notebooks:

```bash
uv sync --all-groups
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
```

## Levantar el laboratorio desde cero

Ejecuta estos pasos desde la raíz y conserva abiertas las terminales B, C y D. El paso de reset es intencionalmente separado: detén primero MLflow y los kernels que estén usando sus archivos.

### Terminal A: reset local confirmado

```bash
rm -rf var/mlflow.db var/mlflow-artifacts var/t23_5_demo
INVOICEOPS_MODE=demo INVOICEOPS_DB_PATH=var/invoiceops.db uv run python scripts/reset_demo.py --confirm
```

El segundo comando recrea y siembra la SQLite operacional. También elimina únicamente `var/t23_5_demo/state.json`; no crea otra base de auditoría.

### Terminal B: MLflow compartido

```bash
uv run mlflow server \
  --backend-store-uri sqlite:///var/mlflow.db \
  --default-artifact-root ./var/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

Espera a que responda en `http://127.0.0.1:5000`. Este es el único servidor MLflow de la sesión.

### Terminal C: portal

```bash
export INVOICEOPS_MODE=demo
export INVOICEOPS_DB_PATH=var/invoiceops.db

uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

`INVOICEOPS_MODE` debe ser explícitamente `demo` o `secure`. Para este laboratorio usa `demo`; no documentes ni uses secretos reales en variables de entorno. El portal aplica las migraciones pendientes al iniciar.

### Terminal D: JupyterLab

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_DB_PATH=var/invoiceops.db

uv run jupyter lab
```

Abre la URL tokenizada que muestra JupyterLab, entra a `notebooks/` y selecciona el kernel **InvoiceOps Python 3.12**. Exporta las variables antes de abrir el kernel: debe compartir `MLFLOW_TRACKING_URI` con MLflow e `INVOICEOPS_DB_PATH` con el portal.

Para usar otro directorio de estado técnico de los Notebooks 04 y 05, define `INVOICEOPS_NOTEBOOK_DEMO_ROOT` antes de iniciar JupyterLab. No cambia la ubicación de SQLite ni de MLflow.

## Usar la aplicación

Abre `http://127.0.0.1:8000/login` y autentícate con la configuración local que corresponda. Tras ingresar:

- La lista de facturas está en `/invoices`.
- El detalle `/invoices/{invoice_id}` muestra el contexto de riesgo, decisiones registradas y el historial de `model_evaluations`.
- Después de ejecutar Notebook 05 con la misma `INVOICEOPS_DB_PATH`, consulta `INV-10029` e `INV-10030` para ver su auditoría de evaluaciones.
- La verificación de servicio es `GET /api/health`.

Una factura solo puede recibir una decisión mientras está en estado `PENDING`. Si se necesita repetir la demostración, ejecuta el reset antes de crear la nueva auditoría.

## Notebooks en orden

La guía de cada notebook, sus precondiciones y verificaciones está en [notebooks/README.md](notebooks/README.md). Ejecuta la secuencia completa:

| Orden | Notebook | Propósito |
|---:|---|---|
| 01 | `01_data_and_baseline.ipynb` | Explorar el dataset y establecer un baseline. |
| 02 | `02_models_and_metrics.ipynb` | Entrenar y comparar métricas de candidatos. |
| 03 | `03_mlflow_and_model_selection.ipynb` | Registrar runs y evidencia de experimentos en MLflow. |
| 04 | `04_registry_gate_and_promotion.ipynb` | Evaluar el Gate, registrar versiones y mover aliases. |
| 05 | `05_serving_policy_and_audit.ipynb` | Cargar `champion`, servir probabilidades, aplicar Policy y auditar. |

No continúes con 04 sin haber creado o reutilizado los runs de 03, ni con 05 sin completar 04.

## MLflow: Tracking y Registry global

Con `MLFLOW_TRACKING_URI=http://127.0.0.1:5000`, todos los comandos y notebooks usan el mismo backend:

- Experimento: `invoice-risk`; en la UI, **Experiments -> invoice-risk**. Después de crear el experimento local, su ruta es `http://127.0.0.1:5000/#/experiments/1`.
- Registry global: `http://127.0.0.1:5000/#/models`; en la UI, **Models -> invoice-review**.
- Modelo registrado: `invoice-review`.
- Aliases: `challenger` identifica una versión registrada para evaluación y `champion` la versión promovida.
- URI usada por Notebook 05: `models:/invoice-review@champion`.

Un run no es una Model Version. El Gate evalúa calidad, pero un resultado `PASS` no registra ni promueve automáticamente un modelo.

## Flujo sin notebooks: CLI con uv

Mantén MLflow de la Terminal B activo y configura la variable en la terminal donde ejecutarás el CLI:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

### Datos, seed y migraciones

Genera explícitamente el dataset canónico si quieres inspeccionarlo antes de entrenar:

```bash
uv run python scripts/generate_synthetic_dataset.py --seed 20260826 --rows 12000 --version invoice-risk-v1
```

El entrenamiento también genera ese dataset si faltan sus particiones. Para recrear y sembrar las ocho facturas operacionales, usa el reset confirmado:

```bash
INVOICEOPS_MODE=demo INVOICEOPS_DB_PATH=var/invoiceops.db uv run python scripts/reset_demo.py --confirm
```

Para aplicar migraciones a una ruta elegida sin sembrar datos:

```bash
uv run python scripts/migrate_db.py --db-path var/invoiceops.db
```

### Entrenamiento

Ejecuta los tres candidatos; cada comando crea un run en `invoice-risk`:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.train --model dummy
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.train --model logistic
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.train --model random_forest
```

Identifica el `run_id` del candidato elegido en la UI de MLflow. No asumas que el run más reciente es el mejor.

### Gate, registro y promotion

Sustituye `RUN_ID` por el identificador del run elegido. El Gate exige `recall >= 0.18` y `precision >= 0.48`; solo continúa si termina en `PASS`:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.gate --run-id RUN_ID
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.registry register --run-id RUN_ID
```

El registro imprime la versión creada. Sustituye `VERSION` por ese valor para promoverla de forma explícita a `champion`:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.registry promote --version VERSION
```

La promoción mueve un alias del Registry; no recarga un Model API que ya esté en memoria. Para la demostración de serving y auditoría, continúa en Notebook 05 y verifica su `/health` tras iniciar el proceso que el notebook administra.

## Pruebas y lint

El proyecto incluye pruebas unitarias e integración en `tests/`. Las herramientas definidas en el proyecto son pytest y Ruff:

```bash
uv run pytest
uv run ruff check .
```

Estos comandos no se ejecutaron al actualizar esta guía.

## Mapa de scripts y documentación

| Recurso | Uso |
|---|---|
| [`scripts/reset_demo.py`](scripts/reset_demo.py) | Reset destructivo confirmado, seed de facturas y limpieza del estado técnico de notebook. |
| [`scripts/migrate_db.py`](scripts/migrate_db.py) | Aplicar migraciones SQLite a una ruta indicada. |
| [`scripts/generate_synthetic_dataset.py`](scripts/generate_synthetic_dataset.py) | Crear un dataset sintético, determinista y particionado. |
| [`notebooks/README.md`](notebooks/README.md) | Ruta de notebooks, variables y comprobaciones por etapa. |
| [`docs/class-02-mlops-runbook.md`](docs/class-02-mlops-runbook.md) | Runbook docente detallado de MLOps, Gate, Registry, Policy y recuperación. |
| [`docs/manual-ejecucion-macos.md`](docs/manual-ejecucion-macos.md) | Instrucciones específicas para macOS. |
| [`docs/manual-ejecucion-windows.md`](docs/manual-ejecucion-windows.md) | Instrucciones específicas para Windows. |

## Troubleshooting básico

| Situación | Comprobación o acción |
|---|---|
| MLflow no muestra runs | Confirma que MLflow está en `:5000` y que la terminal o kernel exportó `MLFLOW_TRACKING_URI=http://127.0.0.1:5000` antes de ejecutar. No inicies un segundo servidor. |
| El portal no muestra auditoría de Notebook 05 | Portal y kernel deben usar exactamente el mismo `INVOICEOPS_DB_PATH`; consulta el detalle de `INV-10029` o `INV-10030`. |
| Notebook 04 o 05 no encuentra lo esperado | Vuelve al notebook anterior y respeta el orden 03 -> 04 -> 05. |
| El Gate falla | No registres ni promociones ese run; revisa `recall` y `precision` en MLflow y elige o entrena otro candidato. |
| El puerto está ocupado | Detén el proceso local conocido o usa un puerto disponible y actualiza la URL que uses. Notebook 05 reserva un puerto libre para su Model API. |
| La evaluación no se repite | El reset borra la auditoría local; ejecútalo antes de reiniciar la demostración, no después. |

Para el recorrido docente completo y los procedimientos de recuperación, consulta el [runbook de Clase 2](docs/class-02-mlops-runbook.md).
