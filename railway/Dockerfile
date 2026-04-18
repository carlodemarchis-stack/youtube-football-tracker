FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY scripts/ scripts/
COPY .streamlit/ .streamlit/

# Default command — overridden per service in railway.toml
CMD ["echo", "Use railway.toml to configure cron services"]
