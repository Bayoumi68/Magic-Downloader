"""Single-instance guard via a local control socket.

Behaviour: **last one takes place** — when the app is launched while another
instance is already running, the new instance tells the old one to quit, waits
for it to release the port, and takes over. The socket also stays open to accept
future 'quit'/'show' commands.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 47923  # dedicated single-instance / control port


class SingleInstance:
    def __init__(self, port: int = CONTROL_PORT) -> None:
        self.port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._on_quit: Callable[[], None] | None = None
        self._on_show: Callable[[], None] | None = None
        self._running = False

    def _try_bind(self) -> socket.socket | None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # No SO_REUSEADDR: a second bind must FAIL so we can detect the
            # existing instance.
            s.bind((CONTROL_HOST, self.port))
            s.listen(8)
            return s
        except OSError:
            s.close()
            return None

    def _send_command(self, cmd: str) -> bool:
        try:
            with socket.create_connection((CONTROL_HOST, self.port), timeout=2) as c:
                c.sendall((cmd + "\n").encode("utf-8"))
                c.settimeout(2)
                try:
                    c.recv(16)
                except OSError:
                    pass
            return True
        except OSError:
            return False

    def acquire(self, takeover: bool = True, wait: float = 8.0) -> bool:
        """Become the single instance. Returns True if we now own it.

        If another instance exists and ``takeover`` is True, tell it to quit and
        wait up to ``wait`` seconds for the port. If it won't release, we run
        anyway (best effort) so the app always opens.
        """
        s = self._try_bind()
        if s is not None:
            self._sock = s
            return True

        if not takeover:
            self._send_command("show")
            return False

        # 'last one takes place': ask the running instance to exit, then grab it.
        self._send_command("quit")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            s = self._try_bind()
            if s is not None:
                self._sock = s
                return True
            time.sleep(0.2)
        return True  # couldn't take the port (old instance hung) — run anyway

    def start_listener(self, on_quit: Callable[[], None], on_show: Callable[[], None] | None = None) -> None:
        self._on_quit = on_quit
        self._on_show = on_show
        if self._sock is None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="single-instance", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running and self._sock is not None:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            data = ""
            try:
                conn.settimeout(2)
                data = conn.recv(64).decode("utf-8", "ignore").strip().lower()
                try:
                    conn.sendall(b"ok")
                except OSError:
                    pass
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            if "quit" in data and self._on_quit:
                self._on_quit()
            elif "show" in data and self._on_show:
                self._on_show()

    def close(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
