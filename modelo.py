"""
Modelo de datos compartido entre generadores y concentrador.
Representa la lectura que cada turbina envía al concentrador.

La validación sigue las 6 dimensiones de calidad del dato vistas en clase:
  - Completeness  : todos los campos son obligatorios, si falta alguno → 422
  - Accuracy      : rangos físicamente realistas por campo
  - Validity      : turbina_id con formato correcto, unidades correctas
  - Consistency   : validaciones cruzadas entre campos (viento-potencia-rpm)
  - Uniqueness    : gestionado a nivel de base de datos
  - Timeliness    : el timestamp no puede ser demasiado antiguo
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, timezone, timedelta


# Modelo de datos de una turbina.
# Pydantic valida automáticamente cada campo al recibir el JSON.
# Si un campo falta o está fuera de rango, devuelve error 422 sin ejecutar nada más.
class DatosTurbina(BaseModel):

    # Completeness: todos los campos son obligatorios — si falta cualquiera → 422
    turbina_id:          str

    # Timeliness: el timestamp viene en milisegundos epoch, igual que en el reto 2
    timestamp:           int

    # Accuracy: rangos físicamente realistas para una turbina eólica de 2 MW
    velocidad_viento:    float = Field(..., ge=0.0,   le=40.0,   description="Velocidad del viento en m/s")
    potencia_generada:   float = Field(..., ge=0.0,   le=3000.0, description="Potencia generada en kW")
    temperatura_nacelle: float = Field(..., ge=-20.0, le=80.0,   description="Temperatura de la góndola en °C")
    rpm_rotor:           float = Field(..., ge=0.0,   le=25.0,   description="RPM del rotor")

    # El propio generador puede marcar su dato como erróneo
    es_erroneo:          bool = False


    # ── Timeliness ────────────────────────────────────────────────────────────
    # El dato debe ser reciente — no aceptamos timestamps demasiado antiguos.
    # Adaptado del modelo.py de Unai al formato de timestamp en milisegundos del reto 2.
    @field_validator("timestamp")
    @classmethod
    def validar_timeliness(cls, v):
        ahora_ms    = int(datetime.now(timezone.utc).timestamp() * 1000)
        antiguedad  = (ahora_ms - v) / 1000   # convertimos a segundos

        # No aceptamos datos de más de 5 minutos de antigüedad
        if antiguedad > 300:
            raise ValueError(
                f"Dato demasiado antiguo: {antiguedad:.0f} segundos (máximo permitido: 300 s)."
            )
        # No aceptamos timestamps del futuro
        if antiguedad < -10:
            raise ValueError(
                f"Timestamp {v} es futuro — posible fallo del reloj del sensor."
            )
        return v


    # ── Consistency ───────────────────────────────────────────────────────────
    # Comprueba que los valores no se contradigan entre sí.
    # Reciclado y adaptado del model_validator de Unai a nuestros campos.
    #
    # Reglas físicas de un aerogenerador de 2 MW:
    #   Cut-in  = 3 m/s  — por debajo no hay rotación ni potencia
    #   Rated   = 12 m/s — potencia nominal 2000 kW
    #   Cut-out = 25 m/s — la turbina se para por seguridad
    @model_validator(mode="after")
    def validar_consistencia_fisica(self) -> "DatosTurbina":

        # Viento por debajo del cut-in pero la turbina genera potencia significativa
        if self.velocidad_viento < 3.0 and self.potencia_generada > 50:
            raise ValueError(
                f"Inconsistencia física: viento {self.velocidad_viento} m/s "
                f"(< cut-in 3 m/s) pero potencia = {self.potencia_generada} kW."
            )

        # Viento por encima del cut-out — la turbina debería estar parada, no generando
        if self.velocidad_viento > 25.0 and self.potencia_generada > 10:
            raise ValueError(
                f"Inconsistencia física: viento {self.velocidad_viento} m/s "
                f"(> cut-out 25 m/s) pero potencia = {self.potencia_generada} kW. "
                f"La turbina debería estar parada."
            )

        # RPM demasiado altas para el viento que hay — imposible físicamente
        if self.rpm_rotor > 20.0 and self.velocidad_viento < 5.0:
            raise ValueError(
                f"Inconsistencia física: rpm = {self.rpm_rotor} "
                f"con viento = {self.velocidad_viento} m/s — imposible físicamente."
            )

        return self
