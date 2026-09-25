# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-compile --retries 5 --timeout 60 -r requirements.txt

COPY . .

EXPOSE 8000

ENV UVICORN_WORKERS=4
CMD uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
