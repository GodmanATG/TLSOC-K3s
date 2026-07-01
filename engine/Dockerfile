# ─────────────────────────────────────────────────────────────
# STAGE 1: Builder
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libmaxminddb-dev \
        libsnappy-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install dependencies into a separate directory for easy copying
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─────────────────────────────────────────────────────────────
# STAGE 2: Final Runtime Image
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install only the runtime C-libraries (NOT the compilers)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmaxminddb0 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy the compiled Python dependencies from Stage 1
COPY --from=builder /install /usr/local

COPY main.py        .
COPY config.yaml    .
COPY core/          ./core/
COPY utils/         ./utils/
COPY rules/         ./rules/

RUN mkdir -p logs database \
    && mkdir -p /var/log/soc_output \
    && chown -R appuser:appuser /app \
    && chown appuser:appuser /var/log/soc_output

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

USER appuser

ENV PYTHONUNBUFFERED=1
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

ENTRYPOINT ["python3", "main.py"]
