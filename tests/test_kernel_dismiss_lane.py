"""Dead-lane dismissal (the user 2026-07-02; DURABLE since 2026-08-14): a DEAD session lingers in the
timeline as a faded/struck lane while it's still in the activity window, with none of the live controls.
Its "Clear" pill posts dismissLane; the kernel drops the lane and PERSISTS the dismissal
(timeline-dismissed.json), so a kernel restart or a reconnect keeps it cleared (the user 2026-08-14:
cleared state must be remembered — reversing the original in-memory-only call). The dismissal only
filters a lane WHILE it's dead: a revived sid reappears and sheds its record. Source pins live here;
the persistence BEHAVIOR (write, hydrate-on-boot, revive-shed, corrupt-file tolerance) is
tests/test_timeline_dismissals.py.
"""
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
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class DismissLane(unittest.TestCase):
    def test_dismissed_lanes_is_a_durable_set(self):
        # a module-level set hydrated from timeline-dismissed.json at boot and rewritten on change —
        # a restart/reconnect remembers it (behavior proven in test_timeline_dismissals.py)
        self.assertIsInstance(km._dismissed_lanes, set)
        self.assertTrue(callable(km._dismiss_lane) and callable(km._undismiss_lanes))

    def test_build_timeline_filters_dead_dismissed_lanes_only(self):
        src = inspect.getsource(km.build_timeline)
        # the filter drops a sid ONLY when it's both dismissed AND currently dead (tmux has no live session),
        # so a revived sid comes back on its own
        self.assertIn('s["sid"] in _dismissed_lanes and tmux.get(s["sid"]) is None', src)

    def test_build_timeline_sheds_records_on_revive(self):
        # the revive is the un-dismiss EVENT: a dismissed sid seen alive drops its durable record there,
        # so an old clear can never invisibly re-filter the session when it later dies again
        self.assertIn("_undismiss_lanes(", inspect.getsource(km.build_timeline))

    def test_the_filter_is_a_noop_when_nothing_is_dismissed(self):
        # guarded by `if _dismissed_lanes:` so the common (empty) case adds no per-build cost
        self.assertIn("if _dismissed_lanes:", inspect.getsource(km.build_timeline))

    def test_ws_handler_records_a_dismissal_durably_and_pushes(self):
        # the dismissLane message persists the sid (not a bare in-memory add) + rebroadcasts so the
        # lane vanishes at once and STAYS gone across restarts
        src = inspect.getsource(km)
        self.assertIn('msg.get("type") == "dismissLane" and msg.get("id")', src)
        self.assertIn('_dismiss_lane(str(msg["id"]))', src)

    def test_boot_hook_posts_dismiss_lane(self):
        # the timeline iframe exposes __rompTimelineDismiss(id) → post({type:"dismissLane",id}) — routed
        # through acquireVsCodeApi so the federation manager sends it to the lane's owning kernel
        self.assertIn('window.__rompTimelineDismiss=function(id){post({type:"dismissLane",id:id});};',
                      km._TIMELINE_BOOT)


if __name__ == "__main__":
    unittest.main()
