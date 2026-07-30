"""dl-picker entry point.

    uv run main.py

Serves the display, the phone controller and the realtime protocol from one
process. Prints every URL the office needs, including the Cloudflare tunnel
command for phones that are not on the office wifi (SPEC.md §7.6).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket

import uvicorn

from server import tunnel as tunnel_module

BANNER = r"""
      _ _                _      _
   __| | |    _ __  _  __| | ___| |__ ___  _ _
  / _` | |___| '_ \| |/ _| |/ / / /-_) '_|(_-<
  \__,_|_____|_.__/|_|\__|_|\_\_\_\___|_|  /__/   🏇  dinner, decided by horse race
"""


def local_ip() -> str:
    """Best-effort LAN address, for phones on the office wifi."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def print_startup(host: str, port: int, *, tunnel_requested: bool = False) -> None:
    address = local_ip()
    print(BANNER)
    print("  Main display (put this on the TV):")
    print(f"    http://localhost:{port}/")
    print()
    print("  Phones on the same wifi:")
    print(f"    http://{address}:{port}/play")
    print()
    if not tunnel_requested:
        print("  Phones on cellular / another network — easiest route:")
        print("    uv run main.py --tunnel")
        if tunnel_module.is_available():
            print("    (cloudflared is installed, so this will just work)")
        else:
            print("    (needs cloudflared: brew install cloudflared)")
        print("    Or run the tunnel yourself and paste its URL into the")
        print('    "Phones off this network?" box under the QR code.')
        print("    A QR pointing at localhost sends phones to themselves.")
        print()
    if os.environ.get("PUBLIC_URL"):
        print(f"  PUBLIC_URL override active: {os.environ['PUBLIC_URL']}")
        print()


def start_tunnel(port: int, timeout: float) -> tunnel_module.Tunnel | None:
    """Bring up a Cloudflare quick tunnel and point the QR code at it."""

    print("  Starting Cloudflare tunnel — this publishes the server publicly…")
    tunnel = tunnel_module.Tunnel(port=port, timeout=timeout)
    url = tunnel.start()
    if url is None:
        print(f"  ⚠  Tunnel unavailable: {tunnel.error}")
        for line in tunnel.recent_log():
            print(f"     {line}")
        print("     Carrying on without it — the QR will use this machine's address,")
        print("     and you can paste a tunnel URL in the lobby at any time.")
        print()
        return None

    tunnel.publish()
    print(f"  ✅ Tunnel up: {url}")
    print("     The QR code now points here automatically. Phones anywhere can join.")
    print()
    return tunnel


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the dl-picker server.")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="auto-reload for development")
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="start a Cloudflare quick tunnel and point the QR code at it "
        "(publishes this server to the public internet)",
    )
    parser.add_argument(
        "--tunnel-timeout",
        type=float,
        default=tunnel_module.DEFAULT_TIMEOUT,
        help="seconds to wait for the tunnel URL (default: 25)",
    )
    parser.add_argument(
        "--log-level", default="warning", help="uvicorn log level (default: warning)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
    )
    print_startup(args.host, args.port, tunnel_requested=args.tunnel)

    tunnel = start_tunnel(args.port, args.tunnel_timeout) if args.tunnel else None
    try:
        uvicorn.run(
            "server.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
            ws_ping_interval=20,
            ws_ping_timeout=20,
        )
    finally:
        if tunnel is not None:
            print("\n  Closing Cloudflare tunnel…")
            tunnel.stop()


if __name__ == "__main__":
    main()
