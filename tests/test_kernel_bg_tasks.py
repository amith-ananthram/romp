"""Background-task box (the user 2026-06-26): surface run_in_background tasks in the chat — a launch (a
tool_use with run_in_background:true) paired with its <task-notification> result. The kernel extracts them
(_bg_tasks) into structured rows {id,status,summary,command,output} for a dedicated box between the
transcript and the composer. The box is a live "what's running now" indicator: a task shows ONLY while
RUNNING and drops out the instant its result lands (its completion is shown separately as the agent notice
card), so it never lingers when nothing is running (the user 2026-07-06). SYNTHETIC fixtures only."""
import json
import os
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

TUSE = "11111111-aaaa-bbbb-cccc-222222222222"


def _launch(tid=TUSE, cmd="sleep 5 && false", desc="Restart server after test", ts=None):
    rec = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash",
         "input": {"run_in_background": True, "command": cmd, "description": desc}}]}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _notif_body(tid, status, outfile, summary):
    return ("<task-notification>\n<task-id>bkv4ddzb1</task-id>\n<tool-use-id>%s</tool-use-id>\n"
            "<output-file>%s</output-file>\n<status>%s</status>\n<summary>%s</summary>\n</task-notification>"
            % (tid, outfile, status, summary))


def _notif(tid=TUSE, status="failed", outfile="", summary='Background command "Restart server after test" failed with exit code 1'):
    # the OLDER result shape: the notification wrapped in a tool_result block naming the launch id
    body = _notif_body(tid, status, outfile, summary)
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": body}]}}


def _notif_str(tid=TUSE, status="completed", outfile="", summary="job finished"):
    # the CURRENT dominant result shape: a standalone user record whose message.content IS the
    # notification string — no tool_result wrapper; the join key is the INNER <tool-use-id> tag
    return {"type": "user", "message": {"content": _notif_body(tid, status, outfile, summary)},
            "promptSource": "sdk", "userType": "external"}


def _notif_queued(tid=TUSE, status="completed", outfile="", summary="job finished"):
    # a notification ENQUEUED while the session is busy — the task itself is already finished
    return {"type": "queue-operation", "operation": "enqueue", "content": _notif_body(tid, status, outfile, summary)}


