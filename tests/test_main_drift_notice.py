#!/usr/bin/env python3
"""The mesh/origin-aware update notice (the user 2026-08-14): the release banner only watched TAGS, so
ordinary merges sat undeployed with every machine reading "in sync" — all equally stale. The kernel now
also compares origin/main vs the checkout vs the RUNNING build, fires the same banner (kind:"main"),
and converges on click or unattended per the update mode. Pure verdict unit-tested; the wiring pinned
by source (the rail-spend pattern). Synthetic only; hermetic state dir."""
import inspect
import os
import tempfile
import unittest
from unittest import mock
import time
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ["ROMP_MANAGER_PORT"] = "1"   # dead port: an unstubbed converge dials nothing real
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_drift", os.path.join(BIN, "romp-kernel"))


class ReleaseRemote(unittest.TestCase):
    """_release_remote picks the remote the updater reads. The remote convention (the user
    2026-09-06) makes a maintainer's `origin` their FORK, whose main nobody advances: an updater
    still reading a literal `origin` would report the fork's stale main as the truth and, in auto
    mode, walk the checkout backwards onto it. A plain bootstrap install has only `origin`, which
    IS the canonical repo. Throwaway repos; no network."""

    def _repo_with_remotes(self, *names):
        import subprocess
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        for n in names:
            subprocess.run(["git", "-C", d, "remote", "add", n, "https://example.invalid/%s.git" % n], check=True)
        return d

    def _resolve_in(self, root):
        from pathlib import Path
        saved = km.ROOT
        km.ROOT = Path(root)
        try:
            return km._release_remote()
        finally:
            km.ROOT = saved

    def test_a_fork_layout_reads_upstream(self):
        self.assertEqual(self._resolve_in(self._repo_with_remotes("origin", "upstream")), "upstream")

    def test_a_plain_install_reads_origin(self):
        self.assertEqual(self._resolve_in(self._repo_with_remotes("origin")), "origin")

    def test_no_git_checkout_falls_back_to_origin(self):
        self.assertEqual(self._resolve_in(tempfile.mkdtemp()), "origin")

    def test_every_updater_probe_and_walk_goes_through_it(self):
        for fn in (km._latest_release_tag, km._origin_main_sha, km._run_main_update, km._run_update):
            self.assertIn("_release_remote()", inspect.getsource(fn), fn.__name__)
            self.assertNotIn('"origin"', inspect.getsource(fn), "%s still names a literal origin" % fn.__name__)


class DriftVerdict(unittest.TestCase):
    def test_origin_ahead_of_the_checkout_wants_a_pull(self):
        self.assertEqual(km._main_drift_verdict("aaaa1111", "bbbb2222", "bbbb2222"), ("pull", "aaaa1111"))

    def test_checkout_ahead_of_the_running_kernel_wants_a_restart(self):
        # the hand-advanced case: updated code sits on disk, nothing booted it
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "cccc3333"), ("restart", "aaaa1111"))

    def test_pull_outranks_restart_when_both_hold(self):
        # origin ahead AND the running build stale: one pull converges both in a single bounce
        self.assertEqual(km._main_drift_verdict("aaaa1111", "bbbb2222", "cccc3333")[0], "pull")

    def test_in_sync_is_quiet(self):
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "aaaa1111"), ("", ""))

    def test_unknown_shas_never_invent_a_notice(self):
        # offline ls-remote, unreadable checkout, missing running sha: unknown, not a disagreement
        self.assertEqual(km._main_drift_verdict("", "bbbb2222", "bbbb2222"), ("", ""))
        self.assertEqual(km._main_drift_verdict("aaaa1111", "", "bbbb2222"), ("", ""))
        self.assertEqual(km._main_drift_verdict("", "", ""), ("", ""))

    def test_a_dirty_tree_at_the_running_commit_is_in_sync(self):
        # the running build's sha carries '-dirty' whenever the checkout had uncommitted work when the
        # kernel resolved it (_kernel_sha); the checkout's reader never appends it. On a shared
        # main-tracking tree that is the normal state — and it read as permanent drift: a 'ready on
        # disk' banner that never cleared in ask mode, and in auto mode a full restart, every
        # in-flight turn cut, once per cool-down for as long as the tree stayed dirty
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "aaaa1111-dirty"), ("", ""))

    def test_sha_width_never_reads_as_drift(self):
        # `rev-parse --short` auto-widens past eight characters on a big repo; `--short=8` and the
        # ls-remote slice never do — the same commit spelled at two widths is not two commits
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "aaaa11112"), ("", ""))
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111" + "0" * 32, "aaaa1111"), ("", ""))

    def test_a_seven_char_running_sha_at_the_checkouts_commit_is_in_sync(self):
        # the other direction: git's auto abbreviation FLOORS at seven on a small object store — a
        # `--depth 1` clone (an install shape the repo's own bug template names) spells the running
        # build at seven while `--short=8` spells the checkout at eight. A downward clamp to eight
        # cannot see that these agree; prefix agreement can
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "aaaa111"), ("", ""))
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "aaaa111-dirty"), ("", ""))
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa111", "aaaa111"), ("", ""),
                         "and origin vs a seven-character checkout the same way")

    def test_seven_against_eight_is_still_drift_when_the_commits_differ(self):
        # agreement is a prefix match, never a shrug: a different seven-character commit restarts,
        # and the target keeps the eight-character spelling the slots and the banner carry
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "cccc333"), ("restart", "aaaa1111"))
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "cccc333-dirty"), ("restart", "aaaa1111"))
        self.assertEqual(km._main_drift_verdict("bbbb222", "aaaa1111", "aaaa1111"), ("pull", "bbbb222"))

    def test_below_gits_floor_only_exact_equality_agrees(self):
        # nothing git prints is shorter than seven; a stray fragment must not agree with everything
        self.assertFalse(km._sha_same("aaaa11", "aaaa1111"), "six characters: a fragment, not a prefix match")
        self.assertTrue(km._sha_same("aaa", "aaa"), "equal strings still agree (the cool-down tests' fixtures)")
        self.assertFalse(km._sha_same("", "aaaa1111"), "unknown agrees with nothing")
        self.assertTrue(km._sha_same("aaaa111", "aaaa1111" + "0" * 32), "seven against forty")

    def test_a_dirty_tree_behind_the_checkout_still_wants_a_restart(self):
        # normalization strips the suffix, never the disagreement: real drift is still drift, and the
        # target it names is the eight-character spelling the banner and the in-place latch compare
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "cccc3333-dirty"), ("restart", "aaaa1111"))
        self.assertEqual(km._main_drift_verdict("bbbb2222", "aaaa1111", "aaaa1111-dirty"), ("pull", "bbbb2222"))


