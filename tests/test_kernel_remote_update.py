"""Remote version-drift detection + `romp update` (the user 2026-07-04): the local kernel polls each attached
remote's /version, flags one running an OLDER commit (outOfDate), and offers to pull+restart it behind the
scenes. `POST /tunnels/update` runs the ssh git-pull + restart; the rail popover + a top banner surface it.
SYNTHETIC hosts; subprocess/http are stubbed so nothing actually launches or connects."""
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source
from git_fixture import git, init_repo

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class _R:
    def __init__(self, out="", err="", rc=0):
        self.stdout, self.stderr, self.returncode = out, err, rc


class VersionDrift(unittest.TestCase):
    def setUp(self):
        self._hc = dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=9e18, full="abc12340000", short="abc1234")   # pin local HEAD, skip the subprocess

    def tearDown(self):
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)

    def test_sha_base_strips_dirty(self):
        self.assertEqual(km._sha_base("abc1234-dirty"), "abc1234")
        self.assertEqual(km._sha_base("abc1234"), "abc1234")
        self.assertIsNone(km._sha_base(""))
        self.assertIsNone(km._sha_base(None))

    def test_shas_agree_tolerates_different_short_lengths(self):
        self.assertTrue(km._shas_agree("abc1234", "abc1234567"), "one a prefix of the other → same commit")
        self.assertTrue(km._shas_agree("abc1234-dirty", "abc1234"), "'-dirty' ignored")
        self.assertFalse(km._shas_agree("abc1234", "def5678"))
        self.assertFalse(km._shas_agree("abc1234", ""))

    def test_drift_is_measured_against_live_HEAD_and_CLEARS_when_matched(self):
        # the fix (the user 2026-07-04): drift compares the remote to the LIVE HEAD — the SAME thing the push
        # sends — so once the remote is pushed to HEAD the flag goes away (it used to compare to the kernel's
        # cached startup sha while the push sent HEAD, so it never reconciled → banner stuck forever).
        self.assertTrue(km._remote_out_of_date({"kernel_sha": "def5678"}), "different commit → out of date")
        self.assertFalse(km._remote_out_of_date({"kernel_sha": "abc1234"}), "remote pushed to HEAD → CLEARS")
        self.assertFalse(km._remote_out_of_date({"kernel_sha": "abc12345"}), "same commit, longer short → clears")
        self.assertFalse(km._remote_out_of_date({}), "unknown remote sha → not flagged")
        self.assertFalse(km._remote_out_of_date({"kernel_sha": ""}), "blank remote sha → not flagged")

    def test_remote_public_exposes_version_fields(self):
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801, "token": "t",
                                 "status": "up", "sids": [], "kernel_sha": "def5678"})
        self.assertEqual(pub["kernelSha"], "def5678")
        self.assertEqual(pub["localSha"], "abc1234", "localSha is the live HEAD short (what a push would send)")
        self.assertTrue(pub["outOfDate"])


