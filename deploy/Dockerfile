# ============================================================
# OjolBoost MAMS — Dockerfile
# Target: Google Cloud Run (asia-southeast1)
# Base : python:3.11-slim (ringan, aman, official)
# Port : 8080 (standar Cloud Run)
# ============================================================

# ── Stage 1: Builder ────────────────────────────────────────
# Pisahkan stage installasi dari runtime untuk mengurangi attack surface
# dan mempercepat layer caching di Artifact Registry.
FROM python:3.11-slim AS builder

# Metadata
LABEL maintainer="ojolboost-team"
LABEL org.opencontainers.image.title="OjolBoost MAMS"
LABEL org.opencontainers.image.description="Multi-Agent Management System — MCP Server"
LABEL org.opencontainers.image.version="1.0.0"

# Variabel Python best practices:
# PYTHONDONTWRITEBYTECODE=1 → jangan buat file .pyc (hemat disk)
# PYTHONUNBUFFERED=1        → stdout/stderr langsung ke Cloud Logging (tanpa buffer)
# PIP_NO_CACHE_DIR=1        → jangan cache pip (hemat image size)
# PIP_DISABLE_PIP_VERSION_CHECK=1 → skip version check pip (percepat build)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /install

# Salin requirements terlebih dahulu untuk memanfaatkan Docker layer cache.
# Layer ini hanya di-rebuild jika requirements.txt berubah.
COPY requirements.txt .

# Upgrade pip dan install semua dependensi ke direktori khusus.
# --no-deps tidak digunakan agar transitive dependencies tetap terinstall.
RUN pip install --upgrade pip==24.0 \
    && pip install --prefix=/install/deps \
        --requirement requirements.txt \
        # Exclude dev/test tools dari production image
        --ignore-requires-python


# ── Stage 2: Runtime ────────────────────────────────────────
# Mulai dari image bersih — tanpa sisa build tools dari stage 1.
FROM python:3.11-slim AS runtime

# Security: Jalankan sebagai non-root user (best practice Cloud Run)
# Cloud Run sudah mendukung non-root containers sejak 2023.
RUN groupadd --gid 1001 ojolboost \
    && useradd --uid 1001 --gid ojolboost --no-create-home --shell /bin/false ojolboost

# Environment variables runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Path ke dependensi dari builder stage
    PYTHONPATH="/app:/install/deps/lib/python3.11/site-packages" \
    # Konfigurasi runtime default (di-override oleh Cloud Run env vars)
    PORT=8080 \
    MCP_SERVER_PORT=8080 \
    LOG_LEVEL=INFO \
    LOG_FORMAT=json \
    # Google Cloud — diisi saat deploy, bukan hardcode di sini
    GOOGLE_CLOUD_PROJECT="" \
    VERTEX_AI_LOCATION="asia-southeast1" \
    BIGQUERY_DATASET="ojolboosttrack2" \
    BIGQUERY_LOCATION="asia-southeast1"

WORKDIR /app

# Salin dependensi hasil install dari stage builder
COPY --from=builder /install/deps /install/deps

# Salin source code aplikasi.
# .dockerignore harus memastikan hanya file yang diperlukan yang masuk.
COPY agents/      ./agents/
COPY guardrails/  ./guardrails/
COPY mcp_server/  ./mcp_server/
COPY shared/      ./shared/
COPY skills/      ./skills/

# Ganti kepemilikan ke non-root user
RUN chown -R ojolboost:ojolboost /app

# Jalankan sebagai non-root
USER ojolboost

# Expose port standar Cloud Run
EXPOSE 8080

# Health check untuk Cloud Run readiness probe.
# Ping endpoint /health setiap 30 detik.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" \
    || exit 1

# ── ENTRYPOINT ──────────────────────────────────────────────
# Gunakan Gunicorn sebagai WSGI server production-grade.
# Workers: 2 (optimal untuk Cloud Run 1 vCPU + memory < 512MB)
# Threads: 8 per worker (cocok untuk I/O-bound agent workload)
# Timeout: 120 detik (toleransi untuk Vertex AI synthesis latency)
# Bind   : 0.0.0.0:$PORT (Cloud Run inject $PORT env var)
#
# Catatan: MCP Server (mcp_server/server.py) mengekspos Flask app
# sebagai objek 'app' yang kompatibel dengan Gunicorn WSGI.
CMD ["sh", "-c", \
     "exec gunicorn \
        --workers 2 \
        --threads 8 \
        --worker-class gthread \
        --timeout 120 \
        --keep-alive 5 \
        --bind 0.0.0.0:${PORT:-8080} \
        --log-level ${LOG_LEVEL:-info} \
        --access-logfile - \
        --error-logfile - \
        mcp_server.server:app"]
