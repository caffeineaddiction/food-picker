"""Optional Cloudflare quick tunnel, started and owned by ``main.py``.

Running the tunnel ourselves removes the one manual step that actually bites in
practice: the display sits on ``localhost``, so the QR code encodes
``localhost``, which on a phone means *the phone itself*. If we start the tunnel
we know its public hostname, and the QR can point at it from the first frame.

**Opt-in only.** A tunnel publishes this machine's server to the public
internet, so nothing here runs unless the operator passes ``--tunnel``.

The URL is handed to the app through the ``PUBLIC_URL`` environment variable,
which :class:`server.rooms.Room` already reads. That keeps the tunnel entirely
outside the request path, and it survives ``--reload`` (uvicorn's child process
inherits the environment).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

CLOUDFLARED = "cloudflared"

#: cloudflared prints its assigned hostname to stderr, inside a banner box.
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

DEFAULT_TIMEOUT = 25.0


def is_available(binary: str = CLOUDFLARED) -> bool:
    """True when the tunnel binary is on PATH (or is a path that exists)."""

    return shutil.which(binary) is not None


@dataclass
class Tunnel:
    """A running ``cloudflared`` quick tunnel."""

    port: int
    timeout: float = DEFAULT_TIMEOUT
    binary: str = CLOUDFLARED
    """Overridable so tests can drive a stub instead of the real thing."""
    process: subprocess.Popen | None = None
    url: str | None = None
    error: str | None = None
    _log: list[str] = field(default_factory=list)
    _found: threading.Event = field(default_factory=threading.Event)

    def start(self) -> str | None:
        """Launch cloudflared and block until it reports a URL (or gives up)."""

        if not is_available(self.binary):
            self.error = (
                "cloudflared is not installed. Install it "
                "(brew install cloudflared) or paste a tunnel URL in the lobby."
            )
            return None

        self._found = threading.Event()
        try:
            self.process = subprocess.Popen(
                [
                    self.binary,
                    "tunnel",
                    "--url",
                    f"http://localhost:{self.port}",
                    "--no-autoupdate",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:  # pragma: no cover - depends on local install
            self.error = f"could not start cloudflared: {exc}"
            return None

        reader = threading.Thread(target=self._read_output, daemon=True)
        reader.start()
        self._found.wait(timeout=self.timeout)

        if self.url is None:
            self.error = self.error or (
                f"cloudflared did not report a URL within {self.timeout:.0f}s"
            )
            self.stop()
            return None
        return self.url

    def _read_output(self) -> None:
        """Scrape the tunnel hostname out of cloudflared's log stream."""

        stream = self.process.stdout if self.process else None
        if stream is None:  # pragma: no cover - defensive
            return
        for line in stream:
            self._log.append(line.rstrip())
            del self._log[:-40]  # keep the tail only, for diagnostics
            if self.url is None:
                match = URL_PATTERN.search(line)
                if match:
                    self.url = match.group(0)
                    self._found.set()
        # cloudflared exited: unblock anyone still waiting.
        self._found.set()

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def recent_log(self, lines: int = 8) -> list[str]:
        return self._log[-lines:]

    def stop(self) -> None:
        """Terminate the tunnel, escalating to a kill if it ignores us."""

        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.1)
        if process.poll() is None:  # pragma: no cover - stubborn child
            process.kill()

    def publish(self) -> None:
        """Expose the URL to the app (and to a ``--reload`` child process)."""

        if self.url:
            os.environ["PUBLIC_URL"] = self.url
