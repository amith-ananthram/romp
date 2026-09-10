#!/usr/bin/env python3
"""The to-do mirror joins the ask-unit principle (the user 2026-08-28, T137): a step declared
while the session serves a linked dispatch is that dispatch's fan-out, not a standalone ask.
The sync stamps a kernel-resolved `serving` ref ({peer, msgId, goalId} — a DISTINCT field from
origin/links, which carry run_propagate's complete-the-tracker semantics) latched at mint on a
named event: the newest delegate-kind peer segment at or before the declaring segment, in
transcript order, within the episode. The dispatch's frame and root-ask thread into the node so
the prose writers anchor; a dispatch-less declaration threads the session's own prompt record.
The FEED folds a serving-marked top into the sender's ask card at render (a read-only cross-store
join — the node stays in the worker's store, where plan-sync completion, nudge freshness, and
clears all live), with the needs-you breakthrough: a BLOCKED serving mirror never folds silently.
SYNTHETIC fixtures only; private synthetic sids."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
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
km = load_source("romp_kernel_servmir", os.path.join(BIN, "romp-kernel"))
jd = km.jd
em = jd.em

NOW = 1_788_000_000
T0 = NOW - 3600
SND = "d88c0001-1111-4222-8333-000000000001"    # private synthetic sids — never the shared placeholder
WKR = "d88c0001-1111-4222-8333-000000000002"
MID = "1787999000.000001_1.TESTHOST"
MID2 = "1787999100.000001_2.TESTHOST"
ASK = "the metric panels should pick sensible ranges on their own"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def dmail(t, uuid, mid, kind="delegate", parent=None):
    body = ("do the panels piece\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: %s -->" % (mid, kind))
    return uline(t, body, uuid, parent=parent, ps="sdk")


ROWS = [{"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": SND,
         "to_id": WKR, "kind": "delegate", "body": "pick per-metric ranges for the panels"},
        {"t": T0 + 100, "ev": "sent", "id": MID2, "from": "web", "from_id": SND,
         "to_id": WKR, "kind": "delegate", "body": "then wire the picker into settings"}]


class ServingResolver(unittest.TestCase):
    """_serving_dispatch: transcript-positional, delegate-kind only, honest about a gone segment."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._msgs = (jd.MESSAGES, em.MESSAGES_LOG)
        p = Path(self.td.name) / "messages.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
        jd.MESSAGES = em.MESSAGES_LOG = p

    def tearDown(self):
        jd.MESSAGES, em.MESSAGES_LOG = self._msgs
        self.td.cleanup()

    def _sess(self, recs):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (WKR + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            return em.parse_session(str(p), rompuuid=WKR, candidate_files=[str(p)], now=NOW)

    def _segids(self, s):
        st = {"rompUuid": WKR, "nodes": {}, "placements": {}, "status": {}}
        return [(sg["id"], sg) for turn in s["turns"] for sg in jd._segs(turn, st)], st

    def test_the_newest_delegate_at_or_before_the_declaration_wins(self):
        s = self._sess([dmail(T0, "m1", MID), aline(T0 + 30, "on it", "a1", "m1"),
                        dmail(T0 + 100, "m2", MID2, parent="a1"),
                        aline(T0 + 130, "and this", "a2", "m2"),
                        uline(T0 + 200, "carry on", "u3", "a2"), aline(T0 + 230, "ok", "a3", "u3")])
        segs, st = self._segids(s)
        self.assertEqual(jd._serving_dispatch(s, st, WKR, segs[-1][0]),
                         {"peer": SND, "msgId": MID2}, "declared after the second dispatch → it")
        self.assertEqual(jd._serving_dispatch(s, st, WKR, segs[0][0])["msgId"], MID,
                         "declared in the first dispatch's own segment → the first (at-or-before)")

    def test_no_delegate_or_coordinate_only_resolves_nothing(self):
        s = self._sess([uline(T0, "work on my own thing", "u1"), aline(T0 + 30, "ok", "a1", "u1")])
        segs, st = self._segids(s)
        self.assertIsNone(jd._serving_dispatch(s, st, WKR, segs[-1][0]))
        s2 = self._sess([dmail(T0, "m1", MID, kind="coordinate"), aline(T0 + 30, "noted", "a1", "m1")])
        segs2, st2 = self._segids(s2)
        self.assertIsNone(jd._serving_dispatch(s2, st2, WKR, segs2[-1][0]),
                          "a coordinate is not a dispatch")

    def test_a_gone_declaring_segment_attributes_nothing(self):
        s = self._sess([dmail(T0, "m1", MID), aline(T0 + 30, "on it", "a1", "m1")])
        _, st = self._segids(s)
        self.assertIsNone(jd._serving_dispatch(s, st, WKR, "seg-that-no-longer-exists"),
                          "no confident placement → no attribution, never newest-overall")


def _mail_atom_peer(recs):
    return recs


class SyncStamps(unittest.TestCase):
    """The mint stamps serving + frame + userAsk from the lazy ctx, latched; the one-shot
    back-fill resolves existing open mirrors as-of their own declaring segment, once."""

    def setUp(self):
        self._plan = em.task_store_plan
        self._msgs = (jd.MESSAGES, em.MESSAGES_LOG)
        self.td = tempfile.TemporaryDirectory()
        p = Path(self.td.name) / "messages.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
        jd.MESSAGES = em.MESSAGES_LOG = p

    def tearDown(self):
        em.task_store_plan = self._plan
        jd.MESSAGES, em.MESSAGES_LOG = self._msgs
        self.td.cleanup()
        for d in (jd.GOALDIR, jd.GOALARCHDIR):
            try:
                (d / (WKR + ".json")).unlink()
            except OSError:
                pass
        try:
            (jd._overrides_dir() / (WKR + ".jsonl")).unlink()
        except OSError:
            pass

    def _store(self):
        return {"rompUuid": WKR, "seq": 0, "nodes": {}, "placements": {}, "status": {}}

    def test_a_serving_mint_carries_ref_frame_and_root_ask(self):
        em.task_store_plan = lambda fsid: [{"key": "1", "text": "probe the axes",
                                            "activeForm": "", "status": "pending"}]
        st = self._store()
        ref = {"peer": SND, "msgId": MID, "goalId": SND + ":g2"}
        ua = {"text": ASK, "sid": SND}
        self.assertTrue(jd._sync_declared_plan(st, {"turns": [], "leafFsid": WKR}, "s1", T0 + 50,
                                               prompt_uuid="pu1", ctx=lambda: (ref, ua)))
        nd = next(iter(st["nodes"].values()))
        self.assertEqual(nd["serving"], ref)
        self.assertEqual(nd["frame"], "pick per-metric ranges for the panels",
                         "the dispatch's ledger head frames the step")
        self.assertEqual(nd["userAsk"]["text"], ASK, "the dispatch chain's root anchors the prose")
        self.assertEqual(nd["servingT"], T0 + 50)

    def test_the_stamp_is_latched_never_rederived(self):
        em.task_store_plan = lambda fsid: [{"key": "1", "text": "probe the axes",
                                            "activeForm": "", "status": "pending"}]
        st = self._store()
        jd._sync_declared_plan(st, {"turns": [], "leafFsid": WKR}, "s1", T0 + 50,
                               ctx=lambda: ({"peer": SND, "msgId": MID, "goalId": SND + ":g2"}, None))
        jd._sync_declared_plan(st, {"turns": [], "leafFsid": WKR}, "s2", T0 + 900,
                               ctx=lambda: ({"peer": SND, "msgId": MID2, "goalId": SND + ":g9"}, None))
        nd = next(iter(st["nodes"].values()))
        self.assertEqual(nd["serving"]["msgId"], MID,
                         "a later dispatch never re-attributes an existing step (no flap)")

    def test_a_dispatchless_mint_anchors_on_the_sessions_own_record(self):
        em.task_store_plan = lambda fsid: [{"key": "1", "text": "tidy my own backlog",
                                            "activeForm": "", "status": "pending"}]
        st = self._store()
        self.assertTrue(jd._sync_declared_plan(st, {"turns": [], "leafFsid": WKR}, "s1", T0 + 50,
                                               ctx=lambda: (None, {"text": "sort out my backlog",
                                                                   "sid": WKR})))
        nd = next(iter(st["nodes"].values()))
        self.assertNotIn("serving", nd)
        self.assertEqual(nd["userAsk"]["text"], "sort out my backlog")

    def test_the_one_shot_backfill_resolves_as_of_the_declaring_segment_once(self):
        em.task_store_plan = lambda fsid: [{"key": "7", "text": "old declared step",
                                            "activeForm": "", "status": "pending"}]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (WKR + ".jsonl")
            recs = [dmail(T0, "m1", MID), aline(T0 + 30, "on it", "a1", "m1"),
                    uline(T0 + 60, "declare it", "u2", "a1"), aline(T0 + 90, "declared", "a2", "u2")]
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            session = em.parse_session(str(p), rompuuid=WKR, candidate_files=[str(p)], now=NOW)
            st = {"rompUuid": WKR, "nodes": {}, "placements": {}, "status": {}}
            segs = [sg["id"] for turn in session["turns"] for sg in jd._segs(turn, st)]
            st["nodes"][WKR + ":g5"] = jd.GuardedNode(
                {"id": WKR + ":g5", "text": "old declared step", "parentId": None,
                 "nodeComplete": False, "blocked": False, "cleared": False,
                 "trail": [segs[-1]], "t": T0 + 60, "mt": T0 + 60,
                 "why": "declared in the agent's own to-do list",
                 "agentTask": {"key": "7", "status": "open", "raw": "pending"},
                 "agentBornOpen": True, "log": []})
            st["seq"] = 5
            jd._sync_declared_plan(st, session, segs[-1], NOW, ctx=lambda: (None, None))
            nd = st["nodes"][WKR + ":g5"]
            self.assertEqual(nd["serving"]["msgId"], MID, "back-filled as-of its own segment")
            self.assertEqual(nd["servingT"], NOW, "latched — never re-attempted")

    def test_frame_enrichment_treats_serving_as_origin(self):
        st = self._store()
        st["nodes"][WKR + ":g5"] = {"id": WKR + ":g5", "text": "t", "parentId": None,
                                    "serving": {"peer": SND, "msgId": MID, "goalId": None},
                                    "frame": "pick per-metric ranges for the panels"}
        self.assertIn("pick per-metric ranges", jd._deleg_frame(st, WKR + ":g5"),
                      "a serving mirror's dispatch frames its prose (T137)")


class FoldMatrix(unittest.TestCase):
    """The view-side fold: a serving-marked mirror top joins the sender's ask card at render —
    read-only, cross-store, never orphaning — and needs-you BREAKS THROUGH (a blocked serving
    mirror stands on the board like any needs-you card)."""

    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        import re as _re
        pdir = proj / _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        for sid, nm in ((SND, "web"), (WKR, "api")):
            recs = [uline(T0, "kick off the panels round", "hu-" + nm, ps="typed"),
                    aline(T0 + 20, "On it.", "a-" + nm, "hu-" + nm)]
            (pdir / (sid + ".jsonl")).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"
        names.mkdir()
        (names / SND).write_text("web\t%s\t#abcdef\n" % str(cdir))
        (names / WKR).write_text("api\t%s\t#ffaa00\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm)
        jd.gist_llm = lambda p: ""
        km._autonudge_cache.clear()
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        km._tmux_sessions = lambda: {
            SND: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                  "context": None, "compactPct": None, "color": None},
            WKR: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                  "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm) = self.saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _sender_store(self):
        ask, trk, sub = SND + ":g1", SND + ":g2", SND + ":g3"
        (jd.GOALDIR / (SND + ".json")).write_text(json.dumps({
            "rompUuid": SND, "seq": 3, "lastNode": ask,
            "nodes": {ask: {"id": ask, "text": "Ship the panels round", "parentId": None,
                            "nodeComplete": False, "blocked": False, "cleared": False,
                            "trail": [], "t": NOW - 300, "mt": NOW - 300, "log": []},
                      trk: {"id": trk, "text": "\u21aa delegated to api: pick the ranges",
                            "parentId": ask, "nodeComplete": False, "blocked": False,
                            "cleared": False, "trail": [], "t": NOW - 250, "mt": NOW - 250,
                            "handoff": {"peer": WKR, "msgId": MID}, "log": []},
                      sub: {"id": sub, "text": "the round's own step", "parentId": ask,
                            "nodeComplete": False, "blocked": False, "cleared": False,
                            "trail": [], "t": NOW - 240, "mt": NOW - 240, "log": []}},
            "placements": {}, "status": {ask: "working"}}))
        return ask, trk

    def _worker_store(self, serving=True, blocked=False, goal_id=None):
        g = WKR + ":g5"
        nd = {"id": g, "text": "probe the axes", "parentId": None,
              "nodeComplete": False, "blocked": blocked, "cleared": False,
              "trail": [], "t": NOW - 200, "mt": NOW - 200,
              "why": "declared in the agent's own to-do list",
              "agentTask": {"key": "1", "status": "open", "raw": "pending"},
              "agentBornOpen": True, "log": []}
        if blocked:
            nd["blockWhy"] = "pick a range basis"
        if serving:
            nd["serving"] = {"peer": SND, "msgId": MID,
                             "goalId": goal_id if goal_id is not None else SND + ":g2"}
        (jd.GOALDIR / (WKR + ".json")).write_text(json.dumps({
            "rompUuid": WKR, "seq": 5, "lastNode": g, "nodes": {g: nd},
            "placements": {}, "status": {g: "blocked" if blocked else "working"}}))
        return g

    def test_a_working_serving_mirror_folds_into_the_ask_card(self):
        ask, trk = self._sender_store()
        g = self._worker_store()
        asks = km.build_feed(NOW)["asks"]
        self.assertNotIn(g, {a.get("itemId") for a in asks},
                         "no standalone worker card — fan-out lives inside the ask card")
        card = next(a for a in asks if a.get("itemId") == ask)
        rows = {r["id"]: r for r in card["tree"]}
        self.assertIn(g, rows, "the step rides the ask card's tree")
        self.assertIn(g, rows[trk].get("children") or [],
                      "…as a child of its dispatch's tracker row")
        self.assertEqual(rows[g]["who"], "api", "the row wears the WORKER's identity")
        self.assertEqual(rows[g]["auth"], "open", "authority disc intact across the join")

    def test_a_blocked_serving_mirror_breaks_through(self):
        self._sender_store()
        g = self._worker_store(blocked=True)
        asks = km.build_feed(NOW)["asks"]
        card = next((a for a in asks if a.get("itemId") == g), None)
        self.assertIsNotNone(card, "needs-you NEVER folds silently — the card stands")
        self.assertEqual(card["column"], "needs_input")

    def test_a_missing_tracker_keeps_the_standalone_card(self):
        self._sender_store()
        g = self._worker_store(goal_id=SND + ":gone")
        asks = km.build_feed(NOW)["asks"]
        self.assertIn(g, {a.get("itemId") for a in asks},
                      "no rendered home → the card stays; suppression never hides live work")

    def test_a_legacy_mirror_without_serving_is_untouched(self):
        self._sender_store()
        g = self._worker_store(serving=False)
        asks = km.build_feed(NOW)["asks"]
        self.assertIn(g, {a.get("itemId") for a in asks})


if __name__ == "__main__":
    unittest.main()
