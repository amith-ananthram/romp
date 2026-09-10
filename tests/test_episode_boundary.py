"""/clear is an episode boundary, not a deletion (the user 2026-07-26).

A `/clear` mints a new transcript whose head record has NO parent link; the goal layer used to be
blind to it, so a session's open cards outlived the conversation that was their only evidence and
four machines (closer candidates, unblocker, auto-nudge, awaiting backstop) carried them into the
fresh, unrelated conversation. The kernel's episode boundary tick records each observed episode head
in episodes/<sid>.jsonl and settles the open cards that died with their episode: sealed like the
mute path (cleared.jsonl + the durable node flag, romp-authored verdict), no agent notify, completed
cards untouched. The judge's _placed_key fuzzy match is scoped to the current episode so a retyped
identical prompt plans again instead of deduping against its dead twin. Synthetic data only.
"""
import json
import os
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1750000000


def _rec(uuid, parent=None, logical=None, ts="2026-01-01T00:00:00Z", typ="user"):
    r = {"type": typ, "uuid": uuid, "parentUuid": parent, "timestamp": ts}
    if logical is not None:
        r["logicalParentUuid"] = logical
    return r


def _write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


def _node(nid, parent, **kw):
    n = {"id": nid, "text": nid, "parentId": parent, "nodeComplete": False,
         "blocked": False, "cleared": False, "trail": [], "t": 1, "mt": 1, "log": []}
    n.update(kw)
    return n


