FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py app.js index.html styles.css ./
COPY static ./static

EXPOSE 5000

CMD ["sh", "-c", "exec waitress-serve --listen=0.0.0.0:${PORT} app:app"]
