#!/usr/bin/env python3
"""Kernel wiring for the unified session API (bin/romp-kernel _drive + Sessions.live merge).

Deterministic: _sdk() is stubbed with a FakeBackend that records calls, so this needs neither the SDK nor
any state on disk. It locks in the routing table — _drive sends each per-session op to whichever backend
OWNS the sid (Sessions.backend_for): SDK-owned sids → the SDK backend, everything else → the tmux backend —
plus the live-session merge. (the user 2026-06-26: tmux + SDK behind one session API.)
"""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
# Fixture git goes through the shared runner: `git commit` spawns `git maintenance run --auto`, which on recent
# git detaches from its parent and can still be writing into .git while the fixture's directory is removed (the
# CI flake "Directory not empty: '.git'", tests/test_restart_classifier.py, 2026-09-10); the runner forbids that
# background work on every invocation and in every repo it inits.
from git_fixture import git, init_repo


class FakeBackend:
    def __init__(self):
        self.calls = []
        self._owned = {"sid-sdk"}

    def owns(self, sid):
        return sid in self._owned

    def send(self, sid, text):
        self.calls.append(("send", sid, text)); return True

    def interrupt(self, sid):
        self.calls.append(("interrupt", sid)); return True

    def busy(self, sid):
        return None   # no authoritative signal in this double → the gate uses the cached parse, as before

    def kill(self, sid):
        self.calls.append(("kill", sid)); return True

    def on_ask(self, sid, kind, payload=None):
        self.calls.append(("on_ask", sid, kind, payload)); return True

    def set_mode(self, sid, m):
        self.calls.append(("set_mode", sid, m)); return True

    def set_model(self, sid, v):
        self.calls.append(("set_model", sid, v)); return True

    def set_effort(self, sid, v):
        self.calls.append(("set_effort", sid, v)); return True

    def set_fast(self, sid, v):
        self.calls.append(("set_fast", sid, v)); return True

    def rename(self, sid, n):
        self.calls.append(("rename", sid, n)); return True

    def live_sessions(self):
        return {"sid-sdk": {"state": "working", "since": "100", "model": "m",
                            "effort": "", "mode": "acceptEdits"}}

    def live_atoms(self, sid):
        return getattr(self, "_live", {}).get(sid, [])

    def prune_live(self, sid, tx_uuids, tx_texts=(), human_floor=0):
        self.calls.append(("prune_live", sid))