def _agent_launch(tid="tu_agent1", desc="Map the parser", ts=None):
    # an async Agent dispatch ack: a user record whose TOP-LEVEL toolUseResult says async_launched;
    # the tool_result block names the launching tool_use id
    rec = {"type": "user",
           "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "a1",
                             "description": desc, "prompt": "map the parser end to end",
                             "outputFile": "/tmp/agent-a1.output"},
           "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                    "content": [{"type": "text", "text": "Async agent launched successfully."}]}]}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _agent_tool_use(tid="tu_agent1", desc="Map the parser", prompt="map the parser end to end", ts=None):
    # the assistant-side Agent dispatch: background by default, so no run_in_background key — the ack
    # (isAsync, _agent_launch above) is what proves the launch; this block holds the ask's full text
    rec = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Agent",
         "input": {"description": desc, "prompt": prompt, "subagent_type": "general-purpose"}}]}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _workflow_launch(tid="tu_wf1", name="notes-api-audit",
                     summary="Sweep the notes-api routes for slow spots",
                     script="export const meta = {name: 'notes-api-audit'}\nphase('Scan')"):
    # a background Workflow dispatch: the assistant tool_use carries only the script; the ack's
    # toolUseResult names the work via `summary` + `workflowName` (there is no `description` key)
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Workflow", "input": {"script": script}}]}}
    ack = {"type": "user",
           "toolUseResult": {"status": "async_launched", "taskType": "local_workflow",
                             "workflowName": name, "summary": summary},
           "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                    "content": [{"type": "text", "text": "Workflow launched."}]}]}}
    return use, ack


def _prompt(text="next thing please"):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _write(recs):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return path


class BgTasks(unittest.TestCase):
    def test_a_finished_background_task_is_NOT_surfaced(self):
        # its result landed → the task is done → the box drops it AT ONCE (no lingering "empty" line, even
        # before the user sends another message); its completion is shown as the agent notice card instead
        path = _write([_launch(), _notif(status="failed")])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 0, "a finished task is not in the box")
        self.assertEqual(res["tasks"], [])

    def test_an_unmatched_launch_is_running(self):
        path = _write([_launch()])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["tasks"][0]["status"], "running")
        self.assertEqual(res["tasks"][0]["summary"], "Restart server after test", "running shows the launch description")

    def test_a_finished_task_stays_cleared_regardless_of_a_later_prompt(self):
        # a completed task is gone whether or not the user has moved on — it never lingers
        path = _write([_launch(), _notif(status="completed"), _prompt()])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 0, "a finished task is cleared")
        self.assertEqual(res["tasks"], [])

    def test_a_running_task_persists_across_a_later_prompt(self):
        # launched, no result yet, then a new prompt → still in flight → keep showing it
        path = _write([_launch(), _prompt()])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["tasks"][0]["status"], "running")

    def test_count_reflects_all_tasks_even_when_the_list_is_capped(self):
        # the header count is the TRUE total; the list itself is capped at 30 (the flat list scrolls)
        recs = []
        for i in range(35):
            recs.append(_launch(tid="t%02d" % i, desc="job %d" % i))
        path = _write(recs)
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 35, "count reports every surfaced task")
        self.assertEqual(len(res["tasks"]), 30, "the list is capped")

    def test_output_file_tail_is_read_for_the_details(self):
        # the details body reads the task's output FILE tail; test the helper directly (a finished task is no
        # longer surfaced in the box, so this decouples the output-reading from the surfacing rule)
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".output", delete=False)
        out.write("line one\nline two\nboom: exit 1\n")
        out.close()
        try:
            tail = km._read_task_output(out.name)
        finally:
            os.unlink(out.name)
        self.assertIn("boom: exit 1", tail, "the output file's tail is the details body")

    def test_parse_task_notification_keys_on_exact_tags(self):
        note = km._parse_task_notification("<task-notification><status>completed</status><summary>done</summary></task-notification>")
        self.assertEqual(note["status"], "completed")
        self.assertEqual(note["summary"], "done")
        self.assertIsNone(km._parse_task_notification("just some text"), "non-notification text → None")

    def test_live_lifecycle_set_gates_the_box_for_sdk_sessions(self):
        # the CLI's task lifecycle stream is the AUTHORITATIVE liveness source (the user 2026-07-11): a
        # scan row survives only while its tool_use id is in the live set, so a killed/finished task drops
        # the instant its terminal event lands — and live=[] (no tasks) empties the box even if the
        # transcript still reads 'running'. live=None (a backend mid-restart, no snapshot) keeps the scan verdict.
        path = _write([_launch(), _launch(tid="tu_watch", desc="power watcher")])
        try:
            live_one = [{"desc": "Restart server after test", "type": "local_bash",
                         "since": 1, "toolUseId": TUSE, "lastTool": ""}]
            res = km._bg_tasks(path, live=live_one)
            self.assertEqual([t["id"] for t in res["tasks"]], [TUSE], "only the live task survives")
            self.assertEqual(res["count"], 1)
            self.assertEqual(km._bg_tasks(path, live=[])["count"], 0,
                             "an EMPTY live set is authoritative: nothing is running")
            self.assertEqual(km._bg_tasks(path, live=None)["count"], 2,
                             "no live info (no snapshot) → the transcript scan stands")
        finally:
            os.unlink(path)

    def test_chat_body_hosts_the_box_between_the_transcript_and_the_composer(self):
        body = km._chat_body()
        self.assertIn('id="bg-tasks"', body)
        self.assertLess(body.index('id="content"'), body.index('id="bg-tasks"'), "after the transcript")
        self.assertLess(body.index('id="bg-tasks"'), body.index('id="composer"'), "before the composer")


class NotificationShapes(unittest.TestCase):
    """The result can land in THREE durable shapes; a task must clear on any of them. Missing the standalone
    string shape (the current dominant one) left finished tasks reading 'running' forever in the box."""

    def test_a_string_shape_notification_clears_the_task(self):
        path = _write([_launch(), _notif_str(tid=TUSE)])
        try:
            self.assertEqual(km._bg_tasks(path)["count"], 0, "the standalone string notification finishes the task")
        finally:
            os.unlink(path)

    def test_an_enqueued_notification_clears_the_task(self):
        # queued while the session is busy — the task itself is already done; don't wait for delivery
        path = _write([_launch(), _notif_queued(tid=TUSE)])
        try:
            self.assertEqual(km._bg_tasks(path)["count"], 0, "an enqueued notification finishes the task")
        finally:
            os.unlink(path)

    def test_parse_extracts_the_inner_tool_use_id(self):
        note = km._parse_task_notification(_notif_body("tu_42", "completed", "", "done"))
        self.assertEqual(note["tool_use_id"], "tu_42", "the string shape's only join key is the inner tag")

    def test_a_notification_for_an_unknown_launch_is_ignored(self):
        path = _write([_launch(), _notif_str(tid="tu_other")])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 1, "a foreign notification never clears someone else's task")


class AsyncAgentDispatch(unittest.TestCase):
    """An async Agent dispatch is background work too: its ack (toolUseResult async_launched) is the durable
    launch record, its <task-notification> the durable result."""

    def test_an_async_agent_ack_counts_as_running(self):
        path = _write([_agent_launch(tid="tu_agent1", desc="Map the parser")])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["tasks"][0]["status"], "running")
        self.assertEqual(res["tasks"][0]["summary"], "Map the parser", "running shows the dispatch description")

    def test_its_notification_clears_it(self):
        path = _write([_agent_launch(tid="tu_agent1"), _notif_str(tid="tu_agent1", summary="agent finished")])
        try:
            self.assertEqual(km._bg_tasks(path)["count"], 0)
        finally:
            os.unlink(path)

    def test_a_sync_agent_result_is_not_a_launch(self):
        rec = _agent_launch(tid="tu_sync")
        rec["toolUseResult"] = {"status": "success", "isAsync": False, "description": "quick lookup"}
        path = _write([rec])
        try:
            self.assertEqual(km._bg_tasks(path)["count"], 0, "a synchronous agent result never enters the box")
        finally:
            os.unlink(path)


