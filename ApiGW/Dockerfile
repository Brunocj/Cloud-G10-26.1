FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY keys/ ./keys/
RUN chmod 600 ./keys/id_ed25519

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