class UpdateRemote(unittest.TestCase):
    """PEER-TO-PEER update (the user 2026-07-04): push local committed HEAD to the remote (no GitHub), refuse on
    a dirty/diverged remote, restart. Three subprocess calls — ssh-discover, git-push, ssh-apply — are dispatched
    by inspecting argv so each case can drive them independently."""
    LFULL = "1" * 40                        # local HEAD (full sha) the push sends
    RHEAD = "2" * 40                         # a remote at a DIFFERENT (older) commit

    def setUp(self):
        self._run, self._hc = km.subprocess.run, dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=0.0, full=None, short=None)   # force _local_head to consult the mocked git

    def tearDown(self):
        km.subprocess.run = self._run
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)

    def _wire(self, rhead=None, dirty="", disc_out=None, push_rc=0, push_err="", apply_out="SYNCED:abcdef0"):
        """Install a dispatching subprocess mock; returns the list of argv it saw."""
        if disc_out is None:
            disc_out = "DIR:/home/u/romp\nHEAD:%s\nDIRTY:%s" % (rhead if rhead is not None else self.RHEAD, dirty)
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "push" in argv:
                return _R(err=push_err, rc=push_rc)
            if argv[0] == "git" and "rev-parse" in argv and "HEAD" in argv:   # _local_head
                return _R(out=self.LFULL)
            cmd = argv[-1]                                                     # ssh: dispatch on the remote command
            if "for d in" in cmd:
                return _R(out=disc_out)
            if "merge-base" in cmd or "reset --hard" in cmd:
                return _R(out=apply_out)
            return _R()
        km.subprocess.run = fake
        return calls

    def test_no_host_is_a_no_op(self):
        self.assertEqual(km._update_remote(""), (False, "no host"))

    def test_a_clean_ancestor_remote_is_pushed_reset_and_restarted(self):
        calls = self._wire(apply_out="SYNCED:abcdef0")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("synced to abcdef0", detail)
        # it force-pushed local HEAD to a scratch ref at host:remote-dir
        push = next(a for a in calls if a[0] == "git" and "push" in a)
        self.assertIn("--force", push)
        self.assertIn("TESTHOST:/home/u/romp", push)
        self.assertIn(self.LFULL + ":refs/heads/" + km._P2P_REF, push,
                      "pushes the exact advertised sha to the scratch ref, not a HEAD that may move under it")

    def test_the_apply_restarts_through_the_manager_at_once_with_an_audit_row(self):
        # T238: the remote apply used to `pkill` the far kernel outright — an anonymous, immediate
        # SIGTERM (no restart-audit row: nine in-flight-turn cuts in three hours on a merge day, each
        # read by the dialing side as "unreachable"). The apply writes the audit row first and asks the
        # far MANAGER for the restart; pkill survives only as the last-resort branch for a host with no
        # manager. T269 (the user 2026-09-08): the manager bounces AT ONCE — the parked quiet window
        # held a box unusable for the full 15-minute backstop on 26 of 32 restarts in a morning, and
        # boot reconcile resumes the cut turns either way; the quiet window is `romp refresh --quiet` only.
        calls = self._wire(apply_out="SYNCED:abcdef0:MANAGED")
        km._remotes["TESTHOST"] = {"host": "TESTHOST"}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertIn("restarting now", detail)
        self.assertNotIn("quiet", detail)
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        self.assertIn("restart-audit.jsonl", apply, "the restart is never anonymous")
        self.assertIn("p2p-update", apply)
        self.assertIn('romp-manager" restart-all >>', apply, "the manager's IMMEDIATE restart, not a kill")
        self.assertNotIn("restart-all --quiet", apply, "no deploy path asks for the quiet window (T269)")
        self.assertNotIn("'when':'quiet'", apply, "the p2p row is an immediate request: no quiet marker")
        self.assertLess(apply.index('restart-all >>'), apply.index('pkill -f "bin/romp-kern[e]l"'),
                        "pkill is the fallback AFTER the manager path, never the first move")
        exp = km._remotes["TESTHOST"].get("restartExpected")
        self.assertTrue(exp and exp.get("sha") == self.LFULL and exp.get("t") and exp.get("quiet") is False,
                        "the dialing side expects the restart it just caused; quiet is recorded, not read")

    def test_every_deploy_restart_is_immediate_and_only_refresh_quiet_defers(self):
        # T269, the three deploy callers: a peer's p2p update (the apply script above), a release
        # self-update (_run_update, immediate since T160) and the automatic converge (_run_main_update's
        # default). The quiet window's one door is `romp refresh --quiet`: bin/romp forwards the flag,
        # bin/romp-manager maps it to when=quiet, and nothing else sends it.
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ksrc = open(os.path.join(root, "kernel", "kernel.py")).read()
        i = ksrc.index("def _run_update(tag):"); j = ksrc.index("\ndef ", i + 10)
        self.assertNotIn("when=quiet", ksrc[i:j], "the release self-update restarts at once")
        self.assertIn("def _run_main_update(kind, immediate=True", ksrc, "the converge's default is immediate")
        self.assertNotIn("restart-all --quiet", ksrc, "no kernel-generated script asks the manager for the quiet window")
        cli = open(os.path.join(root, "bin", "romp")).read()
        self.assertIn('restart-all "${2:-}"', cli, "romp refresh forwards --quiet, the one door")
        mgr = open(os.path.join(root, "bin", "romp-manager")).read()
        self.assertIn("process.argv[3] === '--quiet' ? { when: 'quiet' } : {}", mgr, "…which the manager maps to when=quiet")

    def test_a_host_with_no_owning_manager_restarts_the_old_way_and_says_so(self):
        calls = self._wire(apply_out="SYNCED:abcdef0:FALLBACK")
        km._remotes["TESTHOST"] = {"host": "TESTHOST"}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertIn("immediate", detail)
        self.assertNotIn("quiet window", detail)
        self.assertIs(km._remotes["TESTHOST"]["restartExpected"]["quiet"], False)

    def test_the_managed_path_requires_the_manager_to_own_the_polled_kernel(self):
        # a manager owning nothing (or a bare kernel beside a crash-looping managed one) answers 202
        # and restarts nothing — trusting it turned the update into a silent never-restart (review)
        calls = self._wire(apply_out="SYNCED:abcdef0:MANAGED")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        self.assertIn('romp-manager" status', apply, "ownership is read from the manager's own registry")
        self.assertLess(apply.index('romp-manager" status'), apply.index('restart-all >>'))
        self.assertIn('if [ "$OWNED" = 1 ]', apply)
        # per-branch audit rows: the request row precedes the manager call; the fallback writes its own
        # row right before pkill, so the cut row joins the request that happened
        self.assertEqual(apply.count("restart-audit.jsonl"), 2)
        self.assertLess(apply.index("p2p-update"), apply.index('restart-all >>'),
                        "the request row lands before the manager request")
        self.assertLess(apply.index('restart-all >>'), apply.index("immediate: no owning manager"),
                        "the fallback writes its own row after the managed branch was skipped")
        self.assertLess(apply.index("immediate: no owning manager"), apply.index('pkill -f "bin/romp-kern[e]l"'))
        self.assertIn('SYNCED:$NEW:FALLBACK', apply)

    def test_both_generated_apply_scripts_parse_as_bash(self):
        import shlex, subprocess as sp
        calls = self._wire(apply_out="SYNCED:abcdef0:MANAGED")
        km._update_remote("TESTHOST")
        wrapper = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        inner = shlex.split(wrapper.split("; if command -v setsid")[0][len("APPLY="):])[0]
        r = sp.run(["bash", "-n"], input=inner, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        calls2 = []
        real = km.subprocess.run
        def fake2(argv, **kw):
            calls2.append(argv)
            if argv[0] == "git":
                return _R(out=self.LFULL)
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % self.RHEAD)
            return _R(out="RESTARTED:1")
        km.subprocess.run = fake2
        try:
            ok, _ = km._restart_remote_kernel("TESTHOST")
        finally:
            km.subprocess.run = real
        self.assertTrue(ok)
        wrapper2 = next(a[-1] for a in calls2 if isinstance(a[-1], str) and "RESTARTED" in a[-1])
        inner2 = shlex.split(wrapper2.split("; if command -v setsid")[0][len("APPLY="):])[0]
        r2 = sp.run(["bash", "-n"], input=inner2, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_the_expectation_is_stamped_before_the_apply_runs_and_popped_when_nothing_restarted(self):
        # an idle far kernel is SIGTERMed within milliseconds of the manager's 202, and the fallback
        # kills it mid-ssh: a stamp AFTER the ssh returned arrived after the gap it explains (review)
        km._remotes["TESTHOST"] = {"host": "TESTHOST"}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        seen = []
        calls = self._wire(apply_out="SYNCED:abcdef0")
        real = km.subprocess.run
        def spy(argv, **kw):
            if isinstance(argv[-1], str) and "reset --hard" in argv[-1]:
                seen.append(dict(km._remotes["TESTHOST"].get("restartExpected") or {}))
            return real(argv, **kw)
        km.subprocess.run = spy
        km._update_remote("TESTHOST")
        km.subprocess.run = real
        self.assertTrue(seen and seen[0].get("sha") == self.LFULL, "expected BEFORE the apply ssh ran: %r" % seen)
        # a DIVERGED apply restarts nothing → the expectation is withdrawn
        self._wire(apply_out="DIVERGED")
        ok, _ = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"])

    def test_already_up_to_date_re_arms_the_expectation_while_the_far_kernel_lags(self):
        # the checkout holds our build but the KERNEL still answers the old sha: a restart is pending
        # (a quiet window forgotten across our own restart) — expect its gap instead of reading death
        self._wire(rhead=self.LFULL)
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_sha": "2222222"}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("has not restarted into it yet", detail)
        self.assertEqual(km._remotes["TESTHOST"]["restartExpected"]["sha"], self.LFULL)

    def test_an_explicit_restart_expects_a_gap_with_no_sha_and_withdraws_on_failure(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_sha": "2" * 40, "kernel_port": 29855}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        seen = []
        real = km.subprocess.run
        def fake(argv, **kw):
            if argv[0] == "git":
                return _R(out=self.LFULL)
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % self.RHEAD)
            seen.append(dict(km._remotes["TESTHOST"].get("restartExpected") or {}))
            return _R(out="NOLAUNCH")
        km.subprocess.run = fake
        try:
            ok, _ = km._restart_remote_kernel("TESTHOST")
        finally:
            km.subprocess.run = real
        self.assertFalse(ok)
        self.assertEqual(seen[0].get("sha"), "", "same build: no new sha to wait for — only the gap ends it")
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"], "nothing restarted → withdrawn")

    def test_already_up_to_date_short_circuits(self):
        self._wire(rhead=self.LFULL)          # remote already at local HEAD
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("already up to date", detail)

    def test_a_commit_made_just_before_the_update_is_what_gets_pushed(self):
        # The dashboard's polls read HEAD through a 15 s cache. A `romp update` inside that window used to
        # read the SAME cache, so a commit made a second earlier was invisible to it: the peer sat on the
        # previous commit, which equalled the cached head, and the update returned "already up to date"
        # having pushed nothing (and when it did push, the restart it told itself to expect named the old
        # sha). The transport reads the head the user actually has.
        stale, fresh = "3" * 40, "4" * 40
        calls = self._wire(rhead=stale, apply_out="SYNCED:4444444:MANAGED")   # the peer is on the OLD commit
        km._HEAD_CACHE.update(ts=9e18, full=stale, short=stale[:8])          # the cache still says so too
        km._remotes["TESTHOST"] = {"host": "TESTHOST"}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        real = km.subprocess.run
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv and "HEAD" in argv:
                calls.append(argv)
                return _R(out=fresh)                                          # what git says NOW
            return real(argv, **kw)
        km.subprocess.run = fake
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertNotIn("already up to date", detail)
        push = next(a for a in calls if a[0] == "git" and "push" in a)
        self.assertIn(fresh + ":refs/heads/" + km._P2P_REF, push, "the head the user has is what travels")
        self.assertEqual(km._remotes["TESTHOST"]["restartExpected"]["sha"], fresh,
                         "and it is the sha the far kernel is expected to come back on")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        self.assertIn("WANT=" + fresh, apply, "the apply is bound to the same sha")
        self.assertEqual(km._local_head(), fresh, "the polling cache was refreshed by the same read")

    def test_a_head_that_is_not_a_full_sha_is_refused_not_pushed(self):
        # the transport insists on the exact 40-hex commit it binds the peer to; anything else (a truncated
        # or garbled rev-parse answer) is "not a checkout", said so, and nothing is pushed
        calls = self._wire()
        real = km.subprocess.run
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(out="abc1234")
            return real(argv, **kw)
        km.subprocess.run = fake
        self.assertEqual(km._fresh_local_head(), "")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("git checkout", detail)
        self.assertFalse(any(a[0] == "git" and "push" in a for a in calls), "nothing pushed")

    def test_a_refusal_at_the_apply_reaches_the_row_and_the_log_as_a_failure(self):
        # the apply's refusals are not successes: the automatic push publishes them on the row's phase
        # and in the Log ring with the reason, never as a bare tag and never as "pushed"
        self._wire(apply_out="DIRTYNOW")
        self.addCleanup(km._set_auto_push, "TESTHOST", None)
        before = km._sync_notice_count()
        ok = km._auto_push_remote("TESTHOST")
        self.assertFalse(ok)
        st = km._auto_push_state("TESTHOST")
        self.assertEqual(st["phase"], "failed")
        self.assertIn("uncommitted changes", st["detail"])
        self.assertIn("TESTHOST", st["detail"])
        rows = [r for r in km._sync_notice_rows(limit=5) if r["text"].find("TESTHOST") >= 0]
        self.assertTrue(rows and rows[-1]["ok"] is False and "uncommitted changes" in rows[-1]["text"], rows)
        self.assertEqual(km._sync_notice_count(), before + 1)

    def test_the_automatic_push_is_keyed_on_the_sha_it_used_not_the_polls_cache(self):
        # The supervisor hook gates one attempt per (remote sha, local head) and keys it on the polls' CACHED
        # head. The push reads the head the user has and writes it into that cache, so the next pass computed
        # a NEW key for the SAME advance and fired a second push, which came back "already up to date ... has
        # not restarted into it yet" and logged "pushed" twice. The attempt is keyed on the sha the push used.
        stale, fresh, remote = "3" * 40, "4" * 40, "2" * 40
        calls, saved = [], (km._behind_info, km.threading.Thread, km._auto_update_remotes_on())
        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "push" in argv:
                return _R()
            if argv[0] == "git" and "rev-parse" in argv and "HEAD" in argv:
                return _R(out=fresh[:7] if "--short" in argv else fresh)             # what git says NOW
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % remote)         # the peer never restarts
            if "merge-base" in cmd or "reset --hard" in cmd:
                return _R(out="SYNCED:4444444:MANAGED")
            return _R()
        km.subprocess.run = fake
        km._behind_info = lambda sha, head=None: {"behind": 1, "ahead": 0, "date": ""}   # a straight fast-forward
        class _Now:                                                                 # the worker, run inline
            def __init__(self, target=None, args=(), daemon=None):
                self._t, self._a = target, args
            def start(self):
                self._t(*self._a)
        km.threading.Thread = _Now
        km._set_auto_update_remotes(True)
        km._auto_push.clear(); km._auto_push_tried.clear()
        km._HEAD_CACHE.update(ts=9e18, full=stale, short=stale[:7])                # what the polls last read
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "kernel_sha": remote[:7]}
        before = km._sync_notice_count()
        try:
            km._maybe_auto_push(dict(km._remotes["TESTHOST"]))                      # this pass pushes
            km._maybe_auto_push(dict(km._remotes["TESTHOST"]))                      # the next pass: same advance
        finally:
            km._behind_info, km.threading.Thread = saved[0], saved[1]
            km._set_auto_update_remotes(saved[2])
            km._auto_push.clear(); km._auto_push_tried.clear()
            km._remotes.pop("TESTHOST", None)
        pushes = [a for a in calls if a[0] == "git" and "push" in a]
        self.assertEqual(len(pushes), 1, "one advance, one push")
        self.assertIn(fresh + ":refs/heads/" + km._P2P_REF, pushes[0], "the head the user has is what travelled")
        self.assertEqual(km._sync_notice_count(), before + 1, "and one Log notice, not a duplicate 'pushed'")

    def test_a_dirty_local_is_not_refused_it_pushes_committed_head(self):
        # "just take what is committed on local" (the user 2026-07-04): a dirty working tree is NOT a blocker —
        # _update_remote pushes the committed HEAD and never asks you to commit first.
        self._wire(apply_out="SYNCED:abcdef0")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertNotIn("commit", detail.lower())

    def test_no_local_checkout_fails_cleanly(self):
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(rc=1)                            # not a git checkout
            return _R()
        km.subprocess.run = fake
        km._HEAD_CACHE.update(ts=0.0, full=None, short=None)
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("git checkout", detail)

    def test_refuses_a_dirty_remote_without_clobbering(self):
        self._wire(dirty="M")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("uncommitted changes", detail)

    def test_refuses_a_diverged_remote(self):
        self._wire(apply_out="DIVERGED")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("diverged", detail)

    def test_no_romp_clone_fails_loudly(self):
        self._wire(disc_out="NOROMP")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("not installed", detail)

    def test_a_failed_push_surfaces_the_git_error(self):
        self._wire(push_rc=1, push_err="Permission denied (publickey)")
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("git push", detail)
        self.assertIn("Permission denied", detail)

    def test_no_github_origin_in_the_remote_commands(self):
        # peer-to-peer: NOTHING should pull from origin / touch GitHub
        calls = self._wire()
        km._update_remote("TESTHOST")
        for a in calls:
            cmd = a[-1] if isinstance(a[-1], str) else ""
            self.assertNotIn("git pull", cmd, "no pull-from-origin anywhere")
            self.assertNotIn("origin", cmd)

    def test_restart_goes_through_the_manager_then_falls_back(self):
        # the user 2026-07-04: the restart should keep the remote MANAGER-owned (romp's durable supervisor, no
        # orphan) — kill the kernel, `romp-manager ensure` (respawns via a live manager, or STARTS one that spawns
        # a supervised kernel — upgrading an attach-bootstrapped bare host), then port-poll; bare romp-serve is a
        # LAST-RESORT fallback only when the port never returns. It must NOT rely on `romp --refresh` (the stuck bug).
        km._remotes = {"TESTHOST": {"host": "TESTHOST", "kernel_port": 29855}}
        calls = self._wire()
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertIn("pkill -f", apply, "kills the running kernel")
        self.assertIn('"$R/bin/romp-manager" ensure', apply, "prefers the manager (ensure = idempotent supervised start)")
        self.assertIn("/dev/tcp/127.0.0.1/29855", apply, "polls the remote's kernel port to confirm it came back")
        self.assertIn('if [ "$UP" = 0 ]; then nohup "$R/bin/romp-serve"', apply, "bare romp-serve only as a last resort")
        self.assertNotIn("--refresh", apply, "does NOT rely on `romp --refresh` (needs a manager) — the stuck bug")

    def test_a_host_stopped_by_romp_down_is_synced_but_not_restarted(self):
        # with the down-by-romp marker on the host, `romp-manager ensure` refuses (that is the marker's
        # job), the port poll fails and the bare fallback would boot an UNSUPERVISED kernel while
        # `romp status` there kept saying down. The apply checks the marker after the owning-manager
        # branch and before the immediate path touches anything
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        calls = self._wire(apply_out="SYNCED:abcdef0:DOWN")
        ok, detail = km._update_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertIn("synced to abcdef0", detail)
        self.assertIn("stopped by romp down", detail)
        self.assertIn("nothing was restarted", detail)
        self.assertIn("romp up on it starts the new code", detail, "the way to start it is named for the user")
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"], "no restart is coming: the gap is not expected")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        # $K trails every outcome after the guarded cleanup: a REFKEPT on a downed host is not dropped
        marker = 'if [ -f "$LOGDIR/down-by-romp" ]; then echo "SYNCED:$NEW:DOWN$K"; exit 0; fi'
        self.assertIn(marker, apply)
        self.assertLess(apply.index('restart-all >>'), apply.index(marker),
                        "a manager that owns the kernel still gets the immediate restart (its start cleared any marker)")
        self.assertLess(apply.index(marker), apply.index("immediate: no owning manager"),
                        "no audit row for a restart that does not happen")
        self.assertLess(apply.index(marker), apply.index('pkill -f "bin/romp-kern[e]l"'), "nothing killed")
        self.assertLess(apply.index(marker), apply.index('"$R/bin/romp-manager" ensure'), "nothing ensured")
        self.assertLess(apply.index(marker), apply.index('nohup "$R/bin/romp-serve"'), "no bare kernel")

    def test_a_romp_down_host_gets_no_audit_row_unless_a_manager_owns_its_kernel(self):
        # the audit row's two sites: with no marker the row is written before the owning-manager check, so the
        # far kernel's drift check sees it during the manager status call; with a marker the branch
        # exits with no restart, and a row there would name a restart nobody made, so the row is
        # written only once a manager is found owning the kernel, right before its restart. One
        # writer function, so the ledger still has one helper writer and one immediate-fallback
        # writer. The row is an immediate request (no when=quiet).
        calls = self._wire(apply_out="SYNCED:abcdef0:DOWN")
        km._update_remote("TESTHOST")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        gate = '[ -f "$LOGDIR/down-by-romp" ] || arow; '
        owned_row = '[ ! -f "$LOGDIR/down-by-romp" ] || arow; '
        self.assertIn('arow() { python3 -c', apply, "the audit row is one function, called per site")
        self.assertLess(apply.index("arow() {"), apply.index(gate))
        self.assertLess(apply.index(gate), apply.index("OWNED=0; if command -v node"),
                        "a live host: the row is on disk before the owning-manager check runs")
        self.assertLess(apply.index('if [ "$OWNED" = 1 ]'), apply.index(owned_row))
        self.assertLess(apply.index(owned_row), apply.index('restart-all >>'),
                        "a manager beside a marker: the row lands before the restart it attributes")
        self.assertEqual(apply.count("restart-audit.jsonl"), 2, "one helper writer, one immediate-fallback writer")
        self.assertEqual(apply.count("arow;"), 2)
        self.assertNotIn("'when':'quiet'", apply, "the p2p row is an immediate request at both sites")

    def test_a_same_build_restart_of_a_romp_down_host_says_not_restarting(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        calls = []
        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git":
                return _R(out=self.LFULL)
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % self.RHEAD)
            return _R(out="DOWN")
        km.subprocess.run = fake
        ok, detail = km._restart_remote_kernel("TESTHOST")
        self.assertFalse(ok, "the restart asked for did not run")
        self.assertIn("TESTHOST is stopped by romp down; not restarting it", detail)
        self.assertIn("romp up there starts it", detail)
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"])
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "RESTARTED" in a[-1])
        marker = 'if [ -f "$LOGDIR/down-by-romp" ]; then echo DOWN; exit 0; fi'
        self.assertIn(marker, apply)
        self.assertLess(apply.index(marker), apply.index("restart-audit.jsonl"), "no audit row, no kill, no boot")
        self.assertLess(apply.index(marker), apply.index('pkill -f "bin/romp-kern[e]l"'))

    def test_apply_is_detached_from_the_ssh_session(self):
        # the user 2026-07-11 (TESTHOST): the apply kills the running kernel before booting its
        # replacement, so an ssh drop between the two halves left the host kernel-LESS — and every
        # banner Retry re-killed whatever a previous attempt had booted. The apply now runs in its
        # own session (setsid, plain-bash fallback where setsid is missing), so once started the
        # kill+boot pair always completes on the remote even if the connection dies.
        km._remotes = {"TESTHOST": {"host": "TESTHOST", "kernel_port": 29855}}
        calls = self._wire()
        km._update_remote("TESTHOST")
        wrapper = next(a[-1] for a in calls if isinstance(a[-1], str) and "merge-base" in a[-1])
        self.assertTrue(wrapper.startswith("APPLY="), "the apply script rides a variable, quoted once")
        self.assertIn('exec setsid bash -c "$APPLY"', wrapper)
        self.assertIn('else exec bash -c "$APPLY"', wrapper, "hosts without setsid still work")

    def test_apply_timeout_says_the_restart_keeps_running(self):
        # the local 60s confirmation window can expire while a slow host is still mid-restart; the
        # detached apply keeps going, so the message must say that instead of implying a dead host
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(out=self.LFULL)
            if argv[0] == "git" and "push" in argv:
                return _R()
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % self.RHEAD)
            raise km.subprocess.TimeoutExpired(argv, 60)
        km.subprocess.run = fake
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("keeps", detail)
        self.assertIn("running", detail)