class DriftWiring(unittest.TestCase):
    def test_off_mode_silences_the_watcher_and_auto_converges_unattended(self):
        src = inspect.getsource(km._main_drift_check)
        self.assertIn('if _update_mode() == "off":', src)
        self.assertIn('if _update_mode() == "auto":', src)
        self.assertIn("_run_main_update(kind, target=target)", src,
                      "the auto converge hands down the commit it advertised — the move is bound to it")
        self.assertIn('"kind": "main"', src, "ask mode fires the shared banner with the drift variant")

    def test_a_dirty_shared_tree_refuses_loudly_and_rearms(self):
        src = inspect.getsource(km._run_main_update)
        self.assertIn('"status", "--porcelain"', src)
        self.assertIn("uncommitted work", src, "the refusal names the real problem")
        self.assertIn('_MAIN_DRIFT[0] = ""', src, "the notice re-fires once the tree is clean")
        self.assertIn('"checkout", "--detach", target', src,
                      "the move lands on the ADVERTISED commit, never the remote's ref (ConvergePullStep drives it)")
        self.assertNotIn('"%s/main" % remote', src, "a ref can move, or sit stale after a failed fetch")
        self.assertIn("remote = _release_remote()", src,
                      "the walk targets the RELEASE remote, never a literal origin (a fork layout's stale mirror)")

    def test_every_converge_is_immediate_by_default_and_quiet_stays_expressible(self):
        # T160 (the user 2026-08-28): deploys cut in-flight turns NOW — auto converge included.
        # The quiet spelling survives as an explicit opt-in, so the drain machinery stays testable
        # and reachable, it just stopped being the default.
        sig = inspect.signature(km._run_main_update)
        self.assertIs(sig.parameters["immediate"].default, True,
                      "auto converge is a deploy: immediate by default")
        src = inspect.getsource(km._run_main_update)
        self.assertIn('"" if immediate else "?when=quiet"', src,
                      "immediate=False still rides the quiet window explicitly")
        self.assertIn('_audit_restart_request("main-converge"', src,
                      "the converge names itself in restart-audit.jsonl so the cut row joins to it")
        route = inspect.getsource(km)
        self.assertIn('threading.Thread(target=_run_main_update, args=(kind, True),', route,
                      "the banner click stays explicitly immediate")

    def test_auto_converges_batch_behind_the_cool_down(self):
        # the user 2026-08-15, after flipping auto: main took a merge every few minutes and auto mode
        # converged once per commit — 4+ restarts/hour, each cutting every in-flight turn. Behind the
        # cool-down, N merges inside the window become ONE restart to the LATEST sha.
        ran = []
        saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
                 km._run_main_update, km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1])
        km._update_mode = lambda: "auto"
        km._checkout_sha = lambda: "aaa"
        km._kernel_sha = lambda: "aaa"
        km._run_main_update = lambda kind, immediate=False, target="": ran.append(kind)
        try:
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._LAST_AUTO_CONVERGE[0] = 0.0
            km._origin_main_sha = lambda: "bbb"
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "the first drift converges at once")
            km._origin_main_sha = lambda: "ccc"          # a new merge lands inside the window
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "inside the cool-down, no second restart")
            self.assertEqual(km._MAIN_DRIFT[0], "", "the deferred sha is NOT marked offered")
            km._LAST_AUTO_CONVERGE[0] = 0.0              # the window passes
            km._main_drift_check()
            self.assertEqual(ran, ["pull", "pull"], "past the window, one converge takes the LATEST sha")
        finally:
            (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
             km._run_main_update) = saved[:5]
            km._LAST_AUTO_CONVERGE[0] = saved[5]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[6], saved[7]

    def test_the_cool_down_holds_across_the_restart_it_spaces(self):
        # T240: the cool-down lived in module memory ("a restart resets it, which is fine — the
        # restart WAS the converge"). It was not fine: the converge's own restart boots a fresh
        # process with the cool-down at zero, so the next merge converged again at once — 05:19Z then
        # 05:24Z against a 1500 s window. The last DEPLOY restart is read from durable state (the
        # restart-cuts ledger's newest deploy row), so a fresh process still honours the window.
        import json, time
        ran = []
        saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
                 km._run_main_update, km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1])
        km._update_mode = lambda: "auto"
        km._checkout_sha = lambda: "aaa"
        km._kernel_sha = lambda: "aaa"
        km._origin_main_sha = lambda: "bbb"
        km._run_main_update = lambda kind, immediate=False, target="": ran.append(kind)
        try:
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._LAST_AUTO_CONVERGE[0] = 0.0                       # a FRESH process: module memory is empty
            now = time.time()
            with open(km.RESTART_CUTS_FILE, "a") as f:            # …but the ledger says a deploy restart
                f.write(json.dumps({"t": int(now - 300), "reason": "main-converge", "cutTurns": []}) + "\n")
            km._main_drift_check()
            self.assertEqual(ran, [], "5 min after a landed deploy restart, the cool-down still holds")
            self.assertEqual(km._MAIN_DRIFT[0], "", "the deferred sha stays unoffered — the first pass past the window takes the LATEST")
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 2000), "reason": "p2p-update: from X to Y",
                                                        "cutTurns": []}) + "\n")
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "past the window, the converge fires")
        finally:
            (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
             km._run_main_update) = saved[:5]
            km._LAST_AUTO_CONVERGE[0] = saved[5]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[6], saved[7]
            if km.RESTART_CUTS_FILE.exists():
                km.RESTART_CUTS_FILE.unlink()

    def test_the_audit_row_anchors_the_window_when_the_cut_row_was_lost(self):
        # review find, reproduced by simulation: a stale-manager converge can lose its cut row (the
        # parent-watch exited the kernel mid-drain). The deploy's AUDIT row is written before the
        # restart and survives — it anchors the window on its own.
        import json, time
        now = time.time()
        audit = km.jd.STATE / "restart-audit.jsonl"
        audit.write_text(json.dumps({"t": int(now - 300), "action": "main-converge", "when": "now", "tag": "pull"}) + "\n")
        started = km._STARTED
        km._STARTED = now - 3600      # this process booted long before every row below
        try:
            # the request LANDED: a boot row newer than it (the cut row itself was lost)
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 296), "bootSettled": True, "pid": 1}) + "\n")
            self.assertAlmostEqual(km._last_deploy_restart_t(), now - 300, delta=2, msg="no cut row needed")
            # a request nothing has booted since — a self-update click whose script failed, a p2p
            # apply that restarted nothing — anchors NOTHING (event, not time)
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 900), "bootSettled": True, "pid": 1}) + "\n")
            self.assertEqual(km._last_deploy_restart_t(), 0.0, "requested, never landed")
            # the RUNNING kernel is a boot too (review find): the new kernel's own boot row lands only
            # after its first serve and reconcile, and the drift loop's first pass runs before that —
            # a lost-row deploy must anchor on that very pass, not one boot row later
            km._STARTED = now - 10
            self.assertAlmostEqual(km._last_deploy_restart_t(), now - 300, delta=2,
                                   msg="a request older than this process has, by construction, been followed by a boot")
            km._STARTED = now - 3600
            # a cut row BETWEEN the request and the boot that the ledger did not join to it (review
            # find): the restart that booted was recorded and attributed elsewhere — an anonymous
            # manual restart ten minutes after a self-update click whose script failed — so the
            # click restarted nothing and anchors nothing, however many boots follow it
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 94), "reason": "", "cutTurns": []}) + "\n"
                                            + json.dumps({"t": int(now - 90), "bootSettled": True, "pid": 1}) + "\n")
            self.assertEqual(km._last_deploy_restart_t(), 0.0, "a later unrelated restart does not land an earlier request")
            # …but a cut row the ledger DID join to the request (auditT) is the request landing
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 294), "reason": "", "cutTurns": [],
                                                        "auditT": int(now - 300)}) + "\n"
                                            + json.dumps({"t": int(now - 290), "bootSettled": True, "pid": 1}) + "\n")
            self.assertAlmostEqual(km._last_deploy_restart_t(), now - 300, delta=2, msg="its own cut row is not a stranger's")
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 296), "bootSettled": True, "pid": 1}) + "\n")
            audit.write_text(json.dumps({"t": int(now - 200), "action": "main-converge", "tag": "abc"}) + "\n")
            self.assertEqual(km._last_deploy_restart_t(), 0.0, "a clicked-Update request row without when=now "
                             "is not a landed deploy on its own")
            audit.write_text(json.dumps({"t": int(now - 100), "action": "p2p-update", "reason": "from X to Y",
                                         "when": "quiet"}) + "\n")
            self.assertEqual(km._last_deploy_restart_t(), 0.0, "a peer's apply that has not restarted us yet anchors nothing")
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 90), "bootSettled": True, "pid": 1}) + "\n")
            self.assertAlmostEqual(km._last_deploy_restart_t(), now - 100, delta=2, msg="a peer's apply anchors once it landed")
        finally:
            km._STARTED = started
            audit.unlink()

    def test_a_parked_quiet_deploy_for_the_checkouts_sha_stands_the_converge_down(self):
        # T240d: a peer's p2p apply advanced the checkout and parked a QUIET restart at the manager
        # (the apply asked for the quiet window then; since T269 it asks for an immediate bounce and
        # writes no quiet row, so this shape now comes from older peers, a quiet converge and
        # `romp refresh --quiet` — the stand-down reads the row, not the writer);
        # the drift check then saw the checkout ahead of the kernel and posted an IMMEDIATE
        # restart-all — 16:23Z quiet park, 16:27Z converge/now, ten sessions cut — so the quiet
        # window the peer asked for never ran. The restart is already on its way: stand down, once,
        # keyed on the ledger row and its consumption (the row expires with the far manager's
        # backstop, the same bound a pending quiet request already has for cut attribution).
        import contextlib, io, json, time
        now = time.time()
        audit = km.jd.STATE / "restart-audit.jsonl"
        ran = []
        saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
                 km._run_main_update, km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1],
                 km._kernel_code_changed, km._QUIET_PARKED_LOGGED[0], km._CONVERGE_COOLDOWN_S)
        km._update_mode = lambda: "auto"
        km._kernel_code_changed = lambda a, b: True
        km._CONVERGE_COOLDOWN_S = 0.0      # isolate the stand-down: the cool-down is T240's own gate
        km._run_main_update = lambda kind, immediate=False, target="": ran.append(kind)
        km._checkout_sha = lambda: "f3dc387a" + "0" * 32
        km._origin_main_sha = km._checkout_sha
        km._kernel_sha = lambda: "aaaaaaaa" + "0" * 32

        def check():
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._LAST_AUTO_CONVERGE[0] = 0.0
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                km._main_drift_check()
            return err.getvalue()

        try:
            km._QUIET_PARKED_LOGGED[0] = ""
            if km.RESTART_CUTS_FILE.exists():
                km.RESTART_CUTS_FILE.unlink()
            # the 16:23Z shape: checkout ahead of the kernel, a quiet p2p row for the checkout's sha
            audit.write_text(json.dumps({"t": int(now - 240), "action": "p2p-update",
                                         "reason": "from TESTHOST to f3dc387a", "when": "quiet"}) + "\n")
            out = check()
            self.assertEqual(ran, [], "a quiet deploy for this very code is parked: the converge stands down")
            self.assertIn("f3dc387a is parked as a quiet deploy; leaving it to the quiet window", out, "one line says why nothing happened")
            self.assertEqual(km._MAIN_DRIFT[1], "", "the sha is not marked acted on — the next pass re-evaluates")
            self.assertIn("is parked as a quiet deploy; leaving it to the quiet window", check(),
                          "…and says it on EVERY pass (T352: a silent hold read as a dead thread), no longer once per sha")
            self.assertEqual(ran, [])
            # the 08:42Z shape: origin reads ahead of the checkout too (a stale fetch, or a merge
            # that landed meanwhile) — the parked restart still delivers the code on disk first;
            # one restart, not two, and the pull follows on the next pass after it lands
            km._origin_main_sha = lambda: "bbbbbbbb" + "0" * 32
            check()
            self.assertEqual(ran, [], "a pull does not pre-empt the parked quiet deploy either")
            km._origin_main_sha = km._checkout_sha
            # CONSUMED: the cut row that landed the quiet deploy joined the row — nothing is parked
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(now - 30), "reason": "p2p-update: from TESTHOST to f3dc387a",
                                                        "cutTurns": [], "auditT": int(now - 240)}) + "\n")
            check()
            self.assertEqual(ran, ["restart"], "a consumed row parks nothing: the converge proceeds as today")
            km.RESTART_CUTS_FILE.unlink()
            # a quiet row for a DIFFERENT sha: not this code — proceeds
            ran.clear()
            audit.write_text(json.dumps({"t": int(now - 240), "action": "p2p-update",
                                         "reason": "from TESTHOST to 99999999", "when": "quiet"}) + "\n")
            check()
            self.assertEqual(ran, ["restart"], "a quiet deploy of other code is not this converge")
            # an IMMEDIATE p2p row (a peer's apply since T269, or the no-owning-manager fallback):
            # nothing is parked — proceeds; the bounce it asked for lands within the manager's ack
            ran.clear()
            audit.write_text(json.dumps({"t": int(now - 240), "action": "p2p-update",
                                         "reason": "from TESTHOST to f3dc387a (immediate: no owning manager)"}) + "\n")
            check()
            self.assertEqual(ran, ["restart"], "an immediate request parks nothing")
            # a quiet row older than the far manager's backstop bound: the park is gone (its manager
            # died with it) — the converge proceeds rather than waiting forever
            ran.clear()
            audit.write_text(json.dumps({"t": int(now - km.RESTART_EXPECT_MAX_S - 30), "action": "p2p-update",
                                         "reason": "from TESTHOST to f3dc387a", "when": "quiet"}) + "\n")
            check()
            self.assertEqual(ran, ["restart"], "past the backstop bound nothing can still be parked")
            # a QUIET converge row from the explicit immediate=False door carries the sha it deploys:
            # parked too (no production caller passes immediate=False today; the CLI door is below)
            ran.clear()
            km._QUIET_PARKED_LOGGED[0] = ""
            audit.write_text(json.dumps({"t": int(now - 60), "action": "main-converge", "tag": "restart",
                                         "when": "quiet", "sha": "f3dc387a" + "0" * 32}) + "\n")
            check()
            self.assertEqual(ran, [], "a quiet local converge for this sha is parked the same way")
            # …a legacy quiet converge row without a sha cannot be matched: proceeds (never guesses)
            audit.write_text(json.dumps({"t": int(now - 60), "action": "main-converge", "tag": "restart",
                                         "when": "quiet"}) + "\n")
            check()
            self.assertEqual(ran, ["restart"], "no sha on the row, no stand-down")
            # the CLI door (review find): `romp refresh --quiet` writes bin/romp's caller-attribution
            # row — no action — now with when=quiet and the checkout sha; it parks the same way
            ran.clear()
            km._QUIET_PARKED_LOGGED[0] = ""
            audit.write_text(json.dumps({"t": int(now - 60), "ppid": 4242, "parent": "bash", "sid": "", "name": "",
                                         "tty": "/dev/pts/0", "when": "quiet", "sha": "f3dc387a"}) + "\n")
            check()
            self.assertEqual(ran, [], "a quiet CLI refresh for this sha is a parked deploy too")
            # a busy box (review find): fifty session self-closes write fifty end-on-idle rows after
            # the quiet row — the reader walks past them; the park is still live
            ran.clear()
            audit.write_text(json.dumps({"t": int(now - 240), "action": "p2p-update",
                                         "reason": "from TESTHOST to f3dc387a", "when": "quiet"}) + "\n"
                             + "".join(json.dumps({"t": int(now - 200 + i), "action": "end-on-idle", "sid": "s%d" % i}) + "\n"
                                       for i in range(50)))
            check()
            self.assertEqual(ran, [], "fifty no-restart rows do not hide a live park")
            # LANDED, cut row lost (review find): the kernel already runs the checkout, the row is still
            # unconsumed — nothing is owed, so a pull must not wait on it (and says nothing about a park).
            # "Runs the checkout" is read the way the verdict reads it (_sha_same): the running sha
            # carries '-dirty' on a shared tree with uncommitted work, and is seven characters on a
            # shallow clone — a raw string compare called both "restart still owed" and stood the
            # pull down behind a park that had already delivered
            ran.clear()
            km._QUIET_PARKED_LOGGED[0] = ""
            audit.write_text(json.dumps({"t": int(now - 240), "action": "p2p-update",
                                         "reason": "from TESTHOST to f3dc387a", "when": "quiet"}) + "\n")
            km._kernel_sha = lambda: "f3dc387a" + "0" * 32 + "-dirty"
            km._origin_main_sha = lambda: "bbbbbbbb" + "0" * 32
            out = check()
            self.assertEqual(ran, ["pull"], "the parked restart already delivered: the pull proceeds")
            self.assertNotIn("already parked", out)
            km._kernel_sha = lambda: "f3dc387"                     # the shallow clone's seven-character spelling
            out = check()
            self.assertEqual(ran, ["pull", "pull"], "spelled at seven, the kernel still runs the checkout")
            self.assertNotIn("already parked", out)
            km._kernel_sha = lambda: "aaaaaaaa" + "0" * 32
            km._origin_main_sha = km._checkout_sha
            # a stand-down that ENDS without landing (the row expired: the park died with its manager)
            # says so once, then the converge proceeds on its own terms (review find: silent expiry)
            ran.clear()
            km._QUIET_PARKED_LOGGED[0] = ""
            check()
            self.assertEqual(ran, [], "parked again")
            audit.write_text(json.dumps({"t": int(now - km.RESTART_EXPECT_MAX_S - 30), "action": "p2p-update",
                                         "reason": "from TESTHOST to f3dc387a", "when": "quiet"}) + "\n")
            out = check()
            self.assertEqual(ran, ["restart"])
            self.assertIn("no longer pending", out, "the end of a stand-down is said, not silent")
            self.assertNotIn("no longer pending", check(), "…once")
        finally:
            (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
             km._run_main_update) = saved[:5]
            km._LAST_AUTO_CONVERGE[0] = saved[5]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[6], saved[7]
            km._kernel_code_changed, km._QUIET_PARKED_LOGGED[0] = saved[8], saved[9]
            km._CONVERGE_COOLDOWN_S = saved[10]
            audit.unlink()
            if km.RESTART_CUTS_FILE.exists():
                km.RESTART_CUTS_FILE.unlink()

    def test_the_quiet_deploy_rows_carry_what_the_stand_down_reads(self):
        # T240d pins on the writers: the p2p apply's audit row (quiet then, immediate since T269) lands
        # right after the reset that advances the checkout — BEFORE the owner check, whose manager
        # status call is the window a drift pass could hit between "checkout ahead" and "row on disk",
        # so a cut in that window still finds its request on disk — and the local converge's
        # row carries the sha it deploys, so a quiet `romp refresh` is matched, not guessed at
        ksrc = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        reset = ksrc.index('reset --hard "$WANT" >/dev/null 2>&1 || {')
        row = ksrc.index("\\'action\\':\\'p2p-update\\',")     # the shell script's escaped spelling
        owned = ksrc.index("'OWNED=0; if command -v node")
        self.assertLess(reset, row, "the row says the checkout IS at the sha: it follows the reset")
        self.assertLess(row, owned, "and precedes the owner check, so the drift check sees it at once")
        self.assertIn('_audit_restart_request("main-converge", tag=kind, when=("now" if immediate else "quiet"),\n'
                      '                               sha=', ksrc,
                      "the converge row names the sha it deploys")
        # the CLI door: bin/romp's own caller-attribution row says when=quiet and names the checkout
        # sha under --quiet (behavior pinned in tests/romp-refresh-audit.bats; the spelling here)
        rsrc = open(os.path.join(os.path.dirname(HERE), "bin", "romp")).read()
        self.assertIn('RA_WHEN="${2:-}"', rsrc)
        self.assertIn('if os.environ.get("RA_WHEN") == "--quiet":\n    # a PARKED restart', rsrc)
        self.assertIn('row["when"] = "quiet"', rsrc)

    def test_the_parent_watch_stands_down_under_a_graceful_term(self):
        # behavioral (review find: the source-order pin executed nothing): the manager is gone —
        # with the graceful term running the watchdog returns; without it, it exits the kernel
        from unittest import mock
        exits = []
        saved = (km._pid_alive, os.environ.get("ROMP_MANAGER_PID"), km._TERMINATING[0])
        km._pid_alive = lambda pid: False
        os.environ["ROMP_MANAGER_PID"] = "424242"
        try:
            km._TERMINATING[0] = True
            with mock.patch.object(km.os, "_exit", lambda code: exits.append(code)):
                km._parent_watch()
            self.assertEqual(exits, [], "the graceful term owns the exit")
            km._TERMINATING[0] = False
            with mock.patch.object(km.os, "_exit", lambda code: exits.append(code)):
                km._parent_watch()
            self.assertEqual(exits, [0], "a dead manager with no drain running still exits the kernel")
        finally:
            km._pid_alive = saved[0]
            if saved[1] is None:
                os.environ.pop("ROMP_MANAGER_PID", None)
            else:
                os.environ["ROMP_MANAGER_PID"] = saved[1]
            km._TERMINATING[0] = saved[2]
        import inspect
        gt = inspect.getsource(km._graceful_term)
        self.assertLess(gt.index("_TERMINATING[0] = True"), gt.index("_broadcast_restarting()"),
                        "the flag is the FIRST thing the graceful term does")

    def test_only_deploy_restarts_count_toward_the_cool_down(self):
        # a rail-button restart, a self-close, an in-place skip: none of them is a converge, so none
        # of them spaces the next one
        import json, time
        now = time.time()
        km.RESTART_CUTS_FILE.write_text("".join(json.dumps(r) + "\n" for r in [
            {"t": int(now - 3000), "reason": "main-converge", "cutTurns": []},
            {"t": int(now - 100), "reason": "kernel-asks-manager-restart-all: rail button", "cutTurns": []},
            {"t": int(now - 50), "reason": "main-converge-skip", "cutTurns": []},
            {"t": int(now + 7200), "reason": "p2p-update: from X to Y", "cutTurns": []},   # a clock stepped back
        ]))
        try:
            self.assertAlmostEqual(km._last_deploy_restart_t(), now - 3000, delta=2)
            km.RESTART_CUTS_FILE.write_text(json.dumps(
                {"t": int(now - 200), "reason": "self-update: v0.15 -> v0.16", "cutTurns": []}) + "\n")
            self.assertAlmostEqual(km._last_deploy_restart_t(), now - 200, delta=2,
                                   msg="a release self-update is a deploy restart too")
        finally:
            km.RESTART_CUTS_FILE.unlink()

    def test_the_shell_banner_carries_the_drift_variants(self):
        src = inspect.getsource(km)
        self.assertIn("m.drift||''", src, "the shell relay forwards the drift kind")
        self.assertIn("new romp commits are on main", src)
        self.assertIn("is ready on disk", src)

    def test_the_route_acts_only_on_what_the_kernel_itself_found(self):
        src = inspect.getsource(km)
        self.assertIn('d0, d1 = _MAIN_DRIFT[0], _MAIN_DRIFT[1]', src,
                      "one snapshot: a re-read could pair a pull with an emptied target — an unbound move")
        self.assertIn('kind = "pull" if d0 else ("restart" if d1 else "")', src,
                      "no version or kind is ever taken from the client")
        self.assertIn('"target": d0 or d1', src, "the click converges onto the commit the banner named")



