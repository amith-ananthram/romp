#!/usr/bin/env python3
"""The glossary (T351 stage 2, the user 2026-09-11): the kernel parses a group's glossary file in the README's grammar,
resolves a session to its group's file, ships a byte-bounded index frame, and answers GET /glossary/<term>. The fixture
is SYNTHETIC (tests/fixtures/glossary_grammar.json: an invented notes-api team's entries); the TS counterpart reads the same
file. Hermetic state; nothing of any real glossary reaches the repo."""
import contextlib
import io
import json
import os
import shutil
import tempfile
from unittest import mock
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
Path(_ROOT, "romp", "session-hosts").write_text("off\n")   # this root replaces the conftest's floored one, so it writes its own hosts off (the review's low)
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_glossary", os.path.join(BIN, "romp-kernel"))

FIX = json.loads(Path(HERE, "fixtures", "glossary_grammar.json").read_text())
SID = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-1111-2222-3333-444444444444"


class Parser(unittest.TestCase):
    def test_the_fixture_parses_to_its_expectation(self):
        got = km._glossary_parse(FIX["text"])
        self.assertEqual(got["skip"], FIX["expect"]["skip"], "the Not-coinages bullets read as words: the bold lead or the text before the colon")
        self.assertEqual([e["slug"] for e in km._slice_headings(FIX["text"])], FIX["expect"]["slugs"], "slugs over ALL headings, the viewer's rule")
        slim = [{k: v for k, v in e.items() if k != "section"} for e in got["terms"]]
        self.assertEqual(slim, FIX["expect"]["terms"])
        self.assertTrue(got["terms"][0]["section"].startswith("## tessel\n"), "each entry keeps its whole section for the route")
        self.assertIn("- registered: 2026-09-11 by web", got["terms"][0]["section"])

    def test_edge_cases(self):
        self.assertEqual(km._glossary_parse(""), {"skip": [], "terms": [], "cutHeadings": 0})
        self.assertEqual(km._glossary_parse("# only a title\n\nprose\n"), {"skip": [], "terms": [], "cutHeadings": 0})
        got = km._glossary_parse("## a\n\ndef\n\n### sub\n\nmore\n\n## b\n\n- status: retired\n")
        self.assertEqual([e["term"] for e in got["terms"]], ["a", "b"], "a level-3 heading stays inside its section")
        self.assertEqual(got["terms"][0]["definition"], "def more", "…and its prose tessels into the definition")
        self.assertEqual(got["terms"][1]["definition"], ""); self.assertEqual(got["terms"][1]["status"], "retired")
        fenced = "## x\n\n```\n## not a heading\n```\n\n- plain words: y\n"
        self.assertEqual([e["term"] for e in km._glossary_parse(fenced)["terms"]], ["x"], "fenced code hides no heading")
        self.assertEqual([e["term"] for e in km._glossary_parse("## \n\nnothing\n\n## real\n\nx\n")["terms"]], ["real"], "a bare '## ' names nothing (the review: its plural was the letter s)")
        many = "".join("## t%d\n\nd%d\n\n" % (i, i) for i in range(300))
        got = km._glossary_parse(many)
        self.assertEqual(len(got["terms"]), km._SLICE_HEADINGS_MAX, "the heading index's ceiling"); self.assertEqual(got["cutHeadings"], 300 - km._SLICE_HEADINGS_MAX, "…and the sections past it are counted, not dropped silently")
        self.assertEqual(km._glossary_parse(FIX["text"])["cutHeadings"], 0)
        two_skips = "## Not coinages\n\n- head\n\n## a\n\nx\n\n## Not coinages\n\n- round\n\n## b\n\ny\n"
        got = km._glossary_parse(two_skips)
        self.assertEqual((got["cutHeadings"], got["skip"], [e["term"] for e in got["terms"]]), (0, ["head", "round"], ["a", "b"]), "a second Not-coinages heading is handled, not counted as a cut section")


