# Clase 2: MLOps, despliegue y automatización de modelos IA

**Módulo 6: Automatización Inteligente y Gobierno de Modelos**  
**Diplomado de IA, USACH**  
**Duración:** 180 minutos  
**Caso conductor:** `InvoiceOps`

## Propósito de la sesión

La Clase 1 terminó con una regla explícita:

```text
Invoice -> Rule v1 -> Recommendation -> Final decision
```

Esta clase no trata de “entrenar un Random Forest y mirar accuracy”. Trata de responder qué falta entre `model.fit()` y una capacidad que una organización puede usar, reconstruir y gobernar:

```text
Historical data -> dataset -> training -> evaluation -> MLflow Tracking
-> Registry -> Quality Gate -> champion -> Model API -> probability
-> Policy -> recommendation -> explicit final decision -> audit
```

**Tesis para repetir durante la clase:** entrenar un modelo es solo una pequeña parte del problema. MLOps aparece cuando ese modelo debe ser reproducible, versionado, evaluable, servible y auditable.

## Resultado esperado

Al cerrar, cada estudiante debe poder explicar:

- `Notebook != production system`.
- `Accuracy != modelo útil` en un problema desbalanceado.
- El coste de FN y FP determina qué métrica importa.
- El preprocessing forma parte del modelo servido.
- Tracking registra experimentos; Registry gobierna versiones de modelos.
- `Run != model version`, `Gate != promotion` y `latest != best`.
- El modelo devuelve una probabilidad; la Policy produce una recomendación; una persona o proceso toma la decisión final.
- Mover el alias `champion` no cambia un proceso de Model API que ya está corriendo.

## Arquitectura real del laboratorio

```text
Notebooks 01-03: dataset -> modelos -> MLflow Tracking
                                      |
Notebook 04: runs -> Gate -> Registry -> challenger/champion
                                      |
                           models:/invoice-review@champion
                                      |
Notebook 05: Model API -> probability -> ml-policy-v1 -> model_evaluations
```

El portal InvoiceOps de `:8000` conserva Rule v1 y visualiza el historial persistido de `model_evaluations`. **No llama al Model API y no tiene botón `Evaluate with ML`.** La integración portal -> Model API corresponde a T24 y no forma parte de esta clase. La demo completa de serving, Policy y auditoría vive en el Notebook 05.

## Preparación docente

Ejecutar desde la raíz de `invoice-ai`. El proyecto exige Python 3.12.

### macOS / Linux

```bash
python3 --version
uv sync --all-groups
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
export INVOICEOPS_MODE=demo
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

### Windows PowerShell

```powershell
py -3.12 --version
uv sync --all-groups
uv run python -m ipykernel install --user --name invoiceops-py312 --display-name "InvoiceOps Python 3.12"
$env:INVOICEOPS_MODE = "demo"
$env:MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
```

Seleccionar el kernel `InvoiceOps Python 3.12`. `MLFLOW_TRACKING_URI` debe estar presente tanto en terminal como en el kernel que use Notebook 03.

### Abrir los notebooks en Chrome

1. Desde la raíz de `invoice-ai`, iniciar JupyterLab:

   ```bash
   uv run jupyter lab
   ```

2. JupyterLab abrirá el navegador predeterminado. Si no abre Chrome, copiar de la terminal la URL tokenizada que muestra JupyterLab y pegarla en Chrome.
3. En JupyterLab, navegar a `notebooks/`.
4. Durante el bloque de Tracking, abrir y ejecutar los notebooks en orden: `01_data_and_baseline.ipynb` -> `02_models_and_metrics.ipynb` -> `03_mlflow_and_model_selection.ipynb`.
5. Para la demo aislada, abrir y ejecutar después `04_registry_gate_and_promotion.ipynb` -> `05_serving_policy_and_audit.ipynb`.
6. En cada notebook, verificar que el kernel sea `InvoiceOps Python 3.12` y ejecutar las celdas con `Run All` o `Run Selected`, según indique esta guía.
7. Mantener viva la terminal donde se ejecuta JupyterLab durante toda la clase. Al terminar, volver a esa terminal y usar `Ctrl+C` para detener JupyterLab.

### Servicios y puertos

| Servicio | Dirección | Uso |
|---|---|---|
| MLflow compartido | `http://127.0.0.1:5000` | Tracking de Notebooks 01-03 |
| Portal InvoiceOps | `http://127.0.0.1:8000` | Regla v1 y lectura de auditorías |
| Model API de Notebook 05 | `127.0.0.1:<puerto libre>` | Demo aislada; no asumir `8001` |

