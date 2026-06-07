FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/0.5.27/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync

COPY ./app ./app

ENV LOGS_PATH=/var/log/app
EXPOSE 8001

CMD ["uv", "run", "uvicorn", "app.server.main:app", "--host", "0.0.0.0", "--port", "8001"]
