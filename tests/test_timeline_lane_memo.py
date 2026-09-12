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
in this module because the dead-lane memo's stamped marks are that split's other caller. The classes on
LaneMemoBase cover the LIVE-lane memo, which shares the lane loop and the /perf block with the dead-lane memo: a
fresh state root per test through jd._rebind_state, one live lane, and private synthetic sids because those
classes mint goal stores."""
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
from romp_load import load_source
from fs_clock import move_ctime   # noqa: E402  the shared test helper, on the path the line above put there
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_lane_memo", os.path.join(BIN, "romp-kernel"))

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
        self.saved = (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR, km.jd.STATE, km.NAMES, km._live_map)
        km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR = names, proj, td / "goals", td / "captions"
        km.jd.STATE = td
        km.NAMES = names
        km._live_map = lambda: {}                 # NOBODY is live: the lane is a dead one within the window
        (td / "states").mkdir(); (td / "captions").mkdir(); (td / "goals").mkdir()
        (td / "goals" / (SID + ".json")).write_text(json.dumps({"nodes": {}, "status": {}}))   # the judging marks need a store
        self.caps = td / "captions" / (SID + ".jsonl")
        km._parse_cache.pop(str(self.tpath), None)
        km._dead_lane_memo.clear()
        km._delta_entry_memo.clear()

    def tearDown(self):
        (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR, km.jd.STATE, km.NAMES, km._live_map) = self.saved
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
        km._live_map = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        km.build_timeline(NOW, km._live_map(), with_bars=True)
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


# ── the LIVE-lane memo: a live lane's segment part is derived once and served while its inputs stand ──
def _live_row(since):
    return {"state": "waiting", "since": since, "model": "", "effort": "", "context": None,
            "compactPct": None, "color": "#336699", "mode": ""}


def _zero(stats):
    for k in list(stats):
        stats[k] = 0


class LaneMemoBase(unittest.TestCase):
    """A fresh state root per test (jd._rebind_state, so each test's shared store view is its own and its
    override journal dies with the root), the memos emptied, one LIVE lane with one finished turn. The band
    carries this build's own artifact marks (_run_judging stubbed to hand them through), read from the frame's
    compact per-lane judging map (j = judge, kd = kind, x = text)."""

    def setUp(self):
        self._saved_state, self._saved_proj, self._saved_names = km.jd.STATE, km.jd.PROJECTS, km.NAMES
        self._td = tempfile.mkdtemp()
        km.jd._rebind_state(Path(self._td))
        km.jd.PROJECTS = Path(self._td) / "projects"
        km.NAMES = km.jd.NAMES
        km.jd._discover_cache.clear(); km.jd._PARSE_CACHE.clear(); km.jd._CHAIN_MEMO.clear()
        km._parse_cache.clear(); km._BARS_COMPLAINED.clear()
        km._dead_lane_memo.clear(); km._delta_entry_memo.clear()
        km._downtime[:] = []
        self._saved = {nm: getattr(km, nm) for nm in (
            "_sdk", "_run_judging", "TL_HORIZON", "JUDGE_CAP_LIMIT", "_LANES_MEMO_MAX", "_lane_segments", "_segs_seam",
            "_derive_judging_marks", "_judging_assemble", "_captions", "_parse", "_merge_live_atoms", "_bind_message_execs")}
        self._saved_backend = km.Sessions.backend_for
        self._saved_file_key = km.jd._file_key
        km._lanes_memo.clear(); _zero(km._lanes_stats)
        km._sdk = lambda: None
        km._run_judging = lambda t0, alive, semantic: [dict(m) for m in semantic]
        self.now = int(time.time())
        self.t0 = self.now - 600
        self.cdir = str(Path(self._td) / "work")
        self.proj = km.jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        self.recs = [_rec("user", self.t0, "u1", None, "make the retry loop back off"),
                     _rec("assistant", self.t0 + 20, "a1", "u1", "Added exponential backoff.")]
        self.add_lane(LIVE_SID, "web", self.recs)

    def tearDown(self):
        for nm, v in self._saved.items():
            setattr(km, nm, v)
        km.Sessions.backend_for = self._saved_backend
        km.jd._file_key = self._saved_file_key
        km.jd._SHARED_OFF[0] = False
        km._lanes_memo.clear(); _zero(km._lanes_stats)
        km._dead_lane_memo.clear(); km._downtime[:] = []
        km.jd._rebind_state(self._saved_state)
        km.jd.PROJECTS = self._saved_proj
        km.NAMES = self._saved_names
        shutil.rmtree(self._td, ignore_errors=True)

    # fixtures
    def tpath(self, sid=LIVE_SID):
        return self.proj / (sid + ".jsonl")

    def add_lane(self, sid, name, recs):
        (km.jd.NAMES / sid).write_text("%s\t%s\n" % (name, self.cdir))
        self.tpath(sid).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km.jd._discover_cache.clear()

    def append_records(self, recs, sid=LIVE_SID):
        with self.tpath(sid).open("a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))

    def seg_id(self, sid=LIVE_SID):
        p = str(self.tpath(sid))
        session = km.em.parse_session(p, rompuuid=sid, candidate_files=[p], now=self.now)
        return km.em.segments(session["turns"][0])[0]["id"]

    def mint_store(self, sid=LIVE_SID, text="Back off the retries", t=None):
        """A store with one minted top, published by save_goals: a FrozenStore from load_goals_shared after."""
        s = km.jd.load_goals(sid)
        km.jd.apply_plan(s, "s1", t or self.t0, [{"do": "mint", "why": "x", "text": text}], [])
        km.jd.rollup_status(s, session_closed=False)
        km.jd.save_goals(sid, s)
        return s

    def write_archive(self, t, headline, sid=LIVE_SID):
        km.jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.ARCHDIR / (sid + ".json")).write_text(json.dumps({"t": t, "headline": headline}))

    def live_map(self, *sids):
        return {s: _live_row(self.now - 100) for s in sids}

    def build(self, live_map=None, with_bars=True, live_only=False, now=None):
        return km.build_timeline(self.now if now is None else now, self.live_map(LIVE_SID) if live_map is None else live_map,
                                 with_bars=with_bars, live_only=live_only)

    def stats(self):
        return km._lanes_memo_report()

    def outcomes(self):
        st = self.stats()
        return {k: st[k] for k in ("hit", "miss", "live_tail", "complain_skip", "unshared_skip")}

    def dead(self):
        st = self.stats()
        return {k: st[k] for k in ("dead_serve", "dead_miss", "dead_failed_serve")}

    def lane(self, tl, sid=LIVE_SID):
        return next(l for l in tl["sessions"] if l["id"] == sid)

    def marks(self, tl, sid=LIVE_SID, judge=None):
        return [m for m in tl["judging"].get(sid, []) if judge is None or m["j"] == judge]

    def spy_segments(self):
        calls, real = [], km._lane_segments

        def spy(*a, **k):
            calls.append(a[0])
            return real(*a, **k)
        km._lane_segments = spy
        return calls


class HitsAndEquality(LaneMemoBase):
    def test_an_unchanged_live_lane_is_served_and_equals_the_uncached_loop(self):
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        self.mint_store()
        self.write_archive(self.now - 50, "retry loop")
        seams, real_seams = [], km._segs_seam
        km._segs_seam = lambda turn, store: (seams.append(1), real_seams(turn, store))[1]
        tl1 = self.build()
        self.assertEqual(len(seams), 1, "the first build walks the lane's turns")
        tl2 = self.build()
        self.assertEqual(len(seams), 1, "the second build walks nothing: a lookup served the lane")
        self.assertEqual(self.outcomes(), {"hit": 1, "miss": 1, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertIs(tl2["turns"][LIVE_SID], tl1["turns"][LIVE_SID], "the same bars list rides into turns[sid]")
        self.assertTrue(all(a is b for a, b in zip(tl1["turns"][LIVE_SID], tl2["turns"][LIVE_SID])), "and the same bar dicts")
        # what the memo served is what the uncached loop returns now, on the same objects
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals = km.jd.load_goals_shared(LIVE_SID)
        caps = km._captions(LIVE_SID)
        bars, seg_ends, last_t, compactions, cap_marks, other_marks, nsegs, complained = km._lane_segments(
            LIVE_SID, session, goals, caps, True, None)
        self.assertEqual(json.dumps(tl2["turns"][LIVE_SID]), json.dumps(bars))
        self.assertEqual(set(bars[0]), {"id", "start", "end", "p", "w", "r", "q", "c"}, "the compact wire bar, defaults omitted")
        self.assertEqual(json.dumps(tl2["sessions"]), json.dumps(tl1["sessions"]), "the badge row, per build, agrees")
        self.assertEqual(json.dumps(tl2["judging"]), json.dumps(tl1["judging"]))
        want = []
        km._derive_judging(LIVE_SID, caps, goals, self.now - km.TL_HORIZON, want, seg_ends)
        self.assertEqual([(m["j"], m["x"]) for m in self.marks(tl2)], [(m["judge"], m["text"]) for m in want],
                         "the marks on a hit are the derivation's")
        self.assertEqual({m["j"] for m in self.marks(tl2)}, {"captioner", "planner", "archiver"})
        self.assertEqual(self.lane(tl2)["since"], self.now - 100, "a live lane's since is the liveness snapshot's")
        st = self.stats()
        self.assertEqual((st["segs_hit"], st["segs_miss"], st["entries"], nsegs), (1, 1, 1, 1))

    def test_a_hit_lane_still_runs_the_parse_the_merge_and_the_captions_read(self):
        counts = {"parse": 0, "merge": 0, "caps": 0}
        real_parse, real_merge, real_caps = self._saved["_parse"], self._saved["_merge_live_atoms"], self._saved["_captions"]
        km._parse = lambda *a, **k: (counts.__setitem__("parse", counts["parse"] + 1), real_parse(*a, **k))[1]
        km._merge_live_atoms = lambda *a, **k: (counts.__setitem__("merge", counts["merge"] + 1), real_merge(*a, **k))[1]
        km._captions = lambda *a, **k: (counts.__setitem__("caps", counts["caps"] + 1), real_caps(*a, **k))[1]
        self.build(); self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(counts, {"parse": 2, "merge": 2, "caps": 2},
                         "the chip reads the merged parse and the captions are the key's input: a hit skips only the loop")

    def test_the_held_bars_survive_the_postal_join_unchanged(self):
        tl = self.build()
        before = json.dumps(tl["turns"][LIVE_SID])
        km._bind_message_execs([{"id": "m1", "toId": LIVE_SID, "from": "api", "sent": self.t0 - 5, "pending": True}],
                               tl["turns"], {})
        self.assertEqual(json.dumps(tl["turns"][LIVE_SID]), before, "_bind_message_execs writes to the messages only")
        tl2 = self.build()
        self.assertIs(tl2["turns"][LIVE_SID], tl["turns"][LIVE_SID])
        self.assertEqual(json.dumps(tl2["turns"][LIVE_SID]), before)

    def test_the_badge_row_is_per_build_on_a_hit(self):
        tl1 = self.build()
        self.assertFalse(self.lane(tl1)["postalServiceOff"])
        km._set_session_flag(LIVE_SID, "postalServiceOff", True)
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "a flag is a badge-row input, not a segment input")
        self.assertTrue(self.lane(tl2)["postalServiceOff"], "and the row carries it at once")

    def test_a_hit_hands_the_binder_the_whole_prompts(self):
        # T278b: a bar carries the first line of its prompt, capped, and the binder's sender heuristic reads the WHOLE
        # text through the builder's prompts map, so a delivery that names its sender on a later line binds. The memo
        # serves the map beside the bars; without it every served build would bind on the first line alone.
        self.add_lane(LIVE_SID, "web", [_rec("user", self.t0, "u1", None, "make the retry loop back off\nfrom api: the cap is two minutes"),
                                         _rec("assistant", self.t0 + 20, "a1", "u1", "Added exponential backoff.")])
        handed, real = [], self._saved["_bind_message_execs"]
        km._bind_message_execs = lambda messages, turns, prompts=None: (handed.append(prompts), real(messages, turns, prompts))[1]
        self.build()
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        bar = tl2["turns"][LIVE_SID][0]
        self.assertEqual(bar["q"], "make the retry loop back off", "the wire carries the first line")
        self.assertEqual(handed[1].get(bar["id"]), "make the retry loop back off\nfrom api: the cap is two minutes",
                         "the served build hands the binder the whole prompt")
        self.assertEqual(handed[1], handed[0])
        msg = {"id": "m1", "toId": LIVE_SID, "from": "api", "fromOrig": "api", "sent": self.t0 - 5, "pending": True}
        real([msg], tl2["turns"], handed[1])
        self.assertEqual((msg["exec"], msg["pending"]), (bar["start"], False), "the sender named on the second line binds")
        bare = {"id": "m2", "toId": LIVE_SID, "from": "api", "fromOrig": "api", "sent": self.t0 - 5, "pending": True}
        real([bare], tl2["turns"], {})
        self.assertTrue(bare["pending"], "the first line alone does not name the sender")

    def test_the_judging_band_reader_leaves_the_held_marks_unchanged(self):
        # the held marks ride into `semantic`, which _run_judging reads for its gloss text and never writes to: the
        # real reader runs over a hit here (the usage log under this root is empty, so the band it builds is bare)
        self.mint_store()
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        self.build()
        before = json.dumps(km._lanes_memo[LIVE_SID][4][4:6])
        self.assertIn("Added backoff", before)
        km._run_judging = self._saved["_run_judging"]
        tl = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(json.dumps(km._lanes_memo[LIVE_SID][4][4:6]), before, "the reader wrote nothing into the held marks")
        self.assertEqual(self.marks(tl), [], "the band is the usage log's, empty under this root")

    def test_two_threads_deriving_one_lane_leave_one_exact_entry(self):
        # the derivation runs unlocked; the lock covers get, put, evict and the counters, so two builds that miss
        # together both store an exact entry and the last wins. The parse cache is warmed first so both threads
        # derive from ONE parse object: two concurrent misses in _parse each store their own, and the entry that
        # survives can hold the object _parse_cache did not, which the next build drops and derives once more. With
        # one parse object the next build's serve is this memo's property, not the parse cache's race.
        km._parse(str(self.tpath()), LIVE_SID, self.now)
        gate = threading.Barrier(2)
        frames, errors = [], []

        def run():
            try:
                gate.wait(5)
                frames.append(self.build())
            except Exception as e:      # a raise in a thread would otherwise be silent
                errors.append(e)
        ts = [threading.Thread(target=run) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(frames), 2)
        self.assertEqual(self.stats()["entries"], 1)
        self.assertEqual(json.dumps(frames[0]["turns"]), json.dumps(frames[1]["turns"]))
        st = self.outcomes()
        self.assertEqual(st["hit"] + st["miss"], 2, "both missed, or the second found the first's entry: either is exact")
        tl = self.build()
        self.assertEqual(self.outcomes()["hit"], st["hit"] + 1, "the next build is served from the surviving entry")
        self.assertEqual(json.dumps(tl["turns"]), json.dumps(frames[0]["turns"]))


class EveryInputBusts(LaneMemoBase):
    """One test per input in the memo's key: change only that input, observe a miss and the new content."""

    def test_a_transcript_append_moves_the_parse_object(self):
        tl1 = self.build()
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes.")])
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(len(tl1["turns"][LIVE_SID]), 1)
        self.assertEqual(len(tl2["turns"][LIVE_SID]), 2, "the appended turn is a second bar")
        self.assertEqual(self.stats()["entries"], 1, "the new entry replaced the old")

    def test_a_states_write_moves_the_parse_object(self):
        # the states log is read into the parse (idle atoms) and sits in _parse's key
        self.build()
        (km.jd.STATE / "states").mkdir(exist_ok=True)
        (km.jd.STATE / "states" / (LIVE_SID + ".jsonl")).write_text(json.dumps({"t": self.t0 + 30, "state": "waiting"}) + "\n")
        self.build()
        self.assertEqual(self.outcomes()["miss"], 2)

    def test_a_store_publish_is_a_new_frozen_store(self):
        self.mint_store(text="First goal")
        tl1 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl1, judge="planner")], ["First goal"])
        s = km.jd.load_goals(LIVE_SID)
        km.jd.apply_plan(s, "s2", self.t0 + 100, [{"do": "mint", "why": "x", "text": "Second goal"}], [])
        km.jd.rollup_status(s, session_closed=False)
        km.jd.save_goals(LIVE_SID, s)
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(sorted(m["x"] for m in self.marks(tl2, judge="planner")), ["First goal", "Second goal"])

    def test_a_journal_append_is_a_new_store_version(self):
        s = self.mint_store()
        nid = next(iter(s["nodes"]))
        self.build()
        km.jd.append_block(LIVE_SID, nid, "nudge", "no answer to the status ask", self.now)
        self.build()
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 2, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})

    def test_a_captions_append_moves_the_captions_key(self):
        seg = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg, "segment", self.t0, "first summary")
        tl1 = self.build()
        self.assertEqual(tl1["turns"][LIVE_SID][0]["c"], "first summary")
        km.jd.append_caption(LIVE_SID, seg, "segment", self.t0, "second summary")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(tl2["turns"][LIVE_SID][0]["c"], "second summary")
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="captioner")], ["second summary"])

    def test_a_caption_landing_between_the_stat_and_the_read_misses_on_the_next_build(self):
        # The captions key is the file's stat taken BEFORE _captions reads it. A row that lands between the two is
        # read by this build and held under the old key, so the next build's stat misses and shows it; a stat taken
        # after the read would hold the old rows under the new key and serve the stale caption until another input moved.
        seg = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg, "segment", self.t0, "first summary")
        real, landed = self._saved["_captions"], []

        def late_row(fsid):
            caps = real(fsid)
            if not landed:
                landed.append(1)
                km.jd.append_caption(fsid, seg, "segment", self.t0, "landed after the read")
            return caps
        km._captions = late_row
        tl1 = self.build()
        self.assertEqual(tl1["turns"][LIVE_SID][0]["c"], "first summary", "this build read the file before the row landed")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2, "the file's stat moved: the lane misses")
        self.assertEqual(tl2["turns"][LIVE_SID][0]["c"], "landed after the read")
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="captioner")], ["landed after the read"])
        self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "and holds again once the key and the rows agree")

    def test_an_archive_rewrite_moves_the_archive_key(self):
        self.write_archive(self.now - 50, "retry loop")
        tl1 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl1, judge="archiver")], ["retry loop"])
        self.write_archive(self.now - 40, "retry loop with a two minute cap")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual([(m["t"], m["x"]) for m in self.marks(tl2, judge="archiver")],
                         [(self.now - 40, "retry loop with a two minute cap")])

    def test_an_archive_appearing_after_a_build_misses(self):
        tl1 = self.build()
        self.assertEqual(self.marks(tl1, judge="archiver"), [])
        self.write_archive(self.now - 50, "retry loop")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(len(self.marks(tl2, judge="archiver")), 1)

    def test_a_host_suspension_moves_the_downtime_tuple(self):
        self.build()
        km._downtime.append((self.now - 20000, self.now - 19000))        # a sleep outside every segment: the same bars,
        self.build()                                                     # a different key
        self.assertEqual(self.outcomes()["miss"], 2)
        km._downtime[:] = [(self.now - 20000, self.now - 19000)]         # the same content rebound: the same key
        self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        km._downtime[:] = [(self.now - 20000, self.now - 18000)]         # a slice-assign moves it too
        self.build()
        self.assertEqual(self.outcomes()["miss"], 3)
        self.assertEqual(self.stats()["entries"], 1)

    def test_a_suspension_inside_the_segment_splits_the_bar_on_the_next_build(self):
        self.build()
        km._downtime[:] = [(self.t0 + 5, self.t0 + 10)]
        tl = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(len(tl["turns"][LIVE_SID]), 2, "the bar is cut at the nap on the very next build")
        self.assertTrue(tl["turns"][LIVE_SID][1].get("t"), "the second piece is a continuation: no new prompt dot")

    def test_liveness_is_in_the_key_and_a_dead_lane_leaves_this_memo(self):
        self.build(live_map=self.live_map(LIVE_SID))
        tl = self.build(live_map={})                                         # the process ended: a dead lane in the window
        self.assertFalse(self.lane(tl)["live"])
        # a dead lane is the dead-lane memo's: derived once by _lane_segments directly (dead_miss) and handed to it,
        # whose populate drops the entry this memo held from the lane's live days (it keys on the parse the
        # dead-lane memo lets go of)
        self.assertEqual(self.outcomes()["miss"], 1, "one miss, the live build's")
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 1, "dead_failed_serve": 0})
        self.assertIn(LIVE_SID, km._dead_lane_memo, "the dead build handed the lane to the dead-lane memo")
        self.assertNotIn(LIVE_SID, km._lanes_memo, "and this memo dropped the entry from the lane's live days")
        self.assertFalse(any(p.endswith("/" + LIVE_SID + ".jsonl") for p in km._parse_cache), "so the parse can go")
        served = km._VIEW_STATS.get("laneServe", 0)
        self.build(live_map={})
        self.assertEqual(km._VIEW_STATS.get("laneServe", 0), served + 1, "a dead-lane serve still counts under views")
        self.assertEqual(self.dead()["dead_serve"], 1, "and on this block beside it")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})

    def test_an_open_turn_reads_open_only_on_a_live_lane(self):
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "keep going"),
                             {"type": "assistant", "timestamp": _iso(self.t0 + 110), "uuid": "a2", "parentUuid": "u2",
                              "message": {"role": "assistant", "stop_reason": None,
                                          "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}}])
        tl_live = self.build(live_map=self.live_map(LIVE_SID))
        tl_dead = self.build(live_map={})
        self.assertEqual(self.outcomes()["miss"], 1, "the live build's; the dead build is derived outside this memo")
        self.assertEqual(self.dead()["dead_miss"], 1)
        self.assertTrue(tl_live["turns"][LIVE_SID][-1].get("u"), "the live turn's last piece is open")
        self.assertNotIn("u", tl_dead["turns"][LIVE_SID][-1], "a dead lane never draws an open bar")

    def fork_fixture(self):
        """A parent lane and a child forked from it at fork_t: the child's transcript carries the parent's history
        verbatim, and the backend reports the fork. Returns fork_t."""
        fork_t = self.now - 300
        shared = [_rec("user", self.now - 600, "u1", None, "how should the retry loop back off?"),
                  _rec("assistant", self.now - 580, "a1", "u1", "Use exponential backoff."),
                  # a compaction inside the copied history, and a turn whose work ends AT the fork time: the
                  # parent's while its lane is in the build (the clip is `<=`), the child's own once it leaves
                  {"type": "system", "subtype": "compact_boundary", "uuid": "cb1", "parentUuid": None,
                   "logicalParentUuid": "a1", "timestamp": _iso(self.now - 500), "compactMetadata": {"trigger": "auto"}},
                  _rec("user", fork_t - 20, "u2", "cb1", "and the cap?"),
                  _rec("assistant", fork_t, "a2", "u2", "Two minutes.")]
        own = [_rec("user", fork_t + 60, "u9", "a2", "and inside the fork?"),
               _rec("assistant", fork_t + 80, "a9", "u9", "Cap the delay at two minutes.")]
        self.add_lane(LIVE_PARENT, "parent", shared)
        self.add_lane(LIVE_CHILD, "child", shared + own)
        kids = {LIVE_PARENT: [{"sid": LIVE_CHILD, "name": "child", "cut": "a2", "t": fork_t}]}

        class FakeBe:
            def fork_children(self):
                return kids

            def owns(self, sid):
                return False

            def pending_cut(self, sid):
                return ""

            def __getattr__(self, name):
                return lambda *a, **k: None
        km._sdk = lambda: FakeBe()
        return fork_t

    def test_a_parent_lane_leaving_the_build_moves_a_live_childs_branch_clip(self):
        fork_t = self.fork_fixture()
        tl1 = self.build(live_map=self.live_map(LIVE_SID, LIVE_PARENT, LIVE_CHILD))
        self.assertIn(fork_t, [b["end"] for b in tl1["turns"][LIVE_PARENT]], "the parent draws the turn that ends at the fork time")
        self.assertEqual([c["t"] for c in self.lane(tl1, LIVE_PARENT)["compactions"]], [self.now - 500], "and its compaction marker")
        self.assertTrue(all(b["start"] > fork_t for b in tl1["turns"][LIVE_CHILD]),
                        "clipped while the parent is a lane, the turn ending AT the fork time included")
        self.assertEqual(self.lane(tl1, LIVE_CHILD)["compactions"], [], "the copied boundary is the parent's marker")
        self.assertEqual(self.outcomes()["miss"], 3)
        self.build(live_map=self.live_map(LIVE_SID, LIVE_PARENT, LIVE_CHILD))
        self.assertEqual(self.outcomes()["hit"], 3)
        (km.jd.NAMES / LIVE_PARENT).unlink()                             # the parent leaves the build: no name, no row
        km.jd._discover_cache.clear()
        tl3 = self.build(live_map=self.live_map(LIVE_SID, LIVE_CHILD))
        self.assertEqual(self.outcomes()["miss"], 4, "the child's clip moved: one more miss")
        self.assertEqual(self.outcomes()["hit"], 4, "the other lane still hits")
        self.assertTrue(any(b["start"] < fork_t for b in tl3["turns"][LIVE_CHILD]), "the whole story, the parent gone")
        self.assertEqual([c["t"] for c in self.lane(tl3, LIVE_CHILD)["compactions"]], [self.now - 500],
                         "the copied boundary is the child's marker now")
        self.assertEqual(self.stats()["evict"], 1, "the parent's entry left with its lane")
        self.assertEqual(self.stats()["entries"], 2)


class NotHeld(LaneMemoBase):
    def test_a_live_tail_is_derived_and_not_held(self):
        fresh = {"type": "assistant", "uuid": "live-1", "t": self.t0 + 40, "session_id": LIVE_SID, "fsid": None,
                 "parentUuid": "a1", "message": {"role": "assistant", "stop_reason": None,
                                                 "content": [{"type": "text", "text": "Streaming a reply."}]}}

        class FakeBE:
            def live_atoms(self, sid):
                return [dict(fresh)]

            def prune_live(self, *a, **k):
                pass
        km.Sessions.backend_for = lambda sid: FakeBE()
        tl1 = self.build()
        tl2 = self.build()
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 2, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0, "a merged tail is a new object every build: nothing to hold")
        self.assertEqual(tl1["turns"][LIVE_SID][0]["end"], self.t0 + 40, "the live atom extends the bar")
        self.assertIsNot(tl2["turns"][LIVE_SID], tl1["turns"][LIVE_SID])

    def test_a_failed_parse_is_derived_says_so_once_and_is_not_held(self):
        km._parse = lambda path, sid, now: (_ for _ in ()).throw(OSError("unreadable"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build(); self.build()
        self.assertEqual(self.outcomes()["complain_skip"], 2)
        self.assertEqual(self.stats()["entries"], 0)
        self.assertEqual(tl["turns"][LIVE_SID], [])
        self.assertEqual(err.getvalue().count("parse failed"), 1)

    def test_a_private_store_with_content_is_not_held(self):
        self.mint_store()
        km.jd._SHARED_OFF[0] = True                                      # the shared cache is off: load_goals' private store
        try:
            tl = self.build(); self.build()
            self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 0, "unshared_skip": 2})
            self.assertEqual(self.stats()["entries"], 0)
            self.assertEqual(len(self.marks(tl, judge="planner")), 1, "derived all the same")
        finally:
            km.jd._SHARED_OFF[0] = False

    def test_a_lane_without_a_store_hits_under_the_empty_rule(self):
        self.assertFalse((km.jd.GOALDIR / (LIVE_SID + ".json")).exists())
        self.build(); self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "no seams, no nodes: the store's identity does not matter")
        self.mint_store()
        self.build()
        self.assertEqual(self.outcomes()["miss"], 2, "a published store is a new input")

    def test_a_faulted_store_is_derived_and_not_held(self):
        """A store that cannot be read (goals None: build_timeline complained) shares no key with the lane that has
        no store: _lane_segments derives NO marks for None, while the empty store still yields the captioner's and
        the archiver's. Both keyed as "empty" once, so a lane whose unreadable store was then removed was served the
        faulted build's empty marks until another input moved. A faulted store is derived and not held (the goals
        stage complained: complain_skip), and the lane without a store derives on the next build."""
        self.write_archive(self.now - 50, "retry loop")
        store = km.jd.GOALDIR / (LIVE_SID + ".json")
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"nodes": {}, "status": {}}))
        store.chmod(0)                                 # unreadable: load_goals_shared raises, the boundary answers None
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tl1 = self.build()
        finally:
            store.chmod(0o600)
        self.assertIn("goals failed", err.getvalue())
        self.assertEqual(self.marks(tl1), [], "a faulted store: the lane's marks are missing, loudly")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 1, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0)
        store.unlink()                                 # no store now: load_goals' fresh private store, the empty key
        tl2 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="archiver")], ["retry loop"],
                         "derived, not served the faulted build's marks: the archiver's mark is this lane's")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 1, "unshared_skip": 0})

    def test_a_held_lane_whose_store_faults_is_derived(self):
        """The reverse transition: a lane held with no store (the archiver's mark in its entry) whose store then
        appears unreadable is derived, as a fresh derivation of a faulted store is (no marks), and not served the
        entry's marks; the entry stands, and serves again once the store reads as absent."""
        self.write_archive(self.now - 50, "retry loop")
        tl1 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl1, judge="archiver")], ["retry loop"])
        self.assertEqual(self.stats()["entries"], 1)
        store = km.jd.GOALDIR / (LIVE_SID + ".json")
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"nodes": {}, "status": {}}))
        store.chmod(0)
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tl2 = self.build()
        finally:
            store.chmod(0o600)
        self.assertIn("goals failed", err.getvalue())
        self.assertEqual(self.marks(tl2), [], "the store faulted: derived without marks, never the held entry's")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 1, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 1, "a skip stores nothing and drops nothing")
        store.unlink()
        tl3 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl3, judge="archiver")], ["retry loop"],
                         "no store again: the standing entry serves, and it equals a fresh derivation")
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(json.dumps(tl3["judging"][LIVE_SID]), json.dumps(tl1["judging"][LIVE_SID]),
                         "the served frame is the first build's derivation, on the same inputs")
        self.assertEqual(json.dumps(tl3["turns"][LIVE_SID]), json.dumps(tl1["turns"][LIVE_SID]))

    def test_a_lane_without_captions_hits_under_the_empty_rule(self):
        self.assertFalse((km.jd.CAPDIR / (LIVE_SID + ".jsonl")).exists())
        self.build(); self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(self.stats()["entries"], 1)

    def test_a_complaining_seams_stage_is_not_held(self):
        km._segs_seam = lambda turn, store: (_ for _ in ()).throw(ValueError("malformed seam"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build(); self.build()
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 2, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0)
        self.assertEqual(len(tl["turns"][LIVE_SID]), 1, "the seams fell back to the plain segments; the bar still draws")
        self.assertIn("seams failed", err.getvalue())

    def test_a_complaining_marks_stage_is_not_held(self):
        km._derive_judging_marks = lambda *a, **k: (_ for _ in ()).throw(KeyError("t"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build(); self.build()
        self.assertEqual(self.outcomes()["complain_skip"], 2)
        self.assertEqual(self.stats()["entries"], 0)
        self.assertEqual(len(tl["turns"][LIVE_SID]), 1)
        self.assertEqual(self.marks(tl), [], "this lane lost its marks; the frame shipped")
        self.assertIn("judging-marks failed", err.getvalue())

    def test_a_caption_row_with_a_string_time_costs_the_marks_not_the_frame(self):
        # the horizon comparisons run at assembly, outside the lane's guard; a time they cannot compare fails the
        # marks stage at derivation (not held, the lane says so) instead of raising out of build_timeline every build
        seg = self.seg_id()
        km.jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.CAPDIR / (LIVE_SID + ".jsonl")).write_text(
            json.dumps({"id": seg, "grain": "segment", "t": "not a number", "caption": "typed time"}) + "\n")
        err = io.StringIO()
        with redirect_stderr(err):
            tl1 = self.build()
            tl2 = self.build()
        for tl in (tl1, tl2):
            self.assertEqual(len(tl["turns"][LIVE_SID]), 1, "the lane is drawn")
            self.assertEqual(tl["turns"][LIVE_SID][0]["c"], "typed time", "the caption itself still serves the bar")
            self.assertEqual(self.marks(tl), [], "the lane is mark-less")
        self.assertEqual(self.outcomes()["complain_skip"], 2, "not held: the miss and the following build both derive")
        self.assertEqual(self.stats()["entries"], 0)
        self.assertIn("judging-marks failed", err.getvalue())

    def test_an_archive_with_a_string_time_costs_the_marks_not_the_frame(self):
        km.jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.ARCHDIR / (LIVE_SID + ".json")).write_text(json.dumps({"t": "soon", "headline": "h"}))
        err = io.StringIO()
        with redirect_stderr(err):
            tl1 = self.build()
            tl2 = self.build()
        for tl in (tl1, tl2):
            self.assertEqual(len(tl["turns"][LIVE_SID]), 1)
            self.assertEqual(self.marks(tl), [])
        self.assertEqual(self.outcomes()["complain_skip"], 2)
        self.assertIn("judging-marks failed", err.getvalue())

    def test_the_assembly_is_guarded_per_lane_at_its_call_site(self):
        km._judging_assemble = lambda *a, **k: (_ for _ in ()).throw(TypeError("unorderable"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build()
        self.assertEqual(len(tl["turns"][LIVE_SID]), 1, "the frame ships with the lane's bars")
        self.assertEqual(self.marks(tl), [])
        self.assertIn("judging-marks failed", err.getvalue())

    def test_captions_read_with_no_file_to_stat_are_derived_and_not_held(self):
        # the captions key is the file's stat taken before the read; rows read when that stat found no file (the
        # file appeared between the two) have no key this build can hold them under, so the lane is derived and not
        # held, and the next build, whose stat sees the file, holds it
        seg, real, landed = self.seg_id(), self._saved["_captions"], []

        def appears_before_the_read(fsid):
            if not landed:
                landed.append(1)
                km.jd.append_caption(fsid, seg, "segment", self.t0, "landed between the stat and the read")
            return real(fsid)
        km._captions = appears_before_the_read
        tl1 = self.build()
        self.assertEqual(tl1["turns"][LIVE_SID][0]["c"], "landed between the stat and the read", "this build read the row")
        self.assertEqual((self.outcomes()["miss"], self.stats()["entries"]), (1, 0), "and had no key to hold it under")
        self.build()
        self.assertEqual((self.outcomes()["miss"], self.stats()["entries"]), (2, 1), "the next build's stat sees the file: held")
        self.build()
        self.assertEqual(self.outcomes()["hit"], 1)

    def test_a_failed_archive_stat_stores_nothing(self):
        real = self._saved_file_key
        arch = str(km.jd.STATE / "archive" / (LIVE_SID + ".json"))
        km.jd._file_key = lambda p: object() if p == arch else real(p)
        self.build(); self.build()
        self.assertEqual(self.outcomes()["miss"], 2, "a sentinel matches nothing")
        self.assertEqual(self.stats()["entries"], 0, "and nothing is stored under it")

    def test_the_skeleton_build_bypasses_the_memo(self):
        self.build(with_bars=False)
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0)
        self.build(with_bars=True)
        self.assertEqual(self.outcomes()["miss"], 1)
        self.build(with_bars=False)
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 1, "a skeleton neither reads nor evicts")


class ClockAtAssembly(LaneMemoBase):
    def test_a_caption_row_without_a_time_is_dropped_on_a_hit_as_on_a_miss(self):
        seg = self.seg_id()
        km.jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.CAPDIR / (LIVE_SID + ".jsonl")).write_text(
            json.dumps({"id": seg + "#p", "grain": "prompt", "caption": "no time on this row"}) + "\n"
            + json.dumps({"id": seg, "grain": "segment", "t": self.t0, "caption": "timed"}) + "\n")
        tl1 = self.build()
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        for tl in (tl1, tl2):
            self.assertEqual([m["x"] for m in self.marks(tl, judge="captioner")], ["timed"])
            self.assertEqual(tl["turns"][LIVE_SID][0]["m"], "no time on this row", "the caption itself still serves the dot")

    def test_the_horizon_applies_per_build_on_a_hit(self):
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.now - 3000, "an older caption")
        tl1 = self.build()
        self.assertEqual(len(self.marks(tl1, judge="captioner")), 1)
        km.TL_HORIZON = 1000                                             # the horizon moves past the mark
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "no re-derivation: the horizon is applied at assembly")
        self.assertEqual(self.marks(tl2, judge="captioner"), [])

    def test_the_caption_cap_applies_per_build_on_a_hit(self):
        seg = self.seg_id()
        for i in range(3):
            km.jd.append_caption(LIVE_SID, seg + ("" if i == 0 else "#p%d" % i), "segment", self.t0 + i, "caption %d" % i)
        tl1 = self.build()
        self.assertEqual(len(self.marks(tl1, judge="captioner")), 3)
        km.JUDGE_CAP_LIMIT = 1
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="captioner")], ["caption 2"], "the newest survives the cap")

    def test_the_segment_derivation_reads_no_clock(self):
        class NoClock:
            def time(self):
                raise AssertionError("the derivation read the clock")

            def __getattr__(self, name):
                return getattr(time, name)
        self.mint_store()
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals, caps = km.jd.load_goals_shared(LIVE_SID), km._captions(LIVE_SID)
        real = km.time
        km.time = NoClock()
        try:
            value = km._lane_segments(LIVE_SID, session, goals, caps, True, None)
        finally:
            km.time = real
        self.assertEqual((len(value[0]), value[6], value[7]), (1, 1, False))
        self.assertTrue(value[4] and value[5], "the marks came out unfiltered")
        tree = ast.parse(inspect.getsource(km._lane_segments))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"now", "time", "TL_HORIZON", "JUDGE_CAP_LIMIT"}, repr(names))


