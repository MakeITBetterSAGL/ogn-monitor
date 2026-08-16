FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OGN_RUNTIME_MODE=docker \
    OGN_DATABASE=/data/ogn.sqlite3 \
    OGN_DDB_FILE=/data/ogn-ddb.json \
    OGN_WEB_HOST=0.0.0.0 \
    OGN_WEB_PORT=5000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app.py collector.py parser.py docker-entrypoint.py ./
COPY templates ./templates
COPY static ./static

RUN groupadd --gid 10001 ogn \
    && useradd --uid 10001 --gid ogn --no-create-home --home-dir /app ogn \
    && mkdir -p /data \
    && chown -R ogn:ogn /app /data

USER ogn

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=3)" || exit 1

ENTRYPOINT ["python", "docker-entrypoint.py"]
