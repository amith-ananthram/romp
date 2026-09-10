"""The test suite's file-path importer: `from romp_load import load_source`.

A test module loads the code under test by path through the stable bin/ names
(`load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))`). The older form,
`SourceFileLoader(...).load_module()`, emits a DeprecationWarning on Python 3.10 and later and its
removal is documented for 3.15; the replacement is kernel/loadsource.py, and this module is how a
test reaches it without a package import. It is importable under every way the suite runs: pytest
and `python -m unittest tests.test_x` import the tests package first, whose __init__ registers this
module under the bare name; a direct `python tests/test_x.py` (or `cd tests && python -m unittest
test_x`) has this directory on sys.path already.

The semantics a test may lean on are load_module()'s, kept on purpose: a name already in
sys.modules is re-executed into the SAME module object, so a test that loads `romp_judge` and then
`romp_kernel` gets a kernel whose `jd` IS its own `jd` handle (and a `_rebind_state` on one is seen
by the other). A test that needs a private copy loads under a private name, as before.
"""
import importlib.util
import os

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "kernel", "loadsource.py")
_spec = importlib.util.spec_from_file_location("romp_loadsource", _SRC)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
load_source = _mod.load_source
