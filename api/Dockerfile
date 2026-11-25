# Imagen base fija (la versión de las imágenes es una variable controlada, diseño 9.1)
FROM python:3.12.16-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Primero las dependencias y después el código: así, cambiar main.py no
# obliga a reinstalar todo (la capa de pip queda en caché).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .

# No correr como root dentro del contenedor
RUN useradd --create-home app
USER app

EXPOSE 8000
# Forma de lista (exec): uvicorn es el PID 1 y recibe SIGTERM directamente
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
