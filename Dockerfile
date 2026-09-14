FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install uv/uvx (needed at runtime by mcp_client.py to launch the
# AviationStack MCP server as a subprocess via `uvx aviationstack-mcp`).
# Copying the prebuilt binaries from the official uv image is the
# recommended, most reliable way to get uv into a Docker image —
# avoids needing curl+shell-script installers or extra build steps.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]