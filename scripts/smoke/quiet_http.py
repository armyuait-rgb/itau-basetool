from __future__ import annotations

from contextlib import suppress
from http.server import BaseHTTPRequestHandler


class QuietOKHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        return

    def handle(self):
        with suppress(BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return super().handle()

    def _write_ok(self) -> None:
        self.send_response(200)
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            self.wfile.write(b"ok")

    def do_GET(self):
        self._write_ok()

    do_POST = do_GET
    do_HEAD = do_GET
