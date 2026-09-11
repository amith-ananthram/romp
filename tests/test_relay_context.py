#!/usr/bin/env python3
"""A relayed question carries the conversation it ends (T334 follow-on, the user 2026-09-11, who saw a peer answer a
relayed question with only its last turn for context). Pinned on synthetic turns, stores and names:
- the excerpt: whole turns only, the question's turn always, earlier turns newest-first while they fit the bound,
  shown oldest first under a line counting what is shown and what was left out; tool calls collapsed to a count; a
  fenced code block never cut (kept whole, or left out whole with a line saying so); romp's own markers stripped;
- the bound: a knob read at call time ($ROMP_RELAY_CONTEXT_BYTES, the config file, the 24 KiB default);
- the closer's block stores the excerpt on the marker, and the kernel's relay carries it inside a fence longer than
  any run of backticks it holds, the why alone scrubbed."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_relay_context", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781300000
T0 = NOW - 3600
WORKER = "11111111-2222-3333-4444-eeeeeeeeeeee"
MANAGER = "11111111-2222-3333-4444-ffffffffffff"


def atom(role, text=None, tools=0, t=T0):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    for i in range(tools):
        content.append({"type": "tool_use", "id": "tu%d" % i, "name": "Bash", "input": {}})
    return {"uuid": "u-%s-%d-%d" % (role, t, len(text or "")), "type": role, "t": t, "message": {"role": role, "content": content}}


def turn(t, prompt, reply, tools=0):
    return {"t": t, "end": t + 30, "atoms": [atom("user", prompt, t=t), atom("assistant", reply, tools=tools, t=t + 10)]}


class Excerpt(unittest.TestCase):
    def test_whole_turns_newest_first_selected_oldest_first_shown_within_the_bound(self):
        turns = [turn(T0 + i * 100, "prompt %d " % i + "x" * 300, "reply %d " % i + "y" * 300) for i in range(8)]
        out = jd._relay_excerpt(turns, T0 + 700, 2200, who="api")   # three turns of ~670 bytes each fit; a fourth would not
        self.assertTrue(out.startswith("The conversation this question ends, oldest first: 3 of 8 turns, the 5 earlier ones left out."), out[:120])
        shown = [int(m) for m in __import__("re").findall(r"--- turn (\d+) of 8 ---", out)]
        self.assertEqual(shown, [6, 7, 8], "the newest three, shown oldest first")
        self.assertIn("user: prompt 7", out)
        self.assertIn("api: reply 7", out)
        self.assertNotIn("prompt 4 ", out, "an earlier turn that does not fit is left out whole, never cut")
        self.assertLessEqual(len(out.encode()), 2200 + 200, "the bound holds, the header aside")

    def test_a_short_conversation_rides_whole(self):
        turns = [turn(T0, "which client?", "the exporter has two."), turn(T0 + 100, "the old one", "then the port stays; which port?")]
        out = jd._relay_excerpt(turns, T0 + 100, jd.RELAY_CONTEXT_BYTES_DEFAULT, who="api")
        self.assertTrue(out.startswith("The conversation this question ends, oldest first: 2 of 2 turns."), out[:100])
        self.assertLess(out.index("which client?"), out.index("which port?"), "oldest first")

    def test_turns_after_the_question_are_not_the_context(self):
        turns = [turn(T0, "a", "b"), turn(T0 + 100, "c", "d"), turn(T0 + 200, "later prompt", "later reply")]
        out = jd._relay_excerpt(turns, T0 + 100, 4096)
        self.assertNotIn("later", out)
        self.assertIn("2 of 2 turns", out)

    def test_tool_calls_collapse_to_a_count_and_markers_are_stripped(self):
        t = turn(T0, "run it\n<!-- romp-msg-id: m-1 -->\n<!-- romp-msg-kind: delegate -->", "done, three files changed", tools=3)
        out = jd._relay_excerpt([t], T0, 4096, who="api")
        self.assertIn("user: run it\napi: done, three files changed\n(3 tool calls)", out)
        self.assertNotIn("romp-msg", out)

    def test_a_fenced_block_is_never_cut(self):
        code = "```python\n" + "\n".join("line %d = %d" % (i, i) for i in range(60)) + "\n```"
        big = turn(T0, "fix the loop", "Here is the fix:\n\n" + code + "\n\nShall I ship it?")
        small = turn(T0 - 100, "context before", "noted")
        out = jd._relay_excerpt([small, big], T0, 400, who="api")
        self.assertIn("(a code block of 61 lines left out)", out, "too big for the bound: left out whole")
        self.assertIn("Shall I ship it?", out, "the question survives")
        self.assertNotIn("line 30", out)
        self.assertIn("shortened: this turn's earlier", out)
        out2 = jd._relay_excerpt([small, big], T0, 8192, who="api")
        self.assertIn(code, out2, "with room, the block rides whole")
        self.assertIn("--- turn 1 of 2 ---", out2)

    def test_the_knob_reads_the_environment_then_the_file_then_the_default(self):
        saved = jd.RELAY_CONTEXT_KNOB
        td = tempfile.TemporaryDirectory()
        jd.RELAY_CONTEXT_KNOB = Path(td.name) / "relay-context-bytes"
        env = os.environ.pop("ROMP_RELAY_CONTEXT_BYTES", None)
        try:
            self.assertEqual(jd.relay_context_bytes(), 24 * 1024)
            jd.RELAY_CONTEXT_KNOB.write_text("# raise for a long exchange\n65536\n")
            self.assertEqual(jd.relay_context_bytes(), 65536, "the file, read at call time")
            jd.RELAY_CONTEXT_KNOB.write_text("lots\n")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(jd.relay_context_bytes(), 24 * 1024, "a bad value is ignored")
            self.assertIn("not a positive integer", err.getvalue())
            os.environ["ROMP_RELAY_CONTEXT_BYTES"] = "4096"
            self.assertEqual(jd.relay_context_bytes(), 4096, "the environment first")
        finally:
            if env is None:
                os.environ.pop("ROMP_RELAY_CONTEXT_BYTES", None)
            else:
                os.environ["ROMP_RELAY_CONTEXT_BYTES"] = env
            jd.RELAY_CONTEXT_KNOB = saved
            td.cleanup()


class OnTheMarkerAndInTheMail(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.STATE, km.NAMES)
        jd._rebind_state(td)
        jd.NAMES.mkdir(); jd.GOALDIR.mkdir(); jd.MESSAGES.parent.mkdir(parents=True)
        km.NAMES = jd.NAMES
        for sid, name in ((WORKER, "api"), (MANAGER, "web")):
            (jd.NAMES / sid).write_text("%s\t/TESTDIR\t#abcdef\n" % name)
        jd.MESSAGES.write_text(json.dumps({"id": "m-d", "from_id": MANAGER, "to_id": WORKER, "from": "web", "to": "api",
                                           "t": T0 - 200, "kind": "delegate"}) + "\n")
        jd._PEER_ASK_CACHE[0] = None; jd._DELEG_CACHE[0] = None
        self.sent = []
        self._orig = km._bus_send_relay
        km._bus_send_relay = lambda payload: self.sent.append(payload) or (True, "", False, {"ok": True, "to": payload["to"]})

    def tearDown(self):
        km._bus_send_relay = self._orig
        km._RELAY_SAID.clear(); km._RELAY_QUIET.clear()
        jd._judge_ctx.relay_turns = None
        jd._rebind_state(self.saved[0]); km.NAMES = self.saved[1]
        self.td.cleanup()

    def _store(self):
        top = WORKER + ":g1"; step = WORKER + ":g2"
        node = lambda nid, text, parent=None, t=T0: {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
                                                    "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
        st = {"rompUuid": WORKER, "seq": 2, "placements": {}, "status": {top: "working"}, "confirming": [],
              "nodes": {top: dict(node(top, "Ship the exporter"), askAnchor="machine"),   # a machine-anchored, delegated top
                        step: node(step, "Ask which client", parent=top, t=T0 + 60)}}
        return st, top, step

    def test_the_closers_block_stores_the_excerpt_and_the_relay_carries_it_fenced(self):
        st, top, step = self._store()
        turns = [turn(T0 + 100, "start on the exporter", "which client should it target? here is what I see:\n```\nclient_a\nclient_b\n```", tools=2),
                 turn(T0 + 400, "keep going", "I cannot move further without the client decision.")]
        jd._judge_ctx.relay_turns = (WORKER, turns)
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step]], {"done": {}, "block": {1: "cannot move further without the client decision"}, "awaiting": {}}, t=T0 + 400)
        rw = st["nodes"][step]["relayWanted"]
        self.assertIn("2 of 2 turns", rw["context"])
        self.assertIn("client_a", rw["context"])
        self.assertIn("(2 tool calls)", rw["context"])
        jd.save_goals(WORKER, st)
        self.assertEqual(km._relay_tick(NOW), 1)
        body = self.sent[0]["body"]
        self.assertTrue(body.startswith("api cannot move further: cannot move further without the client decision\n\n````\n"),
                        "the lead-in and the why, then the excerpt inside a fence longer than the one it holds: %r" % body[:160])
        self.assertTrue(body.rstrip().endswith("````"))
        self.assertIn("--- turn 2 of 2 ---\nuser: keep going\napi: I cannot move further without the client decision.", body)

    def test_a_pass_that_left_no_turns_for_this_session_relays_the_question_alone(self):
        st, top, step = self._store()
        jd._judge_ctx.relay_turns = ("11111111-2222-3333-4444-000000000000", [turn(T0, "someone else's", "conversation")])
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step]], {"done": {}, "block": {1: "which client?"}, "awaiting": {}}, t=T0 + 400)
        self.assertNotIn("context", st["nodes"][step]["relayWanted"], "another session's turns are never this question's context")
        jd.save_goals(WORKER, st)
        self.assertEqual(km._relay_tick(NOW), 1)
        self.assertEqual(self.sent[0]["body"], "api cannot move further: which client?")

    def test_the_body_fence_outgrows_any_run_inside(self):
        body = km._relay_body("api", "which port?", "text with ````` five backticks\nand ``` three")
        self.assertIn("\n``````\ntext with", body, "six backticks fence five")
        self.assertTrue(body.endswith("\n``````"))
        self.assertEqual(km._relay_body("api", "which port?", ""), "api cannot move further: which port?", "no excerpt, no fence")
