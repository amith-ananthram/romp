#!/usr/bin/env python3
"""T216 — deploy restarts get RARER: the no-restart classifier reads per-file classes (not
per-tree prefixes) so tests-only, docs-only, cli-only, postal-only, and PROVABLY-comment-only
diffs inside the kernel trees converge in place, and postal-only changes bounce the BUS (its own
process — a kernel restart converges the wrong thing). The standing constraint is HARD and
pinned here from both directions: anything unprovable restarts (mixed diffs, docstring edits,
new/renamed files, git failures), every skip verdict writes an audit row naming its files, and
the restart path pays the bundle rebuild BEFORE the old kernel dies.

Every verdict below is driven through a REAL throwaway git repo — the exact diff shapes the
dispatch names — never a mocked file list. Synthetic content only; hermetic state."""
import json
import os
import tempfile
import unittest
from git_fixture import GIT_C, GIT_NO_BACKGROUND, git, init_repo
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ["ROMP_MANAGER_PORT"] = "1"
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_rclass", os.path.join(BIN, "romp-kernel"))


# T298/T299: every git the fixture runs forbids BACKGROUND work, through the suite's shared runner
# (tests/git_fixture.py, which explains why: `git commit` spawns `git maintenance run --auto`, which on recent
# git detaches and can still be writing into .git while tearDownClass removes the temp repo — the CI flake
# "Directory not empty: '.git'" from TemporaryDirectory.cleanup's rmtree, the Python 3.10 job, 2026-09-10).
# init_repo writes the same keys into the repo's own config, so a git the KERNEL runs against it obeys them too.
def _git(repo, *args, env=None):
    r = git(repo, *args, env=env, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class RealDiffShapes(unittest.TestCase):
    """The dispatch's diff shapes, verbatim, against a real repo."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        repo = Path(cls.td.name)
        cls.repo = repo
        for sub in ("kernel", "bin", "postal", "cli", "docs", "tests", "ui/webview"):
            (repo / sub).mkdir(parents=True)
        init_repo(repo, "-q")   # T298: the no-background keys land in the repo too, for any git run against it
        _git(repo, "config", "user.email", "t@TESTHOST")
        _git(repo, "config", "user.name", "t")
        (repo / "kernel/mod.py").write_text('def f():\n    """doc."""\n    return 1  # one\n')
        (repo / "bin/tool").write_text("#!/usr/bin/env python3\n# a tool\nx = 1\n")
        (repo / "postal/postal_service.py").write_text("BUS = 1\n")
        (repo / "cli/version.py").write_text("V = 1\n")
        (repo / "docs/a.md").write_text("# docs\n")
        (repo / "kernel/README.md").write_text("# kernel docs\n")
        (repo / "tests/test_x.py").write_text("def test_x():\n    assert True\n")
        (repo / "ui/webview/feed.ts").write_text("export const x = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        cls.base = _git(repo, "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def _commit(self, msg):
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", msg)
        return _git(self.repo, "rev-parse", "HEAD")

    def _verdict(self, a, b):
        saved = km.ROOT
        km.ROOT = self.repo
        try:
            return km._kernel_code_changed(a, b), km._converge_classes(a, b)
        finally:
            km.ROOT = saved

    def _reset(self):
        # detach back to base so each shape's commit hangs off the SAME parent — a paths-only
        # checkout leaks files other shapes committed (tracked adds survive it)
        _git(self.repo, "checkout", "-qf", "--detach", self.base)

    def test_comment_only_kernel_file_skips(self):
        self._reset()
        (self.repo / "kernel/mod.py").write_text('def f():\n    """doc."""\n    return 1  # reworded\n')
        sha = self._commit("comment only")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed, "a comment-only kernel .py compiles identically — no bounce")
        self.assertEqual(cc["ast_equal"], ["kernel/mod.py"], "the audit names WHY it skipped")

    def test_tests_only_skips(self):
        self._reset()
        (self.repo / "tests/test_x.py").write_text("def test_x():\n    assert 1 + 1 == 2\n")
        sha = self._commit("tests only")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed)
        self.assertEqual(cc["skip"], ["tests/test_x.py"])

    def test_docs_only_skips_even_inside_kernel_tree(self):
        self._reset()
        (self.repo / "docs/a.md").write_text("# docs v2\n")
        (self.repo / "kernel/README.md").write_text("# kernel docs v2\n")
        sha = self._commit("docs only")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed)
        self.assertEqual(sorted(cc["skip"]), ["docs/a.md", "kernel/README.md"])

    def test_postal_only_is_bus_class_not_kernel(self):
        self._reset()
        (self.repo / "postal/postal_service.py").write_text("BUS = 2\n")
        sha = self._commit("postal only")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed, "the bus is its own process — a kernel bounce converges the wrong thing")
        self.assertEqual(cc["bus"], ["postal/postal_service.py"])

    def test_the_bus_entry_script_is_bus_class(self):
        self._reset()
        (self.repo / "bin/romp-postal-service").write_text("#!/usr/bin/env python3\nSERVE = 1\n")
        sha = self._commit("bus entry")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed)
        self.assertEqual(cc["bus"], ["bin/romp-postal-service"])

    def test_cli_only_skips(self):
        self._reset()
        (self.repo / "cli/version.py").write_text("V = 2\n")
        sha = self._commit("cli only")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed, "cli/ loads per `romp` invocation — the next run picks it up")
        self.assertEqual(cc["skip"], ["cli/version.py"])

    def test_mixed_diff_must_restart(self):
        self._reset()
        (self.repo / "tests/test_x.py").write_text("def test_x():\n    assert 2 + 2 == 4\n")
        (self.repo / "kernel/mod.py").write_text('def f():\n    """doc."""\n    return 2  # one\n')
        sha = self._commit("mixed")
        changed, cc = self._verdict(self.base, sha)
        self.assertTrue(changed, "any kernel-code file in the diff wins — mixed MUST restart")
        self.assertEqual(cc["kernel"], ["kernel/mod.py"])

    def test_docstring_change_conservatively_restarts(self):
        self._reset()
        (self.repo / "kernel/mod.py").write_text('def f():\n    """doc v2."""\n    return 1  # one\n')
        sha = self._commit("docstring")
        changed, _ = self._verdict(self.base, sha)
        self.assertTrue(changed, "docstrings live in the AST — not provably comment-only")

    def test_new_kernel_file_restarts(self):
        self._reset()
        (self.repo / "kernel/newmod.py").write_text("Y = 1\n")
        sha = self._commit("new module")
        changed, _ = self._verdict(self.base, sha)
        self.assertTrue(changed, "an added file has no old blob to prove equality against")

    def test_bin_script_comment_only_skips(self):
        self._reset()
        (self.repo / "bin/tool").write_text("#!/usr/bin/env python3\n# a better-described tool\nx = 1\n")
        sha = self._commit("bin comment")
        changed, cc = self._verdict(self.base, sha)
        self.assertFalse(changed, "shebang scripts parse as python — comment edits prove equal")
        self.assertEqual(cc["ast_equal"], ["bin/tool"])

    def test_renamed_kernel_file_restarts(self):
        self._reset()
        _git(self.repo, "mv", "kernel/mod.py", "kernel/mod2.py")
        sha = self._commit("rename")
        changed, _ = self._verdict(self.base, sha)
        self.assertTrue(changed, "a rename is never provably equal — restart")

    def test_non_ascii_kernel_path_still_restarts(self):
        # git's default core.quotePath C-quotes non-ASCII paths ('"kernel/m\303\263dulo.py"') —
        # the quoted form defeated the prefix match and a REAL kernel change wrong-skipped
        # (T216 review, reproduced). The -z listing carries raw paths.
        self._reset()
        (self.repo / "kernel/módulo.py").write_text("X = 1\n")
        base2 = self._commit("non-ascii module lands")
        (self.repo / "kernel/módulo.py").write_text("X = 2\n")
        sha = self._commit("non-ascii module changes")
        changed, cc = self._verdict(base2, sha)
        self.assertTrue(changed, "a quoted path must never fall through to skip")
        self.assertEqual(cc["kernel"], ["kernel/módulo.py"])

    def test_kernel_file_moved_out_of_tree_still_restarts(self):
        # rename detection collapses a move to its DESTINATION path — a kernel file moved out
        # of kernel/ vanished from the listing and the diff wrong-skipped (T216 review,
        # reproduced). --no-renames keeps the old path visible as a delete.
        self._reset()
        _git(self.repo, "mv", "kernel/mod.py", "ui/webview/mod.py")
        sha = self._commit("moved out")
        changed, cc = self._verdict(self.base, sha)
        self.assertTrue(changed, "the running kernel LOSES a module it loaded — restart")
        self.assertIn("kernel/mod.py", cc["kernel"])

    def test_the_fixture_forbids_background_git_work(self):
        # T298: the repo's config and every helper invocation forbid auto maintenance, gc and its detach, and
        # the fsmonitor daemon, so no git child outlives the command that spawned it (see GIT_NO_BACKGROUND).
        # Pinned at the source and by behaviour: git's own trace names every child it runs, and a commit
        # through the helper spawns none.
        for k, v in GIT_NO_BACKGROUND.items():
            # --default: a missing key reaches this assertion and its message, not the runner's exit check
            self.assertEqual(_git(self.repo, "config", "--local", "--get", "--default", "", k), v, "the repo's config carries " + k)
            self.assertIn("%s=%s" % (k, v), GIT_C, "…and so does every runner invocation")
        self._reset()
        (self.repo / "docs/a.md").write_text("# docs, traced\n")
        with tempfile.NamedTemporaryFile("r", suffix=".log") as trace:
            env = dict(os.environ, GIT_TRACE=trace.name)
            _git(self.repo, "add", "-A", env=env)
            _git(self.repo, "commit", "-qm", "maintenance-traced", env=env)
            t = trace.read()
        self.assertIn("built-in: git commit", t, "the trace is on")
        self.assertIn("maintenance-traced", t, "the trace names the commit's argv: the pin below cannot be a bare substring")
        self.assertNotIn("run_command: git maintenance", t, "no auto-maintenance child is spawned:\n" + t)
        self.assertNotIn("run_command: git gc", t, "no gc child is spawned")

    def test_unknown_shas_and_git_failure_restart(self):
        self.assertTrue(km._kernel_code_changed("", "abc"), "unknown shas: restart")
        changed, cc = self._verdict("0" * 40, "1" * 40)
        self.assertTrue(changed, "git failure: restart")
        self.assertIsNone(cc)


class ConvergeArms(unittest.TestCase):
    """The drift-check skip arm: bus bounce for bus-class files, audit rows for every skip."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_main_drift_verdict", "_kernel_code_changed", "_converge_classes",
                        "_rebuild_dist", "_bus_converge", "_sync_notice", "_update_mode",
                        "_send_to_app", "_kernel_sha", "_main_tracking", "_audit_restart_request",
                        "_origin_main_sha", "_checkout_sha")}
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._REBUILT_FOR[0] = ""
        km._INPLACE_TRIED[0] = ""
        self.notices, self.banners, self.audits, self.bus = [], [], [], []
        km._sync_notice = lambda msg, ok=True: self.notices.append((msg, ok))
        km._send_to_app = lambda app, payload: self.banners.append(payload)
        km._audit_restart_request = lambda action, **kw: self.audits.append((action, kw))
        km._update_mode = lambda: "ask"
        km._kernel_sha = lambda: "cur-sha"
        km._origin_main_sha = lambda: "tgt"     # stubbed: the real one runs `git ls-remote` —
        km._checkout_sha = lambda: "tgt"        # a NETWORK call per test (T216 review)
        km._main_tracking = lambda: True

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._REBUILT_FOR[0] = ""
        km._INPLACE_TRIED[0] = ""

    def test_bus_only_drift_bounces_the_bus_not_the_kernel(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-bus")
        km._kernel_code_changed = lambda a, b: False
        km._converge_classes = lambda a, b: {"kernel": [], "bus": ["postal/postal_service.py"],
                                             "skip": [], "ast_equal": []}
        km._bus_converge = lambda: (self.bus.append(1), (True, ""))[1]
        km._rebuild_dist = lambda: (True, "")
        km._main_drift_check()
        self.assertEqual(len(self.bus), 1, "the bus bounced")
        self.assertEqual(self.banners, [], "no kernel restart banner")
        self.assertEqual(km._REBUILT_FOR[0], "tgt-bus")
        self.assertIn("bus-converge", [a for a, _ in self.audits])
        self.assertTrue(any("bus" in m for m, _ in self.notices))

    def test_a_broken_dist_build_never_becomes_a_converge_storm(self):
        # ONE full in-place attempt per target (the _dist_converge_check precedent): a broken
        # esbuild must not re-bounce the bus, re-classify, and re-build on every 300s pass
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-storm")
        km._kernel_code_changed = lambda a, b: False
        km._converge_classes = lambda a, b: {"kernel": [], "bus": ["postal/postal_service.py"],
                                             "skip": [], "ast_equal": []}
        km._bus_converge = lambda: (self.bus.append(1), (True, ""))[1]
        self.builds = []
        km._rebuild_dist = lambda: (self.builds.append(1), (False, "esbuild broken"))[1]
        km._main_drift_check()
        km._MAIN_DRIFT[1] = ""          # let the next pass re-enter the arm (banner already sent)
        km._main_drift_check()
        self.assertEqual(len(self.bus), 1, "one bounce per target, however many passes")
        self.assertEqual(len(self.builds), 1, "one build attempt per target, however many passes")

    def test_an_unreadable_second_classification_falls_to_the_restart_path(self):
        # the verdict's classify succeeded; the converge's re-read flaked — an empty mask would
        # silently drop the bus bounce and write a lying audit row. Unprovable restarts.
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-flake")
        km._kernel_code_changed = lambda a, b: False
        km._converge_classes = lambda a, b: None
        km._bus_converge = lambda: self.fail("no classes — nothing may act")
        km._rebuild_dist = lambda: self.fail("no classes — nothing may act")
        km._main_drift_check()
        self.assertEqual(km._REBUILT_FOR[0], "", "never latched")
        self.assertEqual([b.get("drift") for b in self.banners], ["restart"],
                         "the restart offer is the safe converge")

    def test_a_failed_bus_bounce_is_loud_but_never_forces_a_kernel_restart(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-bus2")
        km._kernel_code_changed = lambda a, b: False
        km._converge_classes = lambda a, b: {"kernel": [], "bus": ["postal/postal_service.py"],
                                             "skip": [], "ast_equal": []}
        km._bus_converge = lambda: (False, "port held")
        km._rebuild_dist = lambda: (True, "")
        km._main_drift_check()
        self.assertEqual(self.banners, [], "a kernel restart cannot fix the bus — never offered")
        self.assertTrue(any(not ok and "port held" in m for m, ok in self.notices),
                        "the failure is loud")

    def test_every_skip_writes_a_diagnosable_audit_row(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-skip")
        km._kernel_code_changed = lambda a, b: False
        km._converge_classes = lambda a, b: {"kernel": [], "bus": [],
                                             "skip": ["tests/test_x.py", "docs/a.md"],
                                             "ast_equal": ["kernel/mod.py"]}
        km._rebuild_dist = lambda: (True, "")
        km._main_drift_check()
        acts = dict((a, kw) for a, kw in self.audits)
        self.assertIn("main-converge-skip", acts)
        row = acts["main-converge-skip"]
        self.assertEqual(row["tag"], "tgt-skip")
        self.assertIn("tests/test_x.py", row["skip"])
        self.assertIn("kernel/mod.py", row["ast_equal"], "a wrong skip is diagnosable from disk")


class PreDeathRebuild(unittest.TestCase):
    """The restart path pays esbuild BEFORE the old kernel dies — never inside the outage."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_rebuild_dist", "_sync_notice", "_audit_restart_request",
                        "_kernel_code_changed", "_checkout_sha")}
        km._checkout_sha = lambda: "other-sha"
        self.order, self.notices = [], []
        km._sync_notice = lambda msg, ok=True: self.notices.append((msg, ok))
        km._audit_restart_request = lambda action, **kw: None
        km._kernel_code_changed = lambda a, b: True

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)

    def _run(self, rebuild_ok):
        from unittest.mock import patch
        km._rebuild_dist = lambda: (self.order.append("rebuild"), (rebuild_ok, "boom"))[1]

        order = self.order

        class FakeConn:
            # the restart POST dials http.client the way _restart_this_kernel does — never urllib,
            # whose default opener honours HTTP_PROXY and diverted the loopback POST under a proxy
            def __init__(self, host, port, timeout=0):
                pass

            def request(self, method, path, *a, **k):
                order.append("post")

            def getresponse(self):
                class R:
                    status = 200

                    def read(self):
                        return b""
                return R()

            def close(self):
                pass
        with patch.object(km.http.client, "HTTPConnection", FakeConn):
            km._run_main_update("restart", immediate=True, manager_port="1")

    def test_the_rebuild_lands_before_the_restart_post(self):
        self._run(rebuild_ok=True)
        self.assertEqual(self.order, ["rebuild", "post"],
                         "the new kernel must find fresh bundles, not pay esbuild in the outage")

    def test_a_failed_rebuild_never_blocks_the_restart(self):
        self._run(rebuild_ok=False)
        self.assertEqual(self.order, ["rebuild", "post"], "proceed — _ensure_bundles is the backstop")
        self.assertTrue(any(not ok and "boom" in m for m, ok in self.notices), "loudly")

    def test_a_just_failed_in_place_build_is_not_paid_twice(self):
        # the in-place converge already paid (and failed) this exact esbuild — a doomed 180s
        # re-run before the POST would only stretch the outage
        km._INPLACE_TRIED[0] = "cur-checkout"
        km._checkout_sha = lambda: "cur-checkout"
        try:
            self._run(rebuild_ok=True)
            self.assertEqual(self.order, ["post"], "straight to the restart — no second build")
        finally:
            km._INPLACE_TRIED[0] = ""


if __name__ == "__main__":
    unittest.main()
