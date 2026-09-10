#!/usr/bin/env python3
"""Remote romp-clone discovery reads the remote's OWN authority, never just a guess list. The old
probes searched four hardcoded dirs, so a clone at ~/projects/romp reported "romp not installed"
while that machine's kernel was literally up (the user 2026-08-11, who called the hardcoding
wrong). Now every kernel persists its repo root into state at boot (state/romp/repo-root), and the
probes (_start_remote_kernel, _discover_remote_clone) consult, in order: ROMP_REPO_ROOT on the
target, the repo-root state file, romp-serve on PATH (non-login, then a login shell), then the
conventional dirs — ~/projects/romp included — and a miss names everything tried.

These tests execute the REAL ssh snippets: SSH_BIN is swapped for a stub that runs the probe
command locally under a synthetic fixture HOME (env -i, so nothing of the test machine leaks in).
Synthetic paths only; no real machine data.
"""
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_clonedisc", os.path.join(BIN, "romp-kernel"))


def _mk_clone(root):
    """A minimal thing the probes recognize as a romp clone: bin/romp-serve (executable stub — the
    start path nohups it, so it must run and exit clean) and a .git dir."""
    os.makedirs(os.path.join(root, "bin"), exist_ok=True)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    sh = os.path.join(root, "bin", "romp-serve")
    with open(sh, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(sh, 0o755)
    return root


def _state_file(home, repo_root):
    d = os.path.join(home, ".local", "state", "romp")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "repo-root"), "w") as f:
        f.write(repo_root + "\n")


def _fake_ssh(home, extra_env=""):
    """An SSH_BIN stand-in: ignore every ssh arg, execute the probe command (always the last arg)
    locally under the fixture HOME with a scrubbed environment — the probe's shell logic runs for
    real, against a filesystem the test fully controls."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ssh")
    with open(p, "w") as f:
        f.write('#!/usr/bin/env bash\n'
                'for last in "$@"; do :; done\n'
                'exec env -i HOME="%s" PATH="/usr/bin:/bin" %s bash -c "$last"\n' % (home, extra_env))
    os.chmod(p, 0o755)
    return p


class _ProbeCase(unittest.TestCase):
    def _use(self, home, extra_env=""):
        self._saved = km.SSH_BIN
        km.SSH_BIN = _fake_ssh(home, extra_env)
        self.addCleanup(lambda: setattr(km, "SSH_BIN", self._saved))


class RepoRootStateFile(_ProbeCase):
    def test_state_file_wins_over_the_candidate_dirs(self):
        home = tempfile.mkdtemp()
        real = _mk_clone(os.path.join(home, "weird", "spot", "romp"))   # nowhere a guess would look
        _mk_clone(os.path.join(home, "projects", "romp"))               # decoy in the candidate list
        _state_file(home, real)
        self._use(home)
        rdir, _, _, err = km._discover_remote_clone("TESTHOST")
        self.assertEqual((rdir, err), (real, ""))
        ok, detail = km._start_remote_kernel("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertEqual(detail, os.path.join(real, "bin", "romp-serve"))

    def test_target_env_override_beats_the_state_file(self):
        home = tempfile.mkdtemp()
        enved = _mk_clone(os.path.join(home, "override", "romp"))
        _state_file(home, _mk_clone(os.path.join(home, "stale", "romp")))
        self._use(home, extra_env='ROMP_REPO_ROOT="%s"' % enved)
        rdir, _, _, err = km._discover_remote_clone("TESTHOST")
        self.assertEqual((rdir, err), (enved, ""))

    def test_a_stale_state_file_falls_through_to_the_dirs(self):
        home = tempfile.mkdtemp()
        _state_file(home, os.path.join(home, "moved-away", "romp"))     # points at nothing
        want = _mk_clone(os.path.join(home, "projects", "romp"))
        self._use(home)
        rdir, _, _, err = km._discover_remote_clone("TESTHOST")
        self.assertEqual((rdir, err), (want, ""))

    def test_the_kernel_persists_its_own_repo_root_at_boot(self):
        # the writer half of the contract: what THIS kernel writes is what a peer's probe reads.
        # Re-run the writer the module-level boot call already ran once — another suite's SPAWNED
        # kernel inherits this process's XDG env and can stomp the boot-time copy mid-run.
        f = km.jd.STATE / "repo-root"
        f.unlink(missing_ok=True)
        km._persist_repo_root()
        self.assertEqual(f.read_text().strip(), str(km.ROOT))


class CandidateDirs(_ProbeCase):
    def test_projects_romp_is_now_a_candidate(self):
        home = tempfile.mkdtemp()
        want = _mk_clone(os.path.join(home, "projects", "romp"))        # the dir the old list missed
        self._use(home)
        rdir, _, _, err = km._discover_remote_clone("TESTHOST")
        self.assertEqual((rdir, err), (want, ""))
        ok, detail = km._start_remote_kernel("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertEqual(detail, os.path.join(want, "bin", "romp-serve"))


class LoginShellPath(_ProbeCase):
    def test_login_shell_path_finds_the_bin_and_resolves_its_repo(self):
        # a non-login ssh shell misses the user's PATH additions; the probe retries via `bash -l`,
        # which reads the fixture's .bash_profile — and the found bin is readlink-resolved to the
        # clone that holds it, so the git half works from a PATH hit too
        home = tempfile.mkdtemp()
        clone = _mk_clone(os.path.join(home, "tucked", "romp"))
        os.makedirs(os.path.join(home, "mybin"), exist_ok=True)
        os.symlink(os.path.join(clone, "bin", "romp-serve"), os.path.join(home, "mybin", "romp-serve"))
        with open(os.path.join(home, ".bash_profile"), "w") as f:
            f.write('PATH="$HOME/mybin:$PATH"\n')
        self._use(home)
        ok, detail = km._start_remote_kernel("TESTHOST")
        self.assertTrue(ok, detail)
        self.assertTrue(detail.endswith("romp-serve"), detail)
        rdir, _, _, err = km._discover_remote_clone("TESTHOST")
        # realpath both sides: readlink -f canonicalizes (macOS /var → /private/var)
        self.assertEqual((os.path.realpath(rdir), err), (os.path.realpath(clone), ""))


class MissIsLoud(_ProbeCase):
    def test_noromp_names_every_source_it_tried(self):
        home = tempfile.mkdtemp()                                       # nothing romp-ish at all
        self._use(home)
        rdir, _, _, err = km._discover_remote_clone("TESTHOST")
        self.assertEqual(rdir, "")
        self.assertIn("not installed", err)
        for named in ("repo-root", "PATH", "login shell", "~/projects/romp", "~/GitRepos/romp"):
            self.assertIn(named, err, "the discover miss must name %r" % named)
        ok, detail = km._start_remote_kernel("TESTHOST")
        self.assertFalse(ok)
        self.assertIn("not installed", detail)
        for named in ("repo-root", "PATH", "login shell", "~/projects/romp", "./install.sh"):
            self.assertIn(named, detail, "the start miss must name %r" % named)


if __name__ == "__main__":
    unittest.main(verbosity=2)
