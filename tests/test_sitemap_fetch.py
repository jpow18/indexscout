"""The live sitemap fetch against a local HTTP server: gzip, redirects, size, and scope limits."""

import gzip
import http.server
import threading

import pytest

from indexscout import gsc

BODY = b"<urlset><url><loc>http://127.0.0.1/a</loc></url></urlset>"


@pytest.fixture
def site():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {
                "/s.xml": (200, {}, BODY),
                "/s.xml.gz": (200, {}, gzip.compress(BODY)),
                "/inside": (301, {"Location": "/s.xml"}, b""),
                "/outside": (302, {"Location": "http://evil.example.net/s.xml"}, b""),
                "/big": (200, {}, b"x" * 2048),
            }
            code, headers, body = routes.get(self.path, (404, {}, b""))
            self.send_response(code)
            for k, val in headers.items():
                self.send_header(k, val)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def test_fetch_plain_gzip_and_inside_redirect(site):
    assert gsc.fetch_sitemap_file(site, site + "s.xml") == BODY
    assert gsc.fetch_sitemap_file(site, site + "s.xml.gz") == BODY
    assert gsc.fetch_sitemap_file(site, site + "inside") == BODY


def test_fetch_refuses_outside_redirect_and_scope(site):
    with pytest.raises(gsc.GSCError, match="redirected outside"):
        gsc.fetch_sitemap_file(site, site + "outside")
    with pytest.raises(gsc.GSCError, match="outside the property"):
        gsc.fetch_sitemap_file(site, "http://evil.example.net/s.xml")
    with pytest.raises(gsc.GSCError, match="outside the property"):
        gsc.fetch_sitemap_file(site, "file:///etc/passwd")


def test_fetch_limits_and_errors(site, monkeypatch):
    with pytest.raises(gsc.GSCError, match="HTTP 404"):
        gsc.fetch_sitemap_file(site, site + "missing.xml")
    monkeypatch.setattr(gsc, "MAX_SITEMAP_DOWNLOAD", 1024)
    with pytest.raises(gsc.GSCError, match="larger than"):
        gsc.fetch_sitemap_file(site, site + "big")
