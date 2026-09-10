"""Start a downed remote kernel from the popover (the user 2026-07-10): an ssh-reachable host whose
kernel isn't answering ('no-kernel') gets an explicit ASK — a Start button — and accepting it updates
the remote to this machine's committed code FIRST (the p2p push, which itself reboots the kernel on a
sync), then boots the kernel plainly when the code was already current. Never auto-starts: a stopped
kernel may be stopped on purpose, so the click is the consent. SYNTHETIC hosts; every ssh seam stubbed."""
import inspect
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_start", os.path.join(BIN, "romp-kernel"))

HOST = "TESTHOST"


class StartRemote(unittest.TestCase):
    def setUp(self):
        self._saved = (km._update_remote, km._remote_kernel_up, km._start_remote_kernel,
                       km._fetch_remote_token, km._BOOT_WAIT_S, km._remotes)
        km._remotes = {HOST: {"host": HOST, "kernel_port": 29855, "local_port": 8801,
                              "token": "", "proc": None, "status": "no-kernel", "detail": "", "sids": []}}
        km._BOOT_WAIT_S = 2
        self.kernel_up = {"v": False}
        self.started = []
        km._remote_kernel_up = lambda h, p: self.kernel_up["v"]
        km._start_remote_kernel = lambda h: (self.started.append(h) or self._start_result())
        km._fetch_remote_token = lambda h: "tok-fresh"

    def tearDown(self):
        (km._update_remote, km._remote_kernel_up, km._start_remote_kernel,
         km._fetch_remote_token, km._BOOT_WAIT_S, km._remotes) = self._saved

    def _start_result(self):
        self.kernel_up["v"] = True
        return True, "STARTED"

    def test_no_host_is_a_no_op(self):
        self.assertEqual(km._start_remote(""), (False, "no host"))

    def test_up_to_date_host_gets_a_plain_boot_and_a_fresh_token(self):
        km._update_remote = lambda h: (True, "already up to date (abc1234)")
        ok, detail = km._start_remote(HOST)
        self.assertTrue(ok)
        self.assertEqual(self.started, [HOST], "nothing synced -> nothing rebooted -> plain start needed")
        self.assertIn("started the kernel", detail)
        r = km._remotes[HOST]
        self.assertEqual(r["token"], "tok-fresh", "a first-ever kernel just wrote its serve-token")
        self.assertFalse(r["booting"], "the in-flight flag clears so the supervisor owns the row again")
        self.assertEqual(r["detail"], "")

    def test_a_synced_host_reboots_via_the_update_itself(self):
        km._update_remote = lambda h: (True, "synced to abc1234 + restarting")
        self.kernel_up["v"] = True                        # the update's manager-ensure brought it back
        ok, detail = km._start_remote(HOST)
        self.assertTrue(ok)
        self.assertEqual(self.started, [], "the sync already restarted the kernel — no second boot")
        self.assertIn("synced", detail)

    def test_a_refused_update_fails_loudly_and_never_boots_stale_code(self):
        km._update_remote = lambda h: (False, "remote %s has uncommitted changes" % HOST)
        ok, detail = km._start_remote(HOST)
        self.assertFalse(ok)
        self.assertIn("uncommitted changes", detail)
        self.assertEqual(self.started, [], "a refused update must not fall through to booting old code")
        r = km._remotes[HOST]
        self.assertEqual(r["status"], "no-kernel")
        self.assertIn("uncommitted changes", r["detail"], "the popover row carries the refusal")
        self.assertFalse(r["booting"])

    def test_a_failed_boot_surfaces_its_detail(self):
        km._update_remote = lambda h: (True, "already up to date (abc1234)")
        km._start_remote_kernel = lambda h: (False, "romp not installed on %s" % HOST)
        ok, detail = km._start_remote(HOST)
        self.assertFalse(ok)
        self.assertIn("not installed", detail)

    def test_a_port_that_never_answers_fails_after_the_wait(self):
        km._update_remote = lambda h: (True, "already up to date (abc1234)")
        km._start_remote_kernel = lambda h: (True, "STARTED")   # claims started, port stays dead
        km._BOOT_WAIT_S = 0
        ok, detail = km._start_remote(HOST)
        self.assertFalse(ok)
        self.assertIn("never answered", detail)
        self.assertFalse(km._remotes[HOST]["booting"])

    def test_start_marks_the_row_starting_while_in_flight(self):
        seen = {}
        def upd(h):
            seen["status"], seen["booting"] = km._remotes[HOST]["status"], km._remotes[HOST]["booting"]
            return True, "synced to abc1234 + restarting"
        km._update_remote = upd
        self.kernel_up["v"] = True
        km._start_remote(HOST)
        self.assertEqual(seen["status"], "starting", "the popover shows the boot phase, not stale no-kernel")
        self.assertTrue(seen["booting"])

    def test_an_escaped_exception_releases_the_hold_and_parks_the_failure(self):
        # Every planned failure returns through _fail; an exception a step lets escape is the exit
        # nothing planned for. The hold must not outlive the call: while it stands the supervisor
        # skips its status write and the no-kernel hint, so the row never reads up or no-kernel again,
        # no rendered row shows a Start button, and only a kernel restart's load reset frees it.
        def boom(h):
            raise RuntimeError("stand-in step failed")
        km._update_remote = boom
        with self.assertRaises(RuntimeError):
            km._start_remote(HOST)              # the exception still propagates to the route
        r = km._remotes[HOST]
        self.assertFalse(r["booting"], "the hold must not outlive the call")
        self.assertEqual(r["status"], "no-kernel", "the row reads the settled state, so the popover shows Start again")
        self.assertEqual(r["detail"], "Start on %s failed unexpectedly (RuntimeError): stand-in step failed" % HOST,
                         "the popover row carries the failure, like every failed Start")
        self.assertEqual(self.started, [], "the boot leg never ran; the update leg raised first")

    def test_the_recovery_counter_moves_on_the_first_answered_pass_after_an_escaped_exception(self):
        # _note_recovery skips its bump under the hold (T291b), so a hold left standing also froze the
        # row's recovery counter; once the hold is released the first answered pass on the not-up row
        # bumps once, the one recovery of that Start
        def boom(h):
            raise RuntimeError("stand-in step failed")
        km._update_remote = boom
        with self.assertRaises(RuntimeError):
            km._start_remote(HOST)
        r = km._remotes[HOST]
        r["misses"] = 0
        self.assertTrue(km._note_recovery(r, "up"), "no hold left standing to freeze the recovery counter")
        self.assertEqual(r["upSeq"], 1)

    def test_an_escaped_exception_in_the_boot_leg_releases_the_hold_too(self):
        km._update_remote = lambda h: (True, "already up to date (abc1234)")
        def boom(h):
            raise RuntimeError()                # no message: the detail ends at the type
        km._start_remote_kernel = boom          # kernel_up stays False, so the boot leg runs
        with self.assertRaises(RuntimeError):
            km._start_remote(HOST)
        r = km._remotes[HOST]
        self.assertFalse(r["booting"])
        self.assertEqual(r["status"], "no-kernel")
        self.assertEqual(r["detail"], "Start on %s failed unexpectedly (RuntimeError)" % HOST)

    def test_an_exit_that_is_not_an_exception_releases_the_hold_too(self):
        # except BaseException, not Exception: a SystemExit or KeyboardInterrupt in the handler thread
        # releases the hold the same way, and the parked detail names the type
        def boom(h):
            raise SystemExit("stand-in exit")
        km._update_remote = boom
        with self.assertRaises(SystemExit):
            km._start_remote(HOST)
        r = km._remotes[HOST]
        self.assertFalse(r["booting"], "the hold must not outlive a non-Exception exit either")
        self.assertEqual(r["status"], "no-kernel")
        self.assertEqual(r["detail"], "Start on %s failed unexpectedly (SystemExit): stand-in exit" % HOST)

    def test_a_long_failure_message_is_stripped_and_cut_in_the_parked_detail(self):
        def boom(h):
            raise RuntimeError("  " + "x" * 400 + "  ")
        km._update_remote = boom
        with self.assertRaises(RuntimeError):
            km._start_remote(HOST)
        self.assertEqual(km._remotes[HOST]["detail"],
                         "Start on %s failed unexpectedly (RuntimeError): %s" % (HOST, "x" * 160))


