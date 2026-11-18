# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 app \
    && mkdir /data \
    && chown app:app /data

USER app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["feedly-filter"]
