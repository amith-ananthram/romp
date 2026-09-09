#!/usr/bin/env python3
"""The timeline's DEAD-LANE memo (2026-09-08): a lane whose session is dead is re-derived only when an input moves.

The full timeline build ran every pusher cycle (the fleet signature's 5 s bucket turns over faster than a 6 s
cycle) and re-parsed every lane's transcript and goals each time, dead lanes included. Now a dead lane's
parse-derived parts (bars, compactions, the work end, its judging marks) are served from a memo keyed on
every file they read and the host's recorded suspensions, its parse is dropped from _parse_cache once cached
(the resident-memory lever), and the served frame is byte-identical to a rebuilt one: the judging marks are
derived once at horizon zero, stamped with the value the horizon test compares, and filtered per build on
exactly that. The bars encoder reuses the strings of entry objects it already encoded.

Synthetic transcript, states and captions under a temp root; a placeholder sid; the lane is dead because the
liveness snapshot is empty.

DerivationSplit, below the dead-lane classes, covers the judging derivation's split from the horizon assembly
(_derive_judging_marks and _judging_assemble against a private copy of the one-pass form as the oracle); it sits
in this module because the dead-lane memo's stamped marks are that split's other caller."""
import ast
import inspect
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_lane_memo", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1_800_000_000
T0 = NOW - 3600


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rec(kind, t, uuid, parent, text):
    if kind == "user":
        return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
                "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def move_ctime(path):
    """Move a file's ctime and nothing else: flip its mode between 0o600 and 0o644, checking the stat after
    each chmod, until the ctime differs (a coarse filesystem clock can hand two chmods one timestamp). mtime,
    size and inode stand. Bounded at 5 s: a filesystem that never ticks ctime under chmod fails the test
    loudly rather than passing it."""
    before = cur = os.stat(path)
    deadline = time.monotonic() + 5
    while cur.st_ctime_ns == before.st_ctime_ns:
        if time.monotonic() > deadline:
            raise AssertionError("ctime did not move under chmod within 5 s")
        os.chmod(path, 0o644 if (cur.st_mode & 0o777) == 0o600 else 0o600)
        cur = os.stat(path)
    return cur


