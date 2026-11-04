# Handoff tÃ©cnico: API REST para MarkItDown

## Objetivo

Este documento es el punto de partida para el siguiente agente. Describe la implementaciÃ³n existente y el estado confirmado del despliegue. No recrear el proyecto ni borrar volÃºmenes durante tareas de diagnÃ³stico sin autorizaciÃ³n explÃ­cita.

## Estado de implementaciÃ³n

- ImplementaciÃ³n en `packages/markitdown-api/`: FastAPI, worker, cola Redis, PostgreSQL, almacenamiento temporal, Dockerfile, Compose, README y pruebas.
- **Estado remoto confirmado (2026-11-03):** Dokploy `xperiencIA` / `production`; Compose `markitdown`; ruta `packages/markitdown-api/compose.yaml`; dominio `mdservice.innersoft.com.mx` asignado a `api:8000`.
- El Ãºltimo despliegue consultado estÃ¡ en `done`; `api`, `worker`, `postgres` y `redis` estÃ¡n activos. `https://mdservice.innersoft.com.mx/health/live` y `/health/ready` respondieron `200`.
- Se corrigiÃ³ el `404 page not found`: Dokploy tenÃ­a el dominio configurado, pero los contenedores vivos no tenÃ­an labels Traefik ni estaban en `dokploy-network`. Se volviÃ³ a desplegar; el contenedor `api` ahora sÃ­ tiene las reglas y la red del proxy.
- La rama desplegada es `HP240RG10-dev`. Confirmar la rama y el estado en Dokploy antes de cambios posteriores.
- SMTP debe estar configurado si `EMAIL_VERIFICATION_REQUIRED=true` (valor predeterminado). Los pagos no estÃ¡n integrados.
- OCR estÃ¡ disponible explÃ­citamente mediante el plugin MarkItDown + Azure OpenAI si se configuran sus secretos; por defecto no se invoca.
- El despliegue inicial usa una instancia API, un worker y una conversiÃ³n simultÃ¡nea. No aumentar concurrencia sin medir la VPS de 2 GB.
- El repositorio estÃ¡ indexado en Codebase Memory con el nombre `markitdown`.
- La suite del paquete API registrÃ³ `18 passed`; Black/Ruff pasaron. El build de Docker no se pudo probar en el entorno local porque no habÃ­a daemon Docker; Dokploy sÃ­ completÃ³ el build remoto.

## Requisitos acordados

- VPS actual: Ubuntu, Dokploy, 2 nÃºcleos, 2 GB de RAM y 50 GB de disco.
- Dokploy gestiona la exposiciÃ³n pÃºblica y el enrutamiento del dominio.
- Aceptar archivos y URLs HTTP/HTTPS pÃºblicas.
- TamaÃ±o mÃ¡ximo de archivo o descarga: 100 MB.
- Habilitar los formatos disponibles de MarkItDown segÃºn sus dependencias; dejar preparados OCR y proveedores externos como Azure.
- OCR y servicios externos deben ser explÃ­citos y estar desactivados por defecto.
- Cuentas individuales por cliente, claves API y mediciÃ³n de consumo.
- No integrar pagos en esta etapa. La unidad facturable se decidirÃ¡ despuÃ©s de evaluar pruebas.
- Conservar registros de consumo indefinidamente.
- Eliminar el archivo de entrada al finalizar el intento de conversiÃ³n.
- Conservar el resultado hasta una descarga completa o durante un mÃ¡ximo de 1.440 minutos (24 horas), lo que ocurra primero; eliminarlo despuÃ©s de una descarga completa.
- Empezar con una conversiÃ³n simultÃ¡nea y una cola limitada, y ajustar los lÃ­mites con pruebas de carga.

## Arquitectura implementada

```text
Cliente
  |
  v
Dominio y proxy gestionados por Dokploy
  |
  v
API REST (solo servicio expuesto)
  |                    \
  v                     v
PostgreSQL             Redis / cola
                         |
                         v
                  Worker MarkItDown
                         |
                         v
             Volumen temporal de archivos
```

