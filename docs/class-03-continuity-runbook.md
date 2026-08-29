# Clase 3: continuidad del estado de demostración

Clase 3 reutiliza el estado operacional de Clase 2. Antes de crear evidencia verificable, inspecciona ese estado con el módulo read-only `invoiceops.demo_state`; no crees una SQLite, backend MLflow o flujo paralelo para esta clase.

## Ruta rápida

1. Prepara el laboratorio de Clase 2 siguiendo el [README](../README.md#levantar-el-laboratorio-desde-cero) si la SQLite canónica no existe o no contiene evaluaciones.
2. Exporta las variables de la sesión antes de iniciar el kernel o la terminal que inspeccionará el estado.
3. Ejecuta el inspector y usa los IDs que muestra, sin asumir valores fijos.

```bash
export INVOICEOPS_DB_PATH=var/invoiceops.db
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run python -m invoiceops.demo_state
```

El comando produce JSON estable, legible y sin credenciales en `tracking_uri`. No ejecuta init, seed, migraciones, registro ni promotion.

## Estado canónico

| Componente | Fuente canónica | Uso en Clase 3 |
|---|---|---|
| SQLite operacional | `INVOICEOPS_DB_PATH`, o `var/invoiceops.db` por defecto | Lee `model_evaluations`, sus IDs y `run_id` asociados. |
| MLflow Tracking y Registry | `MLFLOW_TRACKING_URI` | Comprueba modelos registrados y el alias `champion` cuando la URI está configurada. |
| Portal y Policy | Aplicación existente de InvoiceOps | Siguen siendo responsables de decisión y auditoría; este inspector no los modifica. |
| Herramientas EVM | Ejecutables locales, paquete Python `web3` y directorio `contracts/` | Solo informa su disponibilidad para tickets posteriores. |

`var/t23_5_demo/` es estado técnico histórico de los notebooks 04 y 05. No es una SQLite operacional, backend MLflow ni fuente canónica para Clase 3.

## Preparar una demostración con datos

Si `database.status` es `missing` o `model_evaluation_count` es `0`, prepara Clase 2 de forma explícita. El reset es destructivo y local; úsalo solo cuando corresponda a la actividad docente.

```bash
INVOICEOPS_MODE=demo INVOICEOPS_DB_PATH=var/invoiceops.db uv run python scripts/reset_demo.py --confirm
uv run mlflow server \
  --backend-store-uri sqlite:///var/mlflow.db \
  --default-artifact-root ./var/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

En otra terminal, exporta `MLFLOW_TRACKING_URI=http://127.0.0.1:5000`, completa los notebooks 03, 04 y 05 en orden y vuelve a ejecutar el inspector. La secuencia crea runs, registra una versión, promueve `champion` mediante una decisión explícita y persiste `model_evaluations` en la misma SQLite operacional.

## Interpretar el inspector

| Campo | Significado | Acción docente |
|---|---|---|
| `database.status: available` | La SQLite activa se abrió solo lectura. | Usa `evaluation_ids` y `run_ids` mostrados; ambos son dinámicos. |
| `database.status: missing` o `unavailable` | La ruta no existe o no puede leerse. | Corrige `INVOICEOPS_DB_PATH`; el inspector no crea archivos ni directorios. |
| `mlflow.status: not_configured` | No hay `MLFLOW_TRACKING_URI`. | Configúrala solo si se debe verificar MLflow. |
| `mlflow.status: unavailable` | La URI configurada no responde a MLflow. | Inicia o corrige el único servidor MLflow compartido; no uses fallback. |
| `mlflow.status: empty` | El Registry no tiene modelos registrados. | Vuelve al flujo de Registry de Clase 2 si se necesita un champion. |
| `mlflow.status: available`, `champion: null` | MLflow está disponible, pero no hay alias `champion`. | Es válido; promotion sigue siendo una decisión explícita. |

## Notebook docente

Abre `notebooks/06_class_03_continuity_and_demo_state.ipynb` después de configurar el kernel. Su primera celda llama a `inspect_demo_state()` para confirmar el estado sin mutarlo. Las celdas de C3-T01/C3-T02 usan las APIs públicas de `invoiceops.evidence`: listan evaluaciones utilizables, construyen `invoice-evidence-v1` desde la evaluación canónica y su run MLflow, y persisten una sola evidencia por evaluación. C3-T02 serializa el contenido lógico como JSON UTF-8 canónico versionado y calcula Keccak-256 compatible con Ethereum, no SHA3-256 NIST. El digest hexadecimal es en minúsculas y no incluye el prefijo `0x`. No duplican SQL, canonicalización, criptografía ni inventan lineage.

La interfaz técnica equivalente es:

```bash
uv run python -m invoiceops.evidence list --db "$INVOICEOPS_DB_PATH"
uv run python -m invoiceops.evidence build --db "$INVOICEOPS_DB_PATH" --evaluation-id 1
uv run python -m invoiceops.evidence persist --db "$INVOICEOPS_DB_PATH" --evaluation-id 1
uv run python -m invoiceops.evidence get --db "$INVOICEOPS_DB_PATH" --evaluation-id 1
uv run python -m invoiceops.evidence hash --db "$INVOICEOPS_DB_PATH" --evaluation-id 1
uv run python -m invoiceops.evidence verify --db "$INVOICEOPS_DB_PATH" --evaluation-id 1
uv run python -m invoiceops.evidence compare --db "$INVOICEOPS_DB_PATH" --evaluation-id 1 --field reason --value altered
uv run python -m invoiceops.evidence batch --db "$INVOICEOPS_DB_PATH" --evaluation-id 1 --evaluation-id 2
uv run python -m invoiceops.evidence proof --db "$INVOICEOPS_DB_PATH" --batch-id 1 --evaluation-id 1
```

`hash` devuelve el algoritmo, versión canónica y digest del registro persistido. `verify` reproduce el payload canónico desde `evidence_json` y compara payload y digest persistidos. `compare` modifica solo una copia en memoria y devuelve `tampered: true`; no escribe SQLite. Sustituye `1` por un ID realmente listado. Si falta `run_id` o alguno de los tres campos de provenance, `list` lo marca no utilizable con una causa explícita y `build`/`persist` fallan sin escribir evidencia parcial.

## Batches Merkle y proofs

`batch` exige una selección explícita de `--evaluation-id`; no existe un modo que incluya todas las evidencias. Cada ID debe ser único y corresponder a un Evidence Record `invoice-evidence-v1` persistido y verificable. La selección se guarda por `evaluation_id` ascendente, aunque los argumentos lleguen en otro orden. Si un ID no existe, se repite, no tiene evidencia persistida o su digest no verifica, la operación falla sin crear un batch parcial.

La política `invoice-merkle-v1` usa exactamente los 32 bytes de `digest_hex` como hoja, sin recanonicalizar ni rehashear la evidencia. Para nodos internos calcula `keccak256(left_bytes || right_bytes)`; una hoja tiene como root su mismo digest y, si un nivel es impar, duplica su última hoja. Las proofs guardan la orientación explícita `left` o `right` junto al hash hermano.

El batch persistido contiene root, cantidad, estado `verified`, índices de hojas y proofs. `batch-get` relee y verifica el root y todas las proofs almacenadas; `proof` devuelve esa proof persistida, no reconstruye otra alternativa. Conserva el `batch_id` que devuelve `batch` y úsalo en la demostración.

## Dependencias de runtime

- Python 3.12, `uv` y las dependencias del proyecto (`uv sync --all-groups`).
- SQLite de Clase 2 accesible en la ruta resuelta por `INVOICEOPS_DB_PATH`.
- MLflow en ejecución solo si se configura `MLFLOW_TRACKING_URI`.
- Foundry (`forge` y `anvil`), `web3` y `contracts/` no son requisitos de este ticket: el inspector únicamente reporta si están disponibles.
