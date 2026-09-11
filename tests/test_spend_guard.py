#!/usr/bin/env python3
"""The spend guard (T350, the user 2026-09-11): every live session's spend RATE over the last ten minutes, scaled to an
hour, read from the record cache (the leaf transcript and the agent transcripts beside it); over the ceiling the session
is interrupted and handed one message in the user's voice, every dashboard is warned, a session-events row is filed,
once per crossing, re-armed once the rate falls under half the ceiling; the ceiling is a bare-value setting read at each
check (1000 with no file, 0 disables).

SYNTHETIC fixtures only: placeholder ids, an invented session, transcripts written here with usage fields.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# hermetic state BEFORE the loads (the kernel resolves its state root at import time)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_spend_guard", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-00000000a350"
NOW = 1781200000.0
PRICES = {"claude-opus-4-8": {"in": 5e-6, "out": 25e-6, "cache_w": 6.25e-6, "cache_r": 0.5e-6},
          "claude-haiku-4-5-20251001": {"in": 1e-6, "out": 5e-6, "cache_w": 1.25e-6, "cache_r": 0.1e-6}}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def assistant(t, mid, out_tokens, model="claude-opus-4-8", in_tokens=1000, cache_r=0, cache_w=0):
    return {"type": "assistant", "timestamp": iso(t), "uuid": mid + "-u", "sessionId": SID,
            "message": {"id": mid, "role": "assistant", "model": model,
                        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens,
                                  "cache_creation_input_tokens": cache_w, "cache_read_input_tokens": cache_r},
                        "content": [{"type": "text", "text": "working"}]}}


def user(t, uid):
    return {"type": "user", "timestamp": iso(t), "uuid": uid, "sessionId": SID, "message": {"role": "user", "content": "go"}}


def write_jsonl(path, records, mtime=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in records))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class FakeBackend:
    def __init__(self, stops=True, accepts=True):
        self.interrupts, self.sends, self.logs, self.stops, self.accepts = [], [], [], stops, accepts

    def owns(self, sid):
        return True

    def interrupt(self, sid):
        self.interrupts.append(sid)
        return self.stops

    def send(self, sid, text, qid=None, user=False):
        self.sends.append((sid, text))
        return self.accepts

    def busy(self, sid):
        return False

    def _log(self, m, problem=None, key=None, ring_text=None):
        self.logs.append((str(m), problem, ring_text))


def client(bucket):
    return {"app": "chat", "wid": "w1", "alive": True, "send": lambda payload: bucket.append(json.loads(payload))}


class SpendRate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.leaf = os.path.join(self.td.name, "proj", SID + ".jsonl")

    def tearDown(self):
        self.td.cleanup()

    def test_the_window_sums_the_leaf_and_the_agent_files_beside_it_and_ignores_the_rest(self):
        # the leaf: one response outside the window (an hour ago), two inside; a response logged twice (two content
        # blocks, one message id) counts once at its largest usage row
        write_jsonl(self.leaf, [
            user(NOW - 3600, "u0"), assistant(NOW - 3590, "msg_old", out_tokens=100000),
            user(NOW - 500, "u1"), assistant(NOW - 480, "msg_a", out_tokens=10000),
            assistant(NOW - 470, "msg_a", out_tokens=12000),              # the same response again, larger
            assistant(NOW - 200, "msg_b", out_tokens=4000, model="claude-haiku-4-5-20251001", in_tokens=2000)])
        sub = Path(self.leaf).with_suffix("") / "subagents"
        write_jsonl(sub / "agent-1.jsonl", [assistant(NOW - 300, "msg_s1", out_tokens=20000)], mtime=NOW - 300)
        write_jsonl(sub / "agent-old.jsonl", [assistant(NOW - 5000, "msg_s2", out_tokens=900000)], mtime=NOW - 5000)
        # a WORKFLOW agent, one level down (Claude Code 2.1.261): the review's probe, invisible to a flat listing
        write_jsonl(sub / "workflows" / "wf_abc" / "agent-w1.jsonl", [assistant(NOW - 120, "msg_w1", out_tokens=400000, in_tokens=0)], mtime=NOW - 120)
        os.symlink(self.leaf, sub / "agent-link.jsonl")   # a symlinked file is a user's, never the CLI's: skipped
        usd = km._spend_window_usd(self.leaf, NOW, 600, PRICES)
        want = (1000 * 5e-6 + 12000 * 25e-6) + (2000 * 1e-6 + 4000 * 5e-6) + (1000 * 5e-6 + 20000 * 25e-6) + 400000 * 25e-6
        self.assertAlmostEqual(usd, want, places=9)
        self.assertAlmostEqual(km._spend_rate_usd_per_hour(self.leaf, NOW, 600, PRICES), want * 6, places=6)
        # nothing recorded in the window: zero, and a missing leaf is zero too
        self.assertEqual(km._spend_window_usd(self.leaf, NOW + 5000, 600, PRICES), 0.0)
        self.assertEqual(km._spend_window_usd(os.path.join(self.td.name, "absent.jsonl"), NOW, 600, PRICES), 0.0)

    def test_the_window_reads_the_cache_entry_with_a_tail_accepted_never_the_whole_file_road(self):
        """After a restart the assembly checkpoint restores a transcript as a TAIL entry (the records past its cut); the
        whole-file road would upgrade every live transcript to a full re-read on the guard's first cycle (the checkpoint's
        served tests measure those bytes). The window reads the entry with tail_ok, and never calls the whole-file road."""
        write_jsonl(self.leaf, [assistant(NOW - 100, "msg_t", out_tokens=1000, in_tokens=0)])
        sub = Path(self.leaf).with_suffix("") / "subagents"
        write_jsonl(sub / "agent-1.jsonl", [assistant(NOW - 50, "msg_s", out_tokens=1000, in_tokens=0)], mtime=NOW - 50)
        calls = []
        real = km.em._read_jsonl_entry
        def entry(path, on_fail=None, tail_ok=False, tail_from=None):
            calls.append((os.path.basename(str(path)), tail_ok)); return real(path, on_fail=on_fail, tail_ok=tail_ok, tail_from=tail_from)
        with mock.patch.object(km.em, "_read_jsonl_entry", side_effect=entry), \
             mock.patch.object(km.em, "_read_jsonl_incremental", side_effect=AssertionError("the whole-file road")):
            usd = km._spend_window_usd(self.leaf, NOW, 600, PRICES)
        self.assertAlmostEqual(usd, 2 * 1000 * 25e-6, places=9)
        self.assertEqual(sorted(calls), sorted([(os.path.basename(self.leaf), True), ("agent-1.jsonl", True)]), "each file once, a tail accepted")

    def test_an_unpriced_model_counts_at_the_dearest_row_and_a_record_without_usage_counts_nothing(self):
        write_jsonl(self.leaf, [assistant(NOW - 100, "msg_x", out_tokens=1000, model="mystery-9", in_tokens=0),
                                {"type": "assistant", "timestamp": iso(NOW - 90), "uuid": "n", "message": {"id": "msg_n", "model": "claude-opus-4-8", "content": []}}])
        self.assertAlmostEqual(km._spend_window_usd(self.leaf, NOW, 600, PRICES), 1000 * 25e-6, places=9)


class Memo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.leaf = os.path.join(self.td.name, "proj", SID + ".jsonl")
        km._SPEND_ROWS_CACHE.clear()

    def tearDown(self):
        km._SPEND_ROWS_CACHE.clear()
        self.td.cleanup()

    def test_an_unchanged_file_serves_its_rows_from_the_memo_and_a_changed_one_is_scanned_again(self):
        write_jsonl(self.leaf, [assistant(NOW - 100, "msg_a", out_tokens=1000, in_tokens=0)], mtime=NOW - 100)
        self.assertAlmostEqual(km._spend_window_usd(self.leaf, NOW, 600, PRICES), 1000 * 25e-6, places=9)
        stamp, floor, rows = km._SPEND_ROWS_CACHE[self.leaf]
        self.assertEqual(floor, NOW - 600 - km.SPEND_GUARD_MEMO_SLACK_S)
        with mock.patch.object(km, "_msg_epoch", side_effect=AssertionError("a re-scan of an unchanged file")):
            self.assertAlmostEqual(km._spend_window_usd(self.leaf, NOW + 30, 600, PRICES), 1000 * 25e-6, places=9, msg="a later window, the same stamp: no scan")
            self.assertEqual(km._spend_window_usd(self.leaf, NOW + 800, 600, PRICES), 0.0, "the memo's rows age out of the window without a scan")
        write_jsonl(self.leaf, [assistant(NOW - 100, "msg_a", out_tokens=1000, in_tokens=0), assistant(NOW - 10, "msg_b", out_tokens=1000, in_tokens=0)], mtime=NOW - 10)
        self.assertAlmostEqual(km._spend_window_usd(self.leaf, NOW, 600, PRICES), 2 * 1000 * 25e-6, places=9, msg="a changed stamp: scanned again")
        self.assertNotEqual(km._SPEND_ROWS_CACHE[self.leaf][0], stamp)


class Ceiling(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        jd._state_cache.clear()

    def tearDown(self):
        jd.STATE = self.saved
        jd._state_cache.clear()
        self.td.cleanup()

    def test_the_setting_file_is_read_at_each_check(self):
        self.assertEqual(km._spend_ceiling(), 1000.0, "no file: the default")
        for raw, want in (("250", 250.0), ("0", 0.0), ("1500.5\n", 1500.5), ("junk", 1000.0), ("", 1000.0),
                          ("nan", 1000.0), ("inf", 1000.0), ("-inf", 1000.0), ("-5", 1000.0)):   # LOW 1: not a silent disable
            Path(self.td.name, km.SPEND_CEILING_SETTING).write_text(raw)
            jd._state_cache.clear()
            self.assertEqual(km._spend_ceiling(), want, "the file says %r" % raw)


class Guard(unittest.TestCase):
    """The tick with its seams: synthetic sessions, a fake backend, a fake client list, the default prices."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        jd._state_cache.clear()
        km._SPEND_GUARD.clear()
        km._SPEND_GUARD_SEEDED[0] = False
        km._SPEND_ROWS_CACHE.clear()
        km._interrupt_clicked.pop(SID, None)
        self.leaf = os.path.join(self.td.name, "proj", SID + ".jsonl")
        self.be, self.toasts = FakeBackend(), []
        self.clients = [client(self.toasts), client(self.toasts)]
        self.sessions = [{"sid": SID, "name": "web", "path": self.leaf, "anchor": SID, "mtime": NOW}]
        # the message rides the machine sends' door (LOW 3): recorded here, handed over (False), refused (None) when the
        # backend refuses, as the real door answers
        self.parked = []
        def send_or_park(be, sid, text, echo=None, qid=None, user=False):
            self.parked.append((sid, text))
            return False if be.send(sid, text) else None
        p = mock.patch.object(km, "_send_or_park", side_effect=send_or_park)
        p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        km._SPEND_GUARD.clear()
        km._SPEND_GUARD_SEEDED[0] = False
        km._SPEND_ROWS_CACHE.clear()
        km._interrupt_clicked.pop(SID, None)
        jd.STATE = self.saved
        jd._state_cache.clear()
        self.td.cleanup()

    def _tick(self, now):
        km._spend_guard_tick(now, {SID: {"state": "working"}}, sessions=self.sessions, be=self.be, clients=self.clients, prices=PRICES)

    def _rows(self):
        p = Path(self.td.name, "session-events.jsonl")
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def _spend(self, t, usd_in_window):
        """A leaf whose window holds `usd_in_window` of opus output, spent over the last five minutes."""
        toks = int(usd_in_window / 25e-6)
        write_jsonl(self.leaf, [user(t - 400, "u"), assistant(t - 300, "msg_%d" % int(t), out_tokens=toks, in_tokens=0)])

    def test_a_session_under_the_ceiling_is_left_alone(self):
        self._spend(NOW, 100.0)                          # $100 in ten minutes: $600 an hour
        self._tick(NOW)
        self.assertEqual((self.be.interrupts, self.be.sends, self.toasts, self._rows()), ([], [], [], []))
        self.assertEqual(km._SPEND_GUARD, {})

    def test_a_crossing_stops_the_session_tells_it_once_warns_every_dashboard_and_files_the_row(self):
        self._spend(NOW, 200.0)                          # $200 in ten minutes: $1,200 an hour, over the default 1000
        self._tick(NOW)
        self.assertEqual(self.be.interrupts, [SID], "interrupted first: the fan-out ends now")
        # the Stop button's whole road (MEDIUM 2): the chip's stamp, the retry suppression, the views marked dirty
        self.assertEqual(km._interrupt_clicked.get(SID), NOW, "the interrupt-clicked stamp the chip reads")
        self.assertIn(SID, km._retry_suppress_data(), "retry suppression: the auto-retry and the idle-queue drive stand down")
        self.assertEqual(len(self.be.sends), 1)
        sid, body = self.be.sends[0]
        self.assertEqual(self.parked, [(SID, body)], "the message rode _send_or_park, the machine sends' door")
        self.assertEqual(sid, SID)
        self.assertIn("about $1,200 an hour", body)
        self.assertIn("I set the line at $1,000 an hour", body)
        self.assertIn("Stop whatever is fanning out and tell me what it was before doing anything else.", body)
        self.assertIn("<!-- romp-injected --><!-- romp-system -->", body, "a machine message, marked as such in the tail")
        for w in ("romp", "card", "board", "goal", "nudge", "ceiling", "kernel", "session"):
            self.assertNotIn(w, body.split("<!--")[0].lower(), "the prose speaks as the user, no machinery named: %r" % w)
        self.assertEqual(len(self.toasts), 2, "every connected client, once")
        self.assertEqual(self.toasts[0]["type"], "spendCeiling", "its own type, never warn (a warn is an in-flight create's verdict to the chat)")
        self.assertEqual((self.toasts[0]["sid"], self.toasts[0]["name"], self.toasts[0]["phase"], self.toasts[0]["t"]), (SID, "web", "over", int(NOW)))
        self.assertIn("web was spending about $1,200 an hour at ", self.toasts[0]["text"])
        self.assertIn("over the $1,000 an hour ceiling; it has been stopped and told.", self.toasts[0]["text"])
        rows = self._rows()
        self.assertEqual([r["kind"] for r in rows], ["spend.ceiling"])
        self.assertEqual((rows[0]["sid"], rows[0]["name"], rows[0]["ceilingUsdPerHour"], rows[0]["windowS"]), (SID, "web", 1000.0, 600))
        self.assertAlmostEqual(rows[0]["usdPerHour"], 1200.0, places=1)
        self.assertEqual(rows[0]["t"], int(NOW))
        self.assertEqual((rows[0]["stopped"], rows[0]["stopping"], rows[0]["told"]), (True, False, True), "the row carries the truth of the road")
        self.assertTrue(self.be.logs and self.be.logs[0][1] is True and "web was spending" in (self.be.logs[0][2] or ""),
                        "the row also rides the backend's log with the ring flag: the error center shows it")
        self.assertEqual(km._SPEND_GUARD[SID]["over"], True)
        # the latch: the same rate the next cycle, and a still-high rate the cycle after, say nothing more; it survives
        # a kernel restart (the dict cleared, re-seeded from the ledger's rows: MEDIUM 1) and a departure from the live map
        km._SPEND_GUARD.clear(); km._SPEND_GUARD_SEEDED[0] = False
        km._interrupt_clicked.pop(SID, None)
        self._tick(NOW + 5)
        self.assertEqual(km._SPEND_GUARD[SID]["over"], True, "seeded from the ledger: still over")
        km._spend_guard_tick(NOW + 6, {}, sessions=[], be=self.be, clients=self.clients, prices=PRICES)   # left the live map
        self.assertIn(SID, km._SPEND_GUARD, "a departed session keeps its latch")
        self._spend(NOW + 60, 150.0)                     # $900 an hour: under the ceiling, above the re-arm level
        self._tick(NOW + 60)
        self.assertEqual((len(self.be.interrupts), len(self.be.sends), len(self.toasts), len(self._rows())), (1, 1, 2, 1))
        self.assertEqual(km._SPEND_GUARD[SID]["over"], True, "held until the rate is under half the ceiling")
        # under half: the clearing is said where the crossing was, and the guard re-arms
        self._spend(NOW + 120, 40.0)                     # $240 an hour
        self._tick(NOW + 120)
        self.assertEqual(km._SPEND_GUARD[SID]["over"], False)
        self.assertEqual([r["kind"] for r in self._rows()], ["spend.ceiling", "spend.ceiling.cleared"])
        self.assertEqual(len(self.toasts), 4)
        self.assertEqual((self.toasts[2]["type"], self.toasts[2]["phase"]), ("spendCeiling", "under"))
        self.assertIn("web is back under the spend ceiling (about $240 an hour now).", self.toasts[2]["text"])
        self.assertEqual((len(self.be.interrupts), len(self.be.sends)), (1, 1), "a clearing sends the session nothing")
        # a second crossing is new information: it fires again
        self._spend(NOW + 180, 300.0)
        km._interrupt_clicked.pop(SID, None)              # the earlier stop settled long ago
        self._tick(NOW + 180)
        self.assertEqual((len(self.be.interrupts), len(self.be.sends)), (2, 2))
        self.assertEqual([r["kind"] for r in self._rows()], ["spend.ceiling", "spend.ceiling.cleared", "spend.ceiling"])

    def test_the_ceiling_file_governs_and_zero_disables(self):
        self._spend(NOW, 200.0)                          # $1,200 an hour
        Path(self.td.name, km.SPEND_CEILING_SETTING).write_text("0")
        jd._state_cache.clear()
        self._tick(NOW)
        self.assertEqual((self.be.interrupts, self.be.sends, self.toasts, self._rows()), ([], [], [], []), "0 disables")
        Path(self.td.name, km.SPEND_CEILING_SETTING).write_text("2000")
        jd._state_cache.clear()
        self._tick(NOW + 1)
        self.assertEqual(self.be.interrupts, [], "$1,200 an hour is under a $2,000 ceiling")
        Path(self.td.name, km.SPEND_CEILING_SETTING).write_text("500")
        jd._state_cache.clear()
        self._tick(NOW + 2)
        self.assertEqual(self.be.interrupts, [SID], "…and over a $500 one: the file is read at each check")
        self.assertIn("I set the line at $500 an hour", self.be.sends[0][1])
        self.assertIn("over the $500 an hour ceiling", self.toasts[0]["text"])

    def test_a_kernel_restart_does_not_fire_the_same_crossing_again(self):
        """The ledger holds a spend.ceiling row with no clearing after it: a fresh process reads it back and stays latched;
        a clearing row after it re-arms; the ledger's verdict never outranks one this life made."""
        self._spend(NOW, 200.0)
        self._tick(NOW)
        self.assertEqual(len(self.be.interrupts), 1)
        km._SPEND_GUARD.clear(); km._SPEND_GUARD_SEEDED[0] = False; km._interrupt_clicked.pop(SID, None)
        self._tick(NOW + 5)
        self.assertEqual((len(self.be.interrupts), len(self.be.sends), len(self._rows())), (1, 1, 1), "one fire across the restart")
        self.assertTrue(km._SPEND_GUARD[SID]["seeded"])
        self._spend(NOW + 120, 40.0); self._tick(NOW + 120)              # cleared: the row lands
        km._SPEND_GUARD.clear(); km._SPEND_GUARD_SEEDED[0] = False
        self._spend(NOW + 180, 300.0); km._interrupt_clicked.pop(SID, None); self._tick(NOW + 180)
        self.assertEqual(len(self.be.interrupts), 2, "a fresh process after a clearing row fires the new crossing")

    def test_an_unsettled_interrupt_is_not_pressed_again_and_the_sentence_says_so(self):
        self._spend(NOW, 200.0)
        km._interrupt_clicked[SID] = NOW - 5                # a stop is in flight (the ladder would climb on a second press)
        self._tick(NOW)
        self.assertEqual(self.be.interrupts, [], "not pressed again")
        self.assertEqual(len(self.be.sends), 1, "…but told")
        self.assertIn("; a stop was already in flight, and it has been told.", self.toasts[0]["text"])
        self.assertEqual((self._rows()[0]["stopped"], self._rows()[0]["stopping"], self._rows()[0]["told"]), (False, True, True))

    def test_the_sentence_states_what_the_backend_actually_did(self):
        self._spend(NOW, 200.0)
        self.be = FakeBackend(stops=False, accepts=False)   # detached, or no backend owns it: both refused
        self._tick(NOW)
        self.assertIn("; it could not be stopped or told (no backend owns it).", self.toasts[0]["text"])
        self.assertEqual((self._rows()[0]["stopped"], self._rows()[0]["told"]), (False, False))
        self.assertNotIn(SID, km._interrupt_clicked, "a refused interrupt stamps nothing")
        # the tick with no backend given resolves the session's OWN backend (MEDIUM 4), never the SDK one for everyone
        with mock.patch.object(km.Sessions, "backend_for", return_value=FakeBackend(stops=False, accepts=True)) as bf:
            km._SPEND_GUARD.clear(); km._SPEND_GUARD_SEEDED[0] = True
            self.toasts.clear()
            km._spend_guard_tick(NOW + 1, {SID: {"state": "working"}}, sessions=self.sessions, be=None, clients=self.clients, prices=PRICES)
            bf.assert_called_with(SID)
        self.assertIn("; it could not be stopped (the interrupt was refused: detached, or no backend owns it) but has been told.", self.toasts[0]["text"])

    def test_the_fire_road_is_pinned_to_the_shared_doors(self):
        import inspect
        fire = inspect.getsource(km._spend_guard_fire)
        self.assertIn("_send_or_park(be, sid, _spend_ceiling_body(rate, ceiling))", fire, "the machine sends' door, not a bare send")
        self.assertIn("be = Sessions.backend_for(sid)", fire, "the session's own backend")
        stop = inspect.getsource(km._spend_guard_stop)
        for piece in ("_interrupt_clicked[str(sid)] = now", "_suppress_session_retry(sid)", "_mark_views_dirty()"):
            self.assertIn(piece, stop, "the Stop button's road, whole: " + piece)
        self.assertIn('payload = json.dumps(dict(fields, type="spendCeiling", text=text))', inspect.getsource(km._spend_guard_toast))
        ui = open(os.path.join(ROOT, "ui", "webview", "render.ts")).read()
        i = ui.index('m.type === "spendCeiling"')
        handler = ui[i:ui.index("\n  }", i)]
        self.assertIn("warnToast(m.text);", handler)
        self.assertNotIn("failProvisional", handler, "never read as an in-flight create's verdict")
        self.assertIn("_subagent_transcripts(leaf)", inspect.getsource(km._spend_window_files), "the canonical recursive walk")

    def test_the_guard_never_starts_the_price_feed_fetch(self):
        """The cost view refreshes the remote price feed when its cache is stale; the guard runs on the pusher's path in
        every kernel, hermetic test kernels included, so it merges the table WITHOUT that refresh."""
        self._spend(NOW, 200.0)
        with mock.patch.object(km, "_refresh_remote_prices") as refresh:
            km._spend_guard_tick(NOW, {SID: {"state": "working"}}, sessions=self.sessions, be=self.be, clients=self.clients)
            km._spend_window_usd(self.leaf, NOW)
        refresh.assert_not_called()
        self.assertEqual(self.be.interrupts, [SID], "…and the crossing still fired on the merged table")
        with mock.patch.object(km, "_refresh_remote_prices") as refresh:
            km._model_prices(int(NOW))
        refresh.assert_called_once_with(int(NOW))

    def test_the_pusher_runs_the_guard_every_cycle_after_the_spend_pause_check(self):
        import inspect
        src = inspect.getsource(km._pusher_cycle_jobs)
        i, j = src.index("_auto_pause_on_spend_limit(now, live_map)"), src.index("_spend_guard_tick(now, live_map)")
        self.assertLess(i, j, "the guard runs in the tick jobs, after the spend-cap pause decision")
        self.assertIn('sys.stderr.write("spend-guard: %s\\n" % traceback.format_exc())', src, "guarded like every job")


if __name__ == "__main__":
    unittest.main()
