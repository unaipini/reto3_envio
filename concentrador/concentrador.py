import json
import os
import time
import asyncio
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
import logging

# Importamos el modelo compartido con las 6 dimensiones de calidad del dato.
# Al estar en un archivo separado, si cambia un rango físico solo se cambia aquí.
from modelo import DatosTurbina

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Configuración del concentrador — reciclado del guardar_datos.py del reto 2
# Cambia de puerto para el TLS cogiéndolo del docker-compose. Vuelve con string y da fallo, por eso int().
API_KEY = os.getenv("API_KEY", "parque_eolico_secreto")
DB_HOST = os.getenv("DB_HOST", "base_datos")
DB_NAME = os.getenv("DB_NAME", "parque_eolico_db")
DB_USER = os.getenv("DB_USER", "deusto")
DB_PASS = os.getenv("DB_PASS", "deusto")

# Crea la aplicación FastAPI que actúa como concentrador central del parque eólico
app = FastAPI(
    title="Concentrador Parque Eólico",
    description="Recibe, valida y agrega datos de las 10 turbinas",
    version="1.0.0"
)

# Permite que el panel web (nginx) pueda llamar a la API desde el navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Intenta entrar en PostgreSQL con usuario y contraseña.
# Reciclado del guardar_datos.py del reto 2 — mismo patrón de reintento cada 2 segundos.
def conectar_bd():
    while True:
        try:
            conn = psycopg2.connect(
                host=DB_HOST, database=DB_NAME,
                user=DB_USER, password=DB_PASS
            )
            conn.autocommit = True
            print(f"[BD] Conectado exitosamente a {DB_HOST}")
            return conn
        except Exception as e:
            print(f"[BD] Esperando a la base de datos... ({e})")
            time.sleep(2)


# Si no existen, crea las tres tablas del parque eólico
def crear_tablas(conn):
    with conn.cursor() as cur:

        # Tabla de lecturas válidas — equivalente a 'mediciones' del reto 2
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lecturas (
                id                  SERIAL PRIMARY KEY,
                turbina_id          VARCHAR(20),
                timestamp_ms        BIGINT,
                velocidad_viento    FLOAT,
                potencia_generada   FLOAT,
                temperatura_nacelle FLOAT,
                rpm_rotor           FLOAT,
                creado_en           TIMESTAMP DEFAULT NOW()
            );
        """)

        # Tabla de datos rechazados — guarda los inválidos con el motivo del rechazo para auditoría
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rechazados (
                id           SERIAL PRIMARY KEY,
                turbina_id   VARCHAR(20),
                timestamp_ms BIGINT,
                motivo       TEXT,
                datos_crudos JSONB,
                creado_en    TIMESTAMP DEFAULT NOW()
            );
        """)

        # Tabla de agregados minutales — media y total de producción calculados cada minuto
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agregados_minutales (
                id             SERIAL PRIMARY KEY,
                minuto         TIMESTAMP,
                turbina_id     VARCHAR(20),
                potencia_media FLOAT,
                potencia_total FLOAT,
                viento_medio   FLOAT,
                num_lecturas   INT,
                creado_en      TIMESTAMP DEFAULT NOW()
            );
        """)
    print("[BD] Tablas verificadas/creadas.")


# Conexión global a la base de datos — reciclado del guardar_datos.py del reto 2
conn = None


# Se ejecuta automáticamente al arrancar FastAPI.
# Conecta la BD, crea las tablas y lanza la tarea de agregación en segundo plano.
@app.on_event("startup")
async def startup():
    global conn
    conn = conectar_bd()
    crear_tablas(conn)
    # Lanza en segundo plano la tarea que calcula los agregados cada 60 segundos
    asyncio.create_task(tarea_agregacion_minutal())
    print("[SISTEMA] Concentrador iniciado y listo para recibir datos")


# Comprueba que la petición lleva la API Key correcta en la cabecera HTTP.
# Es la seguridad del reto 3 — equivalente a los certificados TLS del reto 2.
# Sin la clave correcta devuelve 401 y no ejecuta nada más.
def verificar_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API Key inválida — acceso denegado")
    return x_api_key


# Estadísticas en memoria para el panel web.
# Se actualizan con cada dato recibido sin necesidad de consultar la base de datos.
stats = {
    "total_recibidos":      0,
    "total_aceptados":      0,
    "total_rechazados":     0,
    "por_turbina":          defaultdict(lambda: {"aceptados": 0, "rechazados": 0, "ultima_potencia": 0.0}),
    "potencia_parque":      0.0,
    "ultima_actualizacion": None,
}


# Recibe el dato de una turbina — equivalente a al_recibir_mensaje() del reto 2.
# DatosTurbina viene de modelo.py y aplica las 6 dimensiones de calidad del dato.
# Si Pydantic rechaza el dato (422), FastAPI devuelve el error automáticamente
# con el motivo exacto del fallo antes de llegar al código de abajo.
@app.post("/datos", summary="Recibir dato de una turbina")
async def recibir_dato(datos: DatosTurbina, api_key: str = Depends(verificar_api_key)):

    stats["total_recibidos"] += 1

    # Si el generador marcó el dato como erróneo, lo registramos y rechazamos
    if datos.es_erroneo:
        stats["total_rechazados"] += 1
        stats["por_turbina"][datos.turbina_id]["rechazados"] += 1
        _guardar_rechazado(
            datos.turbina_id,
            datos.timestamp,
            "Dato marcado como erróneo por el generador",
            datos.model_dump()
        )
        raise HTTPException(status_code=422, detail="Dato marcado como erróneo")

    # Si llega aquí el dato pasó las 6 dimensiones de validación — lo guardamos
    # Equivalente a guardar_lectura() del reto 2
    _guardar_lectura(datos)

    # Actualizamos las estadísticas en memoria para el panel web
    stats["total_aceptados"] += 1
    stats["por_turbina"][datos.turbina_id]["aceptados"] += 1
    stats["por_turbina"][datos.turbina_id]["ultima_potencia"] = datos.potencia_generada

    # Recalcula la potencia total del parque sumando la última lectura de cada turbina
    stats["potencia_parque"] = sum(
        v["ultima_potencia"] for v in stats["por_turbina"].values()
    )
    stats["ultima_actualizacion"] = datetime.now(timezone.utc).isoformat()

    print(f"[BD] Guardado -> Turbina: {datos.turbina_id} | "
          f"potencia={datos.potencia_generada:.1f}kW | "
          f"viento={datos.velocidad_viento:.1f}m/s")
    return {"status": "aceptado", "turbina_id": datos.turbina_id}


# Estado general del parque — el panel web lo consulta cada 2 segundos
@app.get("/estado", summary="Estado general del parque")
async def estado_parque():
    turbinas = []
    for tid, v in stats["por_turbina"].items():
        turbinas.append({
            "turbina_id":      tid,
            "aceptados":       v["aceptados"],
            "rechazados":      v["rechazados"],
            "ultima_potencia": v["ultima_potencia"],
        })
    # Ordenamos por nombre para que el panel las muestre siempre en el mismo orden
    turbinas.sort(key=lambda x: x["turbina_id"])
    return {
        "total_recibidos":      stats["total_recibidos"],
        "total_aceptados":      stats["total_aceptados"],
        "total_rechazados":     stats["total_rechazados"],
        "potencia_parque_kw":   round(stats["potencia_parque"], 2),
        "turbinas":             turbinas,
        "ultima_actualizacion": stats["ultima_actualizacion"],
    }


# Devuelve los últimos 60 registros de la tabla de agregados minutales
@app.get("/agregados", summary="Últimos agregados minutales")
async def ultimos_agregados():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT turbina_id, minuto, potencia_media, potencia_total,
                   viento_medio, num_lecturas
            FROM agregados_minutales
            ORDER BY minuto DESC, turbina_id
            LIMIT 60;
        """)
        return cur.fetchall()