class DeadLaneMemo(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / km.jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.recs = [_rec("user", T0, "u1", None, "run the long benchmark"),
                     _rec("assistant", T0 + 10, "a1", "u1", "Launched it.")]
        self.tpath = pdir / (SID + ".jsonl")
        self._write(self.recs)
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR, km.jd.STATE, km.NAMES, km._tmux_sessions)
        km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR = names, proj, td / "goals", td / "captions"
        km.jd.STATE = td
        km.NAMES = names
        km._tmux_sessions = lambda: {}                 # NOBODY is live: the lane is a dead one within the window
        (td / "states").mkdir(); (td / "captions").mkdir(); (td / "goals").mkdir()
        (td / "goals" / (SID + ".json")).write_text(json.dumps({"nodes": {}, "status": {}}))   # the judging marks need a store
        self.caps = td / "captions" / (SID + ".jsonl")
        km._parse_cache.pop(str(self.tpath), None)
        km._dead_lane_memo.clear()
        km._delta_entry_memo.clear()

    def tearDown(self):
        (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR, km.jd.STATE, km.NAMES, km._tmux_sessions) = self.saved
        km._dead_lane_memo.clear()
        km._downtime[:] = []
        self.td.cleanup()

    def _write(self, recs):
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(self.tpath, (NOW - 30, NOW - 30))    # recently touched: a lane within the 12 h window

    def _build(self, now=NOW):
        return km.build_timeline(now, {}, with_bars=True)

    def _lane(self, tl):
        return next(s for s in tl["sessions"] if s["id"] == SID)

    def test_the_second_build_serves_the_lane_without_a_parse_and_drops_the_parse(self):
        parses = []
        real = km._parse
        km._parse = lambda path, sid, now: (parses.append(path), real(path, sid, now))[1]
        try:
            tl1 = self._build()
            self.assertEqual(parses, [str(self.tpath)], "the first build parses the dead lane once")
            self.assertNotIn(str(self.tpath), km._parse_cache, "and drops the parse once the lane is cached")
            self.assertIn(SID, km._dead_lane_memo)
            tl2 = self._build()
            self.assertEqual(len(parses), 1, "the second build serves the lane: no parse")
            self.assertEqual(tl1["turns"][SID], tl2["turns"][SID])
            self.assertEqual(self._lane(tl1), self._lane(tl2), "a served lane is the rebuilt lane, byte for byte")
            self.assertEqual([m for m in tl1["judging"] if m["sid"] == SID], [m for m in tl2["judging"] if m["sid"] == SID])
            self.assertFalse(any("_h" in m for m in tl2["judging"]), "the horizon stamp never reaches the wire")
        finally:
            km._parse = real

    def test_a_moved_transcript_re_derives_the_lane(self):
        self._build()
        recs = self.recs + [_rec("user", T0 + 600, "u2", "a1", "and the cap?"),
                            _rec("assistant", T0 + 610, "a2", "u2", "Two minutes.")]
        self._write(recs)
        os.utime(self.tpath, (NOW - 20, NOW - 20))
        tl = self._build()
        self.assertEqual(len(tl["turns"][SID]), 2, "the new turn is drawn")
        self.assertEqual(km._dead_lane_memo[SID][1]["bars"], tl["turns"][SID])

    def test_the_memo_keeps_stamped_marks_and_the_wire_never_sees_the_stamp(self):
        cap_t = NOW - km.TL_HORIZON + 30
        self.caps.write_text(json.dumps({"id": "u1", "t": cap_t, "caption": "launched the benchmark",
                                         "grain": "segment"}) + "\n")
        tl = self._build(NOW)
        marks = km._dead_lane_memo[SID][1]["marks"]
        self.assertEqual([(m["judge"], m["_h"]) for m in marks], [("captioner", cap_t)], "derived once, stamped")
        self.assertFalse(any("_h" in m for m in tl["judging"]), "the stamp never reaches the wire")

    def test_every_keyed_input_re_derives_the_lane_when_it_moves(self):
        """The key is every file the parse-derived parts read, not the transcript alone (the review of the
        first batch found a key pinned on one component): touching each one changes the memo's key and the
        lane is derived again."""
        td = Path(self.td.name)
        self._build()
        key0 = km._dead_lane_memo[SID][0]
        inputs = [td / "states" / (SID + ".jsonl"), td / "goals" / (SID + ".json"),
                  td / "overrides" / (SID + ".jsonl"), td / "captions" / (SID + ".jsonl"),
                  td / "archive" / (SID + ".json"), td / "session-flags.json"]
        seen = {key0}
        for i, p in enumerate(inputs):
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == SID + ".json" and p.parent.name == "goals":
                p.write_text(json.dumps({"nodes": {}, "status": {}, "touched": i}))
            elif p.suffix == ".json":
                p.write_text(json.dumps({"touched": i}))
            else:
                p.write_text("")
            os.utime(p, (NOW - 10 + i, NOW - 10 + i))
            self._build()
            key = km._dead_lane_memo[SID][0]
            self.assertNotIn(key, seen, "%s moved but the key did not" % p.name)
            seen.add(key)
        self._build()
        self.assertEqual(km._dead_lane_memo[SID][0], key, "nothing moved: the key stands")

    def test_a_lane_whose_goals_store_cannot_be_read_is_never_cached(self):
        """A goals FAULT is a store that cannot be read (an OSError; malformed bytes are healed by the loader):
        the lane renders without goal-derived data, complains, and is derived again on every build rather
        than served from a memo that would silence the fault."""
        store = Path(self.td.name) / "goals" / (SID + ".json")
        store.chmod(0)
        try:
            self._build()
            self.assertNotIn(SID, km._dead_lane_memo, "a faulted store stays loud on every build, never served stale")
        finally:
            store.chmod(0o600)
        self._build()
        self.assertIn(SID, km._dead_lane_memo, "readable again: cached like any other dead lane")

    def test_a_live_lane_is_not_memoized(self):
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        km.build_timeline(NOW, km._tmux_sessions(), with_bars=True)
        self.assertNotIn(SID, km._dead_lane_memo)

    def test_a_chmod_alone_moves_the_transcripts_stat_key(self):
        """A chmod, chown or rename moves a file's ctime while its mtime, size and inode stand, so the stat
        key carries st_ctime_ns as its fourth member: the repair of a read the memo cached as the empty lane is
        visible to the key."""
        p = str(self.tpath)
        k0 = km._stat_key(p)
        move_ctime(p)
        k1 = km._stat_key(p)
        self.assertNotEqual(k0, k1, "ctime is in the key: a chmod or a rename moves it")
        self.assertEqual(k0[:3], k1[:3], "mtime, size and inode stood")
        self.assertIsNone(km._stat_key(p + ".absent"))

    def test_a_failed_parse_is_served_once_cached_and_re_attempted_when_the_transcripts_stat_moves(self):
        """A transcript that cannot be read parses as the empty lane (the read layer returns no records on an
        OSError, silently) and a parse that raises leaves the same empty lane plus one stderr line; either is
        cached like any other lane (a dead transcript has no writer, so the result would repeat). The lane is
        derived again only when a keyed file moves, and a chmod or chown that repairs the read moves neither
        mtime, size nor inode: the ctime in the key is what makes the repair visible. The vehicle here is a
        raising stub, the path with an observable complaint; the chmod only moves the ctime."""
        parses, failing = [], [True]
        real = km._parse

        def parse(path, sid, now):
            parses.append(path)
            if failing[0]:
                raise OSError("unreadable")
            return real(path, sid, now)
        km._parse = parse
        km._BARS_COMPLAINED.pop((SID, "parse"), None)   # the complaint latch: one line per distinct cause
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tl1 = self._build()
                tl2 = self._build()
            self.assertEqual(len(parses), 1, "parsed once: the failed parse is cached as the empty lane")
            self.assertEqual(tl1["turns"][SID], [])
            self.assertEqual(tl2["turns"][SID], [], "served as the empty lane it drew")
            self.assertEqual(err.getvalue().count("timeline bars:"), 1, "one stderr line, none when served")
            self.assertIn(SID, km._dead_lane_memo)
            failing[0] = False
            move_ctime(self.tpath)
            tl3 = self._build()
            self.assertEqual(len(parses), 2, "the transcript's stat moved: the parse is attempted again")
            self.assertEqual(len(tl3["turns"][SID]), 1, "readable again: the bar is drawn")
            self._build()
            self.assertEqual(len(parses), 2, "and the repaired lane is served like any other")
        finally:
            km._parse = real

    def test_a_suspension_recorded_after_the_lane_was_cached_re_derives_it(self):
        """_awake_spans excises every recorded suspension from each segment's span, reading the in-memory list
        (its jsonl mirror is appended best-effort, so the list, not the file, is the input), and the list
        grows at run time when the producer's tick detects a sleep, on a thread other than the build's. The
        key carries the list: a nap inside a cached segment splits its bar on the very next build; a
        different nap of the same count is a different key; a nap outside every segment re-derives to equal
        bars; the same list rebound is served."""
        parses = []
        real = km._parse
        km._parse = lambda path, sid, now: (parses.append(path), real(path, sid, now))[1]
        try:
            self._build()
            key0 = km._dead_lane_memo[SID][0]
            self.assertEqual(len(parses), 1)
            km._downtime[:] = [(T0 + 3, T0 + 7)]            # a nap inside the one segment, an atom on each side
            tl = self._build()
            self.assertEqual(len(tl["turns"][SID]), 2, "the bar is cut at the nap on the very next build")
            self.assertEqual(len(parses), 2, "derived again, not served")
            key1 = km._dead_lane_memo[SID][0]
            self.assertNotEqual(key1, key0, "the suspensions are in the key")
            km._downtime[:] = [(T0 + 2, T0 + 8)]            # a different nap, the same count
            tl2 = self._build()
            self.assertNotEqual(tl2["turns"][SID], tl["turns"][SID], "a different nap cuts the bar elsewhere")
            key2 = km._dead_lane_memo[SID][0]
            self.assertNotEqual(key2, key1, "a nap of the same count is a different key")
            km._downtime.append((NOW - 20000, NOW - 19000))  # a sleep outside every segment: equal bars, a new key
            tl3 = self._build()
            key3 = km._dead_lane_memo[SID][0]
            self.assertNotEqual(key3, key2)
            self.assertEqual(tl3["turns"][SID], tl2["turns"][SID], "a nap outside every segment: the same bars")
            self.assertEqual(len(parses), 4)
            km._downtime[:] = list(km._downtime)            # the same content, rebound
            self._build()
            self.assertEqual(km._dead_lane_memo[SID][0], key3, "the same suspensions: the same key")
            self.assertEqual(len(parses), 4, "served")
        finally:
            km._parse = real


