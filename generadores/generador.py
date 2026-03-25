import json
import os
import time
import random
import requests
import numpy as np

# Recoge el valor de la variable de entorno
def leer_config(clave, valor_por_defecto):
    return os.environ.get(clave, valor_por_defecto)

# Función para generar datos realistas por medio de la Distribución Normal con numpy.
def generar_dato(media, variacion, minimo=None, maximo=None):
    valor = np.random.normal(float(media), variacion)
    # Recorta el valor si se pasan límites, para que no salga un dato imposible
    if minimo is not None:
        valor = max(minimo, valor)
    if maximo is not None:
        valor = min(maximo, valor)
    return round(float(valor), 2)

def main():
    # Llama a leer_config() para cargar todas las variables
    concentrador_url  = leer_config("CONCENTRADOR_URL",  "http://concentrador:8000")
    api_key           = leer_config("API_KEY",           "parque_eolico_secreto")
    turbina_id        = leer_config("TURBINA_ID",        "turbina_01")
    intervalo         = int(leer_config("INTERVALO",     "3"))

    # Características físicas de esta turbina cada contenedor tiene valores distintos
    # kW
    potencia_nominal  = float(leer_config("POTENCIA_NOMINAL",  "2000"))   
    # m/s media local
    viento_medio      = float(leer_config("VIENTO_MEDIO",      "12.0"))   
    # °C base de la góndola
    temp_nacelle_base = float(leer_config("TEMP_NACELLE_BASE", "45.0"))   
    # probabilidad de dato erróneo
    prob_error        = float(leer_config("PROB_ERROR",        "0.10"))   

    print(f"[{turbina_id}] Turbina iniciada | "
          f"Nominal={potencia_nominal}kW | "
          f"Viento medio={viento_medio}m/s | "
          f"P(error)={prob_error*100:.0f}%")

    # Espera a que el concentrador esté listo antes de empezar a enviar
    time.sleep(5)

    # Envío de datos
    while True:
        # Crea el paquete de datos de la turbina con distribución normal
        viento = generar_dato(viento_medio, 2.5, minimo=0.0, maximo=35.0)

        # La potencia sigue una curva física real: sin viento no genera, con demasiado se para
        if viento < 3.0:
            # velocidad mínima de arranque
            potencia = 0.0                                      
        elif viento > 25.0:
            # parada de seguridad por viento excesivo
            potencia = 0.0                                      
        else:
            factor = min(1.0, (viento - 3.0) / (12.0 - 3.0))
            potencia = potencia_nominal * (factor ** 3)
            potencia = generar_dato(potencia, potencia * 0.05, minimo=0.0, maximo=potencia_nominal)

        rpm  = generar_dato(14.0, 1.5, minimo=0.0, maximo=24.0)
        temp = generar_dato(temp_nacelle_base + (potencia / potencia_nominal) * 15, 2.0,
                            minimo=-15.0, maximo=75.0)

        datos = {
            "turbina_id":          turbina_id,
            # milisegundos epoch
            "timestamp":           int(time.time() * 1000),    
            "velocidad_viento":    viento,
            "potencia_generada":   potencia,
            "temperatura_nacelle": temp,
            "rpm_rotor":           rpm,
            "es_erroneo":          False,
        }

        # Con probabilidad prob_error, corrompe el dato para simular un fallo de la turbina
        if random.random() < prob_error:
            datos = inyectar_error(datos)
            print(f"[{turbina_id}] Inyectando dato erróneo...")

        # Se transforma en JSON y se envía al concentrador por HTTP
        enviar_dato(datos, concentrador_url, api_key, turbina_id)

        time.sleep(intervalo)


# Corrompe el dato de una de tres formas para simular fallos reales de sensores
def inyectar_error(datos: dict) -> dict:
    tipo = random.choice(["fuera_rango", "marcado", "negativo"])

    if tipo == "fuera_rango":
        # Pone un valor físicamente imposible en un campo aleatorio
        campo = random.choice(["velocidad_viento", "potencia_generada",
                               "temperatura_nacelle", "rpm_rotor"])
        if campo == "velocidad_viento":
            # ningún viento opera a 100 m/s
            datos[campo] = round(random.uniform(45.0, 100.0), 2)   
        elif campo == "potencia_generada":
            # supera el límite del generador
            datos[campo] = round(random.uniform(3500.0, 9999.0), 2) 
        elif campo == "temperatura_nacelle":
            # temperatura de incendio
            datos[campo] = round(random.uniform(90.0, 200.0), 2)    
        elif campo == "rpm_rotor":
            # destruiría el rotor
            datos[campo] = round(random.uniform(30.0, 99.0), 2)     

    elif tipo == "marcado":
        # El generador avisa directamente al concentrador de que el dato es erróneo
        datos["es_erroneo"] = True

    elif tipo == "negativo":
        # Valor negativo — imposible físicamente en estos campos
        campo = random.choice(["velocidad_viento", "potencia_generada", "rpm_rotor"])
        datos[campo] = round(random.uniform(-100.0, -1.0), 2)

    return datos


# Envía el dato al concentrador FastAPI con la API Key en la cabecera HTTP.
# El concentrador responde mediante HTTP indicando si acepta o rechaza el dato.
def enviar_dato(datos: dict, url: str, api_key: str, turbina_id: str):
    try:
        respuesta = requests.post(
            f"{url}/datos",
            json=datos,
            # La API Key va en la cabecera HTTP
            headers={"x-api-key": api_key},
            timeout=5
        )
        if respuesta.status_code == 200:
            print(f"[{turbina_id}] Enviado -> "
                  f"potencia={datos['potencia_generada']:.1f}kW | "
                  f"viento={datos['velocidad_viento']:.1f}m/s")
        elif respuesta.status_code == 422:
            # El concentrador rechazó el dato — nos lo comunica directamente
            print(f"[{turbina_id}] Rechazado por el concentrador -> {respuesta.json().get('detail')}")
        elif respuesta.status_code == 401:
            print(f"[{turbina_id}] API Key inválida — acceso denegado")
        else:
            print(f"[{turbina_id}] Error HTTP {respuesta.status_code}: {respuesta.text}")
    except requests.exceptions.ConnectionError:
        print(f"[{turbina_id}] Concentrador no disponible, reintentando...")
    except Exception as e:
        print(f"[{turbina_id}] Error inesperado: {e}")


if __name__ == "__main__":
    main()
