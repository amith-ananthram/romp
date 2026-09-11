#!/usr/bin/env python3
"""POST /down: the quiesce `romp down` asks for before it stops the kernel.

The kernel never exits from this route: under the manager a kernel exit is a crash to respawn,
so the stop comes top-down through the supervisor. The route only makes the moment quiet. It
arms the going-down hold (new turn starts and session creates held), blocks until the in-flight
count reaches 0 or `wait` runs out, and answers what a stop right now would cut. Pinned here:
the explicit-token gate (a WRITE that holds every session's turn starts, so the ambient cookie is
not enough, the /busy?drain=1 rule), the wait against a fake backend whose count falls mid-wait,
the bounded give-up with the in-flight names, the cancel arm, both create doors refusing while
the hold is in force, the no-backend case (nothing to hold: quiet at once), and the pid every 200
names, which is the only pid `romp down` will send a stop signal to (the auth-exempt /version
vouches for nothing, and a CLI aimed at the wrong port once took a pid from it).
Synthetic only: the real Handler on an ephemeral loopback port, a fake backend, an invented token.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# a dead manager port, never popped: absent is the one unsafe state (see tests/conftest.py)
os.environ["ROMP_MANAGER_PORT"] = "1"
km = load_source("romp_kernel_down", os.path.join(BIN, "romp-kernel"))


class _FakeBackend:
    """What the route needs of SdkBackend: the in-flight count, the names behind it, and the
    quiesce trio. Records every call so the tests can assert the hold's TTL and the cancel."""

    def __init__(self, busy=0, names=()):
        self.busy = busy
        self.names = list(names)
        self.quiesced = []      # the TTLs each quiesce() call armed
        self.canceled = 0
        self._until = 0.0

    def busy_count(self):
        return self.busy

    def inflight_names(self):
        return list(self.names[: self.busy])

    def quiesce(self, ttl):
        self.quiesced.append(ttl)
        self._until = time.time() + ttl

    def quiescing(self):
        return self._until > time.time()

    def cancel_quiesce(self):
        self.canceled += 1
        self._until = 0.0


