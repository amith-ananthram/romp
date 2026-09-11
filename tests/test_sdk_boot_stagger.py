#!/usr/bin/env python3
"""Staggered boot-reconcile resumes (2026-07-20). Spawning every reconciled session's CLI at once
detonated a fleet-wide CPU storm — each resumed claude burns ~a full core catching up on its
transcript, so a 13-session restart pegged the machine (load ~20) and starved the kernel's own boot;
the user's restart looked hung. The reconcile now spawns behind BOOT_RESUME_CONCURRENCY semaphore
slots, each freed by the EVENT that the CLI is past its catch-up burst (its first init message, or
its thread dying), with the acquire timeout as a loud backstop only.

All deterministic: no SDK import, no real claude processes, no sleeps as the mechanism — the tests
synchronize on the stub's own call events.
"""
import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_stagger", os.path.join(BIN, "romp_sdk_backend.py"))


def _backend(d=None, log=None):
    return sb.SdkBackend(d or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None,
                         log=log or (lambda *a, **k: None))


def _reg(d, sid, **extra):
    r = {"sid": sid, "name": "s-" + sid[:4], "cwd": "/tmp", "alive": True, "lastSid": sid}
    r.update(extra)
    sb.write_reg(Path(d), sid, r)
    return r


def _cut_regs(d, n):
    """n registries whose state tail is 'working' — cut turns the reconcile must resume."""
    regs = []
    for i in range(n):
        sid = "11111111-bbbb-0000-0000-%012d" % i
        regs.append(_reg(d, sid))
        sb.append_state(Path(d), sid, "working")
    return regs


