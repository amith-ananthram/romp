#!/usr/bin/env python3
"""The auto-nudge ledger is never rewritten from a fabricated default after a read fault (2026-09-07).

auto-nudge.json has one reader (_auto_nudge_data) and one writer (_write_auto_nudge) with some twenty
read-modify-write sites between them — `d = dict(_auto_nudge_data()); d[k] = v; _write_auto_nudge(d)`.
The reader used to answer ANY fault — a stat or read error, bytes that did not parse — with the
fresh-install default {"enabled": True, "nudged": {}} and cache it under the file's real stat key, so the
next writer persisted it: after one EIO every stalled goal in every session was nudged again (the dedupe
map read as empty) and an explicit auto-nudge OFF came back ON.

Now only a MISSING file reads as the default — that IS the fresh-install state. Every other fault yields a
snapshot tagged "_unproved" (a copy of the last one this process proved, else the default) that the writer
refuses, loud once per fault episode; nothing unproved is cached, so the file's next successful read ends
the episode. Bytes that do not parse are moved aside as auto-nudge.json.corrupt-<utc stamp> (evidence
kept) and the ledger then reads as absent. The nudge pass's stand-down rides the same tag and is pinned
where the firing fixture lives (tests/test_wake_deadman_toggle.py); the interrupt→blocked tick, which runs
outside that pass, is pinned here; the retry-suppress ledger takes the same shape in
tests/test_session_retry_suppress.py.

Synthetic fixtures only: a placeholder sid, invented goal ids, a temp state root."""
import contextlib
import errno
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
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_ledger_unproved", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
GID = SID + ":g1"
DEFAULT = {"enabled": True, "nudged": {}}
SEEDED = {"enabled": False, "nudged": {GID: {"count": 2, "lastTurnId": "t7"}}}   # an explicit OFF + a dedupe record
EIO = OSError(errno.EIO, "Input/output error")
# Spelled out, not km.UNPROVED: a kernel without the fix has no such name, and these tests must fail
# there on the DEFECT (a rewritten file, a fired message), never on an AttributeError in the fixture.
UNPROVED = "_unproved"
REGISTRIES = ("_ledger_fault_warned", "_ledger_refusal_warned", "_ledger_write_failed")   # the once-per-episode registries


def _reset_ledger_state():
    km._autonudge_cache.clear()
    for reg in REGISTRIES:                       # absent on a kernel before the fix (see UNPROVED above)
        vars(km).get(reg, {}).clear()
    vars(km).get("_auto_nudge_paused", [None])[0] = None
    vars(km).get("_auto_nudge_drops_pending", set()).clear()   # drops parked under a fault (review find, 2026-09-08)


def _fail_path(target, method, exc):
    """Path.<method> raises `exc` for `target` only; every other path behaves as before. Returns the undo."""
    real = getattr(Path, method)

    def failing(p, *a, **k):
        if str(p) == str(target):
            raise exc
        return real(p, *a, **k)
    setattr(Path, method, failing)
    return lambda: setattr(Path, method, real)