- **API:** recibe solicitudes, autentica clientes, valida lÃ­mites y crea trabajos.
- **Redis:** cola de trabajos y coordinaciÃ³n; no expuesto pÃºblicamente.
- **PostgreSQL:** cuentas, claves API protegidas, estados de trabajos y registros de consumo.
- **Worker:** ejecuta la conversiÃ³n con la API Python de MarkItDown, separado del proceso HTTP.
- **Volumen temporal:** mantiene las entradas mientras estÃ¡n pendientes/en proceso y los resultados hasta su entrega o expiraciÃ³n. Debe tener lÃ­mites y limpieza automÃ¡tica.
- **Dokploy:** publica el servicio API en el dominio configurado. Redis, PostgreSQL y workers permanecen en la red interna de Docker.

Con 2 GB de RAM, mantener un worker con concurrencia 1. Redis y PostgreSQL se ejecutan en la misma VPS que Dokploy; conversiones complejas pueden agotar memoria. No anunciar capacidad de alta carga hasta medirla. El Compose limita recursos y resultados temporales, pero no elimina esta limitaciÃ³n fÃ­sica.

## Contrato inicial de API

- `POST /v1/conversions`: recibe un archivo multipart y responde `202 Accepted` con el ID del trabajo.
- `POST /v1/conversions/from-url`: recibe una URL pÃºblica y responde `202 Accepted` con el ID del trabajo.
- `GET /v1/conversions/{id}`: devuelve el estado y errores pÃºblicos del trabajo.
- `GET /v1/conversions/{id}/result`: entrega el Markdown solo al propietario del trabajo.
- Endpoints de cuenta y claves API para registro/autenticaciÃ³n, rotaciÃ³n y revocaciÃ³n.
- Endpoint de consumo para consultar las mÃ©tricas disponibles.
- Endpoints de salud (`live` y `ready`) para operaciÃ³n y despliegue.

Estados sugeridos: `queued`, `running`, `succeeded`, `failed`, `expired` y `cancelled`.

La descarga se considerarÃ¡ completa cuando el servidor finalice el envÃ­o de la respuesta sin desconexiÃ³n del cliente. Si el cliente se desconecta antes de completar la transferencia, conservar el resultado para un nuevo intento hasta la caducidad. El servidor no puede confirmar que el cliente haya guardado el archivo; si se requiere confirmaciÃ³n de aplicaciÃ³n, diseÃ±ar un endpoint de acuse de recibo.

## Procesamiento, lÃ­mites y capacidad actuales

1. Archivos y descargas HTTP se transmiten a disco temporal; la API exige `Content-Length` para cargas multipart.
2. LÃ­mite de entrada: 100 MiB; mÃ¡ximo de 5 trabajos pendientes; un upload multipart procesÃ¡ndose a la vez.
3. Un worker y una conversiÃ³n simultÃ¡nea.
4. LÃ­mite de salida: 50 MiB por resultado; mÃ¡ximo agregado de 2 GiB en resultados pendientes de descarga; expiraciÃ³n de 1.440 minutos.
5. Timeout de conversiÃ³n: 900 segundos. La cola llena devuelve un error controlado (`429`/`503`).
6. Los lÃ­mites estÃ¡n configurados mediante variables de entorno en `packages/markitdown-api/compose.yaml`.
7. Carga por encima de 100 MiB no estÃ¡ soportada; aceptaciÃ³n de un tamaÃ±o no garantiza que cualquier formato complejo quepa en la memoria disponible.

La categorÃ­a â€œtodos los formatosâ€ debe validarse contra las capacidades y dependencias reales de MarkItDown. Los extras `[all]`, herramientas del sistema, OCR y ciertas integraciones pueden aumentar el tamaÃ±o de la imagen o requerir servicios externos. No asumir que todos los formatos tienen conversiÃ³n local equivalente.

## Seguridad para archivos y URLs

- Autenticar con claves API enviadas en cabecera; no aceptar claves en query strings. Guardar hashes de claves, mostrarlas al cliente solo al crearlas y permitir revocarlas.
- Limitar tamaÃ±o de entrada/descarga, tiempo total, tamaÃ±o de salida, frecuencia de solicitudes y trabajos concurrentes por cuenta.
- Usar nombres aleatorios para temporales; nunca construir rutas a partir del nombre enviado por el usuario.
- Validar extensiÃ³n y contenido; tratar documentos y archivos comprimidos como entrada no confiable.
- Aceptar solo esquemas HTTP/HTTPS para URLs. Rechazar destinos localhost, privados, link-local, reservados y otros rangos no pÃºblicos.
- Validar DNS y cada redirecciÃ³n; limitar puertos, redirecciones, tiempo y bytes descargados. Evitar que el servicio pueda acceder a metadatos de nube o servicios internos.
- Siempre que sea posible, descargar el contenido con un componente controlado y entregar los bytes al conversor, en vez de pasar una URL arbitraria directamente al conversor.
- Ejecutar workers sin privilegios y con acceso de red saliente restringido cuando las integraciones habilitadas lo permitan.
- No registrar contenido, Markdown, claves, nombres de archivo ni URLs completas. Usar identificadores de trabajo para correlaciÃ³n.
- Mantener dependencias actualizadas y eliminar temporales tambiÃ©n ante fallos, cancelaciones y reinicios.