class DispatchGist(unittest.TestCase):
    """The box must say what a dispatched agent/workflow DOES (the user 2026-08-15, whose background
    agent expanded to a generic label with nothing inside): the gist is the dispatch's description (or
    the workflow ack's summary), and the full ask — the Agent prompt / the workflow script — rides the
    row's command field, the detail block the box already expands."""

    def test_a_workflow_ack_shows_its_summary_not_the_generic_label(self):
        use, ack = _workflow_launch()
        path = _write([use, ack])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["tasks"][0]["summary"], "Sweep the notes-api routes for slow spots")
        self.assertIn("export const meta", res["tasks"][0]["command"], "the script is the expandable detail")

    def test_an_agent_ack_without_description_reads_the_launch_input(self):
        ack = _agent_launch(tid="tu_agent1")
        ack["toolUseResult"] = {"isAsync": True, "status": "async_launched"}   # a bare ack: no description
        path = _write([_agent_tool_use(tid="tu_agent1", desc="Audit the sampler",
                                       prompt="Read every sampler call site and report drift."), ack])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["tasks"][0]["summary"], "Audit the sampler")
        self.assertEqual(res["tasks"][0]["command"], "Read every sampler call site and report drift.")

    def test_the_ack_description_outranks_the_launch_but_the_prompt_still_rides(self):
        path = _write([_agent_tool_use(tid="tu_agent1", desc="launch words", prompt="the full ask"),
                       _agent_launch(tid="tu_agent1", desc="ack words")])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["tasks"][0]["summary"], "ack words", "the ack is the designed record")
        self.assertEqual(res["tasks"][0]["command"], "the full ask")

    def test_the_detail_is_capped(self):
        use, ack = _workflow_launch(script="x" * 20000)
        path = _write([use, ack])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        cmd = res["tasks"][0]["command"]
        self.assertLessEqual(len(cmd), 4100, "a workflow script can run to 512KB; the detail must not")
        self.assertTrue(cmd.endswith("(truncated)"))

    def test_an_explicit_run_in_background_agent_still_gets_the_ask(self):
        # the DOMINANT real Agent shape: run_in_background rides the input, so the tool_use itself
        # registers the row (the Bash launch branch) and the ack must enrich it in place
        use = _agent_tool_use(tid="tu_agent1", desc="Audit the sampler", prompt="Read every call site.")
        use["message"]["content"][0]["input"]["run_in_background"] = True
        ack = _agent_launch(tid="tu_agent1")
        ack["toolUseResult"] = {"isAsync": True, "status": "async_launched", "taskType": "local_agent",
                                "outputFile": "/tmp/agent-a1.output"}
        path = _write([use, ack])
        saved = (km._live_map, km._sdk_spawned_at)
        km._live_map = lambda: {"11111111-2222-3333-4444-555555555555": {"name": "web"}}
        km._sdk_spawned_at = lambda s: None
        try:
            res = km._bg_tasks(path)
            row = km._bg_live_norm("11111111-2222-3333-4444-555555555555", path)[0]
        finally:
            km._live_map, km._sdk_spawned_at = saved
            os.unlink(path)
        self.assertEqual(res["count"], 1, "the ack must not duplicate the launch row")
        self.assertEqual(res["tasks"][0]["summary"], "Audit the sampler")
        self.assertEqual(res["tasks"][0]["command"], "Read every call site.")
        self.assertEqual(row["type"], "local_agent", "the scan row carries the agent fact")

    def test_a_scriptpath_workflow_names_its_script(self):
        # a workflow resumed/launched via scriptPath carries no inline script — the detail must still
        # say where the work is defined instead of expanding to an empty block
        use, ack = _workflow_launch()
        use["message"]["content"][0]["input"] = {"scriptPath": "/tmp/notes-api-audit.js"}
        path = _write([use, ack])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["tasks"][0]["command"], "script: /tmp/notes-api-audit.js")

    def test_a_bare_ack_still_carries_its_own_prompt(self):
        # the launch block can predate the scanned tail; the ack's own prompt is the fallback detail
        ack = _agent_launch(tid="tu_agent9")
        ack["toolUseResult"] = {"isAsync": True, "status": "async_launched",
                                "description": "Map the parser", "prompt": "map the parser end to end"}
        path = _write([ack])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["tasks"][0]["command"], "map the parser end to end")

    def test_a_nonstring_workflow_name_never_kills_the_scan(self):
        use, ack = _workflow_launch()
        ack["toolUseResult"] = {"status": "async_launched", "workflowName": 7}
        path = _write([use, ack])
        try:
            res = km._bg_tasks(path)
        finally:
            os.unlink(path)
        self.assertEqual(res["tasks"][0]["summary"], "workflow 7", "a malformed ack degrades, never raises")

    def test_the_acks_task_type_rides_the_scan_row(self):
        # the lifecycle set's type already threads through _bg_live_norm; the transcript scan carried
        # no type at all, so a placed-unstamped agent on the scan path read as furniture
        sid = "11111111-2222-3333-4444-555555555555"
        use, ack = _workflow_launch()
        bare = _agent_launch(tid="tu_agent1")
        bare["toolUseResult"] = {"isAsync": True, "status": "async_launched"}
        path = _write([use, ack, _agent_tool_use(tid="tu_agent1"), bare])
        saved = (km._live_map, km._sdk_spawned_at)
        km._live_map = lambda: {sid: {"name": "web"}}   # live CLI, no lifecycle set → scan source
        km._sdk_spawned_at = lambda s: None
        try:
            rows = {r["desc"]: r["type"] for r in km._bg_live_norm(sid, path)}
        finally:
            km._live_map, km._sdk_spawned_at = saved
            os.unlink(path)
        self.assertEqual(rows["Sweep the notes-api routes for slow spots"], "local_workflow")
        self.assertEqual(rows["Map the parser"], "local_agent")