class HorizonFilterIsExact(unittest.TestCase):
    """The cached marks filtered on their stamped compare value are the marks a fresh derivation at that
    horizon would append, mark for mark: the diary and distiller marks are compared on their EVIDENCE time
    but emitted at the segment's work END, so a filter on the emitted time would admit a mark the fresh
    derivation drops (evidence before the horizon, work end after it). Pure functions, synthetic inputs."""

    def _inputs(self):
        h = 1_700_000_000
        caps = {"c%d" % i: {"id": "c%d" % i, "t": h - 100 + i * 50, "caption": "cap %d" % i, "grain": "segment"} for i in range(6)}
        goals = {"nodes": {
            "g1": {"t": h - 40, "mt": h + 10, "text": "old mint, done after the horizon",
                   "log": [{"src": "closer", "kind": "done", "ev_t": h - 5, "why": "evidence just before the horizon"}]},
            "g2": {"t": h + 20, "mt": h + 30, "text": "new mint", "distilledMt": h - 20, "briefedMt": h + 40},
        }}
        seg_ends = {h - 5: h + 300, h - 20: h + 400}      # completion marks land at the work END, after the horizon
        return h, caps, goals, seg_ends

    def test_filtered_stamped_marks_equal_a_fresh_derivation(self):
        h, caps, goals, seg_ends = self._inputs()
        for t0 in (h - 1000, h - 10, h, h + 15, h + 35, h + 1000):
            fresh = []
            km._derive_judging("s", caps, goals, t0, fresh, seg_ends)
            stamped = []
            km._derive_judging("s", caps, goals, 0, stamped, seg_ends, stamp=True)
            self.assertEqual(km._dead_lane_marks(stamped, t0), fresh, "horizon %+d" % (t0 - h))
        fresh = []
        km._derive_judging("s", caps, goals, h, fresh, seg_ends)
        self.assertFalse(any(m["judge"] == "closer" for m in fresh), "evidence before the horizon: dropped")
        self.assertTrue(any(m["judge"] == "distiller" and m["kind"] == "brief" for m in fresh))

    def test_the_stamp_is_private(self):
        h, caps, goals, seg_ends = self._inputs()
        stamped = []
        km._derive_judging("s", caps, goals, 0, stamped, seg_ends, stamp=True)
        self.assertTrue(stamped and all("_h" in m for m in stamped))
        self.assertFalse(any("_h" in m for m in km._dead_lane_marks(stamped, 0)))


