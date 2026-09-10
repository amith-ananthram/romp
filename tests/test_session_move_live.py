#!/usr/bin/env python3
"""LIVE integration test for the session move (the user 2026-09-01): the CLI's `set_cwd` control
request, driven through the real Claude Agent SDK against a real `claude`, in a HERMETIC config dir
(CLAUDE_CONFIG_DIR under a temp path — the recipe the 2026-09-02 investigation verified the response
shape with). It proves the facts SdkBackend.move is built on: the `ok {cwd, changed,
transcript_relocated}` arm, the messages that follow with no query sent (init with the new cwd, the
turn-less ResultMessage), `changed: false` on a same-dir request, `rejected not_found`, and that the
transcript ends up under the NEW project slug ONLY — never copied — while the conversation (a codeword
planted before the move) is still remembered after it.

OPT-IN, and skipped otherwise: it bills two short model turns and reaches a real CLI, which the test
suite's floors (conftest's ROMP_CLAUDE_BIN=/bin/false) exist to prevent by default. Runs only when
  * ROMP_MOVE_LIVE=1,
  * an interpreter with claude_agent_sdk is available — this one, $ROMP_SDK_PYTHON, or the kernel's own
    SDK venv (~/.local/state/romp/sdkvenv, what bin/romp-sdk-setup builds) — the child runs there, so the
    suite's interpreter needs no SDK,
  * a `claude` binary ($ROMP_MOVE_LIVE_CLAUDE, else PATH), and
  * Claude Code auth the hermetic config dir can use: ANTHROPIC_API_KEY in the environment; or
    $ROMP_MOVE_LIVE_API_KEY_HELPER — a shell command that prints a key, written into the hermetic
    settings as their apiKeyHelper; or, failing both, the `apiKeyHelper` from the user's OWN Claude
    Code settings ($CLAUDE_CONFIG_DIR/settings.json, default ~/.claude/settings.json), copied into
    the hermetic settings as-is (the hermetic dir sees none of the operator's own settings otherwise).
    Under pytest the suite's conftest floors CLAUDE_CONFIG_DIR to an empty dir for every test and saves
    the real location in ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR, which the borrow reads first.
    Either way the helper COMMAND travels, never a key, and this test writes no key to a file.
CI has none of these and skips cleanly. Synthetic content throughout (an invented sid, an invented
codeword, temp folders)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SID = "aaaaaaaa-1111-4222-8333-444444444444"
CODEWORD = "PLUM-FORTY-TWO"

CHILD = r'''
import asyncio, glob, json, os, sys
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
CFG, A, B, CLI, SID, CODEWORD, SETTINGS = sys.argv[1:8]

def desc(m):
    t = type(m).__name__
    if t == "ResultMessage":
        return {"type": t, "num_turns": m.num_turns, "result": (m.result or "")[:400], "is_error": m.is_error}
    if t == "SystemMessage":
        return {"type": t, "subtype": m.subtype, "cwd": (m.data or {}).get("cwd")}
    if t == "AssistantMessage":
        return {"type": t, "text": " ".join(getattr(b, "text", "") for b in m.content)[:400]}
    return {"type": t}

async def drain(c, secs):
    out = []
    async def rd():
        async for m in c.receive_messages():
            out.append(desc(m))
    try:
        await asyncio.wait_for(rd(), timeout=secs)
    except asyncio.TimeoutError:
        pass
    return out

async def main():
    res = {}
    opts = ClaudeAgentOptions(cwd=A, cli_path=CLI, settings=SETTINGS, env={"CLAUDE_CONFIG_DIR": CFG},
                              setting_sources=["user", "project"],
                              system_prompt={"type": "preset", "preset": "claude_code"},
                              extra_args={"session-id": SID})
    async with ClaudeSDKClient(opts) as c:
        q = c._query
        res["has_sender"] = callable(getattr(q, "_send_control_request", None))
        await c.query("The codeword for this project is %s. Reply with just OK." % CODEWORD)
        async for m in c.receive_response():
            pass
        r = await q._send_control_request({"subtype": "set_cwd", "path": B})
        if r.get("status") == "needs_trust":
            res["needs_trust"] = r
            r = await q._send_control_request({"subtype": "set_cwd", "path": B, "trust_accepted": True,
                                               "trusted_directory": r.get("directory") or B})
        res["move"] = r
        res["after_move"] = await drain(c, 6)
        res["same_dir"] = await q._send_control_request({"subtype": "set_cwd", "path": B})
        res["missing"] = await q._send_control_request({"subtype": "set_cwd", "path": B + "/does-not-exist"})
        await c.query("Without running any tools, answer in one line: what is the codeword?")
        reply = ""
        async for m in c.receive_response():
            d = desc(m)
            if d["type"] == "ResultMessage":
                reply = d["result"]
        res["reply"] = reply
    res["files"] = sorted(os.path.relpath(p, os.path.join(CFG, "projects"))
                          for p in glob.glob(os.path.join(CFG, "projects", "*", "*.jsonl")))
    print(json.dumps(res))

asyncio.run(main())
'''


def _sdk_python():
    for cand in (sys.executable, os.environ.get("ROMP_SDK_PYTHON") or "",
                 os.path.expanduser("~/.local/state/romp/sdkvenv/bin/python")):
        if cand and os.path.exists(cand):
            r = subprocess.run([cand, "-c", "import claude_agent_sdk"], capture_output=True)
            if r.returncode == 0:
                return cand
    return ""


def _user_api_key_helper(config_dir=None):
    """The `apiKeyHelper` command from the user's own Claude Code settings ("" when there is none): the
    hermetic config dir borrows the COMMAND, so the child authenticates exactly the way the user's real
    sessions do. Never a key: this test writes none to disk. `config_dir` defaults to the user's real
    config dir: under pytest the suite's conftest floors $CLAUDE_CONFIG_DIR to an empty dir and saves the
    real location in $ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR, so that is read first; else $CLAUDE_CONFIG_DIR,
    else ~/.claude."""
    d = (config_dir or os.environ.get("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
         or os.path.expanduser("~/.claude"))
    try:
        with open(os.path.join(d, "settings.json")) as f:
            v = json.load(f).get("apiKeyHelper")
    except (OSError, ValueError, AttributeError):
        return ""
    return v.strip() if isinstance(v, str) else ""


def _api_key_helper():
    """The apiKeyHelper command the hermetic settings get when ANTHROPIC_API_KEY is unset: the explicit
    $ROMP_MOVE_LIVE_API_KEY_HELPER first, else the command borrowed from the user's own settings."""
    return os.environ.get("ROMP_MOVE_LIVE_API_KEY_HELPER") or _user_api_key_helper()