class DurableAwaitingSource(unittest.TestCase):
    """_session_awaiting source 0.75: for a LIVE CLI with no lifecycle set (the CLI outlives kernel
    restarts and has no SDK task stream), the transcript's own launch↔notification pairing keeps awaiting
    across kernel restarts. An SDK snapshot's bgTasks key (even empty) stays authoritative; a dormant
    session never reaches the source."""
    SID = "11111111-2222-3333-4444-555555555555"

    def _patched(self, live_map, spawned=None):
        saved = (km._live_map, km._sdk_spawned_at, km._states_awaiting_overlay)
        km._live_map = lambda: live_map
        km._sdk_spawned_at = lambda sid: spawned
        km._states_awaiting_overlay = lambda sid: None
        return saved

    def _restore(self, saved):
        km._live_map, km._sdk_spawned_at, km._states_awaiting_overlay = saved

    def test_pending_agent_and_shell_dispatches_are_two_rows_of_two_kinds(self):
        # dispatched agents/workflows are AGENT rows even through the task stream; a shell launch is a
        # COMMAND row. Until 2026-09-05 a mixed pending set collapsed to the generic kind "task" (the
        # agent silently absorbed into "Awaiting 2 tasks"); the user's call (plans/subagent-transcripts.md
        # slice 2): the kinds are different things — separate rows, kind "mixed", count = every row.
        rows = [{"toolUseId": "t1", "desc": "audit the sampler", "since": 5, "type": "local_agent"},
                {"toolUseId": "t2", "desc": "notes-api sweep", "since": 6, "type": "local_workflow"}]
        saved = self._patched({self.SID: {"bgTasks": rows}})
        try:
            aw = km._session_awaiting(self.SID, None, True)
            self.assertEqual((aw["kind"], aw["count"]), ("agents", 2))
            self.assertEqual([it["kind"] for it in aw["items"]], ["agents", "agents"])
            rows.append({"toolUseId": "t3", "desc": "mkdocs serve", "since": 7, "type": "local_bash"})
            aw = km._session_awaiting(self.SID, None, True)
            self.assertEqual((aw["kind"], aw["count"]), ("mixed", 3), "two kinds present → mixed, every row counted")
            self.assertEqual([(it["kind"], it["label"]) for it in aw["items"]],
                             [("agents", "audit the sampler"), ("agents", "notes-api sweep"), ("commands", "mkdocs serve")])
            self.assertNotIn("task", aw["why"], "the collapse word is gone: %r" % aw["why"])
            self.assertEqual(aw["why"], "waiting on 2 background agents and 1 background command")
        finally:
            self._restore(saved)

    def test_a_live_lifecycle_less_cli_reads_awaiting_from_the_transcript(self):
        path = _write([_launch(desc="power watcher")])
        saved = self._patched({self.SID: {"name": "web"}})
        try:
            why = km._session_awaiting(self.SID, path, True)
        finally:
            self._restore(saved)
            os.unlink(path)
        self.assertEqual(why, {"kind": "task", "why": "waiting on a background command: power watcher",   # "command" since slice 2 (2026-09-05)
                               "since": None, "count": 1,   # the transcript scan carries no dispatch stamp → no duration, never a guess
                               "tasks": ["power watcher"],
                               "items": [{"kind": "commands", "id": TUSE, "label": "power watcher", "since": None}]})   # the one awaited row (slice 2)

    def test_the_notification_landing_ends_it(self):
        path = _write([_launch(), _notif_str(tid=TUSE)])
        saved = self._patched({self.SID: {"name": "web"}})
        try:
            self.assertIsNone(km._session_awaiting(self.SID, path, True))
        finally:
            self._restore(saved)
            os.unlink(path)

    def test_an_sdk_snapshots_empty_bg_set_is_authoritative(self):
        # the live lifecycle set says NOTHING is running → the transcript scan must not override it
        path = _write([_launch()])
        saved = self._patched({self.SID: {"name": "web", "bgTasks": []}})
        try:
            self.assertIsNone(km._session_awaiting(self.SID, path, True))
        finally:
            self._restore(saved)
            os.unlink(path)

    def test_a_dormant_session_never_reads_the_transcript_source(self):
        # dead CLI → its tasks died with it; the death notice, not awaiting, is the truth
        path = _write([_launch()])
        saved = self._patched({})
        try:
            self.assertIsNone(km._session_awaiting(self.SID, path, True))
        finally:
            self._restore(saved)
            os.unlink(path)

    def test_the_spawn_stamp_gates_pre_restart_ghosts(self):
        # launched BEFORE the current CLI spawned → died with the old one → not awaiting
        path = _write([_launch(ts="2026-07-22T10:00:00.000Z", desc="old watcher")])
        saved = self._patched({self.SID: {"name": "web"}}, spawned=4102444800)  # spawn far after the launch
        try:
            self.assertIsNone(km._session_awaiting(self.SID, path, True))
        finally:
            self._restore(saved)
            os.unlink(path)


