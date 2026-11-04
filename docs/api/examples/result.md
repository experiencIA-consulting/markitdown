# Manual de ejemplo: estación meteorológica

Documento sintético usado para ilustrar la forma de una descarga de resultado. No proviene de ningún
cliente ni de un documento real.

## Resumen

La estación registra temperatura, humedad y presión cada cinco minutos y publica un resumen horario.

- **Alcance:** una unidad interior y una exterior.
- **Alimentación:** panel solar con batería de reserva.
- **Conectividad:** red local; sin acceso a internet por defecto.

## Sensores

| Sensor | Magnitud | Rango | Precisión | Intervalo |
| --- | --- | ---: | ---: | ---: |
| T-01 | Temperatura | -40 a 85 °C | ±0,3 °C | 5 min |
| H-01 | Humedad relativa | 0 a 100 % | ±2 % | 5 min |
| P-01 | Presión | 300 a 1100 hPa | ±0,5 hPa | 5 min |

> Los valores de precisión corresponden a la hoja de datos del fabricante y no a una calibración de
> campo.

## Puesta en marcha

1. Fijar la unidad exterior a 1,5 m del suelo y a la sombra.
2. Conectar el panel solar y esperar el indicador verde.
3. Emparejar la unidad interior con la tecla `SET` durante tres segundos.
4. Verificar la primera lectura en el panel local.

## Configuración

```json
{
  "interval_minutes": 5,
  "timezone": "UTC",
  "retention_days": 90,
  "publish_hourly": true
}
```

## Mantenimiento

| Tarea | Frecuencia | Herramienta |
| --- | --- | --- |
| Limpiar el pluviómetro | Mensual | Paño húmedo |
| Revisar la batería | Trimestral | Multímetro |
| Sustituir el filtro de polvo | Anual | Filtro de repuesto |

### Notas

- La unidad exterior no debe exponerse a riego directo.
- Si el panel solar queda cubierto de nieve, la autonomía baja a unas 48 horas.