Iniciar MLflow:

```bash
uv run mlflow server \
  --backend-store-uri sqlite:///var/mlflow.db \
  --default-artifact-root ./var/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

Iniciar el portal solo si se usará para abrir las dos facturas o consultar su historial:

```bash
INVOICEOPS_MODE=demo uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

En PowerShell, usar `$env:INVOICEOPS_MODE = "demo"` antes del comando. Si un puerto está ocupado, detener el proceso conocido o elegir otro puerto y actualizar la URL pertinente. Notebook 05 reserva su propio puerto libre: no iniciar manualmente un proceso en `:8001` para esa demo.

### Ver resultados en MLflow

MLflow UI es un visor del **mismo backend** que usa el notebook: no duplica runs, modelos ni artifacts. Los notebooks imprimen los comandos para abrirlo después de generar o consultar sus datos.

Para Notebook 03, desde la raíz del repositorio, levantar el backend compartido y abrir `http://127.0.0.1:5000`:

```bash
uv run mlflow server \
  --backend-store-uri sqlite:///var/mlflow.db \
  --default-artifact-root ./var/mlflow-artifacts \
  --host 127.0.0.1 \
  --port 5000
```

El mismo comando funciona en PowerShell. En la UI, seleccionar `invoice-risk` y comparar runs, métricas, parámetros, tags y artifacts. Notebook 03 no registra versiones ni cambia aliases.

Para Notebooks 04 y 05, usar la celda **Ver esta ejecución en MLflow**. El comando se deriva dinámicamente de `DEMO_ROOT`, por lo que funciona también cuando se configura `INVOICEOPS_NOTEBOOK_DEMO_ROOT` para una demo aislada. Elegir un `<puerto-libre>` y abrir `http://127.0.0.1:<puerto-libre>`. Revisar `invoice-risk` y, en Models, `invoice-review`, sus versiones y aliases. No hay rutas personales codificadas.

Notebook 04 crea runs y Registry. Notebook 05 añade registros de auditoría en SQLite; `model_evaluations`, `source`, `reason` y `correlation_id` no son métricas MLflow. Se revisan en las tablas de Notebook 05 o, si el portal está iniciado, en su historial.

### Reset y contingencia

Antes de la sesión, si se necesita partir de la base legacy limpia:

```bash
INVOICEOPS_MODE=demo uv run python scripts/reset_demo.py
```

`reset_demo.py` borra la base legacy, incluidas facturas, decisiones y `model_evaluations`, y vuelve a sembrar las facturas. Ejecutarlo **antes** de crear la auditoría que se quiere mostrar, nunca después.

Notebook 04 y 05 usan `var/t23_5_demo/` por defecto: su propio Tracking SQLite, Registry, estado idempotente y base de auditorías. Para un entorno alternativo sin tocar ese directorio, definir `INVOICEOPS_NOTEBOOK_DEMO_ROOT` antes de abrir el kernel y ejecutar 04 -> 05 con el mismo valor. Para repetir la ruta por defecto desde cero, cerrar el proceso creado por Notebook 05, eliminar intencionalmente solo `var/t23_5_demo/` y ejecutar 04 -> 05. No hacerlo como paso rutinario durante la clase.

Fallbacks disponibles: renders HTML de 04 y 05 en `notebooks/rendered/`. No afirmar que existen renders versionados de 01-03.

## Mapa temporal y checkpoints

| Tiempo | Bloque | Evidencia visible |
|---|---:|---|
| 00:00-00:10 | Recap de Rule v1 | Diagrama y ventaja/límite de una regla |
| 00:10-00:25 | Hook: `INV-10029` vs `INV-10030` | Dos facturas, mismo resultado Rule v1, distinto riesgo |
| 00:25-00:40 | Fundamentos ML | Features, target, split temporal y leakage |
| 00:40-01:00 | Notebook 01 | Dataset, baseline, matriz de confusión |
| 01:00-01:20 | Logistic Regression | Sigmoid, métricas, coefficients |
| 01:20-01:30 | Pausa | MLflow y kernels verificados |
| 01:30-01:48 | Árbol y Random Forest | Gini, árbol, importancias |
| 01:48-02:00 | Pipeline y activaciones | Preprocessing como parte del modelo |
| 02:00-02:20 | MLflow Tracking | Runs, lineage y selección humana |
| 02:20-02:38 | Registry y Gate | Versiones, aliases, PASS/FAIL |
| 02:38-02:58 | Serving, Policy y auditoría | 04 -> 05, `/health`, A/B, fallback |
| 02:58-03:00 | Cierre y Trabajo 1 | Tres ideas y puente a Clase 3 |

