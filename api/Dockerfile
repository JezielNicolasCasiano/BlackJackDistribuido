# Versión fija
FROM python:3.12.16-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencias antes que el código para aprovechar la caché
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .

# Sin root
RUN useradd --create-home app
USER app

EXPOSE 8000
# Forma exec para que uvicorn reciba SIGTERM
# Sin registro de acceso: main.py escribe uno propio que incluye la clave
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
