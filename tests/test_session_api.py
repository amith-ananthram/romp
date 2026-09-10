#!/usr/bin/env python3
"""The SessionBackend contract (the user 2026-06-26): tmux + SDK behind ONE clean session API, and NOTHING
above the backend shells tmux. These tests pin (a) both backends honor the ABC and (b) the no-raw-tmux
guard — so a future tmux leak into the higher layers fails CI instead of silently rotting the abstraction.
"""
import os
import re
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_session_backend", os.path.join(BIN, "romp_session_backend.py"))

ABSTRACT = sorted(sb.SessionBackend.__abstractmethods__)


class AbcContract(unittest.TestCase):
    def test_abc_lists_the_expected_contract(self):
        for m in ("owns", "live_sessions", "send", "interrupt", "set_model", "set_mode", "set_effort",
                  "spawn", "resume", "kill", "rename",
                  "pending_queued", "live_atoms", "prune_live", "on_ask", "current_ask"):
            self.assertIn(m, ABSTRACT, "SessionBackend must declare %s as part of the contract" % m)

    def test_coordination_methods_exist_as_concrete_defaults_for_now(self):
        # working_note/set_working_note/wake are part of the target contract but start as concrete no-op
        # defaults (the SDK gap is filled in P3); assert they exist and the base is a safe no-op.
        for m in ("working_note", "set_working_note", "wake"):
            self.assertTrue(hasattr(sb.SessionBackend, m), "the ABC declares %s (concrete default for now)" % m)
        self.assertNotIn("wake", ABSTRACT, "coordination methods are concrete defaults until P3")

    def test_forwards_sends_capability(self):
        # forwards_sends is a CONCRETE default (False) on the ABC — the kernel holds + merges a backend's
        # sends when it can't forward them itself (tmux inherits this). The SDK overrides it True so the
        # kernel hands it composer sends mid-turn (the user 2026-07-17). SDK checked at the source level.
        self.assertNotIn("forwards_sends", ABSTRACT,
                         "forwards_sends is a concrete default, not part of the abstract contract")
        self.assertFalse(sb.SessionBackend.forwards_sends(object()),
                         "the ABC default is False (hold + merge, like tmux)")
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        m = re.search(r"def forwards_sends\(self\)[\s\S]*?\n        return (\w+)", src)
        self.assertTrue(m and m.group(1) == "True", "SdkBackend.forwards_sends returns True")

    def test_move_is_a_concrete_default_that_refuses_and_the_sdk_implements_it(self):
        # move (the user 2026-09-01: a session follows a subproject promoted to its own repo) is a
        # CONCRETE default on the ABC — a backend with no relocation primitive answers with the reason,
        # never "" (which would read as success), never "busy" (which would park a retry forever) and
        # never a raise. The SDK backend implements it over the CLI's set_cwd control request; asserted
        # at the source level like the abstract set.
        self.assertNotIn("move", ABSTRACT, "move is a concrete default (a backend without one inherits the refusal)")
        why = sb.SessionBackend.move(object(), "sid", "/tmp")
        self.assertIsInstance(why, str)
        self.assertTrue(why, "the default is a REASON, not an empty success")
        self.assertNotEqual(why, "busy")
        self.assertIn("no way to move", why)
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        self.assertIn("\n    def move(self, sid: str, new_cwd: str) -> str:", src,
                      "SdkBackend implements move")

    def test_sdk_backend_honors_every_abstract_method(self):
        # SdkBackend is SDK-gated so it can't import the ABC when the dep is absent; it conforms by
        # duck-typing. Assert at the SOURCE level (no SDK dep needed) that it DEFINES each abstract method,
        # so the duck-typing can't silently drift from the contract.
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        defs = set(re.findall(r"\n    def ([a-z_]+)\s*\(", src))
        for m in ABSTRACT:
            self.assertIn(m, defs, "SdkBackend must implement the SessionBackend method %s" % m)

    def test_control_setters_document_their_per_backend_mechanics(self):
        # set_model, set_effort and set_fast land DIFFERENTLY on the two backends — the SDK switches
        # model live over its control request but RECONNECTS for effort; tmux types the CLI's own
        # command into the pane and accepts /model's confirmation — and the contract is where a reader
        # learns that, so each carries a docstring naming its mechanism. One distinctive word per
        # method keeps the pin honest without freezing the prose.
        for m, word in (("set_model", "control"), ("set_effort", "reconnect"), ("set_fast", "connect")):
            doc = getattr(sb.SessionBackend, m).__doc__ or ""
            self.assertTrue(doc.strip(), "SessionBackend.%s carries a docstring" % m)
            self.assertIn(word, doc, "SessionBackend.%s's docstring names its mechanism (%r)" % (m, word))