class ApplyHonesty(unittest.TestCase):
    """The scripts the p2p updater ships over ssh, EXECUTED against a real throwaway git repo (SSH_BIN is
    swapped for a stub that runs the command locally under a scrubbed env — the same harness the clone
    discovery tests use). The probe used to answer a FAILING `git status` as a clean tree; the apply used to
    go from the ancestry check straight to `reset --hard <scratch ref>` with no look at the tree it was about
    to overwrite and no check that the ref still named the commit this machine pushed. Synthetic repo, no
    real machine data; the only faked calls are the LOCAL `git push` (the scratch ref is planted directly)
    and the local head read."""

    def setUp(self):
        self._run, self._hc, self._ssh = km.subprocess.run, dict(km._HEAD_CACHE), km.SSH_BIN
        km._HEAD_CACHE.update(ts=0.0, full=None, short=None)
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = os.path.join(self.home, "romp")
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", HOME=self.home)
        # every git here rides the shared runner (T299): `git commit` spawns a detached `git maintenance run
        # --auto` that can still be writing into .git while the temp dir is removed (the CI flake "Directory
        # not empty: '.git'", tests/test_restart_classifier.py, 2026-09-10); the runner forbids that work on
        # every invocation, and init_repo writes the same keys into the repo the apply's own git runs against
        def g(*a, **kw):
            return git(self.repo, *a, env=env, **kw).stdout.strip()
        self.g = g
        os.makedirs(self.repo)
        init_repo(self.repo, "-q", "-b", "main", env=env)
        self.f = os.path.join(self.repo, "f")
        def commit(text, msg):
            with open(self.f, "w") as fh:
                fh.write(text)
            g("add", "f"); g("-c", "user.name=romp-test", "-c", "user.email=romp-test@TESTHOST", "commit", "-qm", msg)
            return g("rev-parse", "HEAD")
        self.A = commit("a\n", "A")            # where the peer sits
        self.B = commit("b\n", "B")            # this machine's head, a child of A
        g("reset", "-q", "--hard", self.A)
        self.C = commit("c\n", "C")            # another sender's build: also a child of A, not B
        g("reset", "-q", "--hard", self.A)
        stub = os.path.join(self.home, "ssh")
        with open(stub, "w") as fh:            # run the probe/apply locally, scrubbed env, fixture HOME
            fh.write('#!/usr/bin/env bash\nfor last in "$@"; do :; done\n'
                     'exec env -i HOME="%s" PATH="/usr/bin:/bin" ROMP_REPO_ROOT="%s" bash -c "$last"\n'
                     % (self.home, self.repo))
        os.chmod(stub, 0o755)
        km.SSH_BIN = stub
        km._remotes["TESTHOST"] = {"host": "TESTHOST"}

    def tearDown(self):
        km.subprocess.run, km.SSH_BIN = self._run, self._ssh
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        km._remotes.pop("TESTHOST", None)

    def _drive(self, lfull, landed=None, between=None):
        """Run _update_remote with the local head at `lfull`; the faked `git push` plants the scratch ref at
        `landed` (what actually arrived on the peer) and runs `between` (an event in the window between the
        probe and the apply). Returns (ok, detail, argv list)."""
        calls, real, g = [], self._run, self.g
        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "push" in argv:
                if landed:
                    g("update-ref", "refs/heads/" + km._P2P_REF, landed)
                if between:
                    between()
                return _R()
            if argv[0] == "git" and "rev-parse" in argv and "HEAD" in argv:
                return _R(out=lfull)
            return real(argv, **kw)                 # the ssh legs run for real through the stub
        km.subprocess.run = fake
        try:
            ok, detail = km._update_remote("TESTHOST")
        finally:
            km.subprocess.run = real                # the repo reads below must see the REAL git
        return ok, detail, calls

    def _scratch(self):
        r = git(self.repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + km._P2P_REF, check=False)
        return r.stdout.strip()

    def _sibling_push_at_the_apply(self, dirty=False):
        """Make the apply's own `git status` the moment another sender's push lands: the scratch ref moves
        from our B to their C (a child of A, not of B) AFTER the sha gate has passed on B, which is the one
        window the gate cannot see. Done with a `git` shim first on the apply shell's PATH that acts once,
        when armed, and otherwise hands straight to the real git; the probe's status runs before the push and
        so before the arming. `dirty` also lands an edit there, so the apply refuses. Returns the `between`
        hook that arms it."""
        import shlex
        real_git, shim_dir, marker = shutil.which("git"), os.path.join(self.home, "bin"), os.path.join(self.home, "armed")
        os.makedirs(shim_dir)
        with open(os.path.join(shim_dir, "git"), "w") as fh:
            fh.write('#!/usr/bin/env bash\n'
                     'if [ "$3" = status ] && [ -e %s ]; then rm -f %s; %s -C %s update-ref refs/heads/%s %s; %sfi\n'
                     'exec %s "$@"\n' % (shlex.quote(marker), shlex.quote(marker), shlex.quote(real_git),
                                         shlex.quote(self.repo), km._P2P_REF, self.C,
                                         ('printf "late edit\\n" >%s; ' % shlex.quote(self.f)) if dirty else "",
                                         shlex.quote(real_git)))
        os.chmod(os.path.join(shim_dir, "git"), 0o755)
        with open(km.SSH_BIN, "w") as fh:
            fh.write('#!/usr/bin/env bash\nfor last in "$@"; do :; done\n'
                     'exec env -i HOME="%s" PATH="%s:/usr/bin:/bin" ROMP_REPO_ROOT="%s" bash -c "$last"\n'
                     % (self.home, shim_dir, self.repo))
        return lambda: open(marker, "w").close()

    def test_the_fixture_repos_forbid_background_git_work(self):
        # the probe and the apply run git against this repo through the ssh stub, under `env -i` and the
        # kernel's own subprocess, so the no-background keys must sit in the repo's own config (T299)
        self.assertEqual(git(self.repo, "config", "--local", "--get", "maintenance.auto").stdout.strip(), "false")

    def test_an_edit_landing_after_the_probe_is_refused_and_survives(self):
        # the probe saw a clean tree; an edit lands before the apply; the apply must see it itself
        def edit():
            with open(self.f, "w") as fh:
                fh.write("late edit\n")
        ok, detail, calls = self._drive(self.B, landed=self.B, between=edit)
        self.assertFalse(ok, detail)
        self.assertIn("uncommitted changes", detail)
        self.assertIn("TESTHOST", detail)
        self.assertEqual(self.g("rev-parse", "HEAD"), self.A, "nothing was reset")
        self.assertEqual(open(self.f).read(), "late edit\n", "the late edit is intact")
        self.assertEqual(self._scratch(), "", "the scratch ref is cleaned up")
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"], "no restart is expected of it")

    def test_an_unstaged_edit_already_there_is_refused_at_the_probe(self):
        # the most common dirty state: an unstaged edit to a tracked file, whose porcelain line starts with
        # a SPACE (" M f"). The probe used to report the first character, the parser stripped it, and the
        # peer read as CLEAN: the push went ahead, and the apply's re-check then blamed the peer for an
        # edit that predated the probe.
        with open(self.f, "w") as fh:
            fh.write("edit before the probe\n")
        ok, detail, calls = self._drive(self.B, landed=self.B)
        self.assertFalse(ok, detail)
        self.assertIn("uncommitted changes", detail)
        self.assertFalse(any(a[0] == "git" and "push" in a for a in calls), "refused at the probe: nothing pushed")
        self.assertEqual(self.g("rev-parse", "HEAD"), self.A)
        self.assertEqual(open(self.f).read(), "edit before the probe\n")
        self.assertEqual(self._scratch(), "")

    def test_a_status_that_fails_right_before_the_apply_refuses_and_cleans_up(self):
        # the probe saw a healthy tree; by the apply its index is unreadable (a corruption; an index LOCK is
        # not this case, `git status` reads through one and the reset then fails as RESETFAIL). The
        # same-shell re-check answers STATERR: nothing is reset, the scratch ref is removed, no restart is
        # expected, and the row says the tree state could not be read — never the bare tag, never "synced"
        def corrupt():
            with open(os.path.join(self.repo, ".git", "index"), "w") as fh:
                fh.write("garbage")
        ok, detail, calls = self._drive(self.B, landed=self.B, between=corrupt)
        self.assertFalse(ok, detail)
        self.assertIn("TESTHOST", detail)
        self.assertIn("right before the apply", detail)
        self.assertIn("state is unknown", detail)
        self.assertEqual(self.g("rev-parse", "HEAD"), self.A, "nothing was reset")
        self.assertEqual(self._scratch(), "", "the scratch ref is cleaned up")
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"])

    def test_a_failing_status_on_the_peer_is_unknown_never_clean(self):
        # an index git cannot read: `git status` dies while `rev-parse HEAD` still answers, so the old
        # probe printed an empty (clean) DIRTY field and the push went ahead
        with open(os.path.join(self.repo, ".git", "index"), "w") as fh:
            fh.write("garbage")
        ok, detail, calls = self._drive(self.B, landed=self.B)
        self.assertFalse(ok, detail)
        self.assertIn("state is unknown", detail)
        self.assertIn("TESTHOST", detail)
        self.assertFalse(any(a[0] == "git" and "push" in a for a in calls), "nothing was pushed")
        self.assertEqual(self.g("rev-parse", "HEAD"), self.A)

    def test_the_apply_binds_to_the_advertised_commit_not_the_scratch_ref(self):
        # another sender force-pushed the scratch ref between our push and our apply: the ref names THEIR
        # build. The old apply reset to the ref and reported our sha as synced.
        ok, detail, calls = self._drive(self.B, landed=self.C)
        self.assertFalse(ok, detail)
        self.assertIn("another push moved it", detail)
        self.assertIn(self.B[:8], detail)
        self.assertIn(self.C[:8], detail)
        self.assertEqual(self.g("rev-parse", "HEAD"), self.A, "nothing was reset")
        self.assertEqual(self._scratch(), self.C, "the other sender's ref is left for its own apply")
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"])

    def test_a_sibling_senders_ref_landing_after_the_gate_survives_a_refusal(self):
        # the gate passed on our B; then another sender's push moved the scratch ref to their C and an edit
        # made the tree dirty. The refusal's cleanup used to delete the ref UNCONDITIONALLY: that sender's
        # apply then found nothing under the name and was told a phantom push had moved it. The delete is
        # guarded by our sha, so their ref survives, and the detail says it was left for them.
        arm = self._sibling_push_at_the_apply(dirty=True)
        ok, detail, _ = self._drive(self.B, landed=self.B, between=arm)
        self.assertFalse(ok, detail)
        self.assertIn("uncommitted changes", detail)
        self.assertIn("another sender", detail)
        self.assertIn("left alone", detail)
        self.assertEqual(self.g("rev-parse", "HEAD"), self.A, "nothing was reset")
        self.assertEqual(open(self.f).read(), "late edit\n")
        self.assertEqual(self._scratch(), self.C, "the other sender's ref is left for its own apply")
        self.assertNotIn("restartExpected", km._remotes["TESTHOST"])

    def test_a_sibling_senders_ref_landing_after_the_gate_survives_the_reset_too(self):
        # the same window on the success path: our reset to B goes ahead (the gate held, the tree is clean)
        # and the cleanup after it finds the ref at their C, so it stays; the outcome still names what it did
        arm = self._sibling_push_at_the_apply()
        ok, detail, _ = self._drive(self.B, landed=self.B, between=arm)
        self.assertFalse(ok)                                   # the fixture has no launcher: NOLAUNCH after the reset
        self.assertIn("launcher", detail)
        self.assertIn("another sender", detail)
        self.assertEqual(self.g("rev-parse", "HEAD"), self.B, "our reset went ahead")
        self.assertEqual(self._scratch(), self.C, "their ref survives our cleanup")

    def test_a_clean_apply_resets_to_exactly_the_advertised_commit(self):
        # the positive path of the same script: clean tree, ref at our sha → the checkout lands on it (the
        # fixture has no launcher, so the script stops at NOLAUNCH after the reset, before any restart)
        ok, detail, calls = self._drive(self.B, landed=self.B)
        self.assertFalse(ok)
        self.assertIn("launcher", detail)
        self.assertEqual(self.g("rev-parse", "HEAD"), self.B)
        self.assertEqual(self._scratch(), "", "the scratch ref is cleaned up after the reset")
        apply = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        self.assertIn("WANT=" + self.B, apply)
        self.assertIn('reset --hard "$WANT"', apply)
        self.assertNotIn("reset --hard " + km._P2P_REF, apply, "never the mutable scratch ref")
        self.assertNotIn("reset --hard %s" % km._P2P_REF, apply)
        # order inside the one shell: bind, ancestry, tree re-check, then the reset
        i = apply.index
        self.assertLess(i('rev-parse --verify --quiet refs/heads/' + km._P2P_REF), i("merge-base --is-ancestor"))
        self.assertLess(i("merge-base --is-ancestor"), i("status --porcelain"))
        self.assertLess(i("status --porcelain"), i('reset --hard "$WANT"'))
        self.assertLess(i("STATERR"), i("DIRTYNOW"), "a failed status refuses before the content is even looked at")


