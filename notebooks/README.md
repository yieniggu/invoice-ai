# InvoiceOps notebooks

Estos notebooks muestran un flujo completo: partir de facturas, entrenar modelos,
comparar evidencia, registrar una versión aprobada y comprobar qué hace la
aplicación con ella. Ejecuta los notebooks en orden: cada uno usa el resultado
del anterior.

## Inicio rápido

Desde la raíz del proyecto (`invoice-ai`), abre tres terminales.

### Terminal A: MLflow

```bash
uv run mlflow server \
  --backend-store-uri sqlite:///var/mlflow.db \
  --default-artifact-root ./var/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

Deja esta terminal abierta. MLflow guarda los experimentos, runs y versiones de
modelo. Puedes verlo en `http://127.0.0.1:5000`.

### Terminal B: aplicación web

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_DB_PATH=var/invoiceops.db
export INVOICEOPS_MODE=demo

uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

Deja esta terminal abierta. La aplicación se abre en
`http://127.0.0.1:8000`.

### Terminal C: JupyterLab

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export INVOICEOPS_DB_PATH=var/invoiceops.db

uv run jupyter lab
```

Abre la URL que muestra JupyterLab y selecciona el kernel **InvoiceOps Python
3.12**. Las dos variables deben estar presentes antes de abrir el kernel.

## Orden de los notebooks

| Notebook | Qué haces | Qué aprendes |
|---|---|---|
| `01_data_and_baseline.ipynb` | Exploras las facturas y el baseline. | Qué datos existen y por qué un baseline es necesario. |
| `02_models_and_metrics.ipynb` | Entrenas y comparas modelos. | Cómo interpretar accuracy, precision, recall y otras métricas. |
| `03_mlflow_and_model_selection.ipynb` | Registras runs en MLflow. | Un run conserva métricas, parámetros y artifacts de un experimento. |
| `04_registry_gate_and_promotion.ipynb` | Aplicas el Gate y registras versiones. | Un run no es una Model Version; `champion` es un alias que puede moverse. |
| `05_serving_policy_and_audit.ipynb` | Cargas `champion`, predices y auditas. | Modelo, Policy y auditoría son responsabilidades diferentes. |

## Qué comprobar al avanzar

- Después de **03**, revisa los runs en:
  `http://127.0.0.1:5000/#/experiments/1`
- Después de **04**, revisa el Model Registry global en:
  `http://127.0.0.1:5000/#/models`
- Después de **05**, abre `INV-10029` e `INV-10030` en la aplicación. Verás
  sus features y las evaluaciones que dejó el notebook.

## Conceptos clave

| Concepto | Significa |
|---|---|
| **Run** | Una ejecución de entrenamiento con sus métricas, parámetros y artifacts. |
| **Model Version** | Una versión registrada a partir de un run que aprobó el Gate. |
| **Gate** | Comprueba la calidad del modelo antes de permitir su registro. |
| **Champion** | Alias que señala la versión aprobada actualmente. |
| **Policy** | Regla de negocio que transforma una probabilidad en una recomendación. |
| **Auditoría** | Evidencia de qué modelo, versión, probabilidad y Policy participaron. |

## Si algo no aparece

1. Comprueba que MLflow sigue abierto en `http://127.0.0.1:5000`.
2. Confirma que JupyterLab y la aplicación tienen las mismas variables
   `MLFLOW_TRACKING_URI` e `INVOICEOPS_DB_PATH`.
3. Reinicia el kernel de Jupyter si abriste JupyterLab antes de exportar las
   variables.
4. Vuelve al notebook anterior: no continúes con 04 sin ejecutar 03, ni con 05
   sin terminar 04.

> No ejecutes el reset durante una actividad en curso: borra las facturas,
> decisiones y auditorías locales del laboratorio.
