#!/usr/bin/env python3
"""Three gaps a fresh attach-bootstrapped machine showed in the 2026-07-27 federation shakedown:
  1. Nothing started its postal bus — only a session's MCP server does, lazily, so a sessionless box
     ran a kernel while staying postal-invisible. The kernel now insists at every boot.
  2. Sessions spawned by that kernel inherited its non-login PATH, without the repo's bin/ — the
     postal MCP command and `romp mail send` died command-not-found. The SDK options now overlay PATH.
  3. `romp mail send` printed "delivered" for a cross-host message that was merely relaying (and may
     be quarantined at the receiver). The CLI now echoes the bus's own routing note.

Synthetic only — hermetic temp STATE, placeholder hosts, no processes actually spawned."""
import io
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_freshfed", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_freshfed",
                      os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))
ps = load_source("romp_postal_freshfed", os.path.join(BIN, "romp-postal-service"))


class KernelEnsuresBus(unittest.TestCase):
    def test_boot_runs_postal_ensure_by_absolute_path(self):
        calls = []
        saved = km.subprocess.run
        km.subprocess.run = lambda argv, **kw: calls.append(argv)
        try:
            km._ensure_postal_bus()
        finally:
            km.subprocess.run = saved
        self.assertEqual(len(calls), 1)
        argv = [str(a) for a in calls[0]]
        self.assertTrue(argv[1].endswith("bin/romp-postal-service"),
                        "absolute path — a bootstrap-started kernel's PATH has no repo bin/: %s" % argv)
        self.assertEqual(argv[2], "ensure")

    def test_a_failing_ensure_never_raises(self):
        saved = km.subprocess.run
        km.subprocess.run = lambda *a, **kw: (_ for _ in ()).throw(OSError("no python"))
        try:
            km._ensure_postal_bus()   # must swallow and log, not kill the boot thread
        finally:
            km.subprocess.run = saved

    def test_main_starts_the_ensure_thread(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()
        self.assertIn("threading.Thread(target=_ensure_postal_bus, daemon=True).start()", src,
                      "boot wires the ensure — the function alone fixes nothing")


class SpawnedSessionPath(unittest.TestCase):
    def setUp(self):
        self.rbin = str(Path(os.path.dirname(HERE)) / "bin")

    def test_a_binless_path_gains_the_repo_bin_first(self):
        env = sb._bin_on_path_env({"PATH": "/usr/bin:/bin"})
        self.assertEqual(env["PATH"], self.rbin + os.pathsep + "/usr/bin:/bin",
                         "prepended, so the repo's own tools win")

    def test_a_path_already_carrying_bin_is_left_alone(self):
        cur = self.rbin + os.pathsep + "/usr/bin"
        self.assertEqual(sb._bin_on_path_env({"PATH": cur}), {},
                         "no overlay at all — nothing to change")

    def test_an_empty_environment_still_yields_the_bin(self):
        self.assertEqual(sb._bin_on_path_env({}), {"PATH": self.rbin})

    def test_options_pass_the_overlay(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read_text()
        self.assertIn('env={**_bin_on_path_env(os.environ), "ROMP_SID": str(sess.sid),', src,
                      "_options wires the overlay through the SDK's designed env field")


class CliSendEchoesRouting(unittest.TestCase):
    def _send(self, resp):
        saved_http, saved_ensure = ps._http, ps.ensure
        saved_identity = ps._self_identity
        # the sender must be IDENTIFIED (2026-08-18: an anonymous send is refused before _http —
        # the guard these routing tests would otherwise trip in CI, where no session env exists);
        # this class tests echo ROUTING, and its scenario always implied an identified sender
        ps._self_identity = lambda: ("uuid-a", "alpha")     # cli_send resolves both halves in one call (2026-09-06)
        ps.ensure = lambda: True
        ps._http = lambda method, path, payload=None: resp
        out = io.StringIO()
        saved_out = ps.sys.stdout
        ps.sys.stdout = out
        try:
            rc = ps.cli_send(["--kind", "coordinate", "web", "synthetic test body"])
        finally:
            ps.sys.stdout = saved_out
            ps._http, ps.ensure = saved_http, saved_ensure
            ps._self_identity = saved_identity
        return rc, out.getvalue()

    def test_local_delivery_still_reads_delivered(self):
        rc, out = self._send({"ok": True, "id": "m1"})
        self.assertEqual(rc, 0)
        self.assertIn("delivered to 'web'", out)

    def test_a_cross_host_relay_says_relaying_not_delivered(self):
        rc, out = self._send({"ok": True, "id": "px-1", "note": "relaying to 'web' on boxalias"})
        self.assertEqual(rc, 0)
        self.assertIn("relaying to 'web' on boxalias", out)
        self.assertNotIn("delivered", out, "the receiving bus may still hold it — never claim delivery")

    def test_a_parked_send_says_parked(self):
        rc, out = self._send({"ok": True, "id": "px-2", "parked": "boxalias",
                              "note": "parked for boxalias (unreachable) — delivers on reconnect, "
                                      "or bounces back to you"})
        self.assertEqual(rc, 0)
        self.assertIn("parked for boxalias", out)


if __name__ == "__main__":
    unittest.main()
