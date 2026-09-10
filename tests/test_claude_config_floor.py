#!/usr/bin/env python3
"""The suite-wide CLAUDE_CONFIG_DIR floor (tests/conftest.py, 2026-09-08): no test resolves the REAL
~/.claude. The judge module and the event model compute their projects root at IMPORT from
CLAUDE_CONFIG_DIR (default ~/.claude), so a test that touched a per-session project dir without
patching jd.PROJECTS wrote synthetic-sid directories under the developer's real ~/.claude/projects
(thirty of them, one night). conftest now points CLAUDE_CONFIG_DIR at a fresh directory inside the
run's private temp root before any test module loads, and re-asserts it before every test, so a
module-level pop or write in one test file cannot erase the floor for the rest of the run; a test
that needs its own Claude root sets the variable in setUp, which runs after the fixture, as before.
The location the run was handed is saved first, under ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR, for the one
opt-in test that borrows the operator's apiKeyHelper command from their own settings
(tests/test_session_move_live.py); captured after the floor it would name the empty floor dir.

Source pins in the style of test_supervised_floor.py, the floor observed from inside a test, from a
module loaded under it, and from a subprocess whose shell exports a value of its own; the saved
location observed from inside a test, from a subprocess whose shell exports the root it saves, from
one whose shell exports no root and is handed the settings dir under a synthetic HOME, and from one
whose shell already carries a saved value."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
# Hermetic state BEFORE any romp code loads (the repo-wide rule the state-isolation meta-test enforces).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"

# Run by a child pytest under conftest (`-p tests.conftest`) whose shell exports synthetic Claude roots,
# or a synthetic HOME and no root: one generated test, which passes only if the child's saved value is
# the one the case expects of that shell and CLAUDE_CONFIG_DIR is the floor, which is neither the saved
# value nor anything the shell exported.
SAVED_LOCATION_CASE = '''\
import os


def test_the_saved_location_is_the_expected_one_and_the_floor_is_neither_it_nor_anything_exported():
    expected, exported = %r, %r
    assert os.environ["ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR"] == expected
    assert os.environ["CLAUDE_CONFIG_DIR"] != expected
    assert os.environ["CLAUDE_CONFIG_DIR"] not in exported
'''


def _under_the_run_root(path):
    root = os.path.realpath(os.environ["TMPDIR"])
    return os.path.realpath(str(path)).startswith(root + os.sep)


class ClaudeConfigFloor(unittest.TestCase):
    def test_conftest_sets_the_variable_at_import_and_per_test(self):
        src = open(os.path.join(HERE, "conftest.py")).read()
        head, _, body = src.partition("@pytest.fixture(autouse=True)\ndef _no_real_claude_config")
        self.assertTrue(body, "the fixture header moved - re-anchor this pin")
        self.assertIn('os.environ["CLAUDE_CONFIG_DIR"] = _CLAUDE_CONFIG', head, "the import-time floor")
        self.assertIn('os.environ["CLAUDE_CONFIG_DIR"] = _CLAUDE_CONFIG', body, "the per-test re-assert")
        self.assertIn('tempfile.mkdtemp(prefix="romp-tests-claude-")', head, "a fresh dir inside the run root")

    def test_the_floor_holds_inside_a_test(self):
        cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        self.assertTrue(cfg, "CLAUDE_CONFIG_DIR is set for every test")
        self.assertTrue(_under_the_run_root(cfg), "…to a directory inside the run's private temp root")
        self.assertFalse(os.path.realpath(cfg).startswith(os.path.realpath(str(Path.home() / ".claude"))),
                         "never the real ~/.claude")

    def test_a_module_loaded_under_the_floor_resolves_its_projects_root_inside_it(self):
        # the judge computes PROJECTS at import: a test that imports it and writes a project dir lands
        # inside the floor, never under the real ~/.claude/projects (the incident's shape)
        jd = load_source("romp_judge_claude_cfg_floor", os.path.join(ROOT, "bin", "romp-judge"))
        self.assertEqual(Path(jd.PROJECTS), Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
        self.assertTrue(_under_the_run_root(jd.PROJECTS), str(jd.PROJECTS))
        d = jd._proj_dir("/TESTDIR/notes-api")
        self.assertTrue(_under_the_run_root(d), str(d))

    def test_the_floor_overrides_a_value_the_shell_exports(self):
        """A subprocess whose environment carries its own CLAUDE_CONFIG_DIR (a developer pointing their
        Claude at a custom root) runs only this module's behavioural checks, which pass only if
        conftest's floor replaced that value rather than defaulting to it."""
        env = dict(os.environ, CLAUDE_CONFIG_DIR=os.path.join(tempfile.gettempdir(), "synthetic-claude-root-not-a-floor"))
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            os.path.join(HERE, "test_claude_config_floor.py") + "::ClaudeConfigFloor::test_the_floor_holds_inside_a_test",
                            os.path.join(HERE, "test_claude_config_floor.py") + "::ClaudeConfigFloor::test_a_module_loaded_under_the_floor_resolves_its_projects_root_inside_it"],
                           env=env, capture_output=True, text=True, timeout=180, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-400:])
        self.assertNotIn("synthetic-claude-root-not-a-floor", r.stdout)

    def test_the_location_the_run_was_handed_is_saved_for_the_live_move_test(self):
        """tests/test_session_move_live.py borrows the operator's apiKeyHelper command from their own
        settings, which the floor hides from every test; conftest saves the location the run was handed
        under ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR before the floor replaces it. Observed from inside a test:
        set, and never the floor. Not asserted: which path it is, since a nested pytest run whose shell
        carries the outer run's value keeps it (the floor-override case in this module runs that way;
        the last two cases here pin the keeping)."""
        saved = os.environ.get("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR")
        self.assertTrue(saved, "the pre-floor Claude settings dir is saved for every test")
        floor = os.path.realpath(os.environ["CLAUDE_CONFIG_DIR"])
        self.assertNotEqual(os.path.realpath(saved), floor, "the saved location is not the floor")
        self.assertFalse(os.path.realpath(saved).startswith(floor + os.sep), "and not inside it")

    def _saved_location_seen_by_a_child(self, shell, expected, extra=()):
        """Runs SAVED_LOCATION_CASE under a child pytest that loads conftest as a plugin. `shell` is
        what the child's shell exports on top of this process's environment (a None value clears that
        variable from the child's shell instead), with the saved value cleared unless `shell` carries
        one; `expected` is the saved value the generated test asserts; `extra` is appended to the
        child's command line."""
        exported = tuple(value for value in shell.values() if value is not None)
        case = tempfile.mkdtemp(prefix="romp-claude-floor-case-")
        self.addCleanup(shutil.rmtree, case, ignore_errors=True)
        with open(os.path.join(case, "test_saved_location.py"), "w") as f:
            f.write(SAVED_LOCATION_CASE % (expected, exported))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for var in ("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER",
                    "PYTEST_XDIST_WORKER_COUNT"):
            env.pop(var, None)
        for var, value in shell.items():
            if value is None:
                env.pop(var, None)
            else:
                env[var] = value
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest",
                            *extra, os.path.join(case, "test_saved_location.py")],
                           env=env, capture_output=True, text=True, timeout=180, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-400:])
        self.assertIn("1 passed", r.stdout)

    def test_the_saved_location_is_the_root_the_shell_exported_before_the_floor(self):
        """The save must come BEFORE the floor: a child pytest whose shell exports a synthetic Claude root
        and carries no saved value of its own passes the generated test only if the saved value is that
        root while CLAUDE_CONFIG_DIR is the floor. Captured after the floor, the saved value would be the
        floor itself, and the live move test would skip for want of auth on every machine."""
        handed = os.path.join(tempfile.gettempdir(), "synthetic-claude-root-handed-to-the-run")
        self._saved_location_seen_by_a_child({"CLAUDE_CONFIG_DIR": handed}, expected=handed)

    def test_the_saved_location_is_the_home_settings_dir_when_the_shell_exports_no_root(self):
        """A shell that exports no CLAUDE_CONFIG_DIR at all (a developer's, and CI's) is handed the
        default settings dir, .claude under HOME, and the save must record that one: a child pytest
        whose shell carries no root and a synthetic HOME passes the generated test only if the saved
        value is that home's .claude and CLAUDE_CONFIG_DIR is neither that value nor the home. A conftest
        that floored HOME ahead of the save, or saved after the CLAUDE_CONFIG_DIR floor, would record a
        run-private directory instead, and the live move test would skip for want of auth on every
        machine that never exported the variable; one that never floored a shell exporting no root
        would leave CLAUDE_CONFIG_DIR at the saved value."""
        home = tempfile.mkdtemp(prefix="romp-claude-floor-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self._saved_location_seen_by_a_child({"CLAUDE_CONFIG_DIR": None, "HOME": home},
                                             expected=os.path.join(home, ".claude"))

    def test_a_saved_value_the_shell_already_carries_survives_the_import(self):
        """An xdist worker imports conftest in an environment the controller has already floored and
        stamped with its saved value; the worker must keep that value (the save is a setdefault) rather
        than record CLAUDE_CONFIG_DIR, by then the controller's empty floor, as the location the run was
        handed. A child pytest whose shell carries a saved value and a different CLAUDE_CONFIG_DIR passes
        the generated test only if the saved value is the one it was handed; a plain assignment would
        record the other root."""
        preset = os.path.join(tempfile.gettempdir(), "synthetic-claude-root-saved-by-the-controller")
        other = os.path.join(tempfile.gettempdir(), "synthetic-claude-root-floored-by-the-controller")
        self._saved_location_seen_by_a_child(
            {"ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR": preset, "CLAUDE_CONFIG_DIR": other}, expected=preset)

    @unittest.skipUnless(importlib.util.find_spec("xdist"), "pytest-xdist not installed")
    def test_an_xdist_worker_keeps_the_saved_location_the_controller_recorded(self):
        """The case above, run for real: the child under -n 2 imports conftest once in the controller
        and again in a worker whose environment the controller has already floored and stamped, and the
        generated test, run in the worker, still sees the root the shell handed the controller."""
        handed = os.path.join(tempfile.gettempdir(), "synthetic-claude-root-handed-to-the-run")
        self._saved_location_seen_by_a_child({"CLAUDE_CONFIG_DIR": handed}, expected=handed, extra=("-n", "2"))


if __name__ == "__main__":
    unittest.main()
