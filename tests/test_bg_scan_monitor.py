"""The durable background-task scan knows the Monitor tool — the THIRD launch shape.

A session idle behind a Monitor watch (a timed check on a long server-side run) scanned as
"nothing dispatched": the pip read plain ready, its goal's awaiting stamp could lift only by the
6h backstop, and the nudge gates couldn't see the wait (the user 2026-08-10, whose session was
plainly waiting on server jobs). The live lifecycle path (SDK stream) already handled monitors —
verified by live probe — so the gap was only the transcript pairing, which recognized just
backgrounded Bash and async Agent launches.

Shapes derived from a 44-monitor corpus across real transcripts (rebuilt synthetically here, per
the privacy rules): a monitor's per-EVENT notification carries task-id + summary + event but NO
status and NO tool-use-id; only its TERMINAL notification carries tool-use-id + an explicit
status. The parser's missing-status→"completed" default therefore must never end a monitor.
Non-persistent monitors also record their lifetime ceiling (timeout_ms) at launch — the deadline
consumers expire on when a CLI dies mid-watch and the terminal record can never arrive.
"""
import io
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_t", os.path.join(ROOT, "kernel", "event_model.py"))

TS = "2026-01-01T00:00:00.000Z"
T0 = em.parse_z(TS)
TID = "toolu_11111111222233334444555555555555"


def launch(name="Monitor", inp=None, tid=TID):
    return {"type": "assistant", "timestamp": TS,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name,
                                     "input": inp or {}}]}}


