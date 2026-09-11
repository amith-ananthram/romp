#!/usr/bin/env python3
"""T323 stage 2 (the user 2026-09-10; built 2026-09-11): ONE parse for the kernel and the judges. The judges' cache is
the shared store: the kernel's _parse delegates to jd.parsed_session, so a transcript is parsed once per file version
and its tree is held once, whichever side asked first. The store keys on every fact either side keyed on (the fileset
stat pair over the candidates and the states file, the pending cut, sdk_human), keeps a slot per pending cut so two
callers reading different cuts never share one wrong tree, evicts least recently used instead of clearing wholesale,
and answers the kernel's path-keyed readers through a view. Hermetic: synthetic transcripts under a temp root."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_t323s2", os.path.join(BIN, "romp-kernel"))

A = "11111111-2222-4333-8444-000000000201"
B = "22222222-2222-4333-8444-000000000202"


def _transcript(d, sid, n=2):
    p = Path(d) / (sid + ".jsonl")
    recs, parent = [], None
    for i in range(n):
        u, a = "u%d" % i, "a%d" % i
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": "2026-09-10T00:%02d:00Z" % i,
                     "promptSource": "typed", "message": {"role": "user", "content": "wire the fixtures %d" % i}})
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": "2026-09-10T00:%02d:30Z" % i,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "done %d" % i}], "stop_reason": "end_turn"}})
        parent = a
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return str(p)


class OneParseForBoth(unittest.TestCase):
    def setUp(self):
        jd.parse_cache_clear()
        km._PERF_STATS.reset()
        self.d = tempfile.mkdtemp()
        self.now = int(time.time())

    def _misses(self):
        return jd.parse_misses()

    def test_kernel_then_judges_share_one_cold_parse_and_one_object(self):
        p = _transcript(self.d, A)
        m0 = self._misses()
        s1 = km._parse(p, A, self.now)
        s2 = jd.parsed_session(A, [p], self.now)
        self.assertIs(s1, s2, "the judges receive the very tree the kernel rendered")
        self.assertEqual(self._misses() - m0, 1, "one cold parse for the pair")
        snap = km._PERF_STATS.snapshot()["parses"]
        self.assertEqual(snap["kernel"], 1, "the kernel's ask was the cold one")
        self.assertGreaterEqual(snap["sharedHits"], 1, "the judges' ask was served from the store")

    def test_judges_then_kernel_share_too(self):
        p = _transcript(self.d, B)
        m0 = self._misses()
        s1 = jd.parsed_session(B, [p], self.now)
        s2 = km._parse(p, B, self.now)
        self.assertIs(s1, s2)
        self.assertEqual(self._misses() - m0, 1)
        snap = km._PERF_STATS.snapshot()["parses"]
        self.assertEqual((snap["kernel"], snap["hits"]), (0, 1), "the kernel's ask was a hit on the judges' parse")

    def test_an_appended_record_is_one_new_cold_parse_for_both(self):
        p = _transcript(self.d, A)
        km._parse(p, A, self.now); jd.parsed_session(A, [p], self.now)
        m0 = self._misses()
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u9", "parentUuid": "a1", "timestamp": "2026-09-10T01:00:00Z",
                                "promptSource": "typed", "message": {"role": "user", "content": "one more"}}) + "\n")
        s1 = jd.parsed_session(A, [p], self.now); s2 = km._parse(p, A, self.now)
        self.assertIs(s1, s2)
        self.assertEqual(self._misses() - m0, 1, "the grown file costs one parse, shared")

    def test_different_pending_cuts_get_separate_slots(self):
        """The manager's rule: when the kernel and the judges would read different cuts, each gets its own entry
        rather than one reading the other's view."""
        p = _transcript(self.d, A, n=3)
        saved = jd._PENDING_CUT_FN
        try:
            jd.set_pending_cut_provider(lambda fsid: "")
            plain = jd.parsed_session(A, [p], self.now)
            jd.set_pending_cut_provider(lambda fsid: "a1")          # a bare rollback armed: the world cut at a1
            cut = jd.parsed_session(A, [p], self.now)
            self.assertIsNot(plain, cut)
            self.assertIn((A, ""), jd._PARSE_CACHE); self.assertIn((A, "a1"), jd._PARSE_CACHE)   # a slot per cut, a tree per flag inside
            m0 = self._misses()
            jd.set_pending_cut_provider(lambda fsid: "")
            self.assertIs(jd.parsed_session(A, [p], self.now), plain, "the un-cut slot is still there, not evicted by the cut one")
            self.assertEqual(self._misses() - m0, 0)
            self.assertIs(jd._PARSE_CACHE[A][1], plain, "a bare id reads the newest slot")
        finally:
            jd.set_pending_cut_provider(saved)

    def test_sdk_human_comes_from_the_owner_hook_and_is_part_of_the_match(self):
        p = _transcript(self.d, B)
        saved = jd._SDK_OWNER_FN
        try:
            jd.set_sdk_owner_provider(lambda fsid: False)
            s_no = jd.parsed_session(B, [p], self.now)
            self.assertFalse(jd._PARSE_CACHE[B][3])
            jd.set_sdk_owner_provider(lambda fsid: True)
            m0 = self._misses()
            s_yes = jd.parsed_session(B, [p], self.now)
            self.assertEqual(self._misses() - m0, 1, "a flipped owner answer is a different parse, never a stale hit")
            self.assertIsNot(s_no, s_yes)
            self.assertTrue(jd._sdk_owned(B))
            jd.set_sdk_owner_provider(lambda fsid: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertFalse(jd._sdk_owned(B), "a failing hook falls back to the registry file (absent here)")
        finally:
            jd._SDK_OWNER_FN = saved
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("jd.set_sdk_owner_provider(", src, "the kernel installs the backends' owns() as the one answer")

    def test_the_kernel_view_reads_the_store(self):
        p = _transcript(self.d, A)
        self.assertNotIn(p, km._parse_cache)
        self.assertIsNone(km._parse_cached(p), "cache-only read: nothing yet, and no parse ran")
        s = km._parse(p, A, self.now)
        self.assertIn(p, km._parse_cache)
        self.assertIs(km._parse_cache.get(p)[1], s)
        self.assertIs(km._parse_cache[p][1], s)
        self.assertIs(km._parse_cached(p), s, "the live-key read answers from the store")
        self.assertIn(p, list(km._parse_cache))
        self.assertEqual(len(km._parse_cache), 1)
        km._parse_cache.pop(p, None)
        self.assertNotIn(p, km._parse_cache, "a dead lane's drop empties every cut's slot")
        km._parse(p, A, self.now)
        km._parse_cache.clear()
        self.assertEqual(len(jd._PARSE_CACHE), 0)

    def test_least_recently_used_eviction_never_clears_wholesale(self):
        saved = jd._PARSE_CACHE_MAX
        jd._PARSE_CACHE_MAX = 3
        try:
            paths = [_transcript(self.d, "3333333%d-2222-4333-8444-00000000030%d" % (i, i)) for i in range(4)]
            sids = [os.path.splitext(os.path.basename(p))[0] for p in paths]
            for p, sid in zip(paths[:3], sids[:3]):
                jd.parsed_session(sid, [p], self.now)
            jd.parsed_session(sids[0], [paths[0]], self.now)        # touch the oldest: it becomes newest
            jd.parsed_session(sids[3], [paths[3]], self.now)        # a fourth: evicts ONE, the least recently used (sids[1])
            self.assertEqual(len(jd._PARSE_CACHE), 3)
            self.assertIn(sids[0], jd._PARSE_CACHE); self.assertNotIn(sids[1], jd._PARSE_CACHE)
            self.assertIn(sids[2], jd._PARSE_CACHE); self.assertIn(sids[3], jd._PARSE_CACHE)
        finally:
            jd._PARSE_CACHE_MAX = saved

    def test_parse_cached_never_parses_and_matches_the_live_key(self):
        p = _transcript(self.d, B)
        m0 = self._misses()
        self.assertIsNone(jd.parse_cached(B, [p]))
        self.assertEqual(self._misses() - m0, 0)
        s = jd.parsed_session(B, [p], self.now)
        self.assertIs(jd.parse_cached(B, [p]), s)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u7", "parentUuid": "a1", "timestamp": "2026-09-10T02:00:00Z",
                                "promptSource": "typed", "message": {"role": "user", "content": "grown"}}) + "\n")
        self.assertIsNone(jd.parse_cached(B, [p]), "a moved file is not the cached version")
        self.assertEqual(self._misses() - m0, 1, "and the cache-only read still parsed nothing")


if __name__ == "__main__":
    unittest.main()
