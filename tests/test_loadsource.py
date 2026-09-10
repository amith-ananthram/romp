#!/usr/bin/env python3
"""kernel/loadsource.py replaces SourceFileLoader.load_module() for the kernel's file-path imports.

load_module() emits a DeprecationWarning on Python 3.10 and later, and its removal is documented for
3.15. The first class here runs each kernel module's import in a fresh interpreter with that one
warning turned into an error: a kernel module (or one it loads at import) still calling load_module()
fails at the line that does, and the child's traceback names it. The second class pins the properties
of load_module() that the kernel and the tests rely on (registered before executing, the same object on
a second load under one name, no entry after a failed first load, the object a module put in sys.modules
in place of itself, a suffix-less path), against throwaway modules written to a temp dir under a private
name: nothing under test reads romp state.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
KERNEL = os.path.join(os.path.dirname(HERE), "kernel")
# Hermetic state BEFORE the loads: the ratchet counts every load_source call as a load. The modules
# loaded in-process here are throwaway, and the floor costs two lines.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

NAME = "romp_loadsource_probe"           # private: no other module loads under it

# Run in a child interpreter: import one file by path under a private name with load_module()'s own
# DeprecationWarning promoted to an error. The filter is narrow (the message, not the category), so a
# deprecation elsewhere in the kernel's imports cannot trip it; the child's own load goes through
# spec_from_file_location + exec_module, so only a load_module() call inside the loaded code can raise.
_CHILD = r"""
import importlib.util, sys, warnings
warnings.filterwarnings("error", message=r".*load_module\(\).*")
name, path = sys.argv[1:3]
spec = importlib.util.spec_from_file_location(name, path)
mod = importlib.util.module_from_spec(spec)
sys.modules[name] = mod
spec.loader.exec_module(mod)
print("loaded", mod.__name__)
"""


class KernelModulesLoadWithoutTheDeprecatedCall(unittest.TestCase):
    """Each kernel module that imports other files by path is loaded in its own fresh interpreter, so
    every one of its load sites runs (a module already in sys.modules would short-circuit the guarded
    ones) and nothing leaks into this process's shared modules."""

    def _load_in_a_fresh_interpreter(self, filename):
        with tempfile.TemporaryDirectory() as state:
            env = dict(os.environ)
            env["XDG_STATE_HOME"] = state          # the child resolves its state root at import
            env.pop("ROMP_STATE_DIR", None)
            proc = subprocess.run(
                [sys.executable, "-c", _CHILD, "romp_loadsource_probe_" + filename[:-3],
                 os.path.join(KERNEL, filename)],
                env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0,
            "kernel/%s (or a module it loads at import) still calls SourceFileLoader.load_module(), "
            "deprecated (its DeprecationWarning is emitted on Python 3.10 and later; removal documented "
            "for 3.15); the traceback names the site:\n%s" % (filename, proc.stderr))
        self.assertIn("loaded romp_loadsource_probe_" + filename[:-3], proc.stdout)

    def test_kernel_py_loads_under_the_load_module_warning_as_error(self):
        self._load_in_a_fresh_interpreter("kernel.py")

    def test_judge_py_loads_under_the_load_module_warning_as_error(self):
        self._load_in_a_fresh_interpreter("judge.py")

    def test_sdk_backend_py_loads_under_the_load_module_warning_as_error(self):
        self._load_in_a_fresh_interpreter("sdk_backend.py")

    def test_codex_backend_py_loads_under_the_load_module_warning_as_error(self):
        self._load_in_a_fresh_interpreter("codex_backend.py")


class LoadSource(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        sys.modules.pop(NAME, None)

    def tearDown(self):
        sys.modules.pop(NAME, None)
        self.td.cleanup()

    def _write(self, name, body):
        p = self.dir / name
        p.write_text(body)
        return p

    def test_a_fresh_load_is_registered_before_it_executes(self):
        p = self._write("a.py", "import sys\nSELF = sys.modules.get(__name__)\nVALUE = 1\n")
        mod = load_source(NAME, p)
        self.assertIs(mod.SELF, mod, "the module saw itself in sys.modules while its own body ran")
        self.assertIs(sys.modules[NAME], mod)
        self.assertEqual(mod.__name__, NAME)
        self.assertEqual(mod.__file__, str(p))
        self.assertEqual(mod.__spec__.origin, str(p))
        self.assertEqual(mod.VALUE, 1)

    def test_a_second_load_under_the_same_name_reexecutes_the_same_object(self):
        # kernel.py and judge.py both load romp_event_model, and a test module loads romp_judge before
        # the kernel does: the second load must land in the FIRST module object (km.jd is jd), with the
        # module's own attributes saying where its code now comes from
        a = self._write("a.py", "VALUE = 1\n")
        b = self._write("b.py", "VALUE = 2\n")
        first = load_source(NAME, a)
        second = load_source(NAME, b)
        self.assertIs(second, first)
        self.assertIs(sys.modules[NAME], first)
        self.assertEqual(first.VALUE, 2, "re-executed from the new path")
        self.assertEqual(first.__file__, str(b))
        self.assertEqual(first.__spec__.origin, str(b))
        self.assertIs(first.__loader__, first.__spec__.loader)

    def test_a_failing_first_load_leaves_no_entry(self):
        p = self._write("bad.py", "import sys\nassert __name__ in sys.modules\nraise RuntimeError('boom')\n")
        with self.assertRaises(RuntimeError):
            load_source(NAME, p)
        self.assertNotIn(NAME, sys.modules, "a half-built module is never left behind")

    def test_a_failing_reload_keeps_the_existing_module_registered(self):
        # load_module()'s behaviour, kept: the re-executed object stays what it was, in sys.modules
        good = self._write("good.py", "VALUE = 1\n")
        bad = self._write("bad.py", "raise RuntimeError('boom')\n")
        mod = load_source(NAME, good)
        with self.assertRaises(RuntimeError):
            load_source(NAME, bad)
        self.assertIs(sys.modules[NAME], mod)

    def test_a_module_that_replaces_itself_in_sys_modules_is_returned_as_the_replacement(self):
        # load_module()'s behaviour, kept: the caller gets whatever the name maps to once the body has
        # run, so a module that installs a stand-in for itself hands that stand-in to its importer
        p = self._write("a.py", "import sys, types\nsys.modules[__name__] = types.SimpleNamespace(VALUE=9)\n")
        mod = load_source(NAME, p)
        self.assertEqual(mod.VALUE, 9)
        self.assertIs(sys.modules[NAME], mod)

    def test_a_path_without_a_py_suffix_loads(self):
        # the bin/ names (romp-kernel, romp-judge) carry no suffix, which is why an explicit
        # SourceFileLoader is built rather than spec_from_file_location guessing one
        p = self._write("romp-probe", "VALUE = 3\n")
        self.assertEqual(load_source(NAME, p).VALUE, 3)


if __name__ == "__main__":
    unittest.main()
