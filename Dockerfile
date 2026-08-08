# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Install cloudflared for the optional --tunnel flag.
# Use the standalone binary — the apt repo doesn't support all Debian releases.
ARG TARGETARCH
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${TARGETARCH}" \
 && chmod +x /usr/local/bin/cloudflared \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8000

ENTRYPOINT ["python", "main.py"]
CMD ["--host", "0.0.0.0"]
