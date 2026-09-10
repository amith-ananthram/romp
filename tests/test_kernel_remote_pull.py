#!/usr/bin/env python3
"""Peer-to-peer PULL — the other direction of `romp update` (the user 2026-07-27): sync rides the
attaching side's own ssh in BOTH directions, because the remote has no route back (its push died with
"No route to host"). `POST /tunnels/pull` / _pull_remote fetches the remote clone's committed HEAD
and fast-forwards the local checkout onto it — ff-only, clean local tree required, kernel never
auto-restarted. The automatic variant fires from the supervisor only for a TRUSTED remote strictly
ahead while the local checkout sits on main.

SYNTHETIC fixtures only — invented hosts and placeholder shas; subprocess/ssh fully stubbed."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_pull", os.path.join(BIN, "romp-kernel"))

LOCAL = "a" * 40                     # local HEAD (full sha)
REMOTE = "b" * 40                    # the remote's newer commit


class _R:
    def __init__(self, out="", err="", rc=0):
        self.stdout, self.stderr, self.returncode = out, err, rc


class FastPullGate(unittest.TestCase):
    """_is_fast_pull mirrors _is_fast_forward: it must say no to anything it cannot prove is a strict
    fast-forward of the LOCAL checkout."""

    def _fp(self, behind, ahead, ood=True):
        saved = (km._remote_out_of_date, km._behind_info)
        km._remote_out_of_date = lambda r, head=None: ood
        km._behind_info = lambda sha, head=None: {"behind": behind, "ahead": ahead, "date": ""}
        try:
            return km._is_fast_pull({"host": "TESTHOST", "kernel_sha": REMOTE})
        finally:
            km._remote_out_of_date, km._behind_info = saved

    def test_strictly_ahead_is_a_fast_pull(self):
        self.assertTrue(self._fp(behind=0, ahead=3), "the remote purely extends us — pulling only adds")

    def test_everything_else_is_not(self):
        self.assertFalse(self._fp(behind=0, ahead=0, ood=False), "up to date")
        self.assertFalse(self._fp(behind=2, ahead=0), "behind is the PUSH direction")
        self.assertFalse(self._fp(behind=1, ahead=1), "diverged: never automatic")
        self.assertFalse(self._fp(behind=None, ahead=None), "an unknown build is unprovable — no")
        self.assertFalse(self._fp(behind=0, ahead=None), "half-known is still unproven")


class PullRemote(unittest.TestCase):
    """_pull_remote drives git fetch + merge --ff-only via a dispatching subprocess mock."""

    def setUp(self):
        self._run, self._hc = km.subprocess.run, dict(km._HEAD_CACHE)
        km._HEAD_CACHE.update(ts=9e18, full=LOCAL, short=LOCAL[:8])
        km._remotes.clear()

    def tearDown(self):
        km.subprocess.run = self._run
        km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(self._hc)
        km._remotes.clear()

    def _wire(self, dirty="", rhead=REMOTE, ancestor_rc=0, merge_rc=0, count="3", status_rc=0, present=True,
              head=LOCAL):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            if argv[0] == "git" and "status" in argv:
                return _R(out=dirty, rc=status_rc, err="fatal: not a git repository" if status_rc else "")
            if argv[0] == "git" and "fetch" in argv:
                return _R()
            if argv[0] == "git" and "merge-base" in argv:
                return _R(rc=ancestor_rc)
            if argv[0] == "git" and "rev-list" in argv:
                return _R(out=count)
            if argv[0] == "git" and "merge" in argv:
                return _R(rc=merge_rc, err="not a fast-forward" if merge_rc else "")
            if argv[0] == "git" and "rev-parse" in argv and any(str(x).endswith("^{commit}") for x in argv):
                return _R(out=rhead) if present else _R(rc=1)   # is the reported commit here after the fetch?
            if argv[0] == "git" and "rev-parse" in argv and "--short" in argv:
                return _R(out="bbbbbbb")
            if argv[0] == "git" and "rev-parse" in argv:
                return _R(out=head)                     # this checkout's HEAD, read by the pull itself
            cmd = argv[-1]                          # ssh: the clone discovery
            if "for d in" in cmd:
                return _R(out="DIR:/home/u/romp\nHEAD:%s\nDIRTY:" % rhead)
            return _R()
        km.subprocess.run = fake
        return calls

    def test_no_host_is_a_no_op(self):
        self.assertEqual(km._pull_remote(""), (False, "no host"))

    def test_a_checked_in_host_is_refused_with_the_honest_reason(self):
        # This is the observed bug: a sync attempted at a host with no ssh route died with a bare
        # "No route to host". The row knows there is no path — say so, and say where to sync instead.
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "checkin_peer": True}
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("no ssh path", detail)
        self.assertIn("dashboard", detail)

    def test_push_refuses_a_checked_in_host_the_same_way(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "checkin_peer": True}
        ok, detail = km._update_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("no ssh path", detail)

    def test_a_dirty_local_tree_is_refused(self):
        # the fast-forward rewrites the working tree, and peers' uncommitted edits are not ours to move
        self._wire(dirty=" M kernel/kernel.py")
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("uncommitted", detail)

    def test_a_failed_status_is_unknown_never_clean(self):
        # rc 128 with nothing on stdout read as a CLEAN tree before: the fetch and the fast-forward
        # then ran over a tree whose state nobody had read. Unknown refuses, and names the failure.
        calls = self._wire(status_rc=128)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("tree state failed", detail)
        self.assertIn("not a git repository", detail, "the refusal carries git's own words")
        self.assertFalse(any(a[0] == "git" and ("fetch" in a or "merge" in a) for a in calls),
                         "nothing fetched or moved past the refusal")

    def test_a_clean_fast_forward_pulls_and_says_what_to_do_next(self):
        calls = self._wire()
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertIn("pulled 3 commits from TESTHOST", detail)
        self.assertIn("restart romp", detail, "the kernel is NOT auto-restarted; the detail says the next step")
        fetch = next(a for a in calls if a[0] == "git" and "fetch" in a)
        self.assertIn("TESTHOST:/home/u/romp", fetch, "fetches from the discovered clone over our own ssh")
        merge = next(a for a in calls if a[0] == "git" and "merge" in a)
        self.assertIn("--ff-only", merge, "never a merge or rebase on the user's behalf")

    def test_divergence_is_refused_loudly(self):
        self._wire(ancestor_rc=1)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("diverged", detail)

    def test_already_up_to_date_short_circuits(self):
        self._wire(rhead=LOCAL)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok)
        self.assertIn("already up to date", detail)

    def test_the_merge_binds_to_the_commit_the_peer_reported_never_FETCH_HEAD(self):
        # FETCH_HEAD is a mutable name: any other fetch into this checkout rewrites it between our fetch
        # and our merge. Every step after the fetch names the exact sha the peer reported, and FETCH_HEAD
        # enters no decision at all.
        calls = self._wire()
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok, detail)
        for step in ("merge-base", "rev-list", "merge"):
            argv = next(a for a in calls if a[0] == "git" and step in a)
            self.assertTrue(any(REMOTE in str(x) for x in argv), "%s binds to the sha: %r" % (step, argv))
        self.assertFalse(any("FETCH_HEAD" in str(x) for a in calls for x in a), "FETCH_HEAD is never consulted")
        verify = next(a for a in calls if a[0] == "git" and "rev-parse" in a and "--verify" in a)
        self.assertIn(REMOTE + "^{commit}", verify, "the fetch must have brought the reported commit itself")

    def test_a_peer_that_moved_off_the_commit_it_reported_is_refused(self):
        # the fetch did not bring the commit the peer reported (it rewound or was rewritten since the
        # probe): the one honest refusal, naming the peer and that commit — a LOCAL fetch that rewrote
        # FETCH_HEAD in the meantime can no longer be blamed on it
        calls = self._wire(present=False)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("moved off", detail)
        self.assertIn(REMOTE[:8], detail)
        self.assertIn("nothing merged", detail)
        self.assertFalse(any(a[0] == "git" and "merge" in a for a in calls), "no merge of an absent commit")

    def test_a_local_move_since_the_last_poll_does_not_hide_a_pull(self):
        # the polls' cache says this tree is at the peer's commit; git says it was moved back since (a
        # reset within the 15 s window). The cached gate answered "already up to date" and skipped a pull
        # that would have moved the tree; the pull reads the head this tree is AT.
        km._HEAD_CACHE.update(ts=9e18, full=REMOTE, short=REMOTE[:8])
        self._wire(rhead=REMOTE, head=LOCAL)
        ok, detail = km._pull_remote("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertNotIn("already up to date", detail)
        self.assertIn("pulled 3 commits from TESTHOST", detail)

    def test_a_peer_that_reports_no_commit_is_not_pulled(self):
        # with no reported commit there is nothing to bind the merge to — refuse before fetching
        calls = self._wire(rhead="")
        ok, detail = km._pull_remote("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("did not report", detail)
        self.assertFalse(any(a[0] == "git" and "fetch" in a for a in calls), "nothing fetched")


class AutoPullFiring(unittest.TestCase):
    """The supervisor hook fires the pull only for a TRUSTED remote strictly ahead, local checkout on
    main — and picks the pull worker, not the push one."""

    def setUp(self):
        self._prev = km._auto_update_remotes_on()
        km._set_auto_update_remotes(True)
        km._auto_push.clear()
        km._auto_push_tried.clear()
        self._saved = (km._remote_out_of_date, km._behind_info, km._local_head, km._local_branch)
        km._remote_out_of_date = lambda r, head=None: True
        km._behind_info = lambda sha, head=None: {"behind": 0, "ahead": 2, "date": ""}   # strictly ahead → pull side
        km._local_head = lambda short=False: (LOCAL[:8] if short else LOCAL)
        km._local_branch = lambda: "main"
        self.calls = []

    def tearDown(self):
        (km._remote_out_of_date, km._behind_info, km._local_head, km._local_branch) = self._saved
        km._set_auto_update_remotes(self._prev)
        km._auto_push.clear()
        km._auto_push_tried.clear()

    def _run(self, row):
        saved = km.threading.Thread
        calls = self.calls

        class _T:
            def __init__(self, target=None, args=(), daemon=None):
                self._t, self._a = target, args

            def start(self):
                calls.append((self._t.__name__, self._a[0]))
        km.threading.Thread = _T
        try:
            km._maybe_auto_push(row)
        finally:
            km.threading.Thread = saved

    def test_a_trusted_ahead_remote_fires_one_pull(self):
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        self.assertEqual(self.calls, [("_auto_pull_remote", "TESTHOST")])

    def test_a_directed_remote_is_never_auto_pulled(self):
        # pulling runs THEIR code here; that is exactly what the trusted tier means and directed does not
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "directed"})
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE})
        self.assertEqual(self.calls, [])

    def test_off_main_is_never_auto_pulled(self):
        # feature branches are mid-thought; they move when their owner says so
        km._local_branch = lambda: "somefeature"
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        self.assertEqual(self.calls, [])
        km._local_branch = lambda: ""              # detached HEAD (a release checkout)
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        self.assertEqual(self.calls, [])

    def test_a_checked_in_row_never_fires_this_machine_s_ssh(self):
        # no ssh route from here — a push or pull of OURS could only manufacture "No route to host"
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted", "checkin_peer": True})
        self.assertEqual(self.calls, [], "strictly ahead + no route: nothing this machine can run")

    def test_a_checked_in_peer_that_is_BEHIND_is_asked_to_update_itself(self):
        # the third direction (the user 2026-07-28): the peer owns the only ssh between the machines, so
        # the fast-forward is driven through the tunnel IT holds instead of offered as an impossible push
        km._behind_info = lambda sha, head=None: {"behind": 2, "ahead": 0, "date": ""}
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted", "checkin_peer": True})
        self.assertEqual(self.calls, [("_auto_ask_peer", "TESTHOST")])

    def test_a_checked_in_peer_is_never_driven_on_an_unproven_relationship(self):
        # same bar as the push gate: diverged, or a build this repo has never seen, is not driven at all
        for drift in ({"behind": 2, "ahead": 1, "date": ""}, {"behind": None, "ahead": None, "date": ""}):
            km._auto_push_tried.clear()
            km._behind_info = lambda sha, head=None, d=drift: d
            self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted", "checkin_peer": True})
        self.assertEqual(self.calls, [])

    def test_the_same_advance_is_not_pulled_twice(self):
        row = {"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"}
        self._run(row)
        self._run(row)
        self.assertEqual(len(self.calls), 1, "one attempt per (remote sha, local HEAD)")

    def test_a_pulled_phase_survives_the_drift_clearing(self):
        # after a pull the drift clears at once (HEAD moved) but the RUNNING kernel is the old build —
        # the 'pulled … restart romp' trace must outlive the clearing event, until the restart itself
        km._set_auto_push("TESTHOST", "pulled", "pulled 2 commits from TESTHOST — restart romp to run it")
        km._remote_out_of_date = lambda r, head=None: False
        self._run({"host": "TESTHOST", "kernel_sha": REMOTE, "trust": "trusted"})
        st = km._auto_push_state("TESTHOST")
        self.assertEqual((st or {}).get("phase"), "pulled")

    def test_the_row_publishes_the_fast_pull_verdict(self):
        pub = km._remote_public({"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                 "token": "t", "status": "up", "sids": [], "kernel_sha": REMOTE})
        self.assertTrue(pub["fastPull"])
        self.assertFalse(pub["fastForward"])


class PullRoute(unittest.TestCase):
    def test_the_popover_wires_a_pull_button_to_the_route(self):
        src = km._LANDING_REMOTES_JS
        self.assertIn("data-p=", src, "a Pull control keyed by host")
        self.assertIn("/tunnels/pull", src)
        self.assertIn("t.fastPull", src, "offered exactly when the pull is a provable fast-forward")
        self.assertIn("checkinPeer", src, "no Push/Pull dead-ends on a host with no ssh path")


if __name__ == "__main__":
    unittest.main()
