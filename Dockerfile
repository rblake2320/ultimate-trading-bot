FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY main.py config.example.json ./

# Paper mode by default. Live trading additionally requires
# TRADING_BOT_LIVE=YES and exchange keys in the environment.
ENV PYTHONUNBUFFERED=1

VOLUME ["/app/data", "/app/logs"]

ENTRYPOINT ["python", "main.py"]
CMD ["trade"]
