#!/usr/bin/env python3
"""ROMP_STATE_DIR — the per-kernel state-root override (plans/multi-kernel.md, phase 1).

Every surface derives its state root as $XDG_STATE_HOME/romp; multi-kernel needs each kernel on its
OWN root, and the override is deliberately romp-specific: overriding XDG_STATE_HOME instead would
leak into spawned children (SDK `claude` processes) and move THEIR unrelated state too.

Python surfaces are tested functionally in subprocesses (import with the env set, print the derived
STATE); the kernel itself is NOT imported (importing it runs boot reconcile against the live fleet —
tests/README's standing caution), so its three embedded shell snippets are source-pinned instead,
alongside the shell/node surfaces. Each functional case builds a CLEAN env: running this suite
inside a romp session must not inherit the live kernel's ROMP_STATE_DIR/XDG_STATE_HOME (the
romp-wake-hook.bats lesson, romp_docs 2026-07-24)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(os.path.dirname(os.path.realpath(__file__)))
ROOT = HERE.parent

PY_SURFACES = [
    # (module file, expression printing the derived root, suffix the module adds under the root)
    ("kernel/event_model.py", "STATE", ""),
    ("kernel/judge.py", "STATE", ""),
    ("postal/postal_service.py", "STATE", "/postal"),
    ("postal/postal_service.py", "NAMES_DIR", "/names"),
    # The live Codex smoke helper reads the root to find the installed Codex runtime before it
    # rebinds XDG_STATE_HOME to a scratch dir; nothing else executes it, so it is a row here.
    ("tests/smoke_codex_live.py", "RUNTIME_STATE", ""),
]


def _derive(module, attr, env, cwd=None):
    """Import `module` in a SUBPROCESS with exactly `env` and print its `attr` path. A subprocess so
    each case gets a fresh import (the constants bind at import time) and a clean environment.
    `cwd` defaults to the checkout; a case that could resolve a RELATIVE root passes a temp dir, since
    judge.py mkdirs its root at import and a regression must not litter the checkout. TMPDIR is the
    case's temp home, so a module that mkdtemps at import (the smoke helper) leaves nothing behind."""
    code = ("import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('m', %r)\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "try:\n    spec.loader.exec_module(m)\n"
            "except SystemExit:\n    pass\n"
            "print(getattr(m, %r))\n" % (str(ROOT / module), attr))
    home = env.pop("_HOME")
    full = {"PATH": os.environ.get("PATH", ""), "HOME": home, "TMPDIR": home, **env}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=full, cwd=str(cwd or ROOT), timeout=60)
    self_desc = "%s.%s with %s" % (module, attr, {k: v for k, v in full.items() if k != "PATH"})
    assert out.returncode == 0, "%s failed: %s" % (self_desc, out.stderr[-400:])
    return out.stdout.strip().splitlines()[-1]


class PythonSurfaces(unittest.TestCase):
    def test_override_wins_everywhere(self):
        with tempfile.TemporaryDirectory() as td:
            alt = td + "/alt"
            for module, attr, suffix in PY_SURFACES:
                got = _derive(module, attr, {"_HOME": td, "ROMP_STATE_DIR": alt})
                self.assertEqual(got, alt + suffix, "%s.%s under the override" % (module, attr))

    def test_default_is_untouched_without_the_override(self):
        # ROMP_STATE_DIR deliberately ABSENT from the subprocess env — the control case must not
        # inherit a live kernel's export (the default-vs-override trap, romp_docs 2026-07-24).
        with tempfile.TemporaryDirectory() as td:
            for module, attr, suffix in PY_SURFACES:
                got = _derive(module, attr, {"_HOME": td, "XDG_STATE_HOME": td + "/xdg"})
                self.assertEqual(got, td + "/xdg/romp" + suffix,
                                 "%s.%s must keep the XDG derivation" % (module, attr))

    def test_empty_xdg_state_home_is_unset(self):
        # XDG_STATE_HOME present but EMPTY reads as unset: the XDG spec says so, and every shell
        # surface's ${XDG_STATE_HOME:-...} already does. A .get default kept the empty string and made
        # the root the RELATIVE path romp under the process cwd, so a kernel started with a blank
        # XDG_STATE_HOME= line kept its state (and looked for the SDK venv) under its cwd while the
        # shell surfaces used the home root.
        with tempfile.TemporaryDirectory() as td:
            for module, attr, suffix in PY_SURFACES:
                with self.subTest(module=module, attr=attr):
                    got = _derive(module, attr, {"_HOME": td, "XDG_STATE_HOME": ""}, cwd=td)
                    self.assertEqual(got, td + "/.local/state/romp" + suffix,
                                     "%s.%s must read an empty XDG_STATE_HOME as unset" % (module, attr))