class UpdateListing(unittest.TestCase):
    """The listing the no-host `romp update` chooses hosts from (GET /tunnels?fresh=1) is judged against
    the head this checkout is at NOW; the dashboard's polls keep reading the 15 s cache (a poll must not
    fork git)."""
    STALE, FRESH = "3" * 40, "4" * 40

    def setUp(self):
        self._run, self._hc = km.subprocess.run, dict(km._HEAD_CACHE)
        km._BEHIND_CACHE.clear()
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801, "token": "t",
                                   "status": "up", "sids": [], "kernel_sha": self.STALE[:7]}   # on the OLD commit
        km._HEAD_CACHE.update(ts=9e18, full=self.STALE, short=self.STALE[:7])                    # what the polls last read
        fresh = self.FRESH
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv and "HEAD" in argv:
                return _R(out=fresh[:7] if "--short" in argv else fresh)                       # what git says NOW
            return _R(rc=1)
        km.subprocess.run = fake

    def tearDown(self):
        km.subprocess.run = self._run
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        km._BEHIND_CACHE.clear()
        km._remotes.pop("TESTHOST", None)

    def test_a_fresh_listing_is_judged_against_the_head_the_user_has(self):
        polled = next(t for t in km._tunnels_listing()["tunnels"] if t["host"] == "TESTHOST")
        self.assertFalse(polled["outOfDate"], "the polls' listing reads the cache")
        acted = next(t for t in km._tunnels_listing(fresh=True)["tunnels"] if t["host"] == "TESTHOST")
        self.assertTrue(acted["outOfDate"], "the listing a command acts on sees the commit made just now")
        self.assertEqual(acted["localSha"], self.FRESH[:7])

    def test_the_handler_serves_the_fresh_listing_for_the_flag_and_the_cached_one_without(self):
        # the same three requests the CLI and the dashboard make, through the real Handler on a loopback
        # server (the /tunnels tests' shape), so the wiring is exercised rather than pinned as source text
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("GET", path, headers={"X-Romp-Token": km.TOKEN})     # the serve token gates every route
            resp = c.getresponse()
            raw = resp.read()
            c.close()
            self.assertEqual(resp.status, 200, raw)
            return next(t for t in json.loads(raw.decode())["tunnels"] if t["host"] == "TESTHOST")
        self.assertFalse(get("/tunnels")["outOfDate"], "a poll reads the cache")
        self.assertFalse(get("/tunnels?fresh=0")["outOfDate"], "only the one spelling asks for a re-read")
        acted = get("/tunnels?fresh=1")
        self.assertTrue(acted["outOfDate"], "the listing `romp update` acts on sees the commit made just now")
        self.assertEqual(acted["localSha"], self.FRESH[:7])

    def test_the_route_and_the_cli_agree_on_the_flag(self):
        import inspect
        from urllib.parse import parse_qs
        self.assertIn('_tunnels_listing(fresh=_fresh_listing_asked(q))', inspect.getsource(km.Handler))
        self.assertIn('_get(u, "/tunnels?fresh=1")', open(os.path.join(BIN, "romp-update")).read())
        # the contract is the one spelling the CLI sends: parse_qs yields ["0"] for ?fresh=0, so a
        # truthiness test re-read HEAD for 0 and no while a blank ?fresh= did not
        self.assertTrue(km._fresh_listing_asked(parse_qs("fresh=1")))
        for query in ("fresh=0", "fresh=", "fresh=no", "", "other=1"):
            self.assertFalse(km._fresh_listing_asked(parse_qs(query)), query)
            row = next(t for t in km._tunnels_listing(fresh=km._fresh_listing_asked(parse_qs(query)))["tunnels"]
                       if t["host"] == "TESTHOST")
            self.assertFalse(row["outOfDate"], "%r must not re-read HEAD" % query)
        row = next(t for t in km._tunnels_listing(fresh=km._fresh_listing_asked(parse_qs("fresh=1")))["tunnels"]
                   if t["host"] == "TESTHOST")
        self.assertTrue(row["outOfDate"])


