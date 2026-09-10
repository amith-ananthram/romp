#!/usr/bin/env python3
"""The /remote/<host>/file relay — previews for a FEDERATED session's mentioned paths.

A remote session's chat mentions paths on the REMOTE machine's disk, but a preview <img> can only
dial the origin that served the page — its /file hit the local kernel, read the local disk, 404'd,
and every mentioned plot/screenshot on a federated session silently hid itself (2026-07-31). The
kernel now relays: GET/HEAD /remote/<host>/file forwards the one request to the attached host's
kernel through the same ssh -L tunnel the /remote/<host>/ws splice uses, after the normal local
auth gate, rewriting the remote kernel's own token into the forwarded query so the per-host trust
boundary is unchanged (THAT kernel still runs its allowlist, size cap and path resolution).

These tests run the real Handler against a fake "remote kernel" (an HTTP server that answers
/file and records what it was asked). Synthetic only: host name `gpu1`, invented tokens, no
session state touched.
"""
import os
import socket
import threading
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Mirror tests/test_kernel_ws_auth.py's load order. The token env keeps _load_token() away from
# the real state dir; NO_OPEN keeps the import from launching a browser.
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

REMOTE_TOKEN = "remote-token-DO-NOT-USE"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png-bytes"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"   # a minimal synthetic PDF
PY_BYTES = b"print('hello from the remote box')\n"
# the download fixture: NUL-ridden, off every view allowlist, and BIGGER than one relay stream chunk,
# so the pass-through provably crosses a chunk boundary intact
BIN_BYTES = bytes(range(256)) * ((km._DOWNLOAD_CHUNK // 256) + 60)


class _FakeRemoteFileHandler(BaseHTTPRequestHandler):
    """The attached host's kernel behind the ssh -L port, /file route only: serves PNG_BYTES for
    path=/tmp/plot.png and BIN_BYTES for path=/tmp/data.bin&download=1 (recording the request
    lines), 404s anything else."""
    requests = []               # class-level: the recorded request lines
    ctype = "image/png"         # what this remote CLAIMS the bytes are (a hostile one lies)
    dl_ctype = "application/octet-stream"          # …and the download-side claims (a hostile one lies)
    dl_disp = 'attachment; filename="data.bin"'
    dl_truncate = False         # short body then a clean close, as _file_download sends when the file shrank

    def _serve(self, head):
        _FakeRemoteFileHandler.requests.append(self.path)
        if "download=1" in self.path and "data.bin" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", _FakeRemoteFileHandler.dl_ctype)
            self.send_header("Content-Disposition", _FakeRemoteFileHandler.dl_disp)
            self.send_header("Content-Length", str(len(BIN_BYTES)))
            self.end_headers()
            if not head:
                if _FakeRemoteFileHandler.dl_truncate:
                    # the remote kernel's own truncation path: a short body, then a clean close
                    self.wfile.write(BIN_BYTES[: len(BIN_BYTES) // 2])
                    self.close_connection = True
                else:
                    self.wfile.write(BIN_BYTES)
            return
        if "app.py" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", _FakeRemoteFileHandler.ctype)
            self.send_header("Content-Length", str(len(PY_BYTES)))
            self.end_headers()
            if not head:
                self.wfile.write(PY_BYTES)
            return
        if "big.pdf" in self.path:
            # the remote's own size cap: its _file_preview's prose verdict, text/plain, no disposition
            body = b"too large to show: /tmp/big.pdf (95.4 MB, limit 47.7 MB)"
            self.send_response(413)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if "paper.pdf" in self.path:
            # a (hostile) remote's own disposition — an instruction to this browser, which the relay must
            # never echo: ours is derived from the requested name
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="evil.pdf"')
            self.send_header("Content-Length", str(len(PDF_BYTES)))
            self.end_headers()
            if not head:
                self.wfile.write(PDF_BYTES)
            return
        if "plot.png" not in self.path:
            body = b"not found: /tmp/gone" if "download=1" in self.path else b""
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", _FakeRemoteFileHandler.ctype)
        self.send_header("Content-Length", str(len(PNG_BYTES)))
        self.end_headers()
        if not head:
            self.wfile.write(PNG_BYTES)

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def log_message(self, *a):  # keep the test output clean
        pass


class _RecorderSink:
    def __init__(self, sink):
        self.sink = sink

    def write(self, b):
        self.sink.append(bytes(b))


class _RelayRecorder:
    """Just enough Handler surface for a direct _relay_download call: records status, headers and
    every wfile.write, and starts with close_connection False — so a test can see the one thing an
    over-HTTP client cannot: whether the relay itself decided to close the connection."""

    def __init__(self):
        self.writes, self.headers, self.status = [], {}, None
        self.close_connection = False
        self.wfile = _RecorderSink(self.writes)

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    _send = km.Handler._send      # the error paths route through the real _send, captured by the fakes


class RemoteFileRelay(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRemoteFileHandler)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        _FakeRemoteFileHandler.requests = []
        _FakeRemoteFileHandler.ctype = "image/png"
        _FakeRemoteFileHandler.dl_ctype = "application/octet-stream"
        _FakeRemoteFileHandler.dl_disp = 'attachment; filename="data.bin"'
        _FakeRemoteFileHandler.dl_truncate = False
        self._saved_remotes = dict(km._remotes)

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved_remotes)
        for s in (self.srv, self.fake):
            s.shutdown()
            s.server_close()

    def _register(self, host, local_port, token=REMOTE_TOKEN):
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "kernel_port": 29855, "local_port": local_port,
                                 "token": token, "status": "up"}

    def _get(self, path, token=True, method="GET"):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method)
        if token:
            req.add_header("X-Romp-Token", km.TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_relays_the_bytes_and_rewrites_the_token(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get(
            "/remote/gpu1/file?path=%2Ftmp%2Fplot.png&sid=11111111-2222-3333-4444-555555555555"
            "&token=whatever-the-browser-sent")
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG_BYTES, "the remote's bytes must pass through unmodified")
        self.assertEqual(headers.get("Content-Type"), "image/png")
        # the forwarded request: path /file, the REMOTE's token (not what the browser sent, and
        # never the local serve token), the sid + path intact
        (req,) = _FakeRemoteFileHandler.requests
        self.assertTrue(req.startswith("/file?"), req)
        self.assertIn("token=" + REMOTE_TOKEN, req)
        self.assertNotIn("whatever-the-browser-sent", req)
        self.assertNotIn(km.TOKEN, req)
        self.assertIn("path=%2Ftmp%2Fplot.png", req)
        self.assertIn("sid=11111111-2222-3333-4444-555555555555", req)

    def test_a_remote_pdf_is_served_inline_with_its_own_name_derived_here_never_the_remotes(self):
        # a remote session's PDF opens in its own browser tab too (2026-09-06): the tab's title and a Save's
        # name ride Content-Disposition, which the local route sends — so the relay must as well, from the
        # REQUESTED basename, on GET and on the HEAD probe. The remote's header is discarded like its
        # Content-Type: an instruction to this browser, not a fact about its disk.
        self._register("gpu1", self.fake.server_address[1])
        for method in ("GET", "HEAD"):
            status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fpaper.pdf", method=method)
            self.assertEqual(status, 200, method)
            self.assertEqual(headers.get("Content-Type"), "application/pdf")
            self.assertEqual(headers.get("Content-Disposition"), 'inline; filename="paper.pdf"', method)
            self.assertNotIn("evil", headers.get("Content-Disposition") or "")
            self.assertEqual(body, PDF_BYTES if method == "GET" else b"")
        # a remote 404 for a .pdf name carries no disposition — there is no file to name
        status, _, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.pdf")
        self.assertEqual(status, 404)
        self.assertIsNone(headers.get("Content-Disposition"))
        # …and an image never gets one, exactly as locally
        status, _, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Disposition"))

    def test_a_remote_error_verdict_is_prose_and_an_oversize_pdf_tab_gets_the_relays_own_way_out(self):
        # a PDF opens in its own TAB now (2026-09-06): the remote's 413/404 prose used to come back labelled
        # with OUR media mime, which an <img> merely failed on but a tab shows as a corrupt-PDF error
        # (skeptic find). Every non-success verdict is text/plain; a navigation to an oversize PDF gets the
        # same way-out page the local route serves, linking THIS relay's download half.
        self._register("gpu1", self.fake.server_address[1])
        sid = "11111111-2222-3333-4444-555555555555"
        qp = "/remote/gpu1/file?path=%2Ftmp%2Fbig.pdf&sid=" + sid
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, qp),
                                     headers={"X-Romp-Token": km.TOKEN, "Sec-Fetch-Dest": "document"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, body, headers = r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            status, body, headers = e.code, e.read(), dict(e.headers)
        self.assertEqual(status, 413)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/html"), headers.get("Content-Type"))
        page = body.decode("utf-8")
        self.assertIn("too large to show: /tmp/big.pdf", page)
        dq = urllib.parse.urlencode({"path": "/tmp/big.pdf", "download": "1", "sid": sid})
        self.assertIn('href="' + km._html_esc("/remote/gpu1/file?" + dq) + '"', page, "the relay's download half")
        # the same without the navigation marker: the remote's prose, labelled as prose
        status, body, headers = self._get(qp)
        self.assertEqual(status, 413)
        self.assertEqual(headers.get("Content-Type"), "text/plain")
        self.assertTrue(body.startswith(b"too large to show:"), body[:40])
        status, body, headers = self._get(qp, method="HEAD")
        self.assertEqual((status, body, headers.get("Content-Type")), (413, b"", "text/plain"))
        # a remote 404 for a .pdf name: prose too, never application/pdf
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.pdf")
        self.assertEqual((status, headers.get("Content-Type")), (404, "text/plain"))

    def test_head_relays_the_verdict_without_a_body(self):
        # the PDF chip's existence probe: headers only, the remote's real length
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Length"), str(len(PNG_BYTES)))
        (req,) = _FakeRemoteFileHandler.requests
        self.assertTrue(req.startswith("/file?"), req)

    def test_remote_404_passes_through(self):
        # a deleted/hallucinated path: the REMOTE's 404 reaches the <img> so it hides itself
        self._register("gpu1", self.fake.server_address[1])
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.png")
        self.assertEqual(status, 404)

    def test_text_relays_with_a_locally_derived_type_the_remote_cannot_override(self):
        """The text half of /file relays too — this gate predated it, so a remote session's
        .py/.md 404'd here while the LOCAL route served the same file (the viewer just failed
        on every federated text file). The defense is unchanged and now covers text: the type
        is OURS — a lying remote's text/html arrives as inert text/plain + nosniff. (.html is
        on _TEXT_EXT, so it too relays as text/plain now — same inertness, matching the local
        route; the never-asked decline for off-every-allowlist extensions is pinned by the
        .bin test below.)"""
        self._register("gpu1", self.fake.server_address[1])
        _FakeRemoteFileHandler.ctype = "text/html"
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fapp.py")
        self.assertEqual(status, 200)
        self.assertEqual(body, PY_BYTES)
        self.assertEqual(headers.get("Content-Type"), "text/plain; charset=utf-8")
        self.assertNotIn("html", (headers.get("Content-Type") or "").lower())

    def test_unknown_host_404s(self):
        status, _, _ = self._get("/remote/nosuch/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 404)
        self.assertEqual(_FakeRemoteFileHandler.requests, [])

    def test_unauthorized_403s_before_any_dial(self):
        self._register("gpu1", self.fake.server_address[1])
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", token=False)
        self.assertEqual(status, 403, "the local auth gate must run before the relay")
        self.assertEqual(_FakeRemoteFileHandler.requests, [],
                         "an unauthorized request must never touch the tunnel")

    # ── the download half (the user 2026-08-09): /remote/<host>/file?download=1 relays ANY file the
    # remote will serve, streamed through, with the attachment headers derived on THIS side ──

    def test_download_relays_any_extension_and_the_disposition_survives(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get(
            "/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1&token=whatever-the-browser-sent")
        self.assertEqual(status, 200)
        self.assertEqual(body, BIN_BYTES,
                         "bigger than one stream chunk, so the pass-through crossed a boundary intact")
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="data.bin"')
        self.assertEqual(headers.get("Content-Type"), "application/octet-stream")
        self.assertEqual(headers.get("Content-Length"), str(len(BIN_BYTES)))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        # forwarded intact: download=1 rides through, the REMOTE's token replaces the browser's
        (req,) = _FakeRemoteFileHandler.requests
        self.assertIn("download=1", req)
        self.assertIn("token=" + REMOTE_TOKEN, req)
        self.assertNotIn("whatever-the-browser-sent", req)

    def test_download_headers_are_derived_locally_never_mirrored_from_the_remote(self):
        # the lying-remote rule, download edition: an attached host serves its own bytes but never
        # chooses how this browser handles them — a hostile Content-Type/Disposition is discarded
        self._register("gpu1", self.fake.server_address[1])
        _FakeRemoteFileHandler.dl_ctype = "text/html"
        _FakeRemoteFileHandler.dl_disp = "inline"
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1")
        self.assertEqual(status, 200)
        self.assertEqual(body, BIN_BYTES)
        self.assertEqual(headers.get("Content-Type"), "application/octet-stream")
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="data.bin"')

    def test_the_view_relay_still_declines_that_same_extension(self):
        # without download=1 nothing changed: a .bin is off _PREVIEW_MIME, 404'd HERE, remote unasked
        self._register("gpu1", self.fake.server_address[1])
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin")
        self.assertEqual(status, 404)
        self.assertEqual(_FakeRemoteFileHandler.requests, [])

    def test_a_remote_download_404_passes_through(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.bin&download=1")
        self.assertEqual(status, 404)
        self.assertIn(b"not found", body, "the remote's own verdict, which names the path IT resolved")

    def test_head_download_relays_the_attachment_without_a_body(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1",
                                          method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="data.bin"')
        self.assertEqual(headers.get("Content-Length"), str(len(BIN_BYTES)))

    def test_a_remote_that_truncates_cleanly_still_closes_this_connection_short(self):
        """The remote's own _file_download truncation path sends SHORT and then closes cleanly — on
        this side http.client's read() answers that with b'' and NO exception, so only counting the
        copied bytes against the Content-Length already forwarded can spot the broken promise.
        Without the close, this keep-alive (HTTP/1.1) connection leaves the browser waiting on
        bytes that will never come — a hung download instead of a visibly failed one. Pinned by a
        direct _relay_download call: over HTTP a test client's own Connection: close (urllib sends
        it always) would close the connection for the wrong reason and hide exactly this bug."""
        _FakeRemoteFileHandler.dl_truncate = True
        rec = _RelayRecorder()
        km.Handler._relay_download(rec, "gpu1", self.fake.server_address[1], REMOTE_TOKEN,
                                   {"path": ["/tmp/data.bin"], "download": ["1"]})
        self.assertEqual(rec.status, 200)
        self.assertEqual(rec.headers.get("Content-Length"), str(len(BIN_BYTES)),
                         "the remote's promised length went out before the truncation")
        self.assertEqual(b"".join(rec.writes), BIN_BYTES[: len(BIN_BYTES) // 2],
                         "the bytes that did arrive still pass through")
        self.assertTrue(rec.close_connection,
                        "short of the forwarded Content-Length must CLOSE, or the browser hangs")

    def test_a_dead_tunnel_502s_the_download_too(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        self._register("gpu1", dead_port)
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1")
        self.assertEqual(status, 502)

    def test_dead_tunnel_502s(self):
        # a registered host whose forwarded port has no listener (the ssh died mid-flight)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        self._register("gpu1", dead_port)
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 502)


if __name__ == "__main__":
    unittest.main()