class EpisodeBoundaryTest(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        self.proj = Path(self._td) / "proj"
        self.proj.mkdir()
        self.g = lambda n: "%s:%s" % (SID, n)

    def tearDown(self):
        jd._rebind_state(self._saved_state)   # the judge module is shared process-wide: never leave it on a removed dir
        shutil.rmtree(self._td, ignore_errors=True)

    def _store(self):
        g = self.g
        nodes = {
            g("g1"): _node(g("g1"), None),                        # open working top -> settled at boundary
            g("g1a"): _node(g("g1a"), g("g1")),                   # sub-goal -> untouched (cleared rolls down in views)
            g("g2"): _node(g("g2"), None, nodeComplete=True),     # completed top -> stays (history needs no evidence)
            g("g3"): _node(g("g3"), None, blocked=True),          # blocked top -> open; dies with its episode too
            g("g4"): _node(g("g4"), None, cleared=True),          # already cleared -> not re-cleared
        }
        store = {"rompUuid": SID, "seq": 5, "nodes": nodes,
                 "status": {self.g("g1"): "working", self.g("g2"): "completed",
                            self.g("g3"): "blocked", self.g("g4"): "cleared"},
                 "placements": {}}
        jd.save_goals(SID, store)

    def _cleared_rows(self):
        p = jd.STATE / "cleared.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    # ── transcript_head ──────────────────────────────────────────────────────────────────────────

    def test_head_null_root(self):
        p = self.proj / "a.jsonl"
        _write_jsonl(p, [{"type": "summary", "summary": "x"},        # no uuid -> skipped
                         _rec("u1", ts="2026-01-01T01:00:00Z")])
        h = jd.transcript_head(p)
        self.assertEqual(h["uuid"], "u1")
        self.assertTrue(h["root"])
        self.assertGreater(h["t"], 0)

    def test_head_resume_fork_is_not_root(self):
        p = self.proj / "b.jsonl"
        _write_jsonl(p, [_rec("u2", parent="u1")])
        self.assertFalse(jd.transcript_head(p)["root"])

    def test_head_compaction_stitch_is_not_root(self):
        p = self.proj / "c.jsonl"
        _write_jsonl(p, [_rec("u3", parent=None, logical="u2", typ="system")])
        self.assertFalse(jd.transcript_head(p)["root"])

    def test_head_missing_or_empty(self):
        self.assertIsNone(jd.transcript_head(self.proj / "nope.jsonl"))
        p = self.proj / "empty.jsonl"
        p.write_text("")
        self.assertIsNone(jd.transcript_head(p))

    # ── the boundary tick ────────────────────────────────────────────────────────────────────────

    def test_first_observation_seeds_without_settling(self):
        self._store()
        p = self.proj / (SID + ".jsonl")
        _write_jsonl(p, [_rec("root1")])
        km._episode_boundary_check(SID, str(p), NOW)
        rows = jd.episode_rows(SID)
        self.assertEqual([r["head"] for r in rows], ["root1"])
        self.assertEqual(self._cleared_rows(), [])                   # deploy never mass-settles existing fleets
        store = jd.load_goals(SID)
        self.assertFalse(store["nodes"][self.g("g1")].get("cleared"))

    def test_clear_boundary_settles_open_tops(self):
        self._store()
        anchor = self.proj / (SID + ".jsonl")
        _write_jsonl(anchor, [_rec("root1")])
        km._episode_boundary_check(SID, str(anchor), NOW)
        # /clear: the session's path re-points at a NEW null-rooted transcript
        fork = self.proj / "aaaaaaaa-0000-0000-0000-000000000001.jsonl"
        _write_jsonl(fork, [_rec("root2", ts="2026-01-02T00:00:00Z")])
        km._episode_boundary_check(SID, str(fork), NOW)

        rows = jd.episode_rows(SID)
        self.assertEqual([r["head"] for r in rows], ["root1", "root2"])
        self.assertEqual(rows[1]["fsid"], "aaaaaaaa-0000-0000-0000-000000000001")
        # the boundary's OWN settle record rides the log as an ANNOTATION row keyed to its head
        # (the user 2026-07-27): the feed's bell notice and the chat boundary card read back what
        # the clear took, so the settle is never invisible. A separate row, never a field on the
        # head row — seed-vs-boundary is only decided after the head row lands (the writer race),
        # so a seed row must never be able to claim a settle.
        self.assertTrue(all("settled" not in r for r in rows), "head rows never carry the settle")
        ann = jd.episode_settles(SID).get("root2")
        self.assertIsNotNone(ann, "the settle annotation is keyed to the boundary head")
        self.assertEqual({d["id"] for d in ann["settled"]}, {self.g("g1"), self.g("g3")})
        self.assertTrue(all(d.get("text") for d in ann["settled"]), "titles ride along for the notices")

        store = jd.load_goals(SID)
        g = self.g
        self.assertTrue(store["nodes"][g("g1")].get("cleared"))      # open working top: settled
        self.assertTrue(store["nodes"][g("g3")].get("cleared"))      # blocked top: settled too
        self.assertFalse(store["nodes"][g("g2")].get("cleared"))     # completed top: stays
        self.assertTrue(store["nodes"][g("g1a")].get("cleared"))     # sub-goal: rollup rolls the clear down
        #                                                              (a cleared card takes its sub-steps with it)
        # the verdict log says WHO and WHY -- romp-authored, honest reason
        ev = [e for e in store["nodes"][g("g1")]["log"] if e.get("kind") == "clear"]
        self.assertEqual(ev[-1]["src"], "romp")
        self.assertIn("conversation was cleared", ev[-1]["why"])
        # cleared.jsonl: one row per settled top, ONE shared batch t (a single Undo restores the batch)
        rows = self._cleared_rows()
        self.assertEqual({r["id"] for r in rows}, {g("g1"), g("g3")})
        self.assertEqual(len({r["t"] for r in rows}), 1)

    def test_boundary_settle_feeds_the_bell_notice(self):
        # build_feed ships the newest settled boundary per living session (clearNotices) so the
        # shell's bell logs the drop exactly once (client seen-set) — a /clear must never take
        # cards off the board with nothing shown anywhere (the user 2026-07-27)
        self._store()
        anchor = self.proj / (SID + ".jsonl")
        _write_jsonl(anchor, [_rec("root1")])
        km._episode_boundary_check(SID, str(anchor), NOW)
        self.assertEqual(km._boundary_clear_notices([{"sid": SID, "name": "web"}]), [],
                         "a seeded-but-never-cleared session ships no notice")
        fork = self.proj / "aaaaaaaa-0000-0000-0000-000000000004.jsonl"
        _write_jsonl(fork, [_rec("root2", ts="2026-01-02T00:00:00Z")])
        km._episode_boundary_check(SID, str(fork), NOW)
        notices = km._boundary_clear_notices([{"sid": SID, "name": "web"}])
        self.assertEqual(len(notices), 1)
        n = notices[0]
        self.assertEqual((n["sid"], n["name"]), (SID, "web"))
        self.assertEqual(set(n["titles"]), {self.g("g1"), self.g("g3")})
        self.assertTrue(n["t"], "the boundary's own t keys the bell's once-only signature")

    def test_resume_fork_is_not_a_boundary(self):
        self._store()
        anchor = self.proj / (SID + ".jsonl")
        _write_jsonl(anchor, [_rec("root1")])
        km._episode_boundary_check(SID, str(anchor), NOW)
        fork = self.proj / "aaaaaaaa-0000-0000-0000-000000000002.jsonl"
        _write_jsonl(fork, [_rec("u5", parent="root1")])              # resume: chains into the prior file
        km._episode_boundary_check(SID, str(fork), NOW)
        self.assertEqual(len(jd.episode_rows(SID)), 1)               # recorded episode unchanged
        self.assertEqual(self._cleared_rows(), [])

    def test_same_head_is_idempotent(self):
        self._store()
        p = self.proj / (SID + ".jsonl")
        _write_jsonl(p, [_rec("root1")])
        km._episode_boundary_check(SID, str(p), NOW)
        km._episode_boundary_check(SID, str(p), NOW)
        self.assertEqual(len(jd.episode_rows(SID)), 1)

    def test_interleaved_seed_race_still_settles(self):
        """Two kernel instances overlapped for ~1s on 2026-07-27 (a restart-churn morning): a
        stale-path writer SEEDED row 1 between the fresh writer's read and its append, so the fresh
        writer — holding a pre-append "no rows yet" — took the seed path and a REAL /clear boundary
        silently skipped its settle. Seed-vs-boundary is now decided by re-reading the log AFTER the
        append: whichever writer finds rows besides its own settles."""
        self._store()
        fork = self.proj / "aaaaaaaa-0000-0000-0000-000000000004.jsonl"
        _write_jsonl(fork, [_rec("root2", ts="2026-01-02T00:00:00Z")])
        orig = jd.append_episode

        def interleaved(sid, head, fsid, t):
            orig(sid, "root1", SID, 1)      # the peer's seed lands FIRST, unseen by our pre-read
            orig(sid, head, fsid, t)
        jd.append_episode = interleaved
        try:
            km._episode_boundary_check(SID, str(fork), NOW)
        finally:
            jd.append_episode = orig
        self.assertEqual([r["head"] for r in jd.episode_rows(SID)], ["root1", "root2"])
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][self.g("g1")].get("cleared"),
                        "the raced boundary still settles its open cards")
        self.assertTrue(self._cleared_rows())

    def test_resighted_historical_head_is_not_a_boundary(self):
        """A stale-path writer re-sighting a HISTORICAL head (the pre-clear transcript, mid path
        transition) must neither re-append it — the log would grow on every flip — nor settle."""
        self._store()
        anchor = self.proj / (SID + ".jsonl")
        _write_jsonl(anchor, [_rec("root1")])
        km._episode_boundary_check(SID, str(anchor), NOW)             # seed root1
        fork = self.proj / "aaaaaaaa-0000-0000-0000-000000000005.jsonl"
        _write_jsonl(fork, [_rec("root2", ts="2026-01-02T00:00:00Z")])
        km._episode_boundary_check(SID, str(fork), NOW)               # real boundary: settles
        n_rows = len(self._cleared_rows())
        km._episode_boundary_check(SID, str(anchor), NOW)             # stale re-sighting of root1
        self.assertEqual([r["head"] for r in jd.episode_rows(SID)], ["root1", "root2"])
        self.assertEqual(len(self._cleared_rows()), n_rows, "a re-sighted head never settles again")

    def test_boundary_with_no_open_tops_records_only(self):
        g = self.g
        store = {"rompUuid": SID, "seq": 1,
                 "nodes": {g("g2"): _node(g("g2"), None, nodeComplete=True)},
                 "status": {g("g2"): "completed"}, "placements": {}}
        jd.save_goals(SID, store)
        anchor = self.proj / (SID + ".jsonl")
        _write_jsonl(anchor, [_rec("root1")])
        km._episode_boundary_check(SID, str(anchor), NOW)
        fork = self.proj / "aaaaaaaa-0000-0000-0000-000000000003.jsonl"
        _write_jsonl(fork, [_rec("root2")])
        km._episode_boundary_check(SID, str(fork), NOW)
        self.assertEqual(len(jd.episode_rows(SID)), 2)
        self.assertEqual(self._cleared_rows(), [])

    # ── _placed_key episode scoping ──────────────────────────────────────────────────────────────

    def test_fuzzy_match_rejects_prior_episode_twin(self):
        # episode 2 starts at t=2000; a placement recorded in episode 1 (t=1000) must not dedup a
        # byte-identical prompt retyped in the fresh conversation (t=3000)
        jd.append_episode(SID, "root1", SID, 1)
        jd.append_episode(SID, "root2", "fork1", 2000)
        placements = {"%s:1000:abcd#p" % SID: self.g("g1")}
        self.assertFalse(jd._placed_key(placements, "%s:3000:abcd#p" % SID, live={"%s:3000:abcd" % SID}))

    def test_fuzzy_match_keeps_same_episode_drift(self):
        jd.append_episode(SID, "root1", SID, 1)
        jd.append_episode(SID, "root2", "fork1", 2000)
        placements = {"%s:2500:abcd#p" % SID: self.g("g1")}           # recorded IN episode 2, t drifted
        self.assertTrue(jd._placed_key(placements, "%s:2600:abcd#p" % SID, live={"%s:2600:abcd" % SID}))

    def test_exact_match_survives_across_episodes(self):
        # pre-clear segment ids can only re-enter the parse as-written; the exact hit is the
        # anti-re-mint guard and must NOT be episode-scoped
        jd.append_episode(SID, "root2", "fork1", 2000)
        key = "%s:1000:abcd#p" % SID
        self.assertTrue(jd._placed_key({key: self.g("g1")}, key))

    def test_no_episode_recorded_keeps_old_behavior(self):
        placements = {"%s:1000:abcd#p" % SID: self.g("g1")}
        self.assertTrue(jd._placed_key(placements, "%s:3000:abcd#p" % SID, live={"%s:3000:abcd" % SID}))

    def test_live_twin_guard_still_wins(self):
        # the recorded key IS another live segment (a twin) -> still no dedup, episode or not
        jd.append_episode(SID, "root1", SID, 1)
        placements = {"%s:2500:abcd" % SID: self.g("g1")}
        self.assertFalse(jd._placed_key(placements, "%s:2600:abcd" % SID,
                                        live={"%s:2500:abcd" % SID, "%s:2600:abcd" % SID}))


if __name__ == "__main__":
    unittest.main()
