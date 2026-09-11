#!/usr/bin/env python3
"""Postal isolation (the user 2026-06-23): a session with the timeline lane's mailbox toggled off
(postalServiceOff — legacy postalOff — in the kernel's session-flags.json) is invisible to list_agents, can't send, and can't receive —
for working privately. These pin the flag reader + the read_box RECEIVE gate at the unit level; the
end-to-end /send + /agents enforcement is in tests/romp-postal.bats.

Synthetic only — placeholder UUIDs, hermetic temp state dir, no real session data.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-3333-4444-555555555555"


def _set_flag(sid, postal_off):
    pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(pm.SESSION_FLAGS.read_text()) if pm.SESSION_FLAGS.exists() else {}
    if postal_off:
        data[sid] = {"postalOff": True}     # legacy key on purpose: pins back-compat (reader honours old + new)
    else:
        data.pop(sid, None)
    pm.SESSION_FLAGS.write_text(json.dumps(data))


class PostalOff(unittest.TestCase):
    def tearDown(self):
        try:
            pm.SESSION_FLAGS.unlink()
        except OSError:
            pass

    def test_default_not_isolated(self):
        self.assertFalse(pm._postal_off(SID), "no flags file → on the Romp Postal Service")
        self.assertFalse(pm._postal_off(""), "empty sid → not isolated")

    def test_flag_toggles_isolation(self):
        _set_flag(SID, True)
        self.assertTrue(pm._postal_off(SID))
        _set_flag(SID, False)
        self.assertFalse(pm._postal_off(SID), "clearing the flag rejoins the Romp Postal Service")

    def test_other_flags_do_not_isolate(self):
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text(json.dumps({SID: {"hideFromFeed": True}}))   # muted from feed, NOT postal
        self.assertFalse(pm._postal_off(SID), "hideFromFeed alone must not isolate from postal")

    def test_malformed_flags_file_fails_open(self):
        # the flags dir is made HERE, not inherited from an earlier test: under pytest-xdist the
        # class's tests split across workers, and this one landed on a worker where no sibling had
        # created the dir yet (surfaced 2026-09-04 when the suite's test count shifted the split)
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertFalse(pm._postal_off(SID), "a corrupt flags file must NOT wedge messaging (fail open)")

    def test_read_box_holds_mail_while_isolated(self):
        box = pm.MAILROOT / SID / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "msg1").write_text("From: peer\nFrom-Id: x\nDate: now\n\nhello\n")
        _set_flag(SID, True)
        self.assertEqual(pm.read_box(SID, consume=True), [],
                         "isolated → a drain delivers nothing")
        self.assertTrue((box / "msg1").exists(),
                        "the message stays in new/ (not consumed) until the session reconnects")
        _set_flag(SID, False)
        got = pm.read_box(SID, consume=True)
        self.assertEqual([m["body"] for m in got], ["hello"],
                         "reconnecting delivers the held mail")


class WiringAcrossSurfaces(unittest.TestCase):
    """The postalServiceOff flag spans three files (kernel boot exposure → timeline render/toggle → postal
    enforcement). Pin the cross-surface wiring by name so a rename can't silently disconnect a surface."""

    def test_kernel_boot_exposes_postaloff(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"postalServiceOff": _postal_isolated(sid)', src,
                      "the kernel must publish postalServiceOff in the session boot so the timeline can render it: the EFFECTIVE "
                      "state (the mailbox flag with its legacy twin, and a comment thread's mail-off default, T356)")

    def test_timeline_view_draws_and_toggles_the_mailbox(self):
        # Since 2026-07-28 the mailbox lives in the lane GEAR's drop-down (LANE_TOGGLES) rather than as
        # its own lane icon — the row still draws the mailboxIcon and toggles postalServiceOff through
        # the same _setSessionFlag persistence.
        src = open(os.path.join(os.path.dirname(BIN), "ui", "romp-timeline-view.js")).read()
        self.assertIn("mailboxIcon", src, "the gear menu draws the (monochrome) mailbox icon")
        self.assertIn("flag: 'postalServiceOff', label: 'Postal service', icon: mailboxIcon", src,
                      "the gear menu row toggles the postalServiceOff flag")
        self.assertIn("this._setSessionFlag(s, t.flag, next);", src, "menu rows persist via _setSessionFlag")



# ── a comment thread's mail is OFF until the user breaks it out (T356) ──────────────────────────────────

PARENT = "22222222-3333-4444-5555-666666666666"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
SENDER = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"


def _reg(sid, **fields):
    d = pm.SESSION_FLAGS.parent / "sdk"
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".json")).write_text(json.dumps({"sid": sid, "alive": True, **fields}))


def _flags(data):
    pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
    pm.SESSION_FLAGS.write_text(json.dumps(data))