def ack(tid=TID, text="Monitor started (task b1a2b3c4d, timeout 300000ms).", err=False):
    r = {"type": "user", "timestamp": TS,
         "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}
    if err:
        r["message"]["content"][0]["is_error"] = True
    return r


def event_note(task="b1a2b3c4d"):
    # the EVENT shape: task-id + summary + event, NO tool-use-id, NO status (corpus-derived)
    return {"type": "user", "timestamp": TS, "message": {"content":
            "<task-notification>\n<task-id>%s</task-id>\n<summary>Monitor event: \"synthetic watch\"</summary>\n"
            "<event>tick</event>\n</task-notification>" % task}}


def terminal_note(tid=TID, task="b1a2b3c4d", status="completed"):
    return {"type": "user", "timestamp": TS, "message": {"content":
            "<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n"
            "<output-file>/tmp/t/%s.output</output-file>\n<status>%s</status>\n"
            "<summary>Monitor \"synthetic watch\" stream ended</summary>\n</task-notification>"
            % (task, tid, task, status)}}


def scan(records, want_all=False):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write("\n".join(json.dumps(r) for r in records) + "\n")
    try:
        return em._scan_bg_tasks(f.name, want_all=want_all)
    finally:
        os.unlink(f.name)


class MonitorLaunchShape(unittest.TestCase):
    def test_a_monitor_launch_registers_as_running(self):
        rows = scan([launch(inp={"command": "sleep 60; echo tick", "description": "synthetic watch",
                                 "timeout_ms": 300000, "persistent": False}), ack()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "running")
        self.assertTrue(rows[0]["monitor"])
        self.assertEqual(rows[0]["summary"], "synthetic watch")
        self.assertAlmostEqual(rows[0]["deadline"], T0 + 300.0)

    def test_a_persistent_monitor_is_furniture_not_awaited_work(self):
        rows = scan([launch(inp={"command": "tail -f x.log", "persistent": True}), ack()])
        self.assertEqual(rows, [], "a session-length subscription must never hold 'awaiting'")

    def test_a_ws_monitor_records_its_url_as_the_command(self):
        rows = scan([launch(inp={"ws": {"url": "wss://TESTHOST/stream"}, "timeout_ms": 60000})])
        self.assertEqual(rows[0]["command"], "wss://TESTHOST/stream")

    def test_backgrounded_bash_launches_are_unchanged(self):
        rows = scan([launch(name="Bash", inp={"command": "make -j", "run_in_background": True,
                                              "description": "build"})])
        self.assertEqual((rows[0]["status"], rows[0].get("monitor")), ("running", None))
        self.assertNotIn("deadline", rows[0])


class EventsNeverTerminate(unittest.TestCase):
    MON = launch(inp={"command": "sleep 60", "description": "synthetic watch",
                      "timeout_ms": 300000, "persistent": False})

    def test_an_event_notification_leaves_the_watch_running(self):
        rows = scan([self.MON, ack(), event_note(), event_note()])
        self.assertEqual([r["status"] for r in rows], ["running"])

    def test_a_wrapped_event_without_status_cannot_default_to_completed(self):
        # the trap: _parse_task_notification defaults a missing <status> to "completed"; an event
        # arriving in the older tool_result-wrapper shape must still not end the watch
        wrapped = {"type": "user", "timestamp": TS, "message": {"content": [
            {"type": "tool_result", "tool_use_id": TID,
             "content": event_note()["message"]["content"]}]}}
        rows = scan([self.MON, wrapped])
        self.assertEqual([r["status"] for r in rows], ["running"])

    def test_the_terminal_notification_ends_it(self):
        rows = scan([self.MON, ack(), event_note(), terminal_note()], want_all=True)
        self.assertEqual([r["status"] for r in rows], ["completed"])
        self.assertEqual(scan([self.MON, ack(), event_note(), terminal_note()]), [],
                         "the running-only view drops a finished watch")

    def test_a_stopped_terminal_ends_it_too(self):
        rows = scan([self.MON, terminal_note(status="stopped")], want_all=True)
        self.assertEqual(rows[0]["status"], "stopped")

    def test_parser_reports_status_presence_distinct_from_the_default(self):
        ev = em._parse_task_notification(event_note()["message"]["content"])
        self.assertEqual((ev["status"], ev["has_status"]), ("completed", False))
        tm = em._parse_task_notification(terminal_note()["message"]["content"])
        self.assertEqual((tm["status"], tm["has_status"]), ("completed", True))


class ErroredLaunchesAndExpiry(unittest.TestCase):
    def test_an_errored_launch_ack_is_a_phantom_not_a_forever_running_task(self):
        rows = scan([launch(inp={"command": "sleep 60", "timeout_ms": 60000}),
                     ack(text="InputValidationError: timeout_ms too small", err=True)], want_all=True)
        self.assertEqual([r["status"] for r in rows], ["failed"])

    def test_expiry_is_the_recorded_ceiling_plus_grace_applied_with_the_consumers_now(self):
        rows = scan([launch(inp={"command": "sleep 60", "timeout_ms": 300000})])
        t = rows[0]
        self.assertFalse(em._bg_expired(t, T0 + 300.0), "inside its lifetime")
        self.assertFalse(em._bg_expired(t, T0 + 300.0 + 60.0), "grace absorbs kill/notify latency")
        self.assertTrue(em._bg_expired(t, T0 + 300.0 + 121.0), "past ceiling+grace → can never return")
        self.assertFalse(em._bg_expired({"id": "x", "status": "running"}, 9e12),
                         "no recorded ceiling (a bash task) → never expires this way")

    def test_the_ceiling_is_clamped_to_the_harness_bounds(self):
        rows = scan([launch(inp={"command": "x", "timeout_ms": 999999999})])
        self.assertAlmostEqual(rows[0]["deadline"], T0 + 3600.0)


class ConsumersApplyExpiry(unittest.TestCase):
    def test_the_lift_and_the_judge_gate_read_expiry_outside_their_caches(self):
        kernel = open(os.path.join(ROOT, "kernel", "kernel.py"), encoding="utf-8").read()
        judge = open(os.path.join(ROOT, "kernel", "judge.py"), encoding="utf-8").read()
        self.assertIn('t.get("status") == "running" and not em._bg_expired(t, now)', kernel,
                      "the awaiting-stamp lift must not wait forever on a dead monitor")
        self.assertIn("em._bg_expired(tk, time.time())", kernel,
                      "source 0.75's normalized rows must drop expired monitors")
        body = judge.split("def _bg_unresolved(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("em._bg_expired(t, now)", body,
                      "the judge's settled gate must apply expiry outside the cache, with the caller's now")
        self.assertIn("now = time.time()", body,
                      "...a fresh wall clock when no pass clock is handed in, not the cached one")


if __name__ == "__main__":
    unittest.main()