class OneRowPerAgentAcrossTheHookAndTheStream(unittest.TestCase):
    """A background agent is reported TWICE to _session_awaiting: by the SubagentStart hook set (source 0:
    {type, since, agentId}) and by the CLI's task lifecycle stream (source 0.5: the bgTasks mirror, whose
    rows carry the launch's toolUseId and the CLI's own description). The slice-2 join matched them on
    agentId — but the stream rows never carried one: _live_bg_tasks dropped the lifecycle task_id (which IS
    the agent id for an Agent task, probe-verified on 2.1.257 and already relied on by _on_task_event), the
    launch ledger records Bash/Monitor only (its agentId is the ACTING agent), and the sidecar meta map is
    keyed on the ORIGINAL launch's toolUseId, which a resumed agent's task no longer carries. Seen live on
    2026-09-06: two agents and a shell command read "Awaiting 5 · 4 agents · 1 command" — each agent once by
    type (with the open-transcript arrow) and once as "Running <description>" (no arrow). The join key is
    the stream's own task_id (`taskId` on every bgTasks row), and an agent row's label is the dispatch
    description with the CLI's "Running " prefix stripped (the STATUS word already says running).
    SYNTHETIC: placeholder sid, invented agent ids and descriptions (the notes-api demo domain)."""
    SID = "11111111-2222-3333-4444-555555555555"
    A1, A2 = "a1111111111111111", "a2222222222222222"

    def setUp(self):
        self._saved = (km._live_map, km._sdk_spawned_at, km._states_awaiting_overlay, km._bg_pending)
        km._sdk_spawned_at = lambda sid: None
        km._states_awaiting_overlay = lambda sid: None
        km._bg_pending = lambda sid, path, tasks: tasks     # nothing placed yet: every live row is pending

    def tearDown(self):
        km._live_map, km._sdk_spawned_at, km._states_awaiting_overlay, km._bg_pending = self._saved

    def _snap(self, subs, tasks):
        km._live_map = lambda: {self.SID: {"subagents": subs, "bgTasks": tasks}}

    def test_a_placed_launch_still_joins_its_hook_row(self):
        # the ordinary idle-awaiting steady state: the judge has PLACED the launch turn, so the launch is
        # no longer pending — the hook row must still meet its stream twin (one row, the launch's id and
        # description), not flap back to {id: agentId, label: agent type} (review find on #938, 2026-09-07)
        km._bg_pending = lambda sid, path, tasks: []          # everything placed
        self._snap([{"type": "general-purpose", "since": 100, "agentId": self.A1}],
                   [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                     "desc": "Running Check the exporter for banned words", "lastTool": ""},
                    {"toolUseId": "toolu_03", "taskId": "b3333", "type": "local_bash", "since": 110,
                     "desc": "build the docs site", "lastTool": ""}])
        aw = km._session_awaiting(self.SID, None, True)
        self.assertEqual(aw["count"], 1, "the placed command adds no row; the hook agent is one joined row: %r" % aw["items"])
        it = aw["items"][0]
        self.assertEqual((it["kind"], it["id"], it["label"], it.get("agentId"), it["since"]),
                         ("agents", "toolu_01", "Check the exporter for banned words", self.A1, 98),
                         "joined over EVERY live agent task, not only the pending ones")

    def test_two_agents_seen_by_both_sources_and_a_shell_command_are_three_rows(self):
        # the live defect, reproduced on the REAL _bg_live_norm: hook rows (type-labelled) + stream rows
        # (the same two agents, the CLI's "Running <description>" wording, toolUseIds) + one shell task
        self._snap([{"type": "general-purpose", "since": 100, "agentId": self.A1},
                    {"type": "general-purpose", "since": 105, "agentId": self.A2}],
                   [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                     "desc": "Running Check the exporter for banned words", "lastTool": ""},
                    {"toolUseId": "toolu_02", "taskId": self.A2, "type": "local_agent", "since": 104,
                     "desc": "Running Rerun the notes-api harness", "lastTool": ""},
                    {"toolUseId": "toolu_03", "taskId": "b3333", "type": "local_bash", "since": 110,
                     "desc": "build the docs site", "lastTool": ""}])
        aw = km._session_awaiting(self.SID, None, True)
        self.assertEqual(aw["count"], 3, "one row per awaited thing — the same agent is never counted twice: %r" % aw["items"])
        self.assertEqual([it["kind"] for it in aw["items"]], ["agents", "agents", "commands"])
        self.assertEqual([it["label"] for it in aw["items"]],
                         ["Check the exporter for banned words", "Rerun the notes-api harness", "build the docs site"],
                         "the dispatch description, never the agent type and never the CLI's 'Running ' prefix")
        self.assertEqual([it.get("agentId") for it in aw["items"]], [self.A1, self.A2, None],
                         "every joined agent row keeps its agentId (the open-transcript arrow)")
        self.assertEqual([it["id"] for it in aw["items"]], ["toolu_01", "toolu_02", "toolu_03"],
                         "a joined row wears the launch's toolUseId — Stop's handle")
        self.assertEqual([it["since"] for it in aw["items"]], [98, 104, 110], "the earlier of the two starts")
        self.assertEqual((aw["kind"], aw["why"]), ("mixed", "waiting on 2 background agents and 1 background command"))
        self.assertEqual(aw["tasks"], ["Check the exporter for banned words", "Rerun the notes-api harness", "build the docs site"])

    def test_the_same_three_rows_ride_while_the_turn_is_open(self):
        # 2026-09-06, on the REAL _bg_live_norm: the joined rows are a turn-agnostic read
        # (_session_background_items) — the chat box lists them under a working header instead of falling
        # to the legacy tasks list, which is what made the box swap presentations at every turn boundary
        self._snap([{"type": "general-purpose", "since": 100, "agentId": self.A1},
                    {"type": "general-purpose", "since": 105, "agentId": self.A2}],
                   [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                     "desc": "Running Check the exporter for banned words", "lastTool": ""},
                    {"toolUseId": "toolu_02", "taskId": self.A2, "type": "local_agent", "since": 104,
                     "desc": "Running Rerun the notes-api harness", "lastTool": ""},
                    {"toolUseId": "toolu_03", "taskId": "b3333", "type": "local_bash", "since": 110,
                     "desc": "build the docs site", "lastTool": ""}])
        idle_rows = km._session_awaiting(self.SID, None, True)["items"]
        self.assertIsNone(km._session_awaiting(self.SID, None, False), "mid-turn there is no WAIT (the chip reads Working)")
        self.assertEqual(km._session_background_items(self.SID, None), idle_rows, "…but the rows are the same three")
        self.assertEqual([it["label"] for it in idle_rows],
                         ["Check the exporter for banned words", "Rerun the notes-api harness", "build the docs site"])

    def test_a_live_agent_the_stream_has_not_seen_keeps_its_type_as_the_label(self):
        self._snap([{"type": "explore", "since": 100, "agentId": self.A1}], [])
        aw = km._session_awaiting(self.SID, None, True)
        self.assertEqual([(it["kind"], it["label"], it.get("agentId"), it["id"]) for it in aw["items"]],
                         [("agents", "explore", self.A1, self.A1)])
        self.assertEqual(aw["count"], 1)

    def test_a_stream_agent_with_no_hook_row_is_one_row_by_description(self):
        self._snap([], [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                         "desc": "Running Check the exporter for banned words", "lastTool": ""}])
        aw = km._session_awaiting(self.SID, None, True)
        self.assertEqual([(it["kind"], it["label"], it.get("agentId"), it["id"]) for it in aw["items"]],
                         [("agents", "Check the exporter for banned words", self.A1, "toolu_01")])
        self.assertEqual(aw["count"], 1)

    def test_the_stream_row_carries_the_agent_id_and_the_plain_description(self):
        # the normalized row itself, so every reader of _bg_live_norm (the chip's awaitingTasks, the
        # feed pill, the nudge gates) sees one vocabulary
        self._snap([], [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                         "desc": "Running Check the exporter for banned words", "lastTool": ""},
                        {"toolUseId": "toolu_03", "taskId": "b3333", "type": "local_bash", "since": 110,
                         "desc": "Running the docs build", "lastTool": ""}])
        rows = km._bg_live_norm(self.SID, None)
        self.assertEqual([(r["tid"], r.get("agentId"), r["desc"]) for r in rows],
                         [("toolu_01", self.A1, "Check the exporter for banned words"),
                          ("toolu_03", None, "Running the docs build")],
                         "only an AGENT row's desc wears the CLI's prefix; a shell task's description is the user's own words")

    def test_a_subagents_own_command_nests_under_the_agent_by_the_ledgers_acting_agent(self):
        # 2026-09-10, on the REAL _bg_live_norm: one live agent whose background test chunk registered under
        # the parent's task list (one list per session). The launch ledger's entry for the Bash carries the
        # hook's agent_id (the acting agent), so the command is the AGENT's wait, nested under its row and
        # stoppable (a lifecycle-set task); the session's own count is one agent, breakdown "1 agent".
        saved_reg = km._thread_reg
        km._thread_reg = lambda sid: {"bgLedger": [{"tid": "b3333", "toolUseId": "toolu_03", "tool": "bash",
                                                    "desc": "run the parser test chunk", "armedAt": 110,
                                                    "agentId": self.A1, "src": "hook"}]}
        try:
            self._snap([{"type": "general-purpose", "since": 100, "agentId": self.A1}],
                       [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                         "desc": "Running Check the exporter for banned words", "lastTool": ""},
                        {"toolUseId": "toolu_03", "taskId": "b3333", "type": "local_bash", "since": 110,
                         "desc": "run the parser test chunk", "lastTool": ""}])
            aw = km._session_awaiting(self.SID, None, True)
        finally:
            km._thread_reg = saved_reg
        self.assertEqual((aw["kind"], aw["count"], aw["why"]), ("agents", 1, "1 background agent still working"))
        self.assertEqual(aw["items"], [{"kind": "agents", "id": "toolu_01", "label": "Check the exporter for banned words",
                                        "since": 98, "agentId": self.A1, "stoppable": True,
                                        "waits": [{"kind": "commands", "id": "toolu_03", "label": "run the parser test chunk",
                                                   "since": 110, "stoppable": True}]}])
        self.assertEqual(aw["tasks"], ["Check the exporter for banned words"])

    def test_a_command_with_no_ledger_owner_and_no_agent_file_stays_beside_the_agent(self):
        # the same two tasks, the ledger silent and no transcript to consult (path None): never a guess
        self._snap([{"type": "general-purpose", "since": 100, "agentId": self.A1}],
                   [{"toolUseId": "toolu_01", "taskId": self.A1, "type": "local_agent", "since": 98,
                     "desc": "Running Check the exporter for banned words", "lastTool": ""},
                    {"toolUseId": "toolu_03", "taskId": "b3333", "type": "local_bash", "since": 110,
                     "desc": "run the parser test chunk", "lastTool": ""}])
        aw = km._session_awaiting(self.SID, None, True)
        self.assertEqual((aw["kind"], aw["count"]), ("mixed", 2))
        self.assertEqual([it["kind"] for it in aw["items"]], ["agents", "commands"])
        self.assertTrue(all(it.get("stoppable") for it in aw["items"]), "every lifecycle-set row is stoppable")

    def test_the_transcript_scan_path_carries_the_acks_agent_id(self):
        # source 0.75 (a live CLI with no lifecycle set): the async ack names the agent; the row
        # must carry it so the same join works there
        path = _write([_agent_tool_use(tid="tu_agent1", desc="Map the parser"), _agent_launch(tid="tu_agent1")])
        km._live_map = lambda: {self.SID: {"name": "web"}}
        try:
            rows = km._bg_live_norm(self.SID, path)
        finally:
            os.unlink(path)
        self.assertEqual([(r["tid"], r.get("agentId"), r["desc"]) for r in rows], [("tu_agent1", "a1", "Map the parser")])