class EntryEncodeMemo(unittest.TestCase):
    def test_an_entry_object_seen_last_split_is_not_encoded_again(self):
        km._delta_entry_memo.clear()
        sep = km._DELTA_SEP
        b1, b2 = {"id": "b1", "start": 1, "end": 2}, {"id": "b2", "start": 3, "end": 4}
        ents1, _ = km._delta_split("dictlist:id", {"S": [b1, b2]}, memo_key=("bars", "turns"))
        b3 = {"id": "b3", "start": 5, "end": 6}
        ents2, _ = km._delta_split("dictlist:id", {"S": [b1, b3]}, memo_key=("bars", "turns"))
        self.assertIs(ents2["S" + sep + "b1"][1], ents1["S" + sep + "b1"][1], "the same object: the same string, not re-encoded")
        self.assertEqual(json.loads(ents2["S" + sep + "b3"][1]), b3)
        self.assertNotIn(id(b2), km._delta_entry_memo[("bars", "turns")], "the memo is rebuilt from THIS split: no growth")
        b1b = dict(b1)                                   # equal content, a NEW object: encoded afresh (identity, never equality)
        ents3, _ = km._delta_split("dictlist:id", {"S": [b1b]}, memo_key=("bars", "turns"))
        self.assertEqual(ents3["S" + sep + "b1"][1], ents1["S" + sep + "b1"][1])
        self.assertIsNot(ents3["S" + sep + "b1"][1], ents1["S" + sep + "b1"][1])

    def test_the_wire_fill_hands_the_collection_key_down(self):
        import inspect
        self.assertIn("_delta_split(kind, value, memo_key=(ftype, name))", inspect.getsource(km._delta_parts))