# quoted-literal markers — a raw `["tmux"` subprocess list, a tmux SUBCOMMAND string arg, or a tmux @-var
# NAME string. Matching only QUOTED literals (not bare words) means prose in comments/docstrings that merely
# mentions "send-keys" or "@claude-state" is NOT flagged — only actual tmux code is.
_TMUX_MARKERS = [
    (re.compile(r'\[\s*["\']tmux["\']'), "raw tmux subprocess list"),
    (re.compile(r'["\'](?:send-keys|list-sessions|paste-buffer|capture-pane|set-buffer|kill-session|'
                r'rename-session|display-message|pane_in_mode)["\']'), "tmux subcommand literal"),
    (re.compile(r'["\']@(?:claude|romp|identity)-[a-z-]*["\']'), "tmux @-var literal"),
]


def _scan_tmux(text, skip_span=None):
    """Lines (1-based) of `text` that hold a raw-tmux marker in CODE (a trailing #comment is dropped first),
    excluding the optional [start,end) line span. Returns [(lineno, desc, line)]."""
    out = []
    for i, line in enumerate(text.split("\n")):
        if skip_span and skip_span[0] <= i < skip_span[1]:
            continue
        code = line.split("#", 1)[0]
        for rx, desc in _TMUX_MARKERS:
            if rx.search(code):
                out.append((i + 1, desc, line.strip()[:90]))
    return out


class NoRawTmuxOutsideTmuxBackend(unittest.TestCase):
    """The leak guard (the user 2026-06-26): raw tmux lives ONLY in bin/romp-kernel's TmuxBackend class; the
    higher layers (build_*, the _drive dispatch, GET /sessions, the postal bus) speak the SessionBackend API.
    A future tmux call shelled outside the class fails CI instead of silently re-coupling the kernel to tmux."""

    KERNEL = os.path.join(BIN, "romp-kernel")

    def _span(self, lines):
        start = next(i for i, l in enumerate(lines) if l.startswith("class TmuxBackend("))
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i] and lines[i][0] not in " \t#)"), len(lines))
        return start, end

    def test_no_raw_tmux_outside_the_class(self):
        lines = open(self.KERNEL, encoding="utf-8").read().split("\n")
        leaks = _scan_tmux("\n".join(lines), skip_span=self._span(lines))
        self.assertEqual(leaks, [], "raw tmux leaked outside TmuxBackend:\n"
                         + "\n".join("  L%d [%s]: %s" % x for x in leaks))

    def test_the_class_actually_owns_the_raw_tmux(self):
        # so the span exclusion above isn't vacuously passing — the raw tmux really IS in the class
        lines = open(self.KERNEL, encoding="utf-8").read().split("\n")
        start, end = self._span(lines)
        body = "\n".join(lines[start:end])
        self.assertIn('["tmux"]', body, "TmuxBackend holds the raw tmux subprocess primitives")
        self.assertIn('"send-keys"', body)
        self.assertIn('"list-sessions"', body)


class PostalIsFullyTmuxFree(unittest.TestCase):
    """P3 complete: the postal bus (a SEPARATE process) reaches tmux ONLY through the kernel's session API —
    session enumeration, the working-note, mail delivery/wake, the resume-picker check, and the status-bar
    mail/peer/message chrome all go over HTTP. So bin/romp-postal shells NO tmux at all; a regression fails CI
    instead of silently re-coupling the bus to tmux. (the user 2026-06-26.)"""

    POSTAL = os.path.join(BIN, "romp-postal-service")

    def test_no_raw_tmux_anywhere_in_the_bus(self):
        src = open(self.POSTAL, encoding="utf-8").read()
        leaks = _scan_tmux(src)
        self.assertEqual(leaks, [], "raw tmux leaked into the postal bus:\n"
                         + "\n".join("  L%d [%s]: %s" % x for x in leaks))

    def test_the_tmux_shell_helper_is_gone(self):
        src = open(self.POSTAL, encoding="utf-8").read()
        self.assertNotIn("def tmux(", src, "the bus's tmux() shell helper is removed")
        self.assertNotIn("def tmux_bin(", src)

    def test_the_bus_reaches_the_kernel_for_every_session_op(self):
        src = open(self.POSTAL, encoding="utf-8").read()
        for ep in ('"/sessions"', '"/working"', '"/deliver"', '"/picker-check"',
                   '"/mail-badge"', '"/deliver-chrome"', '"/reconcile-peers"'):
            self.assertIn(ep, src, "the bus reaches the kernel endpoint %s" % ep)


if __name__ == "__main__":
    unittest.main()