- Minuto 25: debemos estar hablando de dataset. Si no, el recap fue demasiado largo.
- Minuto 60: Notebook 01 y baseline terminados.
- Minuto 80: pausa. No profundizar más matemática.
- Minuto 120: MLflow ya debe estar abierto.
- Minuto 145: Registry y Gate en curso.
- Minuto 170: serving y Policy ya deben estar en pantalla.

## Guion de clase

### 00:00-00:10 | Recap: qué resolvió Rule v1

**Objetivo:** reconectar con Clase 1 sin repetirla.

Escribir o mostrar:

```text
Invoice
  -> Rule v1
  -> Recommendation
  -> Final decision
```

Decir: “La regla observa monto, purchase order y three-way match. Su fortaleza es que es simple, explícita, reproducible y fácil de auditar.”

Preguntar: “¿Cuál es su límite?”

Esperar: solo considera variables escritas, no aprende de ejemplos y las interacciones hacen crecer las reglas de forma difícil de mantener.

Dibujar al lado:

```text
Rule-based: features -> lógica definida por personas -> recomendación
ML: historical examples -> relación aprendida -> probability
```

**No decir que ML es mejor.** Decir que aparece cuando la relación entre señales deja de ser razonable de escribir y mantener manualmente.

### 00:10-00:25 | Hook: dos facturas, una incomodidad

Abrir el portal, si está disponible, y mostrar `INV-10029` y `INV-10030`.

| Factura | Lo que ve Rule v1 | Resultado Rule v1 |
|---|---|---|
| `INV-10029` | 4200, PO sí, three-way match sí | `AUTO_PROCESS` |
| `INV-10030` | 4200, PO sí, three-way match sí | `AUTO_PROCESS` |

Luego mostrar el contexto que Rule v1 no usa:

| Factura | Tenure | Incidentes | Banco cambió | Ratio | País |
|---|---:|---:|---|---:|---|
| `INV-10029` | 2200 días | 0 | no | 1.05 | low |
| `INV-10030` | 12 días | 4 | sí | 3.40 | high |

Preguntar, y dejar silencio: “¿Realmente deberían recibir exactamente el mismo tratamiento? ¿Cuántas reglas nuevas necesitaríamos? ¿Qué ocurre con `new vendor AND bank changed`?”

Transición literal: “Cuando tenemos ejemplos históricos y queremos aprender relaciones entre variables, entramos a Machine Learning. Pero hoy nos interesa el ciclo completo necesario para operarlo.”

No predecir por adelantado la probabilidad ni la recomendación ML de ninguna factura. Notebook 05 usa el modelo servido y la Policy real; los resultados son dinámicos.

### 00:25-00:40 | Fundamentos que sostienen el resto

Presentar el contrato real de ocho features:

| Tipo | Features |
|---|---|
| Numéricas | `invoice_amount_cents`, `vendor_tenure_days`, `previous_incidents_12m`, `amount_vs_vendor_median` |
| Booleanas | `has_purchase_order`, `three_way_match`, `bank_account_recently_changed` |
| Categórica | `country_risk`: `low`, `medium`, `high` |

Mostrar separado:

```text
metadata: invoice_id, submitted_at, vendor_name, status
target:   manual_review_required
```

Explicar: una feature es input; el target es la respuesta histórica conocida durante entrenamiento. `manual_review_required=1` significa que requirió revisión manual. En inferencia el target no existe; por eso no puede entrar como feature. `invoice_id` sirve para trazabilidad, no para que el modelo “aprenda riesgo”.

Mostrar la línea temporal:

```text
PAST ---------------------------------------------------- FUTURE
[ TRAIN 70% ][ VALIDATION 15% ][ TEST 15% ]
```