class AgentTasksAreNeverServices(unittest.TestCase):
    """_bg_split (the user 2026-07-27): a dispatched AGENT is work the session waits on by construction,
    never furniture — only shell tasks can be classified as services. A background agent was riding the
    neutral bgServices chip while its card sat plain Working, because the placed-with-no-stamp rule
    assumed the closer's stamp would affirm agent waits and the stamp could not reach the surfaces."""
    SID = "11111111-2222-3333-4444-555555555555"

    def _split(self, tasks, placed, stamped=frozenset()):
        saved = (km._bg_placed_tops, km._session_stamped_tops)
        km._bg_placed_tops = lambda sid, path, tids: placed
        km._session_stamped_tops = lambda sid: stamped
        try:
            return km._bg_split(self.SID, "/p", tasks)
        finally:
            km._bg_placed_tops, km._session_stamped_tops = saved

    def test_a_placed_unstamped_agent_stays_awaited(self):
        t = {"tid": "t1", "desc": "a dispatched research agent", "t": 100, "type": "local_agent"}
        awaited, services = self._split([t], {"t1": self.SID + ":g1"})
        self.assertEqual(awaited, [t], "an agent is never furniture")
        self.assertEqual(services, [])

    def test_a_placed_unstamped_shell_task_is_a_service(self):
        t = {"tid": "t2", "desc": "mkdocs serve", "t": 100, "type": "local_shell"}
        awaited, services = self._split([t], {"t2": self.SID + ":g1"})
        self.assertEqual(services, [t], "a shell process with no affirmed wait is the session's furniture")
        self.assertEqual(awaited, [])

    def test_a_stamped_top_keeps_even_its_shell_tasks_awaited(self):
        t = {"tid": "t3", "desc": "a watcher loop", "t": 100, "type": "local_shell"}
        awaited, services = self._split([t], {"t3": self.SID + ":g1"}, stamped=frozenset({self.SID + ":g1"}))
        self.assertEqual(awaited, [t], "the closer affirmed this thread's wait")

    def test_norm_threads_the_type_through_both_sources(self):
        saved = km._live_map
        km._live_map = lambda: {self.SID: {"bgTasks": [
            {"toolUseId": "t4", "desc": "an agent", "since": 5, "type": "local_agent"}]}}
        try:
            rows = km._bg_live_norm(self.SID, None)
        finally:
            km._live_map = saved
        self.assertEqual(rows[0]["type"], "local_agent", "the lifecycle set's type survives normalization")


