# Handoff técnico: API REST para MarkItDown

## Objetivo

Este documento es el punto de partida para el siguiente agente. Describe la implementación existente y el estado confirmado del despliegue. No recrear el proyecto ni borrar volúmenes durante tareas de diagnóstico sin autorización explícita.

## Estado de implementación

- Implementación en `packages/markitdown-api/`: FastAPI, worker, cola Redis, PostgreSQL, almacenamiento temporal, Dockerfile, Compose, README y pruebas.
- **Estado remoto confirmado (2026-11-03):** Dokploy `xperiencIA` / `production`; Compose `markitdown`; ruta `packages/markitdown-api/compose.yaml`; dominio `mdservice.innersoft.com.mx` asignado a `api:8000`.
- El último despliegue consultado está en `done`; `api`, `worker`, `postgres` y `redis` están activos. `https://mdservice.innersoft.com.mx/health/live` y `/health/ready` respondieron `200`.
- Se corrigió el `404 page not found`: Dokploy tenía el dominio configurado, pero los contenedores vivos no tenían labels Traefik ni estaban en `dokploy-network`. Se volvió a desplegar; el contenedor `api` ahora sí tiene las reglas y la red del proxy.
- La rama desplegada es `HP240RG10-dev`. Confirmar la rama y el estado en Dokploy antes de cambios posteriores.
- SMTP debe estar configurado si `EMAIL_VERIFICATION_REQUIRED=true` (valor predeterminado). Los pagos no están integrados.
- OCR está disponible explícitamente mediante el plugin MarkItDown + Azure OpenAI si se configuran sus secretos; por defecto no se invoca.
- El despliegue inicial usa una instancia API, un worker y una conversión simultánea. No aumentar concurrencia sin medir la VPS de 2 GB.
- El repositorio está indexado en Codebase Memory con el nombre `markitdown`.
- La suite del paquete API registró `18 passed`; Black/Ruff pasaron. El build de Docker no se pudo probar en el entorno local porque no había daemon Docker; Dokploy sí completó el build remoto.

## Requisitos acordados

- VPS actual: Ubuntu, Dokploy, 2 núcleos, 2 GB de RAM y 50 GB de disco.
- Dokploy gestiona la exposición pública y el enrutamiento del dominio.
- Aceptar archivos y URLs HTTP/HTTPS públicas.
- Tamaño máximo de archivo o descarga: 100 MB.
- Habilitar los formatos disponibles de MarkItDown según sus dependencias; dejar preparados OCR y proveedores externos como Azure.
- OCR y servicios externos deben ser explícitos y estar desactivados por defecto.
- Cuentas individuales por cliente, claves API y medición de consumo.
- No integrar pagos en esta etapa. La unidad facturable se decidirá después de evaluar pruebas.
- Conservar registros de consumo indefinidamente.
- Eliminar el archivo de entrada al finalizar el intento de conversión.
- Conservar el resultado hasta una descarga completa o durante un máximo de 1.440 minutos (24 horas), lo que ocurra primero; eliminarlo después de una descarga completa.
- Empezar con una conversión simultánea y una cola limitada, y ajustar los límites con pruebas de carga.

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

- **API:** recibe solicitudes, autentica clientes, valida límites y crea trabajos.
- **Redis:** cola de trabajos y coordinación; no expuesto públicamente.
- **PostgreSQL:** cuentas, claves API protegidas, estados de trabajos y registros de consumo.
- **Worker:** ejecuta la conversión con la API Python de MarkItDown, separado del proceso HTTP.
- **Volumen temporal:** mantiene las entradas mientras están pendientes/en proceso y los resultados hasta su entrega o expiración. Debe tener límites y limpieza automática.
- **Dokploy:** publica el servicio API en el dominio configurado. Redis, PostgreSQL y workers permanecen en la red interna de Docker.

Con 2 GB de RAM, mantener un worker con concurrencia 1. Redis y PostgreSQL se ejecutan en la misma VPS que Dokploy; conversiones complejas pueden agotar memoria. No anunciar capacidad de alta carga hasta medirla. El Compose limita recursos y resultados temporales, pero no elimina esta limitación física.

## Contrato inicial de API