class KernelWiring(unittest.TestCase):
    def setUp(self):
        self.be = FakeBackend()
        self.saved = (km._sdk, km._push_all, km._send_to_app, km.jd.optimistic_followup)
        km._sdk = lambda: self.be
        self.pushes = []
        km._push_all = lambda *a, **k: self.pushes.append(1)
        self.app_sends = []                              # (app, msg) fan-outs: cardPredict + its cardMoveAck
        km._send_to_app = lambda app, msg: self.app_sends.append((app, msg))
        # insulate the real goal store: record the optimistic-reopen call, no disk I/O. Tests that need a
        # "reopened" return flip this to True.
        self.fu_calls = []
        km.jd.optimistic_followup = lambda sid, gid, **kw: (self.fu_calls.append((sid, gid)), False)[1]   # **kw tolerates text=/now=/stub= (judge optimistic_followup signature grew)
        # a compactSession routed by ONE test sets the optimistic compacting flag, which makes a LATER
        # test's setModel PARK instead of applying (the intended mid-compaction behavior) — isolate both.
        km._compact_clicked.clear()
        km._pending_ops.clear()

    def tearDown(self):
        km._sdk, km._push_all, km._send_to_app, km.jd.optimistic_followup = self.saved
        km._compact_clicked.clear()
        km._pending_ops.clear()
        km._user_goal_write.pop("sid-sdk", None)         # the punch-through marker is module state — don't leak it

    def _route(self, msg):
        return km._drive(msg, {"send": lambda s: None})

    def test_send_routes_to_backend(self):
        self.assertTrue(self._route({"type": "sendMessage", "id": "sid-sdk", "text": "hi"}))
        self.assertIn(("send", "sid-sdk", "hi"), self.be.calls)

    def test_non_sdk_sid_routes_to_the_tmux_backend(self):
        # a non-SDK sid no longer "falls through" — _drive routes it to the tmux backend via
        # Sessions.backend_for (the fallback). The unified dispatch handles BOTH kinds.
        # It must be a session this kernel HAS, though: since 2026-07-29 _drive refuses an id it has never
        # heard of rather than letting backend_for's tmux fallback type at a pane that isn't there. The
        # names entry is what a real tmux session would carry; test_drive_foreign_sid.py owns the refusal.
        tm = FakeBackend(); tm._owned = set()
        saved, saved_name = km._TMUX, km._name_of
        km._TMUX = tm
        km._name_of = lambda sid: "web"
        try:
            self.assertTrue(self._route({"type": "sendMessage", "id": "sid-tmux", "text": "hi"}))
            self.assertIn(("send", "sid-tmux", "hi"), tm.calls)   # routed to the tmux backend
            self.assertEqual(self.be.calls, [])                   # the SDK backend was untouched
        finally:
            km._TMUX, km._name_of = saved, saved_name
            km._tmux_echo.pop("sid-tmux", None)                   # the optimistic echo wrote here — don't leak it

    def test_a_typed_slash_model_or_effort_on_a_tmux_session_takes_the_setter_too(self):
        # The routing is backend-agnostic: a typed "/model X" or "/effort X" on a tmux-owned sid reaches
        # TmuxBackend's setters — which type the CLI's own command into the pane and, for /model, accept
        # the confirmation the CLI asks for (SessionBackend.set_model) — never send() as literal text.
        # The SDK-sid test below covers one door; this pins the other, so the two backends cannot drift
        # apart at the router.
        tm = FakeBackend(); tm._owned = set()
        saved, saved_name = km._TMUX, km._name_of
        km._TMUX = tm
        km._name_of = lambda sid: "web"
        try:
            self.assertTrue(self._route({"type": "sendMessage", "id": "sid-tmux", "text": "/model opus"}))
            self.assertTrue(self._route({"type": "sendMessage", "id": "sid-tmux", "text": "/effort high"}))
            self.assertIn(("set_model", "sid-tmux", "opus"), tm.calls)
            self.assertIn(("set_effort", "sid-tmux", "high"), tm.calls)
            self.assertEqual([c for c in tm.calls if c[0] == "send"], [], "neither reaches the pane as text")
            self.assertEqual(self.be.calls, [], "the SDK backend was untouched")
        finally:
            km._TMUX, km._name_of = saved, saved_name
            km._tmux_echo.pop("sid-tmux", None)
            km._model_switch_pending.pop("sid-tmux", None)        # the pick's switching-dots stamp — don't leak it

    def test_ui_op_falls_through_even_for_sdk_sid(self):
        # closeTab/openSession are backend-agnostic UI ops → never intercepted
        self.assertFalse(self._route({"type": "closeTab", "id": "sid-sdk"}))
        self.assertFalse(self._route({"type": "openSession", "id": "sid-sdk"}))

    def test_ask_ops_map_to_on_ask(self):
        self._route({"type": "answerAsk", "id": "sid-sdk", "target": 2})
        self._route({"type": "toggleAsk", "id": "sid-sdk", "target": 1})
        self._route({"type": "submitAsk", "id": "sid-sdk"})
        self._route({"type": "addCustomAsk", "id": "sid-sdk", "text": "custom"})
        self._route({"type": "cancelAsk", "id": "sid-sdk"})
        self._route({"type": "askText", "id": "sid-sdk", "text": "raw"})
        on_ask = [c for c in self.be.calls if c[0] == "on_ask"]
        self.assertEqual(on_ask, [
            ("on_ask", "sid-sdk", "answer", 2),
            ("on_ask", "sid-sdk", "toggle", 1),
            ("on_ask", "sid-sdk", "submit", None),
            ("on_ask", "sid-sdk", "custom", "custom"),
            ("on_ask", "sid-sdk", "cancel", None),
            ("on_ask", "sid-sdk", "text", "raw"),
        ])

    def test_interrupt_and_kill(self):
        self.assertTrue(self._route({"type": "interrupt", "id": "sid-sdk"}))
        self.assertTrue(self._route({"type": "endSession", "id": "sid-sdk"}))
        self.assertIn(("interrupt", "sid-sdk"), self.be.calls)
        self.assertIn(("kill", "sid-sdk"), self.be.calls)

    def test_setmodel_goes_live_not_slash(self):
        # model is a runtime control request (set_model), NOT a /model slash injection the SDK ignores
        self._route({"type": "setModel", "id": "sid-sdk", "value": "opus"})
        self.assertIn(("set_model", "sid-sdk", "opus"), self.be.calls)
        self.assertFalse(any(c == ("send", "sid-sdk", "/model opus") for c in self.be.calls))

    def test_a_typed_slash_model_effort_fast_routes_through_the_setters_not_literal_text(self):
        # THE BUG: the chat composer's "/model X" went to the backend as literal text; the CLI
        # executed it, but romp's registry, sdk-defaults.json and the reconnect's --model still said
        # the OLD model — so the user's switch silently reverted at the next reconnect. The composer
        # now takes the same door the timeline's sendCommand does (set_model & co).
        self._route({"type": "sendMessage", "id": "sid-sdk", "text": "/model claude-fable-5-1"})
        self._route({"type": "sendMessage", "id": "sid-sdk", "text": " /effort high "})
        self._route({"type": "sendMessage", "id": "sid-sdk", "text": "/fast on"})
        self.assertIn(("set_model", "sid-sdk", "claude-fable-5-1"), self.be.calls)
        self.assertIn(("set_effort", "sid-sdk", "high"), self.be.calls)
        self.assertIn(("set_fast", "sid-sdk", "on"), self.be.calls)
        self.assertEqual([c for c in self.be.calls if c[0] == "send"], [], "none of them reach the CLI as text")
        # …and the version pick lands in the shared pick memory exactly as a menu click would
        self._route({"type": "sendMessage", "id": "sid-sdk", "text": "/model claude-opus-4-8"})
        self.assertEqual(km._model_picks().get("opus"), "claude-opus-4-8")

    def test_other_slash_commands_and_plain_text_still_go_through_verbatim(self):
        # only the three the kernel has a setter for are intercepted; the CLI owns everything else,
        # and a bare "/model" (no value) is the CLI's own picker, not a set
        for text in ("/compact", "/model", "/clear", "hi /model opus", "/models please"):
            self._route({"type": "sendMessage", "id": "sid-sdk", "text": text})
            self.assertIn(("send", "sid-sdk", text), self.be.calls, text)
        self.assertEqual([c for c in self.be.calls if c[0] in ("set_model", "set_effort", "set_fast")], [])

    def test_a_setter_takes_only_a_value_it_can_vouch_for_the_rest_stays_the_clis(self):
        # the composer can type ANYTHING, and set_model persists its value as the seed for every
        # future session — so a typo, a multiline message that merely starts with the command, or a
        # fast value outside on/off must NOT be swallowed: it goes to the CLI verbatim, whose own
        # error the user then sees (as before the routing existed)
        for text in ("/model opsu", "/model opus\nnow refactor the parser", "/model opus please",
                     "/effort turbo", "/effort high\nand hurry", "/fast maybe", "/fast on off"):
            self._route({"type": "sendMessage", "id": "sid-sdk", "text": text})
            self.assertIn(("send", "sid-sdk", text), self.be.calls, text)
        self.assertEqual([c for c in self.be.calls if c[0] in ("set_model", "set_effort", "set_fast")], [])
        # what IS vouched for: a family alias, 'default', a catalog version id, and any well-formed
        # first-party id (the CLI validates the exact version; romp keeps the registry)
        for v in ("fable", "default", "claude-opus-4-8", "claude-opus-4-1"):
            self._route({"type": "sendMessage", "id": "sid-sdk", "text": "/model " + v})
            self.assertIn(("set_model", "sid-sdk", v), self.be.calls, v)
        self._route({"type": "sendMessage", "id": "sid-sdk", "text": "/effort ultracode"})
        self.assertIn(("set_effort", "sid-sdk", "ultracode"), self.be.calls)
        # the CLI's own 1M-context spelling of a family is vouched for like the tagged id
        self._route({"type": "sendMessage", "id": "sid-sdk", "text": "/model fable[1m]"})
        self.assertIn(("set_model", "sid-sdk", "fable[1m]"), self.be.calls)

    def test_the_floating_flag_clears_the_pin_from_both_picker_doors(self):
        # the submenu's "Latest" row: the chat/comment pickers post setModel with `floating`, the
        # timeline lane menu sends its "/model X" command with the same flag — both forget the
        # family's remembered pin and send the alias, so the family follows the CLI's newest again.
        # A plain alias (no flag) keeps leaving the memory alone.
        picks = km.jd.STATE / km.MODEL_PICKS_FILE_NAME
        picks.unlink(missing_ok=True)
        # the timeline keys sendCommand by session NAME (_sid_of resolves it through the live map)
        self._route({"type": "setModel", "id": "sid-sdk", "value": "claude-sonnet-4-6"})
        self.assertTrue(self._route({"type": "sendCommand", "name": "sid-sdk", "cmd": "/model claude-opus-4-8"}))
        self.assertEqual(km._model_picks(), {"sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-8"})
        self._route({"type": "setModel", "id": "sid-sdk", "value": "sonnet"})
        self._route({"type": "sendCommand", "name": "sid-sdk", "cmd": "/model opus"})
        self.assertEqual(km._model_picks(), {"sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-8"},
                         "a bare alias send never downgrades a pin (the standing design)")
        self._route({"type": "setModel", "id": "sid-sdk", "value": "sonnet", "floating": True})
        self.assertEqual(km._model_picks(), {"opus": "claude-opus-4-8"})
        self._route({"type": "sendCommand", "name": "sid-sdk", "cmd": "/model opus", "floating": True})
        self.assertEqual(km._model_picks(), {})
        self.assertEqual([c for c in self.be.calls if c[0] == "set_model"][-2:],
                         [("set_model", "sid-sdk", "sonnet"), ("set_model", "sid-sdk", "opus")],
                         "the alias itself is what reaches the backend")
        picks.unlink(missing_ok=True)

    def test_setmodel_mid_compaction_parks_as_a_queued_command(self):
        # the user 2026-07-01: switching the model while a compaction ran broke the compaction — the
        # kernel now PARKS the change (a queued '/model …' bubble) and _apply_pending_models fires it
        # the moment compaction ends. The optimistic click flag alone is enough to engage the park.
        import time as _time
        km._compact_clicked["sid-sdk"] = _time.time()    # the kernel just sent /compact for this session
        self._route({"type": "setModel", "id": "sid-sdk", "value": "opus"})
        self.assertFalse(any(c[0] == "set_model" for c in self.be.calls),
                         "mid-compaction the backend is NOT touched — that broke the compaction")
        self.assertEqual(km._pending_ops.get("sid-sdk"), [("model", "opus")], "parked for after the compaction")

    def test_seteffort_goes_to_backend_compact_still_slash(self):
        # effort routes to set_effort (the backend reconnects with --effort); compact has no control → slash
        self._route({"type": "setEffort", "id": "sid-sdk", "value": "high"})
        self._route({"type": "compactSession", "id": "sid-sdk"})
        self.assertIn(("set_effort", "sid-sdk", "high"), self.be.calls)
        self.assertFalse(any(c == ("send", "sid-sdk", "/effort high") for c in self.be.calls))
        self.assertIn(("send", "sid-sdk", "/compact"), [c for c in self.be.calls if c[0] == "send"])

    def test_setmode_and_rename(self):
        self._route({"type": "setMode", "id": "sid-sdk", "value": "plan"})
        self._route({"type": "renameSession", "id": "sid-sdk", "name": "newname"})
        self.assertIn(("set_mode", "sid-sdk", "plan"), self.be.calls)
        self.assertIn(("rename", "sid-sdk", "newname"), self.be.calls)

    def test_rename_rejects_bad_name(self):
        warned = []
        self.assertTrue(self._route_capture({"type": "renameSession", "id": "sid-sdk",
                                             "name": "bad name!"}, warned))
        self.assertTrue(any("session names" in w for w in warned))
        self.assertFalse(any(c[0] == "rename" for c in self.be.calls))

    def _route_capture(self, msg, sink):
        import json
        def send(s):
            try:
                sink.append(json.loads(s).get("text", ""))
            except Exception:
                pass
        return km._drive(msg, {"send": send})

    def _sent_to(self, sid):
        return [c[2] for c in self.be.calls if c[0] == "send" and c[1] == sid]

    def test_askfollowup_resolves_sid_from_itemid(self):
        # unified with tmux (the user 2026-07-01): an itemId follow-up now sends the WRAPPED body on the SDK
        # too — the user's text plus the romp-goal-id marker (for the reopen + the chat's ↩ Follow-up header),
        # no longer raw text. (No goal store in the test → the context quote is empty, so the body is just the
        # text + the marker tail.)
        self.assertTrue(self._route({"type": "askFollowUp", "itemId": "sid-sdk:g1", "text": "more"}))
        sent = self._sent_to("sid-sdk")
        self.assertTrue(sent and sent[0].startswith("more"), "the user's text leads the body")
        self.assertIn("<!-- romp-goal-id: sid-sdk:g1 -->", sent[0], "the goal marker rides along for the reopen")

    def test_askfollowup_optimistically_reopens_the_card(self):
        # SDK parity with the tmux path (the user 2026-06-23): a follow-up on an SDK card reopens its goal NOW
        # (optimistic_followup → board jumps to WORKING + a "Followed up" chip), not just sends the text. A
        # reopen (True) dirty-marks the views + wakes the pusher (the store write is invisible to the fleet
        # sig, so a plain push would have served the stale cached feed — the user 2026-07-05).
        km.jd.optimistic_followup = lambda sid, gid, **kw: (self.fu_calls.append((sid, gid)), True)[1]   # **kw tolerates text=/now=/stub=
        dirty_before = km._views_dirty[0]
        km._pusher_wake.clear()
        self.assertTrue(self._route({"type": "askFollowUp", "itemId": "sid-sdk:g1", "nudge": True, "text": "status?"}))
        sent = self._sent_to("sid-sdk")
        self.assertTrue(sent and "status?" in sent[0], "the follow-up body carries the text")
        self.assertIn("<!-- romp-injected -->", sent[0], "a nudge is romp-authored → gray bubble marker")
        self.assertIn(("sid-sdk", "sid-sdk:g1"), self.fu_calls, "the SDK follow-up reopens the goal optimistically")
        self.assertGreater(km._views_dirty[0], dirty_before, "a reopen dirty-marks the views past the cache")
        self.assertTrue(km._pusher_wake.is_set(), "…and wakes the pusher so the board updates at once")
        km._pusher_wake.clear()

    def _app_msgs(self, kind):
        return [m for app, m in self.app_sends if app == "feed" and m.get("type") == kind]

    def test_askfollowup_answers_the_prediction_instead_of_leaving_the_client_to_time_it_out(self):
        # the user 2026-07-21: the client used to give its optimistic Working flip 4 seconds to be confirmed
        # by a payload and then toast "that follow-up didn't move the card to Working" — a stopwatch standing
        # in for something the kernel knows exactly. It now ANSWERS: cardMoveAck carries whether the reopen
        # applied, so only a REAL refusal interrupts the user, and the buildId tells the client which payload
        # is the answer to this gesture (never one already in flight when the click landed).
        km.jd.optimistic_followup = lambda sid, gid, **kw: (self.fu_calls.append((sid, gid)), True)[1]
        km._user_goal_write.pop("sid-sdk", None)
        self.app_sends = []
        self.assertTrue(self._route({"type": "askFollowUp", "itemId": "sid-sdk:g1", "text": "also do X"}))
        acks = self._app_msgs("cardMoveAck")
        self.assertEqual(len(acks), 1, "exactly one answer per gesture")
        self.assertEqual(acks[0]["ids"], ["sid-sdk:g1"])
        self.assertTrue(acks[0]["ok"], "the reopen applied → nothing to interrupt the user about")
        self.assertIsInstance(acks[0]["buildId"], int)
        self.assertIn("sid-sdk", km._user_goal_write,
                      "…and the write is marked so it punches through a mid-flight judge pass (_feed_goals)")
        # the ack FOLLOWS the prediction it answers, so a client can never see the answer before the question
        kinds = [m["type"] for _a, m in self.app_sends if m.get("type") in ("cardPredict", "cardMoveAck")]
        self.assertEqual(kinds, ["cardPredict", "cardMoveAck"])

    def test_a_refused_reopen_acks_false_and_marks_no_user_write(self):
        # optimistic_followup returns False when the goal is GONE from the store (compacted/rotated away) or
        # sealed by a view clear. THAT is the one honest "it didn't stick", and the only case that toasts.
        km._user_goal_write.pop("sid-sdk", None)
        self.app_sends = []                                # setUp's double returns False
        self.assertTrue(self._route({"type": "askFollowUp", "itemId": "sid-sdk:gGONE", "text": "hello?"}))
        acks = self._app_msgs("cardMoveAck")
        self.assertEqual(len(acks), 1)
        self.assertFalse(acks[0]["ok"], "a refused reopen is reported as refused, not left to time out")
        self.assertNotIn("sid-sdk", km._user_goal_write, "nothing was written → nothing to punch through")

    def test_cardmove_op_is_retired(self):
        # the messageless Move to Working was removed (the user 2026-07-25): the op routes nowhere,
        # acks nothing, and marks no goal write
        km._user_goal_write.pop("sid-sdk", None)
        self.app_sends = []
        self.assertFalse(self._route({"type": "cardMove", "id": "sid-sdk",
                                      "itemId": "sid-sdk:g1", "to": "working"}))
        self.assertEqual(self._app_msgs("cardMoveAck"), [])
        self.assertNotIn("sid-sdk", km._user_goal_write)

    def test_askfollowup_without_itemid_just_sends(self):
        # a raw follow-up with no goal id (e.g. a typed message routed as askFollowUp) sends only — no reopen.
        self.fu_calls.clear()
        self.assertTrue(self._route({"type": "askFollowUp", "id": "sid-sdk", "text": "hi"}))
        self.assertIn(("send", "sid-sdk", "hi"), self.be.calls)
        self.assertEqual(self.fu_calls, [], "no itemId → nothing to reopen")

    def test_tmux_sessions_merges_sdk_rows(self):
        sess = km._tmux_sessions()                     # merges tmux (real/empty) + the fake SDK row
        self.assertIn("sid-sdk", sess)
        row = sess["sid-sdk"]
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["since"], 100)            # string -> int via _num
        self.assertEqual(row["model"], "m")
        self.assertIsNone(row["context"])              # SDK rows have no pane-OCR context%
        self.assertIsNone(row["compactPct"])


