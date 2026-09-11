#!/usr/bin/env python3
"""Automatic updates of THIS machine (the user 2026-08-09): at kernel boot, one async check reads
origin's newest release tag (git ls-remote — the remote's own refs, never the local tag list) and
compares it against the VERSION-file release. Modes (update-mode.json, default "ask" — ON out of
the box): ask = the shell's update banner offers it; auto = the kernel updates itself once per
discovered version; off = never checks. The update runs DETACHED (fetch + ff-only merge ONTO THE TAG + install.sh,
report to update-report.json, restart through the manager door only on success), and the outcome is
always filed as a sync notice — by the next boot, or by /update-check's poll on the still-running
kernel (fail loudly, never silent). Synthetic tags/paths only."""
import inspect
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from romp_load import load_source
from git_fixture import git, init_repo, forbid_background
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Raw-run belt over conftest's suspenders — via the ENVIRONMENT, not a module attribute: loading the
# kernel re-executes romp_judge into the same module object, which RESETS a hand-assigned jd.STATE
# back to the env-derived default (verified 2026-08-09 — the webpush test's attribute rebind only
# holds under pytest because conftest moves XDG_STATE_HOME first). ROMP_STATE_DIR is the designed
# override and is read on every (re)execution, so it protects a bare `python3 tests/...` run too.
# Scoped to the loads and RESTORED right after (the modules capture STATE at exec): left set, it
# outranks conftest's XDG_STATE_HOME for every test module pytest imports after this one — which is
# exactly how this file's first cut broke two postal-bus tests three modules downstream.
_STATE_TD = tempfile.TemporaryDirectory()
_PREV_STATE_DIR = os.environ.get("ROMP_STATE_DIR")
os.environ["ROMP_STATE_DIR"] = _STATE_TD.name
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# A dead manager port: any update/converge path a test exercises unstubbed dials nothing real.
# (2026-08-14: the converge route, hit by this suite while genuine main-drift existed, posted an
# IMMEDIATE restart-all to the LIVE manager — every suite run bounced every kernel on the box.)
os.environ["ROMP_MANAGER_PORT"] = "1"
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_update", os.path.join(BIN, "romp-kernel"))
if _PREV_STATE_DIR is None:
    os.environ.pop("ROMP_STATE_DIR", None)
else:
    os.environ["ROMP_STATE_DIR"] = _PREV_STATE_DIR


def _serve_get(path, headers=None):
    """The real do_GET over a fake socket (the webpush-test harness): (status, body_bytes)."""
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers = dict(headers or {})
    h.path = path
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_GET()
    return captured.get("status"), h.wfile.getvalue()