class _Ledger(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        self.p = jd.STATE / "auto-nudge.json"
        self._undo = []
        _reset_ledger_state()

    def tearDown(self):
        self._heal()
        _reset_ledger_state()
        jd.STATE = self.saved_state
        self.td.cleanup()

    def _seed(self, d=SEEDED):
        self.p.write_text(json.dumps(d))
        km._autonudge_cache.clear()
        return self.p.read_bytes()

    def _fail(self, method, exc=EIO):
        self._undo.append(_fail_path(self.p, method, exc))

    def _heal(self):
        for undo in reversed(self._undo):
            undo()
        self._undo = []

    def _aside(self):
        return sorted(n for n in os.listdir(jd.STATE) if n.startswith("auto-nudge.json.corrupt-"))


class FreshInstall(_Ledger):
    def test_a_missing_file_reads_as_the_default_with_no_log_and_no_quarantine(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._auto_nudge_data()
        self.assertEqual(d, DEFAULT)
        self.assertNotIn(UNPROVED, d, "absent is the one fault that IS the default — a proved read")
        self.assertEqual(err.getvalue(), "", "a fresh install is not an incident")
        self.assertEqual(os.listdir(jd.STATE), [], "nothing moved aside, nothing created")
        self.assertEqual(km._autonudge_cache, {}, "absent is not cached — the old arm, byte for byte")
        km._mark_auto_nudged(GID, "t1", 1)
        self.assertEqual(json.loads(self.p.read_text())["nudged"][GID]["count"], 1,
                         "…and the first RMW writes the ledger, as it always did")


class ReadFault(_Ledger):
    def test_an_ordinary_rmw_after_a_read_fault_leaves_the_file_unchanged(self):
        # THE defect: the reader fabricated the default on EIO, cached it under the file's real stat key,
        # and the next writer persisted it — flipping the OFF to ON and erasing the dedupe record.
        before = self._seed()
        self._fail("read_text")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            applied = km._set_auto_nudge(True, gt=1_700_000_000_000)
            applied2 = km._set_compact_suggest(True, gt=1_700_000_000_000)
            km._mark_auto_nudged(GID + "x", "t9", 1)
        self._heal()
        self.assertEqual(self.p.read_bytes(), before, "the file on disk is byte for byte what it was")
        self.assertIsNone(applied, "a refused write reports not-applied, like a failed write")
        self.assertIsNone(applied2, "…the compaction-suggestion toggle too (the same blob, the same writer)")
        self.assertEqual(err.getvalue().count("refusing to write auto-nudge.json"), 1,
                         "one refusal line per fault episode — not one per write, not one per tick")
        self.assertIn("Input/output error", err.getvalue(), "the line names the fault")
        self.assertEqual(km._autonudge_cache, {}, "nothing unproved is cached")

    def test_a_stat_fault_is_unproved_too(self):
        # the old stat arm returned the default for ANY OSError; only ENOENT means absent
        before = self._seed()
        self._fail("stat")
        with contextlib.redirect_stderr(io.StringIO()):
            d = km._auto_nudge_data()
            km._mark_auto_nudged(GID + "x", "t9", 1)
        self._heal()
        self.assertEqual(self.p.read_bytes(), before)
        self.assertTrue(str(d.get(UNPROVED, "")).startswith("stat failed"))

    def test_the_fault_text_carries_the_errno_never_the_path(self):
        # the tag rides the settingStale frame's `why` and the error-center row: errno + strerror only, the
        # writer's shaping (_oserror_text), so no absolute path leaves the kernel (review find, 2026-09-08)
        self._seed()
        for method, arm in (("stat", "stat failed"), ("read_text", "read failed")):
            self._fail(method, OSError(errno.EIO, "Input/output error", str(self.p)))
            with contextlib.redirect_stderr(io.StringIO()):
                d = km._auto_nudge_data()
            self._heal()
            self.assertEqual(d.get(UNPROVED), "%s: [Errno 5] Input/output error" % arm, method)

    def test_the_snapshot_is_a_copy_of_the_last_proved_one_tagged_with_the_fault(self):
        self._seed()
        proved = km._auto_nudge_data()                     # a proved read fills the cache
        self.assertFalse(proved["enabled"])
        self.p.write_text(json.dumps(DEFAULT))             # the file moves on (a different size → a new key)…
        self._fail("read_text")                            # …and the new bytes cannot be read
        with contextlib.redirect_stderr(io.StringIO()):
            d = km._auto_nudge_data()
        self.assertFalse(d["enabled"], "the last snapshot this process proved — not the fabricated default")
        self.assertIn(GID, d["nudged"])
        self.assertTrue(str(d.get(UNPROVED, "")).startswith("read failed"))
        d["scratch"] = 1
        self.assertNotIn("scratch", proved, "a copy: no site can poison the proved snapshot through it")
        self.assertIs(km._autonudge_cache[str(self.p)][1], proved, "the cache still holds the proved one")
        self.assertFalse(km._write_auto_nudge(dict(d)), "the writer refuses a snapshot wearing the tag")
        km._autonudge_cache.clear()                        # no proved snapshot at all (a fault at boot):
        with contextlib.redirect_stderr(io.StringIO()):
            d2 = km._auto_nudge_data()
        self.assertEqual({k: v for k, v in d2.items() if k != UNPROVED}, DEFAULT,
                         "readers get the default to display — tagged, so no writer may persist it")
        self.assertIn(UNPROVED, d2)

    def test_the_reader_and_the_writer_shout_once_per_fault_episode(self):
        # keyed on the fault TEXT, reset by EVERY proved state — a write, a read, a cache hit, absence —
        # so a disk that stays broken says so once, and the SAME fault returning after a heal says so again
        self._seed()
        err = io.StringIO()

        def episode(n, method="read_text"):
            if method == "read_text":
                km._autonudge_cache.clear()                # the file must actually be read (no cache hit)
            self._fail(method)
            with contextlib.redirect_stderr(err):
                for _ in range(3):
                    km._auto_nudge_data()
                    km._mark_auto_nudged(GID + "y", "t1", 1)
            self._heal()
            self.assertEqual(err.getvalue().count("auto-nudge.json is unreadable"), n, "the reader: once per episode")
            self.assertEqual(err.getvalue().count("refusing to write"), n, "the writer: once per episode")
        episode(1)
        with contextlib.redirect_stderr(err):
            km._mark_auto_nudged(GID + "y", "t1", 1)       # healed: a proved RMW lands, quietly…
        self.assertIn(GID + "y", json.loads(self.p.read_text())["nudged"])
        episode(2)                                         # …and ended the episode: the same text shouts again
        with contextlib.redirect_stderr(err):
            km._auto_nudge_data()                          # healed: a proved READ alone, no write…
        episode(3)                                         # …ends the episode too (both registries, not just the reader's)
        with contextlib.redirect_stderr(err):
            km._auto_nudge_data()                          # a proved read: the cache now holds the file's key
        episode(4, "stat")                                 # a STAT fault: the cache is never consulted…
        with contextlib.redirect_stderr(err):
            self.assertNotIn(UNPROVED, km._auto_nudge_data(), "healed: stat OK, same key — a cache HIT")
        episode(5, "stat")                                 # …and the cache hit ended the episode (same text, shouts again)
        self.p.unlink()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._auto_nudge_data(), DEFAULT, "healed by absence: ENOENT is a proved state")
        self._seed()                                       # recreated…
        episode(6, "stat")                                 # …and faulting again with the same text: shouts again
        km._autonudge_cache.clear()
        self._fail("read_text", OSError(errno.EACCES, "Permission denied"))
        with contextlib.redirect_stderr(err):
            km._mark_auto_nudged(GID + "z", "t1", 1)
        self.assertEqual(err.getvalue().count("refusing to write"), 7, "a different fault text is its own episode")
        self.assertIn("Permission denied", err.getvalue())


class CorruptBytes(_Ledger):
    def _corrupt(self, text):
        self.p.write_text(text)
        km._autonudge_cache.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._auto_nudge_data()
        return d, err.getvalue()

    def test_bytes_that_do_not_parse_are_moved_aside_then_read_as_the_default(self):
        d, err = self._corrupt('{"enabled": fals')        # a torn or hand-edited file
        self.assertEqual(d, DEFAULT)
        self.assertNotIn(UNPROVED, d, "absent after the move: a proved default, so writes proceed")
        self.assertFalse(self.p.exists(), "the corrupt file is out of the way")
        aside = self._aside()
        self.assertEqual(len(aside), 1, "evidence kept, never deleted")
        self.assertRegex(aside[0], r"^auto-nudge\.json\.corrupt-\d{8}T\d{6}Z$",
                         "the judge's goal-store quarantine shape: <name>.corrupt-<utc stamp>")
        self.assertEqual((jd.STATE / aside[0]).read_text(), '{"enabled": fals')
        self.assertEqual(err.count("moved aside"), 1, "one stderr line naming the move")
        self.assertIn("invalid JSON", err)
        km._mark_auto_nudged(GID, "t1", 1)
        self.assertEqual(json.loads(self.p.read_text())["nudged"][GID]["count"], 1,
                         "the next RMW writes a fresh ledger: absent is the fresh-install state")

    def test_a_json_value_that_is_not_an_object_is_corrupt_too(self):
        d, err = self._corrupt("[1, 2, 3]")
        self.assertEqual(d, DEFAULT)
        self.assertFalse(self.p.exists())
        self.assertEqual(len(self._aside()), 1)
        self.assertIn("top-level JSON value is list, not an object", err)

    def test_a_second_corrupt_file_in_the_same_second_takes_a_suffix(self):
        real = time.gmtime
        time.gmtime = lambda *a: real(0)                   # pin the stamp: both moves land in one second
        self.addCleanup(setattr, time, "gmtime", real)
        self._corrupt("{")
        self._corrupt("}")
        self.assertEqual(self._aside(), ["auto-nudge.json.corrupt-19700101T000000Z",
                                         "auto-nudge.json.corrupt-19700101T000000Z-1"],
                         "the second never overwrites the first: a -n suffix, the judge's convention")

    def test_a_file_replaced_under_a_corrupt_read_is_not_moved_aside(self):
        # the inode guard: the bytes that failed are the ones to move; a concurrent atomic publish between
        # our read and our rename has already replaced them, and the NEW file must not be quarantined —
        # and (the maintainer's fold on PR #1019) the peer's bytes get their own read IN THIS CALL, so the
        # reader returns what is there now, proved, rather than an unproved copy of the old snapshot
        self.p.write_text("{")
        km._autonudge_cache.clear()
        real, target = Path.read_text, str(self.p)

        def read_then_publish(p, *a, **k):
            raw = real(p, *a, **k)
            if str(p) == target:                           # a peer publishes a valid ledger under us
                tmp = p.with_name("auto-nudge.json.tmp.peer")
                tmp.write_text(json.dumps(SEEDED))
                os.replace(tmp, p)
            return raw
        Path.read_text = read_then_publish
        self._undo.append(lambda: setattr(Path, "read_text", real))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._auto_nudge_data()
        self._heal()
        self.assertEqual(self._aside(), [], "the replacement is not the file whose bytes failed")
        self.assertEqual(json.loads(self.p.read_text()), SEEDED, "…and it stands, untouched")
        self.assertNotIn(UNPROVED, d, "the peer's bytes were read in this call: a PROVED snapshot, not the old one tagged")
        self.assertEqual(d["nudged"], SEEDED["nudged"], "…and it is what the peer published")
        self.assertEqual(err.getvalue(), "", "a peer's publish is not an incident: no line")
        km._autonudge_cache.clear()
        self.assertEqual(km._auto_nudge_data()["nudged"], SEEDED["nudged"])

    def test_a_file_that_keeps_changing_under_the_read_ends_unproved_after_the_bound(self):
        # the re-read is BOUNDED: a peer that republishes corrupt bytes under every read is chased three
        # times, then the reader stops and reports the last try unproved (never a spin, never a move aside)
        self.p.write_text("{")
        km._autonudge_cache.clear()
        real, target, reads = Path.read_text, str(self.p), []

        def read_then_republish_corrupt(p, *a, **k):
            raw = real(p, *a, **k)
            if str(p) == target:                           # a peer publishes again — corrupt again, a new size
                reads.append(1)
                tmp = p.with_name("auto-nudge.json.tmp.peer")
                tmp.write_text("{" * (len(reads) + 1))
                os.replace(tmp, p)
            return raw
        Path.read_text = read_then_republish_corrupt
        self._undo.append(lambda: setattr(Path, "read_text", real))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._auto_nudge_data()
        self._heal()
        self.assertEqual(len(reads), 3, "three tries, then the reader stops chasing the file")
        self.assertIn("replaced meanwhile", str(d.get(UNPROVED, "")), "…and reports the last try unproved")
        self.assertEqual(self._aside(), [], "nothing of a peer's was moved aside")
        self.assertEqual(err.getvalue().count("unreadable"), 1, "said once")

    def test_a_corrupt_file_that_cannot_be_moved_aside_is_said_once_not_once_per_read(self):
        # a read-only state dir: every read re-fails the parse and the move; build_feed reads this ledger
        # once per node per push, so a line per read floods the log — the move failure rides the fault
        # text, which the reader dedupes per episode. The text must be STAMP-FREE: str(OSError) from
        # os.replace names the destination, whose per-second stamp made the text — and so every dedupe:
        # the reader's line, the writer's, the pause latch, the error-center row — fire once a second.
        self.p.write_text("{")
        km._autonudge_cache.clear()
        real_replace, real_gmtime, clock = os.replace, time.gmtime, [1_000_000]

        def refuse_aside(src, dst, *a, **k):
            if ".corrupt-" in str(dst):
                raise OSError(errno.EROFS, "Read-only file system", str(src), None, str(dst))
            return real_replace(src, dst, *a, **k)

        def ticking(*a):                                   # every read lands in a NEW second
            clock[0] += 1
            return real_gmtime(clock[0])
        os.replace, time.gmtime = refuse_aside, ticking
        self._undo.append(lambda: setattr(os, "replace", real_replace))
        self._undo.append(lambda: setattr(time, "gmtime", real_gmtime))
        err, snaps, rows = io.StringIO(), [], len(km._SDK_BOOT_PROBLEMS)
        pause = vars(km).get("_auto_nudge_pause", lambda fault: None)   # absent before the fix (see UNPROVED)
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                snaps.append(km._auto_nudge_data())
                pause(snaps[-1].get(UNPROVED, ""))         # what the ticks do with the text: latch on it
        self._heal()
        self.assertTrue(self.p.exists(), "nothing moved: the bytes stand for repair")
        texts = {str(d.get(UNPROVED, "")) for d in snaps}
        self.assertEqual(len(texts), 1, "ONE fault text across three seconds — no destination, no stamp")
        text = texts.pop()
        self.assertNotRegex(text, r"\d{8}T\d{6}Z", "the stamp never rides the fault text")
        self.assertIn("[Errno %d] Read-only file system" % errno.EROFS, text, "errno + strerror name the failure")
        lines = [l for l in err.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, "two channels, one line each: the reader's, and the pause latch's:\n" + err.getvalue())
        self.assertEqual(sum("serving the default" in l for l in lines), 1, "the reader: once per fault episode, not per second")
        self.assertEqual(sum("paused, not defaulted on" in l for l in lines), 1, "the pause latch: once")
        self.assertTrue(all("could not be moved aside" in l for l in lines), "both carry the stamp-free reason")
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), rows + 1, "one error-center row")
        self.assertFalse(km._write_auto_nudge(dict(snaps[-1])), "and nothing is written over the corrupt file")

    def test_bytes_that_are_not_utf8_are_corrupt_too(self):
        self.p.write_bytes(b"\xff\xfe{")
        km._autonudge_cache.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._auto_nudge_data()
        self.assertEqual(d, DEFAULT)
        self.assertFalse(self.p.exists())
        self.assertEqual(len(self._aside()), 1, "moved aside like any other unparseable bytes")
        self.assertEqual((jd.STATE / self._aside()[0]).read_bytes(), b"\xff\xfe{")
        self.assertIn("not UTF-8", err.getvalue())

    def test_the_move_is_said_in_the_error_center_once(self):
        # stderr alone left the user with a ledger that read as a fresh install (an explicit OFF back ON,
        # every session's stop-retrying gone) and nothing in the dashboard saying so (review find,
        # 2026-09-08). One line, both channels; once per corrupt file, because the next read finds it absent
        rows = len(km._SDK_BOOT_PROBLEMS)
        d, err = self._corrupt('{"enabled": fals')
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), rows + 1, "the dashboard's error center hears the move")
        row = km._SDK_BOOT_PROBLEMS[-1]["text"]
        self.assertIn("reads as a fresh install", row, "…worded as what it costs")
        self.assertIn(self._aside()[0], row, "…and naming the file to restore from")
        self.assertEqual(err.strip(), row, "the stderr line is the same line")
        with contextlib.redirect_stderr(io.StringIO()):
            km._auto_nudge_data()                          # absent now: a proved default, nothing more to say
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), rows + 1, "once per corrupt file")

    def test_a_second_reader_of_the_same_corrupt_bytes_cannot_move_a_fresh_ledger_aside(self):
        # the inode guard alone is check-then-act: readers run unlocked on several threads, and two that
        # read the same corrupt bytes both reach the quarantine with the same stat. Between the first one's
        # rename and a writer's fresh publish, the second's check has already passed, so its rename moved
        # the FRESH ledger aside and served the untagged default (review find, 2026-09-08). The check and
        # the rename are one step under the ledger's writer lock, which every writer publishes under.
        # Deterministic: at the moment of our rename a peer PROBES that lock without blocking: if it gets
        # it, the window is open and the peer does exactly what the first reader and the writer would
        # (moves the bytes, publishes a valid ledger) before our rename runs; if not, it waits like any
        # writer, and acts once we are done. Either way, the fresh ledger must stand.
        self.p.write_text("{")
        km._autonudge_cache.clear()
        real_replace, ledger, lock, probed, peer = os.replace, self.p, km._NUDGE_LOCK, threading.Event(), []

        def peer_moves_and_publishes():
            got = lock.acquire(blocking=False)
            if not got:
                probed.set()
                lock.acquire()                             # a peer that respects the lock waits for us
            try:
                if ledger.exists():                        # the first reader's rename of the bytes that failed
                    real_replace(ledger, ledger.with_name("auto-nudge.json.corrupt-peer"))
                tmp = ledger.with_name("auto-nudge.json.tmp.peer")
                tmp.write_text(json.dumps(SEEDED))         # the writer's publish
                real_replace(tmp, ledger)
            finally:
                lock.release()
                probed.set()

        def replace_racing(src, dst, *a, **k):
            if ".corrupt-" in str(dst) and not peer:
                peer.append(threading.Thread(target=peer_moves_and_publishes))
                peer[0].start()
                self.assertTrue(probed.wait(5), "the peer probed the lock")
            return real_replace(src, dst, *a, **k)
        os.replace = replace_racing
        self._undo.append(lambda: setattr(os, "replace", real_replace))
        with contextlib.redirect_stderr(io.StringIO()):
            km._auto_nudge_data()
        peer[0].join(5)
        self._heal()
        self.assertTrue(self.p.exists(), "the fresh ledger was not moved aside")
        self.assertEqual(json.loads(self.p.read_text()), SEEDED, "…and it stands, untouched")
        for name in self._aside():
            self.assertEqual((jd.STATE / name).read_text(), "{", "only the bytes that failed were moved aside: " + name)
        km._autonudge_cache.clear()
        self.assertEqual(km._auto_nudge_data()["nudged"], SEEDED["nudged"], "the next read serves the fresh ledger")