class UpdateEndpoint(unittest.TestCase):
    def test_post_tunnels_update_calls_update_remote_and_reports(self):
        import inspect
        src = inspect.getsource(km)
        self.assertIn('if u.path == "/tunnels/update":', src)
        self.assertIn("ok, detail = _update_remote(host)", src)
        self.assertIn('json.dumps({"ok": ok, "detail": detail})', src)
        # a failed update returns a non-2xx so the CLI/banner can tell (fail loudly)
        self.assertIn("200 if ok else 502", src)

    def test_supervisor_polls_the_remote_version(self):
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("_poll_remote_version(r)", src)
        self.assertIn('r["kernel_sha"] = rsha', src)


class UpdateUI(unittest.TestCase):
    def test_drift_banner_uses_the_update_framing(self):
        # mirrors the #rstale reload banner, but asks to bring the remote onto the local build (the user
        # 2026-07-04). ONE neutral word since 2026-07-28: the same button covers a push we run and an ask
        # a checked-in peer runs for itself, and what the user agrees to — that machine ends up on this
        # build — is identical either way.
        self.assertIn("id=rdrift", km._RDRIFT_HTML)
        self.assertIn(">Update<", km._RDRIFT_HTML, "the action button says Update")
        self.assertIn("Update it to this one?", km._RDRIFT_JS, "the prompt asks to bring the remote onto this build")
        self.assertIn("/tunnels/update", km._RDRIFT_JS)
        self.assertIn("outOfDate", km._RDRIFT_JS)
        self.assertIn("_rdrift_block()", inspect_src())

    def test_drift_banner_shows_live_progress_success_and_failure(self):
        # the user 2026-07-04: the banner must stay up through the work with a spinner + status, a success
        # confirmation, and a persistent actionable error — not silently flip back to the prompt.
        self.assertIn("rd-spin", km._RDRIFT_HTML)
        self.assertIn("romp-swirl-glyph.svg", km._RDRIFT_CSS)   # the spinner is the romp loader glyph
        self.assertIn("Updating ", km._RDRIFT_JS, "an 'updating…' progress message")
        self.assertIn("waiting for", km._RDRIFT_JS, "a 'waiting for it to restart' verify phase")
        self.assertIn("Up to date", km._RDRIFT_JS, "a success confirmation")
        self.assertIn("Update failed", km._RDRIFT_JS, "a persistent, specific failure message")
        self.assertIn("phase", km._RDRIFT_JS, "a state machine drives the flow")

    def test_popover_shows_behind_and_a_push_button(self):
        self.assertIn("behind", km._LANDING_REMOTES_JS)
        self.assertIn(">Push</button>", km._LANDING_REMOTES_JS)
        self.assertIn("/tunnels/update", km._LANDING_REMOTES_JS)
        self.assertIn("data-u=", km._LANDING_REMOTES_JS)


