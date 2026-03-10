# Dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    gcc \
    libssl-dev \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
WORKDIR /app
COPY . /app
CMD ["python", "main.py"]
