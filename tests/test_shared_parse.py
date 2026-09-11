#!/usr/bin/env python3
"""T323 stage 2 (the user 2026-09-10; built 2026-09-11): ONE parse for the kernel and the judges. The judges' cache is
the shared store: the kernel's _parse delegates to jd.parsed_session, so a transcript is parsed once per file version
and its tree is held once, whichever side asked first. The store keys on every fact either side keyed on (the fileset
stat pair over the candidates and the states file, the pending cut, sdk_human), keeps a slot per pending cut and per
leaf transcript so a spent cut's tree goes and an override render of another file under the same sid never evicts
the live leaf's tree, evicts least recently used instead of clearing wholesale,
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
        km._display_sdk_human(A)                # builds the backend once: its setter installs the owner hook and
        #                                          clears the store, which must never happen mid-test (review find D)
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

    def test_a_leaf_named_after_the_cli_session_is_found_by_path(self):
        """Review find (A): after a /clear or a resume fork the leaf is <lastSid>.jsonl while the romp sid is another
        id; the cache-only read and the view's drop go by leaf path and the entry's own sid, never a filename stem."""
        other = "55555555-6666-4777-8888-000000000777"
        p = _transcript(self.d, other, n=3)                  # a leaf named after the CLI session id
        states = Path(jd.STATE) / "states" / (A + ".jsonl")  # the romp sid's states log: part of the key, so a read that
        states.parent.mkdir(parents=True, exist_ok=True)     #  derived the states path from the leaf's stem would miss (review find)
        states.write_text(json.dumps({"t": self.now, "state": "idle"}) + "\n")
        self.addCleanup(lambda: states.unlink(missing_ok=True))
        km._parse(p, A, self.now)                            # parsed for romp sid A
        st = states.stat()
        stored = jd._PARSE_CACHE[A][0]                       # the STORED key: (fileset pair, cut)
        self.assertIn([st.st_mtime, st.st_size], stored[0], "the stored key carries states/<A>.jsonl's stat")
        stem_states = Path(jd.STATE) / "states" / (other + ".jsonl")   # a states path derived from the leaf's stem
        self.assertFalse(stem_states.exists())
        self.assertNotEqual(jd._fileset_key(jd._parse_key_files(A, [p], str(stem_states))[2]), stored[0],
                            "a key built over the stem-derived states path would not match the stored one")
        self.assertIsNotNone(km._parse_cached(p), "the cache-only read finds the display's tree by leaf path")
        self.assertIs(km._parse_cached(p), jd._PARSE_CACHE[A][1])
        self.assertIn(p, km._parse_cache)
        km._parse_cache.pop(p, None)
        self.assertNotIn(p, km._parse_cache, "pop by path drops the tree parsed under the romp sid")
        self.assertIsNone(km._parse_cached(p))
        self.assertNotIn(A, jd._PARSE_CACHE)

    def test_a_store_hit_reports_the_assembly_mode_so_the_chat_fold_can_fold(self):
        """Review find (B): the judges parse an appended transcript first; the kernel's build hits the store and must
        learn the mode of the parse that built the tree (fold or serve), else the fold gate rebuilds the whole chat."""
        p = _transcript(self.d, B, n=2)
        km._parse(p, B, self.now)
        self.assertEqual(km._parse_mode[p], "full", "the first parse is a full assembly")
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u9", "parentUuid": "a1", "timestamp": "2026-09-10T00:20:00.000Z",
                                "message": {"role": "user", "content": "one more"}}) + "\n")
        jd.parsed_session(B, [p], self.now + 1)              # a judge sees the append first (no asm_mode_out of its own)
        m0 = self._misses()
        km._parse(p, B, self.now + 1)                        # the kernel's build: a hit
        self.assertEqual(self._misses() - m0, 0)
        self.assertIn(km._parse_mode[p], ("fold", "serve"), "the hit carries the judges' assembly mode, never a blank read as full: %r" % km._parse_mode[p])
        ent = jd._PARSE_CACHE[B]
        self.assertEqual((ent[4], ent[5]), (B, km._parse_mode[p]), "the entry names its sid and the mode that built it")

    def test_two_answers_in_one_slot_in_a_hookless_process(self):
        """Review find (F), the case the per-flag trees exist for: with no owner hook, the display passes True (its
        backend owns the session) while the judges compute False (no registry file); each keeps its own tree in the
        slot and neither reads the other's."""
        p = _transcript(self.d, A, n=2)
        saved = jd._SDK_OWNER_FN
        jd._SDK_OWNER_FN = None
        try:
            jd.parse_cache_clear()
            judge_tree = jd.parsed_session(A, [p], self.now)                      # the judges: registry absent → False
            disp_tree = jd.parsed_session(A, [p], self.now, sdk_human=True)      # the display: its backend owns → True
            self.assertIsNot(judge_tree, disp_tree)
            self.assertEqual(sorted(jd._PARSE_CACHE[(A, "", p)].keys()), [False, True], "one tree per answer in the slot")
            m0 = self._misses()
            self.assertIs(jd.parsed_session(A, [p], self.now), judge_tree, "the judges hit their own tree")
            self.assertIs(jd.parsed_session(A, [p], self.now, sdk_human=True), disp_tree, "the display hits its own")
            self.assertEqual(self._misses() - m0, 0, "no stale hit either way, no re-parse either")
        finally:
            jd._SDK_OWNER_FN = saved

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
            self.assertIn((A, "a1", p), jd._PARSE_CACHE)
            self.assertNotIn((A, "", p), jd._PARSE_CACHE, "a slot stored under a new cut drops the fsid's other cut slots: one tree per session (review find)")
            jd.set_pending_cut_provider(lambda fsid: "")        # the cut is spent (the next record landed)
            m0 = self._misses()
            again = jd.parsed_session(A, [p], self.now)
            self.assertEqual(self._misses() - m0, 1, "the spent cut's tree is gone, the plain world is parsed once more")
            self.assertNotIn((A, "a1", p), jd._PARSE_CACHE, "and the spent cut's slot went with it")
            self.assertIs(jd._PARSE_CACHE[A][1], again, "a bare id reads the newest slot")
            self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == A]), 1)
        finally:
            jd.set_pending_cut_provider(saved)

    def test_an_override_render_keeps_its_own_slot_beside_the_live_leaf(self):
        """Review find (2026-09-11): build_session(path_override=agent file) parses ANOTHER transcript under the parent
        romp sid (a subagent viewer open on a running agent, an episode render). With a slot per (sid, cut) the two keys
        never matched, so each miss stored over the other's tree: two builds per cycle for one session, the chat fold
        seeing a new object every build, the feed's cache-only read flipping with build order. The leaf is in the slot:
        both hit, one tree each, no eviction, in either order."""
        p = _transcript(self.d, A, n=3)                                  # the live leaf
        os.makedirs(os.path.join(self.d, "subagents"), exist_ok=True)
        sub = _transcript(os.path.join(self.d, "subagents"), "agent-aaaa", n=2)   # an agent's own transcript
        cut = jd._pending_cut(A)
        for order in ((sub, p), (p, sub)):
            jd.parse_cache_clear()
            m0 = self._misses()
            first = km._parse(order[0], A, self.now)
            second = km._parse(order[1], A, self.now)
            self.assertEqual(self._misses() - m0, 2, "two transcripts, two cold parses")
            self.assertIs(km._parse(order[0], A, self.now), first, "the first still hits after the second parsed")
            self.assertIs(km._parse(order[1], A, self.now), second)
            self.assertEqual(self._misses() - m0, 2, "and neither evicted the other")
            self.assertIn((A, cut, p), jd._PARSE_CACHE)
            self.assertIn((A, cut, sub), jd._PARSE_CACHE)
            leaf_tree = first if order[0] == p else second
            self.assertIs(km._parse_cached(p), leaf_tree, "the feed's cache-only read sees the live leaf's tree while the agent's is held")
            self.assertIs(jd.parsed_session(A, [p], self.now), leaf_tree, "a judge pass reads the live leaf's tree")
            self.assertEqual(self._misses() - m0, 2)
        self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == A]), 2, "one slot per transcript under the sid")
        self.assertEqual(km._parse_cache.pop(sub)[1], second if order[1] == sub else first)
        self.assertIn(p, km._parse_cache, "dropping the agent's slot leaves the leaf's")

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

    def test_a_clear_releases_the_previous_leafs_tree(self):
        """Review find (2026-09-11): with the leaf in the slot, a /clear (the SDK registry's lastSid flips to a new
        fsid, discover hands out <lastSid>.jsonl) left the pre-clear leaf's full tree resident until restart: no cut
        changed, the LRU is 256 deep, nothing popped it. discover drops the previous leaf's slots when it first hands
        out the new one, so exactly one slot remains per session; twice, for two clears."""
        import re
        C = "33333333-2222-4333-8444-000000000303"
        L1, L2 = "44444444-2222-4333-8444-000000000441", "44444444-2222-4333-8444-000000000442"
        saved = (jd.STATE, jd.NAMES, jd.PROJECTS, os.environ.get("CLAUDE_CONFIG_DIR"))
        td = Path(tempfile.mkdtemp())
        try:
            jd._rebind_state(td / "state")
            cfg = td / "claude"; os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
            cdir = td / "launchdir"; cdir.mkdir()
            proj_dir = cfg / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            proj_dir.mkdir(parents=True)
            names = td / "names"; names.mkdir()
            (names / C).write_text("worker\t%s\t#abcdef\n" % cdir)
            jd.NAMES, jd.PROJECTS = names, cfg / "projects"
            jd.SDKDIR.mkdir(parents=True, exist_ok=True)
            reg = jd.SDKDIR / (C + ".json")
            anchor_path = _transcript(str(proj_dir), C, n=2)
            reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir)}))
            now = time.time()

            def current_leaf():
                rows = [r for r in jd.discover(now) if r[0] == C]
                self.assertEqual(len(rows), 1, rows)
                return str(rows[0][1])

            def slots():
                return [k for k in jd._PARSE_CACHE if k[0] == C]

            self.assertEqual(current_leaf(), anchor_path)
            t0 = km._parse(anchor_path, C, now)
            jd.parsed_session(C, [anchor_path], now)
            self.assertEqual(len(slots()), 1)
            for i, L in enumerate((L1, L2), start=1):
                leaf = _transcript(str(proj_dir), L, n=1)         # /clear: a fresh-headed transcript under a new fsid
                reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir), "lastSid": L}))
                os.utime(reg, (now + i, now + i))                  # the registry read memoizes on mtime: move it
                #                                                    explicitly, never by a sleep (coarse timestamps)
                self.assertEqual(current_leaf(), leaf, "clear %d: discover hands out the new leaf" % i)
                t = km._parse(leaf, C, now)
                self.assertIsNot(t, t0)
                jd.parsed_session(C, [leaf], now)                  # the judges read the same slot
                self.assertEqual(slots(), [(C, jd._pending_cut(C), leaf)],
                                 "clear %d: exactly one slot remains for the session, the new leaf's" % i)
                self.assertIsNone(km._parse_cached(anchor_path if i == 1 else prev_leaf), "the previous leaf's tree is gone")
                prev_leaf, t0 = leaf, t
            # a pass that snapshotted its rows before the clear parses the retired leaf AFTER the flip: it gets its
            # tree and the store keeps nothing, since the release was a one-shot and nothing would drop it again
            m0 = self._misses()
            late = jd.parsed_session(C, [anchor_path], now)
            self.assertEqual(self._misses() - m0, 1)
            self.assertTrue(late["turns"], "the late caller still gets a parse")
            self.assertEqual(slots(), [(C, jd._pending_cut(C), prev_leaf)], "a store under a retired leaf is refused")
            self.assertIsNone(km._parse_cached(anchor_path))
            self.assertTrue(jd._leaf_retired(C, anchor_path)); self.assertFalse(jd._leaf_retired(C, prev_leaf))
        finally:
            jd.NAMES, jd.PROJECTS = saved[1], saved[2]
            if saved[3] is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved[3]
            jd._rebind_state(saved[0])

    def test_the_clear_race_window_hands_out_the_previous_leaf_not_the_anchor(self):
        """Carried from the stage 2 review (2026-09-11): between the registry's lastSid rewrite and the new transcript's
        first record, discover handed out the ANCHOR, so from the second clear on a build landing in the window parsed
        the pre-clear anchor cold (its tree released at the first clear) and the noted leaf flipped twice. discover
        hands out the leaf it handed out last until the named file exists."""
        import re
        C = "33333333-2222-4333-8444-000000000304"
        L1, L2 = "44444444-2222-4333-8444-000000000451", "44444444-2222-4333-8444-000000000452"
        saved = (jd.STATE, jd.NAMES, jd.PROJECTS, os.environ.get("CLAUDE_CONFIG_DIR"))
        td = Path(tempfile.mkdtemp())
        try:
            jd._rebind_state(td / "state")
            cfg = td / "claude"; os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
            cdir = td / "launchdir"; cdir.mkdir()
            proj_dir = cfg / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            proj_dir.mkdir(parents=True)
            names = td / "names"; names.mkdir()
            (names / C).write_text("worker\t%s\t#abcdef\n" % cdir)
            jd.NAMES, jd.PROJECTS = names, cfg / "projects"
            jd.SDKDIR.mkdir(parents=True, exist_ok=True)
            reg = jd.SDKDIR / (C + ".json")
            anchor_path = _transcript(str(proj_dir), C, n=2)
            leaf1 = _transcript(str(proj_dir), L1, n=1)
            reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir), "lastSid": L1}))
            now = time.time()

            def current_leaf():
                rows = [r for r in jd.discover(now) if r[0] == C]
                self.assertEqual(len(rows), 1, rows)
                return str(rows[0][1])

            self.assertEqual(current_leaf(), leaf1)                       # after the first clear
            km._parse(leaf1, C, now)
            reg.write_text(json.dumps({"sid": C, "name": "worker", "cwd": str(cdir), "lastSid": L2}))
            os.utime(reg, (now + 1, now + 1))                             # the second clear's registry write lands first
            m0 = self._misses()
            self.assertEqual(current_leaf(), leaf1, "the window: lastSid names a file not on disk, the previous leaf stands")
            self.assertEqual(self._misses() - m0, 0)
            self.assertFalse(jd._leaf_retired(C, leaf1), "no flip was noted in the window")
            self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == C], [(C, jd._pending_cut(C), leaf1)], "the anchor was never parsed")
            leaf2 = _transcript(str(proj_dir), L2, n=1)                  # the CLI writes the new transcript's first record
            self.assertEqual(current_leaf(), leaf2, "the file exists: discover hands out the new leaf")
            self.assertTrue(jd._leaf_retired(C, leaf1)); self.assertFalse(jd._leaf_retired(C, anchor_path))
        finally:
            jd.NAMES, jd.PROJECTS = saved[1], saved[2]
            if saved[3] is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved[3]
            jd._rebind_state(saved[0])

    def test_a_fork_childs_flip_leaves_the_parents_slot(self):
        """Review find (2026-09-11): a fork child's SDK registry is born with lastSid = the PARENT's fsid until its
        own init flips it, so discover hands the child the parent's transcript first; the child's flip to its own
        file must drop the child's slots of that leaf only, never the parent's live tree."""
        P, CH = A, "66666666-2222-4333-8444-000000000666"
        parent_leaf = _transcript(self.d, P, n=2)
        child_leaf = _transcript(self.d, CH, n=1)
        jd._note_leaf(P, parent_leaf)
        jd._note_leaf(CH, parent_leaf)                     # the child's registry still names the parent's file
        parent_tree = km._parse(parent_leaf, P, self.now)
        jd.parsed_session(CH, [parent_leaf], self.now)     # a pass parsed the child's row over the parent's file
        m0 = self._misses()
        jd._note_leaf(CH, child_leaf)                      # the child's init flipped its lastSid
        self.assertIs(km._parse(parent_leaf, P, self.now), parent_tree, "the parent's live slot stands")
        self.assertEqual(self._misses() - m0, 0, "the parent is not re-parsed cold by the child's flip")
        self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == CH], [], "the child's slot of the parent's file went")
        self.assertTrue(jd._leaf_retired(CH, parent_leaf)); self.assertFalse(jd._leaf_retired(P, parent_leaf))
        jd.parsed_session(CH, [child_leaf], self.now)
        self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == CH]), 1, "the child's own leaf stores")

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


