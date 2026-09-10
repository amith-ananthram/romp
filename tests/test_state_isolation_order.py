#!/usr/bin/env python3
"""Every test module that loads romp code must make its state root hermetic FIRST (2026-08-12).

bin/romp-kernel, bin/romp-judge, bin/romp-event-model, bin/romp-postal-service and the modules
they pull in resolve STATE = ROMP_STATE_DIR || XDG_STATE_HOME/romp || ~/.local/state/romp at
IMPORT time (and romp-judge mkdirs it). tests/conftest.py points XDG_STATE_HOME at a temp dir,
but conftest is pytest-only: under `python -m unittest` (or a direct `./tests/test_x.py` run)
nothing sets the floor, and the module operates on the REAL ~/.local/state/romp. On 2026-08-12
exactly that happened: a unittest run of tests/test_kernel.py persisted its synthetic check-in
fixtures (TESTHOST, hubhost) into the real remotes.json / remotes-known.json — dropping a real
attached host's row — and the live kernel re-read the file at its next restart and began
ssh-dialing the fixtures. tests/__init__.py now gives unittest package runs the same floor
conftest gives pytest, but neither covers `cd tests && python -m unittest test_x` or a direct
script run, so the per-module preamble is the primary defence and this test is the ratchet.

The rule this file enforces, per tests/test_*.py module: if the module loads romp code (any
load_source or SourceFileLoader call, or an import of the kernel/postal/cli packages), then BEFORE
the first such load, at module top level, it must (a) assign os.environ["XDG_STATE_HOME"] (or
["ROMP_STATE_DIR"]) and (b) handle ROMP_STATE_DIR (assign it, or pop it — a live kernel exports
it to its sessions, and it outranks the XDG floor). The canonical preamble:

    # Hermetic state BEFORE the loads — they resolve their state root at import time, and only
    # pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
    os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
    os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

Static AST scan, ordered by line number; every in-process load call counts as state-touching —
load_source (tests/romp_load.py), SourceFileLoader and the importlib idioms
(spec_from_file_location/exec_module/import_module/__import__) alike, since most bin/* files are or
transitively load a STATE-resolving module, and
the few that aren't pay two harmless lines rather than this test resolving targets. Out of scope by design: a subprocess
spawned with a hand-built env= dict that carries the real HOME — env construction is dynamic and
defeats static checking; the preamble covers the common case because a child spawned without
env= inherits the mutated os.environ.
"""
import ast
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))

# Any entry must justify itself here: a module that genuinely needs the real state root (none
# known — reading real state from a test was always a bug) or a false positive worth documenting.
EXEMPT: set[str] = set()

PREAMBLE = (
    '    os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()\n'
    '    os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel\'s export outranks the XDG floor'
)

ROOT_PACKAGES = {"kernel", "postal", "cli"}
# Every in-process load form counts, not just the repo's usual load_source and SourceFileLoader: the
# spec_from_file_location + exec_module idiom (tests/test_colormap.py) aimed at a STATE-resolving
# bin file would recreate the corruption with the ratchet silent otherwise.
LOAD_CALLS = {"load_source", "SourceFileLoader", "spec_from_file_location", "exec_module",
              "import_module", "__import__"}


def _is_environ_attr(node):
    return isinstance(node, ast.Attribute) and node.attr == "environ"


def _environ_key(node):
    """The constant key of an `<x>.environ[...]` subscript, else None."""
    if isinstance(node, ast.Subscript) and _is_environ_attr(node.value):
        if isinstance(node.slice, ast.Constant):
            return node.slice.value
    return None


def scan(path):
    """Return (first_load, first_set, rsd_handled) linenos for one module (None where absent)."""
    tree = ast.parse(open(path).read(), filename=path)
    first_load = first_set = rsd_handled = None

    def keep_min(cur, lineno):
        return lineno if cur is None or lineno < cur else cur

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in LOAD_CALLS:
                first_load = keep_min(first_load, node.lineno)
        elif isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in ROOT_PACKAGES for a in node.names):
                first_load = keep_min(first_load, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] in ROOT_PACKAGES:
                first_load = keep_min(first_load, node.lineno)

    for stmt in tree.body:                       # top level only: a set inside a function does not
        if isinstance(stmt, ast.Assign):         # run before an import-time load
            keys = {_environ_key(t) for t in stmt.targets}
            if keys & {"XDG_STATE_HOME", "ROMP_STATE_DIR"}:
                first_set = keep_min(first_set, stmt.lineno)
            if "ROMP_STATE_DIR" in keys:
                rsd_handled = keep_min(rsd_handled, stmt.lineno)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            f = stmt.value.func
            if (isinstance(f, ast.Attribute) and f.attr == "pop" and _is_environ_attr(f.value)
                    and stmt.value.args and isinstance(stmt.value.args[0], ast.Constant)
                    and stmt.value.args[0].value == "ROMP_STATE_DIR"):
                rsd_handled = keep_min(rsd_handled, stmt.lineno)
    return first_load, first_set, rsd_handled


class StateIsolationOrder(unittest.TestCase):
    def test_every_loading_module_isolates_state_first(self):
        bad = []
        for fn in sorted(os.listdir(HERE)):
            if not (fn.startswith("test_") and fn.endswith(".py")) or fn in EXEMPT:
                continue
            first_load, first_set, rsd_handled = scan(os.path.join(HERE, fn))
            if first_load is None:
                continue
            if first_set is None or first_set >= first_load:
                bad.append("%s: loads romp code at line %d with no prior state-root assignment"
                           % (fn, first_load))
            elif rsd_handled is None or rsd_handled >= first_load:
                bad.append("%s: sets the XDG floor (line %d) but never handles ROMP_STATE_DIR "
                           "before the load at line %d" % (fn, first_set, first_load))
        self.assertFalse(bad,
            "These modules load romp code before making the state root hermetic — under a bare\n"
            "unittest or script run they operate on the REAL ~/.local/state/romp (which is how\n"
            "tests/test_kernel.py overwrote the real remotes.json on 2026-08-12). Put this at\n"
            "module top level, above the first load_source or SourceFileLoader line:\n\n%s\n\n%s"
            % (PREAMBLE, "\n".join(bad)))

    def test_scan_counts_a_load_source_call_as_a_load(self):
        # tests/romp_load.py's load_source is a load like the others: a module that calls it before
        # the floor must be caught, or the ratchet is blind to every module written that way
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test_probe.py")
            with open(path, "w") as f:
                f.write('import os\n'
                        'from romp_load import load_source\n'
                        'km = load_source("romp_kernel_probe", "bin/romp-kernel")\n'
                        'os.environ["XDG_STATE_HOME"] = "too late"\n')
            first_load, first_set, rsd_handled = scan(path)
        self.assertEqual(first_load, 3, "the load_source call is the module's first load")
        self.assertEqual(first_set, 4)
        self.assertIsNone(rsd_handled)

    def test_the_suite_wide_floors_stay_in_place(self):
        # The suspenders: conftest.py (pytest) and __init__.py (unittest package runs) each set the
        # XDG floor and drop an inherited ROMP_STATE_DIR override. Pin them so neither is silently
        # deleted or loses the pop.
        for fn in ("conftest.py", "__init__.py"):
            src = open(os.path.join(HERE, fn)).read()
            self.assertIn('os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp', src,
                          "%s must keep the temp XDG_STATE_HOME floor" % fn)
            self.assertIn('os.environ.pop("ROMP_STATE_DIR", None)', src,
                          "%s must keep dropping an inherited ROMP_STATE_DIR override" % fn)


if __name__ == "__main__":
    unittest.main()
