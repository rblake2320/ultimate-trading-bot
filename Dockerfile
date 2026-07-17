FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY main.py config.example.json ./

# Paper mode by default. Live trading additionally requires
# TRADING_BOT_LIVE=YES and exchange keys in the environment.
ENV PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /app/data /app/logs \
    && chown -R bot:bot /app
USER bot

VOLUME ["/app/data", "/app/logs"]

# Dashboard (no auth — see .env.example). Inside Docker the default
# 127.0.0.1 bind is unreachable through -p; set DASHBOARD_HOST=0.0.0.0
# consciously if you want the dashboard published.
EXPOSE 8899
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('DASHBOARD_PORT','8899')}/api/status\", timeout=4)"]

ENTRYPOINT ["python", "main.py"]
CMD ["trade"]
