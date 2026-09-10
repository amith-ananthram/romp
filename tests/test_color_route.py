#!/usr/bin/env python3
"""POST /color (the user 2026-08-23, manager/worker workflow): the setSessionColor WS op as a
one-shot token-gated route, the exact sibling of /rename — a manager keeps its whole worker group
one identity color without WS surgery. Only a swatch of a known palette is accepted (its own
palette supplies the fg word), and a recolor is a names-registry write, so a dormant session
recolors by sid just like /rename renames one. Drives the REAL Handler over HTTP (the
test_new_route_prefs.py pattern). Synthetic only."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_cr", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "22222222-3333-4444-5555-666666666666"


class ColorRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.names = Path(self.tmp) / "names"
        self.names.mkdir()
        self._saved = (km.NAMES, km.jd.STATE, km._tmux_sessions, km._live_names,
                       km._mark_views_dirty)
        km.NAMES = self.names
        km.jd.STATE = Path(self.tmp) / "state"
        km._pal_cache.update({"name": km.pal.DEFAULT, "mt": None})   # drop the mtime cache between sandboxes
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {"web": SID}
        self.dirty = []                                       # the route must poke the views push
        km._mark_views_dirty = lambda: self.dirty.append(1)

    def tearDown(self):
        (km.NAMES, km.jd.STATE, km._tmux_sessions, km._live_names,
         km._mark_views_dirty) = self._saved
        km._pal_cache.update({"name": km.pal.DEFAULT, "mt": None})

    def _post(self, body):
        # km.TOKEN, not os.environ: pytest imports every collected module before any test runs, and
        # test_kernel.py assigns ROMP_SERVE_TOKEN at import. When this module was the FIRST collected
        # module to set the variable (a two-file or small-subset run), its kernel captured the default
        # here and test_kernel.py's later write changed what the env held at request time, so the
        # request was refused with a 403 (found 2026-09-06). In the full suite some forty earlier
        # modules setdefault the same value test_kernel.py writes, which is why it passed there. The
        # kernel's own token is the one the handler checks.
        req = urllib.request.Request(
            "http://127.0.0.1:%d/color" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_live_name_recolors_the_names_registry(self):
        (self.names / SID).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        st, r = self._post({"target": "web", "bg": "#54B204"})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("id"), SID)
        self.assertEqual(r.get("bg"), "#54B204")
        self.assertEqual(r.get("fg"), "black", "the owning palette's fg word rides the ack")
        parts = (self.names / SID).read_text().rstrip("\n").split("\t")
        self.assertEqual(parts[0], "web", "name preserved")
        self.assertEqual(parts[1], "/proj/TESTHOST/app", "cwd preserved")
        self.assertEqual(parts[2], "#54B204", "new bg written")
        self.assertEqual(parts[3], "black", "the palette's fg word for green")
        self.assertTrue(self.dirty, "a successful recolor marks views dirty — the dashboards' repaint signal")

    def test_a_sid_target_recolors_a_dormant_session(self):
        # SID2 is NOT in _live_names — a recolor is a names-registry write, so dormant works by sid
        (self.names / SID2).write_text("worker\t/proj/TESTHOST/svc\t#1EA1EB\twhite\n")
        st, r = self._post({"target": SID2, "bg": "#54B204"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("id"), SID2)
        self.assertEqual((self.names / SID2).read_text().rstrip("\n").split("\t"),
                         ["worker", "/proj/TESTHOST/svc", "#54B204", "black"])

    def test_a_non_swatch_refuses_and_leaves_the_file_alone(self):
        (self.names / SID).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        st, r = self._post({"target": "web", "bg": "#123456"})
        self.assertFalse(r.get("ok"))
        self.assertIn("not a swatch", r.get("error") or "")
        self.assertEqual((self.names / SID).read_text(),
                         "web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n",
                         "a refused recolor writes nothing")

    def test_an_unknown_target_is_loud(self):
        st, r = self._post({"target": "ghost", "bg": "#54B204"})
        self.assertFalse(r.get("ok"))
        self.assertIn("no live session named", r.get("error") or "")

    def test_missing_target_or_bg_is_a_400(self):
        st, r = self._post({"target": "web"})
        self.assertEqual(st, 400)
        st, r = self._post({"bg": "#54B204"})
        self.assertEqual(st, 400)


    def test_a_record_that_reads_with_no_name_is_refused_with_the_cause_and_left_alone(self):
        # before the guard: ok True, and the file read \t\t#54B204\tblack\n (name and cwd erased). A record
        # whose first field is empty is another writer's window or a damaged file, never a real record:
        # the writer leaves it alone, and the route says which of its two refusals this is
        (self.names / SID).write_text("")
        saved = list(km._SDK_BOOT_PROBLEMS)
        self.addCleanup(lambda: km._SDK_BOOT_PROBLEMS.__setitem__(slice(None), saved))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            st, r = self._post({"target": "web", "bg": "#54B204"})
        self.assertEqual(st, 200)
        self.assertFalse(r.get("ok"), r)
        self.assertIn("reads with no name", r.get("error") or "", "the cause, not the absent-record question")
        self.assertNotIn("no names record", r.get("error") or "")
        self.assertEqual((self.names / SID).read_text(), "", "left byte for byte as it was")
        self.assertFalse(self.dirty, "nothing to repaint")
        self.assertIn(SID, err.getvalue(), "the log names the sid")

    def test_an_absent_record_still_asks_if_the_session_is_known(self):
        st, r = self._post({"target": SID2, "bg": "#54B204"})
        self.assertFalse(r.get("ok"), r)
        self.assertIn("no names record for that session", r.get("error") or "")
        self.assertFalse((self.names / SID2).exists(), "no record is invented")
        self.assertFalse(self.dirty)


if __name__ == "__main__":
    unittest.main()
