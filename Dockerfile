FROM python:3.12-slim

WORKDIR /app

# lxml and yfinance need gcc + xml libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Secrets injected as env vars by Cloud Run — no .env file needed in prod
CMD ["python", "main.py", "--skip-backtest"]