class DownRoute(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self._saved_be = km._sdk_backend
        self._saved_live_map = km._live_map
        km._live_map = lambda *a, **k: {}     # no live sessions in a test
        self.be = _FakeBackend()
        km._sdk_backend = self.be

    def tearDown(self):
        km._sdk_backend = self._saved_be
        km._live_map = self._saved_live_map
        self.srv.shutdown()
        self.srv.server_close()

    def _post(self, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data,
                                     headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw_body = e.read()
            try:
                return e.code, json.loads(raw_body)
            except Exception:
                return e.code, {"raw": raw_body.decode("utf-8", "replace")}

    def _down(self, body=None, **kw):
        return self._post("/down", body, headers={"X-Romp-Token": km.TOKEN}, **kw)

    # ── the gate ────────────────────────────────────────────────────────────
    def test_no_credential_is_refused_and_arms_nothing(self):
        status, _ = self._post("/down", {"wait": 0})
        self.assertEqual(status, 403)
        self.assertEqual(self.be.quiesced, [], "an unauthenticated POST holds nobody's turns")

    def test_the_ambient_cookie_alone_is_refused(self):
        # the preamble's _authorize takes the cookie; the route demands the EXPLICIT token on top
        status, body = self._post("/down", {"wait": 0}, headers={"Cookie": "romp_token=" + km.TOKEN})
        self.assertEqual(status, 403)
        self.assertIn("serve token", body.get("error", ""))
        self.assertEqual(self.be.quiesced, [])

    def test_a_query_token_is_accepted_like_the_header(self):
        status, body = self._post("/down?token=" + km.TOKEN, {"wait": 0})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(len(self.be.quiesced), 1)

    # ── the pid: the direct stop's proof of ownership ────────────────────────
    def test_a_200_names_this_kernels_pid_on_every_arm(self):
        # `romp down` ends by SIGTERMing a kernel nothing above it stopped, at a pid it must not
        # take from the auth-exempt /version: that route answers any local process, so a CLI aimed
        # at the wrong port (an empty ROMP_KERNEL_PORT falls to the default) could read another
        # kernel's pid there and signal it. The pid a kernel names on a 200 here was given under
        # the caller's serve token, so it is the pid of a kernel the caller manages.
        for body in ({"wait": 0}, {"cancel": True}):
            status, out = self._down(body)
            self.assertEqual(status, 200, repr(body))
            self.assertEqual(out["pid"], os.getpid(), repr(body))
        km._sdk_backend = None                      # the no-backend arm too
        status, out = self._down({"wait": 0})
        self.assertEqual(status, 200)
        self.assertEqual(out["pid"], os.getpid())

    def test_a_wrong_or_missing_token_gets_a_refusal_with_no_pid(self):
        # a kernel that is not the caller's must hand nothing a stop could act on
        for headers, why in ((None, "no credential"),
                             ({"X-Romp-Token": "not-this-kernels-token"}, "a wrong header token"),
                             ({"Cookie": "romp_token=" + km.TOKEN}, "the ambient cookie alone")):
            status, body = self._post("/down", {"wait": 0}, headers=headers)
            self.assertIn(status, (401, 403), why)
            # the preamble's text/plain refusal comes back as {"raw": text}: no pid anywhere in it
            self.assertNotIn("pid", json.dumps(body), why)
        status, body = self._post("/down?token=not-this-kernels-token", {"wait": 0})
        self.assertIn(status, (401, 403))
        self.assertNotIn("pid", json.dumps(body))
        self.assertEqual(self.be.quiesced, [], "a refused caller holds nobody's turns either")

    # ── the quiesce ─────────────────────────────────────────────────────────
    def test_a_quiet_kernel_answers_at_once_and_holds_for_wait_plus_grace(self):
        status, body = self._down({"wait": 5})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "quiet": True, "busy": 0, "inflight": [], "waited": 0.0,
                                "pid": os.getpid()})
        self.assertEqual(self.be.quiesced, [5 + km.DOWN_HOLD_GRACE_S],
                         "the hold outlives the wait by the grace, so the stop that follows lands "
                         "on a still-quiet kernel")

    def test_the_wait_ends_on_the_event_the_count_reaches_zero(self):
        # two turns in flight when the ask arrives; they end while the route waits. The wait asked
        # for is the bound, far longer than the turns take to end: a route that sat out its bound
        # would also answer quiet (the count is 0 by then), so the answer's own `waited` is checked
        # against the bound, and only the bound: it reads the bound when the route sits it out, and
        # the turns' end when the route returns on the event
        self.be.busy = 2
        self.be.names = ["web", "api"]
        seen = []

        def finish_turns():
            seen.append(self.be.busy)
            time.sleep(0.4)
            self.be.busy = 0

        threading.Thread(target=finish_turns, daemon=True).start()
        status, body = self._down({"wait": 30})
        self.assertEqual(status, 200)
        self.assertEqual(seen, [2], "the turns were in flight when the wait began")
        self.assertTrue(body["quiet"])
        self.assertEqual(body["busy"], 0)
        self.assertEqual(body["inflight"], [])
        self.assertLess(body["waited"], 30, "the wait ended on the event, not at its bound")
        self.assertEqual(self.be.quiesced, [30 + km.DOWN_HOLD_GRACE_S], "the hold was armed BEFORE the wait")

    def test_a_kernel_still_busy_at_the_deadline_names_what_a_stop_cuts(self):
        self.be.busy = 1
        self.be.names = ["web"]
        status, body = self._down({"wait": 0.3})
        self.assertEqual(status, 200)
        self.assertFalse(body["quiet"])
        self.assertEqual(body["busy"], 1)
        self.assertEqual(body["inflight"], ["web"], "the CLI says which sessions the stop cuts")
        self.assertIsInstance(body["waited"], float, "how long it waited, for the CLI's line")

    def test_wait_zero_is_a_probe_that_still_arms_the_hold(self):
        # `--now` asks with a wait of 0: no waiting, but the hold and the token check still happen
        self.be.busy = 1
        status, body = self._down({"wait": 0})
        self.assertEqual(status, 200)
        self.assertFalse(body["quiet"])
        self.assertEqual(self.be.quiesced, [km.DOWN_HOLD_GRACE_S])

    def test_the_default_wait_applies_when_the_body_names_none(self):
        status, _ = self._down({})
        self.assertEqual(status, 200)
        self.assertEqual(self.be.quiesced, [km.DOWN_WAIT_DEFAULT_S + km.DOWN_HOLD_GRACE_S])

    # ── the create doors refuse while the hold is in force ───────────────────
    def test_post_new_is_refused_while_quiescing(self):
        self._down({"wait": 0})
        self.assertTrue(self.be.quiescing())
        status, body = self._post("/new", {"name": "web", "dir": tempfile.mkdtemp(), "backend": "sdk"},
                                  headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 503, "a session born now would die with the kernel: refuse, loudly")
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], km.GOING_DOWN_REFUSAL)

    def test_the_ws_create_op_is_refused_with_the_same_words(self):
        self._down({"wait": 0})
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "app": "chat"}
        km.Handler._dispatch_ws(None, {"type": "createSession", "name": "web", "dir": tempfile.mkdtemp(),
                                       "backend": "sdk"}, client)
        warns = [m for m in sent if m.get("type") == "warn"]
        self.assertEqual(len(warns), 1, sent)
        self.assertEqual(warns[0]["text"], km.GOING_DOWN_REFUSAL)

    def test_cancel_releases_the_hold_and_reopens_the_doors(self):
        self._down({"wait": 0})
        self.assertTrue(self.be.quiescing())
        status, body = self._down({"cancel": True})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "canceled": True, "pid": os.getpid()})
        self.assertEqual(self.be.canceled, 1)
        self.assertFalse(self.be.quiescing())
        status, _ = self._post("/new", {"name": "web", "dir": tempfile.mkdtemp(), "backend": "sdk"},
                               headers={"X-Romp-Token": km.TOKEN})
        self.assertNotEqual(status, 503, "after the cancel the door is whatever it was before")

    def test_the_refusal_states_the_fact_and_hands_over_no_command(self):
        # `romp new` inside a session prints a 4xx body's error verbatim, so the reader can be an
        # AGENT; told to run `romp up` it would, and undo a stop the user made on purpose. The
        # text says what is happening and instructs nobody.
        text = km.GOING_DOWN_REFUSAL
        self.assertIn("on purpose", text)
        self.assertIn("cannot start", text)
        for cmd in ("romp up", "romp down", "romp-service", "systemctl", "launchctl", "start it"):
            self.assertNotIn(cmd, text, "no command for the reader to run: %r" % cmd)
        self.assertNotIn("\u2014", text, "no em dashes in text a session can read")

    def test_going_down_reads_the_global_and_never_builds_a_backend(self):
        km._sdk_backend = None
        self.assertFalse(km._going_down())
        km._sdk_backend = False            # "unavailable", the other non-backend value
        self.assertFalse(km._going_down())

    # ── malformed asks and the no-backend case ───────────────────────────────
    def test_a_bad_body_or_wait_is_a_400_and_arms_nothing(self):
        for raw, why in ((b"[]", "a list"), (b"nonsense", "not JSON")):
            status, body = self._down(raw=raw)
            self.assertEqual(status, 400, why)
            self.assertFalse(body["ok"])
        for w in ("5", True, -1, km.DOWN_WAIT_MAX_S + 1):
            status, _ = self._down({"wait": w})
            self.assertEqual(status, 400, repr(w))
        self.assertEqual(self.be.quiesced, [], "no malformed request holds anyone's turns")

    def test_no_sdk_backend_means_nothing_to_hold(self):
        km._sdk_backend = None
        status, body = self._down({"wait": 5})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "quiet": True, "busy": 0, "inflight": [], "waited": 0,
                                "pid": os.getpid()})
        # the cancel arm is a no-op with the same answer
        status, body = self._down({"cancel": True})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "canceled": True, "pid": os.getpid()})


