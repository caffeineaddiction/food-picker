# Docker

## Build

```bash
docker build -t food-picker .
```

## Run

```bash
# Local network only
docker run -p 8000:8000 food-picker

# With Cloudflare tunnel (phones off-network can join via QR)
docker run -p 8000:8000 food-picker --tunnel
```

Open `http://localhost:8000` on the TV. Phones scan the QR code.

## Options

All `main.py` flags work as container arguments:

```bash
docker run -p 9000:9000 food-picker --port 9000 --tunnel --tunnel-timeout 30 --log-level info
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8000` | Server port |
| `--tunnel` | off | Start a Cloudflare quick tunnel |
| `--tunnel-timeout` | `25` | Seconds to wait for tunnel URL |
| `--log-level` | `warning` | Uvicorn log level |