## RetenciÃ³n y registro de consumo

Los archivos y resultados son efÃ­meros; los metadatos de consumo se conservan indefinidamente, segÃºn lo acordado. No almacenar el contenido fuente ni el Markdown en la tabla de consumo.

Campos propuestos por conversiÃ³n:

- ID de conversiÃ³n y cuenta propietaria.
- Fecha/hora, tipo de entrada, formato/extensiÃ³n y estado.
- Bytes exactos de entrada (`input_bytes`); derivar MB como `input_bytes / 1_000_000` y documentar esta convenciÃ³n.
- NÃºmero de pÃ¡ginas (`page_count`), nullable cuando el formato o convertidor no lo pueda determinar de forma fiable.
- Longitud del resultado en caracteres (`output_char_count`) y bytes UTF-8 (`output_utf8_bytes`).
- DuraciÃ³n, categorÃ­a de error y proveedor externo utilizado, si corresponde.

Vigilar el crecimiento de registros indefinidos, Ã­ndices, copias de seguridad y capacidad de PostgreSQL. Evitar guardar datos identificables no necesarios. Definir posteriormente cÃ³mo se anonimizan o eliminan los vÃ­nculos a una cuenta cuando se solicite su cierre.

## Cuentas, claves y facturaciÃ³n

- Implementado: cuenta individual, verificaciÃ³n de correo, login con token de sesiÃ³n, recuperaciÃ³n de contraseÃ±a y gestiÃ³n de claves API.
- Las claves API se entregan una sola vez, se almacenan como HMAC y se envÃ­an en `X-API-Key`; las sesiones usan `Authorization: Bearer`.
- Hay un mÃ¡ximo de 20 claves activas por cuenta. Restablecer una contraseÃ±a revoca las claves y cambia la versiÃ³n de sesiÃ³n.
- `GET /v1/usage` devuelve registros paginados; no procesa pagos ni almacena tarjetas.
- El proveedor de pagos, paÃ­ses, moneda y unidad facturable siguen pendientes.

## OCR y servicios externos

