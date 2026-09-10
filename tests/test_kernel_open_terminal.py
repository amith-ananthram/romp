"""Click the statusline folder → run a CONFIGURABLE opener for that dir on the kernel's machine (the user
2026-06-27). With no config it uses the OS default opener (`open` on macOS / `xdg-open` on Linux — the one
portable "open this" command); a user overrides via $ROMP_OPEN_FOLDER or ~/.config/romp/open-folder with a
command whose `{dir}` placeholder is substituted (else the path is appended) — e.g. `open -a Ghostty {dir}`.

A REMOTE session's folder click (the user 2026-07-03) must instead SSH into that machine and land in its
cwd — the local opener would run against a path that doesn't exist here (a silent no-op). federation.ts
routes `openFolder` to stay LOCAL with the session id's host prefix left INTACT; the kernel splits it
(_split_host_id) and dispatches to _open_folder_remote instead of _open_folder when a host is present.
SYNTHETIC fixtures; subprocess is stubbed so nothing actually launches."""
import inspect
import os
import sys
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

GOOD = "/tmp/TESTHOST/somedir"


class OpenFolder(unittest.TestCase):
    def setUp(self):
        self._popen, self._isdir = km.subprocess.Popen, km.os.path.isdir
        self._env = dict(os.environ)
        self.calls = []
        km.subprocess.Popen = lambda argv, **kw: self.calls.append(list(argv))
        km.os.path.isdir = lambda p: p == GOOD
        os.environ.pop("ROMP_OPEN_FOLDER", None)
        os.environ["HOME"] = tempfile.mkdtemp()   # isolate ~/.config/romp/open-folder from the real machine

    def tearDown(self):
        km.subprocess.Popen, km.os.path.isdir = self._popen, self._isdir
        os.environ.clear(); os.environ.update(self._env)

    def test_no_config_uses_the_os_default_opener(self):
        km._open_folder(GOOD)
        self.assertEqual(len(self.calls), 1)
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        self.assertEqual(self.calls[0], [opener, GOOD], "the OS default folder opener")

    def test_override_with_dir_placeholder(self):
        os.environ["ROMP_OPEN_FOLDER"] = "open -a Ghostty {dir}"
        km._open_folder(GOOD)
        self.assertEqual(self.calls[0], ["open", "-a", "Ghostty", GOOD], "{dir} is substituted in place")

    def test_override_without_placeholder_appends_the_dir(self):
        os.environ["ROMP_OPEN_FOLDER"] = "code"
        km._open_folder(GOOD)
        self.assertEqual(self.calls[0], ["code", GOOD], "no {dir} → the path is appended as the last arg")

    def test_a_nonexistent_dir_is_a_no_op(self):
        km._open_folder("/no/such/TESTHOST/dir")
        self.assertEqual(self.calls, [])

    def test_blank_cwd_is_a_no_op(self):
        km._open_folder("")
        self.assertEqual(self.calls, [])

    def test_folder_opener_reads_env_override(self):
        os.environ["ROMP_OPEN_FOLDER"] = "  ghostty --working-directory={dir}  "
        self.assertEqual(km._folder_opener(), "ghostty --working-directory={dir}", "trimmed env override")

    def test_dispatch_routes_openFolder(self):
        src = inspect.getsource(km)
        self.assertIn('msg.get("type") == "openFolder" and msg.get("cwd")', src)
        self.assertIn('_open_folder(str(msg["cwd"]))', src)
        self.assertIn('_open_folder_remote(host, str(msg["cwd"]))', src, "a host-prefixed id routes remote")


class SplitHostId(unittest.TestCase):
    def test_a_host_prefixed_id_splits_into_host_and_bare(self):
        self.assertEqual(km._split_host_id("gpu1:11111111-2222-3333-4444-555555555555"),
                         ("gpu1", "11111111-2222-3333-4444-555555555555"))

    def test_a_bare_id_has_no_host(self):
        self.assertEqual(km._split_host_id("11111111-2222-3333-4444-555555555555"),
                         ("", "11111111-2222-3333-4444-555555555555"))

    def test_blank_or_none_is_a_bare_empty_id(self):
        self.assertEqual(km._split_host_id(""), ("", ""))
        self.assertEqual(km._split_host_id(None), ("", ""))


