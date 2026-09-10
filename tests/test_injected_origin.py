#!/usr/bin/env python3
"""Harness-injected user-role records are classified by their FIELDS and shown as sourced notices, never as
the user's bubble (the user 2026-09-07: background-agent reports and system notices were rendering as their
own typed words).

Claude Code 2.1.263 changed how it words an injected turn: a fixed preamble paragraph
("[SYSTEM NOTIFICATION - NOT USER INPUT] …") now sits AHEAD of the <task-notification> tag, and the
tag-anchored text test the event model used stopped seeing those records as harness-injected — so under
sdk_human (every romp SDK session) they authored 'human'. The fix reads the record's own `origin.kind`
stamp first (claude_agent_sdk MessageOrigin), keeps the text shapes as the fallback for unstamped records
(preamble included), carries the stamp on the atom, and build_session ships a `source` the chat renders as
a labelled notice card. Synthetic fixtures only: invented text, placeholder ids, hostname TESTHOST."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_injected", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_injected", os.path.join(BIN, "romp_sdk_backend.py"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600

PREAMBLE = ("[SYSTEM NOTIFICATION - NOT USER INPUT]\n"
            "This is an automated background-task event, NOT a message from the user.\n"
            "Do NOT interpret this as user acknowledgement, confirmation, or response to any pending question.\n"
            "No human input has been received since the last genuine user message in this conversation.")
NOTIF_INNER = ("\n<task-id>11111111aaaa2222</task-id>\n<tool-use-id>toolu_0abcDEF123</tool-use-id>\n"
               "<output-file>/tmp/TESTHOST/tasks/11111111aaaa2222.output</output-file>\n"
               "<status>completed</status>\n<summary>Agent \"widget audit\" came to rest</summary>\n"
               "<result>Found 3 widgets: **A**, B, C.\n\nA is stale; the rest are fine.</result>\n")
NOTIF = "<task-notification>" + NOTIF_INNER + "</task-notification>"
CMD_NOTIF = ("<task-notification>\n<task-id>abcabc123</task-id>\n<tool-use-id>toolu_9zzz888</tool-use-id>\n"
             "<status>completed</status>\n<summary>Background command \"measure the loop rate\" completed (exit code 0)"
             "</summary>\n</task-notification>")
SCHEDULED = ("[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT]\n"
             "This turn was started automatically by a schedule, not typed live by the user.\n"
             "The content below is the stored prompt of a scheduled task on this account.\n\n"
             "Sweep the notes-api routes for slow spots and report.")
PEER_TEXT = ("A peer session sent a message while you were working:\n"
             "<cross-session-message from=\"web [a1b2]\" from-name=\"web\">\n"
             "The routes are ready for review on the api branch.\n</cross-session-message>\n\n"
             "This came from another Claude session — not typed by your user.")
CONTINUATION = "The user approved the plan in the browser and chose to implement it in this session."


def tb(text):
    return [{"type": "text", "text": text}]


class AuthorFieldFirst(unittest.TestCase):
    """author_of reads the record's origin stamp BEFORE any text shape; every non-human kind is not the human
    whatever sdk_human says (that flag only reads an UNSTAMPED "sdk" prompt as the composer's)."""

    def test_preamble_led_notification_was_the_human_bubble_and_is_system_now(self):
        # THE reported shape: CLI 2.1.263's preamble ahead of the tag. Red before the fix — the start-anchored
        # tag test missed it and promptSource "sdk" + sdk_human authored the record 'human'.
        blocks = tb(PREAMBLE + "\n\n" + NOTIF)
        self.assertEqual(em.author_of(blocks, "sdk", {}, sdk_human=True, origin={"kind": "task-notification"}), "system")
        self.assertEqual(em.author_of(blocks, "sdk", {}, sdk_human=True), "system",
                         "the unstamped fallback: the preamble line itself marks the record as the harness's")

    def test_origin_kind_outranks_the_text_shape(self):
        # a stamped notification whose text happens to carry NO recognisable wrapper at all is still not the human
        self.assertEqual(em.author_of(tb("plain words with no tag"), "sdk", {}, sdk_human=True,
                                      origin={"kind": "task-notification"}), "system")

    def test_scheduled_trigger_is_a_programmatic_prompt_not_a_fold_in(self):
        # a scheduled task's fired prompt IS work to do: 'sdk' opens a turn (never 'system', which folds in;
        # never 'human', which is the blue bubble). Both the stamp and the preamble text say so.
        self.assertEqual(em.author_of(tb(SCHEDULED), "sdk", {}, sdk_human=True,
                                      origin={"kind": "task-notification", "subkind": "scheduled-trigger"}), "sdk")
        self.assertEqual(em.author_of(tb(SCHEDULED), "sdk", {}, sdk_human=True), "sdk")
        self.assertTrue(em._is_opener({"type": "user", "author": "sdk"}))
        self.assertFalse(em._is_opener({"type": "user", "author": "system"}))

    def test_peer_origin_is_a_teammate_by_stamp_and_by_the_new_lead_ins(self):
        self.assertEqual(em.author_of(tb("anything at all"), "sdk", {}, sdk_human=True,
                                      origin={"kind": "peer", "from": "web [a1b2]", "name": "web"}), "teammate")
        self.assertEqual(em.author_of(tb(PEER_TEXT), "sdk", {}, sdk_human=True), "teammate",
                         "unstamped: the CLI's 'A peer session sent a message while you were working:' lead-in")
        self.assertEqual(em.author_of(tb("<cross-session-message from=\"api\">hi</cross-session-message>"),
                                      "sdk", {}, sdk_human=True), "teammate")
        self.assertEqual(em.author_of(tb("Another Claude session sent a message while you were working:\nhi"),
                                      "sdk", {}, sdk_human=True), "teammate")
        # the pre-existing form still matches, and a conversation summary that merely QUOTES one does not
        self.assertEqual(em.author_of(tb("Another Claude session sent a message:\nhi"), "sdk", {}, sdk_human=True), "teammate")
        self.assertEqual(em.author_of(tb("<turn>\nUSER ASKED: Another Claude session sent a message: x"), "sdk", {},
                                      sdk_human=True), "human")

    def test_peer_send_message_subkind_is_a_teammate_not_a_finished_task(self):
        # the SDK's OTHER task-notification subkind (TaskNotificationOriginSubkind "peer-send-message"): a message
        # from another of the user's sessions, delivered on the task channel. Another session's words → the
        # teammate card, never 'system' (a finished background task that folds into the running turn) and never
        # an opener (review find, 2026-09-09, on #1099)
        origin = {"kind": "task-notification", "subkind": "peer-send-message"}
        self.assertEqual(em.author_of(tb("The routes are ready for review."), "sdk", {}, sdk_human=True, origin=origin),
                         "teammate")
        self.assertEqual(em.author_of(tb(PREAMBLE + "\n\nThe routes are ready for review."), "sdk", {}, sdk_human=True,
                                      origin=origin), "teammate",
                         "the stamp outranks the task channel's own preamble on the text")
        self.assertFalse(em._is_opener({"type": "user", "author": "teammate"}))
        # the subkind-less stamp is still the finished background task
        self.assertEqual(em.author_of(tb("The routes are ready for review."), "sdk", {}, sdk_human=True,
                                      origin={"kind": "task-notification"}), "system")

    def test_other_injected_kinds_are_never_the_human(self):
        for kind in ("coordinator", "channel", "auto-continuation", "observer", "unclassified", "brand-new-kind"):
            self.assertEqual(em.author_of(tb(CONTINUATION), "sdk", {}, sdk_human=True, origin={"kind": kind}), "sdk", kind)

    def test_the_human_is_still_the_human(self):
        self.assertEqual(em.author_of(tb("do the thing"), "sdk", {}, sdk_human=True), "human")
        self.assertEqual(em.author_of(tb("do the thing"), "sdk", {}, sdk_human=True, origin={"kind": "human"}), "human")
        self.assertEqual(em.author_of(tb("do the thing"), "typed", {}, origin={"kind": "human"}), "human")
        # a prompt that merely QUOTES the sentinel mid-text is the user's words (start-anchored, like the tags)
        self.assertEqual(em.author_of(tb("why does it say [SYSTEM NOTIFICATION - NOT USER INPUT] here?"), "sdk", {},
                                      sdk_human=True), "human")
        # a real prompt with a notification APPENDED is still the human's (the kernel splits the reminder off)
        self.assertEqual(em.author_of(tb("do the thing\n" + NOTIF), "sdk", {}, sdk_human=True), "human")

    def test_postal_mail_inside_a_peer_stamped_record_keeps_its_postal_author(self):
        # postal already has a card — never duplicated: a marker-bearing body wins over the peer stamp
        a = em.author_of(tb("hello <!-- romp-msg-id: 1700000000.111_222.TESTHOST -->"), "sdk", {}, sdk_human=True,
                         origin={"kind": "peer"})
        self.assertIsInstance(a, dict)
        self.assertEqual(a["mid"], "1700000000.111_222.TESTHOST")


class PreambleStrip(unittest.TestCase):
    def test_leading_paragraph_is_lifted_out(self):
        rest, pre = em.strip_harness_preamble(PREAMBLE)
        self.assertEqual(rest, "")
        self.assertTrue(pre.startswith("[SYSTEM NOTIFICATION - NOT USER INPUT]"))
        self.assertIn("No human input", pre, "the whole paragraph, not just the sentinel line")

    def test_paragraph_after_a_joined_block_is_lifted_and_the_rest_kept(self):
        # blocks join with a space, so the sentinel can sit mid-line; the paragraph ends at the blank line
        rest, pre = em.strip_harness_preamble("do the thing " + PREAMBLE + "\n\nkeep this")
        self.assertEqual(rest, "do the thing\n\nkeep this")
        self.assertTrue(pre.startswith("[SYSTEM NOTIFICATION"))

    def test_scheduled_preamble_leaves_the_stored_prompt(self):
        rest, pre = em.strip_harness_preamble(SCHEDULED)
        self.assertEqual(rest, "Sweep the notes-api routes for slow spots and report.")
        self.assertTrue(pre.startswith("[SCHEDULED TASK"))

    def test_nothing_to_strip(self):
        self.assertEqual(em.strip_harness_preamble("plain"), ("plain", ""))
        self.assertEqual(em.strip_harness_preamble(""), ("", ""))


class InjectedSourceUnit(unittest.TestCase):
    def test_an_agents_notification_names_the_agent(self):
        # the model DOES see which subagent it came from — the <summary> carries the description
        src = em.injected_source("system", {"kind": "task-notification"}, [NOTIF_INNER])
        self.assertEqual(src, {"kind": "subagent", "name": "widget audit", "status": "completed"})

    def test_a_commands_notification_is_a_task(self):
        inner = CMD_NOTIF.replace("<task-notification>", "").replace("</task-notification>", "")
        self.assertEqual(em.injected_source("system", None, [inner]),
                         {"kind": "task", "name": "measure the loop rate", "status": "completed"})

    def test_a_plain_reminder_is_not_mistaken_for_a_task(self):
        # _parse_task_notification defaults a status for any wrapped text — a reminder must not become a "task"
        self.assertEqual(em.injected_source("system", None, ["Your context is getting full."]),
                         {"kind": "system", "label": "System reminder"})

    def test_stamped_kinds(self):
        self.assertEqual(em.injected_source("sdk", {"kind": "task-notification", "subkind": "scheduled-trigger"}),
                         {"kind": "system", "label": "Scheduled task"})
        self.assertEqual(em.injected_source("teammate", {"kind": "peer", "name": "web", "senderTaskId": "t1"}),
                         {"kind": "peer", "name": "web", "subagent": True})
        self.assertEqual(em.injected_source("teammate", {"kind": "peer", "from": "api [c3d4]"}),
                         {"kind": "peer", "name": "api [c3d4]", "subagent": False})
        self.assertEqual(em.injected_source("sdk", {"kind": "coordinator"}),
                         {"kind": "peer", "name": "the coordinating session", "subagent": False})
        self.assertEqual(em.injected_source("sdk", {"kind": "auto-continuation"}),
                         {"kind": "system", "label": "Automatic continuation"})

    def test_peer_send_message_subkind_is_the_peer_notice(self):
        # the task channel's other subkind carries none of kind "peer"'s sender fields → "another session"; a
        # name, when the CLI gives one, is used (review find, 2026-09-09, on #1099)
        self.assertEqual(em.injected_source("teammate", {"kind": "task-notification", "subkind": "peer-send-message"}),
                         {"kind": "peer", "name": "another session", "subagent": False})
        self.assertEqual(em.injected_source("teammate", {"kind": "task-notification", "subkind": "peer-send-message",
                                                         "name": "web"}),
                         {"kind": "peer", "name": "web", "subagent": False})
        # never named from a notification that happens to ride along as a reminder
        self.assertEqual(em.injected_source("teammate", {"kind": "task-notification", "subkind": "peer-send-message"},
                                            [NOTIF_INNER])["kind"], "peer")

    def test_an_unstamped_scheduled_prompt_keeps_its_label_by_its_lifted_preamble(self):
        # no stamp (an older CLI, the live echo): the paragraph strip_harness_preamble lifted names what fired the
        # prompt, so the record keeps the "Scheduled task" label the stamped path gives it (review find, 2026-09-09,
        # on #1099); with nothing lifted, the unstamped 'sdk' prompt keeps its neutral note as before
        _rest, pre = em.strip_harness_preamble(SCHEDULED)
        self.assertEqual(em.injected_source("sdk", None, (), pre), {"kind": "system", "label": "Scheduled task"})
        self.assertIsNone(em.injected_source("sdk", None, (), ""))
        self.assertIsNone(em.injected_source("sdk", None, (), em.strip_harness_preamble(PREAMBLE)[1]),
                          "a notification's preamble on a programmatic prompt names no scheduled task")

    def test_the_human_and_romp_and_unstamped_sdk_have_no_source(self):
        self.assertIsNone(em.injected_source("human", None, [NOTIF_INNER]),
                          "a prompt that arrived with a notification attached stays the user's bubble")
        self.assertIsNone(em.injected_source("romp", None))
        self.assertIsNone(em.injected_source({"peer": None, "mid": "x", "kind": ""}, None))
        self.assertIsNone(em.injected_source("sdk", None), "an unstamped programmatic prompt keeps today's neutral note")


class ParseSessionCarriesTheStamp(unittest.TestCase):
    """The file adapter: the atom authors by the stamp under sdk_human and carries `origin`."""

    def _parse(self, recs):
        td = tempfile.mkdtemp()
        p = Path(td) / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return em.parse_session(str(p), rompuuid=SID, name="impl", dir="/TESTDIR", now=NOW, sdk_human=True)

    def _iso(self, t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _recs(self, text, origin=None, ps="sdk"):
        u = {"type": "user", "timestamp": self._iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "start"}}
        a = {"type": "assistant", "timestamp": self._iso(T0 + 5), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}}
        n = {"type": "user", "timestamp": self._iso(T0 + 10), "uuid": "u2", "parentUuid": "a1", "promptSource": ps,
             "message": {"role": "user", "content": text}}
        if origin:
            n["origin"] = origin
        return [u, a, n]

    def _atom(self, sess):
        for t in sess["turns"]:
            for a in t["atoms"]:
                if a.get("uuid") == "u2":
                    return a
        self.fail("the injected record made no atom")

    def test_stamped_preamble_notification_is_system_with_its_origin(self):
        a = self._atom(self._parse(self._recs(PREAMBLE + "\n\n" + NOTIF, {"kind": "task-notification"})))
        self.assertEqual(a["author"], "system")
        self.assertEqual(a["origin"], {"kind": "task-notification"})

    def test_unstamped_preamble_notification_is_system_by_text(self):
        a = self._atom(self._parse(self._recs(PREAMBLE + "\n\n" + NOTIF)))
        self.assertEqual(a["author"], "system")
        self.assertNotIn("origin", a)

    def test_peer_stamp_carries_only_the_documented_keys(self):
        a = self._atom(self._parse(self._recs(PEER_TEXT, {"kind": "peer", "from": "web [a1b2]", "name": "web",
                                                          "body": "The routes are ready.", "verifiedPeerPid": 4242,
                                                          "fromSession": "zzz"})))
        self.assertEqual(a["author"], "teammate")
        self.assertEqual(a["origin"], {"kind": "peer", "from": "web [a1b2]", "name": "web", "body": "The routes are ready."})

    def test_a_typed_prompt_is_the_human_with_no_stamp(self):
        a = self._atom(self._parse(self._recs("please rerun the tests", {"kind": "human"})))
        self.assertEqual(a["author"], "human")
        self.assertNotIn("origin", a)


