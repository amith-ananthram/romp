"""load_source(name, path): import a Python file by path under a chosen module name.

Everything under kernel/ is loaded by file path, not installed as a package (the repo runs from a
clone; bin/ holds symlinks with the stable names, and cli/ and postal/ run as scripts through them).
Every such load used to be `SourceFileLoader(name, path).load_module()`. `load_module()` has been
deprecated since Python 3.4, emits a DeprecationWarning on 3.10 and later, and its removal is
documented for 3.15, so this is the one replacement, built from the documented pieces
(`spec_from_loader`, `module_from_spec`, `exec_module`) and keeping what the kernel relied on in the
old call:

- The module is registered in `sys.modules[name]` before it executes, so a module that loads
  another under the same name during its own import gets the same object.
- A name that is ALREADY in `sys.modules` is re-executed into the existing module object rather
  than replaced. kernel.py, judge.py and every test module load `romp_event_model` and
  `romp_judge` by these fixed names, so `km.jd is jd` holds in a test that loaded both, and a
  test's `jd._rebind_state(...)` is seen by the kernel it then drives. The two places that must
  NOT share (sdk_backend's and codex_backend's key imports of session_backend, loaded as
  `romp_session_backend_keys`) already use a distinct name for exactly this reason.
- A failed first load leaves no half-built entry in `sys.modules`.

An explicit SourceFileLoader is required: the bin/ names carry no `.py` suffix, and
`spec_from_file_location` without a loader returns None for a path it cannot map to one.

Loaded by path itself (`spec_from_file_location`, `module_from_spec` and `exec_module` in each
module that needs it); tests reach it through tests/romp_load.py.
"""
import importlib.util
import sys
from importlib.machinery import SourceFileLoader


def load_source(name, path):
    """Execute the source file at `path` as module `name` and return the module (see the module
    docstring for the `sys.modules` semantics)."""
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = sys.modules.get(name)
    if mod is None:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    else:
        # the attributes load_module() re-stamped on a reused module (importlib's
        # _init_module_attrs with override=True), so a module re-executed from a differently
        # spelled path reports where its code now comes from
        mod.__spec__ = spec
        mod.__loader__ = loader
        mod.__file__ = spec.origin
        mod.__cached__ = spec.cached
        loader.exec_module(mod)
    return sys.modules.get(name, mod)
