"""The boot line naming credential-shaped names in the kernel's own environment that reach every
session. All values here are synthetic; no backend construction dials a real kernel (the ports are
poisoned at import, matching conftest). romp holds no key of its own (kernel/credentials.py), so the
variables are staged straight into os.environ: nothing of romp's claims them but the login tokens. The
boot check (credentials.check_boot_environment, run by kernel.main() before a backend exists and pinned in
tests/test_credentials.py, BootCheck and KernelSide) never runs here, so a backend built directly sees
what is staged; no retired provider name is staged anywhere in this module."""
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import types
import unittest
from unittest.mock import patch
from romp_load import load_source

ROOT = Path(__file__).resolve().parents[1]
_IMPORT_STATE = tempfile.mkdtemp(prefix="romp-envnames-")
os.environ["XDG_STATE_HOME"] = _IMPORT_STATE
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_MANAGER_PORT"] = "1"          # never dial the real manager (restart-all)
os.environ["ROMP_KERNEL_PORT"] = "1"           # never dial the real kernel
os.environ["ROMP_SERVICE_ENV_FILE"] = _IMPORT_STATE + "/absent.env"
os.environ["ROMP_SERVICE_ENV"] = os.environ["ROMP_SERVICE_ENV_FILE"]
sb = load_source("romp_sdk_envnames", str(ROOT / "kernel/sdk_backend.py"))


def _op_name(n):
    """The 1Password CLI's own names as credentials.py classifies them (is_op_env_name): the boot line's op
    shape is that classifier, the one the boot check refuses and the test floor pops."""
    return sb._cred.is_op_env_name(n)


def _shaped(n):
    return n.endswith("_API_KEY") or n.endswith("_TOKEN") or _op_name(n)


class PureNames(unittest.TestCase):
    def names(self, env):
        return sb.env_credential_names(env)

    def test_api_key_and_token_names_are_named(self):
        env = {"EXAMPLE_API_KEY": "x", "OPENAI_API_KEY": "y", "HF_TOKEN": "z", "MY_SECRET_TOKEN": "q"}
        self.assertEqual(self.names(env), ["EXAMPLE_API_KEY", "HF_TOKEN", "MY_SECRET_TOKEN", "OPENAI_API_KEY"])

    def test_the_control_token_is_the_one_exclusion(self):
        env = {"ROMP_SERVE_TOKEN": "control", "OPENROUTER_API_KEY": "r"}
        self.assertEqual(self.names(env), ["OPENROUTER_API_KEY"],
                         "romp's own control token is not a provider credential; nothing else is excluded by name")

    def test_a_login_token_still_present_is_named(self):
        """The helper excludes no name the boot claim removes: a login token still in the environment when it
        runs did reach sessions, so it is named, and the call's place after startup_auth_env (BootWiring
        below) is what keeps one off the line. The key name is named too: the boot check
        (credentials.check_boot_environment), not this helper, is what keeps a key out of the environment."""
        env = {n: "v" for n in sb.AUTH_ENV_NAMES}
        self.assertEqual(self.names(env), sorted(sb.AUTH_ENV_NAMES))

    def test_empty_or_whitespace_values_are_not_named(self):
        self.assertEqual(self.names({"FOO_API_KEY": "", "BAR_TOKEN": "   ", "BAZ_API_KEY": "v"}), ["BAZ_API_KEY"])

    def test_non_credential_names_are_ignored(self):
        self.assertEqual(self.names({"ROMP_PERF": "1", "PATH": "/bin", "EDITOR_TOKENIZER": "x"}), [])

    def test_op_names_are_named_as_credentials_classifies_them(self):
        """OP_SESSION_<account> (what `op signin` exports) ends in neither suffix, yet credentials.py classifies
        it (is_op_env_name, beside the service-account and Connect names in OP_ENV_NAMES) as the 1Password
        CLI's own and refuses it at boot; the helper names exactly what that classifier accepts, so the boot
        line and the boot check agree on what an op name is."""
        env = {"OP_SESSION_TESTACCT": "s", "OP_CONNECT_HOST": "h", "OP_ACCOUNT": "a",
               "OP_SERVICE_ACCOUNT_TOKEN": "t", "OPTIONS_FOR_X": "not an op name"}
        self.assertEqual(self.names(env), ["OP_ACCOUNT", "OP_CONNECT_HOST", "OP_SERVICE_ACCOUNT_TOKEN",
                                           "OP_SESSION_TESTACCT"])
        for n in self.names(env):
            self.assertTrue(_op_name(n), n)
        self.assertEqual(self.names({"OP_SESSION_TESTACCT": ""}), [], "an empty value is not a leak")

    def test_the_return_is_names_never_values(self):
        env = {"OPENAI_API_KEY": "plain-oai-must-not-appear"}
        out = self.names(env)
        self.assertEqual(out, ["OPENAI_API_KEY"])
        self.assertNotIn("plain-oai-must-not-appear", " ".join(out))