# ── the judging derivation split (_derive_judging_marks + _judging_assemble): what the one-pass form emitted ──
LIVE_SID = "44444444-5555-6666-7777-888888888801"      # private synthetic sids: the classes below mint goal stores, and a
LIVE_SID2 = "44444444-5555-6666-7777-888888888802"     # store minted under the shared placeholder sid can be re-flagged by
LIVE_PARENT = "44444444-5555-6666-7777-888888888803"   # another module's journaled overrides (load_goals replays them)
LIVE_CHILD = "44444444-5555-6666-7777-888888888804"
LIVE_SID3 = "44444444-5555-6666-7777-888888888805"


def _one_pass_judging(sid, caps, goals, t0, out, seg_ends=None):
    """_derive_judging as it stood in one pass, before the derivation was split from the horizon assembly: the
    equality oracle for DerivationSplit (the horizon compared inline, the caption cap taken inline)."""
    endt = (lambda tt: seg_ends.get(tt, tt)) if seg_ends else (lambda tt: tt)
    caps_in = sorted((c for c in caps.values() if c.get("t") and c["t"] >= t0), key=lambda c: c["t"])
    for c in caps_in[-km.JUDGE_CAP_LIMIT:]:
        out.append({"judge": "captioner", "sid": sid, "t": c["t"],
                    "kind": c.get("grain", "segment"), "text": c.get("caption", "")})
    for n in goals.get("nodes", {}).values():
        t = n.get("t")
        if not t:
            continue
        text = n.get("text", "")
        mt = n.get("mt") or t
        go = n.get("groupOp")
        if isinstance(go, dict) and (go.get("t") or 0) >= t0:
            out.append({"judge": "grouper", "sid": sid, "t": go["t"],
                        "kind": go.get("kind") or "group", "text": text})
        if n.get("origin"):
            if t >= t0:
                out.append({"judge": "courier", "sid": sid, "t": t, "kind": "plant", "text": text})
        elif n.get("umbrella"):
            if mt >= t0:
                out.append({"judge": "grouper", "sid": sid, "t": mt, "kind": "group", "text": text})
        elif t >= t0:
            out.append({"judge": "planner", "sid": sid, "t": t,
                        "kind": ("mint" if not n.get("parentId") else "sub"), "text": text})
        for _e in (n.get("log") or []):
            if _e.get("synth") or (_e.get("ev_t") or 0) < t0:
                continue
            if _e.get("src") in ("planner", "closer") and _e.get("kind") in ("done", "block"):
                out.append({"judge": _e["src"] if _e["src"] == "planner" else "closer", "sid": sid,
                            "t": endt(_e["ev_t"]),
                            "kind": ("done" if _e["src"] == "planner" else "close") if _e["kind"] == "done" else "block",
                            "text": _e.get("why") or text})
        if n.get("distilledMt") and n["distilledMt"] >= t0:
            out.append({"judge": "distiller", "sid": sid, "t": endt(n["distilledMt"]), "kind": "distill",
                        "text": n.get("summary") or text})
        if n.get("briefedMt") and n["briefedMt"] >= t0:
            out.append({"judge": "distiller", "sid": sid, "t": endt(n["briefedMt"]), "kind": "brief",
                        "text": n.get("blockSummary") or text})
    try:
        arch = json.loads((km.jd.STATE / "archive" / (sid + ".json")).read_text(errors="replace"))
        if arch.get("t") and arch["t"] >= t0:
            out.append({"judge": "archiver", "sid": sid, "t": arch["t"], "kind": "index",
                        "text": arch.get("headline", "")})
    except (OSError, ValueError):
        pass


