# ChargeMyHyundai Price Map - Docker Image
# Multi-stage build for optimal image size

# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.12-slim AS production

# Labels
LABEL org.opencontainers.image.title="ChargeMyHyundai Price Map"
LABEL org.opencontainers.image.description="Interactive map showing charging station prices for Hyundai vehicles"
LABEL org.opencontainers.image.vendor="b0t.at"
LABEL org.opencontainers.image.source="https://github.com/utesgui/chargemyhyundai-map"

# Install gosu for dropping privileges in entrypoint (Debian alternative to Alpine's su-exec)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application files
COPY --chown=appuser:appuser app.py .
COPY --chown=appuser:appuser chargemyhyundai_api.py .
COPY --chown=appuser:appuser station_cache.py .
COPY --chown=appuser:appuser background_updater.py .
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser static/ ./static/

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create necessary directories (including data dir for SQLite cache)
RUN mkdir -p /app/templates /app/static/css /app/static/js /app/data && \
    chown -R appuser:appuser /app

# NOTE: Don't switch to non-root user here - entrypoint will handle it
# This allows the entrypoint to fix volume permissions before dropping privileges

# Environment variables
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CACHE_DB_PATH=/app/data/station_cache.db

# Volume for persistent cache data
VOLUME /app/data

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1

# Entrypoint handles volume permissions and drops to non-root user
ENTRYPOINT ["docker-entrypoint.sh"]

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
