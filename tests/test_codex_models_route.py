#!/usr/bin/env python3
"""GET /models' Codex section says WHY it is empty. A Codex session's model picker opened on a blank menu
with no word of what was wrong: the route carried `codex: {models: []}` whenever the backend's
model_catalog() answered [] (the app-server client in its retry backoff, a model_list that raised, an
empty page) or raised (the handler swallowed the exception into the same empty list), and it carried the
same empty list on a dashboard where the Codex consult is gated off (no live Codex session, Codex neither
the default backend nor the judge engine) or where the backend module failed to load. The section now
carries `error`: null beside a non-empty list, else one sentence naming the reason, read from the
backend after an empty answer (model_catalog_error) or built from the raise (out of either call), and the
closed gate and an absent backend name themselves so an empty list is never read as the app-server's
answer. A raising catalog is logged once per distinct reason, and again when the same fault recurs after
the catalog answered: every picker open and every models frame re-reads the route.
The handler runs in-process on a loopback ThreadingHTTPServer (the test_kernel_cors idiom) over a FAKE
backend holding the slice the handler reads, and once over the REAL clientless backend so the sentence the
route serves is the backend's own. Synthetic fixtures only.
"""
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state, and a kernel module that
# can reach a live manager port restarts the live kernel).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_MANAGER_PORT"] = "1"             # a dead port, never an inherited live one
os.environ["ROMP_MODEL_CATALOG"] = "off"          # never the Models API from a test
km = load_source("romp_kernel_codex_models", os.path.join(BIN, "romp-kernel"))
cb = load_source("romp_codex_backend_codex_models",
                      os.path.join(ROOT, "kernel", "codex_backend.py"))

SID = "11111111-2222-4333-8444-555555555555"
MODELS = [{"value": "gpt-5-test", "label": "GPT-5 Test"}]


class FakeCodex:
    """The slice of CodexBackend the /models handler reads: the gate's row walk, the catalog, its reason."""

    def __init__(self, models=None, error=None, live=None, raise_catalog=None, raise_error=None):
        self.models, self.error, self.live = list(models or []), error, dict(live or {})
        self.raise_catalog, self.raise_error = raise_catalog, raise_error
        self.catalog_calls = 0

    def live_sessions(self):
        return self.live

    def model_catalog(self):
        self.catalog_calls += 1
        if self.raise_catalog:
            raise self.raise_catalog
        return list(self.models)

    def model_catalog_error(self):
        if self.raise_error:
            raise self.raise_error
        return self.error


