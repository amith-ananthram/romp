#!/usr/bin/env python3
"""The bare slash-invocation TWIN drops for BOTH wrapper shapes (2026-08-13). CLI 2.1.215+ writes a
typed command TWICE — a raw-text user record and the wrapper record, sharing a promptId — and the
wrapper's ORDER is not fixed: a built-in writes <command-name> first (shape A), a skill / custom
command writes <command-message> first (shape B). The emit path has matched both since 2026-07-22
(COMMAND_NAME_ANY_RE inside a wrapper record), but the twin-drop pre-pass stayed anchored-only, so a
shape-B invocation kept its raw twin: a phantom human segment beside the real command atom — same
text hash, different t — which read as a plannable human ask. Fixed under PLACEMENTS_V=8 (a SMALLER
atom set for transcripts carrying shape-B commands — v5's shape in reverse, same seal).
SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_em_twinshapes", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def urec(t, text, uuid, parent, prompt_id=None):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "user", "content": text}, "sessionId": SID}
    if prompt_id:
        r["promptId"] = prompt_id
    return r


class TwinDropsForBothWrapperShapes(unittest.TestCase):
    def _parse(self, recs, path_dir):
        p = os.path.join(path_dir, SID + ".jsonl")
        with open(p, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in recs) + "\n")
        return em.parse_session(p, rompuuid=SID)

    def test_shape_a_name_first_twin_drops(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [urec(1000, "/autocompact auto", "tw1", None, "p1"),
                    urec(1001, "<command-name>/autocompact</command-name>\n"
                               "<command-args>auto</command-args>", "cw1", "tw1", "p1")]
            sess = self._parse(recs, td)
            atoms = [a for t in sess["turns"] for a in t["atoms"] if a["type"] == "user"]
            self.assertEqual(len(atoms), 1, "one atom per invocation — the raw twin is the echo")
            self.assertEqual(atoms[0].get("command"), "/autocompact")

    def test_shape_b_message_first_twin_drops_too(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [urec(1000, "/jld help me write the abstract", "tw1", None, "p1"),
                    urec(1001, "<command-message>jld</command-message>\n"
                               "<command-name>/jld</command-name>\n"
                               "<command-args>help me write the abstract</command-args>", "cw1", "tw1", "p1")]
            sess = self._parse(recs, td)
            atoms = [a for t in sess["turns"] for a in t["atoms"] if a["type"] == "user"]
            self.assertEqual(len(atoms), 1,
                             "the message-first wrapper is the same invocation — its twin is the same echo "
                             "(the pre-pass used to match name-first only, leaving a phantom human segment)")
            self.assertEqual(atoms[0].get("command"), "/jld")

    def test_prose_quoting_a_wrapper_is_never_an_invocation(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [urec(1000, "why does <command-name>/x</command-name> appear in my transcript?", "u1", None)]
            sess = self._parse(recs, td)
            atoms = [a for t in sess["turns"] for a in t["atoms"] if a["type"] == "user"]
            self.assertEqual(len(atoms), 1)
            self.assertIsNone(atoms[0].get("command"),
                              "ANY-match applies only inside a record that BEGINS with a command wrapper")


class LiveStreamMirrorsTheAnyMatch(unittest.TestCase):
    def test_sdk_backend_builder_uses_the_any_match_inside_wrappers(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        self.assertIn("_COMMAND_NAME_ANY_RE = re.compile", src)
        self.assertIn("_COMMAND_NAME_RE.match(text) or (_COMMAND_NAME_ANY_RE.search(text)", src,
                      "the live twin of the file adapter's matcher — a skill-shaped invocation must not "
                      "stream as wrapper noise")


if __name__ == "__main__":
    unittest.main()