- `POST /v1/conversions`: recibe un archivo multipart y responde `202 Accepted` con el ID del trabajo.
- `POST /v1/conversions/from-url`: recibe una URL pública y responde `202 Accepted` con el ID del trabajo.
- `GET /v1/conversions/{id}`: devuelve el estado y errores públicos del trabajo.
- `GET /v1/conversions/{id}/result`: entrega el Markdown solo al propietario del trabajo.
- Endpoints de cuenta y claves API para registro/autenticación, rotación y revocación.
- Endpoint de consumo para consultar las métricas disponibles.
- Endpoints de salud (`live` y `ready`) para operación y despliegue.

Estados sugeridos: `queued`, `running`, `succeeded`, `failed`, `expired` y `cancelled`.

La descarga se considerará completa cuando el servidor finalice el envío de la respuesta sin desconexión del cliente. Si el cliente se desconecta antes de completar la transferencia, conservar el resultado para un nuevo intento hasta la caducidad. El servidor no puede confirmar que el cliente haya guardado el archivo; si se requiere confirmación de aplicación, diseñar un endpoint de acuse de recibo.

## Procesamiento, límites y capacidad actuales

1. Archivos y descargas HTTP se transmiten a disco temporal; la API exige `Content-Length` para cargas multipart.
2. Límite de entrada: 100 MiB; máximo de 5 trabajos pendientes; un upload multipart procesándose a la vez.
3. Un worker y una conversión simultánea.
4. Límite de salida: 50 MiB por resultado; máximo agregado de 2 GiB en resultados pendientes de descarga; expiración de 1.440 minutos.
5. Timeout de conversión: 900 segundos. La cola llena devuelve un error controlado (`429`/`503`).
6. Los límites están configurados mediante variables de entorno en `packages/markitdown-api/compose.yaml`.
7. Carga por encima de 100 MiB no está soportada; aceptación de un tamaño no garantiza que cualquier formato complejo quepa en la memoria disponible.

La categoría “todos los formatos” debe validarse contra las capacidades y dependencias reales de MarkItDown. Los extras `[all]`, herramientas del sistema, OCR y ciertas integraciones pueden aumentar el tamaño de la imagen o requerir servicios externos. No asumir que todos los formatos tienen conversión local equivalente.

## Seguridad para archivos y URLs

- Autenticar con claves API enviadas en cabecera; no aceptar claves en query strings. Guardar hashes de claves, mostrarlas al cliente solo al crearlas y permitir revocarlas.
- Limitar tamaño de entrada/descarga, tiempo total, tamaño de salida, frecuencia de solicitudes y trabajos concurrentes por cuenta.
- Usar nombres aleatorios para temporales; nunca construir rutas a partir del nombre enviado por el usuario.
- Validar extensión y contenido; tratar documentos y archivos comprimidos como entrada no confiable.
- Aceptar solo esquemas HTTP/HTTPS para URLs. Rechazar destinos localhost, privados, link-local, reservados y otros rangos no públicos.
- Validar DNS y cada redirección; limitar puertos, redirecciones, tiempo y bytes descargados. Evitar que el servicio pueda acceder a metadatos de nube o servicios internos.
- Siempre que sea posible, descargar el contenido con un componente controlado y entregar los bytes al conversor, en vez de pasar una URL arbitraria directamente al conversor.
- Ejecutar workers sin privilegios y con acceso de red saliente restringido cuando las integraciones habilitadas lo permitan.
- No registrar contenido, Markdown, claves, nombres de archivo ni URLs completas. Usar identificadores de trabajo para correlación.
- Mantener dependencias actualizadas y eliminar temporales también ante fallos, cancelaciones y reinicios.

## Retención y registro de consumo

Los archivos y resultados son efímeros; los metadatos de consumo se conservan indefinidamente, según lo acordado. No almacenar el contenido fuente ni el Markdown en la tabla de consumo.

Campos propuestos por conversión:

- ID de conversión y cuenta propietaria.
- Fecha/hora, tipo de entrada, formato/extensión y estado.
- Bytes exactos de entrada (`input_bytes`); derivar MB como `input_bytes / 1_000_000` y documentar esta convención.
- Número de páginas (`page_count`), nullable cuando el formato o convertidor no lo pueda determinar de forma fiable.
- Longitud del resultado en caracteres (`output_char_count`) y bytes UTF-8 (`output_utf8_bytes`).
- Duración, categoría de error y proveedor externo utilizado, si corresponde.

