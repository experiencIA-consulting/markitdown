# Acceso: bajo solicitud, sin sandbox

**No hay cuenta demo, ni entorno de sandbox, ni API key de prueba compartida.** Es una decisión de
producto: no se publica ninguna credencial y no existe una ruta `/v1/sandbox` ni respuestas pregrabadas.

Todo el contrato de este directorio —esquemas, ejemplos, códigos de error y el snapshot OpenAPI— es
documentación. Para ejecutar conversiones reales hace falta una cuenta real con un plan activo.

## Cómo se solicita acceso

1. **Pide una invitación.** El alta es por invitación desde la administración del servicio; el registro
   público está desactivado (`PUBLIC_SIGNUP_ENABLED=false`), así que `POST /v1/accounts` responde
   `403 public_signup_disabled`.
2. **Indica para qué la necesitas**, para que se asigne el plan y las cuotas adecuadas:
   - caso de uso y volumen estimado (conversiones/mes, tamaño típico y máximo de archivo);
   - formatos que vas a enviar (archivo y/o URL);
   - si necesitas OCR (implica coste externo y consentimiento de privacidad);
   - ventana de pruebas y contacto técnico.
3. **Acepta la invitación.** El correo lleva a `/accept-invitation`; ahí se establece la contraseña. El
   enlace caduca en 7 días y es de un solo uso.
4. **Crea tu clave API.** Con la sesión iniciada, en `/profile` (`POST /v1/api-keys`). La clave se muestra
   una sola vez; guárdala en un gestor de secretos. Se pueden tener hasta 20 claves activas y revocarlas
   individualmente.
5. **Verifica el acceso** contra `/health/ready` y una conversión de un archivo pequeño de tu propiedad.

Sin plan activo, cualquier conversión de una cuenta no administradora responde `403 plan_not_configured`.
No es un fallo de la clave: falta la asignación de plan.

## Qué se obtiene

- Una cuenta individual (no hay organizaciones ni varios usuarios por cuenta).
- Una o varias claves API propias, revocables, sin scopes ni caducidad.
- Las cuotas del plan que asigne administración: conversiones/mes, bytes de entrada/mes, páginas/mes,
  solicitudes/minuto, tamaño máximo por archivo y, si aplica, unidades OCR/mes.
- Consumo consultable por la persona titular desde el portal, no desde la superficie de integración.

Los valores concretos de cada plan los fija administración y pueden cambiar; `GET /v1/plans` muestra el
catálogo activo con sus límites, sin precios contractuales ni *checkout*. No hay cobro integrado en esta
etapa.

## Qué no se obtiene

- Ni una clave compartida entre integradores, ni una clave “de pruebas” que apunte a un entorno distinto.
  Todas las claves operan contra el mismo servicio y consumen cuotas reales.
- Ni datos de otras cuentas: cada `id` es accesible solo por su cuenta; el resto responde `404`.
- Ni un entorno con límites relajados, ni datos sembrados, ni respuestas simuladas.
- Ni webhooks, ni notificaciones, ni un canal de soporte en tiempo real.
- Ni SLA de capacidad: el despliegue actual procesa una conversión a la vez (ver
  [`limits-and-formats.md`](./limits-and-formats.md)).

## Buenas prácticas durante la prueba

- Empieza con archivos pequeños y representativos de tu caso real, no con el máximo permitido.
- No uses documentos de terceros ni datos personales que no puedas enviar a un servicio externo; si activas
  OCR, las imágenes salen hacia Azure OpenAI.
- No automatices reintentos en bucle sobre `429` o `5xx`: respeta el *backoff* descrito en
  [`errors.md`](./errors.md).
- Descarga cada resultado una sola vez y en streaming; no uses `HEAD`, *previews* ni reintentos
  especulativos (ver [`markdown-result.md`](./markdown-result.md)).
- Guarda los `id` de conversación y `X-Request-ID` de cualquier incidencia: son lo único que permite
  correlacionar sin exponer contenido.