WRAPPED = "${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}"
UNWRAPPED = "${XDG_STATE_HOME:-$HOME/.local/state}/romp"


class ShellAndNodeSourcePins(unittest.TestCase):
    """The shell/node surfaces can't be cheaply executed here; pin their source instead. The
    UNWRAPPED literal must be gone outside the wrapped form — a new call site pasted from an old
    example would silently pin an aux kernel to the primary's state."""

    SHELL = ["bin/romp", "bin/romp-node-launch", "bin/romp-sdk-setup", "bin/romp-service",
             "hooks/romp-postal-revive.sh", "hooks/romp-wake.sh", "hooks/tmux-status.sh",
             "kernel/kernel.py"]

    def test_every_shell_site_is_wrapped(self):
        for p in self.SHELL:
            src = (ROOT / p).read_text()
            self.assertIn(WRAPPED, src, p)
            self.assertEqual(src.count(UNWRAPPED), src.count(WRAPPED),
                             "%s holds an UNWRAPPED derivation (pins an aux kernel to the primary's state)" % p)

    def test_node_surfaces_honor_the_override(self):
        ext = (ROOT / "vscode-extension/src/extension.ts").read_text()
        self.assertIn('process.env.ROMP_STATE_DIR || path.join(base, "romp")', ext)
        tl = (ROOT / "ui/romp-timeline-view.js").read_text()
        self.assertIn("process.env.ROMP_STATE_DIR || path.join(base, 'romp')", tl)


class VisibilityScoping(unittest.TestCase):
    """Phase 2 (plans/multi-kernel.md): two kernels must not see each other's sessions. The projects
    root honors CLAUDE_CONFIG_DIR (unscoped, both kernels judge every transcript on the machine —
    double LLM spend), and the tmux runner takes ROMP_TMUX_SOCKET (unscoped, both kernels inject
    nudges into the same panes)."""

    def test_projects_root_honors_claude_config_dir(self):
        with tempfile.TemporaryDirectory() as td:
            for module in ("kernel/event_model.py", "kernel/judge.py"):
                got = _derive(module, "PROJECTS",
                              {"_HOME": td, "CLAUDE_CONFIG_DIR": td + "/cfg",
                               "ROMP_STATE_DIR": td + "/st"})
                self.assertEqual(got, td + "/cfg/projects", module)
                got = _derive(module, "PROJECTS", {"_HOME": td, "ROMP_STATE_DIR": td + "/st"})
                self.assertEqual(got, td + "/.claude/projects", "%s default unchanged" % module)

    def test_sdk_backend_transcript_path_honors_claude_config_dir(self):
        src = (ROOT / "bin/romp_sdk_backend.py").read_text()
        self.assertIn('os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")', src)

    def test_tmux_runner_takes_the_per_kernel_socket(self):
        # source-level: the argv builder is the ONE tmux seam (test_session_api's guard), and it must
        # read the socket at CALL time so a profile's env drives it without re-import.
        src = (ROOT / "kernel/kernel.py").read_text()
        self.assertIn('sock = os.environ.get("ROMP_TMUX_SOCKET")', src)
        self.assertIn('(["tmux", "-L", sock] if sock else ["tmux"]) + list(args)', src)
        # functional: build the argv both ways without importing the kernel (import runs boot
        # reconcile against the live fleet) — execute just the builder body.
        ns = {"os": os}
        exec("def _tmux_argv(args):\n"
             "    sock = os.environ.get('ROMP_TMUX_SOCKET')\n"
             "    return (['tmux', '-L', sock] if sock else ['tmux']) + list(args)", ns)
        old = os.environ.pop("ROMP_TMUX_SOCKET", None)
        try:
            self.assertEqual(ns["_tmux_argv"](["ls"]), ["tmux", "ls"])
            os.environ["ROMP_TMUX_SOCKET"] = "romp-alt"
            self.assertEqual(ns["_tmux_argv"](["ls"]), ["tmux", "-L", "romp-alt", "ls"])
        finally:
            os.environ.pop("ROMP_TMUX_SOCKET", None)
            if old is not None:
                os.environ["ROMP_TMUX_SOCKET"] = old


if __name__ == "__main__":
    unittest.main()
