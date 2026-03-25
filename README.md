# 🌬️ Parque Eólico IoT — FastAPI + PostgreSQL

Simulación de un parque eólico con 10 turbinas individuales, concentrador central con validación de datos, agregaciones minutales y panel web en tiempo real.

---

## 👥 Miembros del equipo

- Juan Mari Díaz
- Unai Pinilla
- Imanol Lama

---

## 🏗️ Arquitectura

```
Turbina 01 ─┐
Turbina 02 ─┤
Turbina 03 ─┤
   ...       ├──► POST /datos ──► Concentrador FastAPI ──► PostgreSQL
Turbina 10 ─┘   (API Key)         - Valida rangos           - lecturas
                                   - Agrega minutalmente     - rechazados
                                         │                   - agregados_minutales
                                         ▼
                                    Panel Web (nginx)
                                    localhost:80
```

**Seguridad:** cada generador incluye `x-api-key` en la cabecera HTTP. Sin la clave correcta, el concentrador devuelve 401.

---

## 📖 Explicación de los pasos seguidos

### 1. Modelo de datos de la turbina
Se diseñó un modelo con los campos físicamente relevantes de una turbina eólica real: velocidad de viento, potencia generada, temperatura de la góndola (nacelle) y RPM del rotor. Cada campo tiene rangos de validación basados en límites físicos reales.

### 2. Generadores individuales
Cada turbina es un contenedor Docker independiente con características propias (potencia nominal, viento medio, probabilidad de error). Generan datos con distribución normal y una curva de potencia realista: sin viento no generan, con viento excesivo se paran.

### 3. Inyección de errores con probabilidad N
Cada generador tiene una variable `PROB_ERROR` (entre 0.05 y 0.20 según la turbina). Cuando se inyecta un error, el dato se corrompe de una de tres formas: valor fuera de rango, campo negativo, o marcado explícitamente como erróneo.

### 4. Concentrador FastAPI con validación
FastAPI con Pydantic valida automáticamente los rangos en cuanto llega el JSON. Los datos inválidos devuelven 422 y se guardan en la tabla `rechazados` para auditoría. Los válidos se guardan en `lecturas`.

### 5. Agregaciones minutales
Una tarea asíncrona en FastAPI se ejecuta cada 60 segundos y calcula con SQL la media de potencia y viento por turbina del último minuto, guardándolo en `agregados_minutales`.

### 6. Panel web en tiempo real
Página HTML/JS servida por nginx que hace polling cada 2 segundos al concentrador y actualiza el estado de cada turbina, el log en vivo y el gráfico de barras de potencia.

---

## 🚀 Instrucciones de uso

### Requisitos
- Docker y Docker Compose instalados.

### 1. Levantar todo
```bash
docker compose up --build
```

### 2. Acceder al panel web
Abrir en el navegador: **http://localhost**

### 3. Acceder a la documentación de FastAPI
Abrir en el navegador: **http://localhost/docs**

Desde ahí se pueden probar los endpoints manualmente, incluyendo enviar datos de prueba.

### 4. Verificar datos en PostgreSQL
```bash
docker exec -it base_datos psql -U deusto -d parque_eolico_db
```
```sql
-- Últimas lecturas válidas
SELECT turbina_id, potencia_generada, velocidad_viento,
       to_timestamp(timestamp_ms/1000) AS hora
FROM lecturas ORDER BY timestamp_ms DESC LIMIT 10;

-- Últimos rechazos
SELECT turbina_id, motivo, creado_en
FROM rechazados ORDER BY creado_en DESC LIMIT 10;

-- Agregados minutales
SELECT turbina_id, minuto, potencia_media, num_lecturas
FROM agregados_minutales ORDER BY minuto DESC LIMIT 20;

-- Media de producción del parque por minuto
SELECT minuto,
       ROUND(SUM(potencia_total)::numeric, 2) AS produccion_total_kw,
       ROUND(AVG(potencia_media)::numeric, 2) AS media_por_turbina
FROM agregados_minutales
GROUP BY minuto ORDER BY minuto DESC LIMIT 10;
\q
```

### 5. Probar la seguridad (API Key)
```bash
# Con API Key correcta → debe funcionar
curl.exe -X POST "http://localhost:8000/datos" -H "Content-Type: application/json" -H "x-api-key: parque_eolico_secreto" -d "{\""turbina_id\"":\""test\"",\""timestamp\"":1234567890000,\""velocidad_viento\"":12.0,\""potencia_generada\"":800.0,\""temperatura_nacelle\"":45.0,\""rpm_rotor\"":14.0}"

# Sin API Key → 422 Unprocessable / 401 Unauthorized
curl.exe -X POST "http://localhost:8000/datos" -H "Content-Type: application/json" -d "{\""turbina_id\"":\""intruso\"",\""timestamp\"":1234567890000,\""velocidad_viento\"":12.0,\""potencia_generada\"":800.0,\""temperatura_nacelle\"":45.0,\""rpm_rotor\"":14.0}"
```

---

## ⚠️ Problemas / Retos encontrados

### Validación de errores inyectados vs errores de rango
Algunos datos erróneos pasan la validación de Pydantic si el valor erróneo está dentro de rango pero el generador los marca como `es_erroneo: true`. Se maneja como caso especial en el endpoint antes de guardar.

### Race condition en el arranque
PostgreSQL tarda en inicializarse. El concentrador reintenta la conexión cada 2 segundos con un bucle while hasta que la BD esté lista.

### 10 contenedores con el mismo Dockerfile
Todos los generadores comparten imagen pero tienen comportamiento diferente vía variables de entorno. Docker Compose los construye una sola vez (caché) y los lanza con configuración distinta.

---

## 🔭 Posibles vías de mejora

- **Grafana + InfluxDB** para visualización avanzada de series temporales.
- **WebSockets** en el panel en lugar de polling, para actualizaciones en tiempo real sin overhead HTTP.
- **HTTPS** en el concentrador con certificados propios.
- **Alertas automáticas** cuando una turbina supera umbrales de temperatura o RPM.
- **API Key por turbina** en lugar de una compartida, para revocar acceso individualmente.
- **Simulación de viento correlacionado** entre turbinas contiguas (en un parque real el viento no es independiente entre aerogeneradores).

---

## 🔀 Alternativas posibles

### Protocolo de comunicación
En lugar de HTTP/REST se podría usar MQTT con el concentrador como suscriptor. La ventaja de HTTP es que el concentrador puede responder directamente al generador (ACK o rechazo), mientras que MQTT es fire-and-forget.

### Validación
En lugar de Pydantic se podría usar `jsonschema` o `marshmallow`. Pydantic se eligió por su integración nativa con FastAPI y su rendimiento.

### Base de datos
InfluxDB sería más adecuado para series temporales puras. PostgreSQL se eligió por familiaridad y por soportar tanto las lecturas como los metadatos de rechazos en el mismo sistema.

### Panel web
Grafana conectado directamente a PostgreSQL daría un panel mucho más potente sin código frontend. Se optó por HTML/JS propio para tener control total y no añadir más servicios.
