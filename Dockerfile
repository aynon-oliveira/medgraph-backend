FROM python:3.11-slim

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Transcrição de áudio (Whisper). Para pular: INSTALAR_WHISPER=0 no .env (imagem menor)
ARG INSTALAR_WHISPER=1
COPY requirements-ia.txt .
RUN if [ "$INSTALAR_WHISPER" = "1" ]; then pip install --no-cache-dir -r requirements-ia.txt; fi

COPY . .