class ModelsRoute(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self._saved = km._codex_backend
        km._codex_backend = False        # every test picks its backend; none builds the real one by accident
        self._saved_fault = km._codex_catalog_fault[0]
        km._codex_catalog_fault[0] = None   # the once-per-reason latch starts clear whatever ran before

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        km._codex_backend = self._saved
        km._codex_catalog_fault[0] = self._saved_fault
        try:
            os.unlink(km.jd.STATE / "default-backend")
        except OSError:
            pass

    def _models(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/models", headers={"X-Romp-Token": km.TOKEN})
            r = conn.getresponse()
            self.assertEqual(r.status, 200)
            return json.loads(r.read())
        finally:
            conn.close()

    def _codex(self):
        d = self._models()
        self.assertIn("error", d["codex"], "the section always carries the field; null when the list is served")
        return d["codex"]

    def test_a_live_codex_session_gets_the_backends_list_and_no_error(self):
        km._codex_backend = FakeCodex(models=MODELS, live={SID: {"backend": "codex"}})
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]), (MODELS, None))
        self.assertEqual([e["value"] for e in cx["efforts"]], ["low", "medium", "high", "xhigh"])

    def test_an_empty_list_carries_the_backends_reason(self):
        km._codex_backend = FakeCodex(models=[], error="model_list failed: app-server not ready",
                                      live={SID: {"backend": "codex"}})
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]), ([], "model_list failed: app-server not ready"))

    def test_an_empty_list_with_no_recorded_reason_still_says_so(self):
        km._codex_backend = FakeCodex(models=[], error=None, live={SID: {"backend": "codex"}})
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]), ([], "the Codex app-server sent no model list"))

    def test_a_raising_catalog_is_reported_not_swallowed(self):
        km._codex_backend = FakeCodex(raise_catalog=RuntimeError("pump died"), live={SID: {"backend": "codex"}})
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]), ([], "model catalog: pump died"))

    def test_a_raise_out_of_the_reason_read_is_reported_too(self):
        # An empty list sends the handler to model_catalog_error(); a raise there is the same kind of
        # fault as a raise out of model_catalog() and takes the same sentence, never a swallowed None.
        km._codex_backend = FakeCodex(models=[], raise_error=RuntimeError("reason lost"),
                                      live={SID: {"backend": "codex"}})
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]), ([], "model catalog: reason lost"))

    def test_a_raising_catalog_is_logged_once_per_distinct_reason(self):
        # The route is re-read on every picker open and every models frame, so a line per read repeats for
        # as long as the fault lasts: one line per distinct reason, and the SAME fault recurring after the
        # catalog answered in between is a new line (the answer clears the latch). A raise out of the
        # reason read is logged the same way.
        fake = FakeCodex(raise_catalog=RuntimeError("pump died"), live={SID: {"backend": "codex"}})
        km._codex_backend = fake
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self._codex()
            self._codex()
            fake.raise_catalog = RuntimeError("app-server not ready")
            self._codex()
            self._codex()
            fake.raise_catalog = None
            fake.models = list(MODELS)
            self.assertEqual(self._codex()["error"], None)
            fake.raise_catalog = RuntimeError("app-server not ready")
            self._codex()
            fake.raise_catalog, fake.models = None, []
            fake.raise_error = RuntimeError("reason lost")
            self._codex()
            self._codex()
        lines = [l for l in err.getvalue().splitlines() if "model catalog" in l]
        self.assertEqual(lines, ["codex-backend: model catalog: pump died",
                                 "codex-backend: model catalog: app-server not ready",
                                 "codex-backend: model catalog: app-server not ready",
                                 "codex-backend: model catalog: reason lost"])

    def test_the_closed_gate_consults_nothing_and_names_itself(self):
        # No live Codex session, Codex neither the default backend nor the judge engine: the backend is not
        # asked (asking would spawn the app-server on every dashboard load), and the reason says so rather
        # than leaving an empty list that reads as the app-server's answer.
        fake = FakeCodex(models=MODELS)
        km._codex_backend = fake
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"], fake.catalog_calls),
                         ([], "no live Codex session; the list is read once one runs", 0))

    def test_the_codex_default_backend_opens_the_gate_without_a_live_session(self):
        fake = FakeCodex(models=MODELS)
        km._codex_backend = fake
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "default-backend").write_text("codex\n")
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"], fake.catalog_calls), (MODELS, None, 1))

    def test_an_unavailable_backend_names_itself(self):
        km._codex_backend = False        # _codex(): the module failed to load, so every caller gets None
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]), ([], "the Codex backend is unavailable (see the kernel log)"))

    def test_the_real_backends_reason_rides_the_route(self):
        # The real backend, clientless: the client factory fails as a missing `codex login` does, so
        # model_catalog() answers [] with the client's failure as the reason, and the route serves that
        # sentence, not the fallback. The gate is opened by the machine default rather than a live row.
        be = cb.CodexBackend(tempfile.mkdtemp(), log=lambda m: None,
                             client_factory=lambda: (_ for _ in ()).throw(RuntimeError("codex login missing")))
        km._codex_backend = be
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "default-backend").write_text("codex\n")
        cx = self._codex()
        self.assertEqual((cx["models"], cx["error"]),
                         ([], "the Codex app-server client is unavailable: codex login missing"))


if __name__ == "__main__":
    unittest.main()
