"""
Tests for the local server that connects the bundled eForm Mapper to FreeScribe.

This software is released under the AGPL-3.0 license.
"""
import http.client
import json

import pytest

from services.mapper_server import MapperServer, TOKEN_HEADER


class Oops(Exception):
    pass


@pytest.fixture
def server(tmp_path):
    (tmp_path / "eform_mapper.html").write_text("<html>mapper</html>")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.js").write_text("var x = 1;")
    (tmp_path.parent / "secret.txt").write_text("do not serve")

    def detect(payload):
        if payload.get("fail") == "user":
            raise Oops("That PDF is scanned.")
        if payload.get("fail") == "crash":
            raise RuntimeError("boom")
        return {"echo": payload.get("value")}

    srv = MapperServer(str(tmp_path), {"/api/detect": detect}, status=lambda: {"model": "test"},
                       user_errors=(Oops,))
    srv.start()
    yield srv
    srv.stop()


def request(server, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    all_headers = {"Host": host or f"127.0.0.1:{server.port}"}
    all_headers.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=all_headers)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, payload, response


def auth(server, **extra):
    return {TOKEN_HEADER: server.token, **extra}


def test_serves_the_mapper_and_its_files(server):
    status, body, response = request(server, "GET", "/eform_mapper.html")
    assert status == 200 and body == b"<html>mapper</html>"
    assert response.getheader("Cache-Control") == "no-store"
    assert request(server, "GET", "/")[1] == b"<html>mapper</html>"
    status, body, response = request(server, "GET", "/vendor/lib.js")
    assert status == 200 and "javascript" in response.getheader("Content-Type")


def test_never_serves_files_outside_its_folder(server):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/vendor/../../secret.txt", "/missing.html"):
        assert request(server, "GET", path)[0] == 404


def test_page_url_carries_the_key_after_the_hash(server):
    url = server.page_url()
    assert url.startswith(f"http://127.0.0.1:{server.port}/eform_mapper.html#fs=")
    assert url.endswith(server.token)


def test_status_needs_the_key(server):
    assert request(server, "GET", "/api/status")[0] == 401
    status, body, _ = request(server, "GET", "/api/status", headers=auth(server))
    assert status == 200 and json.loads(body) == {"ok": True, "app": "FreeScribe", "model": "test"}


def test_requests_need_the_key_and_a_local_origin(server):
    assert request(server, "POST", "/api/detect", {"value": 1})[0] == 401
    assert request(server, "POST", "/api/detect", {"value": 1}, headers={TOKEN_HEADER: "wrong"})[0] == 401
    evil = auth(server, Origin="https://example.com")
    assert request(server, "POST", "/api/detect", {"value": 1}, headers=evil)[0] == 403
    status, body, _ = request(server, "POST", "/api/detect", {"value": 7},
                              headers=auth(server, Origin=server.origin))
    assert status == 200 and json.loads(body) == {"echo": 7}


def test_other_hostnames_are_refused(server):
    # A web page that points its own domain name at 127.0.0.1 (DNS rebinding)
    assert request(server, "GET", "/eform_mapper.html", host=f"evil.example:{server.port}")[0] == 403
    assert request(server, "POST", "/api/detect", {"value": 1}, headers=auth(server),
                   host=f"evil.example:{server.port}")[0] == 403
    assert request(server, "GET", "/eform_mapper.html", host=f"localhost:{server.port}")[0] == 200


def test_errors(server):
    status, body, _ = request(server, "POST", "/api/detect", {"fail": "user"}, headers=auth(server))
    assert status == 422 and json.loads(body)["error"] == "That PDF is scanned."
    status, body, _ = request(server, "POST", "/api/detect", {"fail": "crash"}, headers=auth(server))
    assert status == 500 and "log" in json.loads(body)["error"]
    assert request(server, "POST", "/api/unknown", {}, headers=auth(server))[0] == 404
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("POST", "/api/detect", body=b"not json",
                 headers={"Host": f"127.0.0.1:{server.port}", TOKEN_HEADER: server.token})
    assert conn.getresponse().status == 400


def test_listens_on_this_computer_only(server):
    assert server._httpd.server_address[0] == "127.0.0.1"


def test_restart_keeps_working(tmp_path):
    (tmp_path / "eform_mapper.html").write_text("x")
    srv = MapperServer(str(tmp_path), {})
    first = srv.start()
    assert srv.start() == first          # starting twice reuses the running server
    srv.stop()
    assert not srv.running
    srv.start()
    assert request(srv, "GET", "/eform_mapper.html")[0] == 200
    srv.stop()


def test_bundled_mapper_is_complete():
    """The copy FreeScribe ships has the connection, its libraries and every licence file."""
    import os
    import re
    root = os.path.join(os.path.dirname(__file__), "..", "..", "src", "FreeScribe.client", "assets", "eform_mapper")
    page = open(os.path.join(root, "eform_mapper.html"), encoding="utf-8").read()
    assert page.count("FreeScribe connection (added by FreeScribe)") >= 3      # styles, panel, script
    assert 'id="btnFsDetect"' in page and "fsCapturePdf(file.name" in page
    assert "standardFontDataUrl:'vendor/standard_fonts/'" in page
    for name in ("LICENSE.txt", "eForm_Generator_ATTRIBUTIONS.md", "vendor/pdf.min.js", "vendor/pdf.worker.min.js",
                 "vendor/jszip.min.js", "vendor/LICENSE-pdfjs.txt", "vendor/LICENSE-jszip.md",
                 "vendor/standard_fonts/FoxitDingbats.pfb", "vendor/standard_fonts/LICENSE_FOXIT",
                 "vendor/standard_fonts/LICENSE_LIBERATION"):
        assert os.path.isfile(os.path.join(root, name)), name
    notices = open(os.path.join(root, "..", "..", "markdown", "help", "third_party_notices.md"), encoding="utf-8").read()
    for credit in ("OSCAR eForm Mapper", "PDF.js", "JSZip", "PDFium Authors", "SIL Open Font License"):
        assert credit in notices
    # Every script the page loads from vendor/ is there
    for src in re.findall(r'<script src="(vendor/[^"]+)"', page):
        assert os.path.isfile(os.path.join(root, src)), src