def _skip_reason():
    if os.environ.get("ROMP_MOVE_LIVE") != "1":
        return "live move test is opt-in (ROMP_MOVE_LIVE=1): it bills two model turns against a real CLI"
    if not _sdk_python():
        return "no interpreter with claude_agent_sdk (this one, $ROMP_SDK_PYTHON, or ~/.local/state/romp/sdkvenv)"
    if not (os.environ.get("ROMP_MOVE_LIVE_CLAUDE") or shutil.which("claude")):
        return "no `claude` binary ($ROMP_MOVE_LIVE_CLAUDE or PATH)"
    if not (os.environ.get("ANTHROPIC_API_KEY") or _api_key_helper()):
        return ("no Claude Code auth for a hermetic config dir (ANTHROPIC_API_KEY in the environment, "
                "ROMP_MOVE_LIVE_API_KEY_HELPER, or an apiKeyHelper in your Claude Code settings)")
    return ""


class UserApiKeyHelper(unittest.TestCase):
    """The lookup the live test's hermetic settings borrow their apiKeyHelper from: the command string
    when the user's settings name one; "" for a missing dir, unreadable JSON, or settings without the
    key — and never a default that reads a key from a file."""

    def test_returns_the_configured_command(self):
        td = tempfile.mkdtemp(prefix="romp-move-live-cfg-")
        try:
            with open(os.path.join(td, "settings.json"), "w") as f:
                json.dump({"apiKeyHelper": " /opt/example/helper --print "}, f)
            self.assertEqual(_user_api_key_helper(td), "/opt/example/helper --print")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_empty_without_a_helper(self):
        td = tempfile.mkdtemp(prefix="romp-move-live-cfg-")
        try:
            self.assertEqual(_user_api_key_helper(os.path.join(td, "absent")), "")
            with open(os.path.join(td, "settings.json"), "w") as f:
                f.write("{not json")
            self.assertEqual(_user_api_key_helper(td), "")
            with open(os.path.join(td, "settings.json"), "w") as f:
                json.dump({"permissions": {"defaultMode": "default"}, "apiKeyHelper": 7}, f)
            self.assertEqual(_user_api_key_helper(td), "")
            with open(os.path.join(td, "settings.json"), "w") as f:
                json.dump(["not", "an", "object"], f)
            self.assertEqual(_user_api_key_helper(td), "")
        finally:
            shutil.rmtree(td, ignore_errors=True)


