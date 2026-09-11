#!/usr/bin/env python3
"""Where romp's tmux server keeps its socket (T325, 2026-09-11).

tmux puts its default socket at ``$TMUX_TMPDIR/tmux-<uid>/default`` (``/tmp/tmux-<uid>/default`` with the variable
unset). On 2026-09-11 a tmpfs was mounted over a populated ``/tmp`` and ``/tmp/tmux-<uid>`` vanished under it, so
every tmux-backed session's terminal was unreachable for an hour while the CLIs inside kept running. The user's
RUNTIME directory (``XDG_RUNTIME_DIR``, ``/run/user/<uid>`` under systemd) is a per-user tmpfs that no ``/tmp``
housekeeping, tmpfiles age sweep or ``/tmp`` mount-over can touch, so romp's server lives there when there is one.

ONE rule, resolved the same way by the manager (before it starts the server, so every kernel inherits the value),
the kernel (here, before its first tmux call, so every dial and every ``romp new -t`` it spawns agree) and ``bin/romp``
(``bin/romp-tmux-env``, before its first tmux call, so a plain shell's client dials the server the manager started):

* a ``TMUX_TMPDIR`` the operator set wins, as it stands;
* else ``XDG_RUNTIME_DIR`` naming an existing, writable directory → ``<XDG_RUNTIME_DIR>/romp``, created 0700 when
  missing (tmux 3.4 falls back to ``/tmp`` SILENTLY when ``TMUX_TMPDIR`` names a missing directory, so the directory
  is made here, never assumed);
* else None: tmux's own default, as before (macOS under launchd, a shell without the variable).

A client already inside a pane uses the socket path in its own ``$TMUX`` and needs none of this. Pure over the
environment it is handed; no tmux is run here.
"""
import os

ROMP_SUBDIR = "romp"


def _usable_dir(p):
    return bool(p) and os.path.isdir(p) and os.access(p, os.W_OK | os.X_OK)


def tmux_tmpdir(env=None, mkdir=True):
    """The TMUX_TMPDIR every romp tmux client and server should use, or None for tmux's own default (the rule in
    the module docstring). `env` defaults to os.environ; `mkdir=False` only reports (the manager's node twin and
    the tests read the rule without making directories)."""
    env = os.environ if env is None else env
    op = (env.get("TMUX_TMPDIR") or "").strip()
    if op:
        return op
    run = (env.get("XDG_RUNTIME_DIR") or "").strip()
    if not _usable_dir(run):
        return None
    d = os.path.join(run, ROMP_SUBDIR)
    if mkdir and not os.path.isdir(d):
        try:
            os.mkdir(d, 0o700)
        except OSError:
            return None
    return d if _usable_dir(d) else None


def export_tmux_tmpdir(env=None):
    """Write the resolved TMUX_TMPDIR into `env` (os.environ by default) so every tmux subprocess and every child
    inherits it; returns the value, or None when tmux's default stands (nothing written)."""
    env = os.environ if env is None else env
    d = tmux_tmpdir(env)
    if d and env.get("TMUX_TMPDIR") != d:
        env["TMUX_TMPDIR"] = d
    return d


def socket_path(env=None):
    """The default socket's path under the resolved directory, for log lines: <dir>/tmux-<uid>/default."""
    d = tmux_tmpdir(env, mkdir=False) or "/tmp"
    return os.path.join(d, "tmux-%d" % os.getuid(), "default")
