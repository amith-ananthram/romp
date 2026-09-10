"""One git runner for every test fixture that builds a throwaway repository (T298, T299).

Every git the fixtures run forbids BACKGROUND work. `git commit` (and fetch, merge, rebase, am) spawns
`git maintenance run --auto`, which on recent git detaches from its parent (maintenance.autoDetach, on by
default; older git's `gc --auto` detached once it had work), so the child can still be writing into .git
while a fixture's TemporaryDirectory removes the repo: the CI flake "OSError: [Errno 39] Directory not
empty: '.git'" from rmtree (tests/test_restart_classifier.py, the Python 3.10 job, 2026-09-10). The keys
below ride every invocation as -c flags, and init_repo / forbid_background write them into the repo's own
config so a git that the KERNEL runs against the repo, through its own subprocess, obeys them too. The
fsmonitor daemon is off for the same reason on hosts where git has one.

The runner keeps each fixture's habits as parameters rather than choosing for them: the identity (every
file commits as its own synthetic author), the timeout, whether a failure raises, the environment (files
that pin git's config floor pass GIT_CONFIG_GLOBAL and friends themselves), and text or bytes output. It
returns the CompletedProcess; a file's local wrapper keeps whatever shape its call sites expect (a
stripped stdout, an AssertionError instead of CalledProcessError, nothing).
"""
import subprocess

GIT_NO_BACKGROUND = {"maintenance.auto": "false", "maintenance.autoDetach": "false", "gc.auto": "0",
                     "gc.autoDetach": "false", "core.fsmonitor": "false"}
GIT_C = [x for k, v in GIT_NO_BACKGROUND.items() for x in ("-c", "%s=%s" % (k, v))]


def _ident_pairs(ident):
    """ident: None, an (email, name) pair, or a mapping of git config keys to values."""
    if not ident:
        return []
    if isinstance(ident, dict):
        return list(ident.items())
    email, name = ident
    return [("user.email", email), ("user.name", name)]


def git(repo, *args, env=None, ident=None, timeout=60, check=True, text=True):
    """`git -C <repo> <no-background flags> [identity flags] <args>`, both streams captured. Returns the
    CompletedProcess; with check, a nonzero exit raises subprocess.CalledProcessError carrying the captured
    output, exactly as subprocess.run(check=True) would."""
    argv = ["git", "-C", str(repo)] + GIT_C + [x for k, v in _ident_pairs(ident) for x in ("-c", "%s=%s" % (k, v))]
    r = subprocess.run(argv + list(args), capture_output=True, text=text, env=env, timeout=timeout)
    if check:
        r.check_returncode()
    return r


def forbid_background(repo, env=None, timeout=60):
    """Write GIT_NO_BACKGROUND into an EXISTING repo's local config: for a clone or a worktree the fixture
    did not init itself but that the kernel will run git against. Returns repo."""
    for k, v in GIT_NO_BACKGROUND.items():
        git(repo, "config", k, v, env=env, timeout=timeout)
    return repo


def init_repo(path, *init_args, env=None, ident=None, timeout=60):
    """`git init <init_args>` at path (an existing directory), then forbid_background and, when an
    identity is given, write it into the repo's local config too. Returns path."""
    git(path, "init", *init_args, env=env, timeout=timeout)
    forbid_background(path, env=env, timeout=timeout)
    for k, v in _ident_pairs(ident):
        git(path, "config", k, v, env=env, timeout=timeout)
    return path