class QuiesceLease(unittest.TestCase):
    """The quiesce on the REAL SdkBackend, where the route's fake above cannot reach: the lease's wake
    timer, and the resume notice a turn cut by `romp down` gets at the next start. The timer cases
    read the ARMED timer (its interval, and what its callback does when the hold is or is not in
    force), never the clock."""

    @classmethod
    def setUpClass(cls):
        cls.sb = load_source("romp_sdk_backend_down", os.path.join(BIN, "romp_sdk_backend.py"))

    def _backend(self, d=None):
        return self.sb.SdkBackend(d or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)

    def _disarm(self, be):
        t = be._drain_wake_timer
        if t is not None:
            t.cancel()

    def test_a_deploy_poll_inside_a_quiesce_keeps_a_wake_at_the_quiesce_lapse(self):
        # refresh_drain_hold extends the hold (max) and must re-arm the wake for the hold it just
        # extended, not for its own 12.5s: a wake fired under a still-held lease, with none at the
        # longer quiesce's lapse, leaves held fresh turns waiting on an unrelated event when no
        # stop follows (the CLI died between /down and the service call)
        be = self._backend()
        be.DRAIN_HOLD_TTL = 0.2
        be.quiesce(5.0)
        be.refresh_drain_hold()                      # the parked poll lands inside the quiesce
        try:
            self.assertGreater(be._drain_hold_until, time.time() + 4, "the hold is still the quiesce's")
            self.assertGreater(be._drain_wake_timer.interval, 4.0,
                               "the re-armed wake covers the hold it just extended, not the 0.7s lease")
        finally:
            self._disarm(be)

    def test_a_plain_deploy_poll_still_wakes_after_its_own_lease(self):
        be = self._backend()
        be.DRAIN_HOLD_TTL = 0.2
        be.refresh_drain_hold()
        try:
            self.assertAlmostEqual(be._drain_wake_timer.interval, 0.7, places=1,
                                   msg="no quiesce: the wake is the lease TTL plus the half-second, as before")
        finally:
            self._disarm(be)

    def test_a_second_shorter_quiesce_keeps_the_doors_and_the_wake_at_the_longer_lapse(self):
        # a second, shorter quiesce inside a longer one (a `romp down --wait 300` abandoned at
        # Ctrl-C, then a `romp down --wait 30` abandoned too) must not reopen the create doors at
        # the short lapse while the hold keeps turn starts to the long one, and the wake must be
        # armed for the hold's lapse
        be = self._backend()
        be.quiesce(5.0)
        be.quiesce(0.4)
        try:
            self.assertGreater(be._drain_hold_until, time.time() + 4, "the hold is the longer lease's")
            self.assertGreater(be._quiesce_until, time.time() + 4, "and so are the create doors")
            self.assertGreater(be._drain_wake_timer.interval, 4.0, "the wake covers the hold, not the shorter ttl")
            self.assertTrue(be.quiescing() and be.drain_holding(), "the doors and the hold agree")
        finally:
            self._disarm(be)

    def test_a_wake_that_fires_under_the_hold_re_arms_to_the_remaining_time(self):
        # the timer's own callback checks the hold at the moment it fires: still held (a timer armed
        # for a lease that has since been extended) means re-arm for what remains, not wake now and
        # never again
        be = self._backend()
        wakes = []
        be._wake_all_inputs = lambda: wakes.append(time.monotonic())
        be.quiesce(5.0)
        self._disarm(be)
        # what remains of the hold, plus the half-second, read right before the fire: the re-armed
        # interval is compared against this, never against the 5.0 the quiesce asked for
        expected = be._drain_hold_until - time.time() + 0.5
        be._drain_wake_fired()                       # an early fire, by hand
        self.assertEqual(wakes, [], "no wake while the hold is in force")
        self.assertIsNotNone(be._drain_wake_timer, "re-armed")
        self.assertAlmostEqual(be._drain_wake_timer.interval, expected, delta=0.2,
                               msg="for the remaining hold plus the half-second, not a fresh lease")
        self._disarm(be)
        be.cancel_quiesce()
        self.assertEqual(len(wakes), 1, "the cancel wakes at once, as before")
        be._drain_wake_fired()                       # a fire with no hold left: wake, arm nothing
        self.assertEqual(len(wakes), 2)
        self.assertIsNone(be._drain_wake_timer)

    def test_a_fire_after_the_hold_lapsed_wakes_the_held_inputs(self):
        be = self._backend()
        wakes = []
        be._wake_all_inputs = lambda: wakes.append(1)
        be.quiesce(5.0)
        self._disarm(be)
        with be._lock:
            be._drain_hold_until = time.time() - 1   # the lapse, as the timer would find it
            be._quiesce_until = time.time() - 1
        be._drain_wake_fired()
        self.assertEqual(wakes, [1], "a lapsed hold wakes now")
        self.assertIsNone(be._drain_wake_timer)
        self.assertFalse(be.quiescing() or be.drain_holding())

    # ── the resume notice after `romp down` + a later start ──────────────────
    def _cut_session(self, d, sid, working_t):
        sb = self.sb
        sb.write_reg(Path(d), sid, {"sid": sid, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": sid})
        sb.append_state(Path(d), sid, "working", t=working_t)

    def _audit(self, d, t, action, **extra):
        row = {"t": t, "action": action}
        row.update(extra)
        with open(os.path.join(d, "restart-audit.jsonl"), "a") as f:
            f.write(json.dumps(row) + "\n")

    def _reconcile(self, d, be, sid):
        from unittest import mock
        be._ensure = lambda sid, on_boot_settled=None: on_boot_settled and on_boot_settled()
        with mock.patch.object(self.sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([self.sb.read_reg(Path(d), sid)])
        return self.sb.read_reg(Path(d), sid).get("queue")

    def test_a_turn_romp_down_cut_hears_the_stop_and_the_gap(self):
        # `romp down` files {t, action: down} on restart-audit.jsonl before the stop; at the next
        # start the turn it cut is resumed with the stop and start times, not a bare "restarted"
        # that reads as a gap of seconds
        sb = self.sb
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000001"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 4 * 3600 - 240)     # the turn began before the stop
        self._audit(d, now - 4 * 3600, "down", cmd="romp down")
        q = self._reconcile(d, self._backend(d), sid)
        self.assertEqual(len(q), 1)
        self.assertTrue(q[0].startswith(sb._RESUME_NUDGE_LEAD), "the lead sentence is the restart notice's")
        self.assertIn("The stop was on purpose: romp down at ", q[0])
        self.assertIn("started again at ", q[0])
        self.assertIn("(4 h later)", q[0])
        self.assertIn("before relying on it", q[0])
        self.assertIn(sb._RESUME_NUDGE_REST, q[0], "the disarm and the continue instruction follow")
        self.assertRegex(q[0], r"<!-- romp-gist: [^>]*-->$", "a gist head for the chat, like every notice")
        self.assertTrue(sb.is_resume_nudge(q[0]))
        self.assertNotIn("\u2014", q[0])
        self.assertTrue(q[0].startswith("<!-- romp-injected --><!-- romp-system -->[romp] "),
                        "a [romp] notice, the sanctioned family")

    def test_a_down_older_than_the_cut_turn_is_someone_elses_stop(self):
        # `romp down` Friday, `romp up` Monday (that boot said so), then a crash respawn: the newest
        # audit row is still the Friday `down`, but the turn it would explain began after it
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000002"
        now = int(time.time())
        self._audit(d, now - 3 * 86400, "down", cmd="romp down")
        self._cut_session(d, sid, working_t=now - 600)
        q = self._reconcile(d, self._backend(d), sid)
        self.assertEqual(q, [self.sb.BOOT_RESUME_NUDGE], "the plain restart notice: this cut was not the down")

    def test_a_refresh_or_no_row_keeps_the_plain_notice(self):
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000003"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 100)
        self.assertEqual(self._reconcile(d, self._backend(d), sid), [self.sb.BOOT_RESUME_NUDGE],
                         "no audit row (a crash respawn): the notice as before")
        d2 = tempfile.mkdtemp()
        self._cut_session(d2, sid, working_t=now - 100)
        self._audit(d2, now - 3600, "down", cmd="romp down")
        self._audit(d2, now - 50, "refresh")               # the newest row is the refresh that cut this
        self.assertEqual(self._reconcile(d2, self._backend(d2), sid), [self.sb.BOOT_RESUME_NUDGE])

    def test_a_mark_written_between_the_down_row_and_the_stop_still_hears_the_stop(self):
        # `romp down` files its row and the service stop lands a moment later; a session that marked
        # mid-turn in that window (an api_retry storm's `retrying`, a mid-turn forward's `working`)
        # stamped a state NEWER than the row. The row is compared against the turn's START: the
        # first machine-active record after the last turn boundary, however many marks the turn
        # wrote after it.
        sb = self.sb
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000004"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 3 * 3600 - 300)      # the turn began here
        sb.append_state(Path(d), sid, "retrying", t=now - 3 * 3600 - 20)
        self._audit(d, now - 3 * 3600, "down", cmd="romp down --wait 5")
        sb.append_state(Path(d), sid, "working", t=now - 3 * 3600 + 1)  # the stop window
        self.assertEqual(sb.cut_turn_start(Path(d), sid), now - 3 * 3600 - 300, "the start, not the newest mark")
        q = self._reconcile(d, self._backend(d), sid)
        self.assertEqual(len(q), 1)
        self.assertIn("The stop was on purpose: romp down at ", q[0])
        self.assertIn("(3 h later)", q[0])
        # the reconcile itself writes the machineCut boundary for the resume it queued, so the next
        # cut of this session starts a fresh count from there
        self.assertEqual(sb.cut_turn_start(Path(d), sid), int(self.sb.last_state(Path(d), sid)["t"]))

    def test_a_failed_down_then_an_unrelated_cut_keeps_the_plain_notice(self):
        # a `romp down` whose stop did not land files a superseding `down-failed` row and leaves the
        # kernel running; whatever cuts that kernel later is not the down, and the newest row being
        # the failure says so (any newest row that is not `down` reads as not deliberate)
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000005"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 7200)
        self._audit(d, now - 3600, "down", cmd="romp down")
        self._audit(d, now - 3590, "down-failed", cmd="romp down")
        self.assertEqual(self._reconcile(d, self._backend(d), sid), [self.sb.BOOT_RESUME_NUDGE])

    def test_a_manager_sigterm_note_after_the_down_row_still_hears_the_stop(self):
        # the manager appends its own row, action manager-sigterm, right before it kills a kernel:
        # under `romp down` that row lands AFTER the CLI's `down` row, and a reader that took the
        # newest row alone would read it as no deliberate stop and lose the stop wording. The row
        # is a mechanism note (the manager was the messenger), never an intent, so the walk back
        # skips it. The exact order: down, then manager-sigterm with trigger cli-down, then the cut.
        sb = self.sb
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000008"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 4 * 3600 - 240)
        self._audit(d, now - 4 * 3600, "down", cmd="romp down")
        self._audit(d, now - 4 * 3600 + 2, "manager-sigterm", kernel="main", pid=424242, reason="stop", trigger="cli-down")
        self.assertEqual(sb.newest_down_stop(Path(d)), now - 4 * 3600)
        q = self._reconcile(d, self._backend(d), sid)
        self.assertEqual(len(q), 1)
        self.assertIn("The stop was on purpose: romp down at ", q[0])
        self.assertIn("(4 h later)", q[0])
        self.assertTrue(sb.is_resume_nudge(q[0]))

    def test_a_failed_down_under_a_manager_sigterm_note_is_still_no_deliberate_stop(self):
        # the skip lands on the newest INTENT, whatever it is: down, then down-failed (the stop did not
        # land), then a manager-sigterm from a later hand stop (trigger stop) reads as no deliberate
        # stop, exactly as down-failed newest does; the skip never reaches past the failure to the down
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000009"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 7200)
        self._audit(d, now - 3600, "down", cmd="romp down")
        self._audit(d, now - 3590, "down-failed", cmd="romp down")
        self._audit(d, now - 1800, "manager-sigterm", kernel="main", pid=424242, reason="stop", trigger="stop")
        self.assertIsNone(self.sb.newest_down_stop(Path(d)))
        self.assertEqual(self._reconcile(d, self._backend(d), sid), [self.sb.BOOT_RESUME_NUDGE])

    def test_a_down_then_an_up_then_a_later_cut_keeps_the_plain_notice(self):
        # `romp down` cut this turn; `romp up` resumed it (that boot wrote the machineCut boundary
        # and the resumed turn marked working); a crash cut THAT turn. The newest row is still the
        # down (`romp up` files none): the boundary is what keeps the old down off the new cut, since
        # the two working records are otherwise one unbroken machine-active run
        sb = self.sb
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000006"
        now = int(time.time())
        self._cut_session(d, sid, working_t=now - 4000)
        self._audit(d, now - 3600, "down", cmd="romp down")
        sb.append_machine_cut(Path(d), sid, "restart", t=now - 3000)    # the up's boot reconcile
        sb.append_state(Path(d), sid, "working", t=now - 2900)          # the resumed turn
        self.assertEqual(sb.cut_turn_start(Path(d), sid), now - 2900)
        self.assertEqual(self._reconcile(d, self._backend(d), sid), [self.sb.BOOT_RESUME_NUDGE])

    def test_cut_turn_start_reads_back_to_the_last_boundary(self):
        sb = self.sb
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-000000000007"
        self.assertIsNone(sb.cut_turn_start(Path(d), sid), "no file: no cut turn")
        sb.append_state(Path(d), sid, "working", t=100)
        sb.append_state(Path(d), sid, "idle", t=200)
        self.assertIsNone(sb.cut_turn_start(Path(d), sid), "an idle tail is no cut turn")
        sb.append_state(Path(d), sid, "working", t=300)
        sb.append_awaiting(Path(d), sid, False)                        # an overlay, skipped
        sb.append_state(Path(d), sid, "compacting", t=310)
        sb.append_state(Path(d), sid, "working", t=320)
        self.assertEqual(sb.cut_turn_start(Path(d), sid), 300, "the first active record after the idle")
        sb.append_machine_cut(Path(d), sid, "crash", t=400.7)
        self.assertEqual(sb.cut_turn_start(Path(d), sid), 400, "a boundary with nothing after it: its own stamp")
        sb.append_state(Path(d), sid, "retrying", t=500)
        self.assertEqual(sb.cut_turn_start(Path(d), sid), 500)
        with open(os.path.join(d, "states", sid + ".jsonl"), "a") as f:
            f.write("not json\n")
        self.assertEqual(sb.cut_turn_start(Path(d), sid), 500, "a corrupt line is skipped, never a raise")

    def test_the_down_notice_names_dates_across_days_and_the_constant_is_unchanged(self):
        sb = self.sb
        stop = int(time.mktime((2026, 9, 4, 17, 12, 0, 0, 0, -1)))
        start = int(time.mktime((2026, 9, 7, 9, 3, 0, 0, 0, -1)))
        text = sb.down_resume_nudge(stop, start)
        self.assertIn("romp down at 2026-09-04 17:12, started again at 2026-09-07 09:03 (2 d 15 h later)", text)
        same = sb.down_resume_nudge(stop, stop + 3 * 3600 + 38 * 60)
        self.assertIn("romp down at 17:12, started again at 20:50 (3 h 38 min later)", same)
        self.assertIn("(under a minute later)", sb.down_resume_nudge(stop, stop + 5))
        self.assertIn("(12 min later)", sb.down_resume_nudge(stop, stop + 12 * 60 + 3))
        # the plain constant is byte-identical to its text before the split (the fixtures and the
        # popover match on it), and the kernel's restart signature is a substring of both variants
        self.assertEqual(sb.BOOT_RESUME_NUDGE, (
            "<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted and cut this session's "
            "in-flight turn; the session has been resumed with its history intact. If the conversation tail "
            "shows '[Request interrupted by user]', that record came from this cut, not from the user: nobody "
            "asked you to stop. Re-read the tail of the conversation and pick the work back up where it "
            "stopped, without asking whether to continue. Any messages queued before the restart follow "
            "this one.<!-- romp-gist: resumed after a romp restart cut its turn -->"))
        self.assertIn(km.INTR_RESTART_SIG, text)
        self.assertEqual(len(km.ROMP_GIST_RE.findall(text)), 1, "one gist head, like the plain notice")
        self.assertTrue(sb.is_resume_nudge(sb.BOOT_RESUME_NUDGE) and sb.is_resume_nudge(text)
                        and sb.is_resume_nudge(sb.CRASH_RESUME_NUDGE))
        self.assertFalse(sb.is_resume_nudge("a user's own message") or sb.is_resume_nudge(None))

    def test_newest_down_stop_reads_the_newest_intent_only(self):
        sb = self.sb
        d = tempfile.mkdtemp()
        self.assertIsNone(sb.newest_down_stop(Path(d)), "no file")
        self._audit(d, 1000, "down")
        self.assertEqual(sb.newest_down_stop(Path(d)), 1000)
        self._audit(d, 2000, "p2p-update", reason="from TESTHOST")
        self.assertIsNone(sb.newest_down_stop(Path(d)), "a later restart of another kind hides the down")
        with open(os.path.join(d, "restart-audit.jsonl"), "a") as f:
            f.write("not json\n")
        self.assertIsNone(sb.newest_down_stop(Path(d)), "a corrupt tail is nothing, never a raise")

    def test_newest_down_stop_skips_manager_sigterm_notes_and_stops_at_the_newest_intent(self):
        # the manager's rows in the orders the ledger sees them: one note per kernel it kills, after
        # whichever intent row asked; every trigger the manager writes is a note
        sb = self.sb
        d = tempfile.mkdtemp()
        self._audit(d, 1000, "down", cmd="romp down")
        self._audit(d, 1001, "manager-sigterm", kernel="main", pid=424242, reason="stop", trigger="cli-down")
        self.assertEqual(sb.newest_down_stop(Path(d)), 1000, "the manager's note of its SIGTERM is not an intent")
        self._audit(d, 1001, "manager-sigterm", kernel="aux", pid=424243, reason="stop", trigger="cli-down")
        self.assertEqual(sb.newest_down_stop(Path(d)), 1000, "one note per kernel: every one skipped")
        self._audit(d, 1003, "down-failed", cmd="romp down")
        self.assertIsNone(sb.newest_down_stop(Path(d)), "down-failed newest: no deliberate stop")
        self._audit(d, 1004, "manager-sigterm", kernel="main", pid=424244, reason="stop", trigger="stop")
        self.assertIsNone(sb.newest_down_stop(Path(d)), "the skip lands on the failure, never past it")
        self._audit(d, 1005, "refresh")
        self._audit(d, 1006, "manager-sigterm", kernel="main", pid=424245, reason="restart", trigger="restart-all")
        self.assertIsNone(sb.newest_down_stop(Path(d)), "a refresh under its note hides the down, as before")
        d2 = tempfile.mkdtemp()
        self._audit(d2, 1, "manager-sigterm", kernel="main", pid=424246, reason="restart", trigger="restart")
        self.assertIsNone(sb.newest_down_stop(Path(d2)), "a note with no intent beneath it is nothing")

    def test_the_thread_popover_hides_every_resume_nudge_not_one_exact_text(self):
        # the popover read the transcript's user rows and dropped the one equal to BOOT_RESUME_NUDGE;
        # the down variant must be dropped the same way, so the reader matches on is_resume_nudge
        import sys
        sb = self.sb
        d = tempfile.mkdtemp()
        p = os.path.join(d, "thread.jsonl")
        rows = [
            {"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": "2026-09-01T10:00:00.000Z",
             "message": {"role": "user", "content": sb.down_resume_nudge(1000, 2000)}},
            {"type": "user", "uuid": "u2", "parentUuid": "u1", "timestamp": "2026-09-01T10:00:01.000Z",
             "message": {"role": "user", "content": sb.BOOT_RESUME_NUDGE}},
            {"type": "user", "uuid": "u3", "parentUuid": "u2", "timestamp": "2026-09-01T10:00:02.000Z",
             "message": {"role": "user", "content": "what did the cache test show?"}},
            {"type": "assistant", "uuid": "a1", "parentUuid": "u3", "timestamp": "2026-09-01T10:00:03.000Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "It passed on the second run."}]}},
        ]
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        tsid = "11111111-2222-3333-4444-00000000000a"
        saved = (km._thread_reg, km._thread_transcript_path, sys.modules.get("romp_sdk_backend"))
        km._thread_reg = lambda t: {"sid": t}
        km._thread_transcript_path = lambda reg, t: p
        sys.modules["romp_sdk_backend"] = sb
        km._thread_msgs_cache.pop(tsid, None)
        try:
            got = km._thread_messages(tsid, "")
        finally:
            km._thread_reg, km._thread_transcript_path = saved[0], saved[1]
            if saved[2] is not None:
                sys.modules["romp_sdk_backend"] = saved[2]
            else:
                sys.modules.pop("romp_sdk_backend", None)
            km._thread_msgs_cache.pop(tsid, None)
        self.assertEqual([r["text"] for r in got], ["what did the cache test show?", "It passed on the second run."])
        self.assertEqual([r["who"] for r in got], ["you", "agent"])

if __name__ == "__main__":
    unittest.main()
