# Notebooks de InvoiceOps

Los notebooks son la ruta didáctica desde datos hasta una evidencia verificable. Ejecutarlos en orden evita mezclar el estado de Tracking, Registry, SQLite y Anvil. Los IDs, versiones y probabilidades son resultados de la ejecución: no los reemplace por valores fijos de ejemplos históricos.

## Antes de abrir Jupyter

Desde la raíz del proyecto, prepare únicamente el entorno y el demo aislado indicado abajo. No reutilice SQLite, MLflow, artifacts ni estado de notebook compartidos de otra práctica.

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
uv run jupyter lab
```

Seleccione el kernel **InvoiceOps Python 3.12**. Si exportó variables después de abrir JupyterLab, reinicie el kernel. La copia de `.env.example` es una escritura de configuración; no incluya secretos reales, claves privadas ni mnemonic en notebooks, outputs o historial de terminal.

## Empezar limpio

Use un entorno aislado si sus pruebas locales están mezcladas. Cierre MLflow, Portal y kernels que usen ese directorio antes de resetearlo.

```bash
uv run python scripts/reset_demo.py --demo-root var/local-demo
uv run python scripts/bootstrap_local_demo.py --initialize-demo-root
uv run python scripts/reset_demo.py --demo-root var/local-demo --confirm-reset-local-demo
uv run mlflow server \
  --backend-store-uri sqlite:///var/local-demo/mlflow.db \
  --default-artifact-root ./var/local-demo/mlflow-artifacts \
  --host 127.0.0.1 --port 5000
```

El primer comando es un **dry run** y lista exactamente las rutas. El segundo crea o reutiliza el marcador de propiedad; el tercero es irreversible y elimina solo `invoiceops.db`, `invoiceops.db-shm`, `invoiceops.db-wal`, `mlflow.db`, `mlflow-artifacts/`, `notebook-state/state.json` y el dataset canónico `data/invoice-risk-v1` dentro de `var/local-demo`. El reset confirmado recrea SQLite; el bootstrap posterior recrea el dataset aislado. Acepta exclusivamente esa raíz canónica marcada por InvoiceOps y rechaza cualquier otra ruta, contenido ajeno o enlace simbólico. No toca código, servicios remotos ni secretos.

En otra terminal, complete el bootstrap y la ruta didáctica:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_DB_PATH=var/local-demo/invoiceops.db
uv run python scripts/bootstrap_local_demo.py --db-path var/local-demo/invoiceops.db
uv run jupyter lab
```

Ejecute 03, 04 y 05 con esas variables. En 06, ejecute todas las celdas: selecciona la primera evaluación `USABLE`, crea o reutiliza su Evidence Record y batch, despliega el contrato local si Anvil está limpio y ancla el root. Una repetición reutiliza los artefactos persistidos y entrega el resultado E2E sin IDs ni flags manuales.

## Orden y propósito

| Orden | Notebook didáctico | Qué hace | Equivalente técnico |
|---:|---|---|---|
| 01 | `01_data_and_baseline.ipynb` | Genera/reutiliza el dataset sintético, explica splits temporales y baseline. | `uv run python scripts/generate_synthetic_dataset.py --seed 20260826 --rows 12000 --version invoice-risk-v1` (escribe dataset). |
| 02 | `02_models_and_metrics.ipynb` | Compara Dummy, Logistic y Random Forest sobre validation. | No es un comando de operación: usa un dataset temporal para la explicación. |
| 03 | `03_mlflow_and_model_selection.ipynb` | Consulta/reutiliza o crea runs de `invoice-risk`; compara evidencia para selección humana. | `uv run python -m invoiceops.ml.train --model dummy|logistic|random_forest` (escribe run MLflow). |
| 04 | `04_registry_gate_and_promotion.ipynb` | Aplica Gate, registra versiones y mueve `champion` explícitamente. | `invoiceops.ml.gate`, `invoiceops.ml.registry register` y `promote` (Registry). |
| 05 | `05_serving_policy_and_audit.ipynb` | Arranca una API didáctica local, comprueba health, predice, aplica Policy y persiste auditoría. | Model API en `:8001`, Portal y `POST /predict`; Registry/SQLite en celdas marcadas. |
| 06 | `06_class_03_continuity_and_demo_state.ipynb` | Inspecciona continuidad y explica Evidence, Merkle, anchor y verificación. | `invoiceops.demo_state`, `invoiceops.evidence`, `invoiceops.anchor` y `invoiceops.verification`. |

Los notebooks 03 a 05 son la clase de MLOps: un run no es una Model Version, un Gate no es promotion y mover `champion` no recarga una API ya iniciada. Tras cualquier promotion, reinicie Model API y confirme `GET /health` antes de persistir una evaluación nueva.

## Clase 3 y efectos laterales

Notebook 06 distingue visualmente lecturas, escrituras SQLite, cambios de manifest y transacciones Anvil. Para el cierre de la clase, el recorrido operativo completo está en el [runbook de Clase 3](../docs/class-03-continuity-runbook.md):

1. Inspeccione estado con `invoiceops.demo_state` (solo lectura).
2. Liste y persista Evidence Records verificables.
3. En el Portal, cree un batch inicial con 2+ evidencias y un sucesor acumulativo con 1+ evidencia nueva.
4. Lea batch y proof; confirme el árbol y su root.
5. Inicie Anvil `31337`, despliegue una vez y use preflight/confirmación o el CLI de anchor.
6. Verifique end-to-end desde Evidence Record hasta root on-chain.

La celda de batch de Notebook 06 conserva el caso didáctico de una hoja para explicar que una hoja puede ser su propio root. Para el flujo de Portal requerido en la práctica final, use dos o más Evidence Records en el batch inicial. El sucesor acumulativo se crea en la UI **Create successor**; la CLI actual no expone un subcomando para sucesores, por lo que no debe simularse creando un batch independiente.

## Comprobaciones visibles

- Después de 03: `http://127.0.0.1:5000/#/experiments/1` muestra runs, métricas, parámetros y artifacts.
- Después de 04: `http://127.0.0.1:5000/#/models` muestra `invoice-review`, sus versiones y el alias `champion`.
- Después de 05: la API didáctica local identifica modelo, versión y run; la auditoría se conserva en `var/local-demo/invoiceops.db`.
- Después de Clase 3: la verificación final solo es válida cuando todos los checks, incluido `root_on_chain`, son verdaderos.

## Artefactos generados

Los notebooks fuente son la única ruta estudiantil y la autoridad. No se distribuyen HTML o PDF como contingencia: cualquier render futuro debe generarse desde el notebook correspondiente en una tarea controlada y verificada, nunca editarse a mano.
