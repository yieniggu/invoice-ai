# Clase 3: Evidence, Merkle, Anvil y verificación end-to-end

Clase 3 reutiliza el estado operacional de Clase 2. Antes de crear evidencia verificable, inspecciona ese estado con el módulo read-only `invoiceops.demo_state`; no crees una SQLite, backend MLflow o flujo paralelo para esta clase.

## Ruta rápida

1. Prepara el laboratorio de Clase 2 siguiendo la [Ruta rápida del README](../README.md#ruta-rápida) si la SQLite canónica no existe o no contiene evaluaciones.
2. Exporta las variables de la sesión antes de iniciar el kernel o la terminal que inspeccionará el estado.
3. Ejecuta el inspector y usa los IDs que muestra, sin asumir valores fijos.

```bash
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
uv run python -m invoiceops.demo_state
```

El comando produce JSON estable, legible y sin credenciales en `tracking_uri`. No ejecuta init, seed, migraciones, registro ni promotion: es **solo lectura**.

## Mapa de efectos laterales

| Clase de acción | Comandos o UX | Efecto |
|---|---|---|
| Solo lectura | `demo_state`, `evidence list/records/get/hash/verify/compare/batch-get/proof`, `anchor query/status/batch-status`, `verification` | Lee SQLite, MLflow o RPC; no crea records, batches ni transacciones. |
| Escritura SQLite | `evidence persist`, `evidence batch`, backfill sin `--dry-run`, Portal al crear Evidence Record/batch/sucesor | Persiste evidencia, metadata canónica, batch/proofs o relaciones. |
| Escritura MLflow | Bootstrap, entrenamiento, Registry/promotion | Crea/reutiliza runs, versiones o aliases. Reinicie Model API después de promotion. |
| Deployment/transacción Anvil | `anchor deploy`, `anchor register`, `anchor batch-anchor`, confirmación web | `deploy` modifica el manifest; `register`/`batch-anchor` transmiten un root local. |
| Destructiva | reset confirmado, borrar SQLite/MLflow/artifacts | Fuera de la ruta de Clase 3. |

Nunca documente, pegue o imprima secretos, claves privadas o mnemonic. El cliente usa la primera cuenta desbloqueada del Anvil local; no recibe una clave privada.

## Estado canónico

| Componente | Fuente canónica | Uso en Clase 3 |
|---|---|---|
| SQLite operacional | `INVOICEOPS_DB_PATH`, configurada como `var/local-demo/invoiceops.db` en la ruta aislada | Lee `model_evaluations`, sus IDs y `run_id` asociados. |
| MLflow Tracking y Registry | `MLFLOW_TRACKING_URI` | Comprueba modelos registrados y el alias `champion` cuando la URI está configurada. |
| Portal y Policy | Aplicación existente de InvoiceOps | Siguen siendo responsables de decisión y auditoría; este inspector no los modifica. |
| Herramientas EVM | Ejecutables locales, paquete Python `web3` y directorio `contracts/` | Solo informa su disponibilidad para tickets posteriores. |

`var/local-demo/` contiene el estado local aislado de la demostración: SQLite, MLflow, artifacts, estado auxiliar y el dataset canónico en `data/invoice-risk-v1`. No es una fuente compartida ni remota. El estado técnico histórico de 04/05 ya no se usa como estado compartido.

## Preparar una demostración con datos

Si `database.status` es `missing` o `model_evaluation_count` es `0`, prepara Clase 2 de forma explícita. El reset es destructivo y local; úsalo solo cuando corresponda a la actividad docente.

```bash
uv run python scripts/reset_demo.py --demo-root var/local-demo
# Tras revisar el dry run:
uv run python scripts/bootstrap_local_demo.py --initialize-demo-root
uv run python scripts/reset_demo.py --demo-root var/local-demo --confirm-reset-local-demo
uv run mlflow server \
  --backend-store-uri sqlite:///var/local-demo/mlflow.db \
  --default-artifact-root ./var/local-demo/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

El reset confirmado elimina `invoiceops.db`, sus WAL/SHM, `mlflow.db`, `mlflow-artifacts/`, `notebook-state/state.json` y el dataset canónico `data/invoice-risk-v1` bajo `var/local-demo`; recrea SQLite. En otra terminal, exporta `MLFLOW_TRACKING_URI=http://127.0.0.1:5000` e `INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db`, ejecuta `uv run python scripts/bootstrap_local_demo.py` para recrear el dataset aislado, completa los notebooks 03, 04 y 05 en orden y vuelve a ejecutar el inspector. La secuencia crea runs, registra una versión, promueve `champion` mediante una decisión explícita y persiste `model_evaluations` en la misma SQLite operacional. El marcador de la raíz solo autoriza el reset de ese demo local; no contiene secretos.

## Interpretar el inspector

| Campo | Significado | Acción docente |
|---|---|---|
| `database.status: available` | La SQLite activa se abrió solo lectura. | Usa `evaluation_ids` y `run_ids` mostrados; ambos son dinámicos. |
| `database.status: missing` o `unavailable` | La ruta no existe o no puede leerse. | Corrige `INVOICEOPS_DB_PATH`; el inspector no crea archivos ni directorios. |
| `mlflow.status: not_configured` | No hay `MLFLOW_TRACKING_URI`. | Configúrala solo si se debe verificar MLflow. |
| `mlflow.status: unavailable` | La URI configurada no responde a MLflow. | Inicia o corrige el único servidor MLflow compartido; no uses fallback. |
| `mlflow.status: empty` | El Registry no tiene modelos registrados. | Vuelve al flujo de Registry de Clase 2 si se necesita un champion. |
| `mlflow.status: available`, `champion: null` | MLflow está disponible, pero no hay alias `champion`. | Es válido; promotion sigue siendo una decisión explícita. |

## Notebook docente y CLI equivalente

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
uv run python -m invoiceops.evidence batch --db "$INVOICEOPS_DB_PATH" --evaluation-id EVALUATION_ID_1 --evaluation-id EVALUATION_ID_2
uv run python -m invoiceops.evidence batch-get --db "$INVOICEOPS_DB_PATH" --batch-id BATCH_ID
uv run python -m invoiceops.evidence proof --db "$INVOICEOPS_DB_PATH" --batch-id 1 --evaluation-id 1
```

`build`, `get`, `hash`, `verify`, `compare`, `batch-get` y `proof` son lecturas; `persist` y `batch` escriben SQLite. `hash` devuelve algoritmo, versión canónica y digest del registro persistido. `verify` reproduce el payload canónico desde `evidence_json` y compara payload y digest persistidos. `compare` modifica solo una copia en memoria y devuelve `tampered: true`; no escribe SQLite. Sustituye los marcadores por IDs realmente listados. Si falta `run_id` o alguno de los tres campos de provenance, `list` lo marca no utilizable con una causa explícita y `build`/`persist` fallan sin escribir evidencia parcial.

## Batches Merkle y proofs

`batch` exige una selección explícita de `--evaluation-id`; no existe un modo que incluya todas las evidencias. Cada ID debe ser único y corresponder a un Evidence Record `invoice-evidence-v1` persistido y verificable. La selección se guarda por `evaluation_id` ascendente, aunque los argumentos lleguen en otro orden. Si un ID no existe, se repite, no tiene evidencia persistida o su digest no verifica, la operación falla sin crear un batch parcial.

Para la práctica final, el **batch inicial debe contener 2+ evidencias**. En Portal abra **Evidence Batches**, seleccione los records verificados y cree el batch; la vista muestra hojas, proofs, root y árbol visual. Aunque el motor admite un batch de una hoja para explicar el caso base, no lo use como evidencia de la práctica final.

### Sucesor acumulativo

Abra un batch en Portal y use **Create successor**. Seleccione **1+ Evidence Records nuevos**: el sucesor incorpora todas las evidencias del origen, agrega las seleccionadas, genera otro root y guarda el enlace origen/sucesor. El batch de origen, sus proofs y su anchor no cambian; una evidencia ya incluida queda excluida de la selección. El historial y enlaces entre batches aparecen en la vista del batch y en el detalle de la factura.

La CLI actual no tiene `batch-successor`. No sustituya el sucesor por `evidence batch` con una lista reconstruida: crearía un batch sin lineage. La interfaz técnica existente es la API Python `create_evidence_batch_successor(db_path, source_batch_id, new_evaluation_ids)`, pero para la clase use la UX del Portal.

La política `invoice-merkle-v1` usa exactamente los 32 bytes de `digest_hex` como hoja, sin recanonicalizar ni rehashear la evidencia. Para nodos internos calcula `keccak256(left_bytes || right_bytes)`; una hoja tiene como root su mismo digest y, si un nivel es impar, duplica su última hoja. Las proofs guardan la orientación explícita `left` o `right` junto al hash hermano.

El batch persistido contiene root, cantidad, estado `verified`, índices de hojas y proofs. `batch-get` relee y verifica el root y todas las proofs almacenadas; `proof` devuelve esa proof persistida, no reconstruye otra alternativa. Conserva el `batch_id` que devuelve `batch` y úsalo en la demostración.

## C3-T06: verificación end-to-end y preflight

Antes de la demostración, ejecuta el inspector read-only. Además de los campos
existentes, informa IDs de evaluaciones utilizables cuando MLflow está disponible,
el estado del deployment/RPC EVM y, opcionalmente, el anchor del batch elegido.
No crea SQLite, migraciones, servicios, contratos ni transacciones.

```bash
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_EVIDENCE_BATCH_ID=BATCH_ID # opcional
uv run python -m invoiceops.demo_state
```

`evidence.usable_evaluation_ids` se deriva de las evaluaciones persistidas y su
lineage MLflow real. `evm_runtime.status` es `not_deployed` mientras el manifest
no tenga dirección, `rpc_unavailable` si el deployment existe pero el RPC no
responde, y `available` solo cuando ambos son utilizables. Si se indicó un batch,
`anchor_status` es su lifecycle persistido; `missing` nunca equivale a un root
confirmado.

Tras crear un batch y completar el anchor, verifica una evaluación perteneciente
al batch con la API reusable o la CLI equivalente:

```bash
uv run python -m invoiceops.verification \
  --db "$INVOICEOPS_DB_PATH" \
  --batch-id BATCH_ID \
  --evaluation-id EVALUATION_ID
```

El JSON conserva `canonical_hash_valid`, `proof_valid`, `batch_valid`,
`anchor_persisted`, `root_on_chain` y `valid`, y añade `evidence_leaf_valid`.
Este último confirma que el digest canónico de la Evidence Record actual es la
misma hoja persistida en el batch. `valid` es `true` exclusivamente si todos los
checks son verdaderos. La verificación vuelve a leer las fuentes persistidas y
consulta el mismo RPC del anchor; no vuelve a construir Evidence Records,
batches, proofs ni anchors, y no escribe datos. Un anchor ausente, una proof o
batch inválido, una Evidence Record que no corresponde a su hoja, un root no
registrado o un RPC inaccesible da `valid: false` sin false positive.

Para demostrar tampering, usa una copia en memoria: `compare_evidence_records`
debe devolver `true` para la copia alterada. No edites SQLite para simular una
demostración. El Notebook 06 invoca tanto esta API como esa comparación; ejecútalo
manualmente con el laboratorio validado. El notebook fuente y este runbook son
la autoridad; no hay HTML renderizado como contingencia en la ruta estudiantil.

Fallback: si `evm_runtime.status` no es `available`, conserva el resultado
inválido, corrige el único runtime local existente y vuelve a ejecutar solo la
verificación. No inicies un Anvil/MLflow paralelo ni reproceses `batch-anchor`;
si el anchor es `ambiguous`, sigue la reconciliación C3-T05 antes de reintentar
la comprobación read-only.

## Dependencias de runtime

- Python 3.12, `uv` y las dependencias del proyecto (`uv sync --all-groups`).
- SQLite de Clase 2 accesible en la ruta resuelta por `INVOICEOPS_DB_PATH`.
- MLflow en ejecución solo si se configura `MLFLOW_TRACKING_URI`.
- Foundry (`forge` y `anvil`), `web3` y `contracts/` son necesarios solo para confirmar el root EVM de C3-T06; el inspector y la verificación no los inician ni despliegan.

## C3-T04: anchor local del root

El único runtime EVM permitido es Foundry Anvil local con chain ID `31337`. Inícielo en una terminal dedicada:

```bash
anvil --chain-id 31337
```

La única configuración de deployment es el manifest versionado `contracts/deployments/local.json`. Un manifest sin dirección requiere deployment. El deployment usa la primera cuenta desbloqueada de Anvil y escribe la dirección y signer en el manifest; no requiere interpretar salida de consola:

```bash
forge test --root contracts
uv run python -m invoiceops.anchor deploy
```

`forge test` es solo lectura respecto de la cadena; `anchor deploy` escribe el manifest. El comando falla explícitamente si falta Anvil, chain ID `31337`, Forge o el manifest. No agregue claves privadas al repositorio, notebook o historial.

Ancle solo un `root_hash` devuelto por `get_evidence_batch(...).root_hash`; no reconstruya el árbol. Tras deployment:

```bash
uv run python -m invoiceops.anchor register --root-hash ROOT_HASH
uv run python -m invoiceops.anchor query --root-hash ROOT_HASH
uv run python -m invoiceops.anchor status --root-hash ROOT_HASH
```

`query` y `status` son solo lectura. `register` primero consulta y no envía un duplicado; una llamada directa duplicada revierte. La cadena guarda únicamente el root de 32 bytes y emite `RootRegistered`; no guarda Evidence Records, proofs, ML artifacts, decisiones de Policy o SQLite.

## C3-T05: persistent batch anchoring and recovery

Apply migrations before anchoring. The batch must already be a real C3-T03
`verified` batch; its persisted root is the only root accepted by the client.
Do not provide a reconstructed root, alter `evidence_batches.status`, or store
keys in SQLite, the manifest, the notebook, or shell history.

```bash
uv run python -c 'from invoiceops.legacy.db import run_migrations; run_migrations()'
uv run python -m invoiceops.anchor batch-anchor --db "$INVOICEOPS_DB_PATH" --batch-id BATCH_ID
```

`batch-anchor` es una escritura SQLite y una posible transacción: resuelve el manifest, usa Anvil desbloqueado y
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

Si Anvil aceptó la transacción pero el proceso o RPC falló antes de que SQLite
persistiera `transaction_hash`, el anchor reservado queda `ambiguous` sin hash.
Ejecute el mismo `batch-reconcile` cuando RPC esté disponible. Puede confirmar
que el root reservado existe con `isRootRegistered`, pero eso no es un receipt
canónico y debe permanecer `ambiguous`. Recupere identidad de transacción y
receipt completo, incluido `RootRegistered`, antes de registrar `verified`.
Nunca ejecute `batch-anchor` otra vez para ese `batch_id`: la reserva única
impide un segundo envío.

`batch-status` es solo lectura. `batch-reconcile` puede escribir el lifecycle SQLite pero nunca envía una transacción. La reconciliación lee el receipt y valida `RootRegistered` cuando
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
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
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
