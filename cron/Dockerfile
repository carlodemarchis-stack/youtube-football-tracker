FROM python:3.11-slim

WORKDIR /app

# Lightweight deps — NO Streamlit
COPY requirements-cron.txt .
RUN pip install --no-cache-dir -r requirements-cron.txt

COPY src/ src/
COPY scripts/ scripts/

CMD ["python", "scripts/hourly_rss.py"]
