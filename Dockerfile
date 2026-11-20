FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium

# Dependencias del sistema: Chromium para renderizar CVs HTML -> PDF y curl para healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    curl \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del motor (los datos del usuario se montan vía bind-mount en docker-compose)
COPY AGENTS.md CLAUDE.md agentes.yaml ./
COPY scripts/ ./scripts/
COPY app/ ./app/
COPY .agents/ ./.agents/

RUN mkdir -p /app/.locks && chmod 777 /app/.locks

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
