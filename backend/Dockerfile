# Backend Dockerfile - FastAPI app
# Use official Python slim image
FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Install build deps and cleanup to keep image small
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/app ./app

# Copy repo-root pipeline modules needed at runtime
COPY generate_budget.py generate_budget_pipeline.py ./

# Expose port
EXPOSE 8000

# Environment defaults (can be overridden at runtime)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Run Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