Decir: “No dejamos que el futuro enseñe al pasado.” Train ajusta el modelo; validation compara candidatos y ayuda a tomar decisiones; test queda reservado para evaluación final. Definir leakage como información que no estaría disponible de forma legítima al inferir. Un target accidentalmente incluido produce un modelo aparentemente excelente e inútil.

Explicar por qué se usa `invoice-risk-v1`: dataset sintético, reproducible con seed `20260826`, sin datos personales reales y diseñado para controlar señales, desbalance, ruido e interacciones.

### 00:40-01:00 | Notebook 01: baseline antes de complejidad

Abrir `notebooks/01_data_and_baseline.ipynb` y ejecutar de arriba abajo.

1. Mostrar las columnas, sin leer cada fila.
2. Confirmar los rangos temporales de train y validation.
3. Ejecutar la distribución del target.
4. Preguntar: “Si siempre predigo `0`, ¿qué accuracy obtengo?”
5. Ejecutar el baseline y `DummyClassifier(strategy="most_frequent")`.
6. Mostrar métricas y matriz de confusión.

Explicar la matriz:

```text
                    Predicted
                 0             1
Actual 0        TN            FP
Actual 1        FN            TP
```

- FP: factura normal enviada a revisión; cuesta trabajo humano.
- FN: factura que requería revisión y fue auto-procesada; puede tener mayor impacto.
- Precision: de lo enviado a revisión, cuánto realmente lo requería.
- Recall: de lo que requería revisión, cuánto detectamos.
- F1: balance armónico de precision y recall; no es una métrica universal.

La pregunta que debe quedar: “¿Un modelo con accuracy cercana a 80% y recall cero sirve para detectar las facturas que nos interesan?” No. El coste del error define la métrica prioritaria.

### 01:00-01:20 | Logistic Regression: score, sigmoid y probabilidad

Abrir `notebooks/02_models_and_metrics.ipynb` y avanzar por Dummy y Logistic.

Mostrar, sin derivar:

```text
z = beta_0 + beta_1*x_1 + ... + beta_n*x_n
sigmoid(z) = 1 / (1 + e^-z)
```

Explicar que `z` puede ir de menos infinito a más infinito; sigmoid lo lleva al rango 0-1. Los coeficientes son asociaciones predictivas, **no causalidad**.

Mostrar el gráfico sigmoid del notebook y marcar `0.5` y `0.8`. Separación crítica:

```text
sigmoid -> probability del modelo
0.80    -> threshold de Policy
```

Mostrar Binary Cross Entropy solo de manera conceptual:

```text
L = -1/N sum(y log(p) + (1-y) log(1-p))
```

Decir: una predicción muy segura y equivocada recibe una penalización alta. Ejecutar Logistic, mostrar métricas, matriz y gráfico de coefficients. Preguntar: “¿Qué error sigue siendo costoso para nuestro negocio?”

### 01:20-01:30 | Pausa técnica

- Verificar que MLflow responde en `http://127.0.0.1:5000`.
- Confirmar el kernel de Notebook 03 y su `MLFLOW_TRACKING_URI`.
- Si corresponde, preparar las pestañas MLflow, Notebook 04 y Notebook 05.

No usar este bloque para incorporar contenido nuevo.

### 01:30-01:48 | Decision Tree y Random Forest

Volver a Notebook 02. Explicar un árbol como preguntas sucesivas, por ejemplo:

```text
previous_incidents > 1?
  yes -> bank_changed?
  no  -> country_risk == high?
```

Mostrar Gini como intuición, no como derivación:

```text
G = 1 - sum(p_k^2)
```

Un nodo puro tiene Gini 0; uno con dos clases 50/50 tiene Gini 0.5. Mostrar solo un árbol real truncado a profundidad 2-3.

Explicar Random Forest:

```text
many trees + different samples + feature subsets -> aggregation -> probability
```

Esto permite representar interacciones como proveedor nuevo y cambio bancario sin escribirlas una a una. Ejecutar el bloque del bosque, mostrar métricas, matriz y feature importances. Repetir: importancia predictiva no implica causalidad.

Pregunta: “¿Por qué no usar siempre Random Forest?” Respuesta: no existe ganador universal; hay trade-offs de interpretabilidad, latencia, memoria, comportamiento y métricas reales.