class BuildSessionSourcedEvents(unittest.TestCase):
    """build_session: each injected shape becomes a user-role event with `source` (+ the preamble lifted into
    `preamble`), human False; a genuine prompt stays human with no source. The chat's renderInjected reads
    exactly these fields."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
                      km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"
        km._read_task_store = lambda fsid, fold=None: []
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def _iso(self, t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _write(self, msgs):
        # msgs: (uuid, parent, promptSource, content, origin|None)
        recs = []
        for u, p, ps, c, origin in msgs:
            r = {"type": "user", "timestamp": self._iso(T0 + len(recs) * 10), "uuid": u, "parentUuid": p,
                 "promptSource": ps, "message": {"role": "user", "content": c}}
            if origin:
                r["origin"] = origin
            recs.append(r)
            recs.append({"type": "assistant", "timestamp": self._iso(T0 + len(recs) * 10), "uuid": u + "a",
                         "parentUuid": u, "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}})
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _events(self, *shapes):
        msgs = [("u0", None, "typed", "start", None)]
        prev = "u0a"
        for i, (text, origin) in enumerate(shapes, 1):
            msgs.append(("u%d" % i, prev, "sdk", text, origin))
            prev = "u%da" % i
        self._write(msgs)
        evs = km.build_session(SID, NOW)["events"]
        return {e["uuid"]: e for e in evs if e.get("kind") in ("user", "teammate") and e.get("uuid")}

    def test_a_preamble_led_notification_is_a_subagent_notice_with_the_preamble_folded(self):
        ev = self._events((PREAMBLE + "\n\n" + NOTIF, {"kind": "task-notification"}))["u1"]
        self.assertEqual(ev["kind"], "user")
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"], {"kind": "subagent", "name": "widget audit", "status": "completed"})
        self.assertEqual(ev["md"], "", "the preamble is not the message — nothing is left as text")
        self.assertTrue(ev["preamble"].startswith("[SYSTEM NOTIFICATION - NOT USER INPUT]"), "kept for the fold")
        self.assertEqual(len(ev["reminders"]), 1)
        self.assertIn("<result>Found 3 widgets", ev["reminders"][0], "the agent's report rides for the card body")

    def test_the_same_without_a_stamp_by_the_text_fallback(self):
        ev = self._events((PREAMBLE + "\n\n" + NOTIF, None))["u1"]
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"]["kind"], "subagent")
        self.assertEqual(ev["md"], "")

    def test_tag_first_notification_still_a_subagent_notice(self):
        # the pre-2.1.263 shape must keep working: origin + the tag opening the record
        ev = self._events((NOTIF, {"kind": "task-notification"}))["u1"]
        self.assertEqual(ev["source"], {"kind": "subagent", "name": "widget audit", "status": "completed"})
        self.assertNotIn("preamble", ev)

    def test_system_reminder_wrapped_notification_with_the_preamble_inside(self):
        wrapped = "<system-reminder>\n" + PREAMBLE + "\n\n" + NOTIF + "\n</system-reminder>"
        ev = self._events((wrapped, None))["u1"]
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"]["kind"], "subagent", "the notification inside the reminder names the agent")
        self.assertEqual(ev["md"], "")

    def test_peer_delivery_is_a_teammate_card_from_the_named_session(self):
        ev = self._events((PEER_TEXT, {"kind": "peer", "from": "web [a1b2]", "name": "web",
                                       "body": "The routes are ready for review on the api branch."}))["u1"]
        self.assertEqual(ev["kind"], "teammate")
        self.assertEqual(ev["source"], {"kind": "peer", "name": "web", "subagent": False})
        self.assertEqual(ev["blocks"][0]["id"], "web", "the envelope's from-name")
        self.assertEqual(ev["blocks"][0]["body"], "The routes are ready for review on the api branch.",
                         "the block body, never the CLI's boilerplate around it")

    def test_an_unstamped_peer_lead_in_still_parses_the_cross_session_block(self):
        ev = self._events((PEER_TEXT, None))["u1"]
        self.assertEqual(ev["kind"], "teammate")
        self.assertEqual(ev["blocks"][0]["id"], "web")
        self.assertNotIn("This came from another Claude session", ev["blocks"][0]["body"])

    def test_a_background_subagents_message_is_labelled_as_such(self):
        ev = self._events(("hi from below", {"kind": "peer", "from": "sub", "senderTaskId": "t9", "body": "hi from below"}))["u1"]
        self.assertEqual(ev["kind"], "teammate")
        self.assertTrue(ev["source"]["subagent"])
        self.assertEqual(ev["blocks"][0]["body"], "hi from below")

    def test_scheduled_trigger_is_a_system_notice_carrying_the_stored_prompt(self):
        ev = self._events((SCHEDULED, {"kind": "task-notification", "subkind": "scheduled-trigger"}))["u1"]
        self.assertEqual(ev["kind"], "user")
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"], {"kind": "system", "label": "Scheduled task"})
        self.assertEqual(ev["md"], "Sweep the notes-api routes for slow spots and report.")
        self.assertTrue(ev["preamble"].startswith("[SCHEDULED TASK"))

    def test_an_unstamped_scheduled_prompt_keeps_its_label_and_preamble(self):
        # the same record without the stamp (the text fallback): still the labelled notice, the stored prompt as
        # its text and the preamble kept for its fold, never an unlabelled neutral note (review find,
        # 2026-09-09, on #1099)
        ev = self._events((SCHEDULED, None))["u1"]
        self.assertEqual(ev["kind"], "user")
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"], {"kind": "system", "label": "Scheduled task"})
        self.assertEqual(ev["md"], "Sweep the notes-api routes for slow spots and report.")
        self.assertTrue(ev["preamble"].startswith("[SCHEDULED TASK"))

    def test_a_peer_send_message_delivery_is_a_teammate_card_not_a_task(self):
        # the task channel's "peer-send-message" subkind: the teammate card from "another session" (that subkind
        # names no sender), its body the message, never a "background task" notice folded into the running
        # turn (review find, 2026-09-09, on #1099)
        ev = self._events(("The routes are ready for review on the api branch.",
                           {"kind": "task-notification", "subkind": "peer-send-message"}))["u1"]
        self.assertEqual(ev["kind"], "teammate")
        self.assertEqual(ev["source"], {"kind": "peer", "name": "another session", "subagent": False})
        self.assertEqual(ev["blocks"][0]["body"], "The routes are ready for review on the api branch.")

    def test_auto_continuation_is_a_system_notice(self):
        ev = self._events((CONTINUATION, {"kind": "auto-continuation"}))["u1"]
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"], {"kind": "system", "label": "Automatic continuation"})
        self.assertEqual(ev["md"], CONTINUATION)

    def test_a_genuine_prompt_has_no_source_and_keeps_its_bubble(self):
        evs = self._events(("please rerun the tests", {"kind": "human"}), ("and a notification came with this one\n" + NOTIF, None))
        self.assertNotIn("source", evs["u1"])
        self.assertNotIn("preamble", evs["u1"])
        # a real prompt that arrived with a notification attached: the user's words stay, the card nests below
        self.assertEqual(evs["u2"]["md"], "and a notification came with this one")
        self.assertEqual(len(evs["u2"]["reminders"]), 1)
        self.assertNotIn("source", evs["u2"], "never a sourced notice — the bubble is the user's")

    def test_a_bare_system_reminder_is_a_system_notice(self):
        ev = self._events(("<system-reminder>Your context is getting full.</system-reminder>", None))["u1"]
        self.assertFalse(ev["human"])
        self.assertEqual(ev["source"], {"kind": "system", "label": "System reminder"})
        self.assertEqual(ev["reminders"], ["Your context is getting full."])


class LiveAtomCarriesOrigin(unittest.TestCase):
    """The SDK live stream leads the disk write: msg_to_atom must carry UserMessage.origin exactly as the file
    adapter carries the record's stamp, or the live tail shows the preamble as a message until the record lands."""

    class _TextBlock:
        def __init__(self, text):
            self.text = text

    class _UserMessage:
        def __init__(self, content, origin=None):
            self.content, self.uuid, self.tool_use_result, self.origin = content, "u1", None, origin

    _TextBlock.__name__ = "TextBlock"
    _UserMessage.__name__ = "UserMessage"

    def test_origin_rides_the_live_atom_with_the_documented_keys_only(self):
        m = self._UserMessage([self._TextBlock(PREAMBLE + "\n\n" + NOTIF)],
                              origin={"kind": "task-notification", "verifiedPeerPid": 7, "subkind": "x"})
        a = sb.msg_to_atom(m, "s", "f", 5)
        self.assertEqual(a["origin"], {"kind": "task-notification", "subkind": "x"})

    def test_no_origin_no_key(self):
        a = sb.msg_to_atom(self._UserMessage([self._TextBlock("hello")]), "s", "f", 5)
        self.assertNotIn("origin", a)
        a = sb.msg_to_atom(self._UserMessage([self._TextBlock("hello")], origin={"bogus": 1}), "s", "f", 5)
        self.assertNotIn("origin", a, "a stamp without a string kind is not a stamp")

    def test_the_two_adapters_agree_on_the_keys(self):
        # sdk_backend loads standalone and mirrors event_model's tuple — the drift pin
        self.assertEqual(tuple(sb.ORIGIN_KEYS), tuple(em._ORIGIN_KEYS))
        self.assertEqual(sb._ORIGIN_BODY_CAP, em._RESULT_CAP)


class QueuedCopiesAndThePopover(unittest.TestCase):
    def test_a_queued_preamble_notification_is_not_the_users_pending_message(self):
        # the CLI queues a notification that lands while the session is busy; the preamble-led copy must not
        # show as "1 queued message" any more than the tag-led copy did (2026-06-30)
        self.assertFalse(km._genuine_queued(PREAMBLE + "\n\n" + NOTIF))
        self.assertFalse(km._genuine_queued(NOTIF))
        self.assertTrue(km._genuine_queued("a message the user really typed"))

    def test_record_origin_reads_both_record_shapes(self):
        self.assertEqual(em._record_origin({"type": "user", "origin": {"kind": "peer", "name": "web", "fromSession": "z"}}),
                         {"kind": "peer", "name": "web"})
        self.assertEqual(em._record_origin({"type": "attachment", "attachment": {"type": "queued_command",
                                                                                 "commandMode": "task-notification"}}),
                         {"kind": "task-notification"}, "the older spelling on a queued copy")
        self.assertIsNone(em._record_origin({"type": "user"}))


if __name__ == "__main__":
    unittest.main()
