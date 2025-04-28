FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ./requirements.txt /app/

RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir --upgrade wheel setuptools && \
    pip3 install --no-cache-dir -r requirements.txt

COPY ./db /app/db
COPY ./src /app/src

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["python", "src/main.py"]