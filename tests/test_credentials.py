#!/usr/bin/env python3
"""romp holds no API key (the user 2026-09-08): Claude Code's own credential resolution is the only key path.

kernel/credentials.py is the whole of romp's contact with API credentials, and this module pins it:
  * the settings reader follows Claude Code's precedence (managed, project local, project, user) and reads
    the empty string as "helper disabled", a null as "not defined here";
  * the in-process helper run follows the CLI's contract (one line on stdout, exit 0) and its TTL memo, and
    never sees the kernel's own environment;
  * the boot check stops the kernel on any retired provider line, the marker, or a key in the kernel's
    environment, naming variables and files only;
  * the judges launch keyless for a key-billed call (the first pass after boot like every later one) and
    pass the helper suppression for a login-billed one;
  * the kernel's own catalog credential comes from the helper, and main() runs the boot check first.

Synthetic values throughout: the fixture helper prints a string no validator would take for a key."""
import inspect
import json
import os
import stat
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their roots at import time, and only pytest runs conftest's
# floors (a bare unittest or script run otherwise reads and writes REAL state and REAL settings).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["ROMP_SERVICE_ENV_FILE"] = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
for _n in ("ANTHROPIC_API_KEY", "ROMP_API_KEY_CMD", "ROMP_API_KEY_REF", "ANTHROPIC_AUTH_TOKEN",
           "CLAUDE_CODE_OAUTH_TOKEN", "ROMP_EXPECTED_AUTH", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS"):
    os.environ.pop(_n, None)
cred = load_source("romp_credentials", os.path.join(ROOT, "kernel", "credentials.py"))
sb = load_source("romp_sdk_backend_cred", os.path.join(BIN, "romp_sdk_backend.py"))
jd = load_source("romp_judge_cred", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_cred", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
HELPER_OUT = "synthetic-helper-output-1"          # not key-shaped on purpose: nothing may mistake it for one


def _helper_script(d, name="helper.sh", out=HELPER_OUT, body=None):
    """A fixture helper: touches a marker each run (so runs are countable) and prints `out`."""
    p = Path(d) / name
    marker = Path(d) / (name + ".ran")
    p.write_text(body or "#!/bin/sh\nprintf '.' >> '%s'\necho '%s'\n" % (marker, out))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p), marker


def _runs(marker):
    try:
        return len(marker.read_text())
    except OSError:
        return 0


class _Settings(unittest.TestCase):
    """A temp cwd with its own .claude/, a temp CLAUDE_CONFIG_DIR, a temp managed file: every settings file
    Claude Code reads, all synthetic, none of them real."""

    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self.managed = os.path.join(tempfile.mkdtemp(), "managed-settings.json")
        self._managed_before = cred.managed_settings_path
        cred.managed_settings_path = lambda: self.managed
        cred.forget_helper_key()

    def tearDown(self):
        cred.managed_settings_path = self._managed_before
        cred.forget_helper_key()
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before

    def _write(self, which, d):
        p = {"managed": self.managed,
             "local": os.path.join(self.cwd, ".claude", "settings.local.json"),
             "project": os.path.join(self.cwd, ".claude", "settings.json"),
             "user": os.path.join(self.cfg, "settings.json")}[which]
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Path(p).write_text(json.dumps(d) if not isinstance(d, str) else d)
        return p


class SettingsPrecedence(_Settings):
    def test_the_files_and_their_order_are_claude_codes(self):
        files = cred.settings_files(self.cwd)
        self.assertEqual(files, [self.managed,
                                 os.path.join(os.path.realpath(self.cwd), ".claude", "settings.local.json"),
                                 os.path.join(os.path.realpath(self.cwd), ".claude", "settings.json"),
                                 os.path.join(self.cfg, "settings.json")])

    def test_no_file_defines_the_helper(self):
        self.assertIsNone(cred.api_key_helper(self.cwd))
        self.assertFalse(cred.key_available())
        self.assertEqual(cred.helper_key(), "", "no helper: an empty answer, and the caller says so")

    def test_the_user_file_defines_it_and_each_higher_layer_overrides(self):
        self._write("user", {"apiKeyHelper": "/u/helper.sh"})
        self.assertEqual(cred.api_key_helper(self.cwd), "/u/helper.sh")
        self._write("project", {"apiKeyHelper": "/p/helper.sh"})
        self.assertEqual(cred.api_key_helper(self.cwd), "/p/helper.sh")
        self._write("local", {"apiKeyHelper": "/l/helper.sh"})
        self.assertEqual(cred.api_key_helper(self.cwd), "/l/helper.sh")
        self._write("managed", {"apiKeyHelper": "/m/helper.sh"})
        self.assertEqual(cred.api_key_helper(self.cwd), "/m/helper.sh")
        self.assertTrue(cred.key_available())

    def test_an_empty_string_disables_and_a_null_falls_through(self):
        self._write("user", {"apiKeyHelper": "/u/helper.sh"})
        self._write("local", {"apiKeyHelper": ""})
        self.assertEqual(cred.api_key_helper(self.cwd), "", "the CLI's disable value, the one a login launch writes")
        self.assertTrue(cred.key_available(), "the box's key side is the operator's helper, whatever a project says")
        self.assertTrue(cred.project_helper_differs(self.cwd), "and that project would resolve differently")
        self._write("local", {"apiKeyHelper": None})
        self.assertEqual(cred.api_key_helper(self.cwd), "/u/helper.sh", "null is not defined here: the CLI reads on")
        self.assertFalse(cred.project_helper_differs(self.cwd))

    def test_helper_source_names_the_operator_file_that_defines_it(self):
        self.assertIsNone(cred.helper_source())
        self._write("user", {"apiKeyHelper": "/u/helper.sh"})
        self.assertEqual(cred.helper_source(), "user")
        self._write("managed", {"apiKeyHelper": "/m/helper.sh"})
        self.assertEqual(cred.helper_source(), "managed", "managed outranks user, and no per-session layer can disable it")
        self._write("managed", {"apiKeyHelper": ""})
        self.assertIsNone(cred.helper_source(), "a managed disable is no helper at all")
        self._write("project", {"apiKeyHelper": "/p/helper.sh"})
        self.assertIsNone(cred.helper_source(), "a project's helper is not the operator's")
        self.assertEqual(cred.settings_files("/nonexistent/one", operator_only=True),
                         cred.settings_files("/nonexistent/two", operator_only=True), "cwd-independent")

    def test_the_kernel_acts_only_on_the_operators_helper(self):
        """The kernel reads a project's settings to know what the CLI will do, but never RUNS a helper a
        repository checked in (review 2026-09-08): its own calls use the managed or user helper only."""
        proj_script, proj_marker = _helper_script(tempfile.mkdtemp(), out="synthetic-project-output")
        self._write("project", {"apiKeyHelper": proj_script})
        self.assertEqual(cred.api_key_helper(self.cwd), proj_script, "the CLI would run it for that project")
        self.assertFalse(cred.key_available(), "but it is not the box's key side")
        self.assertEqual(cred.helper_key(), "", "and the kernel does not run it")
        self.assertEqual(_runs(proj_marker), 0)
        self.assertEqual(cred.settings_files(self.cwd, operator_only=True),
                         [self.managed, os.path.join(self.cfg, "settings.json")])
        user_script, user_marker = _helper_script(tempfile.mkdtemp(), out="synthetic-user-output")
        self._write("user", {"apiKeyHelper": user_script})
        self.assertEqual(cred.helper_key(), "synthetic-user-output", "the operator's helper is the one that runs")
        self.assertEqual((_runs(user_marker), _runs(proj_marker)), (1, 0))
        self.assertTrue(cred.project_helper_differs(self.cwd))

    def test_a_file_without_the_key_is_skipped_and_a_broken_file_is_loud(self):
        self._write("user", {"apiKeyHelper": "/u/helper.sh"})
        self._write("project", {"permissions": {}})
        self.assertEqual(cred.api_key_helper(self.cwd), "/u/helper.sh")
        p = self._write("local", "{not json")
        with self.assertRaisesRegex(cred.CredentialError, "not valid JSON") as cm:
            cred.api_key_helper(self.cwd)
        self.assertIn(p, str(cm.exception), "the path, so the user can fix the file")


class HelperRun(_Settings):
    def test_the_helper_runs_once_per_ttl_and_the_value_stays_in_memory(self):
        script, marker = _helper_script(tempfile.mkdtemp())
        self._write("user", {"apiKeyHelper": script})
        self.assertEqual(cred.helper_key(now=1000.0), HELPER_OUT)
        self.assertEqual(cred.helper_key(now=1000.0 + 299.0), HELPER_OUT)
        self.assertEqual(_runs(marker), 1, "within the TTL the memo answers")
        self.assertEqual(cred.helper_key(now=1000.0 + 301.0), HELPER_OUT)
        self.assertEqual(_runs(marker), 2, "past the TTL (five minutes by default) the helper runs again")
        cred.forget_helper_key()
        cred.helper_key(now=1000.0 + 302.0)
        self.assertEqual(_runs(marker), 3)

    def test_the_value_is_held_only_within_the_ttl(self):
        """romp holds no key material beyond what one call needs (the user): the memo clears at expiry on its
        own, a failed re-run leaves nothing behind, and a helper removed from the settings takes its value."""
        script, marker = _helper_script(tempfile.mkdtemp())
        self._write("user", {"apiKeyHelper": script})
        with patch.dict(os.environ, {"CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "150"}):
            self.assertEqual(cred.helper_key(), HELPER_OUT)
            self.assertEqual(cred._HELPER_MEMO["value"], HELPER_OUT, "held within the TTL")
            time.sleep(0.5)
            self.assertEqual(cred._HELPER_MEMO["value"], "", "cleared at expiry with nobody asking")
        self.assertEqual(cred.helper_key(now=0.0), HELPER_OUT)
        script2, _ = _helper_script(tempfile.mkdtemp(), "h.sh", body="#!/bin/sh\nexit 1\n")
        self._write("user", {"apiKeyHelper": script2})
        with self.assertRaises(cred.CredentialError):
            cred.helper_key(now=1.0)
        self.assertEqual(cred._HELPER_MEMO["value"], "", "a failed run leaves no value behind")
        self._write("user", {"apiKeyHelper": script})
        self.assertEqual(cred.helper_key(now=2.0), HELPER_OUT)
        self._write("user", {"permissions": {}})
        self.assertEqual(cred.helper_key(now=3.0), "", "the helper is gone")
        self.assertEqual(cred._HELPER_MEMO["value"], "", "and so is its value")

    def test_the_ttl_is_the_clis_variable_in_milliseconds(self):
        script, marker = _helper_script(tempfile.mkdtemp())
        self._write("user", {"apiKeyHelper": script})
        with patch.dict(os.environ, {"CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "1000"}):
            cred.helper_key(now=0.0)
            cred.helper_key(now=0.9)
            self.assertEqual(_runs(marker), 1)
            cred.helper_key(now=1.1)
            self.assertEqual(_runs(marker), 2)

    def test_a_changed_helper_runs_at_once(self):
        d = tempfile.mkdtemp()
        s1, m1 = _helper_script(d, "one.sh", "synthetic-one")
        s2, m2 = _helper_script(d, "two.sh", "synthetic-two")
        self._write("user", {"apiKeyHelper": s1})
        self.assertEqual(cred.helper_key(now=0.0), "synthetic-one")
        self._write("user", {"apiKeyHelper": s2})
        self.assertEqual(cred.helper_key(now=1.0), "synthetic-two", "the memo is keyed on the command")

    def test_the_helper_never_sees_the_kernels_environment(self):
        d = tempfile.mkdtemp()
        script, _ = _helper_script(d, body="#!/bin/sh\necho \"x${ROMP_SERVE_TOKEN}${ROMP_SECRET_PROBE}\"\n")
        self._write("user", {"apiKeyHelper": script})
        with patch.dict(os.environ, {"ROMP_SECRET_PROBE": "leaked"}):
            self.assertEqual(cred.helper_key(), "x", "a whitelist: PATH, HOME, the config dir and the like")

    def test_failures_are_static_words(self):
        d = tempfile.mkdtemp()
        cases = [("#!/bin/sh\nexit 1\n", "failed \\(non-zero exit\\)"),
                 ("#!/bin/sh\nexit 0\n", "empty or invalid key"),
                 ("#!/bin/sh\necho a\necho b\n", "empty or invalid key"),
                 ("#!/bin/sh\nexec no-such-command-romp-test\n", "not on the manager's PATH \\(exit 127\\)")]
        for i, (body, pattern) in enumerate(cases):
            script, _ = _helper_script(d, "h%d.sh" % i, body=body)
            self._write("user", {"apiKeyHelper": script})
            cred.forget_helper_key()
            with self.assertRaisesRegex(cred.CredentialError, pattern):
                cred.helper_key()

    def test_a_timeout_is_a_static_word_too(self):
        script, _ = _helper_script(tempfile.mkdtemp(), body="#!/bin/sh\nsleep 5\necho late\n")
        self._write("user", {"apiKeyHelper": script})
        with patch.object(cred, "HELPER_TIMEOUT_S", 1):
            with self.assertRaisesRegex(cred.CredentialError, "timed out"):
                cred.helper_key()


class BootCheck(unittest.TestCase):
    """A retired provider line, the marker, or a key in the kernel's environment stops the kernel; the message
    names files and variables and never a value."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "service.env")

    def _file(self, text):
        Path(self.p).write_text(text)

    def test_a_clean_file_with_the_declaration_starts(self):
        self._file("ROMP_EXPECTED_AUTH=key\nROMP_SERVE_PORT=29855\n# a comment\n")
        cred.check_boot_environment(self.p, environ={})
        cred.check_boot_environment(os.path.join(self.d, "absent.env"), environ={})

    def test_each_retired_line_stops_the_kernel_without_saying_the_value(self):
        for line in ("ROMP_API_KEY_CMD=op read op://vault/item/field",
                     "ROMP_API_KEY_REF=op://vault/item/field",
                     "ANTHROPIC_API_KEY=synthetic-value-never-printed",
                     "export ANTHROPIC_API_KEY='synthetic-value-never-printed'",
                     "ANTHROPIC_API_KEY="):
            self._file("ROMP_EXPECTED_AUTH=key\n%s\n" % line)
            with self.assertRaises(RuntimeError) as cm:
                cred.check_boot_environment(self.p, environ={})
            msg = str(cm.exception)
            self.assertIn(self.p, msg)
            self.assertIn(line.split("=")[0].replace("export ", ""), msg)
            self.assertNotIn("synthetic-value", msg, "names and files, never a value")
            self.assertNotIn("op://", msg)
            self.assertIn("did NOT start", msg)
            self.assertIn("apiKeyHelper", msg)
            self.assertIn("ROMP_EXPECTED_AUTH=key", msg)

    def test_the_1password_names_are_refused_in_the_file_and_the_environment(self):
        """The retired reference kind read the 1Password CLI's token beside it; romp no longer runs op, and a
        token left in service.env would ride into every session and the tmux server (review 2026-09-08)."""
        self._file("ROMP_EXPECTED_AUTH=key\nOP_SERVICE_ACCOUNT_TOKEN=synthetic-value-never-printed\nOP_SESSION_acct=synthetic-2\n")
        with self.assertRaises(RuntimeError) as cm:
            cred.check_boot_environment(self.p, environ={})
        msg = str(cm.exception)
        self.assertIn("OP_SERVICE_ACCOUNT_TOKEN, OP_SESSION_acct", msg)
        self.assertIn("no longer runs op", msg)
        self.assertNotIn("synthetic", msg)
        self._file("ROMP_EXPECTED_AUTH=key\n")
        with self.assertRaises(RuntimeError) as cm:
            cred.check_boot_environment(self.p, environ={"OP_CONNECT_TOKEN": "synthetic-value-never-printed"})
        self.assertIn("the manager's environment carries OP_CONNECT_TOKEN", str(cm.exception))
        cred.check_boot_environment(self.p, environ={"OPENAI_API_KEY": "not ours", "OPTION": "x"})   # the prefix is exact

    def test_the_marker_alone_stops_the_kernel(self):
        self._file("ROMP_EXPECTED_AUTH=key\n")
        Path(self.p + ".source").write_text("command\n")
        with self.assertRaisesRegex(RuntimeError, "retired provider marker"):
            cred.check_boot_environment(self.p, environ={})

    def test_a_retired_name_in_the_kernels_environment_stops_it(self):
        self._file("ROMP_EXPECTED_AUTH=key\n")
        for name in cred.RETIRED_VARS:
            with self.assertRaises(RuntimeError) as cm:
                cred.check_boot_environment(self.p, environ={name: "synthetic-value-never-printed"})
            self.assertIn("the manager's environment carries " + name, str(cm.exception))
            self.assertNotIn("synthetic-value", str(cm.exception))

    def test_an_unreadable_file_is_its_own_loud_failure(self):
        os.makedirs(self.p)          # a directory where the file should be: not readable as a file
        with self.assertRaisesRegex(RuntimeError, "cannot read the service environment file"):
            cred.check_boot_environment(self.p, environ={})

    def test_the_floor_names_cover_every_retired_and_login_name(self):
        for n in ("ROMP_API_KEY_CMD", "ROMP_API_KEY_REF", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                  "CLAUDE_CODE_OAUTH_TOKEN", "ROMP_EXPECTED_AUTH", "OP_SERVICE_ACCOUNT_TOKEN"):
            self.assertIn(n, cred.FLOOR_ENV_NAMES)
        self.assertEqual(cred.FLOOR_ENV_PREFIXES, ("OP_SESSION_",))


class LoginPickSuppressesTheHelper(unittest.TestCase):
    """The per-session settings layer is how a login pick keeps billing the login on a helper box."""

    def test_the_flag_settings_file_carries_the_disable_value_for_a_login_launch(self):
        d = tempfile.mkdtemp()
        p = sb.flag_settings_path(d, SID, no_helper=True)
        self.assertTrue(p, "a login launch always has a settings file")
        self.assertEqual(json.loads(Path(p).read_text()), {"apiKeyHelper": ""})
        self.assertEqual(sb.flag_settings_path(d, SID + "1"), "", "a plain key launch needs no file")
        p2 = sb.flag_settings_path(d, SID + "2", fast=True)
        self.assertNotIn("apiKeyHelper", json.loads(Path(p2).read_text()), "a key launch never disables the helper")

    def test_a_credential_name_in_a_session_env_is_always_refused(self):
        for auth in ("", "login", "key"):
            for name in sb.AUTH_ENV_NAMES:
                err = sb.env_request_error({name: "synthetic"}, auth)
                self.assertIn(name, err)
                self.assertIn("Claude Code's own", err)
        self.assertEqual(sb.env_request_error({"FOO": "bar"}, "key"), "")


class JudgesRunOnClaudeCodesOwnCredential(unittest.TestCase):
    def setUp(self):
        self._login_before = jd._LOGIN_AUTH_ENV_FN
        jd._LOGIN_AUTH_ENV_FN = None
        jd._auth_cache[:] = [None, {}]
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        for p in (jd.JUDGE_AUTH, jd.SDKDIR / (SID + ".json"), jd.STATE / "retry-paused.json"):
            try:
                p.unlink()
            except OSError:
                pass
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
        jd._judge_ctx.paused = False

    def tearDown(self):
        jd._LOGIN_AUTH_ENV_FN = self._login_before
        os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before

    def _reg(self, auth):
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "auth": auth}))

    def test_a_key_billed_call_injects_nothing_even_with_a_key_in_the_ambient_environment(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-ambient", "ANTHROPIC_AUTH_TOKEN": "synthetic-bearer",
                                     "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-op", "OP_SESSION_acct": "synthetic-op2"}):
            env = jd._judge_env("triage", "key")
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                  "OP_SERVICE_ACCOUNT_TOKEN", "OP_SESSION_acct"):
            self.assertNotIn(k, env, k)
        self.assertEqual(env.get("ROMP_SUMMARIZING"), "1", "the rest of the env contract is untouched")

    def test_a_login_billed_call_gets_the_login_tokens_and_the_helper_suppression(self):
        jd._LOGIN_AUTH_ENV_FN = lambda: {"CLAUDE_CODE_OAUTH_TOKEN": "synthetic-login-token"}
        env = jd._judge_env("triage", "login")
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), "synthetic-login-token")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        cmd = jd._judge_cmd("sonnet", "SYS", None, auth="login")
        i = cmd.index("--settings")
        self.assertEqual(json.loads(cmd[i + 1]), {"apiKeyHelper": ""},
                         "the helper outranks the login in the CLI's precedence: disabled for this one call")
        self.assertNotIn("--settings", jd._judge_cmd("sonnet", "SYS", None, auth="key"))
        self.assertNotIn("--settings", jd._judge_cmd("sonnet", "SYS", None))

    def test_the_default_billing_follows_the_helper(self):
        self.assertEqual(jd._judge_auth(SID), "login", "no helper anywhere: the login")
        Path(os.environ["CLAUDE_CONFIG_DIR"], "settings.json").write_text(json.dumps({"apiKeyHelper": "/h.sh"}))
        self.assertEqual(jd._judge_auth(SID), "key", "a helper in Claude Code's settings: the key, read not run")
        self._reg("login")
        jd._auth_cache[:] = [None, {}]
        self.assertEqual(jd._judge_auth(SID), "login", "an explicit pick wins")

    def test_the_first_pass_after_boot_runs_keyless_and_latches_nothing(self):
        """The boot artifact the maintainers saw on 2026-09-08: the first pass after a restart failed 42 calls
        with a remembered-provider error before the second ran clean. There is no remembered anything now."""
        self._reg("key")
        jd._judge_ctx.fsid = SID
        seen = []

        def fake_run(cmd, input=None, env=None, **kw):
            seen.append((cmd, env))
            return SimpleNamespace(stdout=json.dumps({"result": "ok", "usage": {}, "duration_ms": 3}),
                                   stderr="", returncode=0)
        with patch.object(jd, "_judge_engine", return_value="claude"), \
                patch.object(jd.subprocess, "run", side_effect=fake_run):
            out1 = jd._judge_run("sonnet", "SYS", "u", judge="planner", tier="triage")
            out2 = jd._judge_run("sonnet", "SYS", "u", judge="closer", tier="triage")
        self.assertEqual((out1, out2), ("ok", "ok"))
        self.assertEqual(len(seen), 2)
        for cmd, env in seen:
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("--settings", cmd, "a key-billed call: the child resolves the helper itself")
        self.assertEqual(jd._auth_down_map(), {}, "the first call after boot is not an auth failure")
        self.assertFalse(jd._judge_ctx.paused)


class KernelSide(unittest.TestCase):
    def setUp(self):
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
        km.jd._cred.forget_helper_key()

    def tearDown(self):
        os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before
        km.jd._cred.forget_helper_key()

    def test_the_catalog_credential_is_the_helpers_key_or_nothing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            self.assertIsNone(km._models_api_credential(), "no helper, no bearer: the refresh serves the cache and says so")
            script, marker = _helper_script(tempfile.mkdtemp())
            Path(os.environ["CLAUDE_CONFIG_DIR"], "settings.json").write_text(json.dumps({"apiKeyHelper": script}))
            self.assertEqual(km._models_api_credential(), ("x-api-key", HELPER_OUT))
            self.assertEqual(_runs(marker), 1)

    def test_main_runs_the_boot_check_before_anything_spawns(self):
        src = inspect.getsource(km.main)
        self.assertIn("jd._cred.check_boot_environment()", src)
        self.assertLess(src.index("jd._cred.check_boot_environment()"), src.index("_ensure_bundles()"))

    def test_the_kernels_env_validator_refuses_a_credential_name_for_every_pick(self):
        fn = km._env_error                      # the kernel-side twin of sdk_backend.env_request_error
        for auth in ("", "login", "key"):
            err = fn({"ANTHROPIC_API_KEY": "synthetic"}, auth)
            self.assertIn("ANTHROPIC_API_KEY", err)
            self.assertEqual(err, sb.env_request_error({"ANTHROPIC_API_KEY": "synthetic"}, auth), "lockstep")

    def test_no_kernel_module_reads_the_retired_provider_module(self):
        for mod in (km, jd, sb):
            self.assertFalse(hasattr(mod, "_keysrc"), mod.__name__)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "kernel", "keysource.py")))


if __name__ == "__main__":
    unittest.main()
