"""A timeline lane's last bar ends at the session's LAST ACTIVITY, never at the build clock (T324, the user
2026-09-10: nearly every lane's line ended at the same instant, read as every session stopping at once).

The idle atom synthesize_idle adds for a finished session's last state row spans to the parse clock, and a turn's
`end` is the newest atom end, so a finished session's last bar reached the build clock; the parse is cached until
the transcript moves, so for a dormant session that clock was the first build after the last kernel boot, the same
x for every idle lane. The bar's end is now the segment's last recorded EVENT. SYNTHETIC fixtures only (placeholder
UUIDs, invented text, a private synthetic sid)."""
import datetime
import json
import os
import tempfile
import unittest

from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_tl_lane_end", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "f324a001-1111-4222-8333-000000000001"
T0 = 1781100000                 # the session's one turn: the ask, the reply eleven seconds later, the Stop right after
T_STOP = T0 + 11
NOW = T0 + 3600                 # the kernel booted (and first built) an hour after that


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ask(t, uuid, text, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def _reply(t, uuid, text, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


class LaneEndsAtActivity(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []                                   # no host sleeps in the lab
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        self.pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        self.pdir.mkdir(parents=True)
        self.tpath = self.pdir / (SID + ".jsonl")
        (td / "names").mkdir()
        (td / "names" / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km._tmux_sessions)
        jd._rebind_state(td)
        jd.PROJECTS = proj
        km.NAMES = td / "names"
        (td / "states").mkdir()
        self.states = td / "states" / (SID + ".jsonl")
        self.live = {SID: {"state": "waiting", "since": T_STOP, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        km._tmux_sessions = lambda: self.live
        km._parse_cache.pop(str(self.tpath), None)
        with km._LANES_LOCK:
            km._lanes_memo.pop(SID, None)

    def tearDown(self):
        state, jd.PROJECTS, km.NAMES, km._tmux_sessions = self.saved
        jd._rebind_state(state)
        km._parse_cache.pop(str(self.tpath), None)
        self.td.cleanup()

    def _write(self, recs, states):
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(self.tpath, (T_STOP, T_STOP))
        self.states.write_text("".join(json.dumps(r) + "\n" for r in states))

    def _bars(self, now=NOW):
        tl = km.build_timeline(now, with_bars=True)
        lane = next(l for l in tl["sessions"] if l["id"] == SID)
        return lane, [km._expand_bar(b) for b in tl["turns"][SID]]

    def test_a_finished_session_s_last_bar_ends_at_its_stop_not_at_the_boot_an_hour_later(self):
        self._write([_ask(T0, "u1", "Where do we stand on the login flow?"),
                     _reply(T0 + 10, "a1", "The login flow is done and every test passes.", "u1")],
                    [{"t": T_STOP, "state": "waiting"}])
        lane, bars = self._bars()
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["start"], T0)
        self.assertLessEqual(bars[0]["end"], T_STOP, "the bar ends at the Stop, the last recorded event; it read %s, %d s after the activity, the build clock"
                             % (bars[0]["end"], bars[0]["end"] - T_STOP))
        self.assertGreaterEqual(bars[0]["end"], T0 + 10, "…and no earlier than the reply")
        self.assertFalse(bars[0].get("open"), "a finished turn is a closed bar")
        self.assertEqual(lane["since"], T_STOP, "the lane's since is the last state row, untouched")

    def test_two_sessions_finished_at_different_times_end_at_different_x_after_one_boot(self):
        # the board's symptom: with the parse clock as the end, both would have ended at NOW
        self._write([_ask(T0, "u1", "Add the notes table migration."),
                     _reply(T0 + 10, "a1", "Two migrations could go first; the notes one is in.", "u1")],
                    [{"t": T_STOP, "state": "waiting"}])
        _lane, bars = self._bars()
        first_end = bars[0]["end"]
        km._parse_cache.pop(str(self.tpath), None)
        with km._LANES_LOCK:
            km._lanes_memo.pop(SID, None)
        self._write([_ask(T0, "u1", "Add the notes table migration."),
                     _reply(T0 + 10, "a1", "Two migrations could go first; the notes one is in.", "u1"),
                     _ask(T0 + 900, "u2", "Now the index on it.", "a1"),
                     _reply(T0 + 920, "a2", "Indexed; the query plan reads the index.", "u2")],
                    [{"t": T_STOP, "state": "waiting"}, {"t": T0 + 900, "state": "working"}, {"t": T0 + 921, "state": "waiting"}])
        _lane, bars = self._bars()
        self.assertEqual(len(bars), 2)
        self.assertLessEqual(bars[-1]["end"], T0 + 921)
        self.assertNotEqual(first_end, bars[-1]["end"], "two sessions that stopped at different times end at different x")
        self.assertLess(abs(first_end - T_STOP), 2)

    def test_an_open_turn_on_a_live_lane_is_still_an_open_bar_that_reaches_the_live_edge(self):
        # no end_turn, the state log says working: the bar is OPEN (the view draws it to the live edge) and its
        # recorded end is the last event, not the clock
        self.live[SID] = dict(self.live[SID], state="working", since=T0)
        self._write([_ask(T0, "u1", "Cover the notes endpoint."),
                     _reply(T0 + 10, "a1", "Working through the three cases…", "u1", stop=None)],
                    [{"t": T0, "state": "working"}])
        _lane, bars = self._bars()
        self.assertEqual(len(bars), 1)
        self.assertTrue(bars[0].get("open"), "an unended turn on a live lane is the open bar")
        self.assertEqual(bars[0]["end"], T0 + 10)

    def test_a_follow_up_absorbed_mid_turn_leaves_no_hole_before_it_and_the_tail_still_ends_at_the_stop(self):
        # the review find on the first cut: a follow-up that lands while the turn is inside a tool call (the reply
        # stopped on tool_use, so the parse ABSORBS the new input into the same turn) cuts the turn into two
        # segments; the first piece must run to the follow-up's time, contiguous, not retract to its last record
        # (on the live echo road that follow-up is stamped at send time, minutes ahead of the tool result, and the
        # retraction was a hole in a working lane until the result landed); the tail piece ends at the Stop
        self._write([_ask(T0, "u1", "Cover the notes endpoint."),
                     _reply(T0 + 10, "a1", "Running the three cases now.", "u1", stop="tool_use"),
                     _ask(T0 + 20, "f1", "Add the pagination case too.", "a1"),
                     _reply(T0 + 30, "a2", "All four cases pass.", "f1")],
                    [{"t": T0, "state": "working"}, {"t": T0 + 31, "state": "waiting"}])
        self.live[SID] = dict(self.live[SID], since=T0 + 31)
        _lane, bars = self._bars()
        self.assertEqual([b["start"] for b in bars], [T0, T0 + 20], "one turn, two pieces: cut at the absorbed follow-up")
        self.assertEqual(bars[0]["end"], T0 + 20, "the first piece runs to the follow-up, no hole before it (it read %s)" % bars[0]["end"])
        self.assertGreaterEqual(bars[1]["end"], T0 + 30)
        self.assertLessEqual(bars[1]["end"], T0 + 31, "the tail piece ends at the Stop, not the boot an hour later (it read %s)" % bars[1]["end"])
        self.assertFalse(any(b.get("open") for b in bars))

    def test_the_lane_s_last_activity_is_the_bar_end_too(self):
        # last_t feeds the lane `since` when the liveness snapshot has none: the same event, never the clock
        self._write([_ask(T0, "u1", "Write the notes API page."),
                     _reply(T0 + 10, "a1", "The page is drafted.", "u1")],
                    [{"t": T_STOP, "state": "waiting"}])
        sess = km._parse(str(self.tpath), SID, NOW)
        bars, _seg_ends, last_t, *_rest = km._lane_segments(SID, sess, None, {}, True, None)
        self.assertLessEqual(last_t, T_STOP, "last_t is the last recorded event, not the parse clock (%s)" % last_t)
        self.assertEqual(last_t, max(b["end"] for b in (km._expand_bar(b) for b in bars)))


if __name__ == "__main__":
    unittest.main()
