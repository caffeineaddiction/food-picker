# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Install cloudflared for the optional --tunnel flag.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
      -o /usr/share/keyrings/cloudflare-main.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(. /etc/os-release && echo $VERSION_CODENAME) main" \
      > /etc/apt/sources.list.d/cloudflared.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends cloudflared \
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
