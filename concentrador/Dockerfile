# Define las instrucciones para construir el contenedor del concentrador FastAPI
FROM python:3.11-slim

WORKDIR /app

COPY concentrador/requirements.txt .

# Instala las dependencias sin guardar caché para reducir el tamaño de la imagen
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el concentrador y el modelo compartido con las 6 dimensiones de validación.
# modelo.py está en la raíz del proyecto y lo necesitan tanto el concentrador como los generadores.
COPY concentrador/concentrador.py .
COPY modelo.py .

# Arranca FastAPI con uvicorn, escuchando en todas las interfaces del contenedor
CMD ["uvicorn", "concentrador:app", "--host", "0.0.0.0", "--port", "8000"]
