#!/usr/bin/env python3
"""Timeline dead-lane dismissals are DURABLE (the user 2026-08-14: cleared sessions must stay cleared
through kernel restarts and reconnects). The set persists to timeline-dismissed.json, hydrates at boot,
and a revived sid sheds its record (the un-dismiss event). Since 2026-09-10 the set goes through the state-file
door like the flags, the order and the bells (ClearedLanesStateDoor below). Synthetic only — placeholder UUIDs,
temp state.
"""
import contextlib
import errno
import inspect
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"


class DurableDismissals(unittest.TestCase):
    def setUp(self):
        km._dismissed_lanes.clear()
        try:
            km._dismissed_lanes_file().unlink()
        except OSError:
            pass

    def test_dismiss_persists_to_disk(self):
        km._dismiss_lane(SID)
        self.assertIn(SID, km._dismissed_lanes)
        on_disk = json.loads(km._dismissed_lanes_file().read_text())
        self.assertEqual(on_disk, [SID])

    def test_a_restart_remembers_the_cleared_lanes(self):
        # Boot hydration is `_dismissed_lanes = _load_dismissed_lanes()` at module level; a restart is a
        # fresh call of that loader over the same state dir, which is what this asserts. Deliberately NOT
        # a full module re-execution: under pytest the whole suite shares one process, and the loader's
        # name-reuse gives RELOAD semantics — the re-executed kernel re-resolves the judge state root
        # from the LIVE env (moved by the conftest floor since this file's module body ran), so it reads
        # a different dir than the one this test wrote. That was a real CI-only failure (2026-08-14).
        km._dismiss_lane(SID)
        km._dismiss_lane(SID2)
        self.assertEqual(km._load_dismissed_lanes(), {SID, SID2})
        self.assertIn("_dismissed_lanes = _load_dismissed_lanes()", inspect.getsource(km))

    def test_a_revived_sid_sheds_its_record(self):
        km._dismiss_lane(SID)
        km._dismiss_lane(SID2)
        km._undismiss_lanes([SID])                      # SID came back live; SID2 stays cleared
        self.assertEqual(km._dismissed_lanes, {SID2})
        self.assertEqual(json.loads(km._dismissed_lanes_file().read_text()), [SID2])
        km._undismiss_lanes([SID])                      # already shed: a no-op, no rewrite crash
        self.assertEqual(km._dismissed_lanes, {SID2})

    def test_corrupt_or_wrong_shape_file_hydrates_empty_never_crashes(self):
        km._dismissed_lanes_file().parent.mkdir(parents=True, exist_ok=True)
        km._dismissed_lanes_file().write_text("{not json")
        self.assertEqual(km._load_dismissed_lanes(), set())
        km._dismissed_lanes_file().write_text(json.dumps({"sid": True}))   # an object, not a list
        self.assertEqual(km._load_dismissed_lanes(), set())


SID3 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SID4 = "ffffffff-0000-1111-2222-333333333333"
NODE = shutil.which("node")
VIEW_JS = os.path.join(os.path.dirname(HERE), "ui", "romp-timeline-view.js")


