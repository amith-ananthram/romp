#!/usr/bin/env python3
"""Restart layer 2 (T217, the user 2026-09-01): the felt restart gap shrinks to a blip, and is
finally MEASURED. Four pieces, tested per piece:
(1) boot-settled — the outage's other bookend in restart-cuts.jsonl: firstServe (the accept loop
    starts) + reconcileDone (the SDK boot reconcile returned, or was found unavailable), plus
    outageS = firstServe minus the previous cut row's t. Same-host deltas only by construction
    (the ledger lives under one host's STATE).
(2) the dying kernel ANNOUNCES itself: one {type: restarting, boot} frame to every ws client,
    enqueue-only (the per-client writer thread owns the socket) under a hard sub-second walk
    budget — the frame can never widen the shutdown.
(3) the shims reconnect ON that event (tight redial after an announced death; the blind 1.5s
    cadence stays for unannounced drops), and a drop over EXISTING content shows a translucent
    corner badge instead of the opaque loader — content stays legible; the opaque sheet remains
    for a genuinely empty pane (the loading-states rule).
(4) an announced restart's reconnect skips the stale-banner arm ONCE (the resync lands in a
    beat; the flash was noise) — a restart that never comes back stays loud through the
    disconnected state, and any SECOND reconnect arms exactly as before.
SYNTHETIC fixtures only."""
import json
import os
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_rseam", os.path.join(BIN, "romp-kernel"))
jd = km.jd

KSRC = open(os.path.join(BIN, "romp-kernel")).read()


