"""A subagent's sidechain messages never become PARENT-chat atoms (the user 2026-08-17).

The CLI streams a Task/Agent subagent's OWN turns to the SDK client tagged with
parent_tool_use_id — its kickoff prompt as a UserMessage, its replies and tool calls as
AssistantMessages. msg_to_atom used to check that tag only against the session's Skill
tool_use ids, so subagent traffic fell through to the generic branches and the kickoff
prompt rendered in the parent chat as a giant fully-expanded instruction box below the
collapsed tool group (screenshot-reported). Worse, the leak was a live-tail atom whose
uuid never appears in the parent's transcript (the subagent writes its own file), so
the transcript merge never superseded it and the box persisted.

The contract now: parent_tool_use_id set and not a known Skill tool_use → the message
is a subagent's, and msg_to_atom returns None. The subagent's designed surfaces are the
Task/Agent head's prompt+report folds and the background-task rows. Skill payloads
(skills run inline, not as subagents) keep their skillMd classification.
"""
import os
import tempfile
import unittest
from romp_load import load_source

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

PROMPT = ("You are drafting prose for two research notes in a vault. Return ONLY the "
          "three deliverables in the exact output format at the end.")


class TextBlock:
    def __init__(self, text):
        self.text = text


def _load(name):
    return load_source(name, os.path.join(BIN, "romp_sdk_backend.py"))


class SidechainDrop(unittest.TestCase):
    def test_subagent_kickoff_prompt_is_dropped(self):
        sb = _load("romp_sdk_backend_sc1")

        class UserMessage:
            uuid = "11111111-2222-3333-4444-555555555555"
            parent_tool_use_id = "toolu_agent1"

            def __init__(self, content):
                self.content = content

        # the expanded-instruction-box leak: the subagent's first message is its full prompt
        a = sb.msg_to_atom(UserMessage([TextBlock(PROMPT)]), "s", "f", 5, skill_tool_ids={"skill9"})
        self.assertIsNone(a)
        # with NO skill set at all, still dropped — the tag alone marks it as not the parent's
        b = sb.msg_to_atom(UserMessage([TextBlock(PROMPT)]), "s", "f", 5)
        self.assertIsNone(b)

    def test_subagent_assistant_replies_are_dropped(self):
        sb = _load("romp_sdk_backend_sc2")

        class AssistantMessage:
            uuid = "11111111-2222-3333-4444-666666666666"
            parent_tool_use_id = "toolu_agent1"
            model = "m"
            stop_reason = None

            def __init__(self, content):
                self.content = content

        # untagged handling would render the subagent's words as the PARENT speaking
        a = sb.msg_to_atom(AssistantMessage([TextBlock("working on the notes…")]), "s", "f", 5)
        self.assertIsNone(a)

    def test_parent_messages_without_the_tag_still_flow(self):
        sb = _load("romp_sdk_backend_sc3")

        class UserMessage:
            uuid = "11111111-2222-3333-4444-777777777777"

            def __init__(self, content):
                self.content = content

        class AssistantMessage:
            uuid = "11111111-2222-3333-4444-888888888888"
            model = "m"
            stop_reason = "end_turn"

            def __init__(self, content):
                self.content = content

        ua = sb.msg_to_atom(UserMessage([TextBlock("hello")]), "s", "f", 5)
        self.assertEqual(ua["type"], "user")
        aa = sb.msg_to_atom(AssistantMessage([TextBlock("hi")]), "s", "f", 5)
        self.assertEqual(aa["type"], "assistant")

    def test_none_tag_is_not_a_sidechain(self):
        sb = _load("romp_sdk_backend_sc4")

        class UserMessage:
            uuid = "11111111-2222-3333-4444-999999999999"
            parent_tool_use_id = None      # the SDK types default the field to None on parent turns

            def __init__(self, content):
                self.content = content

        a = sb.msg_to_atom(UserMessage([TextBlock("hello")]), "s", "f", 5)
        self.assertEqual(a["type"], "user")


if __name__ == "__main__":
    unittest.main()