class ApiKeyHelperPrecedence(unittest.TestCase):
    """The helper the hermetic settings get (always-run; the live class stays opt-in): the explicit
    $ROMP_MOVE_LIVE_API_KEY_HELPER wins; else the command borrowed from the real config dir the suite's
    conftest saved in $ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR; else from $CLAUDE_CONFIG_DIR/settings.json;
    else from ~/.claude/settings.json; "" when none names one. HOME and CLAUDE_CONFIG_DIR point at temp
    dirs throughout and the saved real location is cleared, or pointed at a temp dir, inside every patch
    (under pytest it names the operator's own ~/.claude), so nothing here reads the operator's own
    settings."""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="romp-move-live-prec-")
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def _settings(self, sub, helper):
        d = os.path.join(self.td, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"apiKeyHelper": helper}, f)
        return d

    def test_explicit_env_var_wins_over_the_borrowed_command(self):
        cfg = self._settings("cfg", "/opt/example/borrowed --print")
        with mock.patch.dict(os.environ, {"HOME": os.path.join(self.td, "home"), "CLAUDE_CONFIG_DIR": cfg,
                                          "ROMP_MOVE_LIVE_API_KEY_HELPER": "/opt/example/explicit"}):
            os.environ.pop("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR", None)
            self.assertEqual(_api_key_helper(), "/opt/example/explicit")

    def test_without_the_env_var_the_borrowed_command_is_used(self):
        cfg = self._settings("cfg", "/opt/example/borrowed --print")
        with mock.patch.dict(os.environ, {"HOME": os.path.join(self.td, "home"), "CLAUDE_CONFIG_DIR": cfg}):
            os.environ.pop("ROMP_MOVE_LIVE_API_KEY_HELPER", None)
            os.environ.pop("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR", None)
            self.assertEqual(_api_key_helper(), "/opt/example/borrowed --print")
            self.assertEqual(_user_api_key_helper(), "/opt/example/borrowed --print", "no argument: $CLAUDE_CONFIG_DIR")

    def test_the_default_dir_is_dot_claude_under_home(self):
        home = os.path.join(self.td, "home")
        self._settings("home/.claude", "/opt/example/home-helper")
        with mock.patch.dict(os.environ, {"HOME": home}):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            os.environ.pop("ROMP_MOVE_LIVE_API_KEY_HELPER", None)
            os.environ.pop("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR", None)
            self.assertEqual(_user_api_key_helper(), "/opt/example/home-helper")
            self.assertEqual(_api_key_helper(), "/opt/example/home-helper")

    def test_empty_when_nothing_names_a_helper(self):
        with mock.patch.dict(os.environ, {"HOME": os.path.join(self.td, "empty"),
                                          "CLAUDE_CONFIG_DIR": os.path.join(self.td, "absent")}):
            os.environ.pop("ROMP_MOVE_LIVE_API_KEY_HELPER", None)
            os.environ.pop("ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR", None)
            self.assertEqual(_api_key_helper(), "")

    def test_the_saved_real_config_dir_outranks_the_floored_one(self):
        # conftest floors CLAUDE_CONFIG_DIR to an empty dir for every test and saves the location the
        # run was handed in ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR; the live run borrows from the saved
        # location, else it could never find the operator's helper under pytest
        real = self._settings("real", "/opt/example/real-helper")
        floor = self._settings("floor", "/opt/example/floored-helper")
        with mock.patch.dict(os.environ, {"HOME": os.path.join(self.td, "home"), "CLAUDE_CONFIG_DIR": floor,
                                          "ROMP_TESTS_REAL_CLAUDE_CONFIG_DIR": real}):
            os.environ.pop("ROMP_MOVE_LIVE_API_KEY_HELPER", None)
            self.assertEqual(_user_api_key_helper(), "/opt/example/real-helper")
            self.assertEqual(_api_key_helper(), "/opt/example/real-helper")
            self.assertEqual(_user_api_key_helper(floor), "/opt/example/floored-helper", "an explicit dir still wins")