class DerivationSplit(unittest.TestCase):
    """_derive_judging_marks derives every mark with no horizon and no clock; _judging_assemble applies the horizon
    and the caption cap per build. Together they emit what the one-pass form emitted, mark for mark, at every
    horizon, and the wrapper _derive_judging still answers its callers."""

    def setUp(self):
        self._saved_state = km.jd.STATE
        self._td = tempfile.mkdtemp()
        km.jd._rebind_state(Path(self._td))
        self.now = 1_781_100_000

    def tearDown(self):
        km.jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def fixture(self, timeless=False):
        now = self.now
        caps = {"c%d" % i: {"id": "c%d" % i, "grain": "segment" if i % 2 else "turn", "t": now - 9000 + i * 100,
                            "caption": "c%d" % i} for i in range(km.JUDGE_CAP_LIMIT + 10)}
        caps["tie"] = {"id": "tie", "grain": "segment", "t": now - 9000 + 100, "caption": "tie"}   # an equal t: stable order
        caps["none"] = {"id": "none", "grain": "segment", "caption": "no t"}
        nodes = {
            "g1": {"id": "g1", "parentId": None, "t": now - 8000, "mt": now - 7000, "text": "Top",
                   "log": [{"src": "planner", "kind": "done", "ev_t": now - 7000, "why": "shipped"},
                           {"src": "closer", "kind": "done", "ev_t": now - 6500},
                           {"src": "closer", "kind": "block", "ev_t": now - 6000, "why": "waiting"},
                           {"src": "planner", "kind": "done", "ev_t": now - 5000, "synth": True},
                           {"src": "user", "kind": "done", "ev_t": now - 4000}],
                   "distilledMt": now - 7000, "summary": "the takeaway", "briefedMt": now - 6000, "blockSummary": "the brief"},
            "g2": {"id": "g2", "parentId": "g1", "t": now - 7500, "text": "Step"},
            "g3": {"id": "g3", "parentId": None, "t": now - 3000, "text": "Planted", "origin": {"peer": "x"}},
            "g4": {"id": "g4", "parentId": None, "t": now - 2000, "mt": now - 1500, "text": "Umbrella", "umbrella": True},
            "g5": {"id": "g5", "parentId": None, "t": now - 1000, "text": "Merged", "groupOp": {"t": now - 900, "kind": "merge"}},
            "g6": {"id": "g6", "parentId": None, "text": "no t"},
        }
        if timeless:
            nodes["g7"] = {"id": "g7", "parentId": None, "t": now - 500, "text": "timeless op", "groupOp": {"kind": "retitle"},
                           "log": [{"src": "planner", "kind": "done"}]}
        km.jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.ARCHDIR / (LIVE_SID + ".json")).write_text(json.dumps({"t": now - 300, "headline": "h"}))
        seg_ends = {now - 7000: now - 6900, now - 6000: now - 5900}
        return caps, {"nodes": nodes}, seg_ends

    def test_equal_to_the_one_pass_form_at_every_horizon(self):
        caps, goals, seg_ends = self.fixture()
        # now-6950 and now-5950 fall inside a completion's evidence-to-work-end window (seg_ends moves now-7000 to
        # now-6900 and now-6000 to now-5900): a filter on the plotted time instead of the evidence time shows there
        for t0 in (0, self.now - 100000, self.now - 6950, self.now - 6500, self.now - 6000, self.now - 5950,
                   self.now - 1000, self.now - 250, self.now + 1):
            for se in (seg_ends, None):
                want, got = [], []
                _one_pass_judging(LIVE_SID, caps, goals, t0, want, se)
                km._derive_judging(LIVE_SID, caps, goals, t0, got, se)
                self.assertEqual(json.dumps(got), json.dumps(want), "t0=%r seg_ends=%r" % (t0, se))
                cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, se)
                out = []
                km._judging_assemble(cap_marks, other, t0, out)
                self.assertEqual(json.dumps(out), json.dumps(want))
        self.assertEqual(len([m for m in want if m["judge"] == "captioner"]), 0, "the last horizon dropped every caption")

    def test_a_timeless_group_op_or_diary_row_is_dropped_as_the_one_pass_form_dropped_it(self):
        caps, goals, seg_ends = self.fixture(timeless=True)
        for t0 in (self.now - 100000, self.now - 250):
            want, got = [], []
            _one_pass_judging(LIVE_SID, caps, goals, t0, want, seg_ends)
            km._derive_judging(LIVE_SID, caps, goals, t0, got, seg_ends)
            self.assertEqual(json.dumps(got), json.dumps(want))
            self.assertFalse(any(m["kind"] == "retitle" for m in got))

    def test_the_marks_are_derived_once_and_filtered_per_horizon(self):
        caps, goals, seg_ends = self.fixture()
        cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, seg_ends)
        self.assertEqual(len(cap_marks), km.JUDGE_CAP_LIMIT + 11, "every timed caption, no cap yet")
        self.assertEqual([m["t"] for m in cap_marks], sorted(m["t"] for m in cap_marks))
        fts = [ft for ft, m in other]
        self.assertIn(self.now - 7000, fts, "a completion's filter time is the diary's ev_t")
        done = next(m for ft, m in other if m["judge"] == "planner" and m["kind"] == "done")
        self.assertEqual(done["t"], self.now - 6900, "while its plotted t is the segment's work end")
        out = []
        km._judging_assemble(cap_marks, other, self.now - 6950, out)      # a horizon between the two
        kinds = {(m["judge"], m["kind"]) for m in out}
        self.assertNotIn(("planner", "done"), kinds, "filtered on the evidence time (before the horizon), not the plotted end")
        self.assertNotIn(("distiller", "distill"), kinds, "the distiller's mark likewise (distilledMt before the horizon)")
        self.assertIn(("closer", "close"), kinds, "a completion whose evidence is after the horizon stays")

    def test_the_stamped_assembly_copies_the_shared_marks(self):
        # the pairs are a memo's objects, shared into every unstamped build: the stamp lands on copies only
        caps, goals, seg_ends = self.fixture()
        cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, seg_ends)
        stamped = []
        km._judging_assemble(cap_marks, other, 0, stamped, stamp=True)
        self.assertTrue(stamped and all("_h" in m for m in stamped))
        self.assertFalse(any("_h" in m for m in cap_marks) or any("_h" in m for ft, m in other), "the memo's marks stay clean")
        self.assertFalse(any(any(s is m for m in cap_marks) or any(s is m for ft, m in other) for s in stamped))
        for t0 in (self.now - 6500, self.now - 250):
            fresh = []
            km._judging_assemble(cap_marks, other, t0, fresh)
            self.assertEqual(km._dead_lane_marks(stamped, t0), fresh, "filtered on the stamp: the fresh assembly at %d" % t0)

    def test_the_derivation_reads_no_clock(self):
        class NoClock:
            def time(self):
                raise AssertionError("the derivation read the clock")

            def __getattr__(self, name):
                return getattr(time, name)
        caps, goals, seg_ends = self.fixture()
        real = km.time
        km.time = NoClock()
        try:
            cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, seg_ends)
        finally:
            km.time = real
        self.assertTrue(cap_marks and other)
        tree = ast.parse(inspect.getsource(km._derive_judging_marks))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"now", "time", "TL_HORIZON", "JUDGE_CAP_LIMIT"}, repr(names))
        self.assertIn("JUDGE_CAP_LIMIT", inspect.getsource(km._judging_assemble), "the cap is read at assembly")


if __name__ == "__main__":
    unittest.main()