ru = load_source("romp_update", os.path.join(BIN, "romp-update"))


class RompUpdateCLI(unittest.TestCase):
    def setUp(self):
        self._k, self._g, self._p = ru._kernel, ru._get, ru._post
        ru._kernel = lambda: "http://127.0.0.1:29855"
        self.posted = []
        ru._post = lambda u, path, body: (self.posted.append((path, body)) or {"ok": True, "detail": "updated"})

    def tearDown(self):
        ru._kernel, ru._get, ru._post = self._k, self._g, self._p

    def test_dispatch_routes_update_in_the_bash_cli(self):
        # Dash-only since 2026-07-25: bare `update` names a session (the retired-word
        src = open(os.path.join(BIN, "romp")).read()
        # Round 3 (2026-07-25): commands are bare words again — `update` is the
        # spelling, and the retired `--update` flag fails naming it.
        self.assertIn('"${1:-}" == "update"', src, "bare `update` routes to romp-update")
        self.assertIn('--update)', src, "the retired --update spelling gets a loud hint")
        self.assertIn("exec romp-update", src)

    def test_no_kernel_errors_cleanly(self):
        ru._kernel = lambda: None
        self.assertEqual(ru.main([]), 2)

    def test_named_host_updates_that_remote(self):
        self.assertEqual(ru.main(["TESTHOST"]), 0)
        self.assertEqual(self.posted, [("/tunnels/update", {"host": "TESTHOST"})])

    def test_no_arg_updates_only_out_of_date_remotes(self):
        ru._get = lambda u, path: {"tunnels": [{"host": "TESTHOST", "outOfDate": True},
                                               {"host": "gpu1", "outOfDate": False}]}
        self.assertEqual(ru.main([]), 0)
        self.assertEqual(self.posted, [("/tunnels/update", {"host": "TESTHOST"})], "only the stale remote is updated")

    def test_no_arg_asks_for_a_listing_judged_against_the_current_head(self):
        # the hosts this command pushes to are chosen from the listing's outOfDate; a listing built from
        # the polls' 15 s head cache within 15 s of a local commit says "all up to date" and pushes nothing
        seen = []
        ru._get = lambda u, path: seen.append(path) or {"tunnels": [{"host": "TESTHOST", "outOfDate": True}]}
        self.assertEqual(ru.main([]), 0)
        self.assertEqual(seen, ["/tunnels?fresh=1"], "the kernel re-reads HEAD for the listing this command acts on")
        self.assertEqual(self.posted, [("/tunnels/update", {"host": "TESTHOST"})])

    def test_no_arg_all_current_updates_nothing(self):
        ru._get = lambda u, path: {"tunnels": [{"host": "gpu1", "outOfDate": False}]}
        self.assertEqual(ru.main([]), 0)
        self.assertEqual(self.posted, [], "nothing to do when every remote is current")

    def test_a_failed_update_returns_nonzero(self):
        ru._post = lambda u, path, body: {"ok": False, "detail": "git pull failed"}
        self.assertEqual(ru.main(["TESTHOST"]), 1)