# Devuelve las últimas 50 lecturas válidas — equivalente a la consulta SELECT del reto 2
@app.get("/lecturas/recientes", summary="Últimas 50 lecturas válidas")
async def lecturas_recientes():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT turbina_id,
                   to_timestamp(timestamp_ms/1000) AS hora,
                   potencia_generada, velocidad_viento,
                   temperatura_nacelle, rpm_rotor
            FROM lecturas
            ORDER BY timestamp_ms DESC
            LIMIT 50;
        """)
        return cur.fetchall()


# Devuelve los últimos 30 datos rechazados — para ver en el panel qué llegó corrupto
@app.get("/rechazados/recientes", summary="Últimos 30 datos rechazados")
async def rechazados_recientes():
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT turbina_id, motivo,
                   to_timestamp(timestamp_ms/1000) AS hora,
                   datos_crudos
            FROM rechazados
            ORDER BY creado_en DESC
            LIMIT 30;
        """)
        return cur.fetchall()


# Endpoint de salud — para comprobar que el concentrador está activo
@app.get("/health")
async def health():
    return {"status": "ok"}


# Guarda una lectura válida en la tabla lecturas — reciclado de guardar_lectura() del reto 2
def _guardar_lectura(d: DatosTurbina):
    sql = """
    INSERT INTO lecturas
      (turbina_id, timestamp_ms, velocidad_viento, potencia_generada,
       temperatura_nacelle, rpm_rotor)
    VALUES (%s, %s, %s, %s, %s, %s);
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                d.turbina_id, d.timestamp, d.velocidad_viento,
                d.potencia_generada, d.temperatura_nacelle, d.rpm_rotor
            ))
    except Exception as e:
        print(f"[ERROR BD] No se pudo guardar: {e}")


# Guarda un dato rechazado en la tabla rechazados junto al motivo del rechazo
def _guardar_rechazado(turbina_id, timestamp_ms, motivo, datos):
    sql = """
    INSERT INTO rechazados (turbina_id, timestamp_ms, motivo, datos_crudos)
    VALUES (%s, %s, %s, %s);
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (turbina_id, timestamp_ms, motivo, json.dumps(datos)))
    except Exception as e:
        print(f"[ERROR BD] No se pudo guardar rechazo: {e}")


# Tarea en segundo plano que se ejecuta cada 60 segundos.
# Calcula con SQL la media y el total de producción de cada turbina en el último minuto
# y los guarda en la tabla agregados_minutales.
async def tarea_agregacion_minutal():
    while True:
        await asyncio.sleep(60)
        print(f"[AGREGACION] Calculando agregados minutales...")
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO agregados_minutales
                      (minuto, turbina_id, potencia_media, potencia_total,
                       viento_medio, num_lecturas)
                    SELECT
                        date_trunc('minute', to_timestamp(timestamp_ms/1000.0)) AS minuto,
                        turbina_id,
                        AVG(potencia_generada)  AS potencia_media,
                        SUM(potencia_generada)  AS potencia_total,
                        AVG(velocidad_viento)   AS viento_medio,
                        COUNT(*)                AS num_lecturas
                    FROM lecturas
                    WHERE to_timestamp(timestamp_ms/1000.0) >= NOW() - INTERVAL '2 minutes'
                      AND to_timestamp(timestamp_ms/1000.0) <  date_trunc('minute', NOW())
                    GROUP BY 1, 2
                    ON CONFLICT DO NOTHING;
                """)
            print("[AGREGACION] Agregados minutales guardados correctamente")
        except Exception as e:
            print(f"[ERROR AGREGACION] {e}")