### 01:48-02:00 | Pipeline y activaciones, sin desviar la clase

Mostrar el pipeline real:

```text
numeric     -> StandardScaler para Logistic
boolean     -> passthrough
country_risk -> OneHotEncoder(handle_unknown="ignore")
transformaciones + clasificador -> sklearn Pipeline
```

Explicar train-serving skew: si entrenamiento transforma `country_risk` y serving entrega algo distinto a lo que el clasificador espera, el sistema falla. El pipeline empaqueta preprocessing y clasificador para aplicar el mismo contrato en entrenamiento e inferencia.

Precisar activaciones:

- `DummyClassifier`: no tiene activation function.
- `RandomForest`: no tiene activation function.
- `LogisticRegression`: usa el enlace sigmoid para mapear score a probabilidad.
- Redes neuronales usan activaciones explícitas como ReLU y Softmax.

Mostrar ReLU y Softmax solo como contexto. Decir: “No entrenaremos una red neuronal hoy; esto conecta conceptos sin cambiar el foco de MLOps.”

### 02:00-02:20 | MLflow Tracking: evidencia de experimentación

Con MLflow ya levantado en una terminal, abrir `notebooks/03_mlflow_and_model_selection.ipynb` en Chrome y ejecutar de arriba abajo. La sección **Preparar los candidatos productivos** es la ruta preferida para la clase: consulta `invoice-risk` y reutiliza un run que ya tenga el mismo `model_type` y `dataset_version`; solo si falta ejecuta el CLI productivo desde el kernel. Así se obtienen los tres candidatos `dummy`, `logistic` y `random_forest` sin exigir comandos de terminal durante la demostración.

Cada nueva ejecución registra un run real. En una segunda ejecución de `Run All`, el notebook muestra y reutiliza los runs existentes, por lo que no crea duplicados. La única excepción es la celda visible `⚠ MODIFICA ESTADO`: cambiar `FORZAR_NUEVAS_CORRIDAS` a `True` genera intencionalmente una nueva corrida por candidato.

La salida docente se presenta por candidato: un separador, modelo, `Run reutilizado` o `Run creado`, dataset y una tabla alineada con `accuracy`, `precision`, `recall`, `f1` y `roc_auc`. Los valores se leen del run real, por lo que no se anticipan ni se inventan scores. Esto permite contrastar el baseline Dummy, Logistic Regression y Random Forest sin confundir una salida cronológica con una decisión de selección.

El bloque plegable **Mensajes técnicos de MLflow** agrupa mensajes conocidos: la creación inicial del experimento, un entorno sin versión de `pip` resoluble y el aviso de firma de columnas enteras. La creación del experimento es informativa. El mensaje de `pip` describe el entorno de empaquetado; se revisa si persiste, pero no invalida por sí mismo las métricas ni el lineage. El aviso de enteros no se ignora: el contrato de features conserva enteros no nulos; MLflow advierte que una inferencia futura con valores faltantes los convertiría a `float` y fallaría la validación de firma. La corrección correcta sería definir y validar un contrato que admita esos faltantes, no convertir tipos solo para silenciar la advertencia. Todo `stderr` no reconocido y cualquier fallo del CLI se muestran explícitamente y deben investigarse antes de continuar: ocultar un error real elimina evidencia de reproducibilidad.

Los comandos CLI siguen siendo una alternativa equivalente para automatización o diagnóstico, no un requisito de la clase:

```bash
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.train --model dummy
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.train --model logistic
MLFLOW_TRACKING_URI=http://127.0.0.1:5000 uv run python -m invoiceops.ml.train --model random_forest
```

El CLI acepta solamente `dummy`, `logistic` o `random_forest`; no introducir hiperparámetros o algoritmos nuevos durante la demo.

Explicar jerarquía:

```text
Experiment: invoice-risk
  -> Run
     -> params
     -> metrics
     -> tags
     -> artifacts
```

Continuar con las celdas de comparación de Notebook 03 y abrir la UI de MLflow. Mostrar: `run_id`, modelo, accuracy, precision, recall, F1, ROC AUC, versión de dataset y commit Git. Señalar que `Latest` describe cronología, no calidad: `Best` requiere criterios explícitos, evidencia y decisión humana. El Notebook 03 puede crear Tracking mediante el CLI, pero no registra ni promueve modelos en el Registry.

