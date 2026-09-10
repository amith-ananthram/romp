#!/usr/bin/env python3
"""The postal heartbeat's cost to the kernel: GET /sessions per beat and per tool call (2026-09-06).

Each MCP heartbeat used to resolve this session's identity twice (my_id, then my_name — one GET /sessions
each) and the bus's handler fetched a third time, so about 30 local sessions beating every 30 s were
3 requests per second against a kernel already at one core, for a beat the bus ignores (local sids are
already in its listing). Now: one resolution per beat and per tool call (_self_identity), and the loop
ends the first time the bus confirms this session local from its own answered listing. A remote
(client-only box) session never hears "local" from the hub's bus and keeps beating exactly as before.

The kernel is the ROMP_SESSIONS_FILE seam; fetches are counted at _kernel_sessions_checked, the one
function every listing read goes through. Synthetic only: placeholder UUIDs, the notes-api demo sessions."""
import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

WEB = "11111111-2222-3333-4444-555555555555"
API = "11111111-2222-3333-4444-666666666666"
THREAD = "11111111-2222-3333-4444-777777777777"
FORK = "99999999-8888-7777-6666-555555555555"

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([
    {"id": WEB, "name": "web", "dir": "", "state": "working", "working": "", "lastSid": FORK},
    {"id": API, "name": "api", "dir": "", "state": "idle", "working": "", "lastSid": ""},
    {"id": THREAD, "name": "web-t1", "dir": "", "state": "working", "working": "", "lastSid": "",
     "thread": True, "parent": WEB}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
pm = load_source("romp_postal_hb_fetches", os.path.join(BIN, "romp-postal-service"))


class Counting(unittest.TestCase):
    """Every listing fetch is counted; the bus is stubbed at _http and answers what each test says."""

    def setUp(self):
        # os.environ is process-wide: other modules in the same pytest worker pop or repoint the seam
        # between tests, so pin both env halves here and put them back after (the self-identity test's idiom)
        self._env = (os.environ.get("CLAUDE_CODE_SESSION_ID"), os.environ.get("ROMP_SESSIONS_FILE"),
                     os.environ.get("ROMP_POSTAL_PEERS"))
        os.environ["CLAUDE_CODE_SESSION_ID"] = WEB
        os.environ["ROMP_SESSIONS_FILE"] = _SESS
        os.environ.pop("ROMP_POSTAL_PEERS", None)               # peer mode, the default; legacy tests set 0
        pm._LOCAL_CONFIRMED[0] = False                          # a fresh MCP process
        self._saved = (pm._kernel_sessions_checked, pm._http, pm.ensure)
        self.fetches, self.posts = [], []
        real = self._saved[0]
        pm._kernel_sessions_checked = lambda threads=False: (self.fetches.append(threads),
                                                             real(threads=threads))[1]
        self.answer = {"ok": True, "local": True, "agents": []}
        pm._http = lambda method, path, payload=None: (self.posts.append((method, path, payload))
                                                      or dict(self.answer))
        pm.ensure = lambda: True

    def tearDown(self):
        pm._kernel_sessions_checked, pm._http, pm.ensure = self._saved
        pm._LOCAL_CONFIRMED[0] = False
        for key, val in zip(("CLAUDE_CODE_SESSION_ID", "ROMP_SESSIONS_FILE", "ROMP_POSTAL_PEERS"), self._env):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    # ── one resolution ────────────────────────────────────────────────────────────────────────

    def test_self_identity_is_one_fetch_with_thread_rows(self):
        self.assertEqual(pm._self_identity(), (WEB, "web"))
        self.assertEqual(self.fetches, [True], "one GET /sessions?threads=1 for both halves")

    def test_self_identity_keeps_both_fallbacks(self):
        # the fork fsid resolves to the stable row (the 2026-07-27 rule) in the same single fetch
        os.environ["CLAUDE_CODE_SESSION_ID"] = FORK
        self.assertEqual(pm._self_identity(), (WEB, "web"))
        # no row anywhere: the env value passes through as the id, the names registry is the name
        os.environ["CLAUDE_CODE_SESSION_ID"] = "abcdef00-0000-0000-0000-000000000000"
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        (pm.NAMES_DIR / "abcdef00-0000-0000-0000-000000000000").write_text("tests\t\t#111111\t#ffffff\n")
        self.assertEqual(pm._self_identity(), ("abcdef00-0000-0000-0000-000000000000", "tests"))
        os.environ.pop("CLAUDE_CODE_SESSION_ID")
        self.assertEqual(pm._self_identity(), (None, None), "not a romp session")
        self.assertEqual(pm.my_id(), None)
        self.assertEqual(pm.my_name(), None)

    def test_a_thread_row_heartbeats_under_its_row_name(self):
        # a comment thread withholds its names entry, so the name must come from the row and nothing else
        os.environ["CLAUDE_CODE_SESSION_ID"] = THREAD
        self.assertTrue(pm._heartbeat_once())
        self.assertEqual(self.posts, [("POST", "/heartbeat", {"id": THREAD, "name": "web-t1"})])
        self.assertEqual(self.fetches, [True])

    def test_one_heartbeat_iteration_is_one_fetch(self):
        pm._heartbeat_once()
        self.assertEqual(self.fetches, [True], "was two: my_id() then my_name()")
        self.assertEqual(self.posts, [("POST", "/heartbeat", {"id": WEB, "name": "web"})])

    def test_one_tool_call_is_one_fetch(self):
        for tool, args in (("list_agents", {}), ("check_inbox", {}),
                           ("send_message", {"to": "api", "body": "the staging port?", "kind": "question"})):
            self.fetches.clear(); self.posts.clear()
            pm._mcp_call(tool, args)
            self.assertEqual(self.fetches, [True], "%s: one GET /sessions per tool call (was two)" % tool)
            self.assertEqual(self.posts[0], ("POST", "/heartbeat", {"id": WEB, "name": "web"}),
                             "%s: the per-call beat still goes out" % tool)
        self.assertEqual(self.posts[1][2]["from_id"], WEB)
        self.assertEqual(self.posts[1][2]["from"], "web")

    def test_cli_agents_and_cli_send_are_one_fetch_each(self):
        import io, sys
        out, saved_out = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            self.assertEqual(pm.cli_agents(), 0)
            self.assertEqual(self.fetches, [True])
            self.assertEqual(self.posts[-1][1], "/agents?me=web")
            self.fetches.clear()
            self.assertEqual(pm.cli_send(["--kind", "coordinate", "api", "synthetic body"]), 0)
            self.assertEqual(self.fetches, [True])
            self.assertEqual(self.posts[-1][2]["from_id"], WEB)
        finally:
            sys.stdout = saved_out

    # ── the loop's exit ───────────────────────────────────────────────────────────────────────

    def test_heartbeat_once_reads_the_bus_verdict(self):
        self.answer = {"ok": True, "local": True}
        self.assertTrue(pm._heartbeat_once(), "the bus confirmed local from its own listing")
        self.answer = {"ok": True, "local": False}
        self.assertFalse(pm._heartbeat_once(), "remote, or the bus's listing was unanswered: keep beating")
        self.answer = {"ok": True}
        self.assertFalse(pm._heartbeat_once(), "an older bus without the bit: keep beating")
        pm._http = lambda *a, **k: (_ for _ in ()).throw(pm.BusError("down"))
        self.assertFalse(pm._heartbeat_once(), "no bus: keep beating")
        os.environ.pop("CLAUDE_CODE_SESSION_ID")
        self.assertFalse(pm._heartbeat_once(), "no identity: nothing to end")

    def test_loop_ends_on_the_first_local_answer_and_not_before(self):
        answers = [{"ok": True, "local": False}, {"ok": True}, {"ok": True, "local": True},
                   {"ok": True, "local": True}]
        pm._http = lambda method, path, payload=None: (self.posts.append(path) or answers.pop(0))
        stop = threading.Event()
        self.addCleanup(stop.set)              # a regression that never returns must not leave a beating thread
        t = threading.Thread(target=pm._heartbeat_loop, kwargs={"interval": 0.01, "stop": stop}, daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "the loop ends once the bus says local")
        self.assertFalse(stop.is_set(), "and it ended on its own, before the cleanup's stop")
        self.assertEqual(self.posts, ["/heartbeat"] * 3, "two non-local answers kept it beating")
        self.assertEqual(self.fetches, [True] * 3, "one listing fetch per beat")

    def _run_loop_until(self, n_posts, stop):
        """Start the loop on a fast cadence and wait until it has posted n_posts beats (5 s cap)."""
        self.addCleanup(stop.set)              # whatever the assertions do, the thread ends with the test
        t = threading.Thread(target=pm._heartbeat_loop, kwargs={"interval": 0.005, "stop": stop}, daemon=True)
        t.start()
        deadline = time.time() + 5
        while len(self.posts) < n_posts and time.time() < deadline:
            time.sleep(0.005)
        return t

    def test_repeated_non_local_answers_never_end_the_loop(self):
        # the remote-session shape: the hub's bus never lists this sid and answers local=false on every
        # beat, so the loop keeps beating and each beat is a full resolution — no memo of the sid or the
        # verdict between beats; the external stop is the only thing that ends it here
        self.answer = {"ok": True, "local": False}
        stop = threading.Event()
        t = self._run_loop_until(4, stop)
        self.assertTrue(t.is_alive(), "four non-local answers and the loop is still running")
        stop.set(); t.join(5)
        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(len(self.posts), 4)
        self.assertEqual(self.fetches, [True] * len(self.posts), "one full resolution per beat")
        self.assertFalse(pm._LOCAL_CONFIRMED[0], "a false answer never latches")

    def test_legacy_mode_keeps_the_loop_through_a_local_answer(self):
        # ROMP_POSTAL_PEERS=0: `romp mail remote` can swap the local bus for the hub's under a running
        # session, so a `local: true` from the bus of the moment must not end the beats (review, 2026-09-06)
        os.environ["ROMP_POSTAL_PEERS"] = "0"
        self.answer = {"ok": True, "local": True}
        stop = threading.Event()
        t = self._run_loop_until(3, stop)
        self.assertTrue(t.is_alive(), "local:true in legacy mode: still beating")
        stop.set(); t.join(5)
        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(len(self.posts), 3)
        self.assertFalse(pm._LOCAL_CONFIRMED[0], "and the per-call beat is not spared either")

    def test_peer_mode_ends_the_loop_without_an_external_stop(self):
        # the same answer in peer mode (the default): the loop returns on its own, once
        self.answer = {"ok": True, "local": True}
        stop = threading.Event()
        self.addCleanup(stop.set)
        t = threading.Thread(target=pm._heartbeat_loop, kwargs={"interval": 0.005, "stop": stop}, daemon=True)
        t.start(); t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse(stop.is_set(), "returned on its own")
        self.assertEqual(self.posts, [("POST", "/heartbeat", {"id": WEB, "name": "web"})])
        self.assertTrue(pm._LOCAL_CONFIRMED[0])

    # ── the per-call beat ─────────────────────────────────────────────────────────────────────

    def test_confirmed_local_session_skips_the_per_call_beat(self):
        self.assertTrue(pm._heartbeat_once())                    # peer mode, local:true → latched
        self.posts.clear(); self.fetches.clear()
        pm._mcp_call("list_agents", {})
        self.assertEqual([p[1] for p in self.posts], ["/agents?me=web"], "no /heartbeat for a confirmed-local session")
        self.assertEqual(self.fetches, [True], "the tool call still resolves identity once")

    def test_remote_session_beats_on_every_call(self):
        self.answer = {"ok": True, "local": False}
        self.assertFalse(pm._heartbeat_once())
        for _ in range(3):
            self.posts.clear()
            pm._mcp_call("list_agents", {})
            self.assertEqual(self.posts[0][1], "/heartbeat", "local:false never spares the beat")

    def test_unanswered_bus_never_latches(self):
        self.answer = {"ok": True}                                # an older bus: no bit
        self.assertFalse(pm._heartbeat_once())
        pm._http = lambda *a, **k: (_ for _ in ()).throw(pm.BusError("down"))
        self.assertFalse(pm._heartbeat_once())
        self.assertFalse(pm._LOCAL_CONFIRMED[0])

    # ── the bus's side of the beat ────────────────────────────────────────────────────────────

    def test_the_bus_answers_local_for_a_thread_row_through_the_seam(self):
        # the handler decides locality from a listing WITH thread rows; the seam filters them out on
        # threads=False exactly as the route does, so a comment thread's beat is local here only if the
        # handler asked for them (otherwise it is filed as remote presence, the phantom of 2026-09-06)
        pm.HEARTBEATS.clear()
        self.addCleanup(pm.HEARTBEATS.clear)
        self.assertTrue(pm._record_heartbeat(THREAD, "web-t1"), "a thread row is local to its own bus")
        self.assertNotIn(THREAD, pm.HEARTBEATS, "and never remote presence")
        self.assertEqual(self.fetches, [True], "one listing for the beat, thread rows included")

    # ── the bus's retry pass ──────────────────────────────────────────────────────────────────

    def _dead_boxes(self, names):
        """Mailboxes for sessions the listing does not hold, each with an empty new/ and a names entry."""
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        sids = []
        for i, name in enumerate(names):
            sid = "99999999-8888-7777-6666-%012d" % i
            (pm.MAILROOT / sid / "new").mkdir(parents=True, exist_ok=True)
            (pm.NAMES_DIR / sid).write_text("%s\t\t#111111\t#ffffff\n" % name)
            sids.append(sid)
        self.addCleanup(lambda: [__import__("shutil").rmtree(pm.MAILROOT / sid, ignore_errors=True) for sid in sids])
        return sids

    def test_orphan_sweep_fetches_once_per_poll(self):
        # every dead mailbox used to cost its own GET /sessions to learn a name the listing could not
        # hold (the box is dead by construction); the registry names it, and the sweep's one listing
        # is the only fetch (28 per 30 s poll on a box with 58 mailboxes, review 2026-09-06)
        self._dead_boxes(["ghost-a", "ghost-b", "ghost-c"])
        pm._sweep_orphans()
        self.assertEqual(self.fetches, [True], "one listing for the whole sweep, thread rows included: a comment "
                                               "thread's box is live while its row is (2026-09-10)")

    def test_recall_by_a_dead_name_fetches_once(self):
        dead = self._dead_boxes(["ghost-a", "ghost-b", "ghost-c"])
        (pm.MAILROOT / dead[1] / "new" / "m3").write_text("From: web\nFrom-Id: %s\nX-Park: 1\n\nparked body" % WEB)
        removed = pm._recall(WEB, "ghost-b", None)
        self.assertEqual([(r["id"], r["to"]) for r in removed], [("m3", "ghost-b")])
        self.assertEqual(self.fetches, [True], "one listing for the whole recall, shared by every name lookup")

    def test_recall_by_a_live_name_fetches_once(self):
        (pm.MAILROOT / API / "new").mkdir(parents=True, exist_ok=True)
        (pm.MAILROOT / API / "new" / "m4").write_text("From: web\nFrom-Id: %s\n\nthe body" % WEB)
        try:
            removed = pm._recall(WEB, "api", None)
            self.assertEqual([(r["id"], r["to"]) for r in removed], [("m4", "api")])
            self.assertEqual(self.fetches, [True])
        finally:
            for f in (pm.MAILROOT / API / "new").iterdir():
                f.unlink()

    def test_recall_by_id_fetches_only_when_a_removed_row_needs_a_name(self):
        # by id with no recipient, the listing serves only the removed rows' names: a recall that
        # removes nothing fetches nothing, and one that removes a row fetches once, at first need
        # (the sent-receipts idiom)
        dead = self._dead_boxes(["ghost-a", "ghost-b", "ghost-c"])
        (pm.MAILROOT / dead[2] / "new" / "m5").write_text("From: web\nFrom-Id: %s\nX-Park: 1\n\nparked body" % WEB)
        self.assertEqual(pm._recall(WEB, "", "no-such-id"), [])
        self.assertEqual(self.fetches, [], "nothing removed, no name to look up, no listing")
        removed = pm._recall(WEB, "", "m5")
        self.assertEqual([(r["id"], r["to"]) for r in removed], [("m5", "ghost-c")])
        self.assertEqual(self.fetches, [True], "one listing, fetched when the first removed row needs its name")

    def test_sent_receipts_fetch_once_for_every_unnamed_row(self):
        # check_sent names each row's recipient; rows without a stored toName used to cost one
        # GET /sessions each — one listing per call now, fetched only when a row needs it
        pm.TLDIR.mkdir(parents=True, exist_ok=True)
        log = pm.TLDIR / "messages.jsonl"
        had = log.read_text() if log.exists() else None
        self.addCleanup(lambda: log.write_text(had) if had is not None else log.unlink(missing_ok=True))
        dead = "99999999-8888-7777-6666-000000000009"
        log.write_text("".join(json.dumps(e) + "\n" for e in (
            {"t": 5, "ev": "sent", "id": "m1", "from_id": WEB, "to_id": API},
            {"t": 6, "ev": "sent", "id": "m2", "from_id": WEB, "to_id": THREAD},
            {"t": 7, "ev": "sent", "id": "m3", "from_id": WEB, "to_id": dead},
            {"t": 8, "ev": "sent", "id": "m4", "from_id": WEB, "to_id": "peer:farhost", "toName": "farhost:api"})))
        rows = pm._sent_receipts(WEB)
        self.assertEqual([r["to"] for r in rows], ["api", "web-t1", dead[:8], "farhost:api"])
        self.assertEqual(self.fetches, [True], "one listing for the whole call (was one per unnamed row)")
        self.fetches.clear()
        log.write_text(json.dumps({"t": 8, "ev": "sent", "id": "m4", "from_id": WEB, "to_id": "peer:farhost",
                                   "toName": "farhost:api"}) + "\n")
        pm._sent_receipts(WEB)
        self.assertEqual(self.fetches, [], "no row needed a name: no fetch at all")

    def test_retry_pending_fetches_nothing_without_pending_mail(self):
        pm.MAILPENDING.mkdir(parents=True, exist_ok=True)
        for m in pm.MAILPENDING.iterdir():
            m.unlink()
        pm._retry_pending()
        self.assertEqual(self.fetches, [], "no marker files: no GET /sessions (was one every 5 s)")
        (pm.MAILPENDING / API).write_text("")                   # a stale marker: new/ is empty
        pm._retry_pending()
        self.assertEqual(self.fetches, [], "a stale marker is cleared without a fetch")
        self.assertFalse((pm.MAILPENDING / API).exists(), "the stale marker was reconciled away")

    def test_retry_pending_fetches_once_for_real_pending_mail(self):
        saved_push = pm._push
        pushed = []
        pm._push = lambda sid, row: pushed.append(sid)
        try:
            pm.deliver(API, "web", WEB, "synthetic body", kind="coordinate")
            pm.deliver(WEB, "api", API, "synthetic reply", kind="coordinate")
            self.fetches.clear()
            pm._retry_pending()
            self.assertEqual(self.fetches, [True], "one listing fetch for the whole pass, not one per marker; "
                                                   "thread rows included so a thread's marker is retried")
            self.assertEqual(sorted(pushed), sorted([API, WEB]))
        finally:
            pm._push = saved_push
            for sid in (API, WEB):
                for f in (pm.MAILROOT / sid / "new").glob("*"):
                    f.unlink()
                pm._mark_pending(sid)

    # ── a peer's send during a kernel blink ───────────────────────────────────────────────────

    def test_after_the_latch_a_local_peer_is_unreachable_by_name_during_a_blink(self):
        # What changed on 2026-09-06, pinned (review find, 2026-09-08): a local session's beat that landed
        # while the kernel's listing was unanswered used to be filed as remote presence (the bus could not
        # call it local), so a send to its name during the blink resolved to that row and delivered into its
        # mailbox with no wake; the mail sat there unannounced until the kernel returned. Once the bus has
        # confirmed the session local, its loop has ended and its per-call beat is skipped, so no such row
        # appears, and the standing blink refusal (503, retry shortly, never a death ruling) covers every
        # local peer alike: the mail stays with the sender, who is told so.
        pm.HEARTBEATS.clear()
        self.addCleanup(pm.HEARTBEATS.clear)
        self.assertTrue(pm._heartbeat_once(), "web is confirmed local while the kernel answers")
        self.assertTrue(pm._record_heartbeat(WEB, "web"), "the bus's side of that beat: local, no presence row")
        self.assertNotIn(WEB, pm.HEARTBEATS)
        # the kernel blinks: the listing does not answer
        pm._kernel_sessions_checked = lambda threads=False: (self.fetches.append(threads), ([], False))[1]
        self.posts.clear()
        pm._mcp_call("list_agents", {})                          # web keeps using postal through the blink...
        self.assertEqual([p[1] for p in self.posts], ["/agents?me="], "...and beats nothing: the latch holds")
        r = pm.resolve_recipient("web", frm_id=API)
        self.assertEqual((r["kind"], r["status"]), ("error", 503))
        self.assertIn("Retry shortly", r["error"])
        self.assertNotIn("no live romp session", r["error"], "never a death ruling")
        # the pre-latch shape, still what a beat landing in the blink does (legacy mode, a remote session)
        self.assertFalse(pm._record_heartbeat(WEB, "web"), "unanswered listing: not local")
        self.assertIn(WEB, pm.HEARTBEATS, "filed as remote presence")
        r = pm.resolve_recipient("web", frm_id=API)
        self.assertEqual(r["kind"], "direct", "which is what made the name reachable before the latch")
        self.assertTrue(r["agent"].get("remote"), "as a presence row, delivered with no wake")

    # ── `romp mail remote` after the latch ────────────────────────────────────────────────────

    def _remote_setup_sandbox(self):
        """setup_remote's side effects land in a scratch dir: the client-only marker and the bus pid file."""
        root = Path(tempfile.mkdtemp())
        saved = (pm.CLIENT_ONLY, pm.PIDFILE)
        pm.CLIENT_ONLY, pm.PIDFILE = root / "client-only", root / "server.pid"
        pm.PIDFILE.write_text("not-a-pid")     # a bus "running" here; a stop attempt prints its line and fails
        self.addCleanup(lambda: (setattr(pm, "CLIENT_ONLY", saved[0]), setattr(pm, "PIDFILE", saved[1])))
        return root

    def test_peer_mode_remote_setup_refuses_after_the_latch_force_included(self):
        # the latch's premise is that the bus behind BASE stays this box's own for the life of the process;
        # `romp mail remote --force` used to break it in peer mode: it stopped the local bus, and a hub's bus
        # swapped in behind the port never heard from a session whose beats had ended, so the session went
        # silent on the hub (review find, 2026-09-08). The command refuses in peer mode before any side
        # effect, --force included, and says why.
        self._remote_setup_sandbox()
        self.assertTrue(pm._heartbeat_once(), "peer mode, local:true: latched")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = pm.setup_remote(force=True)
        self.assertEqual(rc, 2, "a refusal, not the 0 of 'nothing to set up'")
        text = out.getvalue()
        self.assertIn("peer mode", text)
        self.assertIn("heartbeat", text, "the refusal names its reason: the beats that ended")
        self.assertNotIn("Stopped the local-only bus", text, "the local bus is left running")
        self.assertFalse(pm.CLIENT_ONLY.exists(), "no client-only marker")
        self.assertTrue(pm._LOCAL_CONFIRMED[0], "the latch stands, and its premise with it")
        self.posts.clear()
        pm._mcp_call("list_agents", {})
        self.assertEqual([p[1] for p in self.posts], ["/agents?me=web"],
                         "the per-call beat stays skipped: the bus behind BASE is still this box's own")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pm.main(["remote", "--force"]), 2, "the CLI route refuses the same way")
            self.assertEqual(pm.main(["remote"]), 2, "with or without --force")
        self.assertFalse(pm.CLIENT_ONLY.exists())

    def test_peer_mode_unreachable_hint_never_points_at_remote_setup(self):
        # the bus-unreachable hint used to send every SSH'd box to `romp mail remote`; in peer mode that
        # command refuses (above), so the hint would be a dead end: the generic server.log hint instead,
        # and the tunnel hint only in the legacy scheme the command belongs to (review find, 2026-09-08)
        os.environ["SSH_CONNECTION"] = "1 2 3 4"
        self.addCleanup(os.environ.pop, "SSH_CONNECTION", None)
        self.assertNotIn("romp mail remote", pm._unreachable_hint(), "peer mode: never the refused command")
        self.assertIn("server.log", pm._unreachable_hint())
        os.environ["ROMP_POSTAL_PEERS"] = "0"
        self.assertIn("run: romp mail remote", pm._unreachable_hint(), "legacy scheme: the tunnel hint stands")

    def test_legacy_mode_remote_setup_still_configures_the_client(self):
        # ROMP_POSTAL_PEERS=0 is where the command applies: the loop never ends there, so the hub hears every
        # beat once the tunnel is up; with the bus reachable the command configures the client and connects
        os.environ["ROMP_POSTAL_PEERS"] = "0"
        self._remote_setup_sandbox()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = pm.setup_remote(force=True)
        self.assertEqual(rc, 0)
        self.assertIn("Already connected", out.getvalue())
        self.assertTrue(pm.CLIENT_ONLY.exists(), "the client-only marker is written")


if __name__ == "__main__":
    unittest.main(verbosity=2)
