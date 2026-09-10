#!/usr/bin/env python3
"""CORS delivery for the kernel's HTTP API — the VS Code webview fetch path.

A VS Code webview runs on a synthetic origin (vscode-webview://…), so every direct
fetch to the kernel is cross-origin. The auth gate (_origin_ok / _authorize) already
ALLOWS those requests, but without an echoed Access-Control-Allow-Origin the browser
withholds the response after it arrives — the strip's net popover read "Failed to
fetch" with the kernel up and curl working (2026-07-14). These tests pin the
delivery rules:

  - an ALLOWED origin gets its exact origin echoed (never *), plus Vary: Origin
  - a DENIED cross-site origin gets 403 and NO echo (nothing readable, ClawJacked-safe)
  - no Origin (curl / native clients) → no echo (CORS is a browser concern)
  - the auth-EXEMPT routes (/version — the gear's version row) echo too
  - a valid token authorizes a foreign origin (federated dashboard) → echo follows auth
  - OPTIONS preflight approves allowed origins for the JSON POSTs, 403s the rest

Synthetic only — constant-data routes (/models, /version); no session state touched.
"""
import http.client
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Mirror tests/test_kernel_ws_auth.py's load order. The token env keeps _load_token()
# away from the real state dir; NO_OPEN keeps the import from launching a browser.
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

WEBVIEW = "vscode-webview://1a2b3c4d-testhost"


class CorsDelivery(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _req(self, method, path, origin=None, headers=None, token=True):
        # token=True sends X-Romp-Token (the serve token is required on every gated route,
        # loopback included); the deny-path tests pass token=False so the ORIGIN gate is
        # what decides (a valid token would authorize any origin and defeat the point).
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            h = dict(headers or {})
            if token:
                h.setdefault("X-Romp-Token", km.TOKEN)
            if origin is not None:
                h["Origin"] = origin
            conn.request(method, path, headers=h)
            r = conn.getresponse()
            r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}
        finally:
            conn.close()

    def test_webview_origin_echoed(self):
        status, h = self._req("GET", "/models", origin=WEBVIEW)
        self.assertEqual(status, 200)
        self.assertEqual(h.get("access-control-allow-origin"), WEBVIEW,
                         "the exact allowed origin must echo (never *)")
        self.assertIn("origin", (h.get("vary") or "").lower())

    def test_foreign_origin_gets_no_echo(self):
        status, h = self._req("GET", "/models", origin="http://evil.example", token=False)
        self.assertEqual(status, 403, "the auth gate still rejects cross-site origins")
        self.assertIsNone(h.get("access-control-allow-origin"),
                          "a denied origin must never be echoed — the 403 stays unreadable")

    def test_no_origin_no_echo(self):
        status, h = self._req("GET", "/models")
        self.assertEqual(status, 200)
        self.assertIsNone(h.get("access-control-allow-origin"),
                          "no Origin → no CORS header (non-browser clients don't need one)")

    def test_auth_exempt_version_echoes(self):
        # /version answers BEFORE _authorize (auth-exempt) — the gear's version row
        # fetches it from the webview, so the echo must cover the exempt path too.
        status, h = self._req("GET", "/version", origin=WEBVIEW)
        self.assertEqual(status, 200)
        self.assertEqual(h.get("access-control-allow-origin"), WEBVIEW)

    def test_token_authorizes_foreign_origin_and_echoes(self):
        # Federated dashboard: foreign origin + this kernel's token → authorized, echoed.
        status, h = self._req("GET", "/models?token=" + km.TOKEN, origin="http://localhost:9999")
        self.assertEqual(status, 200)
        self.assertEqual(h.get("access-control-allow-origin"), "http://localhost:9999",
                         "the echo follows the AUTH decision, not just the origin list")

    def test_preflight_approves_allowed_origin(self):
        status, h = self._req("OPTIONS", "/tunnels", origin=WEBVIEW,
                              headers={"Access-Control-Request-Method": "POST",
                                       "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(status, 204)
        self.assertEqual(h.get("access-control-allow-origin"), WEBVIEW)
        self.assertIn("POST", h.get("access-control-allow-methods") or "")
        self.assertIn("content-type", (h.get("access-control-allow-headers") or "").lower())

    def test_preflight_rejects_foreign_origin(self):
        # Credential-less: the Origin gate decides. (WITH a valid token a foreign origin
        # is approved on purpose — that's the federated dashboard's preflight.)
        status, h = self._req("OPTIONS", "/tunnels", origin="http://evil.example",
                              headers={"Access-Control-Request-Method": "POST"}, token=False)
        self.assertEqual(status, 403)
        self.assertIsNone(h.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