@unittest.skipIf(_skip_reason(), _skip_reason())
class LiveSetCwd(unittest.TestCase):
    def test_set_cwd_relocates_and_the_conversation_survives(self):
        td = tempfile.mkdtemp(prefix="romp-move-live-")
        try:
            cfg, a, b = (os.path.join(td, d) for d in ("cfg", "a", "b"))
            for d in (cfg, a, b):
                os.makedirs(d)
            settings = {"permissions": {"defaultMode": "default"}}
            env = dict(os.environ)
            env.pop("ROMP_CLAUDE_BIN", None)
            if not env.get("ANTHROPIC_API_KEY"):
                settings["apiKeyHelper"] = _api_key_helper()
            spath = os.path.join(cfg, "settings.json")
            with open(spath, "w") as f:
                json.dump(settings, f)
            cli = os.environ.get("ROMP_MOVE_LIVE_CLAUDE") or shutil.which("claude")
            child = os.path.join(td, "child.py")
            with open(child, "w") as f:
                f.write(CHILD)
            r = subprocess.run([_sdk_python(), child, cfg, a, b, cli, SID, CODEWORD, spath],
                               capture_output=True, text=True, timeout=300, env=env, cwd=td)
            # a failure embeds the child's stderr, which can carry the apiKeyHelper's own diagnostics (never
            # a key: the helper's output is not read here) — read a failing run's message with that in mind
            # before pasting it anywhere
            self.assertEqual(r.returncode, 0, "child failed:\n%s\n%s" % (r.stdout[-2000:], r.stderr[-4000:]))
            res = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(res["has_sender"], "the SDK still exposes _send_control_request")
            mv = res["move"]
            self.assertEqual(mv.get("status"), "ok", mv)
            self.assertEqual(mv.get("cwd"), os.path.realpath(b))
            self.assertIs(mv.get("changed"), True)
            self.assertIs(mv.get("transcript_relocated"), True)
            kinds = [(m["type"], m.get("subtype"), m.get("cwd"), m.get("num_turns")) for m in res["after_move"]]
            self.assertIn(("SystemMessage", "init", os.path.realpath(b), None), kinds,
                          "the CLI announces the new cwd with no query sent")
            self.assertIn(("ResultMessage", None, None, 0), kinds, "…followed by the turn-less result")
            self.assertEqual(res["same_dir"].get("status"), "ok")
            self.assertIs(res["same_dir"].get("changed"), False)
            self.assertEqual((res["missing"].get("status"), res["missing"].get("reason")), ("rejected", "not_found"))
            self.assertIn(CODEWORD, res["reply"], "the conversation is remembered after the move")
            # The CLI's project slug: every non-alphanumeric character of the realpath becomes '-' (what
            # sdk_backend.transcript_path mirrors). Joining on '/' alone misses the '.' of a temp root like
            # ~/.cache and the '_' mkdtemp's suffix alphabet can produce, and fails the run for its path.
            slug_b = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(b))
            self.assertEqual(res["files"], [os.path.join(slug_b, SID + ".jsonl")],
                             "the transcript lives under the NEW slug only — moved, never copied")
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
