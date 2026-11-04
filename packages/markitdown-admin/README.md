# MarkItDown Admin (frontend)

SPA React + TypeScript + Vite para el mismo origen que la API. El gateway debe servir `dist/` y usar fallback de SPA para rutas distintas de `/v1/*`; `/v1/*` se proxya al API.

```sh
npm install
npm run build
```

No hay datos demo ni límites editables codificados. El catálogo de planes y valores variables se obtiene del API. La única cuota visible fija es el plan Free acordado (300 conversiones por mes natural y 10 MiB por entrada); el máximo global es 100 MiB. No se implementan pagos.

La cuota mensual reinicia en `America/Mazatlan`; los informes envían la zona IANA del navegador. Se presenta `page_count` real cuando es fiable y 1 unidad por archivo si no hay paginación fiable (p. ej., imágenes). Los fallos no descuentan conversiones ni páginas, pero sí bytes procesados y requests/minute. OCR requiere capacidad en el plan, activación por solicitud y consentimiento; las unidades son las páginas/imágenes realmente enviadas. `batch_max_files` empieza en 1 y se expone según el catálogo. MB/páginas/RPM de planes de pago y OCR quedan pendientes de benchmark; Business es una cuenta individual en v1.

## Supuestos de integración API

- Respuestas JSON en camelCase o snake_case toleradas en los campos principales; listas pueden venir como `items` o `results`.
- `POST /v1/web-auth/login` acepta `{email,password}`, no requiere CSRF previo, establece cookies same-origin y devuelve `{user,csrf_token}`; ese token queda solo en memoria para mutaciones posteriores.
- `GET /v1/web-auth/me` consulta la sesión; `GET /v1/web-auth/csrf` devuelve `{csrfToken}`. Las demás mutaciones autenticadas envían `X-CSRF-Token` con `credentials: same-origin`.
- El catálogo para todas las cuentas usa `GET /v1/plans`; la vista pública filtra planes activos y no ofrece checkout.
- `/accept-invitation` lee el token de `#token=...`, lo quita de la barra del navegador y envía `{token,password}` a `POST /v1/web-auth/accept-invitation`; contraseña mínima de 12 caracteres.
- Administración consume `/v1/admin/overview`, `/users`, `/users/{id}`, `/plans` y `/reports/consumption`; CSV usa el contrato planeado `GET /v1/admin/reports/consumption.csv`. Planes se crean en `/plans` y se actualizan por ID en `/plans/{id}`.
- `POST /v1/admin/users` solo se envía con un `planId` seleccionado de un plan explícitamente activo. El editor del catálogo bloquea guardar cambios con `active=true` si falta algún límite requerido; el backend conserva la validación autoritativa.
- El detalle de usuario envía overrides con `PATCH /v1/admin/users/{id}` y `{limits:{...}}`; modo heredado se representa con `null`. Los bytes se editan en MiB y se serializan como bytes enteros. Para reabrir correctamente modos personalizados, se usa `limit_overrides`/`limitOverrides` si el API lo devuelve; `limits` se interpreta como límites efectivos.
- El API define los permisos en `role`/`roles`; roles administradores reconocidos: `admin`, `owner`, `superadmin`. Cualquier otro usuario es cliente.
- Para convertir en web, una clave API se crea/elige en Perfil y se guarda solo en `sessionStorage` mientras dura la pestaña. Resultados Markdown nunca pasan a almacenamiento persistente: se recuperan una vez y viven solo en estado React; descargar/copiar/previsualizar no vuelve a consultar el endpoint.
- Estados de conversión esperados: `queued`, `running`, `succeeded`, `failed`, `expired`, `cancelled`; la UI tolera campos habituales alternativos.
- Campos de planes/cuotas/precios son representados según la respuesta del catálogo, sin completar valores ausentes. Los precios son informativos, no habilitan compra.

## Rutas

`/login`, `/accept-invitation`, `/dashboard`, `/convert`, `/plans`, `/users`, `/users/:id`, `/reports/consumption`, `/profile`. La barra lateral cambia según el rol; el catálogo `/plans` es visible a todos los usuarios autenticados. Informes usan la zona IANA local del navegador; los reinicios de cuota son responsabilidad del API en `America/Mazatlan`.
