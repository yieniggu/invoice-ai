# Workflow UiPath InvoiceOps

`InvoiceOps-RPA-Demo.uis` contiene el workflow actualizado. `InvoiceOps-RPA-Demo/` conserva la fuente revisable del proyecto y permite revisar `Main.xaml` directamente.

## Propiedad de artefactos

La fuente revisable es `InvoiceOps-RPA-Demo/` (`Main.xaml`, `project.json`, `project.uiproj`, `entry-points.json` y `.project/`). El paquete distribuible intencional es `InvoiceOps-RPA-Demo.uis`, junto con el manifiesto de solución. El paquete conserva esos archivos requeridos y excluye los directorios que Studio genera por máquina (`.local/`, `.storage/` y `.screenshots/`); esos directorios tampoco son fuente y están ignorados, por lo que no se deben agregar al repositorio.

## Ruta rápida

1. Inicie InvoiceOps en modo demo y restablezca sus datos antes de cada prueba.
2. Importe `uipath/InvoiceOps-RPA-Demo.uis` en UiPath Studio Web y ejecute en `On local machine`.
3. Pruebe `INV-10023` y `INV-10024`; revise el estado final y el log.

## Requisitos

- Chrome instalado.
- UiPath Studio Web compatible con proyectos Windows/Portable y las actividades declaradas en el proyecto.
- Robot local y extensión de navegador UiPath, cuando el entorno seleccionado los requiera.
- InvoiceOps disponible en `http://127.0.0.1:8000` con `INVOICEOPS_MODE=demo`.
- Credenciales demo: `Main.xaml` incluye los valores por defecto `analyst` / `demo-password`. Son datos demo no secretos y deben coincidir con el modo de arranque elegido.

## Checkpoints

1. Login con username, password y Sign in.
2. Búsqueda por ID de factura.
3. Apertura del enlace de la factura encontrada.
4. Lectura UI de amount, PO y three-way match.
5. Rule v1: `amount <= 5000` y PO y match produce `AUTO_PROCESS`; el resto produce `MANUAL_REVIEW`.
6. Click en la acción correspondiente.
7. Verificación de `invoice-status`; una diferencia lanza excepción y una coincidencia registra `<invoiceId>: <status>`.

## Configuración y pruebas

Abra `InvoiceOps-RPA-Demo/Main.xaml` en UiPath Studio y, en el panel **Variables** de la secuencia raíz, ajuste `baseUrl`, `invoiceId`, `username` y `password`. `password` tiene el valor demo por defecto `demo-password` en el workflow; no introduzca ni guarde secretos reales. Antes de ejecutar, abra Chrome manualmente en `baseUrl + "/login"`.

Después de restablecer InvoiceOps, ejecute:

| Factura | Resultado esperado |
| --- | --- |
| `INV-10023` | `AUTO_PROCESSED` si cumple Rule v1 |
| `INV-10024` | `MANUAL_REVIEW` si no cumple Rule v1 |

## Importación en Studio Web

En Studio Web, seleccione **Import project** y cargue `uipath/InvoiceOps-RPA-Demo.uis`. El archivo es una solución ZIP; no existe ni se requiere un archivo `.uip`. Use el proyecto interno `InvoiceOps-RPA-Demo` que aparece tras la importación.

## Guía docente: desde cero en Studio Web

Esta guía está pensada para que cada estudiante ejecute el robot en **su propia máquina**. El navegador y InvoiceOps deben vivir en el mismo equipo: `127.0.0.1` no apunta al servidor de la clase ni al navegador de otra persona.

### 0. Antes de abrir Studio Web

Use esta lista en orden. Si falla un punto, no continúe al siguiente.

- [ ] Tener acceso a un tenant de UiPath Automation Cloud y permiso para crear/importar un proyecto.
- [ ] Tener Chrome instalado y actualizado en la máquina que ejecutará el robot.
- [ ] Tener UiPath Assistant/Robot local disponible e iniciado, si el tenant lo requiere para **On local machine**.
- [ ] Tener instalada y habilitada la extensión oficial de UiPath para Chrome cuando el robot no pueda indicar elementos web.
- [ ] Tener InvoiceOps iniciado en la misma máquina y comprobar en Chrome que `http://127.0.0.1:8000/login` abre la página **Sign in | InvoiceOps**.
- [ ] Ejecutar InvoiceOps en modo demo y hacer coincidir `username` y `password` de **Variables** con el modo elegido: Portal local por `uv` usa las variables de su entorno local; Docker Compose usa `analyst` / `demo-password` fijados en `compose.yml`.
- [ ] Restablecer los datos demo antes de cada ejecución que vaya a hacer click en una decisión; las acciones cambian el estado de la factura.

