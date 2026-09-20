FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

ENV UVICORN_WORKERS=4
CMD uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}