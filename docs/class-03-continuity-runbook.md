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

## C3-T04: local EVM root anchoring

The canonical local EVM runtime is Foundry Anvil on chain ID `31337`. Start it
in a dedicated terminal and keep it local to the teaching environment:

```bash
anvil --chain-id 31337
```

The only deployment configuration is the versioned
`contracts/deployments/local.json` manifest. It starts with `address: null`.
Deploying uses the first unlocked Anvil account, writes the resulting address
and signer to that manifest, and never requires parsing console output:

```bash
forge test --root contracts
uv run python -m invoiceops.anchor deploy
```

Do not add a private key to this repository, a notebook, or a command history.
The Python API uses Anvil's unlocked local account. The deploy command fails
explicitly when Anvil, the expected chain ID, Forge, or the manifest are not
available.

Anchor only a root returned by `get_evidence_batch(...).root_hash`; do not
rebuild its Merkle tree. After deployment, the CLI delegates all work to
`invoiceops.anchor`:

```bash
uv run python -m invoiceops.anchor register --root-hash ROOT_HASH
uv run python -m invoiceops.anchor query --root-hash ROOT_HASH
uv run python -m invoiceops.anchor status --root-hash ROOT_HASH
```

`register` is idempotent at the CLI level: it first queries the contract and
does not submit a duplicate transaction. Direct duplicate contract calls
revert. The chain stores only the 32-byte root and emits `RootRegistered`; it
does not store Evidence Records, Merkle proofs, ML artifacts, policy data, or
operational SQLite state.

## C3-T05: persistent batch anchoring and recovery

Apply migrations before anchoring. The batch must already be a real C3-T03
`verified` batch; its persisted root is the only root accepted by the client.
Do not provide a reconstructed root, alter `evidence_batches.status`, or store
keys in SQLite, the manifest, the notebook, or shell history.

```bash
uv run python -c 'from invoiceops.legacy.db import run_migrations; run_migrations()'
uv run python -m invoiceops.anchor batch-anchor --db "$INVOICEOPS_DB_PATH" --batch-id BATCH_ID
```

`batch-anchor` resolves the deployment manifest, uses Anvil's unlocked local
account, submits the persisted root once, immediately stores the transaction
hash as `submitted`, and then reconciles the receipt. Normally, a verified
result records the chain ID, contract address, transaction hash, block number,
gas used, submission and confirmation times, and the `RootRegistered` receipt
event.
The batch itself remains `verified`; anchoring lifecycle data belongs only in
`evidence_batch_anchors`.

If the receipt is unavailable after submission, the anchor becomes `ambiguous`.
This is not a confirmed failure and must not be solved by resubmitting. Inspect
the stored identity and reconcile the same transaction hash after Anvil/RPC is
available again:

```bash
uv run python -m invoiceops.anchor batch-status --db "$INVOICEOPS_DB_PATH" --anchor-id ANCHOR_ID
uv run python -m invoiceops.anchor batch-reconcile --db "$INVOICEOPS_DB_PATH" --anchor-id ANCHOR_ID
```

If Anvil accepted the transaction but the process or RPC failed before SQLite
persisted `transaction_hash`, the reserved anchor remains `ambiguous` with no
hash. Run the same `batch-reconcile` command after the RPC is available. It
may confirm that the reserved root exists with `isRootRegistered`, but that is
not a canonical receipt and it must leave the anchor `ambiguous`. Recover the
transaction identity and its complete receipt, including `RootRegistered`,
before recording `verified`. Never run `batch-anchor` again for that `batch_id`:
the unique reservation prevents a second submit.

Reconciliation reads the transaction receipt and validates `RootRegistered` when
the provider exposes decoded events. A reverted receipt is `failed`, even when
the root already exists on-chain from another transaction; retain that original
transaction hash, block number, and gas used for diagnosis. It never sends a
replacement transaction. If the contract reports a root registered without a
local anchor, do not submit again: recover the prior transaction identity and
complete receipt before recording a canonical anchor.

## Compatibilidad canónica de Evidence Records

La migración 006 añadió metadatos de canonicalización sin reescribir Evidence
Records existentes. Antes de C3-T04, completa únicamente los cuatro metadatos
ausentes de registros `invoice-evidence-v1` reconstruibles. No requiere MLflow,
no crea batches ni proofs y no recalcula ningún anclaje.

Desde cero, ejecuta el flujo de Clase 2 anterior; los nuevos records ya se
persisten completos y el backfill será un no-op. Desde una SQLite de Clase 2,
primero aplica las migraciones actuales y sigue el mismo flujo:

```bash
export INVOICEOPS_DB_PATH=var/invoiceops.db
uv run python -c 'from invoiceops.legacy.db import run_migrations; run_migrations()'
```

Antes de escribir, crea una copia consistente fuera del repositorio y comprueba
su hash y tamaño. En macOS/Linux:

```bash
backup_dir="$HOME/.invoiceops-backups"
mkdir -p "$backup_dir"
backup="$backup_dir/invoiceops-before-canonical-backfill-$(date +%Y%m%dT%H%M%S).db"
sqlite3 "$INVOICEOPS_DB_PATH" ".backup '$backup'"
shasum -a 256 "$backup"
wc -c "$backup"
```

Ejecuta primero el modo sin escritura. El resultado lista los `evaluation_ids`
candidatos. Si informa un error, no ejecutes el modo de escritura: corrige o
restaura la SQLite y conserva el backup para análisis.

```bash
uv run python -m invoiceops.evidence backfill --db "$INVOICEOPS_DB_PATH" --dry-run
uv run python -m invoiceops.evidence backfill --db "$INVOICEOPS_DB_PATH"
uv run python -m invoiceops.evidence backfill --db "$INVOICEOPS_DB_PATH"
```

La segunda ejecución debe devolver `evaluation_ids: []`. Valida cada ID que
devolvió la primera ejecución de escritura con `verify`; valida también la
integridad SQLite y que no se añadieron batches ni proofs:

```bash
uv run python -m invoiceops.evidence verify --db "$INVOICEOPS_DB_PATH" --evaluation-id ID
sqlite3 "$INVOICEOPS_DB_PATH" "PRAGMA integrity_check; PRAGMA foreign_key_check;"
sqlite3 "$INVOICEOPS_DB_PATH" "SELECT COUNT(*) FROM evidence_batches; SELECT COUNT(*) FROM evidence_batch_items;"
```

El backfill se ejecuta en una transacción única: solo actualiza
`canonical_version`, `canonical_payload`, `digest_algorithm` y `digest_hex`
cuando los cuatro estaban en `NULL`. Rechaza y revierte el lote completo ante
JSON no reconstruible, metadata parcial, versión canónica/algoritmo inesperado
o metadata completa que no verifica. Conserva `id`, `evaluation_id`,
`contract_version`, `evidence_json` y `created_at`.

Para rollback, detén cualquier proceso que use la SQLite, conserva la base
afectada para auditoría y restaura el snapshot verificado sobre la ruta
operacional. Después vuelve a ejecutar `PRAGMA integrity_check` y el dry-run:

```bash
cp "$backup" "$INVOICEOPS_DB_PATH"
sqlite3 "$INVOICEOPS_DB_PATH" "PRAGMA integrity_check;"
uv run python -m invoiceops.evidence backfill --db "$INVOICEOPS_DB_PATH" --dry-run
```