class Eviction(LaneMemoBase):
    def test_entries_leave_with_their_lanes_and_a_live_only_build_evicts_nothing(self):
        self.add_lane(LIVE_SID2, "api", self.recs)
        self.build(live_map=self.live_map(LIVE_SID, LIVE_SID2))
        self.assertEqual(self.stats()["entries"], 2)
        self.build(live_map=self.live_map(LIVE_SID), live_only=True)            # LIVE_SID2 is not in a live-only build
        self.assertEqual(self.stats()["entries"], 2, "a live-only build reads a subset of the lanes and must not evict")
        self.assertEqual(self.stats()["evict"], 0)
        (km.jd.NAMES / LIVE_SID2).unlink()
        km.jd._discover_cache.clear()
        self.build(live_map=self.live_map(LIVE_SID))
        self.assertEqual(self.stats()["entries"], 1)
        self.assertEqual(self.stats()["evict"], 1)
        self.assertEqual(set(km._lanes_memo), {LIVE_SID})

    def test_the_memo_is_bounded_least_recently_served_first(self):
        self.add_lane(LIVE_SID2, "api", self.recs)
        km._LANES_MEMO_MAX = 2
        self.build(live_map=self.live_map(LIVE_SID, LIVE_SID2))                 # both held
        self.assertEqual(set(km._lanes_memo), {LIVE_SID, LIVE_SID2})
        first = next(iter(km._lanes_memo))                               # the FIRST inserted, whichever lane the build drew first
        self.build(live_map=self.live_map(first), live_only=True)                # served: the most recently served, though inserted first
        self.assertEqual(self.outcomes()["hit"], 1)
        self.add_lane(LIVE_SID3, "tests", self.recs)
        self.build(live_map=self.live_map(LIVE_SID3), live_only=True)
        self.assertEqual(set(km._lanes_memo), {first, LIVE_SID3},
                         "the least recently SERVED went, not the first inserted; the served lane stayed")
        self.assertEqual(self.stats()["evict"], 1)
        self.assertEqual(self.stats()["entries"], 2)

    def test_an_entry_whose_parse_is_no_longer_the_builds_is_dropped_when_seen(self):
        self.build()
        self.assertEqual(self.stats()["entries"], 1)
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay")])   # a new parse object
        km._parse = lambda path, sid, now: (_ for _ in ()).throw(OSError("unreadable"))   # and the parse fails
        with redirect_stderr(io.StringIO()):
            self.build()
        self.assertEqual(self.stats()["entries"], 0, "the old entry held a parse the build no longer has")


