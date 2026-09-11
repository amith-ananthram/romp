#!/usr/bin/env python3
"""Where romp's tmux server keeps its socket (T325, 2026-09-11).

tmux puts its default socket at ``$TMUX_TMPDIR/tmux-<uid>/default`` (``/tmp/tmux-<uid>/default`` with the variable
unset). On 2026-09-11 a tmpfs was mounted over a populated ``/tmp`` and ``/tmp/tmux-<uid>`` vanished under it, so
every tmux-backed session's terminal was unreachable for an hour while the CLIs inside kept running. The user's
RUNTIME directory (``XDG_RUNTIME_DIR``, ``/run/user/<uid>`` under systemd) is a per-user tmpfs that no ``/tmp``
housekeeping, tmpfiles age sweep or ``/tmp`` mount-over can touch, so romp's server lives there when there is one.

ONE rule, resolved the same way by the manager (before it starts the server, so every kernel inherits the value),
``bin/romp`` (``bin/romp-tmux-env``, before its first tmux call, so a plain shell's client dials the server the
manager started) and an UNMANAGED kernel (``romp-serve`` bare, a lab, a test), each naming which branch fired:

* ``operator``: a ``TMUX_TMPDIR`` already set wins, AS IT STANDS (no trimming: the three twins agree byte for byte);
* ``runtime-dir``: else ``XDG_RUNTIME_DIR`` naming an existing, writable directory gives ``<XDG_RUNTIME_DIR>/romp``,
  created 0700 when missing (tmux 3.4 falls back to ``/tmp`` SILENTLY when ``TMUX_TMPDIR`` names a missing directory,
  so the directory is made, never assumed; a mkdir lost to a sibling making it at the same moment is re-judged, not
  reported as a failure);
* else None, tmux's own default, as before, with the reason (``no XDG_RUNTIME_DIR``; the runtime dir not a writable
  directory; its ``romp`` subdirectory not makeable or not a writable directory).

A MANAGED kernel (``ROMP_MANAGER_PID`` set) does not resolve at all: the manager alone starts the server, so the
kernel takes the manager's ``TMUX_TMPDIR`` as it stands, absent meaning tmux's default (``manager``). A new-code
kernel respawned under a manager that predates this rule therefore dials the ``/tmp`` server that manager started,
instead of a runtime-dir socket nobody serves (which would have read every terminal session as dead and started a
second, unscoped server on its next spawn). A client already inside a pane uses the socket path in its own ``$TMUX``
and needs none of this. Pure over the environment it is handed; no tmux is run here. The node twin (``tmuxTmpdir``
in ``bin/romp-manager``) and the shell twin make the directory the same way.
"""
import os

ROMP_SUBDIR = "romp"

RULE_OPERATOR = "operator"
RULE_MANAGER = "manager"
RULE_RUNTIME = "runtime-dir"
RULE_NO_RUNTIME = "no XDG_RUNTIME_DIR"
RULE_RUNTIME_UNUSABLE = "XDG_RUNTIME_DIR is not a writable directory"
RULE_SUBDIR_UNUSABLE = "XDG_RUNTIME_DIR/romp could not be made, or is not a writable directory"


def _usable_dir(p):
    return bool(p) and os.path.isdir(p) and os.access(p, os.W_OK | os.X_OK)


def resolve(env=None, mkdir=True, managed=False):
    """(directory or None, rule): the TMUX_TMPDIR every romp tmux client and server should use, or None for tmux's
    own default, and which branch of the module docstring's rule decided it. `env` defaults to os.environ;
    `mkdir=False` only reports (nothing is made); `managed=True` is a kernel under the manager (see the docstring)."""
    env = os.environ if env is None else env
    op = env.get("TMUX_TMPDIR") or ""
    if managed:
        return (op or None), RULE_MANAGER
    if op:
        return op, RULE_OPERATOR
    run = env.get("XDG_RUNTIME_DIR") or ""
    if not run:
        return None, RULE_NO_RUNTIME
    if not _usable_dir(run):
        return None, RULE_RUNTIME_UNUSABLE
    d = os.path.join(run, ROMP_SUBDIR)
    if mkdir and not os.path.isdir(d):
        try:
            os.mkdir(d, 0o700)
        except OSError:
            pass                      # refused, or made by a sibling this instant: the re-judge below decides
    return (d, RULE_RUNTIME) if _usable_dir(d) else (None, RULE_SUBDIR_UNUSABLE)


def tmux_tmpdir(env=None, mkdir=True, managed=False):
    """resolve()'s directory alone."""
    return resolve(env, mkdir=mkdir, managed=managed)[0]


def export_tmux_tmpdir(env=None, managed=False):
    """Resolve into `env` (os.environ by default) so every tmux subprocess and every child inherits the value:
    written only when the runtime-dir rule fired (an operator's or the manager's value already stands in `env`, and
    tmux's default is the variable's absence). Returns (directory or None, rule)."""
    env = os.environ if env is None else env
    d, rule = resolve(env, managed=managed)
    if rule == RULE_RUNTIME and env.get("TMUX_TMPDIR") != d:
        env["TMUX_TMPDIR"] = d
    return d, rule


def describe(d, rule):
    """One log line's worth: the directory (or tmux's default) and the rule that chose it, for the next incident."""
    if rule == RULE_MANAGER:
        return "%s (the manager's environment, as it stands)" % (d or "tmux default")
    if rule == RULE_OPERATOR:
        return "%s (TMUX_TMPDIR set by the operator)" % d
    if rule == RULE_RUNTIME:
        return "%s (the user's runtime directory)" % d
    return "tmux default (%s)" % rule