Definir lineage:

```text
dataset version + feature schema + params + git commit + run -> model artifact
```

Preguntar: “¿Qué cambió entre dos runs? ¿Cuál tiene más recall? ¿Cuál tiene más precision? ¿Podemos reproducirlo?”

Frase clave: MLflow no entrena; scikit-learn entrena y MLflow registra evidencia. Git versiona código, pero no basta para describir datos, parámetros, runtime y artifact.

### 02:20-02:38 | Registry, Quality Gate y promotion explícita

Ejecutar `notebooks/04_registry_gate_and_promotion.ipynb` después de explicar que usa un Tracking aislado en `var/t23_5_demo/`. El notebook prepara cuatro runs reales y es idempotente para no registrar versiones o promociones duplicadas al reejecutar una celda mutable.

Primero preguntar: “¿Tracking y Registry responden la misma pregunta?”

```text
Tracking: ¿qué experimentamos?
Registry: ¿qué artefactos son modelos controlados del sistema?
```

Explicar:

```text
run -> register -> model version -> challenger -> promotion -> champion
```

- Un run no es una model version.
- `challenger` y `champion` son aliases móviles.
- `latest` es cronológico; no significa `best`.
- Las versiones permanecen; cambiar un alias no reescribe ni borra una versión.

Antes del código, fijar el vocabulario: un **candidato** es un run en evaluación, no una versión ni una decisión. La secuencia es A/B/C/D:

- A/B: dos runs independientes de `random_forest`. Tienen la misma métrica por datos y seed deterministas, pero distinto `run_id`; sirven para separar Run de Model Version y mostrar alias switching.
- C: `logistic`, candidato real para comparar algoritmo. Si falla el Gate por recall, sigue siendo un run válido pero no elegible.
- D: `dummy`, baseline observado. Su accuracy puede parecer alta, pero recall 0 demuestra por qué accuracy sola no decide utilidad.

Mostrar la tabla alineada por candidato, el gráfico precision vs recall y el Gate individual. A/B se desplazan levemente **solo en el dibujo** para que no se oculten en las mismas coordenadas: las métricas reales no cambian. El gráfico de barras acompaña el scatter para reconocer accuracy, precision y recall sin inferirlas visualmente. El Gate real exige:

```text
recall >= 0.18
precision >= 0.48
```

El resultado se lee del dataset aislado; no inventar números ni hardcodear `run_id` o versiones. En la ejecución determinista A/B deben dar los dos PASS y D un FAIL. C se explica con su resultado real: si eventualmente aprobara, sigue sin Registration o Promotion automática.

Mostrar la regla de gobierno:

```text
Gate: evalúa elegibilidad
Promotion: mueve champion mediante una decisión explícita
```

Un PASS no registra ni promueve automáticamente. Seguir los tres rótulos de cada bloque: **Qué ocurre**, **Qué se ejecuta** y **Qué comprobar**. En Registry, mostrar estado antes/después de registrar A y B: las versiones aparecen como `vN`, los aliases como texto y la fecha en UTC legible. En Promotion, mostrar estado antes/después y el historial A -> B -> A -> B; el primer origen debe leerse `sin champion`, nunca `NaN`. Repetir: “cambia el alias; la Model Version no se reescribe ni se borra.” C fallido muestra el bloqueo antes de mutar Registry; D se declara baseline observada y no se intenta promover.

### 02:38-02:58 | Serving, Policy, auditoría y fallback

Abrir Notebook 05 únicamente después de 04. El notebook resuelve `models:/invoice-review@champion`, inicia Uvicorn local en un puerto libre, espera `/health` y guarda el proceso que creó para limpiarlo de forma segura.

Mostrar primero:

```text
GET <BASE_URL>/health
```

La respuesta identifica `status`, `model_name`, `model_version` y `run_id`. Preguntar: “¿Por qué debemos saber con exactitud qué versión está cargada?”

El Model API carga el modelo una vez al startup. La promoción no actualiza mágicamente el proceso en memoria:

```text
promote champion
-> stop API
-> start API
-> verify /health
```

No implementar ni sugerir hot reload para la demo. Registry state no es runtime state.

Mostrar el contrato de inferencia: `/predict` recibe exactamente las ocho features y devuelve una probabilidad más identidad del modelo. No devuelve `AUTO_PROCESS` ni `MANUAL_REVIEW`.

