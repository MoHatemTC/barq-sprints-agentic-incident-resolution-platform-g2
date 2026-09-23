FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

ENV UVICORN_WORKERS=4
CMD uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}