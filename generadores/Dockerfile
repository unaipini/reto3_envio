# Define las instrucciones para el contenedor de cada turbina.
# Se diferencian en las variables de entorno del docker-compose, no en la imagen.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# Instala las dependencias sin guardar caché para reducir el tamaño de la imagen
RUN pip install --no-cache-dir -r requirements.txt

COPY generador.py .

CMD ["python", "generador.py"]
