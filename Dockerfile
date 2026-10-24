# Aegis AI — Production Container Image
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (curl for healthchecks, procps for psutil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project specification and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy source code and default configs
COPY aegis/ aegis/
COPY migrations/ migrations/
COPY config.yaml policies.yaml ./

# Create data directory for SQLite WAL database
RUN mkdir -p /app/data
ENV AEGIS_DB_PATH=/app/data/aegis.db

EXPOSE 8000

# Default entrypoint runs the control plane server
ENTRYPOINT ["aegis"]
CMD ["server", "--host", "0.0.0.0", "--port", "8000", "--db", "/app/data/aegis.db"]