Ejecutar `INV-10029` y `INV-10030` usando `invoice_to_features`, nunca un contrato reescrito a mano. Para cada una, mostrar en una tabla:

```text
Rule v1 recommendation | Model probability | Policy recommendation
```

Luego enseñar la Policy real:

```text
probability >= 0.80 -> MANUAL_REVIEW
probability <  0.80 -> AUTO_PROCESS
```

La probabilidad y la recomendación no son lo mismo. El modelo estima `P(manual_review_required=1)`; `ml-policy-v1` aplica una decisión de negocio versionada. La decisión final puede diferir y se debe auditar por separado.

Persistir la evaluación de `INV-10030` con la API real de base de datos y mostrar el historial. Debe incluir modelo, versión, run, probabilidad, policy, threshold, recomendación, source, reason, correlación y fecha. `model_evaluations` no es una final decision.

Completar el flujo A/B: promover A, reiniciar API, verificar `/health`, predecir y persistir; repetir para B. La misma factura y la misma Policy pueden recibir scores distintos por model version distinta. No exigir que las recomendaciones sean diferentes.

Finalmente detener la API y mostrar el fallback real:

```text
recommendation = MANUAL_REVIEW
source = fallback
reason = model_unavailable
probability = null
```

Insistir: el fallback seguro no inventa `probability=1.0`.

### 02:58-03:00 | Cierre y Trabajo 1

Pedir una explicación de un estudiante usando esta frase como estructura:

> “Un modelo aprende desde datos históricos, pero entrenarlo no basta: hay que evaluarlo, registrar experimentos, versionar el artifact, controlar qué se promueve, servirlo tras una interfaz estable y separar inferencia de política de negocio.”

Cerrar con tres mensajes:

1. `Accuracy` por sí sola no define utilidad.
2. MLflow no hace inteligente al modelo; hace gobernable su lifecycle.
3. El modelo estima; la Policy recomienda; la organización decide.

Liberar Trabajo 1 y recordar que no evalúa memorización de definiciones, sino diseño de automatización, datos, métricas, tracking, Gate, Registry, inferencia y gobierno. El uso de IA está permitido solo si cada decisión se puede comprender, validar y defender.

Puente a Clase 3:

```text
model_evaluation -> evidence record -> hash -> Merkle tree -> blockchain anchor
```

No explicar blockchain aún. Dejar la pregunta: “¿Cómo demostramos en seis meses que esta evidencia no fue modificada?”

## Recuperación durante la clase

| Problema | Acción docente |
|---|---|
| Jupyter o kernel falla | Usar render 04/05 si aplica y continuar con el razonamiento. |
| Training falla | Usar runs existentes; no hacer depender toda la sesión de entrenamiento en vivo. |
| MLflow falla | Mostrar Notebook 03 o explicar la jerarquía y lineage con la evidencia disponible. |
| Registry falla | Mostrar el diagrama de run/version/alias y continuar con el concepto. |
| Model API falla | Convertirlo en demo de fallback: `MANUAL_REVIEW`, sin score falso. |
| Tiempo insuficiente | Reducir BCE, Gini y activaciones; nunca cortar baseline, precision/recall, Tracking vs Registry, Model vs Policy o Trabajo 1. |

## Checklist previo a impartir

- [ ] Python 3.12, `uv sync --all-groups` y kernel `InvoiceOps Python 3.12` listos.
- [ ] Portal iniciado en modo `demo` solo si se usará.
- [ ] MLflow compartido responde en `:5000`; kernel/terminal de 01-03 tienen `MLFLOW_TRACKING_URI`.
- [ ] `reset_demo.py` se ejecutó antes de generar auditorías demostrativas.
- [ ] Notebooks 01, 02 y 03 ejecutables con sus precondiciones.
- [ ] Notebook 04 produce candidatos reales, 2 PASS y 1 FAIL, en su estado aislado.
- [ ] Notebook 05 se ejecutará después de 04 y puede reservar un puerto local libre.
- [ ] Renders 04 y 05 disponibles como contingencia.
- [ ] Se puede explicar con precisión que portal solo visualiza auditorías y no integra Model API.
- [ ] Se puede explicar que el API carga el modelo solo al startup y que Promotion requiere restart más `/health` para cambiar el runtime.
