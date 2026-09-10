#!/usr/bin/env python3
"""author_of's sdk_human path (the user 2026-06-22): a human message to an SDK-backed romp session
lands in the transcript as promptSource "sdk" (it arrives over the programmatic stream-json channel),
so without this it rendered as the gray 'sdk' author instead of the blue human bubble. With sdk_human
set, an UNMARKED 'sdk' prompt is the human; romp-injected / postal markers still win."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(SCRIPTS, "romp-event-model"))

TEXT = [{"type": "text", "text": "do the thing"}]
INJECTED = [{"type": "text", "text": "status update <!-- romp-injected -->"}]


class AuthorSdkHuman(unittest.TestCase):
    def test_sdk_prompt_is_human_only_for_sdk_backed_sessions(self):
        self.assertEqual(em.author_of(TEXT, "sdk", {}, sdk_human=True), "human")   # SDK session → the human
        self.assertEqual(em.author_of(TEXT, "sdk", {}, sdk_human=False), "sdk")    # elsewhere → genuine sdk

    def test_default_is_unchanged_sdk(self):
        self.assertEqual(em.author_of(TEXT, "sdk", {}), "sdk")                      # default off → no behavior change

    def test_romp_injected_marker_wins_over_sdk_human(self):
        # a romp nudge to an SDK session is still gray 'romp', not the human, even with sdk_human on
        self.assertEqual(em.author_of(INJECTED, "sdk", {}, sdk_human=True), "romp")

    def test_typed_and_system_unaffected(self):
        self.assertEqual(em.author_of(TEXT, "typed", {}, sdk_human=True), "human")
        self.assertEqual(em.author_of(TEXT, "system", {}, sdk_human=True), "system")

    def test_harness_system_wrappers_author_system_not_human(self):
        # a background-task <task-notification> / <system-reminder> arrives over the SDK channel as
        # promptSource 'sdk'; without this it authored 'human' → opened a turn → the planner force-pinned a
        # junk goal titled "<task-notification>" (the user 2026-06-30). Anchored at START, so a real prompt
        # with a reminder APPENDED is NOT caught.
        tn = [{"type": "text", "text": "<task-notification>\n<task-id>abc</task-id>\n<status>completed</status>"}]
        sr = [{"type": "text", "text": "<system-reminder>do the thing</system-reminder>"}]
        appended = [{"type": "text", "text": "do the thing\n<system-reminder>note</system-reminder>"}]
        self.assertEqual(em.author_of(tn, "sdk", {}, sdk_human=True), "system")
        self.assertEqual(em.author_of(sr, "sdk", {}, sdk_human=True), "system")
        self.assertEqual(em.author_of(appended, "sdk", {}, sdk_human=True), "human")   # appended reminder ≠ system msg
        # author 'system' means _is_opener folds it in (never opens a turn → never a goal)
        self.assertFalse(em._is_opener({"type": "user", "author": "system"}))


if __name__ == "__main__":
    unittest.main()