class DeadCodexSessionKeepsItsAuthor(unittest.TestCase):
    """2026-09-11: the display's sdk_human (_display_sdk_human) and the judges' (the owner hook the kernel installs)
    answered for Codex with CodexBackend.owns, which is live-only, so a Codex session's typed prompts (promptSource
    "sdk", the Codex convention) re-authored from human to programmatic the moment its registry row was marked dead
    (an end, a kernel restart over a dead row) — the same transcript, nothing new learned — while a dead SDK session's
    kept theirs (SdkBackend.owns is the reg file's existence). Both sides answer by record presence now. A REAL
    CodexBackend on a private state root, its registry holding a dead row and a live one the way a restart loads
    them, stands in for the kernel's singleton; nothing else is stubbed."""
    DEAD = "77777777-2222-4333-8444-000000000771"
    LIVE = "77777777-2222-4333-8444-000000000772"
    NONE = "77777777-2222-4333-8444-000000000773"

    def setUp(self):
        km._display_sdk_human(A)               # builds the SDK backend once: its setter installs the owner hook
        self.assertIsNotNone(jd._SDK_OWNER_FN, "the kernel installed its owner hook")
        cx0 = km._codex()
        self.assertIsNotNone(cx0, "the kernel's Codex backend module loads in this process")
        root = Path(tempfile.mkdtemp())
        (root / "codex").mkdir()
        (root / "codex" / "registry.json").write_text(json.dumps({
            self.DEAD: {"tid": self.DEAD, "name": "web", "cwd": "/TESTDIR", "dead": True},
            self.LIVE: {"tid": self.LIVE, "name": "api", "cwd": "/TESTDIR", "dead": False}}))
        self.cx = type(cx0)(root, log=lambda m: None)   # the real backend class, loading the rows as a restart does
        saved = km._codex
        km._codex = lambda: self.cx
        self.addCleanup(setattr, km, "_codex", saved)
        jd.parse_cache_clear()
        self.d = tempfile.mkdtemp()
        self.now = int(time.time())

    def _codex_transcript(self, sid):
        p = Path(self.d) / (sid + ".jsonl")
        recs = [{"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": "2026-09-10T00:00:00Z",
                 "promptSource": "sdk", "message": {"role": "user", "content": [{"type": "text", "text": "wire the fixtures"}]}},
                {"type": "assistant", "uuid": "a0", "parentUuid": "u0", "timestamp": "2026-09-10T00:00:30Z",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}]
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return str(p)

    @staticmethod
    def _prompt_authors(tree):
        return [a["author"] for t in tree["turns"] for a in t["atoms"] if a.get("type") == "user"]

    def test_a_dead_codex_row_answers_human_on_both_sides(self):
        self.assertFalse(self.cx.owns(self.DEAD), "owns() stays live-only: send routing relies on it")
        self.assertTrue(km._display_sdk_human(self.DEAD), "the display: a dead row is still the session's record")
        self.assertTrue(jd._sdk_owned(self.DEAD), "the judges, through the kernel's hook: the same answer")
        p = self._codex_transcript(self.DEAD)
        disp = km._parse(p, self.DEAD, self.now)
        self.assertEqual(self._prompt_authors(disp), ["human"], "the typed prompt keeps its human bubble after the death")
        judges = jd.parsed_session(self.DEAD, [p], self.now)
        self.assertIs(judges, disp, "one slot: the judges read the display's tree, never a second flag's")
        self.assertTrue(jd._PARSE_CACHE[self.DEAD][3])

    def test_a_live_row_and_no_row_answer_as_before(self):
        self.assertTrue(self.cx.owns(self.LIVE))
        self.assertTrue(km._display_sdk_human(self.LIVE)); self.assertTrue(jd._sdk_owned(self.LIVE))
        self.assertFalse(km._display_sdk_human(self.NONE), "no record on any backend: not a backend's session")
        self.assertFalse(jd._sdk_owned(self.NONE))
        p = self._codex_transcript(self.NONE)
        self.assertEqual(self._prompt_authors(km._parse(p, self.NONE, self.now)), ["sdk"],
                         "an unowned transcript's promptSource sdk stays programmatic")


if __name__ == "__main__":
    unittest.main()