class ThreadMailOff(unittest.TestCase):
    """The bus derives a thread's mail-off default from the kernel's durable reg (threadOf) beside the flags file:
    a thread on disk with NO flag reads OFF (the user 2026-09-11: a manager's comment thread received the manager's
    mail, mailed two workers and merged a pull request as if it were the manager); the fresh key `threadMail` at the
    literal True is the one way on short of a break-out; a break-out (threadOf gone) returns the ordinary rule."""

    def tearDown(self):
        for f in (pm.SESSION_FLAGS, pm.SESSION_FLAGS.parent / "sdk" / (THREAD + ".json"), pm.SESSION_FLAGS.parent / "sdk" / (SENDER + ".json")):
            try:
                f.unlink()
            except OSError:
                pass

    def test_a_thread_with_no_flag_reads_off_and_says_why(self):
        _reg(THREAD, threadOf=PARENT)
        self.assertEqual(pm._mail_off_why(THREAD), "thread"); self.assertTrue(pm._postal_off(THREAD))
        self.assertEqual(pm._mail_off_why(PARENT), "", "the session it belongs to is untouched")
        self.assertEqual(pm._thread_of(THREAD), PARENT); self.assertEqual(pm._thread_of(PARENT), "", "no reg: an ordinary session")

    def test_the_fresh_key_at_the_literal_true_turns_it_on_and_nothing_else_does(self):
        _reg(THREAD, threadOf=PARENT)
        _flags({THREAD: {"threadMail": True}})
        self.assertEqual(pm._mail_off_why(THREAD), "")
        for v in ("true", 1, "on"):
            _flags({THREAD: {"threadMail": v}})
            self.assertEqual(pm._mail_off_why(THREAD), "thread", repr(v))
        _flags({THREAD: {"postalServiceOff": False}})
        self.assertEqual(pm._mail_off_why(THREAD), "thread", "the mailbox flag at False is no way on for a thread")
        _flags({THREAD: {"threadMail": True, "postalServiceOff": True}})
        self.assertEqual(pm._mail_off_why(THREAD), "isolation", "mail on for the thread, then the user's own isolation holds")
        _flags("{not json")
        self.assertEqual(pm._mail_off_why(THREAD), "thread", "a corrupt flags file cannot turn a thread's mail on")

    def test_breaking_out_returns_the_ordinary_rule(self):
        _reg(THREAD, threadOf=PARENT)
        self.assertTrue(pm._postal_off(THREAD))
        _reg(THREAD)                                   # promotion pops threadOf from the reg
        self.assertEqual(pm._mail_off_why(THREAD), ""); self.assertFalse(pm._postal_off(THREAD))
        _flags({THREAD: {"postalOff": True}})
        self.assertEqual(pm._mail_off_why(THREAD), "isolation")

    def test_the_receive_gate_holds_a_threads_mail_in_its_box(self):
        _reg(THREAD, threadOf=PARENT)
        box = pm.MAILROOT / THREAD / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "msg1").write_text("From: peer\nFrom-Id: x\nDate: now\n\nhello\n")
        self.assertEqual(pm.read_box(THREAD, consume=True), [], "held, not delivered")
        self.assertTrue((box / "msg1").exists(), "…and kept for the day the thread is broken out")

    def test_a_thread_is_invisible_to_peers_and_a_send_to_it_is_refused_with_the_thread_reason(self):
        _reg(THREAD, threadOf=PARENT)
        rows = [{"id": PARENT, "name": "web"}, {"id": THREAD, "name": "web-comment-1", "thread": True, "parent": PARENT},
                {"id": SENDER, "name": "api"}]
        saved = pm._kernel_sessions_checked
        pm._kernel_sessions_checked = lambda threads=False: ([r for r in rows if threads or not r.get("thread")], True)
        try:
            visible = [a["id"] for a in pm.all_agents(threads=True) if not pm._postal_off(a["id"])]   # the /agents filter
            self.assertNotIn(THREAD, visible); self.assertIn(PARENT, visible)
            res = pm.resolve_recipient("web-comment-1", SENDER)
            self.assertEqual((res["kind"], res["status"]), ("error", 403))
            self.assertIn("COMMENT THREAD", res["error"]); self.assertIn("breaks it out", res["error"]); self.assertIn("final", res["error"])
            _flags({THREAD: {"threadMail": True}})
            res = pm.resolve_recipient("web-comment-1", SENDER)
            self.assertEqual(res["kind"], "direct", "with mail on, the thread is a recipient like any other")
        finally:
            pm._kernel_sessions_checked = saved


class ThreadOwnSendRefused(unittest.TestCase):
    """The thread's OWN send, over the real handler: 403 whose text says the thread's mail is off until it is
    broken out (final; the isolation refusal a peer must not route around)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        _reg(THREAD, threadOf=PARENT)
        self._saved = pm._kernel_sessions_checked
        rows = [{"id": PARENT, "name": "web"}, {"id": THREAD, "name": "web-comment-1", "thread": True, "parent": PARENT}]
        pm._kernel_sessions_checked = lambda threads=False: ([r for r in rows if threads or not r.get("thread")], True)
        pm.STATE.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        pm._kernel_sessions_checked = self._saved
        for f in (pm.SESSION_FLAGS, pm.SESSION_FLAGS.parent / "sdk" / (THREAD + ".json")):
            try:
                f.unlink()
            except OSError:
                pass

    def _send(self, frm_id, frm, to):
        import urllib.error
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/send" % self.port, method="POST",
                                     data=json.dumps({"to": to, "from": frm, "from_id": frm_id, "body": "a note", "kind": "coordinate"}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": pm.SERVE_TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_the_threads_own_send_is_refused_until_broken_out(self):
        status, body = self._send(THREAD, "web-comment-1", "web")
        self.assertEqual(status, 403, body)
        self.assertTrue(body["error"].startswith("isolation:"), "the isolation refusal a peer treats as final")
        self.assertIn("COMMENT THREAD", body["error"]); self.assertIn("breaks it out", body["error"]); self.assertIn("Nothing was sent", body["error"])
        self.assertFalse((pm.MAILROOT / PARENT / "new").exists() and any((pm.MAILROOT / PARENT / "new").iterdir()), "nothing landed")
        _reg(THREAD)                                       # broken out: the reg has no threadOf
        status, body = self._send(THREAD, "web-2", "web")
        self.assertNotEqual(status, 403, "a promoted session sends like any other: %r" % (body,))

if __name__ == "__main__":
    unittest.main(verbosity=2)