class DeadLanesOnTheSameBlock(LaneMemoBase):
    """A dead lane bypasses the live-lane memo: derived by _lane_segments directly on a miss and cached in the
    dead-lane memo; its outcomes ride the same /perf block so one block carries every lane."""

    def dead_build(self):
        return self.build(live_map={})

    def test_a_dead_miss_leaves_this_memo_and_the_parse_cache_empty_and_is_not_a_miss_here(self):
        calls = self.spy_segments()
        tl = self.dead_build()
        self.assertEqual(calls, [LIVE_SID], "derived directly")
        self.assertFalse(self.lane(tl)["live"])
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 1, "dead_failed_serve": 0})
        self.assertNotIn(LIVE_SID, km._lanes_memo)
        self.assertIn(LIVE_SID, km._dead_lane_memo)
        self.assertFalse(km._dead_lane_memo[LIVE_SID][1]["failed"])
        self.assertFalse(any(p.endswith("/" + LIVE_SID + ".jsonl") for p in km._parse_cache))
        self.dead_build()
        self.assertEqual(calls, [LIVE_SID], "served from the dead-lane memo")
        self.assertEqual(self.dead()["dead_serve"], 1)

    def test_a_failed_dead_parse_is_served_as_failed_until_the_stat_moves(self):
        parses, failing, real = [], [True], self._saved["_parse"]

        def parse(path, sid, now):
            parses.append(path)
            if failing[0]:
                raise OSError("unreadable")
            return real(path, sid, now)
        km._parse = parse
        with redirect_stderr(io.StringIO()):
            tl1 = self.dead_build()
            tl2 = self.dead_build()
        self.assertEqual(len(parses), 1, "parsed once: the failed parse is cached as the empty lane")
        self.assertEqual((tl1["turns"][LIVE_SID], tl2["turns"][LIVE_SID]), ([], []))
        self.assertTrue(km._dead_lane_memo[LIVE_SID][1]["failed"])
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 1, "dead_failed_serve": 1})
        failing[0] = False
        move_ctime(self.tpath())
        tl3 = self.dead_build()
        self.assertEqual(len(parses), 2, "the transcript's stat moved: parsed again")
        self.assertEqual(len(tl3["turns"][LIVE_SID]), 1)
        self.dead_build()
        self.assertEqual(self.dead(), {"dead_serve": 1, "dead_miss": 2, "dead_failed_serve": 1})
        self.assertEqual(self.outcomes()["complain_skip"], 0, "a dead lane's failure is not this memo's outcome")

    def test_a_complaining_dead_lane_is_derived_every_build_and_not_cached(self):
        km._segs_seam = lambda turn, store: (_ for _ in ()).throw(ValueError("malformed seam"))
        with redirect_stderr(io.StringIO()):
            self.dead_build(); self.dead_build()
        self.assertNotIn(LIVE_SID, km._dead_lane_memo)
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 2, "dead_failed_serve": 0})


class PerfWiring(LaneMemoBase):
    def test_the_memo_reports_under_perf_memos_lanes(self):
        self.build(); self.build()
        blk = km._PERF_STATS.snapshot()["memos"]["lanes"]
        self.assertEqual(set(blk), {"hit", "miss", "live_tail", "complain_skip", "unshared_skip", "evict", "entries",
                                    "segs_hit", "segs_miss", "dead_serve", "dead_miss", "dead_failed_serve"})
        self.assertEqual((blk["hit"], blk["miss"], blk["entries"], blk["segs_hit"], blk["segs_miss"]), (1, 1, 1, 1, 1))
        self.assertEqual((blk["dead_serve"], blk["dead_miss"], blk["dead_failed_serve"]), (0, 0, 0), "live lanes only so far")
        self.build(live_map={}); self.build(live_map={})
        blk = km._PERF_STATS.snapshot()["memos"]["lanes"]
        self.assertEqual((blk["dead_serve"], blk["dead_miss"], blk["dead_failed_serve"]), (1, 1, 0), "the dead lanes ride the same block")
        self.assertEqual((blk["hit"], blk["miss"]), (1, 1), "and leave the live counters alone")


if __name__ == "__main__":
    unittest.main()