- Implementado mediante el plugin OCR de MarkItDown con Azure OpenAI/OpenAI-compatible.
- Se habilita por conversiÃ³n (`ocr=true`) y falla con `ocr_provider_not_configured` si faltan secretos; por defecto estÃ¡ apagado.
- Secretos en Dokploy: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_MODEL`.
- Habilitar OCR envÃ­a imÃ¡genes de documentos al endpoint configurado; confirmar consentimiento, coste y privacidad antes de hacerlo por cliente.

## Estado de fases y siguiente trabajo

1. **Implementado:** API versionada, auth, conversiÃ³n por archivo y URL, cola, worker, lÃ­mites y retenciÃ³n.
2. **Implementado:** Compose para Dokploy, servicio pÃºblico `api:8000`, Redis/PostgreSQL/worker internos y healthchecks.
3. **Implementado:** tests funcionales y de conversiÃ³n; el CI tiene un job `api-tests`.
4. **Pendiente antes de escala:** realizar benchmark con formatos y tamaÃ±os representativos en la VPS; no hacerlo desde un entorno local si la instrucciÃ³n activa del usuario lo prohÃ­be.
5. **Pendiente de producciÃ³n:** establecer backups/restauraciÃ³n de PostgreSQL, migraciones versionadas (hoy el arranque usa `metadata.create_all`), polÃ­ticas de anonimizaciÃ³n/cierre de cuenta y abuso de alta frecuencia en registro/reset.
6. **Pendiente posterior:** seleccionar proveedor de pagos y unidad de cobro, sin almacenar datos de tarjeta.

## Procedimiento operativo Dokploy

- El Compose debe apuntar a `packages/markitdown-api/compose.yaml` en la raÃ­z del checkout del repositorio `experiencIA-consulting/markitdown`.
- El dominio debe seleccionar `serviceName=api`, puerto interno `8000`, path `/`; nunca seleccionar `worker`.
- Para 404, comparar `compose.getConvertedCompose` con `docker.getConfig` del contenedor vivo: la conversiÃ³n debe tener labels Traefik y `dokploy-network`.
- DespuÃ©s de cambiar dominios/rutas, redeploy Compose para recrear contenedores con los labels. Healthchecks confirmados: `/health/live` y `/health/ready`.
- El flujo API usa `POST /v1/accounts` â†’ verificar correo â†’ `POST /v1/auth/token` â†’ `POST /v1/api-keys`; ver ejemplos en `packages/markitdown-api/README.md`.
- Errores observados histÃ³ricamente: Nixpacks no correspondÃ­a a este monorepo; `composePath` incorrecto causaba â€œCompose file not foundâ€; volumen PostgreSQL persistente puede conservar una contraseÃ±a anterior aunque `POSTGRES_PASSWORD` cambie en Dokploy. No borrar volÃºmenes para resolver auth DB sin confirmar pÃ©rdida de datos; sincronizar el rol en PostgreSQL de forma segura.

## Criterios de aceptaciÃ³n

- Una cuenta no puede consultar conversiones ni resultados de otra.
- Claves API se pueden revocar y no aparecen en logs ni respuestas posteriores a su creaciÃ³n.
- Archivos y descargas de hasta 100 MB se procesan sin cargarse enteros en memoria.
- URLs a destinos internos/privados, redirecciones peligrosas y descargas excesivas se bloquean.
- La cola y la concurrencia respetan los lÃ­mites y rechazan carga excesiva de forma controlada.
- El archivo fuente se elimina al terminar; el resultado se elimina tras transferencia completa o al vencer 24 horas; una transferencia interrumpida no consume el resultado.
- Las mÃ©tricas acordadas se guardan indefinidamente sin almacenar documentos ni Markdown.
- OCR/Azure no se invocan sin habilitaciÃ³n explÃ­cita y credenciales configuradas.
- Dokploy expone Ãºnicamente la API; dependencias internas no tienen puertos pÃºblicos.
- Las pruebas de carga documentan memoria, CPU, disco, tiempos y fallos para decidir si se amplÃ­a la VPS o se separan workers.

## Pendientes para una fase posterior

- Elegir proveedor de pagos, paÃ­ses, moneda, planes y unidad facturable.
- Decidir si se requiere acuse explÃ­cito del cliente para considerar completa la descarga.
- Confirmar la lista exacta de formatos/extras locales y proveedores OCR/Azure a soportar.
- Establecer polÃ­tica de anonimizaciÃ³n de mÃ©tricas si se cierra una cuenta.

## Mejoras implementadas: validaciÃ³n de formatos y lÃ­mites de memoria

ImplementaciÃ³n local en `packages/markitdown-api/`; **todavÃ­a no desplegada ni medida en la VPS**. Mantener un worker y una conversiÃ³n simultÃ¡nea.

### ValidaciÃ³n de entradas

- `src/markitdown_api/input_validation.py` clasifica el contenido con Magika y compara el tipo detectado con una lista de extensiones soportadas por la imagen actual. Rechaza formatos no admitidos, discrepancias claras y contenido no clasificable con `unsupported_format`, `file_type_mismatch` o `invalid_file_content`.
- Valida la cabecera PDF, reconoce ZIP/Office Open XML/EPUB y limita contenedores con `MAX_ARCHIVE_ENTRIES` (predeterminado 5.000) y `MAX_ARCHIVE_EXPANDED_BYTES` (128 MiB). Estos lÃ­mites no equivalen a anÃ¡lisis antivirus.
- Se ejecuta en el subprocess del worker antes de MarkItDown, tanto para archivos cargados como descargados desde URL. Por el contrato asÃ­ncrono, la solicitud puede responder `202`; una entrada invÃ¡lida despuÃ©s queda como trabajo `failed` con su `error_code`. Se hace asÃ­ para no cargar el modelo Magika en el proceso API limitado a 256 MiB.
- Si el nombre no tiene extensiÃ³n, se infiere una extensiÃ³n admitida cuando Magika puede identificarla. Mantener `SUPPORTED_EXTENSIONS` alineado con los conversores/dependencias instalados; no representa automÃ¡ticamente todos los formatos opcionales de MarkItDown.

### Recursos y resultados

- El worker mide el RSS de su subprocess y lo termina si supera `MAX_CONVERSION_MEMORY_BYTES` (predeterminado 512 MiB), reportando `resource_limit_exceeded`; conserva el timeout existente de 900 segundos. El worker en Compose queda limitado por `WORKER_MEMORY_LIMIT` (768 MiB) y `WORKER_CPUS` (1 CPU). El lÃ­mite del contenedor es el respaldo ante memoria de procesos descendientes o picos entre sondeos; un OOM que mate el worker completo aÃºn requiere validaciÃ³n operativa.
- Markdown se escribe en bloques UTF-8 de 64 KiB y se descarta si supera `MAX_OUTPUT_BYTES` (50 MiB). Esto evita crear una segunda copia completa codificada y elimina temporales parciales. **MarkItDown todavÃ­a materializa el Markdown completo en memoria antes de aplicar el lÃ­mite de salida**; el lÃ­mite RSS es el control para ese pico, no streaming de conversiÃ³n.
- Variables nuevas configurables en Compose y documentadas en `.env.example`: `MAX_CONVERSION_MEMORY_BYTES`, `MAX_ARCHIVE_ENTRIES`, `MAX_ARCHIVE_EXPANDED_BYTES`.

### VerificaciÃ³n y pendientes

- Pruebas locales del paquete API: `27 passed`. Ruff y Black pasan; el YAML de Compose parsea. No se probÃ³ el build Docker (sin daemon en este entorno), ni se desplegÃ³ o ejecutÃ³ benchmark en Dokploy/VPS.
- Antes de escalar: probar formatos/tamaÃ±os representativos en la VPS, observar memoria/CPU y confirmar que el OOM mata el subprocess sin reiniciar o reencolar indefinidamente el worker. No aumentar concurrencia hasta medir.
- Aceptar hasta 100 MiB de entrada no garantiza conversiÃ³n para todo documento. La validaciÃ³n de formato tampoco es anÃ¡lisis de malware ni garantÃ­a de seguridad del parser.

## Plan pendiente: interfaz de administraciÃ³n y portal de usuario

**Estado:** plan acordado; la interfaz, roles administrativos, planes/cuotas y API administrativa todavÃ­a no estÃ¡n implementados. No asumir que ya existen endpoints admin.

### Decisiones confirmadas

- La interfaz y la API deben compartir `mdservice.innersoft.com.mx`, con rutas como `/login`, `/dashboard`, `/convert`, `/users`, `/reports/consumption` y `/profile`.
- Backend FastAPI existente; se propone una SPA React + TypeScript + Vite y un gateway Nginx en Dokploy. El gateway sirve la SPA y reenvÃ­a `/v1/*` a FastAPI por la red interna; conservar las rutas actuales de API.
- El dominio pÃºblico se debe dirigir al gateway/web, no al worker. Redis, PostgreSQL y worker siguen privados.
- La zona de reinicio de cuotas mensuales es `America/Mazatlan`. Los informes se presentan agrupados en la zona horaria IANA del navegador.
- Free: mÃ¡ximo 300 conversiones por mes natural y 10 MiB por archivo. Mantener el hard cap global actual de 100 MiB.
- Lotes: mÃ¡ximo inicial de 1 archivo por solicitud; dejar el valor como capacidad editable por plan para una posible ampliaciÃ³n.
- Cuotas requeridas por plan: conversiones, bytes de entrada (MB), pÃ¡ginas y solicitudes por minuto. Los valores de MB/pÃ¡ginas/RPM y los lÃ­mites de otros planes se decidirÃ¡n despuÃ©s de benchmark; no inventarlos ni habilitar un plan con una matriz incompleta.
- ConversiÃ³n fallida: no consume cuota de conversiones ni pÃ¡ginas; sÃ­ se contabilizan los bytes realmente procesados y las solicitudes autenticadas por minuto.
- PÃ¡ginas: mantener `page_count` como nullable cuando el conversor no pueda determinarlo. Para enforcement de cuotas usar una unidad derivada: pÃ¡ginas reales cuando existan; en imagen o formato sin paginaciÃ³n, 1 unidad por archivo convertido.
- OCR se habilita por plan y explÃ­citamente por conversiÃ³n. Medir cuota OCR por el nÃºmero real de pÃ¡ginas/imÃ¡genes enviadas al proveedor, separado del nÃºmero de pÃ¡ginas fuente.
- Usar los niveles y prestaciones de FileToMD.ai como referencia para Free, Pro semanal/mensual/anual, Lifetime y Business. Mostrar precios solo como catÃ¡logo informativo; no hay checkout ni pagos integrados. AsignaciÃ³n manual de plan por administrador.
- Business serÃ¡ una cuenta individual en v1; no implementar organizaciones ni membresÃ­as todavÃ­a.

### Arquitectura e identidad

- AÃ±adir `packages/markitdown-admin/` como aplicaciÃ³n SPA. Publicar mediante un servicio web estÃ¡tico/Nginx en Compose/Dokploy. Nginx sirve la SPA y hace proxy de `/v1/*` a `api:8000`; revisar tambiÃ©n `/health/*` y `/docs` segÃºn la polÃ­tica de exposiciÃ³n.
- Reemplazar el destino pÃºblico del dominio en Dokploy de `api:8000` por el gateway web. Mantener el API accesible al gateway en la red interna y, si se requiere conservar clientes externos, mantener `/v1/*` disponible a travÃ©s del mismo dominio.
- Extender `Account` con rol (`user`/`admin`), estado activo/suspendido, perfil y plan. El primer admin se debe provisionar por comando/operaciÃ³n controlada, nunca por registro pÃºblico ni contraseÃ±a por defecto.
- SesiÃ³n web con cookie `Secure`, `HttpOnly`, `SameSite` y protecciÃ³n CSRF. Mantener las API keys existentes (`X-API-Key`) exclusivamente para clientes de conversiÃ³n. La autorizaciÃ³n siempre se verifica en backend; ocultar rutas en React no es control de acceso.
- Mantener invitaciÃ³n de usuario vÃ­a correo con expiraciÃ³n y flujo para establecer contraseÃ±a/verificar cuenta. Log de auditorÃ­a admin sin contraseÃ±as, claves, documentos ni contenido Markdown.

### Modelo de planes y cuotas

Crear migraciones versionadas; el arranque actual con `metadata.create_all` no sustituye migraciones de producciÃ³n. AÃ±adir catÃ¡logo editable de planes y asignaciÃ³n manual a cuentas. Campos mÃ­nimos por plan:

- ConversiÃ³n mÃ¡xima por mes natural.
- Bytes de entrada mÃ¡ximos por mes natural.
- Unidades de pÃ¡gina mÃ¡ximas por mes natural.
- Requests por minuto.
- Bytes mÃ¡ximos por archivo, sin superar 100 MiB globales.
- MÃ¡ximo de archivos por lote (inicia en 1).
- OCR habilitado y unidades OCR por mes.
- Nombre/beneficios/precio mostrado como informaciÃ³n, sin iniciar cobros.

SemÃ¡ntica de cuotas:

- Calcular los lÃ­mites de mes natural en `America/Mazatlan`; guardar todos los instantes en UTC y calcular los lÃ­mites con `zoneinfo` para respetar cambios de horario.
- Requests/minuto cuenta solicitudes de conversiÃ³n autenticadas; mantener ademÃ¡s lÃ­mites globales/de abuso de infraestructura.
- Bytes se contabilizan por bytes realmente recibidos/procesados, incluso si el conversor termina fallando. Rechazos previos a aceptar/leer la entrada no deben incrementar MB procesados.
- ConversiÃ³n/pÃ¡ginas solo se descuentan cuando la conversiÃ³n termina correctamente. `page_count` almacenado puede quedar nulo; el valor `quota_page_units` aplica el fallback de 1 unidad indicado arriba.
- OCR solo descuenta unidades realmente enviadas al proveedor. Registrar proveedor y unidades, no datos/imÃ¡genes del documento.
- El admin puede editar lÃ­mites; validar que los valores de cuenta no superen lÃ­mites globales, particularmente tamaÃ±o de archivo y recursos de la VPS.

### API administrativa propuesta

- `GET /v1/admin/overview`: totales del periodo y estado operativo.
- `GET /v1/admin/users`: bÃºsqueda, filtros y paginaciÃ³n.
- `POST /v1/admin/users`: crear cuenta invitada con plan/lÃ­mites iniciales.
- `GET /v1/admin/users/{id}` y `PATCH /v1/admin/users/{id}`: detalle, perfil, estado, plan y lÃ­mites.
- `GET /v1/admin/plans` y `POST/PATCH /v1/admin/plans`: catÃ¡logo y cuotas configurables.
- `GET /v1/admin/reports/consumption`: filtros de periodo, cuenta, estado y origen; agrupaciones `day`, `week`, `month`; aceptar zona IANA del navegador.
- `GET /v1/admin/reports/consumption.csv`: exportaciÃ³n paginada/streaming con lÃ­mites de rango.
- `GET/PATCH /v1/account/me`: perfil del usuario autenticado. Reutilizar gestiÃ³n existente de claves API y `GET /v1/usage` donde sea apropiado.

Los informes usarÃ¡n agregaciones en PostgreSQL sobre Conversion (conteos, bytes/MB decimales, unidades de pÃ¡gina, OCR, longitud de salida, estados y duraciÃ³n). AÃ±adir Ã­ndices y probar costes sobre registros indefinidos; no devolver documentos, URLs completas, claves o Markdown.

### Rutas y experiencia de usuario

- `/login`: login y verificaciÃ³n de estado de cuenta.
- `/dashboard`: resÃºmenes personales o globales segÃºn rol.
- `/convert`: archivo o URL, validaciÃ³n de lÃ­mites/plan, progreso `queued`/`running`/resultado/error.
- `/users` y `/users/:id`: solo admin; invitaciÃ³n, plan, cuotas, estado y consumo.
- `/reports/consumption`: solo admin; agrupaciÃ³n dÃ­a/semana/mes y filtros.
- `/profile`: perfil, cambio/recuperaciÃ³n de contraseÃ±a, claves propias y uso personal.

El GET de resultado es de descarga Ãºnica y expira a las 24 horas. La SPA debe recuperar el Markdown una vez, conservarlo solo en memoria durante la vista actual y ofrecer preview, copiar y guardar un Blob local; no volver a pedir el resultado al backend tras consumirlo. Evitar persistir contenido en `localStorage`, analytics o logs.

### Fases para el siguiente agente

1. Confirmar/implementar Alembic y migraciones para rol, estado, Plan, asignaciÃ³n, lÃ­mites, invitaciÃ³n y auditorÃ­a.
2. AÃ±adir RBAC admin, verificaciÃ³n de cuenta activa, invitaciones y endpoints administrativos; probar acceso admin vs usuario en backend.
3. AÃ±adir entitlement/quota service compartido por upload y URL. Aplicar bucket mensual `America/Mazatlan`, requests/minuto, bytes, conversiones, pÃ¡ginas y unidades OCR con las semÃ¡nticas anteriores.
4. AÃ±adir endpoints agregados/reportes antes de construir grÃ¡ficas; no implementar agregaciÃ³n pesada en el browser.
5. Crear SPA, login/sesiÃ³n, shell responsive, Dashboard, Convert, Usuarios, Informes y Perfil.
6. AÃ±adir Nginx/gateway same-origin a Compose y actualizar route/domain de Dokploy al gateway. Verificar labels Traefik, `dokploy-network`, `/health/ready` y proxy `/v1`.
7. Probar lÃ­mites y rollover mensual con reloj/zona controlables, seguridad de rutas admin, invitaciones, OCR accounting, conversiÃ³n web, streaming/one-time result, accesibilidad y export CSV.
8. Antes de elevar lÃ­mites o activar lotes/OCR comercialmente, medir en VPS Ubuntu de 2 CPU/2 GB; el benchmark aÃºn estÃ¡ pendiente.

### Pendiente de producto

- Definir despuÃ©s del benchmark los valores numÃ©ricos de MB, pÃ¡ginas y RPM para Pro semanal/mensual/anual, Lifetime y Business; Free ya tiene 300/mes y 10 MiB/archivo.
- Definir lÃ­mites de conversiones por mes para planes de pago y cupo OCR por plan segÃºn coste real de Azure.
- Confirmar si Free adopta solo el mÃ¡ximo mensual de 300 (sin lÃ­mite diario), segÃºn el acuerdo actual.
- Revisar moneda/precios visibles del catÃ¡logo; no integrar checkout hasta elegir proveedor de pagos.
- AÃ±adir organizaciones/membresÃ­as Business solo en una fase futura, si se solicita.

## ImplementaciÃ³n del portal y cuotas (estado)

**Estado:** implementado en el repositorio local; pendiente de desplegar y validar en la VPS. No ejecutar pruebas locales si el usuario pide trabajar solo en el VPS.

### Backend

- `packages/markitdown-api/` incorpora Alembic (`src/markitdown_api/migrations/`) y aplica `upgrade head` al arrancar. Una base existente sin `alembic_version` se marca como `0001_base` y luego recibe `0002_admin_portal`.
- Nuevos modelos: `Plan`, `PlanAssignment`, `AccountQuotaOverride`, `Invitation`, `AdminAuditEvent`; `Account` aÃ±ade `role`, `is_active`, `display_name`; `Conversion` aÃ±ade `quota_page_units` y `ocr_units`.
- `entitlements.py` calcula el mes natural en `America/Mazatlan` (configurable con `QUOTA_TIMEZONE`) y aplica cuotas de conversiones, bytes, pÃ¡ginas, RPM y OCR. Las conversiones fallidas no consumen conversiones/pÃ¡ginas pero sÃ­ bytes y requests/minuto. Las pÃ¡ginas reales se usan si existen; si no, 1 unidad por archivo. OCR se cuenta por llamadas reales al proveedor.
- `POST /v1/web-auth/login`, `/me`, `/csrf`, `/logout`, `/accept-invitation`; sesiÃ³n con cookies `HttpOnly`/`Secure`/`SameSite=strict` y CSRF en mutaciones.
- `GET/PATCH /v1/account/me`, `POST /v1/account/password`; cambio de contraseÃ±a revoca API keys e invalida la sesiÃ³n.
- Admin: `/v1/admin/overview`, `/v1/admin/users` (GET/POST/PATCH/detalle), `/v1/admin/plans` (GET/POST/PATCH), `/v1/admin/reports/consumption` y `.csv`. Todo pasa por `require_admin_account`.
- Invitaciones requieren SMTP y un plan activo. El primer admin se crea con `python -m markitdown_api.admin_cli bootstrap-admin <email>` dentro del contenedor `api`; el enlace caduca en 7 dÃ­as.
- `GET /v1/plans` devuelve solo planes activos para el catÃ¡logo informativo.
- El registro pÃºblico queda desactivado por defecto (`PUBLIC_SIGNUP_ENABLED=false`). En este estado, un usuario sin plan activo recibe `plan_not_configured`; los admins pueden probar con lÃ­mites globales.
- Los planes sembrados estÃ¡n inactivos. Para activar Free/Pro/etc. hay que completar la matriz de cuotas (MB, pÃ¡ginas, RPM) desde administraciÃ³n; los valores de pago siguen pendientes del benchmark.

### Frontend

- `packages/markitdown-admin/` (React + TypeScript + Vite) con `/login`, `/accept-invitation`, `/dashboard`, `/convert`, `/plans`, `/users`, `/users/:id`, `/reports/consumption`, `/profile`.
- ConversiÃ³n web con archivo o URL, polling, preview Markdown en memoria, copiar y descarga; no persiste resultados.
- Usuarios: invitaciÃ³n con plan activo, ediciÃ³n de rol/estado, overrides de cuotas por usuario (heredar del plan o valor explÃ­cito).
- Informes con agrupaciÃ³n y zona IANA del navegador; export CSV.
- CatÃ¡logo `/plans` informativo, sin checkout.

### Despliegue pendiente

- Compose aÃ±ade `admin-web` (Nginx) que sirve la SPA y proxya `/v1/*`, `/health/*`, `/docs` y pÃ¡ginas de correo a `api:8000`.
- En Dokploy, cambiar el dominio de `api:8000` a `admin-web:80` y redesplegar. Luego verificar `/health/ready`, `/login`, `/v1/plans` y el flujo de invitaciÃ³n.
- Antes de invitar usuarios reales: configurar SMTP, provisionar el admin, completar y activar el plan Free con la matriz aprobada. Los valores de Pro/OCR esperan benchmark.
- No se han ejecutado pruebas locales ni despliegue con estos cambios al momento de escribir esta secciÃ³n.
