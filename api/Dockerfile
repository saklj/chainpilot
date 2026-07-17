FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    DUCKDB_PATH=/app/data/chainpilot.duckdb

WORKDIR /app/api

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" chainpilot \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home chainpilot \
    && mkdir -p /app/data "${FASTEMBED_CACHE_PATH}" \
    && chown -R chainpilot:chainpilot /app

COPY --chown=chainpilot:chainpilot . .

USER chainpilot

# Build-time network access is intentional: keep the BGE model inside the image so
# the first chat request does not need to download it at runtime.
RUN python -c "from agent.retrieval import default_embedder; default_embedder().embed(['ChainPilot 模型预热'])"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
