# Plan de implementación: API pública para MarkItDown

## Objetivo

Publicar MarkItDown como una API REST para convertir archivos y URLs a Markdown, con cuentas individuales, claves API y registro de consumo. El despliegue inicial será en una VPS Ubuntu mediante Docker Compose en Dokploy. Este documento describe el plan; no implica que la API ya esté implementada.

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

## Arquitectura propuesta

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

Con 2 GB de RAM, se recomienda un worker con concurrencia 1. Redis y PostgreSQL en la misma VPS podrían competir por recursos con las conversiones; evaluar servicios externos para la base de datos y/o la cola. No anunciar capacidad de alta carga hasta medirla.

## Contrato inicial de API

- `POST /v1/conversions`: recibe un archivo multipart o una URL y responde `202 Accepted` con el ID del trabajo.
- `GET /v1/conversions/{id}`: devuelve el estado y errores públicos del trabajo.
- `GET /v1/conversions/{id}/result`: entrega el Markdown solo al propietario del trabajo.
- Endpoints de cuenta y claves API para registro/autenticación, rotación y revocación.
- Endpoint de consumo para consultar las métricas disponibles.
- Endpoints de salud (`live` y `ready`) para operación y despliegue.

Estados sugeridos: `queued`, `running`, `succeeded`, `failed`, `expired` y `cancelled`.

La descarga se considerará completa cuando el servidor finalice el envío de la respuesta sin desconexión del cliente. Si el cliente se desconecta antes de completar la transferencia, conservar el resultado para un nuevo intento hasta la caducidad. El servidor no puede confirmar que el cliente haya guardado el archivo; si se requiere confirmación de aplicación, diseñar un endpoint de acuse de recibo.

## Procesamiento, límites y capacidad

1. Recibir archivos y descargas en streaming a disco; no cargar documentos de 100 MB completos en memoria.
2. Aplicar el límite de 100 MB en el proxy/Dokploy y volver a validarlo en la aplicación. Configurar margen para la codificación multipart, sin elevar el límite real del archivo.
3. Empezar con un worker y una conversión simultánea.
4. Implementar una cola de tamaño acotado. Como valor inicial de prueba, evaluar un máximo de 5 trabajos pendientes, configurable y sujeto a los resultados de carga y al espacio temporal.
5. Aplicar límites configurables de duración, memoria, CPU, concurrencia y uso de disco temporal.
6. Ante cola llena, rechazar con respuesta controlada (`429` o `503`) en lugar de acumular archivos sin límite.
7. Medir documentos representativos de hasta 100 MB y aumentar concurrencia solo si la VPS mantiene margen de memoria, CPU y disco.

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

- Implementar una cuenta individual por cliente, autenticación, verificación/recuperación de acceso y autorización por propietario.
- Permitir creación, rotación y revocación de claves API.
- Registrar las métricas de consumo desde el inicio para evaluar si la unidad futura será conversiones, MB, páginas u otra.
- No procesar pagos ni almacenar tarjetas en esta fase.
- Mantener el registro de uso separado de la integración futura de pagos para poder incorporar un proveedor después de seleccionar países, moneda, planes y unidad de cobro.

## OCR y servicios externos

- Definir una interfaz/adaptador para OCR y proveedores como Azure.
- Mantener esas capacidades apagadas por defecto; habilitarlas explícitamente por solicitud.
- Configurar credenciales como secretos de Dokploy/entorno, nunca en el repositorio ni en la base de datos.
- Registrar el proveedor y el estado de la integración, no el documento enviado.
- Documentar que habilitar un proveedor externo puede enviar contenido fuera de la VPS y evaluar costes, latencia y tratamiento del documento antes de activarlo.

## Fases de trabajo

1. **Diseño e inventario:** definir formatos incluidos, dependencias, límites, esquema de estados y comportamiento de descarga.
2. **API y contratos:** crear la aplicación, autenticación inicial, validación y endpoints versionados.
3. **Cola y worker:** añadir procesamiento asíncrono, concurrencia 1, límites, cancelación y manejo de errores.
4. **Almacenamiento efímero:** carga/descarga en streaming, caducidad a 24 horas, eliminación después de descarga completa y limpieza de huérfanos.
5. **Conversión por URL:** implementar descarga segura con mitigaciones SSRF y límite de 100 MB.
6. **Cuentas y métricas:** implementar cuentas individuales, claves API y almacenamiento indefinido de métricas sin contenido documental.
7. **Integraciones opcionales:** preparar adaptadores OCR/Azure apagados por defecto y configuración segura de secretos.
8. **Contenedores y Dokploy:** separar servicios en Compose, exponer solo API, configurar recursos y proxy, health checks y secretos.
9. **Pruebas y publicación:** pruebas funcionales, seguridad, limpieza, carga y operación; iniciar con cola/concurrencia conservadoras y ajustar según mediciones.

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
