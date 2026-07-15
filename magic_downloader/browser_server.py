"""Local HTTP API for browser extension integration (browser capture).

Binds only to 127.0.0.1. The Chrome/Edge extension posts download requests here.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from magic_downloader import __version__
from magic_downloader.media.ffmpeg import has_ffmpeg

AddHandler = Callable[[dict[str, Any]], dict[str, Any]]
StatusHandler = Callable[[], dict[str, Any]]
ProbeHandler = Callable[[dict[str, Any]], dict[str, Any]]


class BrowserAPIServer:
    """Background localhost server for the browser extension."""

    def __init__(
        self,
        port: int,
        on_add: AddHandler,
        on_status: StatusHandler | None = None,
        token: str = "",
        on_probe: ProbeHandler | None = None,
    ) -> None:
        self.port = int(port)
        self.on_add = on_add
        self.on_status = on_status or (lambda: {})
        self.on_probe = on_probe
        self.token = token or ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.running = False
        self.last_error = ""

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self.running:
            return
        handler = self._make_handler()
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
            # Avoid long TIME_WAIT issues on rapid restart during dev
            self._httpd.daemon_threads = True
        except OSError as exc:
            self.last_error = str(exc)
            self.running = False
            raise
        self.running = True
        self.last_error = ""
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="browser-api", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        self._thread = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                # Keep console quiet; errors still useful for debug if needed
                return

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization, X-Magic-Token",
                )

            def _check_token(self) -> bool:
                if not server_ref.token:
                    return True
                auth = self.headers.get("Authorization", "")
                header_token = self.headers.get("X-Magic-Token", "")
                if auth.startswith("Bearer "):
                    header_token = auth[7:].strip() or header_token
                return header_token == server_ref.token

            def _json(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path in ("/api/ping", "/ping"):
                    self._json(
                        200,
                        {
                            "ok": True,
                            "name": "Magic Downloader",
                            "version": __version__,
                            "port": server_ref.port,
                            "ffmpeg": has_ffmpeg(),
                        },
                    )
                    return
                if path in ("/api/status", "/status"):
                    if not self._check_token():
                        self._json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    try:
                        self._json(200, {"ok": True, **server_ref.on_status()})
                    except Exception as exc:  # noqa: BLE001
                        self._json(500, {"ok": False, "error": str(exc)})
                    return
                self._json(404, {"ok": False, "error": "Not found"})

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path not in ("/api/add", "/add", "/api/probe", "/probe"):
                    self._json(404, {"ok": False, "error": "Not found"})
                    return
                if not self._check_token():
                    self._json(401, {"ok": False, "error": "Unauthorized"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                if not isinstance(data, dict):
                    self._json(400, {"ok": False, "error": "Expected JSON object"})
                    return
                url = str(data.get("url") or "").strip()
                if not url or urlparse(url).scheme not in ("http", "https"):
                    self._json(400, {"ok": False, "error": "Invalid url"})
                    return

                if path in ("/api/probe", "/probe"):
                    if server_ref.on_probe is None:
                        self._json(501, {"ok": False, "error": "Probe not supported"})
                        return
                    try:
                        result = server_ref.on_probe(data)
                        self._json(200, {"ok": True, **result})
                    except Exception as exc:  # noqa: BLE001
                        self._json(500, {"ok": False, "error": str(exc)})
                    return

                try:
                    result = server_ref.on_add(data)
                    self._json(200, {"ok": True, **result})
                except Exception as exc:  # noqa: BLE001
                    self._json(500, {"ok": False, "error": str(exc)})

        return Handler
