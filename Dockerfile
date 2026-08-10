FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 120 --retries 8 -r requirements.txt

COPY app.py schema.py app.js index.html styles.css ./
COPY migrations ./migrations
COPY static ./static

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /data /seed \
    && chown -R appuser:appuser /app /data /seed

USER appuser

EXPOSE 5000

CMD ["sh", "-c", "exec waitress-serve --listen=0.0.0.0:${PORT} app:app"]