class ConvergeWaitsSpareOnlyCuts(unittest.TestCase):
    """T352 (the manager's find, 2026-09-11): a merged fix waited 23 minutes behind the auto-converge cool-down on a box
    where every session was hosted, and the hold wrote nothing to the journal. The two waits (the cool-down, the parked
    quiet deploy) exist to spare in-flight turns, so a restart that would cut none skips them; every pass that holds,
    stands down, or cannot read main says so on the kernel's log, each time."""

    def setUp(self):
        import io
        self.saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha, km._run_main_update,
                      km._deploy_would_cut, km._parked_quiet_deploy, km._kernel_code_changed,
                      km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1], km._QUIET_PARKED_LOGGED[0],
                      km._CONVERGE_CRASH_T[0])
        self.ran = []
        km._update_mode = lambda: "auto"
        km._checkout_sha = lambda: "aaa"
        km._kernel_sha = lambda: "aaa"
        km._origin_main_sha = lambda: "bbb"
        km._run_main_update = lambda kind, immediate=False, target="": (self.ran.append(kind), True)[1]   # ran and succeeded
        km._parked_quiet_deploy = lambda checkout, now=None: 0
        km._kernel_code_changed = lambda running, target: True
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._QUIET_PARKED_LOGGED[0] = ""
        km._LAST_AUTO_CONVERGE[0] = 0.0
        km._CONVERGE_CRASH_T[0] = 0.0
        self.err = io.StringIO()

    def tearDown(self):
        (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha, km._run_main_update,
         km._deploy_would_cut, km._parked_quiet_deploy, km._kernel_code_changed) = self.saved[:8]
        (km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1], km._QUIET_PARKED_LOGGED[0],
         km._CONVERGE_CRASH_T[0]) = self.saved[8:]     # the crash stamp restored too (the follow-up review)
        if km.RESTART_CUTS_FILE.exists():
            km.RESTART_CUTS_FILE.unlink()

    def _pass(self):
        import contextlib
        with contextlib.redirect_stderr(self.err):
            km._main_drift_check()

    def _lines(self):
        return [l for l in self.err.getvalue().splitlines() if l.startswith("romp-kernel: converge: ")]

    def _deploy_landed(self, ago):
        import json, time
        km.RESTART_CUTS_FILE.write_text(json.dumps({"t": int(time.time() - ago), "reason": "p2p-update: from X to Y",
                                                    "cutTurns": []}) + "\n")

    def test_a_held_converge_says_so_every_pass_and_names_the_turns_it_spares(self):
        self._deploy_landed(300)
        km._deploy_would_cut = lambda: [{"sid": "1" * 36, "name": "web"}, {"sid": "2" * 36, "name": "api"}]
        self._pass(); self._pass()
        self.assertEqual(self.ran, [], "inside the cool-down with turns to spare: no restart")
        lines = self._lines()
        self.assertEqual(len(lines), 2, "one line per held pass, never once per sha: %r" % lines)
        for l in lines:
            self.assertIn("main is at bbb (the checkout aaa, this box runs aaa): holding ", l)
            self.assertRegex(l, r"holding \d+ s more of the 25 min cool-down since the deploy restart at \d\d:\d\d:\d\dZ")
            self.assertIn("; a restart now would cut 2 turns: web, api", l)
        self.assertEqual(km._MAIN_DRIFT[0], "", "the deferred sha stays unoffered: the first pass past the window takes the latest")

    def test_a_converge_that_would_cut_nothing_skips_the_cool_down_and_says_why(self):
        self._deploy_landed(300)
        km._deploy_would_cut = lambda: []                 # every working session hosted: a restart cuts nothing
        self._pass()
        self.assertEqual(self.ran, ["pull"], "nothing to spare: the converge proceeds inside the cool-down")
        lines = self._lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^romp-kernel: converge: main is at bbb \(this box runs aaa\): \d+ s of the 25 min cool-down since the deploy restart at \d\d:\d\d:\d\dZ remain, but a restart now would cut no turn: converging now$")

    def test_an_unknown_cut_set_keeps_the_hold(self):
        self._deploy_landed(300)
        km._deploy_would_cut = lambda: None               # no backend yet: unknown is not "none"
        self._pass()
        self.assertEqual(self.ran, [], "unknown keeps both waits, as before")
        self.assertIn("; what a restart would cut is unknown (no backend yet)", self._lines()[0])

    def test_an_unreadable_main_says_so_every_pass_and_the_restart_leg_still_converges(self):
        km._origin_main_sha = lambda: ""                  # git ls-remote failed or timed out
        km._deploy_would_cut = lambda: []
        self._pass(); self._pass()
        self.assertEqual(self.ran, [], "the checkout and the kernel agree: nothing to do, said each pass")
        lines = self._lines()
        self.assertEqual(len(lines), 2, lines)
        for l in lines:
            self.assertIn("main at ", l)
            self.assertIn("(git ls-remote failed or timed out) could not be read this pass (main ?, checkout aaa, running aaa): no verdict; again in 300 s", l)
        # the per-leg shape (the review's M1): the checkout ahead of the running kernel converges the restart leg with
        # main unreadable, as it always did; only a pull needs main and the checkout both
        km._checkout_sha = lambda: "bbb"
        self._pass()
        self.assertEqual(self.ran, ["restart"], "offline, the restart leg still converges")
        self.assertIn("the restart leg still decides from what was read; again in 300 s", self._lines()[-1])

    def test_an_empty_kernel_sha_is_asked_again_never_remembered(self):
        import subprocess as _sp
        saved = km._SHA
        real = self.saved[3]                              # the REAL reader: setUp stubbed km._kernel_sha for the gate tests
        calls = []
        def run(argv, **kw):
            calls.append(argv[-1])
            if len(calls) == 1:
                raise _sp.TimeoutExpired(argv, 2)     # a busy boot: git did not answer in time
            return mock.Mock(returncode=0, stdout="" if argv[-1] == "--porcelain" else "abc1234\n")   # a clean tree
        saved_miss = km._SHA_MISS_T[0]
        try:
            km._SHA = None
            km._SHA_MISS_T[0] = 0.0
            with mock.patch.object(km.subprocess, "run", side_effect=run):
                self.assertIsNone(real(), "the blip answers nothing…")
                self.assertIsNone(real(), "…and within the re-ask bound the miss stands without a git call (the round-two low: "
                                          "/version and /sw.js call this per request)")
                self.assertEqual(calls, ["HEAD"], "one git call so far")
                km._SHA_MISS_T[0] = time.time() - km._SHA_REASK_S - 1     # the bound passed
                self.assertEqual(real(), "abc1234", "…and is not remembered for the process's life: asked again after the bound")
                self.assertEqual(real(), "abc1234", "a real answer is memoized")
            self.assertEqual(calls, ["HEAD", "HEAD", "--porcelain"])
        finally:
            km._SHA = saved
            km._SHA_MISS_T[0] = saved_miss

    def test_a_missing_running_sha_inside_the_cool_down_holds_says_so_and_the_next_pass_converges(self):
        # the round-two review's MEDIUM: with the pass no longer returning early on an unreadable input, a None running
        # sha (a 2 s rev-parse timeout on a busy box) inside the cool-down raised at running[:8] AFTER the latch took
        # the target, and the hold's own reset never ran: every later pass returned at the latch, the commit never
        # converged, in silence
        self._deploy_landed(300)
        km._kernel_sha = lambda: None
        km._deploy_would_cut = lambda: [{"sid": "1" * 36, "name": "web"}]
        self._pass()
        self.assertEqual(self.ran, [], "inside the cool-down with a turn to spare: held")
        self.assertIn("main is at bbb (the checkout aaa, this box runs ?): holding ", self._lines()[-1])
        self.assertEqual(km._MAIN_DRIFT[0], "", "the hold's reset ran: no target latched")
        km._kernel_sha = lambda: "aaa"
        km.RESTART_CUTS_FILE.unlink()                    # the cool-down is over
        self._pass()
        self.assertEqual(self.ran, ["pull"], "the next pass converges")
        # the spares branch with a None running sha says so too
        self.ran.clear(); km._MAIN_DRIFT[0] = ""
        self._deploy_landed(300)
        km._kernel_sha = lambda: None
        km._deploy_would_cut = lambda: []
        self._pass()
        self.assertEqual(self.ran, ["pull"], "a restart now would cut no turn: converging")
        self.assertIn("main is at bbb (this box runs ?): ", self._lines()[-1])

    def test_a_crash_in_the_hold_branches_never_leaves_a_target_latched(self):
        def boom():
            raise RuntimeError("boom")
        km._deploy_would_cut = boom
        with self.assertRaises(RuntimeError):
            self._pass()
        self.assertEqual(km._MAIN_DRIFT[0], "", "the latch is reset on the way out, so the next pass judges afresh")
        km._deploy_would_cut = lambda: []
        km._CONVERGE_CRASH_T[0] = 0.0                    # the crash hold is its own test below
        self._pass()
        self.assertEqual(self.ran, ["pull"])

    def test_a_crash_in_the_converge_leg_holds_one_cool_down_before_the_retry(self):
        # the round-three review's low a: the spares branch waived the cool-down the crash had just stamped, so a
        # crashing leg retried every pass on a hosted box
        def boom():
            raise RuntimeError("boom")
        km._deploy_would_cut = boom
        with self.assertRaises(RuntimeError):
            self._pass()
        self.assertGreater(km._CONVERGE_CRASH_T[0], 0.0, "the crash is stamped")
        km._deploy_would_cut = lambda: []                # a restart would cut nothing: the spares branch would converge
        self._pass(); self._pass()
        self.assertEqual(self.ran, [], "held: one cool-down after a crash, whatever a restart would cut")
        held = [l for l in self._lines() if "the converge leg crashed" in l]
        self.assertEqual(len(held), 2, "said on every held pass: %r" % self._lines())
        self.assertIn("holding ", held[0]); self.assertIn(" s more of one cool-down before the retry", held[0])
        self.assertEqual(km._MAIN_DRIFT[0], "", "no target latched while held")
        km._CONVERGE_CRASH_T[0] = time.time() - km._CONVERGE_COOLDOWN_S - 1
        self._pass()
        self.assertEqual(self.ran, ["pull"], "the cool-down over, the retry converges")
        self.assertEqual(km._CONVERGE_CRASH_T[0], 0.0, "a converge that ran clears the crash hold (the follow-up review)")

    def test_a_refused_converge_leaves_the_crash_stamp_standing(self):
        # the second round of the follow-up review: the clear keyed on _run_main_update having RETURNED, and every
        # refusal returns too. The stamp pre-loaded is an EXPIRED one (a live one holds the pass at the cool-down gate
        # above, before the leg runs), so the road reaches the leg and the no-op is what is pinned
        stamp = time.time() - km._CONVERGE_COOLDOWN_S - 1
        km._CONVERGE_CRASH_T[0] = stamp
        km._run_main_update = lambda kind, immediate=False, target="": (self.ran.append(kind), False)[1]   # a refusal
        self._pass()
        self.assertEqual(self.ran, ["pull"], "the leg ran")
        self.assertEqual(km._CONVERGE_CRASH_T[0], stamp, "a refusal is no converge that succeeded: the stamp stands")
        self.ran.clear(); km._MAIN_DRIFT[0] = ""; km._LAST_AUTO_CONVERGE[0] = 0.0   # the cool-down the first pass stamped
        km._run_main_update = lambda kind, immediate=False, target="": (self.ran.append(kind), True)[1]
        self._pass()
        self.assertEqual((self.ran, km._CONVERGE_CRASH_T[0]), (["pull"], 0.0), "a converge that succeeded clears it")

    def test_an_in_place_converge_clears_the_crash_hold_too(self):
        # the follow-up review: a success that bypassed the gate (an in-place converge from the restart leg or /update)
        # left the stamp, and a later converge waited out a cool-down it had no reason to
        import inspect
        src = inspect.getsource(self.saved[4])           # the REAL _run_main_update (setUp stubs km's)
        i = src.index("_in_place_converge(pulled):")
        self.assertIn("_CONVERGE_CRASH_T[0] = 0.0", src[i:i + 400], "cleared on the in-place road before its return")
        self.assertLess(src.index("_CONVERGE_CRASH_T[0] = 0.0", i), src.index("return", i + 30))

    def test_the_converge_leg_asks_git_again_for_the_running_sha_within_the_miss_bound(self):
        # the round-three review's low b: the 30 s miss memo made the leg's own read (in _run_main_update) return None
        # for a blip at the top of the same pass, so a main commit touching no kernel code took a full restart
        import subprocess as _sp
        real = self.saved[3]
        saved, saved_miss = km._SHA, km._SHA_MISS_T[0]
        calls = []
        def run(argv, **kw):
            calls.append(argv[-1])
            return mock.Mock(returncode=0, stdout="" if argv[-1] == "--porcelain" else "abc1234\n")
        try:
            km._SHA = None
            km._SHA_MISS_T[0] = time.time()              # git blipped a moment ago
            with mock.patch.object(km.subprocess, "run", side_effect=run):
                self.assertIsNone(real(), "within the bound the miss stands…")
                self.assertEqual(calls, [])
                self.assertEqual(real(reask=True), "abc1234", "…but the converge leg's read asks once more")
            self.assertEqual(calls, ["HEAD", "--porcelain"])
            self.assertIn("_kernel_code_changed(_kernel_sha(reask=True), pulled)", inspect.getsource(self.saved[4]),
                          "the REAL _run_main_update (setUp stubs km's) asks once more")
        finally:
            km._SHA, km._SHA_MISS_T[0] = saved, saved_miss

    def test_a_parked_quiet_deploy_stands_down_every_pass_unless_nothing_would_be_cut(self):
        km._checkout_sha = lambda: "bbb"                  # the checkout is ahead of the kernel: a restart is owed…
        km._origin_main_sha = lambda: "bbb"
        km._parked_quiet_deploy = lambda checkout, now=None: 1   # …and a quiet deploy is parked for it
        km._deploy_would_cut = lambda: [{"sid": "1" * 36, "name": "web"}]
        self._pass(); self._pass()
        self.assertEqual(self.ran, [], "with a turn to spare the parked quiet deploy stands the check down")
        lines = self._lines()
        self.assertEqual(len(lines), 2, "said on every pass, not once per sha: %r" % lines)
        for l in lines:
            self.assertEqual(l, "romp-kernel: converge: bbb is parked as a quiet deploy; leaving it to the quiet window; a restart now would cut 1 turn: web")
        km._deploy_would_cut = lambda: []
        self._pass()
        self.assertEqual(self.ran, ["restart"], "nothing to spare: the converge does not wait for the window")
        self.assertEqual(self._lines()[-1], "romp-kernel: converge: bbb is parked as a quiet deploy, but a restart now would cut no turn: converging without waiting for the window")

    def test_the_backend_answers_would_cut_with_the_drains_own_predicate(self):
        import sys, tempfile, types
        sbm = sys.modules.get("romp_sdk_backend") or load_source("romp_sdk_backend", os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))
        d = tempfile.mkdtemp()
        open(os.path.join(d, "session-hosts"), "w").write("off")   # a bare state root pins hosts off (the suite's rule, T348)
        be = sbm.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        mk = lambda sid, name, inflight, host=None, intent=False: types.SimpleNamespace(sid=sid, name=name, inflight=inflight, _host=host, _host_intent=intent)
        be.sessions = {"a": mk("a" * 36, "plain-busy", True), "b": mk("b" * 36, "plain-idle", False),
                       "c": mk("c" * 36, "hosted-busy", True, host=object()), "d": mk("d" * 36, "attaching-busy", True, intent=True)}
        self.assertEqual(be.would_cut(), [{"sid": "a" * 36, "name": "plain-busy"}], "only a plain child with a turn in flight is a cut")
        be.sessions = {"c": mk("c" * 36, "hosted-busy", True, host=object())}
        self.assertEqual(be.would_cut(), [], "every working session hosted: a restart cuts nothing")
        self.assertEqual(km._deploy_would_cut(), None, "no backend in this process: unknown")
        saved = km._sdk_backend
        km._sdk_backend = be
        try:
            self.assertEqual(km._deploy_would_cut(), [])
        finally:
            km._sdk_backend = saved


