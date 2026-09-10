#!/usr/bin/env python3
"""ssh advisory chatter must never be mistaken for a failure reason.

OpenSSH 10 prints a post-quantum key-exchange warning to stderr on every connection to an older
server. The connection is fine and stdout is clean, but the remote paths were written as
`proc.stderr or "<helpful fallback>"` — so once that warning existed stderr was always truthy, the
fallback never fired, and EVERY remote failure was reported as the PQ warning instead of its real
cause ("pushes always fail and it's never clear why", the user 2026-07-22).

Two guards: LogLevel=ERROR suppresses the advisories at the source, and _ssh_err() strips whatever
still slips through (a server banner isn't covered by LogLevel) so the helpful fallback survives.

Synthetic only — hermetic temp STATE, placeholder host, no real ssh.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PQ_WARNING = (
    "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
    "** This session may be vulnerable to \"store now, decrypt later\" attacks.\n"
    "** The server may need to be upgraded. See https://openssh.com/pq.html\n"
)


class SshOptsSuppressAdvisories(unittest.TestCase):
    def test_loglevel_error_is_set(self):
        # at the SOURCE: ssh never prints the advisory in the first place
        self.assertIn("LogLevel=ERROR", km._SSH_OPTS)

    def test_loglevel_rides_the_git_push_env_too(self):
        # the p2p push builds GIT_SSH_COMMAND from _SSH_OPTS, so the same suppression applies there
        self.assertIn("LogLevel=ERROR", " ".join(km._SSH_OPTS))


class SshErrFilter(unittest.TestCase):
    def test_pq_warning_alone_is_not_an_error(self):
        self.assertEqual(km._ssh_err(PQ_WARNING), "",
                         "a connection-succeeded advisory must not read as a failure reason")

    def test_fallback_survives_a_noisy_stderr(self):
        # THE regression: `_ssh_err(stderr) or "<fallback>"` must reach the fallback
        self.assertEqual(km._ssh_err(PQ_WARNING) or "couldn't locate the remote romp clone",
                         "couldn't locate the remote romp clone")

    def test_a_real_error_still_comes_through(self):
        real = PQ_WARNING + "Permission denied (publickey).\n"
        self.assertEqual(km._ssh_err(real), "Permission denied (publickey).")

    def test_real_error_without_noise_is_untouched(self):
        self.assertEqual(km._ssh_err("ssh: Could not resolve hostname h: nodename nor servname provided"),
                         "ssh: Could not resolve hostname h: nodename nor servname provided")

    def test_known_hosts_note_is_noise(self):
        self.assertEqual(km._ssh_err("Warning: Permanently added 'h' (ED25519) to the list of known hosts."), "")

    def test_blank_and_none_are_safe(self):
        self.assertEqual(km._ssh_err(None), "")
        self.assertEqual(km._ssh_err(""), "")
        self.assertEqual(km._ssh_err("\n  \n"), "")

    def test_multiline_real_error_is_preserved_in_order(self):
        real = "line one\n" + PQ_WARNING + "line two\n"
        self.assertEqual(km._ssh_err(real), "line one\nline two")


class DivergedMessageIsActionable(unittest.TestCase):
    """The refusal the user actually hit: a rewritten LOCAL history orphans the remote's HEAD, so the
    ancestor guard fires forever. The message must name that cause and give the way out."""

    def _msg(self):
        import subprocess
        from unittest import mock
        # discovery: a clean remote clone whose HEAD differs from local; apply: DIVERGED
        def fake_run(argv, **kw):
            joined = " ".join(argv)
            if argv[0] == "git":                       # the local `git push` to the remote scratch ref
                return subprocess.CompletedProcess(argv, 0, "", PQ_WARNING)
            if "merge-base" in joined:                 # the APPLY leg (checked first: it also says rev-parse)
                return subprocess.CompletedProcess(argv, 0, "DIVERGED\n", PQ_WARNING)
            return subprocess.CompletedProcess(argv, 0, "DIR:/home/u/romp\nHEAD:deadbeef\nDIRTY:\n", PQ_WARNING)
        with mock.patch.object(km.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(km, "_local_head", side_effect=lambda short=False: "cafe1234" * 5 if not short else "cafe123"), \
             mock.patch.object(km, "_fresh_local_head", return_value="cafe1234" * 5):
            return km._update_remote("TESTHOST")

    def test_names_the_rewritten_history_cause_and_the_remedy(self):
        ok, detail = self._msg()
        self.assertFalse(ok)
        self.assertIn("diverged", detail.lower())
        self.assertIn("rewritten", detail.lower(), "must name the rebase/filter-repo cause, not just 'its own commits'")
        self.assertIn("git push --force", detail, "must give the exact way out")
        self.assertNotIn("post-quantum", detail, "the advisory must never leak into the reason")


if __name__ == "__main__":
    unittest.main(verbosity=2)
