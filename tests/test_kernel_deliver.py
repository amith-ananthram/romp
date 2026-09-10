#!/usr/bin/env python3
"""The deliver-time WAKE moved into the kernel (the user 2026-06-26): the postal bus drains its maildir and
hands the banner to the kernel (POST /deliver), which injects it into the pane for a tmux session (draft-
preserving, ported from the bus's _push/_inject) or enqueues it for an SDK session. The bus never shells
tmux. Also the resume-picker watcher (_picker_check). Deterministic — the tmux primitives are stubbed.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

from tests.conftest import thread_census, wait_for_census

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_deliver", os.path.join(BIN, "romp-kernel"))


class TmuxDeliver(unittest.TestCase):
    """TmuxBackend.deliver: inject the banner only at a live ❯ prompt we can write to; otherwise defer (the
    mail stays in the maildir for the Stop-hook drain)."""

    def setUp(self):
        self.T = km._TMUX
        self.saved = {n: getattr(self.T, n) for n in
                      ("send_keys", "set_var", "capture", "pane_in_mode", "set_buffer", "paste_buffer", "live_sessions")}
        self.saved_name = km._name_of
        self.keys = []
        self.T.send_keys = lambda name, *a, **k: self.keys.append(a)
        self.T.set_var = lambda *a, **k: None
        self.T.set_buffer = lambda text: None
        self.pasted = False
        self.T.paste_buffer = lambda name: setattr(self, "pasted", True)
        self.T.pane_in_mode = lambda name, t=2: False
        km._name_of = lambda sid: "sess" if sid == "s1" else None
        self.T.live_sessions = lambda: {"s1": {"state": "idle"}}

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(self.T, n, v)
        km._name_of = self.saved_name

    def _cap_after_paste(self, before, after):
        self.T.capture = lambda *a, **k: (after if self.pasted else before)

    def test_injects_at_idle_empty_prompt(self):
        # the box rule is ─{10,}; use 12 so _box_region finds it
        self._cap_after_paste("────────────\n❯ \n────────────",
                              "────────────\n❯ [Pasted text +2 lines]\n────────────")
        self.assertTrue(self.T.deliver("s1", "## banner"))
        self.assertIn(("Enter",), self.keys, "submitted after the paste landed")

    def test_defers_when_not_at_a_prompt(self):
        self.T.capture = lambda *a, **k: "Loading session…\n(no prompt box yet)"   # no ❯ → not injectable
        self.assertFalse(self.T.deliver("s1", "x"))

    def test_defers_a_draft_while_working(self):
        self.T.live_sessions = lambda: {"s1": {"state": "working"}}
        self.T.capture = lambda *a, **k: "────────────\n❯ a half-typed draft\n────────────"   # has_draft + working → unsafe
        self.assertFalse(self.T.deliver("s1", "x"))

    def test_defers_an_unknown_session(self):
        self.assertFalse(self.T.deliver("nope", "x"))

    def test_defers_a_non_injectable_state(self):
        self.T.live_sessions = lambda: {"s1": {"state": "permission"}}   # at a permission dialog → never inject
        self.assertFalse(self.T.deliver("s1", "x"))


class PickerCheck(unittest.TestCase):
    """_picker_check surfaces a revived session stuck on Claude's resume picker (no hook fires while it's up):
    if the pane shows the picker (no ❯ + a 'summary' option), mark @claude-state=picker + log a picker event."""

    def setUp(self):
        self.T = km._TMUX
        self.saved = (self.T.capture, self.T.live_sessions, km._name_of, km.jd.STATE)
        km._name_of = lambda sid: "sess"
        self.T.live_sessions = lambda: {"s1": {"state": ""}}     # never reaches a normal state → keep polling
        self.recorded = []
        self._saved_rec = self.T.record_state
        self.T.record_state = lambda name, state: self.recorded.append((name, state))
        km.jd.STATE = __import__("pathlib").Path(tempfile.mkdtemp())

    def tearDown(self):
        self.T.capture, self.T.live_sessions, km._name_of, km.jd.STATE = self.saved
        self.T.record_state = self._saved_rec

    def test_marks_picker_when_the_resume_picker_is_up(self):
        self.T.capture = lambda *a, **k: "  Resume from summary\n  Resume full session as-is\n"
        km._PICKER_GRACE = 1
        km._picker_check("s1")
        self.assertIn(("sess", "picker"), self.recorded, "a confirmed picker is surfaced as @claude-state=picker")
        log = (km.jd.STATE / "states" / "s1.jsonl").read_text()
        self.assertIn('"state": "picker"', log)
        self.assertIn('"tier": "strict"', log)


class SdkDeliverSourcePin(unittest.TestCase):
    def test_sdk_backend_defines_deliver_as_a_no_echo_enqueue(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        body = src.split("def deliver(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("s.enqueue(text)", body, "SDK deliver enqueues the banner (the deliver-time wake)")
        self.assertNotIn("_echo_text", body, "no optimistic human echo — it's a peer's mail, not the user's input")

    def test_post_deliver_routes_through_backend_for(self):
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertIn('u.path == "/deliver"', src)
        self.assertIn("Sessions.backend_for(sid).deliver(sid, text)", src)


class Chrome(unittest.TestCase):
    """The tmux status-bar chrome moved into TmuxBackend (the postal bus POSTs the semantic data via
    /mail-badge, /deliver-chrome, /reconcile-peers): the mail badge, peer chips, and message indicator. A
    no-op for a session with no tmux name (an SDK session skips)."""

    def setUp(self):
        self._census0 = thread_census()
        self.T = km._TMUX
        # A painted badge starts an expiry thread that sleeps BADGE_TTL (300 s) before it looks again; under the
        # full suite that thread outlived this module by minutes (T282). Zero TTL here: the thread wakes at once,
        # reads the stubbed token ("" ≠ the badge's) and ends without clearing anything.
        self.T.BADGE_TTL = 0
        self.saved = (self.T.set_var, self.T.fire, self.T.display, self.T.show_var, self.T.refresh_client,
                      km._name_of, km._identity_of)
        self.sets, self.fires = [], []
        self.T.set_var = lambda name, var, val, t=3: self.sets.append((name, var, val))
        self.T.fire = lambda args, t=3: self.fires.append(tuple(args))
        self.T.display = lambda name, fmt, t=2.5: ""              # no existing chips
        self.T.show_var = lambda name, var, t=2.5: ""
        self.T.refresh_client = lambda: None
        km._identity_of = lambda sid: ("#abc", "#fff")

    def tearDown(self):
        # The expiry thread reads BADGE_TTL and the stubbed show_var on ITS first instructions; wait for it to end
        # while both are still in place (restoring first would let a late-scheduled thread read the 300 s constant
        # and the real tmux client), then put the class back as it was.
        left = wait_for_census(self._census0)
        (self.T.set_var, self.T.fire, self.T.display, self.T.show_var, self.T.refresh_client,
         km._name_of, km._identity_of) = self.saved
        self.T.__dict__.pop("BADGE_TTL", None)                  # back to the class constant
        self.assertEqual(left, [], "no badge-expiry thread outlives the test (T282)")

    def test_mail_badge_paints_the_recipient(self):
        km._name_of = lambda sid: "recip" if sid == "r" else None
        self.T.mail_badge("r", "alpha", "a")
        vset = {v: val for (nm, v, val) in self.sets if nm == "recip"}
        self.assertEqual(vset.get("@romp-mail-from"), "alpha")
        self.assertEqual(vset.get("@romp-mail-bg"), "#abc")       # the sender's identity colour

    def test_mail_badge_is_a_noop_without_a_tmux_session(self):
        km._name_of = lambda sid: None                            # SDK / unknown → no tmux name → no paint
        self.T.mail_badge("sdk", "alpha", "a")
        self.assertEqual(self.sets, [])

    def test_deliver_chrome_writes_both_ends(self):
        km._name_of = lambda sid: {"r": "recip", "s": "sender"}.get(sid)
        self.T.deliver_chrome("r", "recip", "s", "sender", "hello", "mid1")
        # one peer-chip write + one message-prefix write per end → 4 batched tmux calls
        self.assertEqual(len(self.fires), 4)
        prefixes = [f for f in self.fires if "@romp-msg-dir" in f]
        self.assertEqual(len(prefixes), 2, "the directional message indicator is set on both ends")


class PostalNoticeRoute(unittest.TestCase):
    """POST /postal-notice: the bus's one door to the dashboard (review find, 2026-09-08). A mail file
    it had to move aside, a return note it could not deliver, temps a crash left: each lands as one
    row on the ring the bell mirrors, under the `refused` kind, with the bus's own text. Token-gated
    like every POST; a body with no text files nothing."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, body, token=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Content-Type": "application/json"}
        if token:
            hdrs["X-Romp-Token"] = km.TOKEN
        c.request("POST", "/postal-notice", json.dumps(body), hdrs)
        r = c.getresponse()
        raw = r.read().decode()
        c.close()
        try:
            return r.status, json.loads(raw or "{}")
        except ValueError:
            return r.status, raw

    def test_a_notice_files_one_refused_row_and_an_empty_one_files_nothing(self):
        before = km._sync_notice_count()
        status, out = self._post({"text": "mail for web: 1.2_ab.TESTHOST could not be read (errno 13); moved aside"})
        self.assertEqual((status, out.get("ok")), (200, True))
        self.assertEqual(km._sync_notice_count(), before + 1, "exactly one row")
        row = km._sync_notice_rows()[-1]
        self.assertEqual((row["kind"], row["ok"]), ("refused", False), "the kind a fault wears, never a machine sync")
        self.assertIn("moved aside", row["text"], "the bus's own text, unchanged")
        status, out = self._post({"text": "   "})
        self.assertEqual((status, out.get("ok")), (400, False))
        self.assertIn("text required", out.get("error", ""))
        status, out = self._post("not an object")
        self.assertEqual(status, 400)
        self.assertEqual(km._sync_notice_count(), before + 1, "a refused body files nothing")
        status, _ = self._post({"text": "anonymous"}, token=False)
        self.assertEqual(status, 403, "token-gated like every POST")
        self.assertEqual(km._sync_notice_count(), before + 1)


if __name__ == "__main__":
    unittest.main()