class BootSettled(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = km.RESTART_CUTS_FILE
        km.RESTART_CUTS_FILE = Path(self.td.name) / "restart-cuts.jsonl"
        km._BOOT_MARKS.clear()

    def tearDown(self):
        km.RESTART_CUTS_FILE = self.saved
        km._BOOT_MARKS.clear()
        self.td.cleanup()

    def _rows(self):
        p = km.RESTART_CUTS_FILE
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_the_second_mark_appends_one_row_with_the_outage_delta(self):
        cut_t = int(time.time()) - 7
        km._append_restart_cut({"t": cut_t, "pid": 1, "cutTurns": [], "stopped": 0,
                                "unjoined": 0, "reaped": 0, "watchesArmed": 0, "reason": "x"})
        km._mark_boot("firstServe")
        self.assertEqual(len(self._rows()), 1, "one mark alone appends nothing")
        km._mark_boot("reconcileDone")
        km._mark_boot("attachDone")            # the row waits for the boot attaches too (2026-09-11)
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        b = rows[-1]
        self.assertTrue(b.get("bootSettled"))
        self.assertEqual(b.get("prevCutT"), cut_t)
        self.assertAlmostEqual(b["outageS"], b["firstServe"] - cut_t, places=1,
                               msg="outageS IS the felt window: previous cut to first serve")
        self.assertIn("settleS", b, "reconcile-done minus first-serve rides along")

    def test_marks_land_in_either_order(self):
        km._mark_boot("reconcileDone")               # the backend builds lazily — order is not fixed
        km._mark_boot("attachDone")            # the row waits for the boot attaches too (2026-09-11)
        self.assertEqual(self._rows(), [])
        km._mark_boot("firstServe")
        self.assertEqual(len(self._rows()), 1)

    def test_a_fresh_install_row_carries_no_outage(self):
        km._mark_boot("firstServe")
        km._mark_boot("reconcileDone")
        km._mark_boot("attachDone")            # the row waits for the boot attaches too (2026-09-11)
        b = self._rows()[-1]
        self.assertNotIn("outageS", b, "no previous cut row → no delta to claim")

    def test_marks_are_idempotent(self):
        km._mark_boot("firstServe")
        km._mark_boot("firstServe")
        km._mark_boot("reconcileDone")
        km._mark_boot("attachDone")            # the row waits for the boot attaches too (2026-09-11)
        km._mark_boot("reconcileDone")
        self.assertEqual(len(self._rows()), 1, "one row per boot, however often the marks re-fire")

    def test_the_boot_hooks_are_wired_at_both_bookends(self):
        self.assertIn('_mark_boot("firstServe")', KSRC.split("srv.serve_forever()")[0][-600:],
                      "firstServe stamps right before the accept loop starts")
        self.assertEqual(KSRC.count('_mark_boot("reconcileDone")'), 2,
                         "the reconcile phase ends on BOTH backend arms (built and unavailable)")


class RestartingBroadcast(unittest.TestCase):
    def setUp(self):
        self._saved = list(km._clients)
        with km._clients_lock:
            km._clients[:] = []

    def tearDown(self):
        with km._clients_lock:
            km._clients[:] = self._saved

    def _client(self, sends, fail=False):
        def send(s):
            if fail:
                raise OSError("dead pipe")
            sends.append(s)
        return {"app": "chat", "wid": "w", "send": send, "alive": True}

    def test_every_client_gets_the_one_dying_frame(self):
        sends = []
        with km._clients_lock:
            km._clients[:] = [self._client(sends), self._client(sends)]
        km._broadcast_restarting()
        self.assertEqual(len(sends), 2)
        for s in sends:
            m = json.loads(s)
            self.assertEqual(m["type"], "restarting")
            self.assertIn("boot", m, "the boot id rides along — the shim can tell lives apart")

    def test_a_dead_client_never_breaks_the_walk(self):
        sends = []
        with km._clients_lock:
            km._clients[:] = [self._client(sends, fail=True), self._client(sends)]
        km._broadcast_restarting()
        self.assertEqual(len(sends), 1, "the raising client is skipped, the next still gets its frame")

    def test_the_walk_respects_its_budget(self):
        # sends are enqueue-only in production (the per-client writer thread owns the socket), so
        # the budget is a backstop — pinned here with a deliberately slow fake
        sends = []

        def slow(s):
            time.sleep(0.4)
            sends.append(s)
        with km._clients_lock:
            km._clients[:] = [{"app": "chat", "wid": str(i), "send": slow, "alive": True}
                              for i in range(10)]
        t0 = time.time()
        km._broadcast_restarting(budget_s=0.5)
        took = time.time() - t0
        self.assertLess(took, 1.5, "the deadline stops the walk — a slow pipe cannot widen the shutdown")
        self.assertLess(len(sends), 10, "…so distant clients are shed, not waited on")

    def test_the_dying_kernel_announces_before_it_drains(self):
        g = KSRC[KSRC.index("def _graceful_term"):KSRC.index("def main()")]
        self.assertLess(g.index("_broadcast_restarting()"), g.index('be.drain'),
                        "the frame goes out FIRST — the drain must not eat the announce window")


class ShimSeam(unittest.TestCase):
    """The shim + pane-loader halves are inline JS in kernel.py — source pins, the suite's idiom."""

    def setUp(self):
        self.js = km._shim("chat")
        self.spin = km._pane_spin("content", "live-ask")

    def test_the_restarting_frame_latches(self):
        self.assertIn('if(msg&&msg.type==="restarting"){restartAnnounced=Date.now();', self.js)

    def test_an_announced_death_redials_tight_and_a_blind_drop_keeps_the_cadence(self):
        # 2026-09-07 added a second tight-redial event (a close within STALE_MS of a foreground) beside T217's
        self.assertIn("var inWin=Date.now()-foregroundedAt<STALE_MS,d=1500;", self.js)
        self.assertIn("if(inWin){d=eagerDial?0:250;eagerDial=false;}", self.js)   # the FIRST close after a foreground redials now; every further one in the window waits 250 ms
        self.assertIn("if(restartAnnounced&&Date.now()-restartAnnounced<30000)d=Math.min(d,250);", self.js)
        self.assertIn("setTimeout(connect,d);", self.js)

    def test_the_announced_reconnect_skips_the_stale_arm_once(self):
        self.assertIn("var ann=restartAnnounced&&Date.now()-restartAnnounced<30000;restartAnnounced=0;",
                      self.js, "the latch is one-shot: spent at the reconnect that consumes it")
        self.assertIn('if(!ann)armStale(pendingWhy||"reconnect");', self.js)
        self.assertIn("freshPending=true;", self.js,
                      "…and the resync bookkeeping still runs, so the retire path is intact")

    def test_the_reraise_path_survives(self):
        # a restart that comes back SILENT re-raises through the keepalive watchdog's forced close →
        # second reconnect → latch already spent → armStale as always; and one that never comes back
        # stays loud through the disconnected state itself
        self.assertIn('pendingWhy="foreground"', self.js, "the foreground fast-path still arms (through its reconnect)")
        self.assertIn("staleDiag(\"watchdog-close\",\"quiet\")", self.js, "the quiet watchdog still closes")

    def test_a_drop_over_content_shows_the_badge_not_the_sheet(self):
        self.assertIn("window.addEventListener('romp:wsdown',function(){if(ready()){badge(true);}else{show();}});",
                      self.spin, "content stays legible under a translucent corner affordance; the "
                                 "opaque loader is only for a genuinely empty pane")
        # 2026-09-07: the badge ends on the first FRESH frame (romp:wsfresh), not on the socket opening —
        # over a slow link the resync ran for seconds with no cue; wsup still ends the empty-pane sheet
        self.assertIn("window.addEventListener('romp:wsup',function(){hide();});", self.spin)
        self.assertIn("window.addEventListener('romp:wsfresh',function(){badge(false);});", self.spin)
        self.assertIn("id=pane-reconn", self.spin)
        self.assertIn("pointer-events:none", self.spin,
                      "clicks pass through to the content and queue in the shim, by design")
        self.assertIn("reconnecting…", self.spin)


if __name__ == "__main__":
    unittest.main()
