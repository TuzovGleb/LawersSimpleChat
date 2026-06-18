FROM python:3.11-slim

# antiword: extract text from legacy .doc (binary Word) files in a subprocess.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates antiword \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/0.5.27/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync

COPY ./app ./app

ENV LOGS_PATH=/var/log/app
# Yandex Serverless Containers inject PORT (default 8080); fall back to 8001 locally.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn app.server.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
