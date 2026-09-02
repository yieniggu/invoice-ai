# InvoiceOps

InvoiceOps es un laboratorio local para recorrer el ciclo completo de una evaluación de factura: datos, MLflow, Registry, Model API, Portal, Evidence Records, batches Merkle, anchor local y verificación end-to-end.

## Ruta rápida

Ejecute desde la raíz del repositorio. Esta ruta crea o reutiliza el estado local; no borra datos existentes.

### Prerrequisitos

- Python `3.12` y `uv`.
- Para la última clase: Foundry (`forge` y `anvil`) instalado localmente.
- Un navegador para el Portal y JupyterLab si se usarán los notebooks.

### Terminal 1: preparar dependencias y configuración

```bash
uv sync --all-groups
cp .env.example .env
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
```

`cp` crea o reemplaza el archivo local `.env`: es una escritura de configuración. Revise los valores de demostración de ese archivo local si corresponde, pero no agregue ni comparta secretos, claves privadas o mnemonic. El Portal carga `.env` sin sobrescribir variables ya exportadas.

### Terminal 2: MLflow en `:5000`

```bash
uv run mlflow server \
  --backend-store-uri sqlite:///var/local-demo/mlflow.db \
  --default-artifact-root ./var/local-demo/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

Espere que `http://127.0.0.1:5000` responda. Este es el único backend de Tracking y Registry de la sesión.

### Terminal 3: bootstrap idempotente

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
uv run python scripts/bootstrap_local_demo.py
```

**Escritura controlada:** el bootstrap crea o reutiliza el dataset canónico, aplica migraciones y seed de SQLite, reutiliza o crea runs compatibles, registra una versión y garantiza `invoice-review@champion` cuando el candidato aprobado pasa el Gate. No inicia servicios ni borra estado. Si el alias cambia, reinicie el Model API antes de usarlo.

### Terminal 4: Model API en `:8001`

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run uvicorn invoiceops.model_api.app:app --host 127.0.0.1 --port 8001
```

```bash
curl --fail --silent --show-error http://127.0.0.1:8001/health
```

El health debe devolver `status: "ok"` junto con `model_name`, `model_version` y `run_id`. Un `503` significa que la API no pudo resolver/cargar `models:/invoice-review@champion`; ejecute el bootstrap o complete Registry, y reinicie la API.

### Terminal 5: Portal en `:8000`

```bash
export INVOICEOPS_MODE=demo
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
export INVOICEOPS_MODEL_API_URL=http://127.0.0.1:8001
uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/api/health
```

Abra `http://127.0.0.1:8000/login` e ingrese con las credenciales demo definidas en su `.env` local. No copie credenciales fuera del entorno de demostración. `INVOICEOPS_MODEL_API_URL` debe apuntar a `:8001`, nunca al Portal.


## Conceptos que deben quedar claros

```text
model_evaluation
  -> invoice-evidence-v1
  -> canonical JSON UTF-8 (invoice-evidence-canonical-v1)
  -> Keccak-256 digest (leaf)
  -> invoice-merkle-v1 root + ordered proofs
  -> local Anvil anchor (chain ID 31337)
  -> end-to-end verification
```

Un Evidence Record conserva la evaluación, modelo, Policy y provenance. Su payload canónico, ordenado y versionado se digiere con Keccak-256 (no SHA3-256 NIST); el digest hexadecimal en minúsculas sin `0x` es la hoja. El árbol conserva esas hojas ordenadas por `evaluation_id`; cada nodo es `keccak256(left || right)`, duplica la última hoja en niveles impares y cada proof conserva la orientación `left`/`right`.

Los records, batches y anchors persistidos no se editan para "actualizarlos". Un sucesor es un batch nuevo: conserva las evidencias del origen, agrega una o más nuevas, calcula otro root y mantiene el enlace de historial. El anchor guarda solamente el root `bytes32`, nunca facturas, Evidence Records, proofs, modelos, claves privadas o mnemonic.

## Evidence, batches y anchor

Consulte el procedimiento y la clasificación completa de comandos en [Clase 3](docs/class-03-continuity-runbook.md). Resumen operativo:

- En una factura, **Generate model evaluation** escribe auditoría; luego la evidencia corresponde a esa evaluación.
- En **Evidence Batches**, cree un batch inicial con **2 o más** Evidence Records verificados.
- Desde el batch, use **Create successor** y seleccione **1 o más** Evidence Records nuevos. El sucesor es acumulativo, no modifica el origen, y la UI muestra historial, enlaces, árbol y proofs.
- El anchor web es deliberadamente de dos pasos: preflight sin transacción y confirmación que transmite una vez. Solo acepta Anvil local con chain ID `31337` y un deployment existente.
- `ambiguous` o `failed` no se resuelven reenviando. Inspeccione y reconcilie el anchor existente según el runbook.

## Acciones y seguridad

| Tipo | Ejemplos |
|---|---|
| Escrituras no destructivas | Bootstrap, entrenamiento, registro/promotion, `evidence persist`, batch inicial, sucesor, deployment, anchor. |
| Transacciones | `anchor register` y `anchor batch-anchor`; el último además persiste lifecycle SQLite. |
| Lecturas | Health, `demo_state`, `evidence list/get/hash/verify/batch-get/proof/compare`, `anchor query/status/batch-status`, verificación end-to-end. |
| Destructivas | `scripts/reset_demo.py --demo-root var/local-demo --confirm-reset-local-demo`. Primero cree o reutilice el marcador con `scripts/bootstrap_local_demo.py --initialize-demo-root` y ejecute el *dry run*. El reset solo acepta la raíz canónica marcada `var/local-demo`, elimina `invoiceops.db`, sus WAL/SHM, `mlflow.db`, `mlflow-artifacts/`, `notebook-state/state.json` y el dataset canónico `data/invoice-risk-v1`; recrea SQLite. Después, bootstrap recrea el dataset aislado. No es parte de la ruta rápida. |

No documente, imprima ni pegue secretos, claves privadas o mnemonic. Anvil usa su cuenta desbloqueada local a través de la API; no se entrega una clave al CLI.

## Runtime Compose

Ejecute estos comandos desde la raíz del repositorio. El perfil `classroom` levanta el recorrido integrado con datos persistentes en volúmenes Docker.

### Primer inicio o reinicio limpio

```bash
docker compose --profile classroom down --volumes --remove-orphans
docker compose --profile classroom up --build --wait
```

Este reinicio es destructivo: elimina los volúmenes del aula, incluidos los datos de la aplicación, MLflow y sus artefactos.

### Reinicio normal y detención

```bash
docker compose --profile classroom down
docker compose --profile classroom up --wait
```

El reinicio normal conserva los volúmenes y sus datos. Para detener la pila sin reiniciarla:

```bash
docker compose --profile classroom down
```

### Reabrir después de cambiar código o imágenes

```bash
docker compose --profile classroom up --build --wait
```

Abra las superficies del aula:

| Servicio | URL |
|---|---|
| Portal | `http://127.0.0.1:8080` |
| MLflow | `http://127.0.0.1:5000` |
| Model API (health) | `http://127.0.0.1:8001/health` |
| JupyterLab | `http://127.0.0.1:8889/lab` |
| Anvil RPC | `http://127.0.0.1:8545` |

Los manuales de [macOS](docs/manual-ejecucion-macos.md) y [Windows](docs/manual-ejecucion-windows.md) adaptan los comandos de terminal. Los notebooks fuente son la única ruta estudiantil; no hay HTML o PDF renderizado que funcione como fallback.

## Troubleshooting

| Síntoma | Verificación y corrección |
|---|---|
| Model API responde `503` | Falta `invoice-review@champion` o la API arrancó antes de la promoción. Verifique MLflow, ejecute bootstrap/Registry y reinicie Model API. |
| Portal no puede evaluar | Verifique que `INVOICEOPS_MODEL_API_URL=http://127.0.0.1:8001`, Portal y API estén activos, y que Portal/Notebook compartan `INVOICEOPS_DB_PATH`. |
| No aparecen runs o alias | Confirme un solo MLflow en `:5000` y que el terminal/kernel exportó `MLFLOW_TRACKING_URI` antes de iniciar. |
| RPC, manifest o deployment falla | Use un único Anvil local `31337`, confirme `contracts/deployments/local.json` y despliegue antes de anclar. No use otra red. |
| Root ya registrado | No envíe otra transacción. Recupere la identidad/transacción del anchor previo y complete reconciliación. |
| Anchor `ambiguous` o `failed` | Use `batch-status` y `batch-reconcile`; no ejecute de nuevo `batch-anchor` para el mismo batch. |

## Artefactos generados

Los notebooks fuente son la autoridad. Cualquier HTML o PDF futuro se genera solo desde una ejecución manual controlada, con servicios y datos validados; no se edita ni se presenta como fallback del material fuente.
