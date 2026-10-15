"""
extension_server.py

A small web server, reachable only from this computer, that lets the FreeScribe
browser extension control FreeScribe during a visit (start, pause and stop the
recording) and fetch the finished note to put into the EMR.

Safety:

* it listens on 127.0.0.1 only, so other computers cannot reach it
* every request must carry the pairing code shown in FreeScribe (the clinician
  copies it into the extension once). Web pages cannot read it: it lives in the
  extension's own storage, never in a page
* requests from web pages are refused: the Origin must be a browser extension
  (or absent), and the Host must be this computer (protection against DNS
  rebinding)
* nothing is stored; notes are only handed to the paired extension on request

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import json
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional

from utils.log_config import logger

DEFAULT_PORT = 52617
CODE_HEADER = "X-FreeScribe-Code"
MAX_BODY = 1024 * 1024
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")


def new_code() -> str:
    """A pairing code that is easy to copy: four groups of four letters and digits."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"          # no 0/O, 1/I/L
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


class ExtensionServer:
    """
    :param port: the port to listen on (127.0.0.1 only)
    :param code: a function returning the current pairing code
    :param get_routes: ``{"/api/name": function() -> dict}`` answered to GET
    :param post_routes: ``{"/api/name": function(payload) -> dict}`` answered to POST
    :param user_errors: exception classes whose message is shown in the extension as is
    """

    def __init__(self, port: int, code: Callable[[], str], get_routes: Dict[str, Callable[[], dict]],
                 post_routes: Dict[str, Callable[[dict], dict]], user_errors: tuple = ()):
        self.requested_port = int(port)
        self.code = code
        self.get_routes = dict(get_routes)
        self.post_routes = dict(post_routes)
        self.user_errors = tuple(user_errors)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.requested_port

    def start(self):
        with self._lock:
            if self._httpd is None:
                server = self

                class Handler(_Handler):
                    owner = server

                self._httpd = ThreadingHTTPServer(("127.0.0.1", self.requested_port), Handler)
                self._httpd.daemon_threads = True
                self._thread = threading.Thread(target=self._httpd.serve_forever, name="extension-server",
                                                daemon=True)
                self._thread.start()
                logger.info(f"Browser extension server listening on 127.0.0.1:{self.port}")

    def stop(self):
        with self._lock:
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
                self._httpd = None
                self._thread = None


class _Handler(BaseHTTPRequestHandler):
    owner: ExtensionServer = None
    server_version = "FreeScribeExtension/1"
    sys_version = ""

    def log_message(self, fmt, *args):
        logger.debug("Browser extension server: " + fmt % args)

    # ------------------------------------------------------------- checks
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.owner.port
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin(self) -> Optional[str]:
        return self.headers.get("Origin")

    def _origin_ok(self) -> bool:
        origin = self._origin()
        return origin is None or origin.lower().startswith(EXTENSION_ORIGINS)

    def _code_ok(self) -> bool:
        expected = normalize_code(self.owner.code())
        given = normalize_code(self.headers.get(CODE_HEADER, ""))
        return bool(expected) and secrets.compare_digest(given, expected)

    def _check(self) -> bool:
        if not self._host_ok() or not self._origin_ok():
            self._error(HTTPStatus.FORBIDDEN, "Only the FreeScribe browser extension can connect.")
            return False
        if not self._code_ok():
            self._error(HTTPStatus.UNAUTHORIZED, "The pairing code does not match. Copy it again from FreeScribe.")
            return False
        return True

    # ------------------------------------------------------------- replies
    def _cors(self):
        origin = self._origin()
        if origin and origin.lower().startswith(EXTENSION_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {CODE_HEADER}")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Vary", "Origin")

    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _run(self, path, function, *args):
        try:
            result = function(*args)
        except self.owner.user_errors as error:
            return self._error(HTTPStatus.CONFLICT, str(error))
        except Exception as error:  # noqa: BLE001
            logger.exception(f"Browser extension request {path} failed: {error}")
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "FreeScribe could not do that. See FreeScribe's log.")
        self._json(HTTPStatus.OK, result if isinstance(result, dict) else {"ok": True})

    # ------------------------------------------------------------- methods
    def do_OPTIONS(self):
        if not self._host_ok() or not self._origin_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Only the FreeScribe browser extension can connect.")
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._check():
            return
        path = urllib.parse.urlsplit(self.path).path
        function = self.owner.get_routes.get(path)
        if function is None:
            return self._error(HTTPStatus.NOT_FOUND, "Unknown request.")
        self._run(path, function)

    def do_POST(self):
        if not self._check():
            return
        path = urllib.parse.urlsplit(self.path).path
        function = self.owner.post_routes.get(path)
        if function is None:
            return self._error(HTTPStatus.NOT_FOUND, "Unknown request.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._error(HTTPStatus.BAD_REQUEST, "The request was too large.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            if not isinstance(payload, dict):
                raise ValueError("expected an object")
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "The request could not be read.")
        self._run(path, function, payload)
