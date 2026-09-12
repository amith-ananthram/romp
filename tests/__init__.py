"""unittest-side twin of conftest.py's state floor (2026-08-12): `python -m unittest tests.test_x`
imports this package first, so the floor lands before any test module loads bin/romp-* — which
resolve their state root at import time. conftest.py covers only pytest; without this, a bare
unittest run operated on the REAL ~/.local/state/romp (tests/test_kernel.py overwrote the real
remotes.json that way today). Does NOT cover `cd tests && python -m unittest test_x` or a direct
script run — the per-module preambles do, and tests/test_state_isolation_order.py enforces them."""
import atexit
import os
import shutil
import sys
import tempfile

# Temp-directory hygiene, the in-process half (2026-09-06): the suite used to leak every directory it
# made. This floor and conftest.py's each minted a state root per process and never removed it (two
# per pytest process: eighteen for a `pytest -n 8` run), and about three hundred test modules call
# tempfile.mkdtemp with no cleanup of their own. On a developer machine that ran the suite many times
# a day that left about 1.9 million directories under the system temp directory, enough that listing
# it took minutes and tens of gigabytes of memory, and the out-of-memory kills that followed took a
# running romp kernel down. The fix is at the source: every tempfile.mkdtemp call made in THIS
# process is recorded, and everything recorded is removed at interpreter exit (and, under pytest, at
# session end; see conftest.py). Only directories this process created, and only under the process
# temp dir, are ever removed: tempfile.gettempdir(), which is the system temp dir under a unittest
# run and conftest's private per-run root under pytest. tempfile.TemporaryDirectory goes through the
# same mkdtemp and cleans itself first, so the exit sweep finds nothing of it.
# What this hook cannot see is the other half, conftest.py's: directories made by CHILD processes
# (kernels, git, a shell's `mktemp -d`), files from mkstemp, and os.mkdir paths under the temp dir.
# Under pytest those land in conftest's root because TMPDIR points there, and the root is removed
# whole at run end; the two directories this process mints BEFORE that redirect (this package's
# state dir below, and the root itself, both recorded here but outside the redirected gettempdir()
# and so skipped by this sweep) are conftest's to remove. No such cover exists under a bare
# unittest run, where the per-module cleanups (addCleanup, tearDownClass) are what there is.
_MADE_DIRS = []
_REAL_MKDTEMP = tempfile.mkdtemp


def _tracked_mkdtemp(*a, **k):
    p = _REAL_MKDTEMP(*a, **k)
    _MADE_DIRS.append(p)
    return p


_tracked_mkdtemp.romp_tracked = True          # tests/test_tempdir_hygiene.py asserts the hook is installed
if not getattr(tempfile.mkdtemp, "romp_tracked", False):
    tempfile.mkdtemp = _tracked_mkdtemp


def remove_made_dirs():
    """Remove every directory this process minted through tempfile.mkdtemp. Idempotent; registered
    with atexit here and called again from conftest's pytest_sessionfinish."""
    root = os.path.realpath(tempfile.gettempdir())
    for p in reversed(list(_MADE_DIRS)):
        try:
            rp = os.path.realpath(p)
            if rp != root and rp.startswith(root + os.sep) and os.path.isdir(rp) and not os.path.islink(p):
                shutil.rmtree(rp, ignore_errors=True)
        except Exception:
            pass
    _MADE_DIRS.clear()


atexit.register(remove_made_dirs)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="romp-tests-state-")
# Recorded by the hook above, which removes it at exit under a unittest run. Under pytest it is
# minted BEFORE conftest.py redirects the temp root (the package imports first), so it sits beside
# the root in the system temp dir, outside the hook's redirected scope; conftest reads STATE_DIR
# and removes it with the root when the run ends. Nothing runs after an os._exit (pytest-timeout's
# thread method ends a hung run that way), so a hung run leaves this dir beside the root.
STATE_DIR = os.environ["XDG_STATE_HOME"]
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# No unittest run may reach a REAL manager or kernel either: the same three ports conftest.py poisons
# for pytest (its comment has the readers and the incident). Set to a dead port, never popped: every
# reader maps an absent variable to the default port, which on a machine running romp is the live one.
os.environ["ROMP_MANAGER_PORT"] = "1"
os.environ["ROMP_KERNEL_PORT"] = "1"
os.environ["ROMP_SERVE_PORT"] = "1"

# `from romp_load import load_source` in a test module (tests/romp_load.py): under pytest and
# `python -m unittest tests.test_x` the test modules are imported as members of this package, so the
# bare name resolves only because it is registered here; a direct script run finds the file on
# sys.path itself. One module object either way.
from . import romp_load as _romp_load  # noqa: E402
sys.modules.setdefault("romp_load", _romp_load)
# `from fs_clock import move_ctime` in a test module (tests/fs_clock.py, 2026-09-12): the shared force-until-moved ctime
# helper the memo tests and the thread mail-off test import, registered under its bare name like romp_load above
from . import fs_clock as _fs_clock  # noqa: E402
sys.modules.setdefault("fs_clock", _fs_clock)
# `from git_fixture import git, init_repo` (tests/git_fixture.py, T299): the throwaway-repo fixtures' one git
# runner, registered the same way for the same reason.
from . import git_fixture as _git_fixture  # noqa: E402
sys.modules.setdefault("git_fixture", _git_fixture)
