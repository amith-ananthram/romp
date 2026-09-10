#!/usr/bin/env python3
"""The pusher cycle takes ONE names-registry snapshot (round 2 of the latency workstream).

build_session re-resolves every path token through _cwd_of and every outgoing postal card through
_name_color_by_name, so the ACTIVE tab's rebuild re-read the same ~64 one-line registry files
hundreds of times per 0.5s cycle — py-spy (2026-08-31) attributed ~38% of the pusher's wall time
to those two helpers alone, standing GIL pressure every request thread paid for. The cycle now
publishes ONE snapshot as _live_scope.names (the tmux-liveness hoist idiom, 2026-08-10); the
helpers read it when set and keep their direct disk reads otherwise (request threads, WS builds).
_postal_index gets the sibling fix: an exact-change (mtime_ns, size) memo over the append-only
messages log it re-parsed per push.

SYNTHETIC fixtures only: placeholder UUIDs, invented names.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_names_scope", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class NamesScopeReads(unittest.TestCase):
    """The name/cwd/identity/color helpers read the cycle's snapshot when set, disk otherwise."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        names = Path(self.td.name) / "names"
        names.mkdir()
        (names / SID).write_text("web\t/work/web\t#112233\t#ffffff\n")
        self.saved_names = km.NAMES
        km.NAMES = names
        self.addCleanup(lambda: setattr(km, "NAMES", self.saved_names))
        self.addCleanup(self.td.cleanup)
        self.addCleanup(lambda: setattr(km._live_scope, "names", None))

    def test_scope_serves_all_helpers_without_disk(self):
        # scope content deliberately DIFFERS from disk, so a disk read is caught as a wrong answer
        km._live_scope.names = {SID: ["api", "/work/api", "#445566", "#000000"]}
        self.assertEqual(km._name_of(SID), "api")
        self.assertEqual(km._cwd_of(SID), "/work/api")
        self.assertEqual(km._identity_of(SID), ("#445566", "#000000"))
        self.assertEqual(km._name_color(SID), {"bg": "#445566", "fg": "#ffffff"})
        self.assertEqual(km._name_color_by_name("api"), {"bg": "#445566", "fg": "#ffffff"})
        self.assertIsNone(km._name_color_by_name("web"), "the scope is authoritative while set")

    def test_scope_miss_is_a_miss_not_a_disk_fallback(self):
        # half-and-half reads would smear two moments in time across one build
        km._live_scope.names = {}
        self.assertIsNone(km._name_of(SID))
        self.assertEqual(km._cwd_of(SID), "")
        self.assertEqual(km._identity_of(SID), ("", ""))

    def test_no_scope_reads_disk(self):
        self.assertEqual(km._name_of(SID), "web")
        self.assertEqual(km._cwd_of(SID), "/work/web")
        self.assertEqual(km._identity_of(SID), ("#112233", "#ffffff"))
        self.assertEqual(km._name_color_by_name("web"), {"bg": "#112233", "fg": "#ffffff"})

    def test_snapshot_reads_every_entry_once(self):
        snap = km._names_snapshot()
        self.assertEqual(snap, {SID: ["web", "/work/web", "#112233", "#ffffff"]})

    def test_an_undecodable_entry_is_skipped_never_raised(self):
        # review find (2026-08-31): the snapshot runs on the pusher thread's bare loop, so ONE
        # non-UTF-8 names entry (a torn temp flush, a raw-bytes path from the shell launcher) raising
        # UnicodeDecodeError would kill the pusher permanently — and again every restart while the
        # file persisted. The poisoned entry degrades to a miss (the old per-call readers' semantics);
        # the healthy sibling still snapshots.
        bad = "99999999-8888-7777-6666-555555555555"
        (km.NAMES / bad).write_bytes(b"caf\xe9\t/work/caf\xe9\t#112233\t#ffffff\n")
        snap = km._names_snapshot()
        self.assertEqual(snap, {SID: ["web", "/work/web", "#112233", "#ffffff"]})
        km._live_scope.names = snap
        self.assertIsNone(km._name_of(bad), "the poisoned sid reads as a miss, not a crash")


class CyclePublishesNamesScope(unittest.TestCase):
    """_pusher_cycle sets the names scope for its jobs and ALWAYS clears it — a leaked snapshot
    would freeze names/colors on the pusher thread forever."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        names = Path(self.td.name) / "names"
        names.mkdir()
        (names / SID).write_text("web\t/work/web\t#112233\t#ffffff\n")
        self.saved = (km.NAMES, km._tmux_sessions, km._pusher_cycle_jobs)
        km.NAMES = names
        km._tmux_sessions = lambda: {}
        self.addCleanup(lambda: (setattr(km, "NAMES", self.saved[0]),
                                 setattr(km, "_tmux_sessions", self.saved[1]),
                                 setattr(km, "_pusher_cycle_jobs", self.saved[2])))
        self.addCleanup(self.td.cleanup)

    def test_jobs_see_the_snapshot_and_it_clears_after(self):
        seen = []
        km._pusher_cycle_jobs = lambda now, tmux, any_client: seen.append(getattr(km._live_scope, "names", None))
        km._pusher_cycle()
        self.assertEqual(seen, [{SID: ["web", "/work/web", "#112233", "#ffffff"]}])
        self.assertIsNone(getattr(km._live_scope, "names", None), "cleared at cycle end")

    def test_a_jobs_raise_still_clears_the_scope(self):
        km._pusher_cycle_jobs = lambda now, tmux, any_client: (_ for _ in ()).throw(RuntimeError("job died"))
        with self.assertRaises(RuntimeError):
            km._pusher_cycle()
        self.assertIsNone(getattr(km._live_scope, "names", None), "the finally clears it on the raise too")


class PostalIndexMemo(unittest.TestCase):
    """_postal_index re-parsed the whole messages log per push; the memo keys on (mtime_ns, size),
    which the append-only log moves on exactly the sends that change the answer."""

    def setUp(self):
        self.log = km.jd.STATE / "timeline" / "messages.jsonl"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        km._postal_index_memo[0] = None
        self.addCleanup(lambda: km._postal_index_memo.__setitem__(0, None))
        self.addCleanup(lambda: self.log.unlink(missing_ok=True))

    def _row(self, mid, body):
        return json.dumps({"ev": "sent", "id": mid, "from": "web", "to_id": SID, "body": body, "t": 1781100000}) + "\n"

    def test_unchanged_log_returns_the_cached_object(self):
        self.log.write_text(self._row("m1", "hello"))
        first = km._postal_index()
        self.assertEqual(set(first), {"m1"})
        self.assertIs(km._postal_index(), first, "same (mtime_ns, size) → the memoized dict, no reparse")

    def test_an_append_invalidates_exactly(self):
        self.log.write_text(self._row("m1", "hello"))
        first = km._postal_index()
        with open(self.log, "a") as f:
            f.write(self._row("m2", "and back"))
        second = km._postal_index()
        self.assertIsNot(second, first)
        self.assertEqual(set(second), {"m1", "m2"})

    def test_missing_log_is_empty_and_unmemoized(self):
        self.log.unlink(missing_ok=True)
        self.assertEqual(km._postal_index(), {})
        self.log.write_text(self._row("m3", "late start"))
        self.assertEqual(set(km._postal_index()), {"m3"}, "the miss was not cached")


if __name__ == "__main__":
    unittest.main()