class AwaitedTaskIdsMirrorTheDescs(unittest.TestCase):
    """_awaiting_task_ids (the user 2026-08-19): the chat's #bg-tasks box outlines exactly the rows the
    await-green chip waits on, matched by the LAUNCH id both payloads carry. The ids come from the same
    _bg_split set as _awaiting_task_descs, so the outline and the chip can never disagree about which
    tasks are awaited — and a service row (placed, unstamped shell task) keeps its plain border."""
    SID = "11111111-2222-3333-4444-555555555555"

    def test_ids_track_the_awaited_split_and_skip_services(self):
        saved = (km._live_map, km._bg_placed_tops, km._session_stamped_tops)
        km._live_map = lambda: {self.SID: {"bgTasks": [
            {"toolUseId": "t1", "desc": "suite run", "since": 5, "type": "local_shell"},
            {"toolUseId": "t2", "desc": "mkdocs serve", "since": 6, "type": "local_shell"},
        ]}}
        km._bg_placed_tops = lambda sid, path, tids: {"t2": self.SID + ":g1"}  # placed, no stamp -> service
        km._session_stamped_tops = lambda sid: frozenset()
        try:
            ids = km._awaiting_task_ids(self.SID, "/p")
            descs = km._awaiting_task_descs(self.SID, "/p")
        finally:
            km._live_map, km._bg_placed_tops, km._session_stamped_tops = saved
        self.assertEqual(ids, ["t1"], "pending t1 is awaited; the placed-unstamped service t2 is not")
        self.assertEqual(descs, ["suite run"], "ids and descriptions describe the same awaited set")


