#!/usr/bin/env python3
"""parsed_session: ONE event-model parse per (transcript, mtime+size), reused across the captioner /
planner / sweep / courier instead of each re-parsing the same leaf every pass (the redundancy that
forced the PLAN_SESSIONS cap). An unchanged transcript is served from cache; a changed one re-parses."""
import os
import tempfile
import time
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))


def test_parsed_session_caches_until_the_transcript_changes():
    jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
    calls = []
    orig = jd.em.parse_session
    jd.em.parse_session = lambda *a, **k: calls.append(1) or {"turns": []}   # count real parses
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("{}\n")
            p = f.name
        s1 = jd.parsed_session("sid1", [p], 0)
        s2 = jd.parsed_session("sid1", [p], 0)
        assert s1 is s2, "an unchanged transcript returns the SAME cached parse"
        assert len(calls) == 1, "...parsed exactly once"

        time.sleep(0.01)
        with open(p, "a") as fh:
            fh.write("{}\n")                                                  # grow it → new (mtime,size)
        jd.parsed_session("sid1", [p], 0)
        assert len(calls) == 2, "a CHANGED transcript re-parses (cache keyed on mtime+size)"
    finally:
        jd.em.parse_session = orig
        os.unlink(p)


def test_every_parse_cache_clear_in_tests_clears_the_chain_memo():
    """_CHAIN_MEMO keys on the same (mtime, size) identity as _PARSE_CACHE over the same transcripts, so a
    test that clears one for a same-second fixture rewrite must clear the other on the same line, or a memo
    an earlier test populated serves a stale five-way verdict for the new bytes
    (test_judge_rewind_cleanup.py::ChainMemo pins the hazard itself). A source scan by necessity: the
    invariant is about the test corpus, and no behaviour exists to drive."""
    tests = Path(__file__).resolve().parent
    bad = []
    for f in sorted(tests.glob("test_*.py")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if "_PARSE_CACHE.clear()" in line and "_CHAIN_MEMO.clear()" not in line:
                bad.append("%s:%d" % (f.name, n))
    assert bad == [], "parse-cache clears with no chain-memo clear on the same line: " + ", ".join(bad)


if __name__ == "__main__":
    test_parsed_session_caches_until_the_transcript_changes()
    test_every_parse_cache_clear_in_tests_clears_the_chain_memo()
    print("ok")