class Lookup(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.saved_env = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.td
        self.saved_state = km.jd.STATE
        km.jd._rebind_state(Path(self.td) / "state")
        (Path(self.td) / "state").mkdir(parents=True, exist_ok=True)
        Path(self.td, "state", "session-hosts").write_text("off\n")   # a lab root writes its own hosts off (the conftest rule)
        (Path(self.td) / "glossaries").mkdir(parents=True)
        Path(self.td, "glossaries", "notes-api.md").write_text(FIX["text"])
        km._GLOSSARY_CACHE.clear()
        self.saved_views, self.saved_name = km._timeline_views, km._name_of
        km._timeline_views = lambda: {"tags": [{"id": "t1", "name": "notes-api", "members": [{"host": "", "sid": SID}]}]}
        km._name_of = lambda sid: {SID: "web", OTHER: "docs"}.get(sid, "")

    def tearDown(self):
        km._timeline_views, km._name_of = self.saved_views, self.saved_name
        if self.saved_env is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved_env
        km.jd._rebind_state(self.saved_state)
        km._GLOSSARY_CACHE.clear()
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_session_resolves_to_its_groups_file_else_its_own_names_else_none(self):
        self.assertEqual(km._session_groups(SID), ["notes-api"])
        g, p = km._glossary_source(SID)
        self.assertEqual((g, p.name), ("notes-api", "notes-api.md"))
        self.assertEqual(km._glossary_source(OTHER), (None, None), "no group, no docs.md: nothing")
        Path(self.td, "glossaries", "docs.md").write_text("## glimmerwick\n\nAn invented noun for the docs group's own file.\n\n- plain words: a made-up thing\n")
        self.assertEqual(km._glossary_source(OTHER)[0], "docs", "its own name's file")

    def test_the_frame_is_the_index_bounded_by_bytes_with_the_cut_counted(self):
        fr = km._glossary_frame(SID)
        self.assertEqual((fr["type"], fr["id"], fr["group"]), ("glossary", SID, "notes-api"))
        self.assertTrue(fr["path"].endswith("glossaries/notes-api.md")); self.assertTrue(fr["mtime"].isdigit())
        self.assertEqual(fr["skip"], FIX["expect"]["skip"]); self.assertEqual([e["term"] for e in fr["terms"]], ["tessel", "quill", "spar", "tessel head"])
        self.assertNotIn("section", fr["terms"][0], "the section stays on the kernel's side"); self.assertEqual(fr["truncated"], 0)
        self.assertIsNone(km._glossary_frame(OTHER), "no file: no frame")
        cap = km._GLOSSARY_INDEX_MAX_BYTES
        try:
            km._GLOSSARY_INDEX_MAX_BYTES = 600
            before = dict(km._PERF_STATS.glossary_stats)
            fr = km._glossary_frame(SID)
            self.assertLess(len(fr["terms"]), 4); self.assertEqual(fr["truncated"], 4 - len(fr["terms"]), "the cut is counted on the frame")
            self.assertEqual((fr["cutBytes"], fr["cutHeadings"]), (fr["truncated"], 0), "…as a BYTE cut, apart from a heading cut (the card's note names each)")
            self.assertEqual(km._PERF_STATS.glossary_stats["cut"] - before["cut"], fr["truncated"], "…and in /perf")
        finally:
            km._GLOSSARY_INDEX_MAX_BYTES = cap

    def test_sections_past_the_heading_ceiling_ride_the_frame_as_their_own_cut(self):
        p = Path(self.td, "glossaries", "notes-api.md")
        st = p.stat(); p.write_text(FIX["text"] + "".join("\n## t%d\n\nd%d\n" % (i, i) for i in range(300))); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
        fr = km._glossary_frame(SID)
        self.assertGreater(fr["cutHeadings"], 0); self.assertEqual(fr["truncated"], fr["cutHeadings"] + fr["cutBytes"], "the frame carries the two cuts apart and their sum")

    def test_a_rewrite_is_a_new_cache_key_and_the_old_one_goes(self):
        km._glossary_frame(SID)
        self.assertEqual(len(km._GLOSSARY_CACHE), 1)
        p = Path(self.td, "glossaries", "notes-api.md")
        st = p.stat(); p.write_text(FIX["text"] + "\n## bramblet\n\nAn invented noun, appended after the first frame.\n\n- plain words: a made-up thing\n"); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        fr = km._glossary_frame(SID)
        self.assertIn("bramblet", [e["term"] for e in fr["terms"]]); self.assertEqual(len(km._GLOSSARY_CACHE), 1, "one entry per file")

    def test_the_route_answers_a_term_or_an_alias_and_404s_with_the_paths_tried(self):
        s, b = km._glossary_lookup(SID, "Tessel")
        self.assertEqual((s, b["title"], b["anchor"], b["group"], b["status"]), (200, "tessel", "tessel", "notes-api", "unconfirmed"))
        self.assertTrue(b["markdown"].startswith("## tessel")); self.assertTrue(b["source_path"].endswith("notes-api.md"))
        self.assertEqual(km._glossary_lookup(SID, "review tessel")[1]["title"], "tessel", "an alias answers the term")
        self.assertEqual(km._glossary_lookup(SID, "tessel head")[1]["anchor"], "tessel-head", "the multi-word term is its own entry")
        with mock.patch.dict(os.environ, {"HOME": self.td}):     # the lab root stands in for $HOME, so the tilde has something to abbreviate
            s, b = km._glossary_lookup(SID, "nonesuch")
        self.assertEqual(s, 404); self.assertTrue(b["tried"][0].startswith("~/") and b["tried"][0].endswith("notes-api.md"), "tilded like the 200's source_path: %r" % b["tried"])
        s, b = km._glossary_lookup(OTHER, "tessel")
        self.assertEqual(s, 404); self.assertTrue(any(t.endswith("docs.md") for t in b["tried"]), "the paths tried, for a session with no file")

    def test_the_cache_is_bounded_and_refuses_a_file_over_the_read_ceiling(self):
        cap, ents = km._TEXT_MAX_BYTES, km._GLOSSARY_CACHE_ENTRIES
        try:
            km._GLOSSARY_CACHE_ENTRIES = 2
            for i in range(3):
                Path(self.td, "glossaries", "g%d.md" % i).write_text("## w%d\n\nx\n" % i)
                km._glossary_load(Path(self.td, "glossaries", "g%d.md" % i))
            self.assertEqual(len(km._GLOSSARY_CACHE), 2, "least recently read out first")
            km._TEXT_MAX_BYTES = 64
            big = Path(self.td, "glossaries", "big.md"); big.write_text("## huge\n\n" + "x" * 200 + "\n")
            before = km._PERF_STATS.glossary_stats["refused"]
            err = io.StringIO()
            with contextlib.redirect_stderr(err), mock.patch.dict(os.environ, {"HOME": self.td}):   # the lab root stands in for $HOME: the line is tilded
                self.assertEqual(km._glossary_load(big), (None, 0), "over the preview route's own ceiling: not read, not held")
                self.assertEqual(km._glossary_load(big), (None, 0))
            self.assertEqual(km._PERF_STATS.glossary_stats["refused"] - before, 2, "every refusal counts in /perf (the review's low: it was counted nowhere)")
            self.assertEqual(err.getvalue().count("refused:"), 1, "…and is logged once per file version: %r" % err.getvalue())
            self.assertIn("glossary refused: ~/glossaries/big.md is 210 bytes, over the 64-byte read ceiling", err.getvalue(), "tilded, sized, and naming the ceiling")
        finally:
            km._TEXT_MAX_BYTES, km._GLOSSARY_CACHE_ENTRIES = cap, ents

    def test_the_perf_snapshot_carries_the_counters(self):
        snap = km._PERF_STATS.snapshot() if hasattr(km._PERF_STATS, "snapshot") else None
        if snap is not None:
            self.assertIn("glossary", snap)
        src = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertIn('"glossary": glossary_stats,', src)
        self.assertEqual(set(km._PERF_STATS.glossary_stats), {"parses", "framesBuilt", "termsBuilt", "bytesBuilt", "cut", "refused"}, "counted at the build, and named so; the read-ceiling refusals too")


if __name__ == "__main__":
    unittest.main()