class SupervisorRespectsBoot(unittest.TestCase):
    def test_supervisor_defers_to_an_in_flight_start(self):
        # the poll still sees no-kernel until the boot lands; without the `booting` guard the row
        # flickered red mid-Start and the detail was overwritten
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('not r.get("booting")', src)
        self.assertIn('elif st == "no-kernel" and not r.get("booting"):', src)

    def test_no_kernel_detail_names_the_start_button(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("Start pushes this", src, "the detail points at the popover's Start ask")

    def test_supervisor_keeps_a_specific_parked_failure(self):
        # a refused update / dead boot parks its specific reason on the row; the generic
        # "no kernel answering" hint must not clobber it one tick later (the user 2026-07-11)
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('if not cur or cur.startswith("no kernel answering")', src)


class RemotesLoadHygiene(unittest.TestCase):
    def test_booting_never_survives_a_kernel_restart(self):
        # `booting` is the transient in-flight Start flag; persisted mid-boot and reloaded, it would
        # freeze the row's status against the supervisor forever (nothing would ever clear it)
        import json
        import tempfile
        from pathlib import Path
        saved_file, saved_remotes = km.REMOTES_FILE, km._remotes
        try:
            with tempfile.TemporaryDirectory() as td:
                km.REMOTES_FILE = Path(td) / "remotes.json"
                km.REMOTES_FILE.write_text(json.dumps([{
                    "host": HOST, "kernel_port": 29855, "local_port": 8801, "token": "t",
                    "status": "starting", "detail": "updating + starting the kernel",
                    "booting": True, "sids": []}]))
                km._remotes = {}
                km._remotes_load()
                r = km._remotes[HOST]
                self.assertFalse(r["booting"], "the in-flight flag resets on load")
                self.assertEqual(r["status"], "down", "status resets — the supervisor re-derives it")
        finally:
            km.REMOTES_FILE, km._remotes = saved_file, saved_remotes


class StartEndpoint(unittest.TestCase):
    def test_post_tunnels_start_calls_start_remote_and_reports(self):
        src = inspect.getsource(km)
        self.assertIn('if u.path == "/tunnels/start":', src)
        self.assertIn("ok, detail = _start_remote(host)", src)
        self.assertIn("200 if ok else 502", src)


class StartUI(unittest.TestCase):
    def test_popover_offers_start_only_for_no_kernel_rows(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn(">Start</button>", js)
        self.assertIn("data-s=", js)
        self.assertIn("/tunnels/start", js)
        self.assertIn("t.status==='no-kernel'", js, "the ask appears exactly when ssh is up but no kernel answers")

    def test_start_button_acknowledges_and_fails_loudly(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("Starting\\u2026", js, "immediate acknowledgement on click")
        self.assertIn("Start on '+h+' failed", js, "a specific, loud failure")

    def test_no_kernel_is_a_settled_state_not_a_busy_phase(self):
        # busyStatus drives the 600ms fast poll for mid-attach phases; no-kernel is settled and
        # would otherwise fast-poll forever while a downed remote stays attached
        self.assertIn("s!=='no-kernel'", km._LANDING_REMOTES_JS)


if __name__ == "__main__":
    unittest.main()