def inspect_src():
    import inspect
    return inspect.getsource(km)


class BehindInfo(unittest.TestCase):
    """The popover's drift wording data (the user 2026-07-11: 'something more informative than just
    behind'): _behind_info measures HOW an out-of-date remote differs — commits behind, commits ahead
    (a push would clobber those, so 'behind' would be a lie), and the remote commit's date."""
    LOCAL_FULL = "abc1234000000000"
    REMOTE_FULL = "def5678000000000"

    def setUp(self):
        self._hc = dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=9e18, full=self.LOCAL_FULL, short="abc1234")
        km._BEHIND_CACHE.clear()
        self._run = km.subprocess.run

    def tearDown(self):
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        km._BEHIND_CACHE.clear()
        km.subprocess.run = self._run

    def _mock_git(self, behind="12", ahead="0", date="2026-07-08", known=True, calls=None):
        loc, rem = self.LOCAL_FULL, self.REMOTE_FULL

        def run(argv, **kw):
            if calls is not None:
                calls.append(list(argv))
            j = " ".join(argv)
            if "rev-parse" in j and "^{commit}" in j:
                return _R(out=rem + "\n") if known else _R(rc=1)
            if "rev-list" in j and (rem + ".." + loc) in j:
                return _R(out=behind + "\n")
            if "rev-list" in j and (loc + ".." + rem) in j:
                return _R(out=ahead + "\n")
            if "log" in j:
                return _R(out=date + "\n")
            return _R(rc=1)
        km.subprocess.run = run

    def test_behind_counts_and_the_remote_commits_date(self):
        self._mock_git(behind="12", ahead="0", date="2026-07-08")
        self.assertEqual(km._behind_info("def5678"),
                         {"behind": 12, "ahead": 0, "date": "2026-07-08"})

    def test_ahead_is_distinguished_from_behind(self):
        # the remote has its own commits (updated from another machine, or local was rolled back):
        # a push would CLOBBER them, so the row must not claim 'behind'
        self._mock_git(behind="0", ahead="3")
        info = km._behind_info("def5678")
        self.assertEqual((info["behind"], info["ahead"]), (0, 3))

    def test_unknown_sha_reports_none_not_a_guess(self):
        self._mock_git(known=False)
        self.assertEqual(km._behind_info("def5678"), {"behind": None, "ahead": None, "date": ""})

    def test_memoized_per_sha_pair(self):
        calls = []
        self._mock_git(calls=calls)
        km._behind_info("def5678")
        n = len(calls)
        self.assertGreater(n, 0)
        km._behind_info("def5678")
        self.assertEqual(len(calls), n, "the second read is served from the memo — git never re-runs")

    def test_remote_public_carries_the_drift_fields(self):
        self._mock_git(behind="12", ahead="0", date="2026-07-08")
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "status": "up", "kernel_sha": "def5678"})
        self.assertTrue(pub["outOfDate"])
        self.assertEqual((pub["behindBy"], pub["aheadBy"], pub["kernelDate"]), (12, 0, "2026-07-08"))

    def test_in_sync_remote_never_touches_git(self):
        def boom(argv, **kw):
            raise AssertionError("an in-sync row must not pay for drift measurement: %s" % argv)
        km.subprocess.run = boom
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "status": "up", "kernel_sha": "abc1234"})
        self.assertFalse(pub["outOfDate"])
        self.assertEqual((pub["behindBy"], pub["aheadBy"], pub["kernelDate"]), (0, 0, ""))


class DriftWordingUI(unittest.TestCase):
    """The popover row names HOW the remote differs, not just 'behind' (the user 2026-07-11)."""

    def test_row_names_how_the_remote_differs(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("down=bb>0?('behind '+bb):''", js)   # said in words since 2026-07-30
        self.assertIn("up=ab>0?('ahead '+ab):''", js)
        self.assertIn("'diverged: '", js)
        self.assertIn("'different build'", js, "an unknown sha says so instead of guessing")

    def test_a_buildless_connected_host_reads_unversioned_not_blank(self):
        # A connected host that reports NO build at all (a plain file copy, no git checkout) used to
        # show a bare "connected" — indistinguishable from healthy-and-in-sync, while running
        # arbitrarily old code drift detection cannot see (the user 2026-08-11, whose devbox sat
        # months behind beside a blank). The row says "unversioned copy" where the build word sits,
        # and the tooltip says why and what restores updates. Fail loudly, never a blank that reads
        # as fine.
        js = km._LANDING_REMOTES_JS
        self.assertIn("else if(t.status==='up'){ver=' \\u00b7 <span class=\"rnet-old\"", js)
        self.assertIn("unversioned copy", js)
        self.assertIn("Reinstall it as a git clone to restore the build name and updates.", js)
        # the VS Code strip's popover row carries the same word — the two surfaces must not drift
        strip = (pathlib.Path(__file__).resolve().parents[1] / "ui" / "webview" / "strip.ts").read_text()
        self.assertIn('ver = " · unversioned copy";', strip)
        self.assertIn("!t.kernelSha && !t.kernelVer", strip)

    def test_tooltip_carries_the_shas_and_date(self):
        js = km._LANDING_REMOTES_JS
        # since 2026-07-30 each side is named by RELEASE and commit together (buildWord), not the sha
        # alone — the tag is the one number both machines already agree on
        self.assertIn("running '+(buildWord(t.kernelVer,t.kernelSha)||'?')", js)
        self.assertIn("this machine is at '+(buildWord(t.localVer,t.localSha)||'?')", js)
        self.assertIn("t.kernelDate", js)

    def test_popover_js_parses(self):
        # the inline JS ships unparsed inside the kernel's HTML — a stray brace only surfaces
        # when the popover breaks in the browser; parse it the way the browser will
        node = shutil.which("node")
        if not node:
            self.skipTest("node unavailable")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(km._LANDING_REMOTES_JS)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)