> No comparta ni suba contraseñas reales al proyecto. Para la demo use solo credenciales de demo locales.

### 1. Importar la solución

1. Descargue o copie el archivo `uipath/InvoiceOps-RPA-Demo.uis` desde este repositorio sin renombrarlo ni descomprimirlo.
2. Abra UiPath Studio Web en el tenant asignado por el docente.
3. Cree un proyecto nuevo solo si la interfaz exige un destino para la importación; no recree manualmente el workflow.
4. Use la acción **Import project** (el texto puede variar ligeramente según el tenant) y seleccione el archivo `.uis`.
5. Espere a que Studio muestre el proyecto interno **InvoiceOps-RPA-Demo**.
6. Abra `Main.xaml`. Debe ver una secuencia raíz llamada **InvoiceOps RPA Demo** y las siete secciones `01 Login` a `07 Verify Result`.

Si Studio Web no ofrece importación de `.uis` en el tenant, no cree un proyecto desde cero. Entregue al docente el mensaje exacto y use la carpeta `uipath/InvoiceOps-RPA-Demo/` como fuente para importarla/abrirla mediante el procedimiento habilitado en su tenant. Esa carpeta coincide con el contenido del archivo `.uis`.

### 2. Configurar la ejecución local

1. En Studio Web, elija **On local machine** como destino de ejecución.
2. Confirme que el dispositivo o robot local aparece conectado. Si no aparece, inicie sesión en UiPath Assistant con la misma cuenta/tenant y vuelva a cargar Studio Web.
3. Abra `Main.xaml` y seleccione la secuencia raíz **InvoiceOps RPA Demo**.
4. En Variables, conserve `baseUrl` como `http://127.0.0.1:8000` salvo que el docente indique otro host local.
5. En el panel **Variables**, defina `username` y `password` para que coincidan con la instancia demo local. Los valores por defecto `analyst` / `demo-password` de `Main.xaml` corresponden a Docker Compose; para Portal local por `uv`, use las variables del entorno con que inició ese Portal. Son credenciales demo, no secretos reales.
6. Defina `invoiceId` con el caso que se va a probar.
7. Guarde el proyecto antes de ejecutar.

El workflow usa `AttachMode="ByInstance"`, valor compatible con el scaffold importado. No se verificó en Studio Web que `TargetApp Url` abra Chrome o navegue a `baseUrl + "/login"` con este modo. Antes de ejecutar, el estudiante debe abrir Chrome manualmente en `baseUrl + "/login"` y mantener esa pestaña disponible para que el workflow se adjunte a ella.

### 3. Ejecutar los checkpoints incrementales

Ejecute cada checkpoint sobre datos restablecidos. Para aislar un checkpoint, seleccione temporalmente la secuencia correspondiente en Studio y use la ejecución disponible para la actividad o ejecute hasta ese punto según la interfaz del tenant. Restaure el flujo completo antes de la demostración final.

| Checkpoint | Secciones incluidas | Qué debe comprobar |
| --- | --- | --- |
| 1 | `01 Login` | Con Chrome abierto manualmente en `/login`, redirige al listado sin mensaje de error. |
| 2 | 01–02 | El campo de búsqueda recibe el ID y se muestran resultados. |
| 3 | 01–03 | Se abre la página de detalle de la factura elegida. |
| 4 | 01–04 | Se obtienen amount, PO y three-way match desde la interfaz. |
| 5 | 01–05 | Se calculan `decision` y `expectedStatus`, sin hacer click de decisión. |
| 6 | 01–06 | Se ejecuta Process o Manual Review una sola vez. |
| 7 | 01–07 | El estado UI coincide y el log muestra `<invoiceId>: <status>`. |

No use el checkpoint 6 dos veces para la misma factura sin reset: la primera ejecución ya modifica el estado y los botones de decisión dejan de estar disponibles cuando la factura deja de estar `PENDING`.