class LiveTailAndOpen(unittest.TestCase):
    """The live-tail merge + the transcript-less open fix (a just-created SDK session has no transcript,
    so discover() can't see it — without these it never opened: the user 2026-06-22)."""

    def setUp(self):
        self.be = FakeBackend()
        self.saved = (km._sdk, km._sessions, km._push_all, km._send_to_app)
        km._sdk = lambda: self.be
        km._push_all = lambda *a, **k: None
        km._send_to_app = lambda *a, **k: None
        km._tmux_echo.clear()                         # isolate the shared tmux-echo store across tests

    def tearDown(self):
        km._sdk, km._sessions, km._push_all, km._send_to_app = self.saved
        km._tmux_echo.clear()

    def test_merge_appends_fresh_live_atom_non_mutating(self):
        self.be._live = {"sid-sdk": [{"type": "assistant", "uuid": "new1", "t": 50,
                                      "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}]}
        session = {"turns": [{"id": "t", "atoms": [{"uuid": "old", "t": 10}], "ended": True}]}
        out = km._merge_live_atoms(session, "sid-sdk")
        self.assertEqual([a.get("uuid") for a in out["turns"][-1]["atoms"]], ["old", "new1"])  # sorted by t
        self.assertIsNot(out, session)                                   # copy, not mutation
        self.assertEqual(session["turns"][-1]["atoms"], [{"uuid": "old", "t": 10}])  # original untouched

    def test_merge_dedups_by_uuid(self):
        self.be._live = {"sid-sdk": [{"type": "assistant", "uuid": "dup", "t": 50,
                                      "message": {"role": "assistant", "content": [{"type": "text", "text": "x"}]}}]}
        # the disk twin CARRIES the streamed text (the normal case — same record, same uuid). A twin
        # that kept the uuid but LOST the text no longer dedups: see test_kernel_picker_text_salvage.
        session = {"turns": [{"id": "t", "atoms": [{"uuid": "dup", "t": 10, "type": "assistant",
                                                    "message": {"role": "assistant",
                                                                "content": [{"type": "text", "text": "x"}]}}],
                              "ended": True}]}
        out = km._merge_live_atoms(session, "sid-sdk")
        self.assertEqual(len(out["turns"][-1]["atoms"]), 1)              # transcript already has it → not re-added

    def test_merge_skips_when_no_live_atoms(self):
        session = {"turns": []}
        # a tmux sid with an empty echo store has no live atoms → the owning backend (tmux) returns [] and
        # the merge is a no-op (returns the same object). The SDK case is covered by the tests above.
        self.assertIs(km._merge_live_atoms(session, "sid-tmux"), session)

    def test_merge_reopens_the_turn_for_genuine_live_work(self):
        # a streaming assistant reply IS an in-flight turn — the merge must keep forcing it open
        self.be._live = {"sid-sdk": [{"type": "assistant", "uuid": "w1", "t": 50,
                                      "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}]}
        out = km._merge_live_atoms({"turns": [{"id": "t", "atoms": [{"uuid": "old", "t": 10}], "ended": True}]}, "sid-sdk")
        self.assertFalse(out["turns"][-1]["ended"])

    def test_merge_does_NOT_reopen_the_turn_for_a_command_atom(self):
        # the user 2026-07-02 (second half of the phantom-working fix): client.set_model() streams the
        # CLI's confirmation, msg_to_atom classifies it as a COMMAND atom (a completed exchange) — but
        # live_work still counted it and forced the turn open, and on a fresh session NOTHING ever closes
        # it (no reply is coming; a turn-less control request writes no transcript to supersede the live
        # atom). A command atom must keep the turn's real ended state on BOTH shapes:
        cmd = {"type": "assistant", "uuid": "c1", "t": 50, "command": True,
               "message": {"role": "assistant", "content": [{"type": "text", "text": "Set model to sonnet"}],
                           "stop_reason": "end_turn"}}
        # 1) a fresh session (no turns at all) — the synthesized live turn is born ENDED
        self.be._live = {"sid-sdk": [cmd]}
        out = km._merge_live_atoms({"turns": []}, "sid-sdk")
        self.assertTrue(out["turns"][-1]["ended"], "a lone command confirmation never reads as working")
        self.assertFalse(km._session_working(out["turns"]), "the chip stays consistent with the timeline")
        # 2) appended to an existing ENDED turn — stays ended
        self.be._live = {"sid-sdk": [cmd]}
        out = km._merge_live_atoms({"turns": [{"id": "t", "atoms": [{"uuid": "old", "t": 10}], "ended": True}]}, "sid-sdk")
        self.assertTrue(out["turns"][-1]["ended"])

    def test_alive_sessions_includes_transcriptless_sdk(self):
        km._sessions = lambda now: []                                   # discover sees nothing (no transcript yet)
        alive = km._alive_sessions(1000, {"sid-sdk": {"state": "waiting"}})
        self.assertIn("sid-sdk", [s["sid"] for s in alive])             # still opens

    def test_interrupt_record_does_not_floor_out_an_unlanded_echo(self):
        # A just-sent message shows via the optimistic echo before it hits disk. The interrupt record authors
        # 'human' but is a STOP event — it must NOT raise the echo-retirement floor (human_floor), or an
        # interrupted send that got a partial reply VANISHES on the next push (the user 2026-07-07). Give the
        # FakeBackend the REAL stale_echo prune so the regression is behavioural, and capture the floor.
        captured = {}
        def prune(sid, tx_uuids, tx_texts=(), human_floor=0):
            captured["hf"] = human_floor
            d = self.be._live.get(sid, [])
            self.be._live[sid] = [a for a in d if not (
                (a.get("uuid") in tx_uuids or (a.get("_echo_text") and a["_echo_text"] in tx_texts))
                or (a.get("_echo_text") and human_floor and a.get("t", 0) <= human_floor))]
        self.be.prune_live = prune
        self.be._live = {"sid-sdk": [{"type": "user", "uuid": "echo:x", "t": 50, "author": "human",
                                      "_echo_text": "please refactor everything",
                                      "message": {"role": "user", "content": [{"type": "text", "text": "please refactor everything"}]}}]}
        # transcript: a genuine human turn (t=10), then the interrupt record (t=100, author 'human'). The
        # sent message is NOT on disk yet — it lives only in the echo above.
        session = {"turns": [{"id": "t", "ended": True, "atoms": [
            {"type": "user", "uuid": "u0", "t": 10, "author": "human",
             "message": {"role": "user", "content": [{"type": "text", "text": "earlier task"}]}},
            {"type": "user", "uuid": "int1", "t": 100, "author": "human",
             "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}]}]}
        km._merge_live_atoms(session, "sid-sdk")
        self.assertEqual(captured["hf"], 10, "human_floor floors on the genuine turn (10), NOT the interrupt (100)")
        surviving = [a.get("_echo_text") for a in self.be._live.get("sid-sdk", [])]
        self.assertIn("please refactor everything", surviving, "the unlanded echo survives the interrupt")


class Responsiveness(unittest.TestCase):
    """The chat pusher is event-driven + short-poll so BOTH backends feel snappy (the user 2026-06-22):
    the SDK live-tail and /tick wake it instantly; a 0.5s backstop covers tmux mid-turn streaming."""

    def test_tick_wakes_the_pusher_and_short_backstop(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn("_pusher_wake.wait(0.5)", src)                  # short backstop poll
        tick = src.split('u.path == "/tick"', 1)[1].split("return self._send", 1)[0]
        self.assertIn("_pusher_wake.set()", tick)                     # /tick wakes the pusher (tmux turn-end shows now)


class SdkQueuedIndicator(unittest.TestCase):
    """An SDK session keeps its message queue in MEMORY (no transcript queue-op records), so the chat's
    'queued' indicator must read the backend's pending_queued, not _pending_queued (business 2026-06-23)."""

    def test_queued_event_reads_the_owning_backend_pending_queue(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        # build_session reads the queued texts from the OWNING backend, uniformly — the SDK from its
        # in-memory queue, tmux from the transcript's queue-operation records (TmuxBackend.pending_queued →
        # _pending_queued). No backend fork in build_session anymore.
        self.assertIn("be = Sessions.backend_for(sid)", src)
        # (the path_override arm is the read-only episode render — a closed episode has no live queue)
        self.assertIn("queued = [] if path_override else be.pending_queued(sid)", src)
        self.assertIn("return _pending_queued(p) if p else []", src)   # tmux pending_queued reads the transcript


class SdkMetadataParity(unittest.TestCase):
    """SDK sessions should surface the same statusline metadata as tmux (the user 2026-06-24): model/mode on
    OPEN (eager-connect), the git branch derived straight from the FOLDER, and a context-fill bar."""

    def test_git_branch_derived_from_folder(self):
        import subprocess, tempfile
        repo = os.path.dirname(BIN)   # the romp repo itself
        expected = subprocess.run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        # `rev-parse --abbrev-ref HEAD` reports the literal string "HEAD" on a DETACHED head, which is
        # not a branch name. _git_branch maps that to '' by contract, so the expectation has to make the
        # same mapping or this test can only pass on an attached checkout. Every `pull_request` CI run is
        # detached (actions/checkout builds the merge commit), so the unnormalized form failed 100% of PRs
        # while passing every push to main.
        if expected == "HEAD":
            expected = ""
        self.assertEqual(km._git_branch(repo), expected, "branch comes straight from the folder, no transcript")
        self.assertEqual(km._git_branch(tempfile.mkdtemp()), "", "not a repo → ''")
        self.assertEqual(km._git_branch(""), "", "no dir → ''")

    # -c identity so this never depends on (or is polluted by) the machine's global git config.
    IDENT = ("romp-test@example.invalid", "romp test")

    def _committed_repo(self):
        """A purpose-built repo with one commit on a branch: the fixture the kernel's own `git rev-parse`
        (_git_branch) runs against."""
        import tempfile
        d = tempfile.mkdtemp()
        # --template= (empty): a machine-global init.templateDir would otherwise copy its hooks into
        # this repo — a maintainer's gitleaks pre-commit hook failed the commit below whenever the
        # scanner wasn't on the test shell's PATH (2026-08-10). The test is about branch derivation;
        # no machine hook belongs in it.
        init_repo(d, "-q", "--template=", ident=self.IDENT)
        open(os.path.join(d, "f"), "w").write("x")
        git(d, "add", "f", ident=self.IDENT, text=False)
        git(d, "commit", "-qm", "c", ident=self.IDENT, text=False)
        return d

    def test_git_branch_is_empty_on_a_detached_head(self):
        """Detached HEAD → '' is the CONTRACT (kernel _git_branch docstring), not an accident. Pinned on a
        purpose-built repo so it holds regardless of how the checkout running these tests is shaped."""
        d = self._committed_repo()
        # _git_branch caches per cwd keyed on .git/HEAD's mtime. Both calls here land in the same test, so
        # on a coarse-granularity filesystem the two HEAD writes could share an mtime and serve a stale hit.
        # Clear between calls: this test is about the branch derivation, not the cache.
        km._branch_cache.clear()
        self.assertNotEqual(km._git_branch(d), "", "attached: a real branch name")
        git(d, "checkout", "-q", "--detach", ident=self.IDENT, text=False)
        km._branch_cache.clear()
        self.assertEqual(km._git_branch(d), "", "detached HEAD is not a branch name")

    def test_the_fixture_repos_forbid_background_git_work(self):
        # the kernel forks its own `git rev-parse` at this repo (_git_branch), outside the fixture runner's
        # -c flags, so the no-background keys have to sit in the repo's own config
        d = self._committed_repo()
        self.assertEqual(git(d, "config", "--local", "--get", "maintenance.auto").stdout.strip(), "false")

    def test_open_eager_connects_sdk_branch_fallback_and_ctx_passthrough(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        # opening a session eager-connects the SDK backend → model/mode publish before the first message
        oor = src.split("def _open_or_revive", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("be.connect(sid)", oor)
        # sysinfo branch falls back to the folder when the transcript lacks it
        self.assertIn('_norm_branch(meta.get("gitBranch")) or _git_branch(scwd)', src)   # detached 'HEAD' normalized at the merge point (the user 2026-08-13)
        # the SDK merge passes the backend's context-fill % through (was hardcoded None)
        self.assertIn('ctx = st.get("ctx")', src)
        self.assertIn("ctx if isinstance(ctx, (int, float)) else None", src)


if __name__ == "__main__":
    unittest.main()