class ApplyScriptRuns(unittest.TestCase):
    """The apply script RUN against a sandbox host: a scratch git clone at the pushed sha, a stub romp-serve,
    the state root under ROMP_STATE_DIR, and `pkill` shadowed by a no-op first on PATH, so a fall-through
    into the immediate path can kill nothing on the machine running the tests. What the text pins above
    cannot show: which audit rows each branch leaves on disk. The far manager is a stub that lists the
    polled port (owning) or nothing, notes whether the p2p audit row was already on disk when its status
    was read, and records the restart it is asked for. Synthetic host, port 1 (nothing answers)."""
    PORT = 1

    def setUp(self):
        self._run, self._hc = km.subprocess.run, dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=0.0, full=None, short=None)
        self.root = tempfile.mkdtemp()
        self.host = os.path.join(self.root, "romp")             # the far checkout
        self.state = os.path.join(self.root, "state")
        self.stubs = os.path.join(self.root, "stubs")
        for d in (os.path.join(self.host, "bin"), self.state, self.stubs):
            os.makedirs(d)
        self._git("init", "-q")
        self._git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "base")
        self._git("update-ref", "refs/heads/%s" % km._P2P_REF, "HEAD")
        self.sha = self._git("rev-parse", "--short", "HEAD").strip()
        self.full = self._git("rev-parse", "HEAD").strip()      # what the kernel's rev-parse fake answers (see _script)
        # the stubs below live INSIDE the checkout: excluded, so the apply's own dirtiness re-check (`git status
        # --porcelain`: an untracked file is a dirty tree and answers DIRTYNOW) reads it clean, as a real host's
        # bin/ is. Untracked and ignored, so `reset --hard` leaves them in place
        os.makedirs(os.path.join(self.host, ".git", "info"), exist_ok=True)
        with open(os.path.join(self.host, ".git", "info", "exclude"), "a") as fh:
            fh.write("/bin/\n")
        self._stub(os.path.join(self.host, "bin", "romp-serve"), "exit 0")
        self._stub(os.path.join(self.stubs, "pkill"), "exit 0")     # the immediate path's kill, made inert
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": self.PORT}
        self.addCleanup(km._remotes.pop, "TESTHOST", None)

    def tearDown(self):
        km.subprocess.run = self._run
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        shutil.rmtree(self.root, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.host] + list(args), check=True, capture_output=True, text=True).stdout

    def _stub(self, path, body):
        with open(path, "w") as fh:
            fh.write("#!/usr/bin/env bash\n" + body + "\n")
        os.chmod(path, 0o755)

    def _manager(self, owns):
        body = ('case "$1" in\n'
                'status) [ -f "$ROMP_STATE_DIR/restart-audit.jsonl" ] && grep -q \'"action": "p2p-update"\' "$ROMP_STATE_DIR/restart-audit.jsonl" '
                '&& echo row-on-disk >> "%s/manager-calls"; echo \'{"kernels": [%s]}\' ;;\n'
                '*) echo "$*" >> "%s/manager-calls" ;;\n'
                'esac' % (self.root, '{"port": %d}' % self.PORT if owns else "", self.root))
        self._stub(os.path.join(self.host, "bin", "romp-manager"), body)

    def _marker(self):
        with open(os.path.join(self.state, "down-by-romp"), "w") as fh:
            fh.write("{}\n")

    def _script(self):
        """The inner apply script the kernel would ssh to the host, taken off the mocked ssh call."""
        calls = []
        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "push" in argv:
                return _R()
            if argv[0] == "git" and "rev-parse" in argv:
                # the sandbox's own full HEAD: the apply's WANT is the local head the push sent, and its
                # first gate refuses with REFMISMATCH when the scratch ref on the host holds anything else.
                # The discover fake's HEAD below stays a different sha, so the host is not "already up to date"
                return _R(out=self.full)
            cmd = argv[-1]
            if "for d in" in cmd:
                return _R(out="DIR:%s\nHEAD:%s\nDIRTY:" % (self.host, "2" * 40))
            return _R(out="SYNCED:%s:MANAGED" % self.sha)
        km.subprocess.run = fake
        try:
            km._update_remote("TESTHOST")
        finally:
            km.subprocess.run = self._run
        wrapper = next(a[-1] for a in calls if isinstance(a[-1], str) and "reset --hard" in a[-1])
        return shlex.split(wrapper.split("; if command -v setsid")[0][len("APPLY="):])[0]

    def _apply(self):
        env = dict(os.environ, ROMP_STATE_DIR=self.state, PATH=self.stubs + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run(["bash", "-c", self._script()], capture_output=True, text=True, timeout=60, env=env, cwd=self.root)
        return r.stdout.strip(), r.stderr

    def _rows(self):
        p = os.path.join(self.state, "restart-audit.jsonl")
        if not os.path.exists(p):
            return []
        return [(r["action"], r.get("when")) for r in
                (json.loads(l) for l in open(p).read().splitlines() if l.strip())]

    def _calls(self):
        p = os.path.join(self.root, "manager-calls")
        return open(p).read().splitlines() if os.path.exists(p) else []

    def test_a_romp_down_host_with_no_manager_is_synced_and_leaves_no_row(self):
        self._marker()
        out, err = self._apply()
        self.assertEqual(out, "SYNCED:%s:DOWN" % self.sha, err)
        self.assertEqual(self._rows(), [], "no row for a restart nobody made")
        self.assertEqual(self._git("rev-parse", "--short", "HEAD").strip(), self.sha, "the code was synced")

    @unittest.skipUnless(shutil.which("node"), "the owning-manager check needs node on PATH")
    def test_a_live_host_has_the_audit_row_on_disk_when_the_owning_manager_check_runs(self):
        self._manager(owns=True)
        out, err = self._apply()
        self.assertEqual(out, "SYNCED:%s:MANAGED" % self.sha, err)
        self.assertEqual(self._rows(), [("p2p-update", None)], "an immediate request, no when=quiet")
        self.assertEqual(self._calls(), ["row-on-disk", "restart-all"],
                         "the row precedes the status call, and the restart goes through the manager at once")

    @unittest.skipUnless(shutil.which("node"), "the owning-manager check needs node on PATH")
    def test_a_manager_owning_the_kernel_beside_a_marker_gets_one_attributed_immediate_restart(self):
        self._marker()
        self._manager(owns=True)
        out, err = self._apply()
        self.assertEqual(out, "SYNCED:%s:MANAGED" % self.sha, err)
        self.assertEqual(self._rows(), [("p2p-update", None)], "exactly one row, written once the owning manager was found")
        self.assertEqual(self._calls(), ["restart-all"], "no row on disk yet at the status read")

    @unittest.skipUnless(shutil.which("node"), "the owning-manager check needs node on PATH")
    def test_a_marker_beside_a_manager_owning_nothing_leaves_no_row_and_restarts_nothing(self):
        self._marker()
        self._manager(owns=False)
        out, err = self._apply()
        self.assertEqual(out, "SYNCED:%s:DOWN" % self.sha, err)
        self.assertEqual(self._rows(), [])
        self.assertEqual(self._calls(), [], "status was read, nothing restarted, no row for it to note")


if __name__ == "__main__":
    unittest.main()
