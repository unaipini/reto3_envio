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

# Configuración del concentrador a través de variables de entorno
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
# Aplica un patrón de reintento cada 2 segundos si la BD aún no está lista.
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

        # Tabla de lecturas válidas
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


# Conexión global a la base de datos
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
# Representa el mecanismo de seguridad implementado en la comunicación.
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


# Recibe el dato de una turbina y lo procesa.
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
        raise HTTPException(status_code=422, detail="Dato marcado como erróneo
