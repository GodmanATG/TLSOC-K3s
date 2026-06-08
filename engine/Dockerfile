# ─────────────────────────────────────────────────────────────
# STAGE 1: Base image
# ─────────────────────────────────────────────────────────────
# We use python:3.11-slim — the official Python image but stripped
# down to only what Python needs. This keeps the final container
# small (~150MB instead of ~900MB for the full image).
FROM python:3.11-slim

# ─────────────────────────────────────────────────────────────
# STAGE 2: System-level dependencies
# ─────────────────────────────────────────────────────────────
# We need gcc and libmaxminddb-dev to compile the geoip2 C extension for fast performance.
# ca-certificates is needed for secure HTTPS downloads.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libmaxminddb0 \
        libmaxminddb-dev \
        libsnappy-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────
# STAGE 3: Non-root user for security
# ─────────────────────────────────────────────────────────────
# Running containers as root is a major security vulnerability. 
# We create a specific appuser with UID 1001 to run the engine.
RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser

# ─────────────────────────────────────────────────────────────
# STAGE 4: Set the working directory inside the container
# ─────────────────────────────────────────────────────────────
WORKDIR /app

# ─────────────────────────────────────────────────────────────
# STAGE 5: Install Python dependencies
# ─────────────────────────────────────────────────────────────
# We copy requirements.txt FIRST to take advantage of Docker layer caching.
# pip install with --no-cache-dir tells pip NOT to save download cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────
# STAGE 6: Copy all engine source code
# ─────────────────────────────────────────────────────────────
COPY main.py        .
COPY config.yaml    .
COPY core/          ./core/
COPY utils/         ./utils/
COPY rules/         ./rules/

# ─────────────────────────────────────────────────────────────
# STAGE 7: Create required runtime directories
# ─────────────────────────────────────────────────────────────
# The engine needs logs/ and database/ to exist.
# We also create /var/log/soc_output and chown it to appuser so the engine can write there.
RUN mkdir -p logs database \
    && mkdir -p /var/log/soc_output \
    && chown -R appuser:appuser /app \
    && chown appuser:appuser /var/log/soc_output

# ─────────────────────────────────────────────────────────────
# STAGE 8: GeoIP Database (Optional Build Arg)
# ─────────────────────────────────────────────────────────────
ARG MAXMIND_LICENSE_KEY=""
RUN if [ -n "$MAXMIND_LICENSE_KEY" ]; then \
      echo "[+] Downloading GeoIP database..." && \
      curl -L \
        "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-City&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz" \
        -o /tmp/geo.tar.gz && \
      tar -xzf /tmp/geo.tar.gz -C /tmp && \
      mv /tmp/GeoLite2-City_*/GeoLite2-City.mmdb /app/database/ && \
      rm -rf /tmp/geo* && \
      echo "[+] GeoIP database installed successfully" ; \
    else \
      echo "[!] No MaxMind key provided — GeoIP enrichment will be disabled" ; \
    fi

# ─────────────────────────────────────────────────────────────
# STAGE 9: Switch to non-root and set Environment Variables
# ─────────────────────────────────────────────────────────────
USER appuser

# PYTHONUNBUFFERED=1 ensures real-time logs without buffering delays in 'docker logs'
ENV PYTHONUNBUFFERED=1
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

# ─────────────────────────────────────────────────────────────
# STAGE 10: Start the engine
# ─────────────────────────────────────────────────────────────
ENTRYPOINT ["python3", "main.py"]