Vigilar el crecimiento de registros indefinidos, índices, copias de seguridad y capacidad de PostgreSQL. Evitar guardar datos identificables no necesarios. Definir posteriormente cómo se anonimizan o eliminan los vínculos a una cuenta cuando se solicite su cierre.

## Cuentas, claves y facturación

- Implementado: cuenta individual, verificación de correo, login con token de sesión, recuperación de contraseña y gestión de claves API.
- Las claves API se entregan una sola vez, se almacenan como HMAC y se envían en `X-API-Key`; las sesiones usan `Authorization: Bearer`.
- Hay un máximo de 20 claves activas por cuenta. Restablecer una contraseña revoca las claves y cambia la versión de sesión.
- `GET /v1/usage` devuelve registros paginados; no procesa pagos ni almacena tarjetas.
- El proveedor de pagos, países, moneda y unidad facturable siguen pendientes.

## OCR y servicios externos

- Implementado mediante el plugin OCR de MarkItDown con Azure OpenAI/OpenAI-compatible.
- Se habilita por conversión (`ocr=true`) y falla con `ocr_provider_not_configured` si faltan secretos; por defecto está apagado.
- Secretos en Dokploy: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_MODEL`.
- Habilitar OCR envía imágenes de documentos al endpoint configurado; confirmar consentimiento, coste y privacidad antes de hacerlo por cliente.

## Estado de fases y siguiente trabajo

1. **Implementado:** API versionada, auth, conversión por archivo y URL, cola, worker, límites y retención.
2. **Implementado:** Compose para Dokploy, servicio público `api:8000`, Redis/PostgreSQL/worker internos y healthchecks.
3. **Implementado:** tests funcionales y de conversión; el CI tiene un job `api-tests`.
4. **Pendiente antes de escala:** realizar benchmark con formatos y tamaños representativos en la VPS; no hacerlo desde un entorno local si la instrucción activa del usuario lo prohíbe.
5. **Pendiente de producción:** establecer backups/restauración de PostgreSQL, migraciones versionadas (hoy el arranque usa `metadata.create_all`), políticas de anonimización/cierre de cuenta y abuso de alta frecuencia en registro/reset.
6. **Pendiente posterior:** seleccionar proveedor de pagos y unidad de cobro, sin almacenar datos de tarjeta.

## Procedimiento operativo Dokploy

- El Compose debe apuntar a `packages/markitdown-api/compose.yaml` en la raíz del checkout del repositorio `experiencIA-consulting/markitdown`.
- El dominio debe seleccionar `serviceName=api`, puerto interno `8000`, path `/`; nunca seleccionar `worker`.
- Para 404, comparar `compose.getConvertedCompose` con `docker.getConfig` del contenedor vivo: la conversión debe tener labels Traefik y `dokploy-network`.
- Después de cambiar dominios/rutas, redeploy Compose para recrear contenedores con los labels. Healthchecks confirmados: `/health/live` y `/health/ready`.
- El flujo API usa `POST /v1/accounts` → verificar correo → `POST /v1/auth/token` → `POST /v1/api-keys`; ver ejemplos en `packages/markitdown-api/README.md`.
- Errores observados históricamente: Nixpacks no correspondía a este monorepo; `composePath` incorrecto causaba “Compose file not found”; volumen PostgreSQL persistente puede conservar una contraseña anterior aunque `POSTGRES_PASSWORD` cambie en Dokploy. No borrar volúmenes para resolver auth DB sin confirmar pérdida de datos; sincronizar el rol en PostgreSQL de forma segura.

## Criterios de aceptación

- Una cuenta no puede consultar conversiones ni resultados de otra.
- Claves API se pueden revocar y no aparecen en logs ni respuestas posteriores a su creación.
- Archivos y descargas de hasta 100 MB se procesan sin cargarse enteros en memoria.
- URLs a destinos internos/privados, redirecciones peligrosas y descargas excesivas se bloquean.
- La cola y la concurrencia respetan los límites y rechazan carga excesiva de forma controlada.
- El archivo fuente se elimina al terminar; el resultado se elimina tras transferencia completa o al vencer 24 horas; una transferencia interrumpida no consume el resultado.
- Las métricas acordadas se guardan indefinidamente sin almacenar documentos ni Markdown.
- OCR/Azure no se invocan sin habilitación explícita y credenciales configuradas.
- Dokploy expone únicamente la API; dependencias internas no tienen puertos públicos.
- Las pruebas de carga documentan memoria, CPU, disco, tiempos y fallos para decidir si se amplía la VPS o se separan workers.

## Pendientes para una fase posterior

- Elegir proveedor de pagos, países, moneda, planes y unidad facturable.
- Decidir si se requiere acuse explícito del cliente para considerar completa la descarga.
- Confirmar la lista exacta de formatos/extras locales y proveedores OCR/Azure a soportar.
- Establecer política de anonimización de métricas si se cierra una cuenta.

## Mejoras implementadas: validación de formatos y límites de memoria

Implementación local en `packages/markitdown-api/`; **todavía no desplegada ni medida en la VPS**. Mantener un worker y una conversión simultánea.

### Validación de entradas

- `src/markitdown_api/input_validation.py` clasifica el contenido con Magika y compara el tipo detectado con una lista de extensiones soportadas por la imagen actual. Rechaza formatos no admitidos, discrepancias claras y contenido no clasificable con `unsupported_format`, `file_type_mismatch` o `invalid_file_content`.
- Valida la cabecera PDF, reconoce ZIP/Office Open XML/EPUB y limita contenedores con `MAX_ARCHIVE_ENTRIES` (predeterminado 5.000) y `MAX_ARCHIVE_EXPANDED_BYTES` (128 MiB). Estos límites no equivalen a análisis antivirus.
- Se ejecuta en el subprocess del worker antes de MarkItDown, tanto para archivos cargados como descargados desde URL. Por el contrato asíncrono, la solicitud puede responder `202`; una entrada inválida después queda como trabajo `failed` con su `error_code`. Se hace así para no cargar el modelo Magika en el proceso API limitado a 256 MiB.
- Si el nombre no tiene extensión, se infiere una extensión admitida cuando Magika puede identificarla. Mantener `SUPPORTED_EXTENSIONS` alineado con los conversores/dependencias instalados; no representa automáticamente todos los formatos opcionales de MarkItDown.

### Recursos y resultados

- El worker mide el RSS de su subprocess y lo termina si supera `MAX_CONVERSION_MEMORY_BYTES` (predeterminado 512 MiB), reportando `resource_limit_exceeded`; conserva el timeout existente de 900 segundos. El worker en Compose queda limitado por `WORKER_MEMORY_LIMIT` (768 MiB) y `WORKER_CPUS` (1 CPU). El límite del contenedor es el respaldo ante memoria de procesos descendientes o picos entre sondeos; un OOM que mate el worker completo aún requiere validación operativa.
- Markdown se escribe en bloques UTF-8 de 64 KiB y se descarta si supera `MAX_OUTPUT_BYTES` (50 MiB). Esto evita crear una segunda copia completa codificada y elimina temporales parciales. **MarkItDown todavía materializa el Markdown completo en memoria antes de aplicar el límite de salida**; el límite RSS es el control para ese pico, no streaming de conversión.
- Variables nuevas configurables en Compose y documentadas en `.env.example`: `MAX_CONVERSION_MEMORY_BYTES`, `MAX_ARCHIVE_ENTRIES`, `MAX_ARCHIVE_EXPANDED_BYTES`.

### Verificación y pendientes

- Pruebas locales del paquete API: `27 passed`. Ruff y Black pasan; el YAML de Compose parsea. No se probó el build Docker (sin daemon en este entorno), ni se desplegó o ejecutó benchmark en Dokploy/VPS.
- Antes de escalar: probar formatos/tamaños representativos en la VPS, observar memoria/CPU y confirmar que el OOM mata el subprocess sin reiniciar o reencolar indefinidamente el worker. No aumentar concurrencia hasta medir.
- Aceptar hasta 100 MiB de entrada no garantiza conversión para todo documento. La validación de formato tampoco es análisis de malware ni garantía de seguridad del parser.