if __name__ == "__main__":
    unittest.main()


class UiOnlyConverge(unittest.TestCase):
    """A drift whose commits touch nothing the running process executes converges by rebuilding dist
    in place — kernel left up, zero cut turns (the user 2026-08-23: most changes are UI-only, and
    every restart cuts every in-flight turn). Kernel-code drift keeps the restart path unchanged."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_main_drift_verdict", "_kernel_code_changed", "_rebuild_dist",
                        "_sync_notice", "_update_mode", "_send_to_app", "_kernel_sha",
                        "_main_tracking", "_converge_classes", "_origin_main_sha", "_checkout_sha")}
        km._origin_main_sha = lambda: "tgt"     # stubbed: the real one runs `git ls-remote`, a NETWORK call per test
        km._checkout_sha = lambda: "tgt"        # (the classifier test's precedent; the stubbed verdict decides below)
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._REBUILT_FOR[0] = ""
        km._INPLACE_TRIED[0] = ""
        km._converge_classes = lambda a, b: {"kernel": [], "bus": [],
                                             "skip": ["ui/webview/feed.ts"], "ast_equal": []}
        self.notices, self.banners, self.rebuilds = [], [], []
        km._sync_notice = lambda msg, ok=True: self.notices.append((msg, ok))
        km._send_to_app = lambda app, payload: self.banners.append(payload)
        km._update_mode = lambda: "ask"
        km._kernel_sha = lambda: "cur-sha"
        km._main_tracking = lambda: True   # these tests exercise POST-gate behavior; the gate has its own
        self._stamp = km._CONVERGE_CRASH_T[0]

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._REBUILT_FOR[0] = ""
        km._INPLACE_TRIED[0] = ""
        km._CONVERGE_CRASH_T[0] = self._stamp

    def test_the_restart_legs_in_place_converge_clears_the_crash_hold_and_a_failed_build_does_not(self):
        # the follow-up review's second round: this road returned on success with the stamp standing
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-ui")
        km._kernel_code_changed = lambda a, b: False
        km._rebuild_dist = lambda: (False, "esbuild boom")
        km._CONVERGE_CRASH_T[0] = expired = time.time() - km._CONVERGE_COOLDOWN_S - 1
        km._main_drift_check()
        self.assertEqual(km._CONVERGE_CRASH_T[0], expired, "a failed build converged nothing: the stamp stands")
        km._REBUILT_FOR[0] = ""; km._INPLACE_TRIED[0] = ""; km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._rebuild_dist = lambda: (self.rebuilds.append(1), (True, ""))[1]
        km._main_drift_check()
        self.assertEqual((len(self.rebuilds), km._REBUILT_FOR[0]), (1, "tgt-ui"))
        self.assertEqual(km._CONVERGE_CRASH_T[0], 0.0, "the in-place converge from the restart leg clears the crash hold")

    def test_ui_only_restart_drift_rebuilds_in_place_and_latches(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-ui")
        km._kernel_code_changed = lambda a, b: False
        km._rebuild_dist = lambda: (self.rebuilds.append(1), (True, ""))[1]
        km._main_drift_check()
        self.assertEqual(len(self.rebuilds), 1)
        self.assertEqual(km._REBUILT_FOR[0], "tgt-ui", "the converge latches on the target sha")
        self.assertEqual(self.banners, [], "no restart banner for a rebuild that cuts nothing")
        self.assertTrue(any("no restart" in m for m, _ in self.notices))
        km._main_drift_check()   # same target again: already converged, no second build
        self.assertEqual(len(self.rebuilds), 1)

    def test_a_failed_build_falls_through_to_the_restart_path_loudly(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-ui")
        km._kernel_code_changed = lambda a, b: False
        km._rebuild_dist = lambda: (False, "esbuild boom")
        km._main_drift_check()
        self.assertEqual(km._REBUILT_FOR[0], "", "a failed build never latches")
        self.assertTrue(any(not ok and "esbuild boom" in m for m, ok in self.notices))
        self.assertEqual([b.get("drift") for b in self.banners], ["restart"],
                         "the normal restart offer still fires")

    def test_kernel_code_drift_keeps_the_restart_path(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-kern")
        km._kernel_code_changed = lambda a, b: True
        km._rebuild_dist = lambda: self.fail("a kernel-code drift must never rebuild in place")
        km._main_drift_check()
        self.assertEqual([b.get("drift") for b in self.banners], ["restart"])

    def test_the_classifier_reads_the_touched_paths(self):
        from unittest.mock import patch
        km._converge_classes = self._saved["_converge_classes"]   # the REAL classifier under test

        class R:
            def __init__(self, out, rc=0):
                self.stdout, self.returncode = out, rc

        def fake_run(names, show_rc=1):
            # git diff answers the NUL-separated name list; git show (the AST-equality probe)
            # answers show_rc — rc=1 means "blob unreadable", the not-provable arm that must
            # read as kernel code
            def run(argv, **kw):
                return R(names) if argv[:2] == ["git", "diff"] else R(b"", rc=show_rc)
            return run
        with patch.object(km.subprocess, "run", fake_run("ui/webview/feed.ts\0docs/a.md\0")):
            self.assertFalse(km._kernel_code_changed("a1", "b2"), "UI + docs only: rebuild in place")
        with patch.object(km.subprocess, "run", fake_run("ui/webview/feed.ts\0kernel/kernel.py\0")):
            self.assertTrue(km._kernel_code_changed("a1", "b2"),
                            "a kernel file whose blobs cannot be proven equal restarts")
        with patch.object(km.subprocess, "run", lambda argv, **kw: R("", rc=128)):
            self.assertTrue(km._kernel_code_changed("a1", "b2"), "git failure: the restart is the safe converge")
        self.assertTrue(km._kernel_code_changed("", "b2"), "unknown shas: the restart is the safe converge")

    def test_the_pull_path_carries_the_same_in_place_converge(self):
        src = inspect.getsource(km._run_main_update)
        self.assertIn("pulled = _checkout_sha()", src)
        self.assertIn("not _kernel_code_changed(_kernel_sha(reask=True), pulled) and _in_place_converge(pulled)",
                      src, "verdict input and converge target are the SAME read — never raced")
        conv = inspect.getsource(km._in_place_converge)
        self.assertIn("_rebuild_dist()", conv)
        self.assertIn("_REBUILT_FOR[0] = target", conv)


class ChannelGate(unittest.TestCase):
    """The audience gate (the user 2026-08-31, two independent "constant update notices" reports):
    dev commits must never notify a plain install — one notice per release TAG is their whole
    channel, owned by _update_check. The maintainer's mesh keeps the drift watcher through its own
    signals; everything else (bootstrap installs detached at a tag, and the installs an old banner
    walked onto a detached main sha) is the release channel and stays silent here."""

    def test_the_pure_verdict_truth_table(self):
        V = km._main_channel_verdict
        self.assertTrue(V("main", "ask", False), "a deliberate branch-main clone tracks main")
        self.assertTrue(V("", "auto", False), "auto mode = the unattended-converge machines")
        self.assertTrue(V("", "ask", True), "attached remotes = the federated mesh")
        self.assertFalse(V("", "ask", False),
                         "detached + ask + no remotes = a plain install: the bootstrap tag "
                         "checkout AND the old banner's walked-onto-main victims both land here")
        self.assertFalse(V("some-branch", "ask", False), "a feature-branch checkout is not main-tracking")

    def test_the_check_bails_for_a_plain_install_before_any_git_io(self):
        saved = (km._main_tracking, km._main_drift_verdict, km._send_to_app, km._update_mode)
        calls, banners = [], []
        try:
            km._update_mode = lambda: "ask"
            km._main_tracking = lambda: False
            km._main_drift_verdict = lambda *a: calls.append(a) or ("pull", "deadbeef")
            km._send_to_app = lambda app, payload: banners.append(payload)
            km._main_drift_check()
        finally:
            (km._main_tracking, km._main_drift_verdict, km._send_to_app, km._update_mode) = saved
        self.assertEqual(calls, [], "gated out BEFORE the verdict — no ls-remote, no banner")
        self.assertEqual(banners, [])

    def test_the_live_reader_consults_branch_mode_and_both_remote_stores(self):
        src = inspect.getsource(km._main_tracking)
        self.assertIn("bool(_remotes)", src, "live attached rows count (the Mac's dial-in included)")
        self.assertIn("KNOWN_FILE", src, "remembered hosts count — the mesh signal survives a detach")
        self.assertIn("_main_channel_verdict(_checkout_branch(), _update_mode(), attached)", src)


class PersistentDismissal(unittest.TestCase):
    """Not-now outlives the page and the kernel (the user 2026-08-31): a dismissed sha/tag stays
    dismissed until a NEW one — the per-page-load re-derive and the per-restart re-offer were the
    notice-spam compounders on the dev mesh too."""

    def setUp(self):
        try:
            (km.jd.STATE / km._DISMISSED_UPDATES_FILE_NAME).unlink()
        except OSError:
            pass

    tearDown = setUp

    def test_round_trip_and_restart_survival(self):
        km._dismiss_update("aaaa1111")
        km._dismiss_update("v0.14.0")
        self.assertEqual(km._dismissed_updates(), ["aaaa1111", "v0.14.0"],
                         "the store is a FILE — module state resets (a restart) cannot re-offer")
        km._dismiss_update("aaaa1111")
        self.assertEqual(km._dismissed_updates(), ["v0.14.0", "aaaa1111"], "re-dismissal dedupes")

    def test_a_dismissed_drift_sha_never_banners_but_a_new_one_does(self):
        saved = {n: getattr(km, n) for n in
                 ("_main_tracking", "_main_drift_verdict", "_send_to_app", "_update_mode",
                  "_kernel_sha", "_kernel_code_changed", "_origin_main_sha", "_checkout_sha")}
        banners = []
        try:
            km._update_mode = lambda: "ask"
            km._main_tracking = lambda: True
            km._kernel_sha = lambda: "cur"
            km._origin_main_sha = lambda: "tgt"     # stubbed: the real one is a network call (the classifier test's precedent)
            km._checkout_sha = lambda: "tgt"
            km._kernel_code_changed = lambda a, b: True
            km._send_to_app = lambda app, payload: banners.append(payload)
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._dismiss_update("deadbee1")
            km._main_drift_verdict = lambda *a: ("pull", "deadbee1")
            km._main_drift_check()
            self.assertEqual(banners, [], "the dismissed sha stays quiet across restarts")
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._main_drift_verdict = lambda *a: ("pull", "feedf00d")
            km._main_drift_check()
            self.assertEqual([b.get("tag") for b in banners], ["feedf00d"],
                             "a NEW sha is new information and offers")
        finally:
            for n, f in saved.items():
                setattr(km, n, f)
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""

    def test_the_banner_posts_the_dismissal_and_the_route_stores_it(self):
        src = inspect.getsource(km)
        self.assertIn("fetch('/update-dismiss'", src, "Not-now persists server-side, not just page-local")
        self.assertIn('u.path == "/update-dismiss"', src)
        self.assertIn("_dismiss_update((b or {}).get(\"tag\"))", src)

    def test_update_check_route_blanks_dismissed_offers(self):
        src = inspect.getsource(km)
        self.assertIn('"tag": ("" if _UPDATE_AVAIL[0] in dis else _UPDATE_AVAIL[0])', src,
                      "a page load can no longer re-derive a dismissed offer")


class PlainInstallCopy(unittest.TestCase):
    def test_the_drift_copy_drops_mesh_operator_language(self):
        src = inspect.getsource(km)
        self.assertNotIn("restarts every kernel", src)
        self.assertIn("Update pulls them and restarts romp.", src)
        self.assertIn("Update restarts romp onto it.", src)


class ConvergePullStep(unittest.TestCase):
    """The pull step DRIVEN, every subprocess scripted: each guard stops the sequence where it claims,
    nothing is checked out or restarted past a refusal, and the move lands on the commit the verdict
    advertised — never on the remote's ref. Before this, the fetch's exit code went unread (a failed
    fetch re-checked-out the stale local ref and reported success), `git status` read as CLEAN
    whenever it failed, the checkout followed `<remote>/main` with no ancestry check, and the restart
    POST rode urllib, whose default opener honours HTTP_PROXY. Every refusal lands on the sync
    surface (_sync_notice, ok=False) — the row the updater's failures already use — and re-arms."""

    TARGET = "abcd1234"

    def setUp(self):
        self._saved = (km._MAIN_DRIFT[0], km._MAIN_DRIFT[1], km._INPLACE_TRIED[0])
        km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = self.TARGET, ""
        km._INPLACE_TRIED[0] = ""
        self.notices, self.posts, self.calls, self.dials = [], [], [], []

    def tearDown(self):
        km._MAIN_DRIFT[0], km._MAIN_DRIFT[1], km._INPLACE_TRIED[0] = self._saved

    def _drive(self, target=TARGET, fail=(), status_out="", status_rc=0, proxy="", err="", answer=200):
        """`fail`: step names that exit 1, or a {step: rc} dict; `err`: the stderr a failing step
        prints (default '<step>: boom'); `answer`: the manager's HTTP status to the restart POST."""
        import subprocess
        import urllib.request
        from unittest import mock
        rec = self
        rcs = fail if isinstance(fail, dict) else {s: 1 for s in fail}

        def fake_run(argv, **kw):
            step = ("status" if "status" in argv else "fetch" if "fetch" in argv
                    else "merge-base" if "merge-base" in argv else "checkout" if "checkout" in argv
                    else "rev-parse" if "rev-parse" in argv else "other")
            rec.calls.append((step, list(argv)))
            rc, out = rcs.get(step, 0), ""
            if step == "status":
                rc, out = status_rc, status_out
            elif step == "rev-parse":
                out = rec.TARGET + "\n"
            return subprocess.CompletedProcess(argv, rc, stdout=out,
                                               stderr=(err or "%s: boom" % step) if rc else "")

        class FakeConn:
            def __init__(self, host, port, timeout=0):
                rec.dials.append((host, int(port)))

            def request(self, method, path, *a, **k):
                rec.posts.append((method, path))

            def getresponse(self):
                r = mock.MagicMock()
                r.status = answer
                r.read = lambda: b""
                return r

            def close(self):
                pass

        env = {"HTTP_PROXY": proxy, "http_proxy": proxy} if proxy else {}
        with mock.patch.object(km.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(km, "_sync_notice", side_effect=lambda m, ok=True: rec.notices.append((m, ok))), \
             mock.patch.object(km, "_kernel_sha", return_value="cur-sha"), \
             mock.patch.object(km, "_kernel_code_changed", return_value=True), \
             mock.patch.object(km, "_rebuild_dist", return_value=(True, "")), \
             mock.patch.object(km, "_audit_restart_request", lambda *a, **k: None), \
             mock.patch.object(km.http.client, "HTTPConnection", FakeConn), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=AssertionError("the restart POST must not ride urllib")), \
             mock.patch.dict(km.os.environ, env):
            self.result = km._run_main_update("pull", immediate=True, manager_port="1", target=target)
        return [s for s, _ in self.calls if s != "other"]

    def _refusals(self):
        return [m for m, ok in self.notices if not ok]

    def test_the_return_says_whether_a_converge_ran_and_succeeded(self):
        # the follow-up review's second round: the drift check's crash-hold clear keys on this
        self._drive()
        self.assertIs(self.result, True, "the checkout moved and the restart was requested")
        self._drive(target="")
        self.assertIs(self.result, False, "a refusal (no commit named) is not a converge")
        self.assertTrue(self._refusals(), "and it was said")

    def test_the_happy_path_moves_onto_the_advertised_commit_and_restarts(self):
        steps = self._drive()
        # two ancestry checks since 2026-09-08: HEAD against the target (the refusal gate) and the local
        # main branch against it (moved and checked out when it is an ancestor; see the checkout step)
        self.assertEqual(steps[:5], ["status", "fetch", "merge-base", "merge-base", "checkout"], steps)
        self.assertEqual(set(steps[5:]), {"rev-parse"}, "after the move, only the checkout re-reads")
        fetch = next(a for s, a in self.calls if s == "fetch")
        self.assertEqual(fetch[-3:], ["fetch", "origin", "main"],
                         "main from the release remote (the scripted `git remote` lists none: a plain install's origin)")
        anc = next(a for s, a in self.calls if s == "merge-base")
        self.assertEqual(anc[-3:], ["--is-ancestor", "HEAD", self.TARGET], "a fast-forward from HEAD, proven")
        co = next(a for s, a in self.calls if s == "checkout")
        # the scripted git answers every ancestry check yes, so local main is an ancestor here: it is moved
        # onto the sha the verdict named and checked out (2026-09-08); the diverged case detaches instead
        # (tests/test_converge_main_branch.py, over real repositories)
        self.assertEqual(co[-3:], ["-B", "main", self.TARGET], "the move lands on the sha the verdict named, on main")
        self.assertFalse(any(a.endswith("/main") for a in co), "never the ref: it can move, or sit stale")
        self.assertEqual(self.posts, [("POST", "/restart-all")])
        self.assertEqual(self.dials, [("127.0.0.1", 1)])
        self.assertEqual(self._refusals(), [], "nothing refused; the restart went out")

    def test_the_restart_post_ignores_a_proxy_environment(self):
        # urllib's default opener would have sent this loopback POST to the proxy — the updated code
        # on disk then never restarted, and the only trace was "restart request failed"
        self._drive(proxy="http://proxy.invalid:3128")
        self.assertEqual(self.posts, [("POST", "/restart-all")])
        self.assertEqual(self.dials, [("127.0.0.1", 1)], "dialled direct, whatever HTTP_PROXY says")
        self.assertEqual(self._refusals(), [])

    def test_a_refused_restart_answer_is_said_not_swallowed(self):
        # urlopen raised on a 4xx/5xx; http.client hands the status back — an answer of 500 from the
        # manager is still a restart that did not happen, and the notice must say so
        self._drive(answer=500)
        self.assertEqual(self.posts, [("POST", "/restart-all")], "the POST went out")
        said = [m for m, ok in self.notices if not ok and "restart request failed" in m]
        self.assertEqual(len(said), 1, self.notices)
        self.assertIn("HTTP 500", said[0])
        self.assertIn("romp refresh", said[0], "and names the way out")

    def test_a_failed_fetch_aborts_before_any_move(self):
        # a realistic ssh failure is ~190 chars of stderr; the row is capped at 300 on the wire and
        # 240 in the badge, so the OUTCOME leads and git's words come last, trimmed
        ssh_err = "ssh: connect to host git.example.invalid port 22: Connection timed out\n" * 4
        steps = self._drive(fail=("fetch",), err=ssh_err)
        self.assertEqual(steps, ["status", "fetch"], "the sequence stops AT the fetch")
        self.assertEqual(self.posts, [], "nothing restarted onto an unmoved checkout")
        said = [m for m in self._refusals() if "the fetch failed" in m]
        self.assertEqual(len(said), 1, self.notices)
        self.assertIn("left alone", said[0], "the outcome survives a long git error")
        self.assertLess(said[0].index("left alone"), said[0].index("git:"), "outcome first, git's words after")
        self.assertIn("Connection timed out", said[0], "and git's words are still there")
        self.assertLessEqual(len(said[0]), 240, "inside the badge's cap")
        self.assertEqual(km._MAIN_DRIFT[0], "", "the notice re-arms")

    def test_a_commit_that_is_not_a_fast_forward_of_head_is_refused(self):
        # merge-base --is-ancestor exits 1: a real non-ancestor — the histories diverged, which is
        # never merged on the user's behalf; the checkout stays where it is
        steps = self._drive(fail=("merge-base",))
        self.assertEqual(steps, ["status", "fetch", "merge-base"])
        self.assertEqual(self.posts, [])
        said = [m for m in self._refusals() if "not a fast-forward" in m and self.TARGET in m]
        self.assertEqual(len(said), 1, "the refusal names the commit: %r" % self.notices)
        self.assertIn("diverged", said[0])
        self.assertIn("yours to move by hand", said[0])
        self.assertNotIn("did not bring", said[0], "a divergence is not a missing commit")
        self.assertEqual(km._MAIN_DRIFT[0], "")

    def test_a_commit_the_fetch_did_not_bring_is_said_as_such(self):
        # merge-base exits 128: git cannot name the advertised commit — main was rewound after the
        # verdict, or the short prefix is ambiguous. Not a divergence: the next check re-reads main
        steps = self._drive(fail={"merge-base": 128}, err="fatal: Not a valid commit name abcd1234")
        self.assertEqual(steps, ["status", "fetch", "merge-base"])
        self.assertEqual(self.posts, [])
        said = [m for m in self._refusals() if "did not bring" in m and self.TARGET in m]
        self.assertEqual(len(said), 1, self.notices)
        self.assertIn("re-reads main", said[0])
        self.assertIn("Not a valid commit name", said[0], "git's words, after the outcome")
        self.assertNotIn("diverged", said[0])
        self.assertEqual(km._MAIN_DRIFT[0], "")

    def test_a_failing_status_is_unknown_never_clean(self):
        # rc 128 with nothing on stdout: the old `.stdout.strip()` read saw "" and called it clean,
        # then moved a tree whose state nobody had actually read
        steps = self._drive(status_rc=128)
        self.assertEqual(steps, ["status"], "stops at the status read")
        self.assertEqual(self.posts, [])
        refusals = self._refusals()
        self.assertTrue(any("its state could not be read" in m and "left alone" in m for m in refusals), refusals)
        self.assertFalse(any("uncommitted work" in m for m in refusals), "not mistaken for a dirty tree either")
        self.assertEqual(km._MAIN_DRIFT[0], "")

    def test_a_dirty_tree_still_refuses_with_the_standing_wording(self):
        steps = self._drive(status_out=" M kernel/kernel.py\n")
        self.assertEqual(steps, ["status"])
        self.assertEqual(self.posts, [])
        self.assertTrue(any("uncommitted work" in m for m in self._refusals()))
        self.assertEqual(km._MAIN_DRIFT[0], "")

    def test_an_empty_target_refuses_before_touching_git(self):
        # binding is mandatory: with no advertised commit there is nothing to bind the move to
        steps = self._drive(target="")
        self.assertEqual(steps, [], "no status, no fetch, no move")
        self.assertEqual(self.posts, [])
        self.assertTrue(any("no commit was named" in m for m in self._refusals()), self.notices)
        self.assertEqual(km._MAIN_DRIFT[0], "")