class OpenFolderRemote(unittest.TestCase):
    def setUp(self):
        self._popen = km.subprocess.Popen
        self._env = dict(os.environ)
        self.calls = []
        km.subprocess.Popen = lambda argv, **kw: self.calls.append(list(argv))
        os.environ.pop("ROMP_OPEN_REMOTE_FOLDER", None)
        os.environ.pop("ROMP_OPEN_FOLDER", None)          # the local pref feeds the remote derivation — control it
        os.environ["HOME"] = tempfile.mkdtemp()   # isolate ~/.config/romp/open-{remote-,}folder from the real machine

    def tearDown(self):
        km.subprocess.Popen = self._popen
        os.environ.clear(); os.environ.update(self._env)

    def test_no_host_is_a_no_op(self):
        km._open_folder_remote("", "/work/proj")
        self.assertEqual(self.calls, [])

    def test_default_macos_opens_terminal_via_osascript_running_ssh(self):
        if sys.platform != "darwin":
            self.skipTest("macOS-only default opener")
        km._open_folder_remote("gpu1", "/work/proj")   # no local pref → the built-in Terminal.app default
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        self.assertEqual(argv[0], "osascript")
        joined = " ".join(argv)
        self.assertIn("ssh -t gpu1", joined)
        self.assertIn("cd /work/proj", joined)
        self.assertIn("exec $SHELL -l", joined, "a login shell, not a one-shot command that closes")

    def test_no_remote_config_follows_the_local_ghostty_terminal(self):
        # the user 2026-07-03: with no explicit remote config, an SSH terminal should open in the SAME terminal
        # the local folder pref names (Ghostty), not default to Terminal.app.
        if sys.platform != "darwin":
            self.skipTest("macOS-only derivation")
        os.environ["ROMP_OPEN_FOLDER"] = "open -a Ghostty {dir}"
        km._open_folder_remote("gpu1", "/work/proj")
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        # `open -na Ghostty --args -e ssh …` — -n (new instance) is required so macOS passes --args to Ghostty
        self.assertEqual(argv[:6], ["open", "-na", "Ghostty", "--args", "-e", "ssh"])
        self.assertEqual(argv[6:8], ["-t", "gpu1"])
        self.assertEqual(argv[8], "cd /work/proj 2>/dev/null; exec $SHELL -l", "cd + login shell, one ssh arg")

    def test_editor_local_pref_does_not_derive_a_terminal(self):
        # a NON-terminal local opener (an editor) must NOT be misused as an SSH terminal — fall back to the default
        if sys.platform != "darwin":
            self.skipTest("macOS-only derivation")
        os.environ["ROMP_OPEN_FOLDER"] = "code {dir}"
        km._open_folder_remote("gpu1", "/work/proj")
        self.assertEqual(self.calls[0][0], "osascript", "an editor pref falls back to the Terminal.app default")

    def test_local_terminal_app_extracts_only_known_terminals(self):
        os.environ["ROMP_OPEN_FOLDER"] = "open -a Ghostty {dir}"
        self.assertEqual(km._local_terminal_app(), "Ghostty")
        os.environ["ROMP_OPEN_FOLDER"] = "open -a Alacritty {dir}"
        self.assertEqual(km._local_terminal_app(), "Alacritty")
        os.environ["ROMP_OPEN_FOLDER"] = 'open -a "Visual Studio Code" {dir}'
        self.assertEqual(km._local_terminal_app(), "", "an editor opened via open -a is NOT a terminal")
        os.environ["ROMP_OPEN_FOLDER"] = "code {dir}"
        self.assertEqual(km._local_terminal_app(), "", "a non-`open` opener is not derivable")
        os.environ.pop("ROMP_OPEN_FOLDER", None)
        self.assertEqual(km._local_terminal_app(), "", "no local pref → nothing to derive")

    def test_default_non_macos_uses_xterm(self):
        saved = sys.platform
        try:
            km.sys.platform = "linux"
            km._open_folder_remote("gpu1", "/work/proj")
            self.assertEqual(self.calls[0][:4], ["xterm", "-e", "ssh", "-t"])
            self.assertIn("gpu1", self.calls[0])
            self.assertTrue(any("cd /work/proj" in a for a in self.calls[0]))
        finally:
            km.sys.platform = saved

    def test_override_with_host_and_dir_placeholders(self):
        os.environ["ROMP_OPEN_REMOTE_FOLDER"] = "open -a Ghostty --args -e ssh -t {host} cd-to:{dir}"
        km._open_folder_remote("gpu1", "/work/proj")
        self.assertEqual(self.calls[0],
                          ["open", "-a", "Ghostty", "--args", "-e", "ssh", "-t", "gpu1", "cd-to:/work/proj"])

    def test_blank_dir_falls_back_to_home(self):
        os.environ["ROMP_OPEN_REMOTE_FOLDER"] = "term --ssh {host} --dir {dir}"
        km._open_folder_remote("gpu1", "")
        self.assertEqual(self.calls[0], ["term", "--ssh", "gpu1", "--dir", "~"])

    def test_remote_folder_opener_reads_env_override(self):
        os.environ["ROMP_OPEN_REMOTE_FOLDER"] = "  term --ssh {host} {dir}  "
        self.assertEqual(km._remote_folder_opener(), "term --ssh {host} {dir}", "trimmed env override")


class OpenFolderDispatchesByHost(unittest.TestCase):
    """The actual WS handler: a bare id opens LOCALLY, a host-prefixed id SSHes out instead — the split
    that federation.ts's routeOutbound sets up by leaving a remote id's host prefix intact (2026-07-03)."""

    def setUp(self):
        self._local_calls, self._remote_calls = [], []
        self._saved = (km._open_folder, km._open_folder_remote)
        km._open_folder = lambda cwd: self._local_calls.append(cwd)
        km._open_folder_remote = lambda host, cwd: self._remote_calls.append((host, cwd))

    def tearDown(self):
        km._open_folder, km._open_folder_remote = self._saved

    def _dispatch(self, msg):
        host, _bare = km._split_host_id(str(msg.get("id") or ""))
        if host:
            km._open_folder_remote(host, str(msg["cwd"]))
        else:
            km._open_folder(str(msg["cwd"]))

    def test_a_bare_id_opens_locally(self):
        self._dispatch({"type": "openFolder", "cwd": "/work/proj", "id": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual(self._local_calls, ["/work/proj"])
        self.assertEqual(self._remote_calls, [])

    def test_a_host_prefixed_id_sshes_out_instead(self):
        self._dispatch({"type": "openFolder", "cwd": "/work/proj", "id": "gpu1:11111111-2222-3333-4444-555555555555"})
        self.assertEqual(self._remote_calls, [("gpu1", "/work/proj")])
        self.assertEqual(self._local_calls, [])

    def test_no_id_at_all_still_opens_locally(self):
        self._dispatch({"type": "openFolder", "cwd": "/work/proj"})
        self.assertEqual(self._local_calls, ["/work/proj"])
        self.assertEqual(self._remote_calls, [])


if __name__ == "__main__":
    unittest.main()