class Fresh(unittest.TestCase):
    """Every test starts with no update state: fresh STATE dir, empty avail/latch, empty notices."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        km._UPDATE_AVAIL[0] = ""
        km._UPDATE_STATE[0] = ""
        with km._SYNC_LOCK:
            del km._SYNC_NOTICES[:]

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def notices(self):
        with km._SYNC_LOCK:
            return list(km._SYNC_NOTICES)


class Semver(unittest.TestCase):
    def test_parses_plain_releases_only(self):
        self.assertEqual(km._semver("v0.6.0"), (0, 6, 0))
        self.assertEqual(km._semver("1.12.3"), (1, 12, 3))
        for junk in ("", None, "v1.2", "v1.2.3-rc1", "release", "v1.2.3.4", "v1.2.x"):
            self.assertIsNone(km._semver(junk), junk)

    def test_orders_numerically_not_lexically(self):
        self.assertGreater(km._semver("v0.10.0"), km._semver("v0.9.9"))


class ModeStore(Fresh):
    def test_default_is_ask_the_check_is_on_out_of_the_box(self):
        self.assertEqual(km._update_mode(), "ask")

    def test_set_and_persist(self):
        km._set_update_mode("auto")
        self.assertEqual(km._update_mode(), "auto")
        km._set_update_mode("off")
        self.assertEqual(km._update_mode(), "off")

    def test_garbage_is_refused_at_both_ends(self):
        km._set_update_mode("yes please")               # setter drops it
        self.assertEqual(km._update_mode(), "ask")
        (jd.STATE / "update-mode.json").write_text(json.dumps({"mode": "banana"}))
        self.assertEqual(km._update_mode(), "ask", "an unknown stored mode reads as the default")


class LatestReleaseTag(unittest.TestCase):
    def _ls(self, stdout, rc=0, stderr=""):
        return mock.patch.object(km.subprocess, "run", return_value=subprocess.CompletedProcess(
            args=[], returncode=rc, stdout=stdout, stderr=stderr))

    def test_picks_the_numerically_newest_real_release(self):
        out = ("aaa\trefs/tags/v0.9.9\n"
               "bbb\trefs/tags/v0.10.0\n"
               "ccc\trefs/tags/v0.10.0^{}\n"          # peeled duplicate of the annotated tag
               "ddd\trefs/tags/nightly\n"             # not a release number
               "eee\trefs/tags/v0.2.0\n")
        with self._ls(out):
            self.assertEqual(km._latest_release_tag(), "v0.10.0")

    def test_no_release_tags_is_empty_not_an_error(self):
        with self._ls("aaa\trefs/tags/nightly\n"):
            self.assertEqual(km._latest_release_tag(), "")

    def test_git_failure_raises_so_the_caller_can_say_so(self):
        with self._ls("", rc=128, stderr="fatal: could not read from remote"):
            with self.assertRaises(RuntimeError):
                km._latest_release_tag()


class UpdateCheck(Fresh):
    def test_mode_off_never_even_asks_the_network(self):
        km._set_update_mode("off")
        with mock.patch.object(km, "_latest_release_tag", side_effect=AssertionError("must not run")):
            km._update_check()
        self.assertEqual(km._UPDATE_AVAIL[0], "")

    def test_newer_release_in_ask_mode_raises_the_banner_on_every_shell(self):
        sent = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0+"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            km._update_check()
        self.assertEqual(km._UPDATE_AVAIL[0], "v0.7.0")
        self.assertEqual(sent, [("shell", {"type": "updateAvail", "cur": "v0.6.0+", "tag": "v0.7.0",
                                           "boot": km._BOOT_ID})],
                         "the offer names the kernel life it came from, so a page can retire it")

    def test_same_or_older_release_is_silence(self):
        sent = []
        for latest in ("v0.6.0", "v0.5.9", ""):
            with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
                 mock.patch.object(km, "_latest_release_tag", return_value=latest), \
                 mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
                km._update_check()
        self.assertEqual((km._UPDATE_AVAIL[0], sent), ("", []))

    def test_no_local_release_number_means_nothing_to_compare(self):
        with mock.patch.object(km, "_kernel_ver", return_value=None), \
             mock.patch.object(km, "_latest_release_tag", side_effect=AssertionError("must not run")):
            km._update_check()                        # no raise — the check just stands down

    def test_network_failure_is_a_stderr_note_never_a_crash(self):
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", side_effect=RuntimeError("no route to host")):
            km._update_check()
        self.assertEqual(km._UPDATE_AVAIL[0], "")

    def test_auto_mode_updates_once_per_discovered_version(self):
        ran = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) or True), \
             mock.patch.object(km, "_send_to_app"):
            km._set_update_mode("auto")
            km._update_check()
            self.assertEqual(ran, ["v0.7.0"])
            self.assertEqual(json.loads((jd.STATE / "update-attempted.json").read_text())["tag"], "v0.7.0")
            # a SECOND boot finding the same version does not loop the failed attempt — it says so
            # in the Log and falls back to offering the banner. (The in-memory discovery is
            # per-run, so a fresh boot starts empty — modeled by clearing it.)
            km._UPDATE_AVAIL[0] = ""
            km._update_check()
        self.assertEqual(ran, ["v0.7.0"], "one automatic attempt per version")
        self.assertTrue(any(not n["ok"] and "v0.7.0" in n["text"] for n in self.notices()))

    def test_rediscovering_the_same_release_mid_run_stays_quiet(self):
        # the 6h re-check re-finds a version for weeks — only a CHANGED discovery is new information
        sent = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
            km._update_check()
            km._update_check()
            km._update_check()
        self.assertEqual(len(sent), 1, "one banner push per discovered version, not one per pass")

    def test_a_newer_release_than_the_announced_one_reoffers(self):
        sent = []
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
            with mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"):
                km._update_check()
            with mock.patch.object(km, "_latest_release_tag", return_value="v0.8.0"):
                km._update_check()
        self.assertEqual([m["tag"] for m in sent], ["v0.7.0", "v0.8.0"])
        self.assertEqual(km._UPDATE_AVAIL[0], "v0.8.0")

    def test_a_mode_flip_applies_on_the_next_pass_without_a_restart(self):
        # every pass re-reads the mode: turning the gear setting on mid-run must not need a boot
        sent = []
        km._set_update_mode("off")
        with mock.patch.object(km, "_kernel_ver", return_value="v0.6.0"), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append(m)):
            with mock.patch.object(km, "_latest_release_tag", side_effect=AssertionError("off must not ask")):
                km._update_check()
            km._set_update_mode("ask")
            with mock.patch.object(km, "_latest_release_tag", return_value="v0.7.0"):
                km._update_check()
        self.assertEqual([m["tag"] for m in sent], ["v0.7.0"])


class CheckLoop(Fresh):
    def test_two_cadences_one_loop_and_a_crash_never_kills_the_thread(self):
        # The loop carries TWO watchers since the mesh-aware notice (the user 2026-08-14): the cheap
        # origin/main drift probe every round (minutes — a merge should be noticed promptly), the
        # release-tag check on its old six-hour stride. Either watcher dying must not kill the loop,
        # nor one watcher's crash starve the other.
        releases = []
        drifts = []
        converges = []
        naps = []

        # T230b: the loop is unhooked through ITS OWN seam - the stop event - never by patching the
        # process-global time.sleep (that patch, a counter raising SystemExit on call #2, was consumed
        # by leaked heartbeat threads sleeping in the window and hung five CI jobs for 15 silent
        # minutes each). The stub returns True on the 2nd wait (the event fired) and RAISES on any
        # later wait: a loop wired to wait but not to return would otherwise hang this very pin.
        class Stop:
            def wait(self, s):
                naps.append(s)
                if len(naps) == 2:
                    return True
                if len(naps) > 2:
                    raise AssertionError("the loop kept waiting after the stop event fired")
                return False

        def release_pass():
            releases.append(1)
            raise RuntimeError("boom")                 # a dying release check must not kill the loop…

        def drift_pass():
            drifts.append(1)
            if len(drifts) == 1:
                raise RuntimeError("boom")             # …nor a dying drift probe the NEXT drift probe

        def converge_pass():
            converges.append(1)                        # stubbed: the real one spawns node + rglobs ui/
            raise RuntimeError("boom")                 # …nor a dying dist converge any of them

        # T230c: FAST-FAIL if the loop ever regresses to the shared time.sleep. Without this guard the
        # test blocked in a real 300 s sleep until the 600 s CI backstop, whose os._exit then swallowed
        # this pin's own named failure (reproduced: `F` + the timeout dump, the message nowhere).
        # Main-thread-only, a plain function (never a recording mock a foreign sleeper could fill).
        main = threading.get_ident()

        def guard(s):
            if threading.get_ident() == main:
                raise AssertionError("the update-check loop must wait on its own seam, not the shared time.sleep")
        with mock.patch.object(km, "_update_check", side_effect=release_pass), \
             mock.patch.object(km, "_main_drift_check", side_effect=drift_pass), \
             mock.patch.object(km, "_dist_converge_check", side_effect=converge_pass), \
             mock.patch.object(km.time, "sleep", guard), \
             mock.patch.object(km, "_CHECK_LOOP_STOP", Stop()):
            km._update_check_loop()                    # RETURNS on the stop event
        self.assertEqual(len(releases), 1, "the six-hour stride: one release check across two fast rounds")
        self.assertEqual(len(drifts), 2, "the drift probe runs every round, surviving its own crash")
        self.assertEqual(len(converges), 2, "the dist converge runs every round, surviving its own crash")
        self.assertEqual(naps, [km._MAIN_CHECK_EVERY_S] * 2)
        self.assertEqual(km._UPDATE_CHECK_EVERY_S, 6 * 3600)
        self.assertEqual(km._MAIN_CHECK_EVERY_S, 300)


    def test_the_loop_never_sleeps_through_the_shared_time_sleep(self):
        # T230b (the user 2026-09-03, five hung CI jobs since 08-27): the loop's cadence wait must be a
        # loop-PRIVATE seam, never the process-global time.sleep. The sibling test above used to
        # patch km.time.sleep with a counter that raised SystemExit on call #2 — and any OTHER
        # thread sleeping in the window (test_heartbeat_thread leaks _heartbeat daemons that sleep
        # every 10s for the rest of the run) consumed a count; when a foreign thread drew #2,
        # threading swallowed its SystemExit and the loop spun forever on a no-op sleep: a silent
        # 15-minute job. Deterministic, no timing: a spinning foreign sleeper runs throughout; the
        # shared sleep is a PLAIN guard (never a recording mock the spinner would fill) that raises
        # only on the MAIN thread — so a loop reaching time.sleep fails in milliseconds, never hangs.
        # The seam patch deliberately has NO create=True (T230c): an absent or RENAMED seam then
        # fails as an AttributeError in under a second — still red-first and named — where create=True
        # would have minted a dead attribute and let the loop wait on the real Event to the backstop.
        import threading
        stop = threading.Event()
        main = threading.get_ident()

        def spinner():
            while not stop.is_set():
                time.sleep(0)
        t = threading.Thread(target=spinner, daemon=True)
        t.start()
        releases, drifts, naps = [], [], []

        def guard(s):
            if threading.get_ident() == main:
                raise AssertionError("the update-check loop must wait on its own seam, not the shared time.sleep")

        class Stub:
            def wait(self, s):
                naps.append(s)
                if len(naps) == 2:
                    return True                        # the stop event fires → the loop must RETURN
                if len(naps) > 2:
                    raise AssertionError("the loop kept waiting after the stop event fired")
                return False

        def release_pass():
            releases.append(1)
            raise RuntimeError("boom")

        def drift_pass():
            drifts.append(1)
        try:
            with mock.patch.object(km, "_update_check", side_effect=release_pass), \
                 mock.patch.object(km, "_main_drift_check", side_effect=drift_pass), \
                 mock.patch.object(km, "_dist_converge_check"), \
                 mock.patch.object(km.time, "sleep", guard), \
                 mock.patch.object(km, "_CHECK_LOOP_STOP", Stub()):
                km._update_check_loop()               # returns — the stop event is the loop's exit
        finally:
            stop.set()
            t.join(timeout=2)
        self.assertEqual(naps, [km._MAIN_CHECK_EVERY_S] * 2)
        self.assertEqual(len(releases), 1)
        self.assertEqual(len(drifts), 2)


class RunUpdate(Fresh):
    def test_detached_child_lands_on_the_tag_installs_reports_and_restarts_only_on_success(self):
        calls = []
        # The release remote is pinned: this test's Popen seam swallows every subprocess, the
        # resolver's `git remote` included, and the real checkout's layout (a maintainer's clone
        # has `upstream`) must not decide what a plain-install script says. The resolver has its
        # own tests (test_main_drift_notice.ReleaseRemote).
        with mock.patch.object(km.subprocess, "Popen", side_effect=lambda *a, **kw: calls.append((a, kw))), \
             mock.patch.object(km, "_release_remote", return_value="origin"), \
             mock.patch.dict(km.os.environ, {"ROMP_MANAGER_PORT": "7777"}):
            self.assertTrue(km._run_update("v0.7.0"))
        (a, kw), = calls
        self.assertEqual(a[0][:2], ["bash", "-c"])
        self.assertTrue(kw.get("start_new_session"), "install.sh + the restart take the kernel down — the child must outlive it")
        script = a[0][2]
        # EXACTLY the release commit, never the branch tip (the user 2026-08-09): the tag is fetched
        # by explicit refspec and fast-forwarded onto — an update to v0.7.0 means running v0.7.0
        self.assertIn("git fetch origin refs/tags/v0.7.0:refs/tags/v0.7.0", script)
        self.assertIn("git merge --ff-only v0.7.0", script)
        self.assertNotIn("git pull", script, "a pull takes whatever the branch has gained past the tag")
        self.assertIn("./install.sh", script)
        self.assertIn("update-report.json", script)
        # the restart rides the SUCCESS branch only: everything after `if` up to `else` has it,
        # the failure branch does not
        ok_branch, fail_branch = script.split("\nelse\n", 1)      # the OUTER else, column 0
        self.assertIn("/restart-all'", ok_branch,
                      "the self-update deploy bounces IMMEDIATELY (T160, the user's call from live "
                      "experience — the quiet window cost minutes per push; explicit "
                      "`romp refresh --quiet` still drains)")
        self.assertNotIn("when=quiet", ok_branch, "no drain default on the deploy path")
        self.assertIn("curl -fsS --max-time 60 -X POST 'http://127.0.0.1:", ok_branch,
                      "-f: a non-2xx answer from whatever holds the manager port is not a restart — the "
                      "report's `restarted` is read from curl's exit, never assumed; --max-time: a manager "
                      "that accepts and never answers ends the request instead of holding the latch with no "
                      "report (review find, 2026-09-08)")
        self.assertIn('"action": "self-update"', ok_branch,
                      "the script stamps restart-audit.jsonl at curl time, so the dying kernel's "
                      "restart-cuts row joins to a named reason instead of reading anonymous")
        self.assertNotIn("/restart-all", fail_branch)
        self.assertLess(ok_branch.index("/restart-all'"), ok_branch.index("update-report.json"),
                        "the report is written AFTER the restart request, with what the request did")
        self.assertEqual(km._UPDATE_STATE[0], "running")

    def test_no_manager_means_no_restart_leg(self):
        calls = []
        env = {k: v for k, v in km.os.environ.items() if k != "ROMP_MANAGER_PORT"}
        with mock.patch.object(km.subprocess, "Popen", side_effect=lambda *a, **kw: calls.append(a)), \
             mock.patch.dict(km.os.environ, env, clear=True):
            self.assertTrue(km._run_update("v0.7.0"))
        self.assertNotIn("/restart-all", calls[0][0][2])

    def test_refuses_junk_tags_and_reentry(self):
        with mock.patch.object(km.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            self.assertFalse(km._run_update("v1; rm -rf /"), "a tag is shell payload — semver or nothing")
        with mock.patch.object(km.subprocess, "Popen"):
            self.assertTrue(km._run_update("v0.7.0"))
            self.assertFalse(km._run_update("v0.7.0"), "one update at a time")


class HonestLaunch(Fresh):
    """A launch that did not happen is neither an update in flight nor an attempt."""

    def test_a_spawn_that_raises_gives_the_latch_back_and_says_so(self):
        # the raise used to escape _run_update with the in-flight latch still set: every later
        # update refused for the kernel's life, every banner waiting on a child that never existed
        with mock.patch.object(km.subprocess, "Popen", side_effect=OSError("resource temporarily unavailable")):
            self.assertFalse(km._run_update("v0.0.9"))
        self.assertEqual(km._UPDATE_STATE[0], "", "nothing is running")
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertFalse(ns[0]["ok"])
        self.assertIn("v0.0.9", ns[0]["text"])
        self.assertIn("resource temporarily unavailable", ns[0]["text"])
        with mock.patch.object(km.subprocess, "Popen"):
            self.assertTrue(km._run_update("v0.0.9"), "the next launch is not refused")

    def _auto_pass(self, cur="v0.0.8"):
        with mock.patch.object(km, "_kernel_ver", return_value=cur), \
             mock.patch.object(km, "_latest_release_tag", return_value="v0.0.9"), \
             mock.patch.object(km, "_send_to_app"):
            km._set_update_mode("auto")
            km._update_check()

    def test_a_refused_auto_launch_is_not_an_attempt(self):
        # another update holds the in-flight latch. The pass must not spend the version's one
        # automatic try on a launch that never happened — the marker used to be written FIRST, and
        # the next pass then reported "ran once without landing" about a run that never ran — nor
        # stand on the discovery as acted-on
        km._UPDATE_STATE[0] = "running"
        with mock.patch.object(km.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            self._auto_pass()
        self.assertFalse((jd.STATE / "update-attempted.json").exists(), "a refusal is not an attempt")
        self.assertEqual(km._UPDATE_AVAIL[0], "", "the discovery re-arms — the next pass tries again")
        km._UPDATE_STATE[0] = ""                                  # the in-flight update ended
        ran = []
        with mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) or True):
            self._auto_pass()
        self.assertEqual(ran, ["v0.0.9"], "the retry the re-armed slot stands for")
        self.assertEqual(json.loads((jd.STATE / "update-attempted.json").read_text())["tag"], "v0.0.9",
                         "the marker records a launch that happened")

    def test_a_refusal_for_an_update_already_in_flight_keeps_that_updates_tag(self):
        # the pass found v0.0.9 while the update to v0.0.8 was still running: the launch is refused
        # (one at a time), and the re-arm must give the slot BACK to the in-flight update's tag, not
        # blank it: /update-check's `tag` names what is pending, and nothing about the running
        # update changed (review find, 2026-09-08). Once it ends, the next pass tries v0.0.9.
        km._UPDATE_STATE[0] = "running"
        km._UPDATE_AVAIL[0] = "v0.0.8"
        with mock.patch.object(km.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
            self._auto_pass(cur="v0.0.7")
        self.assertEqual(km._UPDATE_AVAIL[0], "v0.0.8", "the in-flight update's tag stays pending")
        self.assertFalse((jd.STATE / "update-attempted.json").exists())
        km._UPDATE_STATE[0] = ""                                  # the in-flight update ended
        ran = []
        with mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) or True):
            self._auto_pass(cur="v0.0.7")
        self.assertEqual(ran, ["v0.0.9"], "the newer release is tried once the slot is free")

    def test_a_spawn_failure_in_auto_mode_leaves_no_marker_and_no_latch(self):
        with mock.patch.object(km.subprocess, "Popen", side_effect=OSError("no bash")):
            self._auto_pass()                                     # used to raise out of the pass
        self.assertFalse((jd.STATE / "update-attempted.json").exists())
        self.assertEqual((km._UPDATE_STATE[0], km._UPDATE_AVAIL[0]), ("", ""))
        self.assertTrue(any(not n["ok"] and "v0.0.9" in n["text"] for n in self.notices()))


class ReportConsumption(Fresh):
    def test_success_with_restart_files_once_and_archives(self):
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.7.0", "restarted": True}))
        rep = km._consume_update_report()
        self.assertTrue(rep["ok"])
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertTrue(ns[0]["ok"])
        self.assertIn("v0.7.0", ns[0]["text"])
        self.assertFalse((jd.STATE / "update-report.json").exists(), "consumed — never re-filed")
        self.assertTrue((jd.STATE / "update-report-last.json").exists())
        self.assertIsNone(km._consume_update_report(), "second boot: nothing left to file")

    def test_failure_is_a_loud_not_ok_notice(self):
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": False, "tag": "v0.7.0",
                                                                 "why": "the pull or install failed"}))
        km._consume_update_report()
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertFalse(ns[0]["ok"])
        self.assertIn("update.log", ns[0]["text"])

    def test_running_only_clears_the_inflight_latch(self):
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": False, "tag": "v0.7.0"}))
        km._consume_update_report(running_only=True)
        self.assertEqual(km._UPDATE_STATE[0], "")

    def test_a_boot_that_finds_a_non_object_report_does_not_crash(self):
        # main() calls _consume_update_report() bare: `.get` on a parsed null / [] / number raised
        # and took the boot down with it — after the rename, so the next boot came up with nothing
        p = jd.STATE / "update-report.json"
        p.write_text("[]")
        rep = km._consume_update_report()
        self.assertIsInstance(rep, dict)
        self.assertFalse(rep["ok"])
        self.assertFalse(p.exists())
        self.assertFalse((jd.STATE / "update-report-last.json").exists(), "not an outcome — not archived as one")
        aside = sorted(jd.STATE.glob("update-report.json.corrupt-*"))
        self.assertEqual(len(aside), 1, aside)
        self.assertEqual(aside[0].read_text(), "[]", "set aside as evidence, never deleted")
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertFalse(ns[0]["ok"])
        self.assertEqual(ns[0].get("kind"), "refused", "a state file moved aside rings the refused bell, like every quarantine")
        self.assertIn(aside[0].name, ns[0]["text"])
        self.assertIsNone(km._consume_update_report(), "the next boot finds nothing to file")

    def test_a_second_unreadable_report_in_the_same_second_keeps_the_first(self):
        # the sidecar wears the quarantine convention (`.corrupt-<stamp>`, `-n` for a same-second
        # repeat): a plain `.bad` was one fixed name, so a second junk report overwrote the first's bytes
        p = jd.STATE / "update-report.json"
        real = km.time.strftime
        fixed = lambda fmt, *a: "20260101T000000Z" if fmt == "%Y%m%dT%H%M%SZ" else real(fmt, *a)
        with mock.patch.object(km.time, "strftime", side_effect=fixed):
            for junk in ("[]", "null"):
                p.write_text(junk)
                self.assertFalse(km._consume_update_report()["ok"])
        kept = {x.name: x.read_text() for x in jd.STATE.glob("update-report.json.corrupt-*")}
        self.assertEqual(kept, {"update-report.json.corrupt-20260101T000000Z": "[]",
                                "update-report.json.corrupt-20260101T000000Z-1": "null"})

    def test_the_quarantine_moves_only_the_file_whose_bytes_it_read(self):
        # the ledger and state-reader quarantines stat BEFORE the read and decline when the file is
        # no longer the one whose bytes failed (review find, 2026-09-08): here the child's real
        # report lands between the read of the junk and the move, and the move must NOT carry the
        # real report off as junk. The fresh bytes get their own (bounded) read instead.
        p = jd.STATE / "update-report.json"
        p.write_text("[]")
        real_loads, fresh = json.loads, {"ok": True, "tag": "v0.0.9", "restarted": False,
                                         "why": "the manager on port 7777 did not take the restart request"}
        def loads_then_publish(raw, *a, **kw):
            d = real_loads(raw, *a, **kw)
            if not isinstance(d, dict):                           # the junk was read: a peer publishes now
                p.write_text(json.dumps(fresh))
            return d
        with mock.patch.object(km.json, "loads", side_effect=loads_then_publish):
            km._UPDATE_STATE[0] = "running"
            rep = km._consume_update_report(running_only=True)
        self.assertEqual(rep, fresh, "the replacement is what gets consumed")
        self.assertEqual(list(jd.STATE.glob("update-report.json.corrupt-*")), [], "nothing was moved aside")
        self.assertTrue((jd.STATE / "update-report-last.json").exists(), "the real report was archived as one")
        self.assertEqual(km._UPDATE_STATE[0], "")
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertIn("v0.0.9", ns[0]["text"])
        self.assertNotIn("could not be read", ns[0]["text"])

    def test_a_junk_report_that_cannot_be_moved_aside_is_said_once_and_stays(self):
        # a read-only state dir: the move fails. Silence here left the latch cleared with no word on
        # why (review find, 2026-09-08); the convention is one loud notice per fault episode, the
        # junk left in place as evidence, and the poll told the reason. A move that later succeeds
        # ends the episode, so the next unmovable file speaks again.
        p = jd.STATE / "update-report.json"
        p.write_text("[]")
        denied = PermissionError(13, "Permission denied")
        with mock.patch.object(km.os, "replace", side_effect=denied):
            km._UPDATE_STATE[0] = "running"
            rep = km._consume_update_report(running_only=True)
            self.assertEqual(km._UPDATE_STATE[0], "", "the child wrote SOMETHING: nothing is in flight")
            self.assertFalse(rep["ok"])
            self.assertIn("could not be moved aside", rep["why"])
            self.assertIn("Permission denied", rep["why"])
            self.assertTrue(p.exists(), "the evidence stays where it is")
            ns = self.notices()
            self.assertEqual(len(ns), 1)
            self.assertEqual((ns[0]["ok"], ns[0].get("kind")), (False, "refused"))
            self.assertIn("Permission denied", ns[0]["text"])
            self.assertFalse(km._consume_update_report()["ok"], "the boot finds it again")
            self.assertEqual(len(self.notices()), 1, "the same fault is said once per episode")
        self.assertFalse(km._consume_update_report()["ok"])
        self.assertEqual(len(list(jd.STATE.glob("update-report.json.corrupt-*"))), 1, "moved once it can be")
        self.assertEqual(len(self.notices()), 2, "the move is its own word")
        p.write_text("null")
        with mock.patch.object(km.os, "replace", side_effect=denied):
            km._consume_update_report()
        self.assertEqual(len(self.notices()), 3, "a fault after a proved move is a new episode")

    def test_the_restart_hint_names_the_step_that_works_for_the_case(self):
        # `romp refresh` asks the manager; with no manager it exits 1 (review find, 2026-09-08). So
        # the no-manager report's hint names `romp up`, the manager-refused report's names `romp
        # refresh`, and a report from before the `why` field (main's no-manager shape: ok, not
        # restarted, nothing else) reads as the no-manager case it was.
        cases = ((dict(why="no manager is running this kernel"), "romp up"),
                 (dict(why="the manager on port 7777 did not take the restart request"), "romp refresh"),
                 (dict(why="the manager on port 7777 did not answer the restart request within 60 s"), "romp refresh"),
                 (dict(), "romp up"))
        for extra, cmd in cases:
            with km._SYNC_LOCK:
                del km._SYNC_NOTICES[:]
            (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.0.9",
                                                                     "restarted": False, **extra}))
            km._consume_update_report(running_only=True)
            ns = self.notices()
            self.assertEqual(len(ns), 1, extra)
            self.assertIn("`%s`" % cmd, ns[0]["text"], extra)
            self.assertNotIn("romp on", ns[0]["text"], "a retired spelling: the manager runs as `romp up`")
            # the no-manager hint may still NAME `romp refresh` (to say it needs the manager) but never
            # as the step; the manager hint never sends the user to start a manager that is there
            other = "(`romp refresh`) to run it" if cmd == "romp up" else "`romp up`"
            self.assertNotIn(other, ns[0]["text"], extra)

    def test_a_boot_that_already_runs_the_landed_tag_does_not_ask_for_another_restart(self):
        # the report says "on disk, not restarted"; when nobody polled before the user restarted by
        # hand, the boot that RUNS the tag is the one consuming it — its notice says this start runs
        # it, instead of asking for the restart that just happened
        # "v0.0.9+" FIRST: every release tag sits on the release PR's merge commit, not on the
        # VERSION-bump commit, so a checkout exactly on the tag reads the + (per _kernel_ver's own
        # docstring); a bare string compare made this arm dead on every real release (review
        # find, 2026-09-08). The bare shape still counts (a tag placed on the bump commit itself).
        rep = {"ok": True, "tag": "v0.0.9", "restarted": False, "why": "no manager is running this kernel"}
        for ver in ("v0.0.9+", "v0.0.9"):
            with km._SYNC_LOCK:
                del km._SYNC_NOTICES[:]
            (jd.STATE / "update-report.json").write_text(json.dumps(rep))
            with mock.patch.object(km, "_kernel_ver", return_value=ver):
                km._consume_update_report()
            ns = self.notices()
            self.assertEqual(len(ns), 1, ver)
            self.assertTrue(ns[0]["ok"], ver)
            self.assertIn("this start is running it", ns[0]["text"], ver)
            self.assertIn("no manager is running this kernel", ns[0]["text"], ver)
            self.assertNotIn("restart romp yourself", ns[0]["text"], ver)
        # a kernel on any other version (or one whose version reader has nothing) still asks; and
        # the still-running kernel's poll (running_only) never claims to run it, whatever it reports
        for ver, running_only in (("v0.0.8", False), ("v0.0.8+", False), ("v0.0.10", False), (None, False),
                                  ("v0.0.9", True), ("v0.0.9+", True)):
            with km._SYNC_LOCK:
                del km._SYNC_NOTICES[:]
            (jd.STATE / "update-report.json").write_text(json.dumps(rep))
            with mock.patch.object(km, "_kernel_ver", return_value=ver):
                km._consume_update_report(running_only=running_only)
            ns = self.notices()
            self.assertEqual(len(ns), 1, (ver, running_only))
            self.assertIn("restart romp yourself", ns[0]["text"], (ver, running_only))
            self.assertNotIn("this start is running it", ns[0]["text"], (ver, running_only))


class Routes(Fresh):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, token=True):
        import urllib.request, urllib.error
        headers = {"X-Romp-Token": km.TOKEN} if token else {}
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=b"{}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_update_check_is_gated_and_reports_the_state(self):
        status, _ = _serve_get("/update-check")
        self.assertEqual(status, 403)
        km._UPDATE_AVAIL[0] = "v0.7.0"
        status, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual((status, d["tag"], d["mode"], d["state"]), (200, "v0.7.0", "ask", ""))
        self.assertEqual(d["boot"], km._BOOT_ID, "the banner detects the NEW kernel by this flipping")

    def test_update_check_poll_consumes_a_failed_report_mid_run(self):
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": False, "tag": "v0.7.0",
                                                                 "why": "the pull or install failed"}))
        _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual((d["failed"], d["state"]), ("the pull or install failed", ""))
        self.assertTrue(any(not n["ok"] for n in self.notices()), "the failure reached the Log too")

    def test_a_success_headed_for_restart_is_left_for_the_next_boot(self):
        # consuming it mid-run would file the notice into THIS dying kernel's in-memory ring — the
        # new kernel's boot must find the report and log the success durably
        km._UPDATE_STATE[0] = "running"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.7.0",
                                                                 "restarted": True}))
        _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual((d["failed"], d["updated"], d["state"]), ("", "", "running"))
        self.assertTrue((jd.STATE / "update-report.json").exists(), "not consumed — the next boot files it")
        self.assertEqual(self.notices(), [])

    def test_post_update_requires_something_known_and_the_token(self):
        code, _ = self._post("/update", token=False)
        self.assertEqual(code, 403)
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""     # module state: a prior drift pass must not leak in
        code, body = self._post("/update")
        self.assertEqual(code, 409, "nothing known → nothing to act on: " + body)
        km._UPDATE_AVAIL[0] = "v0.7.0"
        ran = []
        with mock.patch.object(km, "_run_update", side_effect=lambda tag: ran.append(tag) or True):
            code, body = self._post("/update")
        self.assertEqual((code, ran), (200, ["v0.7.0"]))

    def test_post_update_converges_main_drift_when_no_release_is_pending(self):
        # the drift click is a REAL restart, so the converge is stubbed: a live manager must never hear
        # a test (2026-08-14: this exact route, exercised unstubbed while real drift existed, restart-
        # stormed the machine running the suite — each run bounced every kernel on the box)
        km._UPDATE_AVAIL[0] = ""
        km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = "aaaa1111", ""
        ran = []
        with mock.patch.object(km, "_run_main_update",   # the route hands over its ack-time port too
                               side_effect=lambda kind, immediate=False, manager_port=None, target="":
                                   ran.append((kind, immediate, target))):
            code, body = self._post("/update")
            self.assertEqual(code, 200)
            self.assertIn("converging", body)
            for _ in range(200):                       # the route hands off to a daemon thread
                if ran:
                    break
                time.sleep(0.01)
        self.assertEqual(ran, [("pull", True, "aaaa1111")],
                         "the banner click is the user's own deliberate cut, onto the commit the banner named")
        km._MAIN_DRIFT[0] = ""

    def test_an_update_that_landed_but_did_not_restart_is_consumed_with_its_reason(self):
        # the manager did not take the restart request: the child reports ok + restarted:false +
        # why, and the still-running kernel's poll consumes THAT into the truthful next step. The
        # kernel used to sit on "running" forever (the child claimed restarted:true before asking),
        # and its only not-restarted wording blamed a missing manager
        km._UPDATE_STATE[0] = "running"
        why = "the manager on port 7777 did not take the restart request"
        (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.0.9",
                                                                 "restarted": False, "why": why}))
        _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        d = json.loads(body)
        self.assertEqual((d["updated"], d["why"], d["failed"], d["state"]), ("v0.0.9", why, "", ""))
        self.assertFalse((jd.STATE / "update-report.json").exists(), "consumed")
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertTrue(ns[0]["ok"])
        for s in ("v0.0.9", "on disk", why, "romp refresh"):
            self.assertIn(s, ns[0]["text"])
        self.assertNotIn("no manager is running", ns[0]["text"], "a manager IS running — it did not take the request")

    def test_update_check_carries_the_restart_hint_for_the_case(self):
        # the banner shows the kernel's hint verbatim, so the no-manager case names the command
        # that works there (`romp refresh` exits 1 with no manager; review find, 2026-09-08)
        for why, cmd in (("no manager is running this kernel", "romp up"),
                         ("the manager on port 7777 did not take the restart request", "romp refresh")):
            km._UPDATE_STATE[0] = "running"
            (jd.STATE / "update-report.json").write_text(json.dumps({"ok": True, "tag": "v0.0.9",
                                                                     "restarted": False, "why": why}))
            _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            d = json.loads(body)
            self.assertEqual((d["updated"], d["why"]), ("v0.0.9", why))
            self.assertIn(cmd, d["hint"], why)
            self.assertNotIn("romp on", d["hint"])
        km._UPDATE_STATE[0] = ""
        _, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(json.loads(body)["hint"], "", "no hint when nothing landed")

    def test_two_clicks_launch_one_update_and_the_second_hears_running(self):
        # two windows click Update: one child, and the second click is told `running` so its banner
        # joins the wait instead of racing a second launch. The route's guard for a launch refused
        # BECAUSE a concurrent click just took the latch (_run_update False AND the latch set) is the
        # `running` answer too, never the 500 a failed spawn earns (review find, 2026-09-08).
        km._UPDATE_AVAIL[0] = "v0.0.9"
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        spawned, pushed = [], []
        with mock.patch.object(km.subprocess, "Popen", side_effect=lambda *a, **kw: spawned.append(a)), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: pushed.append(m)):
            first = self._post("/update")
            second = self._post("/update")
        self.assertEqual((first[0], json.loads(first[1])), (200, {"ok": True, "state": "running"}))
        self.assertEqual((second[0], json.loads(second[1])), (200, {"ok": True, "state": "running"}))
        launches = [a for a in spawned if a[0][:2] == ["bash", "-c"]]    # the seam also sees git helpers
        self.assertEqual(len(launches), 1, ("one launch for two clicks", [a[0][:2] for a in spawned]))
        self.assertEqual([m.get("state") for m in pushed], ["running"], "one running push")
        self.assertEqual(self.notices(), [], "nothing failed, so nothing is filed")
        # the race itself: the launch is refused because the latch was taken between the route's
        # check and _run_update's own (a concurrent click won)
        km._UPDATE_STATE[0] = ""
        def won_the_race(tag):
            km._UPDATE_STATE[0] = "running"
            return False
        with mock.patch.object(km, "_run_update", side_effect=won_the_race), \
             mock.patch.object(km, "_send_to_app"):
            code, body = self._post("/update")
        self.assertEqual((code, json.loads(body)["state"]), (200, "running"))
        km._UPDATE_STATE[0] = ""

    def test_a_report_that_is_not_an_object_is_set_aside_and_the_poll_still_answers(self):
        # null / [] / a number parse but carry nothing: `.get` on them 500'd every poll for the
        # kernel's life (never consumed, so every poll hit the same file again)
        p = jd.STATE / "update-report.json"
        for junk in ("null", "[]", "3"):
            km._UPDATE_STATE[0] = "running"
            with km._SYNC_LOCK:
                del km._SYNC_NOTICES[:]
            p.write_text(junk)
            status, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            self.assertEqual(status, 200, junk)
            d = json.loads(body)
            self.assertEqual(d["state"], "", "nothing is in flight — the child wrote SOMETHING")
            self.assertIn("update-report.json.corrupt-", d["failed"])
            self.assertFalse(p.exists())
            aside = list(jd.STATE.glob("update-report.json.corrupt-*"))
            self.assertEqual(len(aside), 1, aside)
            self.assertEqual(aside[0].read_text(), junk, "evidence kept, never deleted")
            aside[0].unlink()
            ns = self.notices()
            self.assertEqual((len(ns), ns[0]["ok"]), (1, False))
            status, body = _serve_get("/update-check", headers={"X-Romp-Token": km.TOKEN})
            self.assertEqual((status, json.loads(body)["failed"]), (200, ""), "the next poll is quiet")
            self.assertEqual(len(self.notices()), 1, "said once")

    def test_the_update_click_hears_a_failed_launch_instead_of_waiting_on_it(self):
        # a spawn that fails after the click: the route used to answer 200 and push state:'running'
        # to every window with the latch set — every banner waited forever on a child that never
        # existed, and every later click was refused
        km._UPDATE_AVAIL[0] = "v0.0.9"
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        pushed = []
        with mock.patch.object(km.subprocess, "Popen", side_effect=OSError("no bash")), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: pushed.append(m)):
            code, body = self._post("/update")
        self.assertEqual(code, 500, body)
        self.assertIn("could not start the update to v0.0.9", body)
        self.assertEqual(pushed, [], "no 'running' push for a launch that did not happen")
        self.assertEqual(km._UPDATE_STATE[0], "", "not latched — the next click can try again")
        self.assertTrue(any(not n["ok"] and "v0.0.9" in n["text"] for n in self.notices()), "the Log says why")


class Wiring(unittest.TestCase):
    """Source pins: the check runs at boot, the banner ships on the landing page, the gear posts."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()
        cls.gear = (Path(BIN).parent / "ui" / "webview" / "gear.js").read_text()

    def test_boot_starts_the_check_loop_and_files_the_last_report(self):
        # the LOOP, not a one-shot: kernels outlive browser tabs by weeks (the user 2026-08-09),
        # so a boot-only check would almost never fire
        self.assertIn("threading.Thread(target=_update_check_loop, daemon=True).start()", self.src)
        self.assertIn("_consume_update_report()                                   # last self-update's outcome", self.src)

    def test_the_banner_dismissal_is_per_release(self):
        # Not-now silences THE dismissed tag; a strictly newer release found by a later pass is
        # new information and re-offers
        self.assertIn("if(waiting||!tag||tag===dismissedTag)return;", self.src)
        self.assertIn("dm.onclick=function(){dismissedTag=curTag;", self.src)

    def test_the_landing_ships_the_banner_and_the_shell_relay(self):
        self.assertIn("_stale_block(v) + _update_block() + _rdrift_block()", self.src)
        self.assertIn("window.__rompUpdateOffer=offer", self.src)
        self.assertIn("m.type==='updateAvail'&&window.__rompUpdateOffer", self.src)

    def test_offers_retire_on_the_truth_not_in_an_error_banner(self):
        # the user 2026-08-15: a stale offer survived the restart it asked for; its Update click hit a
        # converged kernel and painted "Could not start the update" over a working dashboard. The offer
        # now (a) carries + checks the pushing kernel's boot, (b) retires when the 30s /version poll
        # sees a new boot, (c) treats the 409 as "already done" — retire + Log, never a dead-end error,
        # and (d) can be re-derived on page load from /update-check's new drift fields.
        self.assertIn("if(boot&&bootNow&&boot!==bootNow)return;", self.src)
        self.assertIn("window.__rompUpdBoot=function(b)", self.src)
        self.assertIn("if(v&&v.boot&&window.__rompUpdBoot)window.__rompUpdBoot(v.boot);", self.src)
        self.assertIn("/no newer release or main commit/.test(em)", self.src)
        self.assertIn("__rompNotify('sync','the update this prompt offered already ran", self.src)
        self.assertIn("else if(d.drift&&d.driftSha)offer(d.cur||'',d.driftSha,d.drift);", self.src)
        # …and an update starting ANYWHERE flips every window to the in-flight wait
        self.assertIn("if(state==='running'){waiting=true;go.hidden=true;dm.hidden=true;", self.src)
        self.assertIn('{"type": "updateAvail", "state": "running", "boot": _BOOT_ID}', self.src)

    def test_the_gear_offers_the_three_modes_and_posts_the_pick(self):
        self.assertIn("id=rs-updates", self.gear)
        for opt in ("value=ask", "value=auto", "value=off"):
            self.assertIn(opt, self.gear)
        # the post is gesture-stamped (2026-08-29): setUpdateMode rides federation's queued
        # KERNEL_SETTING class, so the kernel orders applies by the click's own time — minted through
        # the gesture clock (ui/webview/gesture-clock.js), above every stamp the page has seen
        self.assertIn("post({ type: 'setUpdateMode', mode: upm.value, gt: gclock.stamp('update-mode') })", self.gear)
        # fill() renders through setShow now (2026-09-01): the same silent write, plus the
        # honest marked-option injection when a stored value is off this page's list
        self.assertIn("setShow(upm, v.updateMode)", self.gear)
        self.assertIn('msg.get("type") == "setUpdateMode"', self.src)

    def test_the_copy_says_an_automatic_update_restarts_at_once_or_converges_in_place(self):
        # The help line for Install automatically said the converge restarts "at the next quiet
        # moment". Since T269 every deploy restart is immediate (_run_main_update's immediate=True
        # default; the auto caller passes no override), and a pulled range that touches no kernel
        # code converges in place with the kernel left up (_kernel_code_changed + _in_place_converge).
        # The copy names both routes. The route line is pinned too, so a change to the route flags
        # the copy for re-reading.
        self.assertIn("if not _kernel_code_changed(_kernel_sha(reask=True), pulled) and _in_place_converge(pulled):",
                      self.src)
        self.assertNotIn("quiet moment", self.gear,
                         "the gear still promises a quiet-window restart; since T269 every deploy restart "
                         "is immediate and romp refresh --quiet is the only door to the quiet window")
        self.assertIn("Install automatically converges by itself: a change to kernel code restarts it at "
                      "once (turns in flight are cut and resume with their history); anything else (the "
                      "UI, the docs, the postal bus) converges in place with the kernel left up;",
                      self.gear)

    def test_the_banner_names_the_restart_the_user_must_run_when_the_update_landed_on_disk(self):
        # `updated` from /update-check means ON DISK, not running: the banner carries the reason
        # the restart did not happen and names the step that runs the new code
        self.assertIn("(d.why?', but '+d.why:'')", self.src)
        # the kernel words the step by case (`romp refresh` exits 1 with no manager; review find,
        # 2026-09-08): the banner shows its hint, with the manager case as the fallback text
        self.assertIn("(d.hint||'restart romp yourself (romp refresh) to run it')", self.src)