class TaskOutputsForCard(unittest.TestCase):
    """_task_outputs_for joins a completed background command's notification to its shell command (from the
    launch scan) and its output-file tail, so the inline completion card can EXPAND to real detail instead of
    re-printing its one-line summary (the user 2026-07-23). Keyed by the notification's inner tool-use-id."""

    def _inner(self, tid, outfile, summary):
        # the INNER notification XML, as _split_reminders hands it to build_session (outer wrapper peeled)
        return ("<task-id>bkv4ddzb1</task-id>\n<tool-use-id>%s</tool-use-id>\n<output-file>%s</output-file>\n"
                "<status>completed</status>\n<summary>%s</summary>" % (tid, outfile, summary))

    def test_joins_command_and_output_tail_by_tool_use_id(self):
        SUM = 'Background command "measure the watch-loop rate" completed (exit code 0)'
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".output", delete=False)
        out.write("measuring...\nrate = 3.2/s\n"); out.close()
        path = _write([_launch(tid=TUSE, cmd="python measure.py", desc="measure the watch-loop rate"),
                       _notif_str(tid=TUSE, outfile=out.name, summary=SUM)])
        try:
            to = km._task_outputs_for([self._inner(TUSE, out.name, SUM)], path)
        finally:
            os.unlink(path); os.unlink(out.name)
        self.assertIn(TUSE, to)
        self.assertEqual(to[TUSE]["command"], "python measure.py", "the shell command comes from the launch record")
        self.assertIn("rate = 3.2/s", to[TUSE]["output"], "the output tail is read from the file")

    def test_a_reminder_that_is_not_a_task_notification_is_ignored(self):
        self.assertEqual(km._task_outputs_for(["Your context is getting full."], "/nonexistent"), {},
                         "a plain reminder contributes no task output")

    def test_the_output_read_invalidates_when_the_file_changes(self):
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".output", delete=False)
        out.write("first\n"); out.close()
        try:
            a = km._read_task_output(out.name)
            self.assertEqual(km._read_task_output(out.name), a, "an unchanged file serves from cache")
            with open(out.name, "w") as f:
                f.write("second, longer content\n")   # size changes → the (mtime,size) key misses → re-read
            b = km._read_task_output(out.name)
        finally:
            os.unlink(out.name)
        self.assertIn("first", a)
        self.assertIn("second, longer content", b, "a changed file re-reads, never a stale cache hit")


if __name__ == "__main__":
    unittest.main()