class _InterruptTickRig(unittest.TestCase):
    """The interrupt tick's collaborators as recording stubs, the rig the two classes below share
    (unittest collects inherited test_ names, so the fixture lives apart from either's tests).

    The tick pushes nothing inline: a flip reaches the next cycle through its WRITER's dirty mark and pusher
    wake (_record_interrupt_block and _lift_interrupt_block end in _mark_views_dirty), so the rig reads those
    two signals and keeps _push_all as a tripwire. _views_dirty is a module global shared across the suite:
    each test records its own floor (_refloor), and the wake is put back on the way out."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        self.p = jd.STATE / "auto-nudge.json"
        names = ("_alive_sessions", "_session_flag", "_compacting_now", "_api_error", "_interrupt_marks",
                 "_session_working", "_record_interrupt_block", "_lift_interrupt_block", "_intr_block_stands",
                 "_push_all")
        self.saved = {n: getattr(km, n) for n in names}
        self.saved_parsed = jd.parsed_session
        km._alive_sessions = lambda now, live: [{"sid": SID, "path": "/nonexistent.jsonl"}]
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid, **k: False   # the tick hands in the row's path and live meta
        km._api_error = lambda path: None
        jd.parsed_session = lambda sid, paths, now: {"turns": [{"id": "t1", "t": 1000, "atoms": []}]}
        km._session_working = lambda turns: False
        self.marks = (1200, 900)                           # the stop is newer than the last human message: a user stop
        km._interrupt_marks = lambda turns, sid="", **k: self.marks   # the tick names its memo family
        self.recorded, self.lifted, self.pushes = [], [], []
        km._record_interrupt_block = lambda sid, ev: self.recorded.append((sid, ev)) or GID
        km._lift_interrupt_block = lambda sid, gid, ev: self.lifted.append((sid, gid, ev)) or True   # spent; the real
        #                                            one returns False only on a goal-store read fault, keeping the marker (PR #1019)
        km._intr_block_stands = lambda sid, gid: True
        km._push_all = lambda *a, **k: self.pushes.append(1)   # a tripwire: the tick never pushes inline
        self._wake_was = km._pusher_wake.is_set()
        self._refloor()
        self._undo = []
        _reset_ledger_state()

    def tearDown(self):
        for undo in reversed(self._undo):
            undo()
        for n, v in self.saved.items():
            setattr(km, n, v)
        if self._wake_was:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        jd.parsed_session = self.saved_parsed
        _reset_ledger_state()
        jd.STATE = self.saved_state
        self.td.cleanup()

    def _fail_read(self):
        self._undo.append(_fail_path(self.p, "read_text", EIO))

    def _heal(self):
        for undo in reversed(self._undo):
            undo()
        self._undo = []

    def _tick(self, n=1):
        for _ in range(n):
            km._interrupt_block_tick(2000, {SID: {"state": ""}})

    def _refloor(self):
        """Forget the marks so far: _marked and _woken then read only what the ticks after this do."""
        self.floor = km._views_dirty[0]
        km._pusher_wake.clear()

    def _marked(self):
        return km._views_dirty[0] > self.floor

    def _woken(self):
        return km._pusher_wake.is_set()


class InterruptBlockTickUnderAFault(_InterruptTickRig):
    """_interrupt_block_tick runs every push OUTSIDE the paused nudge pass. Under an unproved snapshot its
    block arm used to file the block in the goal store (a proved write) and then have the marker write
    refused — and the lift on re-engagement is gated on that marker, so a block placed during a fault
    episode stood until a judge happened to unblock it; and its lift arm, with the marker clear refused,
    set `changed` every cycle and pushed every cycle. Now the block arm files nothing under a fault (the
    stop is re-evaluated from the transcript every push, so the block lands on the first tick after the
    file reads again), the lift still runs (it is the user's own re-engagement), and nothing pushes inline
    from the tick at all: a writer's own dirty mark and pusher wake carry a flip to the next cycle (the real
    writers run in MidTickFaultThenHeal below; the recording stubs here mark nothing), and a refused marker
    write marks nothing, so a fault repeats nothing every cycle. Control flow only: every collaborator is a
    recording stub."""

    def test_a_stop_during_a_fault_files_no_block_until_the_ledger_reads_again(self):
        self.p.write_text(json.dumps(DEFAULT))
        km._autonudge_cache.clear()
        before = self.p.read_bytes()
        self._fail_read()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._tick(5)
        self.assertEqual(self.recorded, [], "no block whose marker cannot be minted: unmarked, it could never be lifted")
        self.assertEqual(self.pushes, [], "…and nothing pushes inline")
        self.assertFalse(self._marked() or self._woken(), "…or marks the views dirty or wakes the pusher: nothing to show")
        self.assertEqual(self.p.read_bytes(), before)
        self.assertEqual(err.getvalue().count("paused"), 1, "loud once per fault episode, on the pass's latch")
        self._heal()
        with contextlib.redirect_stderr(err):
            self._tick()
        self.assertEqual(len(self.recorded), 1, "the stop is re-evaluated every push: the block lands on the first tick after the file reads")
        self.assertEqual(json.loads(self.p.read_text())["intrBlocked"], {SID: GID}, "…with its once-per-episode marker")
        self.assertEqual(self.pushes, [], "no inline push: the block's own store write is what marks the views dirty "
                                          "(the real writer, in MidTickFaultThenHeal; this rig's stub marks nothing)")

    def test_a_fault_landing_mid_tick_leaves_the_block_it_filed_to_its_writers_mark_and_then_stands_down(self):
        # the tag check at the arm's top proves; the fault lands before the marker write. The block IS in
        # the goal store — a proved write, whose writer marks the views dirty (the real one; MidTickFaultThenHeal
        # pins the mark) — so the flip reaches the next cycle whatever the marker's fate, and the tick pushes
        # nothing inline; every later faulted tick stands down at the check: no storm
        self.p.write_text(json.dumps(DEFAULT))
        km._autonudge_cache.clear()
        real, calls, ledger, test = km._auto_nudge_data, [0], self.p, self

        def flaky():
            calls[0] += 1
            if calls[0] == 2:                              # the file moves on (a new key) and cannot be read
                ledger.write_text(json.dumps(DEFAULT, indent=1))
                test._fail_read()
            return real()
        km._auto_nudge_data = flaky
        self._undo.append(lambda: setattr(km, "_auto_nudge_data", real))
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick(5)
        self.assertEqual(len(self.recorded), 1, "the block was filed once")
        self.assertEqual(self.pushes, [], "…and nothing pushed inline, marker or no marker: the writer's mark carries it")
        self.assertFalse(self._marked() or self._woken(), "the stubbed writer marks nothing, and the tick adds no mark of its own")
        self.assertNotIn("intrBlocked", json.loads(self.p.read_bytes()), "the marker write was refused")

    def test_a_re_engagement_during_a_fault_lifts_our_block_and_marks_nothing_while_the_marker_clear_is_refused(self):
        marked = dict(DEFAULT, intrBlocked={SID: GID})
        self.p.write_text(json.dumps(marked))
        km._autonudge_cache.clear()
        km._auto_nudge_data()                              # a proved read: the marker is in the last proved snapshot
        self.p.write_text(json.dumps(marked, indent=1))    # the file moves on (a new stat key)…
        before = self.p.read_bytes()
        self._fail_read()                                  # …and cannot be read
        self.marks = (900, 1200)                           # the user spoke after the stop: re-engaged
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick(5)
        self.assertGreaterEqual(len(self.lifted), 1, "the lift is the user's own re-engagement: it runs whatever the ledger's state")
        self.assertEqual(self.pushes, [], "the marker clear was refused: no inline push (one per cycle before)")
        self.assertFalse(self._marked() or self._woken(),
                         "…and no dirty mark or wake either: a refused clear marks nothing, so no rebuild storm takes the push storm's place")
        self.assertEqual(self.p.read_bytes(), before)
        self._heal()
        self._tick()
        self.assertNotIn(SID, json.loads(self.p.read_text()).get("intrBlocked", {}), "the marker clears on the first tick after the file reads")
        self.assertEqual(self.pushes, [], "…with no inline push: the lift's own store write is what marks the views dirty "
                                          "(the real writer, in MidTickFaultThenHeal)")


B = 1_700_000_000   # an epoch base for the store class below: the diary is an evidence-time ledger


class MidTickFaultThenHeal(_InterruptTickRig):
    """The window the arm above cannot close: the tag check proved, the block was FILED in the goal store,
    and the fault landed on the marker write's own read. The card is blocked, the marker is absent, and
    every healed tick used to return None from _record_interrupt_block (its focus-top rule accepted only a
    "working" top), so no marker was ever minted, the re-engagement lift (gated on the marker) could never
    run, and the card sat in Needs-you until a judge happened to unblock it (review find, 2026-09-08). Now a
    top blocked SOLELY by our own interrupt row (judge._intr_paused_only) is the same stop still standing:
    the first healed tick re-mints the marker without appending, and the lift runs on re-engagement. A
    judge's block filed since is theirs and is left alone. The real store and the real record/lift/stands
    functions; only the transcript and the session list are stubs."""

    def setUp(self):
        super().setUp()
        for n in ("_record_interrupt_block", "_lift_interrupt_block", "_intr_block_stands"):
            setattr(km, n, self.saved[n])                  # the real ones: this class is about the store
        self.saved_goaldir = jd.GOALDIR
        jd.GOALDIR = jd.STATE / "goals"
        jd.GOALDIR.mkdir(parents=True)                     # (the overrides journal follows GOALDIR: private too)
        store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "lastNode": GID,
                 "nodes": {GID: {"id": GID, "text": "Ship the widget", "parentId": None, "nodeComplete": False,
                                 "blocked": False, "cleared": False, "trail": [], "t": B + 500, "mt": B + 800}}}
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        self.marks = (B + 1200, B + 900)                   # a user stop, newer than the last human message
        jd.parsed_session = lambda sid, paths, now: {"turns": [{"id": "t1", "t": B + 1000, "atoms": []}]}
        self.p.write_text(json.dumps(DEFAULT))
        km._autonudge_cache.clear()

    def tearDown(self):
        jd.GOALDIR = self.saved_goaldir
        super().tearDown()

    def _tick(self, n=1):
        for _ in range(n):
            km._interrupt_block_tick(B + 2000, {SID: {"state": ""}})

    def _store(self):
        return jd.load_goals(SID)

    def _block_rows(self):
        return [e.get("src") for e in self._store()["nodes"][GID].get("log") or [] if e.get("kind") == "block"]

    def _fault_on_the_marker_write(self):
        """Reads 1 (the tag check) and 2 (the marker lookup) prove; the block is filed; read 3, the marker
        write's own, finds the file moved on and unreadable. Leaves the file healed."""
        real, calls, ledger, test = km._auto_nudge_data, [0], self.p, self

        def flaky():
            calls[0] += 1
            if calls[0] == 3:
                ledger.write_text(json.dumps(DEFAULT, indent=1))
                test._fail_read()
            return real()
        km._auto_nudge_data = flaky
        self._undo.append(lambda: setattr(km, "_auto_nudge_data", real))
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(self._store()["status"][GID], "blocked", "the block was filed in the goal store")
        self.assertEqual(self._block_rows(), ["interrupt"])
        self.assertNotIn("intrBlocked", json.loads(self.p.read_bytes()), "…and the marker write was refused")
        self.assertEqual(self.pushes, [], "no inline push from the tick")
        self.assertTrue(self._marked() and self._woken(),
                        "the block's own store write marked the views dirty and woke the pusher: the next cycle carries the "
                        "flip, marker or no marker")
        self._refloor()
        self._heal()

    def _re_engage(self):
        self.marks = (B + 1200, B + 1300)                  # the user spoke after the stop…
        jd.parsed_session = lambda sid, paths, now: {"turns": [{"id": "t1", "t": B + 1000, "atoms": []},
                                                               {"id": "t2", "t": B + 1300, "atoms": []}]}   # …opening a turn

    def test_the_first_healed_tick_mints_the_marker_and_the_re_engagement_lift_runs(self):
        self._fault_on_the_marker_write()
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(json.loads(self.p.read_text()).get("intrBlocked"), {SID: GID},
                         "the marker our own block is owed is minted on the first tick after the file reads")
        self.assertEqual(self._block_rows(), ["interrupt"], "…without a second block row: the diary already says it")
        self.assertEqual(self.pushes, [], "nothing pushes inline")
        self.assertFalse(self._marked() or self._woken(),
                         "…and the re-mint marks nothing: the card already shows the block (the ledger's mtime is in the view signature)")
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick(3)
        self.assertFalse(self._marked() or self._woken(), "settled: the marker stands and the block holds, nothing marks or wakes")
        self._re_engage()
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(self._store()["status"][GID], "working", "the re-engagement lifts our block: the card leaves Needs-you")
        self.assertNotIn(SID, json.loads(self.p.read_text()).get("intrBlocked", {}), "…and the marker clears")
        self.assertTrue(self._marked() and self._woken(), "the lift's store write marked the views dirty and woke the pusher")
        self.assertEqual(self.pushes, [], "…and pushed nothing inline")

    def test_a_judge_block_filed_since_is_left_alone(self):
        self._fault_on_the_marker_write()
        st = self._store()
        jd.record_verdict(st, st["nodes"][GID], "closer", "block", B + 1250, why="pick a name for the widget")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick(3)
        self.assertNotIn("intrBlocked", json.loads(self.p.read_text()), "a card a judge has blocked is theirs: no marker")
        self.assertEqual(self._block_rows(), ["interrupt", "closer"], "…and nothing appended")
        self.assertEqual(self.pushes, [], "…and nothing pushed inline")
        self.assertFalse(self._marked() or self._woken(), "…or marked or woken: a judge's card is not ours to move")
        self._re_engage()
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(self._store()["status"][GID], "blocked", "the judge's block stands through the re-engagement")


if __name__ == "__main__":
    unittest.main()
