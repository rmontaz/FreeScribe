"""
mapper_server.py

A small web server, reachable only from this computer, that serves the bundled
OSCAR eForm Mapper and answers its "Find fields from PDF" requests.

Why a server: the mapper is a web page. Opened as a file it cannot talk to
FreeScribe, and browsers also refuse to run pdf.js's background worker there.
Served from http://127.0.0.1 both work.

Safety:

* it listens on 127.0.0.1 only, on a port the system picks, so other computers
  cannot reach it
* every API request must carry a random key created when FreeScribe starts the
  server. The page receives it in the address's ``#`` part, which browsers never
  send to servers or put in Referer headers, so other web pages cannot learn it
* requests whose Host or Origin is not this server are refused (protection
  against DNS rebinding and cross-site requests)
* only files inside the mapper's folder are served

No patient information is involved: the mapper works on blank forms.

This software is released under the AGPL-3.0 license.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional

from utils.log_config import logger

MAX_BODY = 60 * 1024 * 1024      # PDFs are sent base64-encoded
MAPPER_PAGE = "eform_mapper.html"
TOKEN_HEADER = "X-FreeScribe-Token"


class MapperServer:
    """
    :param static_dir: folder holding the mapper page and its ``vendor/`` files
    :param handlers: ``{"/api/name": function(payload) -> dict}``; a handler may raise
        an exception whose message is meant for the user (``user_errors``)
    :param status: function returning extra fields for ``/api/status``
    :param user_errors: exception classes whose message is shown to the user as is
    """

    def __init__(self, static_dir: str, handlers: Dict[str, Callable[[dict], dict]],
                 status: Optional[Callable[[], dict]] = None, user_errors: tuple = ()):
        self.static_dir = os.path.realpath(static_dir)
        self.handlers = dict(handlers)
        self.status = status
        self.user_errors = tuple(user_errors)
        self.token = secrets.token_urlsafe(32)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control
    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else 0

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def page_url(self) -> str:
        """Address that opens the mapper already connected to FreeScribe."""
        return f"{self.origin}/{MAPPER_PAGE}#fs={self.token}"

    def start(self) -> str:
        with self._lock:
            if self._httpd is None:
                server = self

                class Handler(_Handler):
                    owner = server

                self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                self._httpd.daemon_threads = True
                self._thread = threading.Thread(target=self._httpd.serve_forever, name="eform-mapper-server",
                                                daemon=True)
                self._thread.start()
                logger.info(f"eForm mapper server listening on {self.origin}")
        return self.page_url()

    def stop(self):
        with self._lock:
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
                self._httpd = None
                self._thread = None


class _Handler(BaseHTTPRequestHandler):
    owner: MapperServer = None
    server_version = "FreeScribeMapper/1"
    sys_version = ""

    def log_message(self, fmt, *args):
        logger.debug("eForm mapper server: " + fmt % args)

    # ------------------------------------------------------------- checks
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.owner.port
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True     # same-origin GET/POST from older browsers may omit it
        port = self.owner.port
        return origin.lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _token_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), self.owner.token)

    # ------------------------------------------------------------- replies
    def _send(self, status: int, body: bytes, content_type: str, cache: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: dict):
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    # ------------------------------------------------------------- methods
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Unexpected host.")
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/status":
            if not self._token_ok():
                return self._error(HTTPStatus.UNAUTHORIZED, "Open the mapper from FreeScribe.")
            info = {"ok": True, "app": "FreeScribe"}
            if self.owner.status:
                try:
                    info.update(self.owner.status() or {})
                except Exception as error:
                    logger.exception(f"eForm mapper status failed: {error}")
            return self._json(HTTPStatus.OK, info)
        if path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "Unknown request.")
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path in ("", "/"):
            path = "/" + MAPPER_PAGE
        self._static(path)

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Unexpected origin.")
        if not self._token_ok():
            return self._error(HTTPStatus.UNAUTHORIZED, "Open the mapper from FreeScribe.")
        path = urllib.parse.urlsplit(self.path).path
        handler = self.owner.handlers.get(path)
        if handler is None:
            return self._error(HTTPStatus.NOT_FOUND, "Unknown request.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > MAX_BODY else HTTPStatus.BAD_REQUEST,
                               "The request was empty or too large.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("expected an object")
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "The request could not be read.")
        try:
            result = handler(payload)
        except self.owner.user_errors as error:
            return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        except Exception as error:
            logger.exception(f"eForm mapper request {path} failed: {error}")
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                               "FreeScribe could not finish this request. The details are in FreeScribe's log.")
        self._json(HTTPStatus.OK, result)

    # ------------------------------------------------------------- files
    def _static(self, url_path: str):
        relative = urllib.parse.unquote(url_path).lstrip("/")
        target = os.path.realpath(os.path.join(self.owner.static_dir, relative))
        if not (target == self.owner.static_dir or target.startswith(self.owner.static_dir + os.sep)) \
                or not os.path.isfile(target):
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        with open(target, "rb") as handle:
            body = handle.read()
        self._send(HTTPStatus.OK, body, content_type, cache=target.endswith(".js"))
