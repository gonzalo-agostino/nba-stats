FROM python:3.12-slim

# System libraries needed by LightGBM (libgomp1) and healthchecks (curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create non-root user
RUN useradd -m -u 1000 user

ENV HOME=/home/user \
    PATH="/home/user/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR $HOME/app

# Copy project source and install editable into system Python so project_root = /home/user/app
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs

RUN uv pip install --system --no-cache --no-build-isolation -e . || uv pip install --system --no-cache -e .

# Ensure writable data & artifacts directories exist for HF Dataset pull
RUN mkdir -p data/warehouse data/raw data/processed artifacts/models \
    && chown -R user:user $HOME/app

USER user

EXPOSE 8080

CMD ["sh", "-c", "uvicorn nba_stats.api.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