class ReleaseChannelMigration(unittest.TestCase):
    """The REQUIRED migration (the user 2026-08-31): installs the old drift banner walked onto a
    detached main sha must return to the release channel on the next tag. From a sha AHEAD of the
    tag, `git merge --ff-only <tag>` fails — the script now falls back to checking the tag out
    directly when HEAD is not itself on any tag, loudly in the update log. EXECUTED on a real
    throwaway repo pair (bare origin + detached install), not a source pin: the fallback's git
    behavior is the thing under test."""

    def _repos(self, tmp):
        """origin (bare) with c1 —tag v9.9.8→ c2 —tag v9.9.9→ c3 (main tip); the install cloned
        and DETACHED at c3 — ahead of v9.9.9, on no tag: the walked-onto-main shape."""
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        # Through the shared runner (T299): `git commit`, fetch and merge spawn `git maintenance run
        # --auto`, which on recent git detaches and can still be writing into .git while the
        # TemporaryDirectory removes the repo (the CI flake "Directory not empty: '.git'",
        # tests/test_restart_classifier.py, 2026-09-10); the runner forbids that work on every call,
        # and init_repo / forbid_background write the same keys into each repo's own config so the
        # update script's fetch + merge, run by the kernel's bash and not by this runner, obey them too.
        def g(cwd, *args):
            r = git(cwd, *args, env=env, check=False)
            self.assertEqual(r.returncode, 0, "git %s: %s%s" % (" ".join(args), r.stdout, r.stderr))
            return r.stdout.strip()
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        init_repo(src, "-q", "-b", "main", env=env)
        with open(os.path.join(src, "install.sh"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(src, "install.sh"), 0o755)
        g(src, "add", "install.sh")
        g(src, "commit", "-qm", "c1")
        g(src, "tag", "v9.9.8")
        g(src, "commit", "-qm", "c2", "--allow-empty")
        g(src, "tag", "v9.9.9")
        g(src, "commit", "-qm", "c3", "--allow-empty")
        bare = os.path.join(tmp, "origin.git")
        g(tmp, "clone", "-q", "--bare", src, bare)
        forbid_background(bare, env=env)                         # the script's fetch is served from here
        inst = os.path.join(tmp, "install")
        g(tmp, "clone", "-q", bare, inst)
        forbid_background(inst, env=env)                         # the script's fetch + merge run here
        g(inst, "checkout", "-q", "--detach", "origin/main")     # the old banner's walk
        return g, inst

    def test_the_fixture_repos_forbid_background_git_work(self):
        # The pin for the keys above: the update script runs fetch, merge and checkout against the
        # install through its own bash (the fetch served from the bare origin), never through g, so
        # the no-background config must sit in each repo's LOCAL config, not only on the runner's flags.
        with tempfile.TemporaryDirectory() as tmp:
            _, inst = self._repos(tmp)
            for repo in (inst, os.path.join(tmp, "origin.git"), os.path.join(tmp, "src")):
                self.assertEqual(git(repo, "config", "--local", "--get", "maintenance.auto").stdout.strip(),
                                 "false", repo)

    def _run(self, tag, inst):
        """Capture _run_update's script via the Popen seam, run it SYNCHRONOUSLY. The log and
        report are shared hermetic state — start each run clean or one test reads another's."""
        for f in ("update.log", "update-report.json"):
            try:
                (km.jd.STATE / f).unlink()
            except OSError:
                pass
        calls = []
        env = {**os.environ, "ROMP_MANAGER_PORT": ""}            # no manager → no restart leg
        # The install clones its bare `origin` directly, the plain layout; the resolver would say
        # so, but the Popen seam below swallows its `git remote` too, so it is pinned here.
        with mock.patch.object(km.subprocess, "Popen", side_effect=lambda *a, **kw: calls.append(a)), \
             mock.patch.object(km, "ROOT", Path(inst)), \
             mock.patch.object(km, "_release_remote", return_value="origin"), \
             mock.patch.dict(km.os.environ, env, clear=True):
            km._UPDATE_STATE[0] = ""
            self.assertTrue(km._run_update(tag))
        km._UPDATE_STATE[0] = ""
        script = calls[0][0][2]
        subprocess.run(["bash", "-c", script], cwd=inst, capture_output=True, text=True)
        return script

    def test_a_walked_onto_main_install_returns_to_the_release_channel_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, inst = self._repos(tmp)
            self._run("v9.9.9", inst)
            self.assertEqual(g(inst, "rev-parse", "HEAD"), g(inst, "rev-parse", "v9.9.9^{}"),
                             "HEAD landed exactly on the tag — back on the release channel")
            rep = json.loads((km.jd.STATE / "update-report.json").read_text())
            self.assertTrue(rep.get("ok"), rep)
            self.assertEqual((rep.get("restarted"), rep.get("why")), (False, "no manager is running this kernel"),
                             "the no-manager leg says what did not happen, and why")
            log = (km.jd.STATE / "update.log").read_text()
            self.assertIn("return to the release channel", log,
                          "the move is LOUD in the update log, never a silent history jump")

    def test_forward_only_no_path_ever_moves_an_install_backward(self):
        # the user's freeze ruling (2026-08-31): walked-along installs freeze WHERE THEY SIT —
        # never rolled back — and move only when a NEW release offers forward. The guarantee is
        # the offer gate itself: _run_update is reachable only through _update_check, which
        # refuses any tag whose version is not strictly newer than the running one — so the
        # migration's explicit checkout can only ever land on a release AHEAD of the install.
        src = inspect.getsource(km._update_check)
        self.assertIn("if not lv or lv <= cur:", src)
        i_gate = src.index("if not lv or lv <= cur:")
        i_run = src.index("_run_update(latest)")
        self.assertLess(i_gate, i_run, "the strictly-newer gate precedes the only auto _run_update call")

    def test_a_branch_checkout_never_takes_the_fallback(self):
        # the maintainer guard: a main-tracking BRANCH clone ahead of the tag keeps the harmless
        # no-op fast-forward it always had — the fallback yanking it onto a tag would strand the
        # very mesh the channel gate exists to keep noticed
        with tempfile.TemporaryDirectory() as tmp:
            g, inst = self._repos(tmp)
            g(inst, "checkout", "-q", "main")                     # a dev clone, ahead of v9.9.9
            head = g(inst, "rev-parse", "HEAD")
            self._run("v9.9.9", inst)
            self.assertEqual(g(inst, "rev-parse", "HEAD"), head,
                             "branch checkouts are never moved by the migration")
            log = (km.jd.STATE / "update.log").read_text()
            self.assertNotIn("return to the release channel", log)

    def test_the_normal_release_to_release_move_never_takes_the_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, inst = self._repos(tmp)
            g(inst, "checkout", "-q", "--detach", "v9.9.8")       # a healthy bootstrap install
            self._run("v9.9.9", inst)
            self.assertEqual(g(inst, "rev-parse", "HEAD"), g(inst, "rev-parse", "v9.9.9^{}"))
            log = (km.jd.STATE / "update.log").read_text()
            self.assertNotIn("return to the release channel", log,
                             "the fast-forward is the whole move — no fallback, no log line")

    def _run_with_manager(self, tag, inst, curl_exit):
        """Like _run, but WITH a manager port and a fake `curl` on PATH standing in for the manager
        door: it records whether the report already existed when the restart was requested, then
        exits as told (0: the manager took it; 7: curl's connection refused). Nothing is dialed."""
        for f in ("update.log", "update-report.json"):
            try:
                (km.jd.STATE / f).unlink()
            except OSError:
                pass
        calls = []
        with mock.patch.object(km.subprocess, "Popen", side_effect=lambda *a, **kw: calls.append(a)), \
             mock.patch.object(km, "ROOT", Path(inst)), \
             mock.patch.object(km, "_release_remote", return_value="origin"), \
             mock.patch.dict(km.os.environ, {"ROMP_MANAGER_PORT": "7777"}):
            km._UPDATE_STATE[0] = ""
            self.assertTrue(km._run_update(tag))
        km._UPDATE_STATE[0] = ""
        fake, seen, args = os.path.join(inst, "fake-bin"), os.path.join(inst, "curl-saw"), os.path.join(inst, "curl-args")
        os.makedirs(fake)
        with open(os.path.join(fake, "curl"), "w") as f:
            f.write("#!/bin/sh\nprintf '%s ' \"$@\" > \"$T_ARGS\"\n"
                    "if [ -e \"$T_REPORT\" ]; then echo present > \"$T_SEEN\"; "
                    "else echo absent > \"$T_SEEN\"; fi\nexit \"$T_CURL_EXIT\"\n")
        os.chmod(os.path.join(fake, "curl"), 0o755)
        env = {**os.environ, "PATH": fake + os.pathsep + os.environ.get("PATH", ""),
               "T_REPORT": str(km.jd.STATE / "update-report.json"), "T_SEEN": seen, "T_ARGS": args,
               "T_CURL_EXIT": str(curl_exit)}
        subprocess.run(["bash", "-c", calls[0][0][2]], cwd=inst, env=env, capture_output=True, text=True)
        rep = json.loads((km.jd.STATE / "update-report.json").read_text())
        with open(seen) as f, open(args) as a:
            return rep, f.read().strip(), a.read().strip()

    def test_the_report_says_what_the_restart_request_actually_did(self):
        # The manager door can be shut (gone, or a stale port). The report used to be written
        # BEFORE the request, claiming restarted:true — an "updated and restarted" report the
        # running kernel leaves for a next boot that never comes: latch wedged, banner "updating…"
        # forever, every later update refused. Executed end to end on a real repo pair.
        with tempfile.TemporaryDirectory() as tmp:
            g, inst = self._repos(tmp)
            g(inst, "checkout", "-q", "--detach", "v9.9.8")
            rep, saw, args = self._run_with_manager("v9.9.9", inst, curl_exit=7)
        self.assertEqual(saw, "absent", "the report is written AFTER the restart request, never before")
        self.assertIn("--max-time 60", args, "the request is bounded: a manager that never answers cannot "
                                             "hold the latch with no report (review find, 2026-09-08)")
        self.assertEqual((rep["ok"], rep["restarted"]), (True, False))
        self.assertIn("port 7777", rep["why"])
        self.assertIn("did not take the restart request", rep["why"])
        with tempfile.TemporaryDirectory() as tmp:
            g, inst = self._repos(tmp)
            g(inst, "checkout", "-q", "--detach", "v9.9.8")
            rep, saw, _ = self._run_with_manager("v9.9.9", inst, curl_exit=0)
        self.assertEqual(saw, "absent")
        self.assertEqual((rep["ok"], rep["restarted"], rep.get("why")), (True, True, None),
                         "a request the manager took is the one report the next boot files")

    def test_a_restart_request_the_manager_never_answers_is_not_a_restart(self):
        # curl's exit 28 is its own timeout: the manager accepted the connection and never answered.
        # Without --max-time that curl hung for good, the report was never written, and the latch
        # held with nothing to consume (review find, 2026-09-08). The timeout reads as not restarted,
        # with a why of its own, and the running kernel's poll consumes it like the refused case.
        with tempfile.TemporaryDirectory() as tmp:
            g, inst = self._repos(tmp)
            g(inst, "checkout", "-q", "--detach", "v9.9.8")
            rep, saw, args = self._run_with_manager("v9.9.9", inst, curl_exit=28)
        self.assertEqual(saw, "absent")
        self.assertIn("--max-time 60", args)
        self.assertEqual((rep["ok"], rep["restarted"]), (True, False))
        self.assertIn("port 7777", rep["why"])
        self.assertIn("did not answer the restart request within 60 s", rep["why"])
        self.assertNotIn("did not take", rep["why"], "a timeout is its own reason, not a refusal")


class BootOnTheTag(Fresh):
    """The boot that already runs the landed tag, judged by the REAL version reader on a repo shaped
    like every release: VERSION bumped in one commit, the tag on the commit after it (the release
    PR's merge), so a checkout exactly on the tag reads `vX.Y.Z+`. The mocked arms above pin the
    comparison; this pins the shape the comparison must survive (review find, 2026-09-08)."""

    def test_a_release_checkout_reads_the_plus_and_the_boot_still_says_it_runs_it(self):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        # the shared runner, for the reason ReleaseChannelMigration._repos gives (T299); the kernel's
        # own `git rev-parse` reads this repo through ROOT, so init_repo puts the keys in its config
        def g(cwd, *args):
            r = git(cwd, *args, env=env, check=False)
            self.assertEqual(r.returncode, 0, "git %s: %s%s" % (" ".join(args), r.stdout, r.stderr))
            return r.stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            init_repo(src, "-q", "-b", "main", env=env)
            with open(os.path.join(src, "VERSION"), "w") as f:
                f.write("9.9.9\n")
            g(src, "add", "VERSION")
            g(src, "commit", "-qm", "VERSION 9.9.9")                       # the bump
            g(src, "commit", "-qm", "merge the release PR", "--allow-empty")   # where the tag lands
            g(src, "tag", "v9.9.9")
            saved_ver, saved_head = km._VER, dict(km._HEAD_CACHE)
            try:
                with mock.patch.object(km, "ROOT", Path(src)):
                    km._VER = None
                    km._HEAD_CACHE.update(ts=0.0, full=None, short=None)
                    self.assertEqual(km._kernel_ver(), "v9.9.9+", "the shape every release checkout reads")
                    (jd.STATE / "update-report.json").write_text(json.dumps(
                        {"ok": True, "tag": "v9.9.9", "restarted": False, "why": "no manager is running this kernel"}))
                    rep = km._consume_update_report()
            finally:
                km._VER = saved_ver
                km._HEAD_CACHE.clear()
                km._HEAD_CACHE.update(saved_head)
        self.assertTrue(rep["ok"])
        ns = self.notices()
        self.assertEqual(len(ns), 1)
        self.assertIn("this start is running it", ns[0]["text"])
        self.assertNotIn("restart romp yourself", ns[0]["text"])


if __name__ == "__main__":
    unittest.main()