### 4. Casos de demostración

1. Restablezca InvoiceOps.
2. Asigne `invoiceId = "INV-10023"`.
3. Ejecute el flujo completo. Debe seleccionar `AUTO_PROCESS`, hacer click en el elemento con `data-testid="invoice-process"`, finalizar en `AUTO_PROCESSED` y registrar `INV-10023: AUTO_PROCESSED`.
4. Restablezca InvoiceOps otra vez.
5. Asigne `invoiceId = "INV-10024"`.
6. Ejecute el flujo completo. Debe seleccionar `MANUAL_REVIEW`, hacer click en `data-testid="invoice-manual-review"`, finalizar en `MANUAL_REVIEW` y registrar `INV-10024: MANUAL_REVIEW`.

La regla no se interpreta: solo auto-procesa cuando `amount <= 5000`, hay PO y hay three-way match. Cualquier otro caso va a revisión manual.

### 5. Qué mirar en el workflow

| Área | Contrato estable usado |
| --- | --- |
| Usuario | `name="username"` |
| Contraseña | `name="password"` y `type="password"` |
| Búsqueda | `id="query"` y `name="q"` |
| Factura | enlace con `href="/invoices/<invoiceId>"` y su ID |
| Datos y estado | `data-testid` de amount, PO, match y status |
| Decisiones | `data-testid="invoice-process"` y `data-testid="invoice-manual-review"` |

El botón de proceso puede decir **Process** o **Complete**. El workflow no usa ese texto para decidir el target: usa `data-testid="invoice-process"`.

### 6. Recuperación rápida de errores

| Síntoma | Causa probable | Acción correcta |
| --- | --- | --- |
| `NodeNotFoundException` antes de login | InvoiceOps no está disponible localmente, Chrome/Robot/extensión no están listos o el selector no se resolvió. | Abra `/login` manualmente, confirme el robot local y la extensión, y vuelva a ejecutar. |
| `Failed to create a 'AttachMode' from the text 'ByUrl'` | Studio Web no admite `ByUrl` en la versión importada. | Reimporte `InvoiceOps-RPA-Demo.uis`, que usa `AttachMode="ByInstance"`, y abra Chrome manualmente en `/login` antes de ejecutar. |
| No aparece un robot local | Assistant/Robot está desconectado o pertenece a otro tenant. | Inicie Assistant con la cuenta correcta y confirme **On local machine**. |
| Falla el click de decisión | La factura ya no está `PENDING` o no se hizo reset. | Restablezca InvoiceOps y ejecute una única vez. |
| Status no coincide | Los datos no fueron restablecidos o se usó un ID distinto. | Revise `invoiceId`, reinicie la demo y repita desde checkpoint 1. |
| Studio muestra un selector distinto al reindicar | Diferencia entre el browser/extension local y el Unified Target. | Reindique solo el elemento afectado; conserve el mismo atributo estable real, especialmente `data-testid`. |

### 7. Límites de la práctica

- El workflow no usa API, base de datos ni Playwright: todos los datos se leen de la UI.
- No cambie Rule v1, HTML ni selectores de la aplicación para “hacerlo funcionar”. Reporte primero el bloqueo al docente.
- La primera ejecución debe hacerse con supervisión docente para confirmar que la versión local de Studio, Robot y extensión interpreta los Unified Targets como se espera.
- Si cambia de máquina, repita el checklist de la sección 0; una configuración local no se transfiere automáticamente a otro estudiante.

## Troubleshooting

`NodeNotFoundException` antes de login puede indicar que Chrome no está abierto en `baseUrl + "/login"` o que el selector no se resolvió. Abra esa URL manualmente, confirme que InvoiceOps responde y que Chrome, el robot local y la extensión UiPath estén disponibles.

Los selectores de detalle usan `data-testid`, y los de login/búsqueda usan atributos reales estables. La sintaxis Unified Target se preservó desde el scaffold de `UiPath.UIAutomation.Activities 26.10.3`, pero este entorno no ejecutó UiPath Studio ni Chrome: valide una primera ejecución local. Si Studio expone un selector diferente para `data-testid`, reindique únicamente ese elemento conservando el atributo `data-testid` como identificador principal.
