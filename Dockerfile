# Dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    gcc \
    libssl-dev \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir netmiko ansible pandas openpyxl requests
WORKDIR /app
COPY . /app
CMD ["python3"]