class BootNoticeMethod(unittest.TestCase):
    """The method in isolation: __new__ skips __init__, so no thread, no state dir, no kernel."""

    def setUp(self):
        patch.object(sb, "_ENV_CRED_NAMES_SAID", False).start()
        self.addCleanup(patch.stopall)
        self.be = sb.SdkBackend.__new__(sb.SdkBackend)
        self.logs = []
        self.be._log = lambda m, problem=None: self.logs.append((m, problem))

    def test_says_once_naming_names_not_values(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "value-oai-synth", "HF_TOKEN": "value-hf-synth"}, clear=False):
            self.be._note_env_credential_names()
            self.be._note_env_credential_names()
        rows = [m for m, _ in self.logs if "reach every session" in m]
        self.assertEqual(len(rows), 1, "one line per process")
        self.assertIn("OPENAI_API_KEY", rows[0])
        self.assertIn("HF_TOKEN", rows[0])
        self.assertNotIn("value-oai-synth", rows[0])
        self.assertNotIn("value-hf-synth", rows[0])
        self.assertIs(self.logs[0][1], False, "filed as information explicitly, never left to _log's default")

    def test_quiet_when_no_credential_names(self):
        with patch.dict(os.environ, {}, clear=False):       # restores every popped name on exit
            for n in list(os.environ):
                if _shaped(n):
                    os.environ.pop(n, None)
            self.be._note_env_credential_names()
        self.assertEqual([m for m, _ in self.logs if "reach every session" in m], [])


class BootWiring(unittest.TestCase):
    """A constructed backend runs the notice from __init__. Ports poisoned at import; state dir empty. The
    login-token claim (startup_auth_env) is once per process, so its memory is reset per test and every
    build runs inside patch.dict, which puts back what the claim popped."""

    def setUp(self):
        patch.object(sb, "_ENV_CRED_NAMES_SAID", False).start()
        patch.object(sb, "_STARTUP_AUTH_ENV", None).start()
        self.addCleanup(patch.stopall)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        fake = ModuleType("claude_agent_sdk")
        # an attribute-bearing stand-in, like the SDK's dataclass: with hosts on (the default since T348) the
        # options loop sets each matcher's `timeout` to the host's hook bound, which a plain dict refused
        fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
        fake.ClaudeAgentOptions = dict
        fake.ClaudeSDKClient = unittest.mock.Mock()
        for n in ("AssistantMessage", "ResultMessage", "SystemMessage"):
            setattr(fake, n, type(n, (), {}))
        patch.dict(sys.modules, {"claude_agent_sdk": fake}).start()

    def build(self):
        logs = []
        be = sb.SdkBackend(str(Path(self.tmp.name) / "state"), "/bin/true",
                           lambda *a, **k: None, log=logs.append)
        return be, logs

    def test_init_names_a_synthetic_provider_key_once(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "value-oai-init"}, clear=False):
            _, logs = self.build()
        rows = [m for m in logs if "reach every session" in m]
        self.assertEqual(len(rows), 1)
        self.assertIn("OPENAI_API_KEY", rows[0])
        self.assertNotIn("value-oai-init", rows[0])

    def test_boot_line_is_never_a_problem_row(self):
        """Informational on the wired path: the row reaches the log and NOT the problem ring the dashboard's
        error center reads. Built inside an except block on purpose: _log's default classification files
        any line logged while an exception is being handled, so an implicit problem=None would file this
        one whenever a boot happens on a handler's retry path. The explicit problem=False is what this
        pins; the synthetic exception below is never raised past the handler."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "value-oai-ring"}, clear=False):
            try:
                raise RuntimeError("synthetic: a boot inside an exception handler")
            except RuntimeError:
                be, logs = self.build()
        rows = [m for m in logs if "reach every session" in m]
        self.assertEqual(len(rows), 1)
        self.assertEqual([p["text"] for p in be.problems(50) if "reach every session" in p["text"]], [],
                         "the boot line is information, never a problem row")

    def test_op_names_are_named_on_the_wired_path(self):
        """The op shape on the wired path: a backend built with 1Password's names in the environment names
        them, the service-account token and the `op signin` session token both, beside a second provider's
        key, and the values never appear. romp runs no `op` of its own and claims none of these names
        (credentials.py); in production the boot check refuses them before kernel.main() builds a backend,
        so a backend that sees them was built outside it, and the line says what such a backend's sessions
        inherit."""
        env = {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-op-helper", "OP_SESSION_TESTACCT": "synthetic-op-session",
               "OPENAI_API_KEY": "value-oai-helper"}
        with patch.dict(os.environ, env, clear=False):
            _, logs = self.build()
            self.assertIn("OP_SERVICE_ACCOUNT_TOKEN", os.environ, "nothing of romp's claims op's names")
        rows = [m for m in logs if "reach every session" in m]
        self.assertEqual(len(rows), 1)
        for name in env:
            self.assertIn(name, rows[0])
        for value in env.values():
            self.assertNotIn(value, rows[0])

    def test_login_tokens_claimed_at_boot_are_not_named(self):
        """startup_auth_env pops the login tokens out of os.environ at the top of __init__, before the notice
        runs, so they are not named: sessions never inherit them (a login launch gets them back explicitly).
        The helper itself excludes neither name (PureNames above), so this is the call order in __init__
        and nothing else: moving the notice above startup_auth_env turns it red."""
        env = {"ANTHROPIC_AUTH_TOKEN": "synthetic-bearer", "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth",
               "OPENAI_API_KEY": "value-oai-login"}
        with patch.dict(os.environ, env, clear=False):
            _, logs = self.build()
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", os.environ, "claimed for login launches only")
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", os.environ, "claimed for login launches only")
        rows = [m for m in logs if "reach every session" in m]
        self.assertEqual(len(rows), 1)
        self.assertIn("OPENAI_API_KEY", rows[0])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", rows[0])
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", rows[0])
        for value in env.values():
            self.assertNotIn(value, rows[0])


if __name__ == "__main__":
    unittest.main()