@contextlib.contextmanager
def _reads_fault(target):
    """Fail every byte read of ONE path with an EIO (the door's read_bytes and origin/main's read_text
    alike) for the duration of the block; everything else reads normally."""
    real_rb, real_rt = Path.read_bytes, Path.read_text
    tgt = str(target)

    def rb(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rb(self, *a, **k)

    def rt(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rt(self, *a, **k)
    Path.read_bytes, Path.read_text = rb, rt
    try:
        yield
    finally:
        Path.read_bytes, Path.read_text = real_rb, real_rt


@contextlib.contextmanager
def _writes_fault_after_truncate(target):
    """Fail every write of the store's name -- the file itself (origin/main writes it in place) and the
    `<name>.tmp.<pid>.<tid>.<n>` siblings the atomic door publishes through -- the way a full disk does it:
    open(2) succeeds and truncates (or creates) the file, write(2) fails with ENOSPC. What is left on disk
    afterwards is exactly the difference between the two writers."""
    real = Path.write_text
    name = Path(target).name

    def wt(self, *a, **k):
        if self.name == name or self.name.startswith(name + ".tmp."):
            with open(self, "w"):
                pass
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)
    Path.write_text = wt
    try:
        yield
    finally:
        Path.write_text = real


@contextlib.contextmanager
def _first_in_place_writer_yields(target, held, release):
    """The scheduler's view of two in-place writers: the FIRST write_text of the store file itself opens it
    for writing (the truncate), signals `held`, and finishes its write only after `release` -- the thread
    switch between truncate and write that a second writer lands inside. Later writes of the file run
    normally. A writer that publishes through a temp file and a rename never writes the file itself, so
    this seam never fires for it."""
    real = Path.write_text
    tgt = str(target)
    first = [True]

    def wt(self, data, *a, **k):
        if str(self) == tgt and first[0]:
            first[0] = False
            with open(self, "w") as f:
                held.set()
                release.wait(5)
                f.write(data)
            return len(data)
        return real(self, data, *a, **k)
    Path.write_text = wt
    try:
        yield
    finally:
        Path.write_text = real


@contextlib.contextmanager
def _first_reader_yields(target, held, release):
    """The scheduler's view of two read-modify-writers: the FIRST byte read of the store file reads, then
    signals `held` and hands its (now stale) bytes back only after `release` -- the thread switch between one
    writer's read and its publish that a second writer lands inside. Later reads run normally. With the
    store's lock the second writer waits at the lock; without it, it publishes into the gap and the first
    writer's publish, built on the stale bytes, then erases its change. A writer that never reads the file
    (origin/main publishes its memory copy) never trips it."""
    real = Path.read_bytes
    tgt = str(target)
    first = [True]

    def rb(self, *a, **k):
        data = real(self, *a, **k)
        if str(self) == tgt and first[0]:
            first[0] = False
            held.set()
            release.wait(5)
        return data
    Path.read_bytes = rb
    try:
        yield
    finally:
        Path.read_bytes = real


class ClearedLanesStateDoor(unittest.TestCase):
    """The cleared set goes through the same door as the flags, the order and the bells (2026-09-10): a torn
    file is moved aside, an unreadable one refuses the writers instead of reading as empty, a failed save
    keeps the last whole record, two writers take turns, and the WS arm answers the poster. Before this the
    loader folded every fault to an empty set and the writer truncated the file in place from two threads
    with no lock, so a kernel killed mid-write brought every cleared dead lane back at the next boot and the
    user's next Clear erased the record of all the others, with a stderr line as the only witness."""

    def setUp(self):
        km._dismissed_lanes.clear()
        self.p = km._dismissed_lanes_file()
        self.p.parent.mkdir(parents=True, exist_ok=True)
        self._sweep()
        getattr(km, "_state_fault_seen", {}).clear()
        getattr(km, "_state_write_fault_seen", {}).clear()

    def tearDown(self):
        km._dismissed_lanes.clear()
        self._sweep()

    def _sweep(self):
        for q in self.p.parent.glob(self.p.name + "*"):   # the file, its sidecars, any temp left behind
            q.unlink()

    def _client(self):
        sent = []
        return sent, {"app": "timeline", "wid": "w1", "alive": True, "send": lambda raw: sent.append(json.loads(raw))}

    def test_torn_bytes_are_moved_aside_before_the_set_starts_empty(self):
        self.p.write_text("{not json")                     # a kernel killed mid-write
        self.assertEqual(km._load_dismissed_lanes(), set())
        aside = list(self.p.parent.glob("timeline-dismissed.json.corrupt-*"))
        self.assertEqual(len(aside), 1, "the torn bytes are moved aside, never left in place for the next Clear to overwrite")
        self.assertEqual(aside[0].read_text(), "{not json", "the sidecar holds the ORIGINAL bytes")
        self.assertFalse(self.p.exists())

    def test_the_quarantine_notice_names_the_cleared_lanes_not_the_settings(self):
        # the dashboard hears a quarantine as "<what this file holds> start over empty"; the default noun is
        # "the settings", untrue for this file -- a struck lane's Clear is not a setting
        notices, saved = [], km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": notices.append((str(text), ok, kind))
        try:
            self.p.write_text("{not json")
            self.assertEqual(km._load_dismissed_lanes(), set())
        finally:
            km._sync_notice = saved
        self.assertEqual(len(notices), 1, "one notice for the move (origin/main files none: the bytes stay in place)")
        text, ok, kind = notices[0]
        self.assertEqual((ok, kind), (False, "refused"))
        self.assertIn("timeline-dismissed.json could not be parsed and was moved aside to timeline-dismissed.json.corrupt-", text)
        self.assertIn("the cleared lanes it held start over empty", text)
        self.assertNotIn("the settings", text)

    def test_a_clear_is_refused_while_the_store_cannot_be_read_and_the_record_stays(self):
        self.p.write_text(json.dumps([SID2]))              # the record an earlier kernel wrote
        raised = None
        with _reads_fault(self.p):
            km._dismissed_lanes.clear()
            km._dismissed_lanes.update(km._load_dismissed_lanes())   # the boot over a disk that will not read: nothing hydrates
            try:
                km._dismiss_lane(SID)                      # the user's next Clear
            except Exception as e:                         # noqa: BLE001 -- on origin/main nothing raises
                raised = e
        self.assertEqual(json.loads(self.p.read_text()), [SID2],
                         "the record on disk is untouched (origin/main writes the unproved empty set plus this sid over it)")
        self.assertEqual(type(raised).__name__, "_StateUnreadable",
                         "the Clear is refused LOUDLY, not folded to a fabricated empty (got %r)" % raised)
        self.assertEqual(km._load_dismissed_lanes(), {SID2}, "once the disk reads again the record is whole")

    def test_a_save_that_fails_keeps_the_last_whole_record_and_is_refused(self):
        km._dismiss_lane(SID2)
        before = self.p.read_bytes()
        raised = None
        with _writes_fault_after_truncate(self.p):
            try:
                km._dismiss_lane(SID)
            except Exception as e:                         # noqa: BLE001 -- on origin/main nothing raises
                raised = e
        self.assertEqual(self.p.read_bytes(), before,
                         "the file keeps its last whole contents (origin/main leaves the 0 bytes its in-place truncate made)")
        self.assertEqual(type(raised).__name__, "_StateUnwritable",
                         "the failed save is refused, not a stderr line (got %r)" % raised)
        self.assertEqual(km._dismissed_lanes, {SID2}, "the memory copy follows a landed write only")
        self.assertEqual(list(self.p.parent.glob(self.p.name + ".tmp.*")), [], "no temp file is left behind")

    def test_two_writers_whose_saves_overlap_leave_one_whole_current_record(self):
        for sid in (SID, SID2, SID3):
            km._dismiss_lane(sid)
        held, release = threading.Event(), threading.Event()
        errs = []

        def clear_one():
            try:
                km._dismiss_lane(SID4)                     # a dashboard's Clear, on its WS receive loop
            except Exception as e:                         # noqa: BLE001
                errs.append(e)
        with _first_in_place_writer_yields(self.p, held, release):
            a = threading.Thread(target=clear_one)
            a.start()
            while a.is_alive() and not held.wait(0.02):    # until A holds its truncated file open -- or has published whole
                pass
            km._undismiss_lanes([SID])                     # the pusher, meanwhile: the SID lane came back live
            release.set()
            a.join(5)
        self.assertEqual(errs, [])
        self.assertFalse(a.is_alive())
        on_disk = json.loads(self.p.read_text())
        self.assertEqual(set(on_disk), {SID2, SID3, SID4},
                         "both changes land: the new Clear and the revived lane's shed record (origin/main: the in-place "
                         "writer that truncated first lands last, and the revived lane's clear is back on disk)")
        self.assertEqual(on_disk, sorted(km._dismissed_lanes), "the disk and the memory copy agree")

    def test_a_writer_mid_save_holds_the_next_writer_so_no_change_is_lost(self):
        for sid in (SID, SID2, SID3):
            km._dismiss_lane(sid)
        held, release = threading.Event(), threading.Event()
        errs = []

        def run(fn, arg):
            try:
                fn(arg)
            except Exception as e:                         # noqa: BLE001
                errs.append(e)
        with _first_reader_yields(self.p, held, release):
            a = threading.Thread(target=run, args=(km._dismiss_lane, SID4))     # a dashboard's Clear, paused between its read and its publish
            a.start()
            while a.is_alive() and not held.wait(0.02):    # until A is paused mid-save -- or done (origin/main reads nothing)
                pass
            b = threading.Thread(target=run, args=(km._undismiss_lanes, [SID]))  # the pusher, meanwhile: the SID lane came back live
            b.start()
            b.join(0.3)
            still_waiting = b.is_alive()                   # observed before the release, asserted after the joins
            release.set()
            a.join(5)
            b.join(5)
        self.assertEqual(errs, [])
        self.assertFalse(a.is_alive() or b.is_alive())
        on_disk = json.loads(self.p.read_text())
        self.assertEqual(set(on_disk), {SID2, SID3, SID4},
                         "neither change is lost: without the lock the first writer's publish carries its stale read and "
                         "puts the revived lane's clear back over the second writer's record")
        self.assertEqual(on_disk, sorted(km._dismissed_lanes), "the disk and the memory copy agree")
        self.assertTrue(still_waiting, "the second writer waits while the first is mid-save (without the lock it publishes "
                                       "into the gap; origin/main serializes nothing)")

    def test_the_build_path_sheds_nothing_over_a_fault_and_never_raises(self):
        km._dismiss_lane(SID)
        km._dismiss_lane(SID2)
        with _reads_fault(self.p):
            km._undismiss_lanes([SID])                     # the pusher: the lane came back live, the disk will not read
        self.assertEqual(json.loads(self.p.read_text()), [SID, SID2], "the record stands until the store can be read again")
        self.assertEqual(km._dismissed_lanes, {SID, SID2})
        km._undismiss_lanes([SID])                         # the disk reads again: the revive sheds the record now
        self.assertEqual(json.loads(self.p.read_text()), [SID2])

    def test_a_clear_that_lands_ends_the_fault_episode_so_the_next_fault_is_said_again(self):
        # _note_state_fault says a read fault ONCE per episode, keyed on the path, and only a clean read of
        # that path ends the episode. The other stores' display readers end theirs on every build; this store
        # has none (build_timeline reads the memory copy), so the proved read on the mutation path is the
        # one clean read it has -- without the clear there, one fault muted every later fault with the same
        # text for the life of the process, however many Clears landed in between
        notices, saved = [], km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": notices.append((str(text), ok, kind))
        try:
            km._dismiss_lane(SID)
            with _reads_fault(self.p):
                km._undismiss_lanes([SID])             # the pusher: the lane came back live over a disk that will not read
            self.assertEqual(len(notices), 1, "the first fault is said")
            km._dismiss_lane(SID2)                     # the disk reads again and a Clear lands: the episode is over
            self.assertEqual(km._dismissed_lanes, {SID, SID2})
            with _reads_fault(self.p):
                km._undismiss_lanes([SID])             # a second episode, with the same errno text as the first
        finally:
            km._sync_notice = saved
        self.assertEqual(len(notices), 2, "the second episode is said too (without the clear on the mutation path it is "
                                          "deduped against the first for the life of the process)")
        self.assertEqual([(ok, kind) for _, ok, kind in notices], [(False, "refused")] * 2)
        self.assertIn("timeline-dismissed.json could not be read", notices[1][0])

    def test_the_ws_arm_answers_a_refused_clear_on_the_posters_socket(self):
        km._dismiss_lane(SID2)
        before = self.p.read_bytes()
        sent, client = self._client()
        dirty, saved = [], km._mark_views_dirty
        km._mark_views_dirty = lambda: dirty.append(1)
        try:
            with _reads_fault(self.p):
                km.Handler._dispatch_ws(None, {"type": "dismissLane", "id": SID}, client)
        finally:
            km._mark_views_dirty = saved
        self.assertEqual(len(sent), 1, "the poster hears the refusal (origin/main answers nothing)")
        fr = sent[0]
        self.assertEqual((fr["type"], fr["gesture"], fr["sid"], fr["value"]), ("settingRefused", "lane", SID, None))
        self.assertIn("couldn't save that clear", fr["text"])
        self.assertIn("timeline-dismissed.json could not be read", fr["text"])
        self.assertEqual(self.p.read_bytes(), before, "the record on disk is untouched")
        self.assertEqual(dirty, [], "nothing rebuilds off a Clear that did not land")
        self.assertTrue(client["alive"])

    def test_the_ws_arm_refuses_a_clear_whose_save_fails_instead_of_a_stderr_line(self):
        km._dismiss_lane(SID2)
        before = self.p.read_bytes()
        sent, client = self._client()
        dirty, saved = [], km._mark_views_dirty
        km._mark_views_dirty = lambda: dirty.append(1)
        try:
            with _writes_fault_after_truncate(self.p):
                try:
                    km.Handler._dispatch_ws(None, {"type": "dismissLane", "id": SID}, client)
                except OSError:
                    self.fail("the OSError escaped _dispatch_ws -- the receive loop re-raises it and drops the client")
        finally:
            km._mark_views_dirty = saved
        self.assertEqual(len(sent), 1, "the poster hears the refusal (origin/main writes a stderr line and answers nothing)")
        fr = sent[0]
        self.assertEqual((fr["type"], fr["gesture"], fr["sid"]), ("settingRefused", "lane", SID))
        self.assertIn("couldn't save that clear", fr["text"])
        self.assertIn("timeline-dismissed.json could not be written", fr["text"])
        self.assertEqual(self.p.read_bytes(), before, "the file keeps its last whole contents")
        self.assertEqual(dirty, [])
        self.assertTrue(client["alive"])

    def test_a_clear_that_lands_answers_nothing_and_rebuilds(self):
        sent, client = self._client()
        dirty, saved = [], km._mark_views_dirty
        km._mark_views_dirty = lambda: dirty.append(1)
        try:
            km.Handler._dispatch_ws(None, {"type": "dismissLane", "id": SID}, client)
        finally:
            km._mark_views_dirty = saved
        self.assertEqual(sent, [])
        self.assertEqual(dirty, [1])
        self.assertEqual(json.loads(self.p.read_text()), [SID])


_JS_HARNESS = r"""
const { TimelinePanel } = require(process.argv[1]);
const r = {};
function makeView() {
  const v = Object.create(TimelinePanel.prototype);
  v._dismissed = new Set(); v._pendingFlags = {};   // built without the constructor, like ui/timeline-dismiss.test.ts
  v.data = { sessions: [{ id: 'a', live: true }, { id: 'b', live: false }, { id: 'c', live: true }] };
  v.draws = 0; v.draw = function () { this.draws++; };
  return v;
}
// the Clear pill's state step; origin/main has no such step, its pointerdown handler inlines these two lines
function clear(v, s) {
  if (typeof v._holdDismissed === 'function') { v._holdDismissed(s); return; }
  v._dismissed.add(s.id);
  v.data.sessions = v.data.sessions.filter((x) => x.id !== s.id);
}
const ids = (v) => v.data.sessions.map((s) => s.id).join(',');
{
  // a Clear the kernel refuses: the row comes back in its slot on the refusal, and the next push carrying it is not filtered
  const v = makeView(); const b = v.data.sessions[1];
  clear(v, b);
  r["cleared_hides_at_once"] = [ids(v), 'a,c'];
  v.settingRefused({ type: 'settingRefused', gesture: 'lane', sid: 'b', text: "couldn't save that clear" });
  r["refused_row_back_in_slot"] = [ids(v), 'a,b,c'];
  r["refused_hold_released"] = [v._dismissed.has('b'), false];
  r["refused_drew"] = [v.draws, 1];
  v.data = { sessions: [{ id: 'a', live: true }, { id: 'b', live: false }, { id: 'c', live: true }] };
  v._reconcileDismissed();
  r["next_push_keeps_the_lane"] = [ids(v), 'a,b,c'];
}
{
  // a refusal for ANOTHER lane leaves this Clear held
  const v = makeView(); const b = v.data.sessions[1];
  clear(v, b);
  v.settingRefused({ type: 'settingRefused', gesture: 'lane', sid: 'zz', text: 'x' });
  r["other_lane_refusal_keeps_hold"] = [ids(v) + '|' + v._dismissed.has('b'), 'a,c|true'];
}
{
  // the kernel confirms (its frame no longer carries the lane): the hold and the kept row are both dropped
  const v = makeView(); const b = v.data.sessions[1];
  clear(v, b);
  v.data = { sessions: [{ id: 'a', live: true }, { id: 'c', live: true }] };
  v._reconcileDismissed();
  r["confirmed_drops_hold"] = [v._dismissed.size + '|' + (v._dismissedRows ? v._dismissedRows.size : 0), '0|0'];
}
process.stdout.write(JSON.stringify(r));
"""


@unittest.skipUnless(NODE, "node not available")
class ClearedLaneRefusalOnThePage(unittest.TestCase):
    """The page's half: a Clear the kernel refuses (settingRefused, gesture 'lane') puts the lane straight back
    and releases the sticky hold that re-applies the removal onto every push -- else the refused lane stayed
    hidden until a reload (the kernel's lanes frame is unchanged, so no push carries it for up to a minute).
    Drives the real prototype methods under node, the way test_timeline_touch.py does; synthetic ids."""

    def test_a_refused_clear_puts_the_lane_back(self):
        out = subprocess.run([NODE, "-e", _JS_HARNESS, VIEW_JS], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, f"node harness failed:\n{out.stderr}")
        for name, (got, want) in json.loads(out.stdout).items():
            self.assertEqual(got, want, f"{name}: got {got!r}, want {want!r}")


if __name__ == "__main__":
    unittest.main()
