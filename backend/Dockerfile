# Backend Dockerfile - FastAPI + AI Pipeline
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
    libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0 \
    libffi-dev libjpeg-dev libopenjp2-7-dev \
    qpdf \
    fonts-liberation fonts-dejavu fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