class StaggerBoundsConcurrency(unittest.TestCase):
    def test_fourth_spawn_waits_for_a_released_slot(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        calls = queue.Queue()          # every _ensure call lands here the moment it happens
        releases = []                  # parked slot-release callbacks, NOT fired automatically
        rel_lock = threading.Lock()

        def fake_ensure(sid, on_boot_settled=None):
            with rel_lock:
                releases.append(on_boot_settled)
            calls.put(sid)

        be._ensure = fake_ensure
        regs = _cut_regs(d, sb.BOOT_RESUME_CONCURRENCY + 2)
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            t = threading.Thread(target=be._boot_reconcile, args=(regs,), daemon=True)
            t.start()
            # the first CONCURRENCY spawns happen freely
            first = [calls.get(timeout=10) for _ in range(sb.BOOT_RESUME_CONCURRENCY)]
            self.assertEqual(len(first), sb.BOOT_RESUME_CONCURRENCY)
            # spawn N+1 must NOT arrive while every slot is held (small negative window is the
            # only way to observe "did not happen")
            with self.assertRaises(queue.Empty):
                calls.get(timeout=0.4)
            # the settle EVENT frees a slot → the next spawn follows at once
            with rel_lock:
                cb = releases[0]
            self.assertIsNotNone(cb, "a fresh spawn must carry the slot release")
            cb()
            calls.get(timeout=10)      # spawn N+1 arrives
            with rel_lock:
                for r in releases[1:]:
                    if r:
                        r()
            calls.get(timeout=10)      # the final spawn arrives once more slots free
            t.join(timeout=10)
            self.assertFalse(t.is_alive(), "the sweep finishes once every spawn got a slot")

    def test_backstop_expiry_continues_the_sweep_loudly(self):
        d = tempfile.mkdtemp()
        logs = []
        be = _backend(d, log=lambda m: logs.append(str(m)))
        ensured = []
        be._ensure = lambda sid, on_boot_settled=None: ensured.append(sid)   # NEVER settles
        regs = _cut_regs(d, sb.BOOT_RESUME_CONCURRENCY + 2)
        with mock.patch.object(sb, "BOOT_RESUME_SLOT_S", 0.05), \
             mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(len(ensured), len(regs),
                         "wedged CLIs must never trap the sweep — every session still spawns")
        self.assertTrue(any("backstop expired" in m for m in logs),
                        "the backstop path is LOUD, never silent")


class ThreadsStayDormant(unittest.TestCase):
    """Comment threads are never auto-resumed at boot (the user 2026-09-01: threads persist on disk
    and come alive only on an explicit reply/branch). A cut thread turn or a persisted thread queue
    stays lazy; top-level sessions with the same shape still resume."""

    def test_boot_reconcile_skips_a_cut_thread_but_heals_its_flags(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (ensured.append(sid), on_boot_settled and on_boot_settled())[0]
        regs = _cut_regs(d, 2)
        tsid = "11111111-bbbb-0000-0000-00000000dead"
        regs.append(_reg(d, tsid, threadOf="11111111-bbbb-0000-0000-000000000000", modelPending=True))
        sb.append_state(Path(d), tsid, "working")     # a cut thread turn, no queue
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(sorted(ensured), sorted(r["sid"] for r in regs[:2]),
                         "the two top-level cut sessions resume; the thread stays dormant")
        self.assertFalse(sb.read_reg(Path(d), tsid).get("modelPending"),
                         "the pending-flag heal still runs for a dormant thread")

    def test_a_threads_persisted_queue_earns_the_resume(self):
        # the user's own typed reply the CLI never started: delivering it honors an explicit gesture
        d = tempfile.mkdtemp()
        be = _backend(d)
        ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (ensured.append(sid), on_boot_settled and on_boot_settled())[0]
        tsid = "11111111-bbbb-0000-0000-00000000beef"
        regs = [_reg(d, tsid, threadOf="11111111-bbbb-0000-0000-000000000000", queue=["a queued reply"])]
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(ensured, [tsid])

    def test_boot_leaves_a_dormant_threads_death_flags_for_its_wake(self):
        # a killed question / dead background tasks are reported at the thread's explicit wake
        # (ThreadWakeHearsItsDeadLife below), so the sweep neither resumes the thread for them nor
        # clears them: the flags stay on disk and nothing is queued — a notice persisted here would
        # read as a queue at the next boot and earn the very resume this skip forbids
        d = tempfile.mkdtemp()
        be = _backend(d)
        ensured = []
        be._ensure = lambda sid, on_boot_settled=None: (ensured.append(sid), on_boot_settled and on_boot_settled())[0]
        tsid = "11111111-bbbb-0000-0000-00000000f1a6"
        tasks = [{"desc": "release watcher", "since": 1}]
        regs = [_reg(d, tsid, threadOf="11111111-bbbb-0000-0000-000000000000", pendingAsk=True, bgTasks=tasks)]
        sb.append_state(Path(d), tsid, "waiting")
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile(regs)
        self.assertEqual(ensured, [], "no resume for a thread's dead life alone")
        reg = sb.read_reg(Path(d), tsid)
        self.assertEqual((reg.get("queue") or [], bool(reg.get("pendingAsk")), reg.get("bgTasks")),
                         ([], True, tasks), "nothing queued; both flags left for the wake to report")

    def test_the_orphan_reap_still_covers_a_threads_leftover_cli(self):
        # the skip sits INSIDE the resume loop, after the reap built its sid list from every alive
        # reg — a dead kernel's thread CLI is still a zombie writer nobody manages
        import inspect
        src = inspect.getsource(sb.SdkBackend._boot_reconcile)
        reap, skip = 'lastsids = [str(r.get("lastSid")', 'if r.get("threadOf") and not queued:'
        self.assertIn(reap, src)
        self.assertIn(skip, src)
        self.assertLess(src.index(reap), src.index(skip), "reap first over every alive reg, then the skip")


class ThreadWakeRemap(unittest.TestCase):
    """T223 rider: a thread registered on a superseded full model id comes up on the replacement the
    kernel's hook names, persisted, at its explicit wake — and only threads, only when a hook is set."""

    class _Rec:
        made = []

        def __init__(self, backend, reg):
            self.reg = dict(reg)
            self.thread = mock.Mock(is_alive=lambda: True)
            self.on_boot_settled = None
            ThreadWakeRemap._Rec.made.append(self.reg)

        def start(self):
            pass

    def _wake(self, reg_extra, hook):
        d = tempfile.mkdtemp()
        be = _backend(d)
        be.thread_wake_model = hook
        sid = "11111111-bbbb-0000-0000-0000000000aa"
        _reg(d, sid, model="claude-fable-5", **reg_extra)
        self._Rec.made = []
        with mock.patch.object(sb, "SdkSession", self._Rec):
            be._ensure(sid)
        return self._Rec.made[0]["model"], sb.read_reg(Path(d), sid).get("model")

    THREAD = {"threadOf": "11111111-bbbb-0000-0000-000000000000", "spawnedAt": 1700000000}

    def test_a_dormant_threads_superseded_id_remaps_and_persists(self):
        spawned, on_disk = self._wake(dict(self.THREAD),
                                      lambda m: "claude-fable-5-1" if m == "claude-fable-5" else None)
        self.assertEqual((spawned, on_disk), ("claude-fable-5-1", "claude-fable-5-1"))

    def test_the_label_the_popover_reads_is_refreshed_too(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        be.thread_wake_model = lambda m: "claude-fable-5-1"
        sid = "11111111-bbbb-0000-0000-0000000000ab"
        _reg(d, sid, model="claude-fable-5", liveModel="Fable 5", **self.THREAD)
        self._Rec.made = []
        with mock.patch.object(sb, "SdkSession", self._Rec):
            be._ensure(sid)
        self.assertNotEqual(sb.read_reg(Path(d), sid).get("liveModel"), "Fable 5", "no stale label")

    def test_a_fresh_forks_first_connect_keeps_the_dialogs_pick(self):
        # no spawnedAt = it has never run: the model is the fork dialog's explicit choice, not a
        # dormant registration to modernize
        spawned, on_disk = self._wake({"threadOf": "11111111-bbbb-0000-0000-000000000000"},
                                      lambda m: "claude-fable-5-1")
        self.assertEqual((spawned, on_disk), ("claude-fable-5", "claude-fable-5"))

    def test_a_top_level_session_is_never_remapped_here(self):
        spawned, on_disk = self._wake({"spawnedAt": 1700000000}, lambda m: "claude-fable-5-1")
        self.assertEqual((spawned, on_disk), ("claude-fable-5", "claude-fable-5"))

    def test_no_hook_means_no_remap(self):
        spawned, on_disk = self._wake(dict(self.THREAD), None)
        self.assertEqual((spawned, on_disk), ("claude-fable-5", "claude-fable-5"))


class ThreadWakeHearsItsDeadLife(unittest.TestCase):
    """The boot sweep leaves a dormant thread alone (ThreadsStayDormant), so the two things it tells
    a resumed top-level session about its dead life — a question the kernel's death killed
    (pendingAsk) and background tasks that died with the process (the bgTasks mirror) — reach a
    thread at its EXPLICIT wake instead, in _ensure: once, ahead of the reply that woke it, and the
    flags clear with the report. Before this nothing on the wake path read either flag: they rode
    across every boot and wake until a later boot found the thread WITH a queued reply, and that
    sweep prepended the stale notices ahead of the reply the user had just typed."""

    class _Rec:                       # stands in for SdkSession: records the reg it was built from
        made = []
        fed = []

        def __init__(self, backend, reg):
            self.thread = mock.Mock(is_alive=lambda: True)
            self.on_boot_settled = None
            ThreadWakeHearsItsDeadLife._Rec.made.append(dict(reg))

        def start(self):
            pass

        def enqueue(self, text):
            ThreadWakeHearsItsDeadLife._Rec.fed.append(text)

    PARENT = "11111111-bbbb-0000-0000-000000000000"
    TASKS = [{"desc": "release watcher", "since": 1}]

    def setUp(self):
        self._Rec.made, self._Rec.fed = [], []
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)

    def _flags(self, sid):
        reg = sb.read_reg(Path(self.d), sid)
        return bool(reg.get("pendingAsk")), reg.get("bgTasks") or []

    def test_the_explicit_wake_prepends_both_notices_and_clears_the_flags_once(self):
        sid = "11111111-bbbb-0000-0000-0000000000b1"
        _reg(self.d, sid, threadOf=self.PARENT, pendingAsk=True, bgTasks=list(self.TASKS))
        with mock.patch.object(sb, "SdkSession", self._Rec):
            self.be._ensure(sid)
            self.assertEqual((self._Rec.made[0].get("queue") or []),
                             [sb.ASK_DIED_NOTICE, sb.task_death_notice(self.TASKS)],
                             "the fresh session is seeded with both notices, question first")
            self.assertEqual(self._flags(sid), (False, []), "reported → cleared on disk")
            self.be._update_reg(sid, queue=[])           # the life feeds its queue (_persist_queue)…
            self.be.sessions.pop(sid)                    # …and ends
            self.be._ensure(sid)                         # the next wake owes nothing
        self.assertEqual((self._Rec.made[1].get("queue") or []), [], "once per death, never a nag")

    def test_the_reply_that_woke_it_follows_the_notices(self):
        sid = "11111111-bbbb-0000-0000-0000000000b2"
        _reg(self.d, sid, threadOf=self.PARENT, pendingAsk=True)
        with mock.patch.object(sb, "SdkSession", self._Rec):
            self.assertTrue(self.be.send(sid, "the reply that woke it"))
        self.assertEqual((self._Rec.made[0].get("queue") or []), [sb.ASK_DIED_NOTICE], "the seed carries the notice…")
        self.assertEqual(self._Rec.fed, ["the reply that woke it"], "…and the reply enqueues behind it")

    def test_a_top_level_wake_is_the_boot_sweeps_business(self):
        sid = "11111111-bbbb-0000-0000-0000000000b3"
        _reg(self.d, sid, pendingAsk=True, bgTasks=list(self.TASKS))
        with mock.patch.object(sb, "SdkSession", self._Rec):
            self.be._ensure(sid)
        self.assertEqual((self._Rec.made[0].get("queue") or []), [])
        self.assertEqual(self._flags(sid), (True, self.TASKS),
                         "a top-level session's flags are the sweep's to report, at the next boot")

    def test_a_thread_resumed_at_boot_for_its_reply_hears_each_notice_once(self):
        # the sweep DOES resume a thread with a queued reply, reports and clears the flags itself,
        # then spawns through the real _ensure — which must find nothing left to report
        sid = "11111111-bbbb-0000-0000-0000000000b4"
        regs = [_reg(self.d, sid, threadOf=self.PARENT, queue=["the reply"],
                     pendingAsk=True, bgTasks=list(self.TASKS))]
        with mock.patch.object(sb, "SdkSession", self._Rec), \
             mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            self.be._boot_reconcile(regs)
        self.assertEqual((self._Rec.made[0].get("queue") or []),
                         [sb.ASK_DIED_NOTICE, sb.task_death_notice(self.TASKS), "the reply"])

    def test_a_flagless_thread_wake_writes_nothing(self):
        sid = "11111111-bbbb-0000-0000-0000000000b5"
        _reg(self.d, sid, threadOf=self.PARENT)
        before = sb._reg_path(Path(self.d), sid).read_bytes()
        with mock.patch.object(sb, "SdkSession", self._Rec):
            self.be._ensure(sid)
        self.assertEqual(sb._reg_path(Path(self.d), sid).read_bytes(), before, "no reg churn on a plain wake")


class FireBootSettled(unittest.TestCase):
    def _session(self, d=None):
        d = d or tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-cccc-0000-0000-000000000001"
        return be, sb.SdkSession(be, _reg(d, sid))   # never started: pure surface

    def test_fires_exactly_once(self):
        _, s = self._session()
        fired = []
        s.on_boot_settled = lambda: fired.append(1)
        s._fire_boot_settled()
        s._fire_boot_settled()                       # the init path racing the death path
        self.assertEqual(fired, [1])

    def test_callback_exception_never_propagates(self):
        _, s = self._session()
        s.on_boot_settled = mock.Mock(side_effect=RuntimeError("boom"))
        s._fire_boot_settled()                       # must not raise

    def test_thread_death_fires_the_release(self):
        be, s = self._session()
        fired = []
        s.on_boot_settled = lambda: fired.append(1)

        async def dies():
            raise RuntimeError("spawn failed")

        s._amain = dies
        with mock.patch.object(be, "_on_session_gone"):
            s._run()                                 # synchronous: the real thread body
        self.assertEqual(fired, [1], "a dead thread frees its boot-stagger slot")


class EnsureNoSpawnPaths(unittest.TestCase):
    def test_dead_registry_settles_immediately(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-dddd-0000-0000-000000000001"
        _reg(d, sid, alive=False)
        fired = []
        self.assertIsNone(be._ensure(sid, on_boot_settled=lambda: fired.append(1)))
        self.assertEqual(fired, [1], "no spawn → no CPU burst → the slot frees at once")

    def test_missing_registry_settles_immediately(self):
        be = _backend()
        fired = []
        self.assertIsNone(be._ensure("11111111-dddd-0000-0000-000000000002",
                                     on_boot_settled=lambda: fired.append(1)))
        self.assertEqual(fired, [1])

    def test_already_running_session_settles_immediately(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sid = "11111111-dddd-0000-0000-000000000003"
        _reg(d, sid)
        alive = mock.Mock()
        alive.thread.is_alive.return_value = True
        be.sessions[sid] = alive
        fired = []
        self.assertIs(be._ensure(sid, on_boot_settled=lambda: fired.append(1)), alive)
        self.assertEqual(fired, [1], "an already-live session holds no slot")


class BootAttachesOffTheStagger(unittest.TestCase):
    """The restart-path work (2026-09-11): a boot RE-ATTACH to a live session host is a socket connect, not a claude
    launch, so it runs first and on its own wider bound (BOOT_ATTACH_CONCURRENCY), never on the spawn stagger's
    three slots; the reconcile reports censusDone before any session starts and attachDone once the last attach
    settled (immediately when there is none). Deterministic: a stub _ensure, a stub lease reading."""

    def _host_lease(self, d, sid, cli, host):
        sb.write_lease(d, {"sid": sid, "fsid": sid, "pid": cli, "start": "1", "holder": {"pid": host, "start": "2", "kind": "host"},
                           "version": "", "t": __import__("time").time()})

    def test_attaches_run_first_all_at_once_and_cold_launches_keep_the_stagger(self):
        d = tempfile.mkdtemp()
        phases = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None, boot_phase=phases.append)
        attach_sids = ["22222222-aaaa-0000-0000-%012d" % i for i in range(6)]
        cold = _cut_regs(d, 4)
        regs = []
        alive = {}
        for i, sid in enumerate(attach_sids):
            regs.append(_reg(d, sid))
            cli, host = 900000000 + 2 * i, 900000001 + 2 * i
            self._host_lease(d, sid, cli, host); alive[cli] = "1"; alive[host] = "2"
        regs += cold
        calls = queue.Queue(); settles = {}
        def fake_ensure(sid, on_boot_settled=None):
            calls.put(sid); settles[sid] = on_boot_settled
            return object()
        # the lease classifier reads proc_start from the module registered as romp_sdk_backend (this module loads the
        # backend under another name), so the patched reading must be the one it finds
        with mock.patch.dict(sys.modules, {"romp_sdk_backend": sb}), \
             mock.patch.object(sb, "proc_start", lambda p, run=None: alive.get(p)), \
             mock.patch.object(be, "_ensure", fake_ensure):
            t = threading.Thread(target=be._boot_reconcile, args=(regs,), daemon=True); t.start()
            seen = [calls.get(timeout=5) for _ in range(6 + sb.BOOT_RESUME_CONCURRENCY)]
        self.assertEqual(seen[:6], attach_sids, "every attach is issued first, none waiting on a spawn slot")
        self.assertEqual(len([s for s in seen if s not in attach_sids]), sb.BOOT_RESUME_CONCURRENCY,
                         "the cold launches fill the spawn stagger's slots and the fourth waits")
        self.assertTrue(calls.empty(), "the fourth cold launch waits for a released slot")
        self.assertEqual(phases, ["censusDone"], "the census ended before any start; attachDone waits for the attaches' hellos")
        for sid in attach_sids:
            settles[sid]()                 # the host's hello (the session fires on_boot_settled once)
        self.assertEqual(phases, ["censusDone", "attachDone"], "attachDone once the last attach settled")
        for sid in attach_sids:
            settles[sid]()                 # a second fire (a death after the hello) is ignored
        self.assertEqual(phases.count("attachDone"), 1)
        for sid in list(settles):
            if sid not in attach_sids and settles[sid]:
                settles[sid]()             # release the cold slots so the thread can finish
        t.join(5)

    def test_no_attaches_means_attach_done_at_once_and_a_never_started_attach_counts_down(self):
        d = tempfile.mkdtemp()
        phases = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None, boot_phase=phases.append)
        regs = _cut_regs(d, 1)
        with mock.patch.object(be, "_ensure", lambda sid, on_boot_settled=None: (on_boot_settled and on_boot_settled()) or object()):
            be._boot_reconcile(regs)
        self.assertEqual(phases[:2], ["censusDone", "attachDone"], "nothing to attach: the phase is over before it began")
        # an attach the ensure refuses (a stood-down or dead session returns None) must not hold attachDone
        d2 = tempfile.mkdtemp(); phases2 = []
        be2 = sb.SdkBackend(d2, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None, boot_phase=phases2.append)
        sid = "33333333-aaaa-0000-0000-000000000001"
        regs2 = [_reg(d2, sid)]
        self._host_lease(d2, sid, 910000000, 910000001)
        with mock.patch.dict(sys.modules, {"romp_sdk_backend": sb}), \
             mock.patch.object(sb, "proc_start", lambda p, run=None: {910000000: "1", 910000001: "2"}.get(p)), \
             mock.patch.object(be2, "_ensure", lambda sid, on_boot_settled=None: None):
            be2._boot_reconcile(regs2)
        self.assertEqual(phases2, ["censusDone", "attachDone"], "a never-started attach counts down at once")


class BootBudget(unittest.TestCase):
    """A regression tripwire for the boot (the performance metrics, 2026-09-11): the reconcile over forty alive sessions
    with cut turns, every start stubbed, must read its census and reach its first start within a generous bound, and
    the census milestone must land before any start. Generous on purpose (a shared CI box): it catches a boot that
    started reading transcripts or waiting on something before it starts sessions, not a slow disk."""

    def test_forty_sessions_census_then_starts_inside_the_bound(self):
        import time as _time
        d = tempfile.mkdtemp()
        phases = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None, boot_phase=lambda k: phases.append((k, _time.monotonic())))
        regs = _cut_regs(d, 40)
        starts = []
        def fake_ensure(sid, on_boot_settled=None):
            starts.append(_time.monotonic())
            if on_boot_settled:
                on_boot_settled()           # the CLI proves up at once: the stagger never waits here
            return object()
        t0 = _time.monotonic()
        with mock.patch.object(be, "_ensure", fake_ensure):
            be._boot_reconcile(regs)
        total = _time.monotonic() - t0
        self.assertEqual(len(starts), 40)
        census = dict(phases).get("censusDone")
        self.assertIsNotNone(census, "the census milestone landed")
        self.assertLess(census, min(starts), "the census ends before the first start")
        self.assertLess(total, 5.0, "forty sessions reconciled and started within the bound: took %.2f s" % total)
        import json as _json
        rows = [_json.loads(l) for l in open(os.path.join(d, sb.SESSION_EVENTS_FILE)) if '"reconcile.boot"' in l]
        self.assertEqual(len(rows), 1)
        self.assertLess(rows[0]["durationS"], 5.0)


if __name__ == "__main__":
    unittest.main()
