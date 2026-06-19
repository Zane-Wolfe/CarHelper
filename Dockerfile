FROM python:3.12-slim

# bluez provides bluetoothctl + rfcomm, used by app/bluetooth.py to drive the
# host BlueZ stack (via the mounted dbus socket) for Classic SPP pairing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.29" "obd>=0.7.2" \
        "pyserial>=3.5" "pandas>=2.2" "pyarrow>=15.0"

COPY app ./app
COPY tools ./tools
COPY run.py ./

EXPOSE 8000

# Exec form so uvicorn runs as PID 1 and receives SIGTERM directly (fast, clean
# `docker compose down`). run.py reads HOST/PORT from the environment.
CMD ["python", "run.py"]
