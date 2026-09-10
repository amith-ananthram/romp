#!/usr/bin/env python3
"""Chain-rooted minting (the user 2026-08-25 ~19:4x, replacing the same-day view-side split; the
verdict, paraphrased: team-internal cards must not be CREATED in the first place — a means of
seeing them is not the ask). A delegate whose SENDER's linked goal traces to a HUMAN prompt mints
the recipient top card exactly as before — it is the ask flowing down. An untraceable delegate
mints NO standalone recipient top: the courier files the segment fyi (the coordinate treatment),
the SENDER-side tracking node still plants (the delegation stays one glance away on the sender's
board), and the tracker completes on the recipient's REPLY — the report-back event, the exact rule
the cross-host arm has always used, since no recipient goal will ever carry the msgId. At MINT
time uncertainty files QUIET — the inverse of the retired split's display default — and the
needs-you backstop is what makes that safe: the hard-block floor and the placeholder synthesize a
board card from the live prompt with ZERO goal nodes (tests/test_kernel_blocked_no_goal.py is the
standing behavioral pin), so quietly-filed work that needs the human still interrupts.
SYNTHETIC fixtures only; private synthetic sids."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from inspect import getsource, signature
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_chainmint", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_chainmint", os.path.join(BIN, "romp-kernel"))

NOW = 1_787_100_000
T0 = NOW - 3600
MGR = "c18a0001-1111-4222-8333-000000000001"    # private synthetic sids — never the shared placeholder
WKR = "c18a0001-1111-4222-8333-000000000002"
GRAND = "c18a0001-1111-4222-8333-000000000003"
MID = "1787099000.000001_1.TESTHOST"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _node(nid, text, parent, t=T0, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


def _fake_session(atoms):
    return {"turns": [{"atoms": atoms}]}


class RecordRule(unittest.TestCase):
    """_session_user_prompt_record: only a human prompt (or the queued dictation an attachment
    wraps) counts — returned as the RECORD itself, text + sid (T105) — and everything else — mail,
    romp lines, interrupts, missing records — is None. Truthiness pins here; the record's content
    is tests/test_root_ask_anchor.py's."""

    def _probe(self, atom, uuid="u1"):
        saved = jd.parsed_session
        jd.parsed_session = lambda sid, files, now: _fake_session([atom] if atom else [])
        try:
            return jd._session_user_prompt_record(MGR, "/dev/null", uuid, NOW)
        finally:
            jd.parsed_session = saved

    def test_human_prompt_is_true(self):
        self.assertTrue(self._probe({"uuid": "u1", "type": "user", "author": "human",
                                     "message": {"role": "user", "content": "ship the demo"}}))

    def test_interrupt_artifact_is_false(self):
        self.assertFalse(self._probe({"uuid": "u1", "type": "user", "author": "human",
                                      "message": {"role": "user",
                                                  "content": "[Request interrupted by user]"}}))

    def test_mail_and_romp_authors_are_false(self):
        self.assertFalse(self._probe({"uuid": "u1", "type": "user",
                                      "author": {"peer": WKR, "mid": MID, "kind": "coordinate"},
                                      "message": {"role": "user", "content": "fyi"}}))
        self.assertFalse(self._probe({"uuid": "u1", "type": "user", "author": "romp",
                                      "message": {"role": "user", "content": "[romp] restarted"}}))

    def test_attachment_dictation_is_true_and_marked_attachments_false(self):
        self.assertTrue(self._probe({"uuid": "u1", "type": "attachment",
                                     "message": {"content": [{"type": "text",
                                                              "text": "queued human dictation"}]}}))
        self.assertFalse(self._probe({"uuid": "u1", "type": "attachment",
                                      "message": {"content": [{"type": "text",
                                                  "text": "x <!-- romp-msg-id: %s -->" % MID}]}}))

    def test_a_missing_record_is_false(self):
        self.assertFalse(self._probe({"uuid": "other", "type": "user", "author": "human",
                                      "message": {"role": "user", "content": "hi"}}, uuid="u1"))


class TraceRule(unittest.TestCase):
    """_delegate_user_rooted over synthetic worlds: truthy (the ROOT record, T105) ONLY on a
    chain that reaches a human prompt; every dead-end/machine/mail/cross-host/cycle shape is
    None — uncertainty QUIETS."""

    def setUp(self):
        self._saved = jd.parsed_session
        self.by_sid = {}
        jd.parsed_session = lambda sid, files, now: _fake_session(self.by_sid.get(sid, []))
        self.paths = {MGR: "/dev/null", WKR: "/dev/null", GRAND: "/dev/null"}

    def tearDown(self):
        jd.parsed_session = self._saved
        for sid in (MGR, WKR, GRAND):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass

    def _store(self, sid, nodes, archive=False):
        st = {"rompUuid": sid, "nodes": nodes, "placements": {}, "status": {}}
        (jd.save_goal_archive if archive else jd.save_goals)(sid, st)

    def _human(self, sid, uuid="hu"):
        self.by_sid[sid] = [{"uuid": uuid, "type": "user", "author": "human",
                             "message": {"role": "user", "content": "the user's ask"}}]

    def test_a_human_rooted_link_mints(self):
        self._store(MGR, {"g1": _node("g1", "Ship the demo", None, promptUuid="hu")})
        self._human(MGR)
        self.assertTrue(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW))

    def test_nearest_evidence_climbs_ancestors(self):
        self._store(MGR, {"top": _node("top", "Ship the demo", None, promptUuid="hu"),
                          "kid": _node("kid", "a step", "top")})
        self._human(MGR)
        self.assertTrue(jd._delegate_user_rooted(MGR, "kid", self.paths, NOW))

    def test_a_machine_rooted_link_quiets(self):
        self._store(MGR, {"g1": _node("g1", "internal errand", None, promptUuid="n1")})
        self.by_sid[MGR] = [{"uuid": "n1", "type": "user", "author": "romp",
                             "message": {"role": "user", "content": "[romp] restarted"}}]
        self.assertFalse(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW))

    def test_no_link_quiets(self):
        self.assertFalse(jd._delegate_user_rooted(MGR, None, self.paths, NOW))

    def test_a_missing_node_or_record_quiets(self):
        self._store(MGR, {"g1": _node("g1", "evidence-free", None)})
        self.assertFalse(jd._delegate_user_rooted(MGR, "gone", self.paths, NOW))
        self.assertFalse(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW),
                         "no promptUuid anywhere on the chain → quiet, never a guess")

    def test_an_origin_hop_reaches_the_grand_senders_human_root(self):
        self._store(MGR, {"g1": _node("g1", "mid-chain ask", None,
                                      origin={"peer": GRAND, "goalId": "t1", "msgId": "m0"})})
        self._store(GRAND, {"g9": _node("g9", "the original ask", None, promptUuid="hu"),
                            "t1": _node("t1", "↪ delegated", "g9")}, archive=True)
        self._human(GRAND)
        self.assertTrue(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW),
                        "archive-held grand-sender chains still resolve")

    def test_a_cross_host_origin_quiets(self):
        self._store(MGR, {"g1": _node("g1", "mid-chain ask", None,
                                      origin={"peer": GRAND, "goalId": "t1", "msgId": "m0",
                                              "peerHost": "TESTHOST"})})
        self._store(GRAND, {"g9": _node("g9", "unreachable", None, promptUuid="hu"),
                            "t1": _node("t1", "↪ delegated", "g9")})
        self._human(GRAND)
        self.assertFalse(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW),
                         "another kernel's chain is not ours to read — quiet")

    def test_cycles_terminate_quiet(self):
        self._store(MGR, {"a": _node("a", "x", "b"), "b": _node("b", "y", "a")})
        self.assertFalse(jd._delegate_user_rooted(MGR, "a", self.paths, NOW))

    def test_the_record_reports_whether_the_asks_card_is_live(self):
        # the T101 mint fallback's discriminator: proof found at a LIVE ask node means the ask
        # still has a card the tracker can link under; the record says so (`carded`), and the
        # courier links instead of minting.
        self._store(MGR, {"g1": _node("g1", "Ship the demo", None, promptUuid="hu")})
        self._human(MGR)
        self.assertTrue(jd._delegate_user_rooted(MGR, "g1", self.paths, NOW).get("carded"))

    def test_archive_only_evidence_reads_uncarded(self):
        # …while proof recovered from ARCHIVED history alone means the ask's own card is gone —
        # the shape where T101's "the recipient card IS the ask's card" fallback mints.
        self._store(MGR, {"g2": _node("g2", "follow-up work", "g1")})
        self._store(MGR, {"g1": _node("g1", "the original ask", None, promptUuid="hu")},
                    archive=True)
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "g2", self.paths, NOW)
        self.assertTrue(rec, "the chain still proves the human root")
        self.assertFalse(rec.get("carded"), "…but no live ask node carries it")

    def test_a_cleared_live_ask_reads_uncarded(self):
        # `carded` answers "does this node currently RENDER on a visible card", not "is it in the
        # live store" — a user-cleared ask sits live until the compactor takes it, but its card is
        # off every column, so linking into it hides the fan-out completely. Uncarded → the
        # fallback mints.
        self._store(MGR, {"g1": _node("g1", "Ship the demo", None, promptUuid="hu", cleared=True)})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self.assertTrue(rec, "the human proof stands — clearing a card does not erase the chain")
        self.assertFalse(rec.get("carded"), "…but a cleared card renders nowhere")

    def test_a_sealed_ask_reads_uncarded(self):
        # the sealed flavor: a complete/cleared ANCESTOR folds the subtree into done-display —
        # nothing planted under the ask would render as live fan-out (the sealed-open leak).
        self._store(MGR, {"top": _node("top", "done umbrella", None, nodeComplete=True),
                          "g1": _node("g1", "the ask", "top", promptUuid="hu")})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self.assertTrue(rec)
        self.assertFalse(rec.get("carded"))

    def test_a_dangling_ask_reads_uncarded(self):
        # a rewind-swept parent leaves the ask node live but reachable from NO live top — the feed
        # walks top subtrees, so the node renders nowhere.
        self._store(MGR, {"g1": _node("g1", "the ask", "swept-away", promptUuid="hu")})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self.assertTrue(rec)
        self.assertFalse(rec.get("carded"), "a dangling node has no card to link into")

    def test_carded_never_flips_across_the_compaction_boundary(self):
        # THE BOUNDARY PIN: a cleared ask answers the same before and after the compactor moves it
        # to the archive — both uncarded, both mint. With bare live-membership the same cleared
        # card read carded=True live and carded=False archived, so the mint decision flipped on a
        # compaction pass that carried no new information.
        self._store(MGR, {"g1": _node("g1", "the ask", None, promptUuid="hu", cleared=True)})
        self._human(MGR)
        live_rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self._store(MGR, {})
        self._store(MGR, {"g1": _node("g1", "the ask", None, promptUuid="hu", cleared=True)},
                    archive=True)
        arch_rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self.assertTrue(live_rec and arch_rec)
        self.assertEqual(bool(live_rec.get("carded")), bool(arch_rec.get("carded")),
                         "compaction is bookkeeping, never a mint-decision event")
        self.assertFalse(arch_rec.get("carded"))

    def test_a_visible_intermediate_userask_top_reads_carded(self):
        # HOP STAND-DOWN, trace side: the climb passes a LIVE, VISIBLE userAsk-bearing top — the
        # ask already has a card on the intermediate's board, so the record reads carded even when
        # the ROOT ask node is archive-only (which would otherwise ride the origin hop up as
        # carded=False and re-mint at every hop level).
        self._store(MGR, {"gM": _node("gM", "mid-chain ask", None,
                                      origin={"peer": GRAND, "goalId": "t1", "msgId": "m0"},
                                      userAsk={"text": "the user's ask", "sid": GRAND})})
        self._store(GRAND, {"g9": _node("g9", "the original ask", None, promptUuid="hu"),
                            "t1": _node("t1", "↪ delegated", "g9")}, archive=True)
        self._human(GRAND)
        rec = jd._delegate_user_rooted(MGR, "gM", self.paths, NOW)
        self.assertTrue(rec)
        self.assertTrue(rec.get("carded"),
                        "the intermediate top IS the ask's card — no re-mint below it")
        self.assertEqual(rec.get("sid"), GRAND, "…and it still speaks for the root ask")

    def test_a_cleared_intermediate_userask_top_does_not_stand_down(self):
        # …but a cleared intermediate renders nowhere, so it cannot claim the card: the climb
        # falls through to the origin hop and the root's own (archive-only → uncarded) answer.
        self._store(MGR, {"gM": _node("gM", "mid-chain ask", None, cleared=True,
                                      origin={"peer": GRAND, "goalId": "t1", "msgId": "m0"},
                                      userAsk={"text": "the user's ask", "sid": GRAND})})
        self._store(GRAND, {"g9": _node("g9", "the original ask", None, promptUuid="hu"),
                            "t1": _node("t1", "↪ delegated", "g9")}, archive=True)
        self._human(GRAND)
        rec = jd._delegate_user_rooted(MGR, "gM", self.paths, NOW)
        self.assertTrue(rec, "the chain still proves the root")
        self.assertFalse(rec.get("carded"), "a cleared intermediate is no card — the fallback mints")

    def test_inner_uncarded_fallback_yields_to_outer_carded_top(self):
        # THE MAIN-LOOP RECURSION RULE: sender M holds a VISIBLE carded ask top `p` with a child
        # `x` whose origin hops to a local grand-sender G whose ask node `ga` is CLEARED — an
        # uncarded stored-proof (T126's demoted fallback). An origin-hop callsite that did
        # `rec = _delegate_user_rooted(...); if rec: return rec` took that INNER frame's
        # carded:False fallback as a final answer and stopped the walk at G/ga — instead of
        # climbing the one hop to `p`, the ask's own live card. A carded:False answer here would
        # mint a standalone recipient top for an ask that STILL renders on a visible card (the
        # pre-T101 duplicate-card hole). The climb must reach `p`.
        self._store(MGR, {
            "p": _node("p", "Ship the demo", None,
                       userAsk={"text": "the user's ask", "sid": MGR},
                       askRef={"peer": MGR, "goalId": "p"}),
            "x": _node("x", "a fanned step", "p",
                       origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"})})
        self._store(GRAND, {"ga": _node("ga", "the original ask", None, cleared=True,
                                        userAsk={"text": "the user's ask", "sid": GRAND},
                                        askRef={"peer": GRAND, "goalId": "ga"})})
        rec = jd._delegate_user_rooted(MGR, "x", self.paths, NOW)
        self.assertTrue(rec.get("carded"),
                        "the outer climb reaches p, the ask's live card — no standalone re-mint below it")
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "p"},
                         "…and the dedupe key is the carded top, not the inner cleared proof")
        self.assertEqual(rec.get("sid"), MGR)

    def test_container_rescue_inner_fallback_yields_to_sibling_evidence(self):
        # THE CONTAINER-RESCUE TWIN of that rule: the main climb dead-ends at an evidence-free
        # umbrella, so the sibling rescue runs. childA's origin hop resolves only an uncarded
        # stored-proof (the demoted fallback); childB carries a live human prompt record. A rescue
        # callsite with the same `if rec: return rec` returned childA's inner fallback and never
        # reached childB. The rescue must pass over the demoted fallback and take childB's
        # decisive record.
        self._store(MGR, {
            "U": _node("U", "the dictated round", None, umbrella=True),
            "childA": _node("childA", "a fanned step", "U",
                            origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"}),
            "childB": _node("childB", "the original ask", "U", promptUuid="hu")})
        self._store(GRAND, {"ga": _node("ga", "an aside", None, cleared=True,
                                        userAsk={"text": "an aside", "sid": GRAND},
                                        askRef={"peer": GRAND, "goalId": "ga"})})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "U", self.paths, NOW)
        self.assertTrue(rec, "the sibling human record proves the round")
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "childB"},
                         "the rescue takes the sibling's decisive record, not childA's demoted proof")
        self.assertTrue(rec.get("carded"), "childB renders under the live umbrella")

    def test_askref_consistent_across_a_cross_host_fan(self):
        # THE DEDUPE-KEY SKEW (_walk_root_record, kernel/kernel.py): one ask fanned to two workers
        # plants two sibling children under the same carded top `p`, each hopping to a DIFFERENT
        # local grand-sender's uncarded proof. The record's askRef is the ask's stable identity —
        # apply_courier dedupes minted tops on it — so both walks must yield the SAME key (p's).
        # An inline short-circuit stops each walk at its own grand-sender's node, so the two
        # dispatches carry DIFFERENT askRefs and the dedupe mints a twin card where both parents
        # reused one.
        self._store(MGR, {
            "p": _node("p", "Ship the demo", None,
                       userAsk={"text": "the user's ask", "sid": MGR},
                       askRef={"peer": MGR, "goalId": "p"}),
            "x1": _node("x1", "fan to worker one", "p",
                        origin={"peer": GRAND, "goalId": "ga", "msgId": "m1"}),
            "x2": _node("x2", "fan to worker two", "p",
                        origin={"peer": WKR, "goalId": "gb", "msgId": "m2"})})
        self._store(GRAND, {"ga": _node("ga", "the original ask", None, cleared=True,
                                        userAsk={"text": "the user's ask", "sid": GRAND},
                                        askRef={"peer": GRAND, "goalId": "ga"})})
        self._store(WKR, {"gb": _node("gb", "the original ask", None, cleared=True,
                                      userAsk={"text": "the user's ask", "sid": WKR},
                                      askRef={"peer": WKR, "goalId": "gb"})})
        r1 = jd._delegate_user_rooted(MGR, "x1", self.paths, NOW)
        r2 = jd._delegate_user_rooted(MGR, "x2", self.paths, NOW)
        self.assertEqual(r1.get("askRef"), r2.get("askRef"),
                         "one ask fanned twice must carry ONE dedupe key, not skew with the hop level")
        self.assertEqual(r1.get("askRef"), {"peer": MGR, "goalId": "p"},
                         "…the carded ask top, the identity apply_courier dedupes on")

    def test_inner_uncarded_prompt_record_yields_to_outer_carded_top(self):
        # THE PROMPT-RECORD TWIN of the main-loop recursion rule: demoting only the STORED-PROOF
        # arm through the shared fallback slot leaves the HUMAN PROMPT RECORD arm returning an
        # uncarded record DECISIVELY from the inner frame. This is the TRUE-ORIGIN shape (the
        # origin kernel's own ask node carries promptUuid; stored userAsk exists only on
        # courier-planted mid-chain nodes), so it is the MORE common flavor of the same hole:
        # sender M holds a VISIBLE carded ask top `p` with a fanned child `x` whose origin hops to
        # local grand-sender G, whose CLEARED ask node `ga` resolves a live human record. A
        # carded:False answer here re-mints below `p`, the ask's own live card.
        self._store(MGR, {
            "p": _node("p", "Ship the demo", None,
                       userAsk={"text": "the user's ask", "sid": MGR},
                       askRef={"peer": MGR, "goalId": "p"}),
            "x": _node("x", "a fanned step", "p",
                       origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"})})
        self._store(GRAND, {"ga": _node("ga", "the original ask", None, cleared=True,
                                        promptUuid="hu")})
        self._human(GRAND)
        rec = jd._delegate_user_rooted(MGR, "x", self.paths, NOW)
        self.assertTrue(rec.get("carded"),
                        "the outer climb reaches p, the ask's live card — no standalone re-mint below it")
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "p"},
                         "…and the dedupe key is the carded top, not the inner cleared origin node")
        self.assertEqual(rec.get("sid"), MGR)

    def test_container_rescue_inner_prompt_record_yields_to_sibling_evidence(self):
        # THE CONTAINER-RESCUE TWIN of that arm: the main climb dead-ends at an evidence-free
        # umbrella, childA's origin hop resolves only an UNCARDED human record (the grand-sender's
        # cleared origin node), childB renders live under the umbrella with its own record. The
        # rescue's `if rec: return rec` took childA's inner uncarded record and never reached
        # childB; it must ride the fallback slot instead and yield to childB's carded evidence.
        self._store(MGR, {
            "U": _node("U", "the dictated round", None, umbrella=True),
            "childA": _node("childA", "a fanned step", "U",
                            origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"}),
            "childB": _node("childB", "the original ask", "U", promptUuid="hu")})
        self._store(GRAND, {"ga": _node("ga", "an aside", None, cleared=True, promptUuid="hu")})
        self._human(GRAND)
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "U", self.paths, NOW)
        self.assertTrue(rec, "the sibling human record proves the round")
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "childB"},
                         "the rescue takes the sibling's carded record, not childA's uncarded inner one")
        self.assertTrue(rec.get("carded"), "childB renders under the live umbrella")

    def test_an_exhausted_origin_hop_climb_still_mints_from_the_prompt_record(self):
        # T126 PRESERVED (the guard the demotion must not break): when NOTHING anywhere is carded,
        # the inner uncarded human record is still the climb's answer — promoted from the fallback
        # slot at exhaustion, carded:False, exactly where the mint fallback fires. Pins that
        # demoting the arm never lost the exhausted-climb mint.
        self._store(MGR, {"x": _node("x", "a fanned step", None,
                                     origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"})})
        self._store(GRAND, {"ga": _node("ga", "the original ask", None, cleared=True,
                                        promptUuid="hu")})
        self._human(GRAND)
        rec = jd._delegate_user_rooted(MGR, "x", self.paths, NOW)
        self.assertTrue(rec, "the human proof stands — the exhausted climb still mints")
        self.assertFalse(rec.get("carded"))
        self.assertEqual(rec.get("askRef"), {"peer": GRAND, "goalId": "ga"},
                         "the record keeps the true origin's identity")

    def test_a_prompt_record_outranks_a_stored_proof_when_both_ride_the_fallback(self):
        # THE PRECEDENCE PIN: with BOTH demoted arms riding the shared fallback slot, bare
        # first-seen across arms of DIFFERENT strength would let a courier-written stored proof
        # (seen first, at the origin hop) shadow the human's own prompt record found later in the
        # same climb. The rule is a two-rank ladder — a prompt record REPLACES a held stored proof;
        # within a rank the slot stays first-seen. Red against a naive first-seen-across-arms
        # demotion, which is the regression this pins out.
        self._store(MGR, {
            "q": _node("q", "the dictated ask", None, cleared=True, promptUuid="hu"),
            "x": _node("x", "a fanned step", "q",
                       origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"})})
        self._store(GRAND, {"ga": _node("ga", "the original ask", None, cleared=True,
                                        userAsk={"text": "the user's ask", "sid": GRAND},
                                        askRef={"peer": GRAND, "goalId": "ga"})})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "x", self.paths, NOW)
        self.assertTrue(rec)
        self.assertFalse(rec.get("carded"), "nothing renders — still the mint shape")
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "q"},
                         "the human's own record outranks the courier-written copy seen first")
        self.assertEqual(rec.get("sid"), MGR)

    def test_an_origin_hop_to_a_live_carded_grand_ask_is_decisive_with_its_askref(self):
        # THE DECISIVE ARM'S PIN: the contract keeps exactly TWO decisive mid-climb answers, and
        # the carded hop stand-down has its own test. The other — a CARDED human prompt record
        # resolved in an INNER origin-hop frame, returned through the hop callsite — needs one
        # too: every other origin-hop test uses an archived or cleared grand node (the
        # fallback-slot path) or asserts bare truthiness. A refactor that lost the inner return
        # (demoting EVERY prompt record to the fallback slot, say) would still answer truthy here
        # — but carded:False, and the courier would mint a twin beside the grand-sender's own live
        # card. Pin the whole record.
        self._store(MGR, {"g1": _node("g1", "mid-chain ask", None,
                                      origin={"peer": GRAND, "goalId": "ga", "msgId": "m0"})})
        self._store(GRAND, {"ga": _node("ga", "the original ask", None, promptUuid="hu")})
        self._human(GRAND)
        rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self.assertTrue(rec, "the hop reaches the grand-sender's live human root")
        self.assertTrue(rec.get("carded"),
                        "the grand node still renders its card — decisive, never demoted to the slot")
        self.assertEqual(rec.get("askRef"), {"peer": GRAND, "goalId": "ga"},
                         "the record carries the grand node's identity, apply_courier's dedupe key")
        self.assertEqual(rec.get("sid"), GRAND)

    def test_a_cycle_exit_still_promotes_the_held_fallback(self):
        # PROMOTION-PATH PIN: the seen-guard loop exit is one of the places the climb exhausts,
        # and a record already riding the fallback slot must survive it —
        # test_cycles_terminate_quiet holds only the no-evidence half. A cleared ask inside the
        # cycle offers its uncarded record to the slot; the walk then bites its own tail and must
        # still promote that record at the top frame, never fall out None.
        self._store(MGR, {"a": _node("a", "the dictated ask", "b", cleared=True, promptUuid="hu"),
                          "b": _node("b", "a step", "a")})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "a", self.paths, NOW)
        self.assertTrue(rec, "the cycle terminates, but the held evidence still answers")
        self.assertFalse(rec.get("carded"), "a cleared ask renders nowhere — the mint shape")
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "a"})

    def test_a_missing_parent_exit_still_promotes_the_held_fallback(self):
        # PROMOTION-PATH PIN, the missing-node twin: a chain whose parent pointer dangles exits
        # through the not-a-dict guard, which must return the held slot at the top frame — the
        # identity half test_a_dangling_ask_reads_uncarded leaves unpinned (it asserts truthiness
        # and carded only).
        self._store(MGR, {"g1": _node("g1", "the dictated ask", "swept-away",
                                      cleared=True, promptUuid="hu")})
        self._human(MGR)
        rec = jd._delegate_user_rooted(MGR, "g1", self.paths, NOW)
        self.assertTrue(rec)
        self.assertFalse(rec.get("carded"))
        self.assertEqual(rec.get("askRef"), {"peer": MGR, "goalId": "g1"})


BODY = ("Verify the staged run references and report drift.\n"
        "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID)
LINK_REPLY = '{"verdict": "delegating", "goal": 1, "text": "verify staged run references"}'


class CourierMintMatrix(unittest.TestCase):
    """run_courier end to end: a user-rooted delegate mints the recipient top exactly as before
    (origin, tracking node, propagate wiring untouched); an untraceable one files QUIET — no
    recipient goal, placements fyi, and the sender's tracker marked for report-back completion."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.wpath = d / (WKR + ".jsonl")
        self.wpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, BODY, "m1", ps="sdk"),
            aline(T0 + 60, "On it: verifying the staged references now.", "a1", "m1")]) + "\n")
        self.mpath = d / (MGR + ".jsonl")
        self._msgs = jd.MESSAGES
        jd.MESSAGES = d / "messages.jsonl"
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate", "body": BODY.split("\n")[0]}) + "\n")
        self._disc = jd.discover
        fleet = [(WKR, str(self.wpath), None, "api"), (MGR, str(self.mpath), None, "web")]
        jd.discover = lambda now, window=None, forks=True: fleet
        self._llm = jd.courier_llm
        jd.courier_llm = lambda text, menu, declared=None: LINK_REPLY
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def tearDown(self):
        jd.MESSAGES = self._msgs
        jd.discover = self._disc
        jd.courier_llm = self._llm
        for sid in (MGR, WKR, GRAND):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def _mgr_store(self, prompt_uuid, records):
        st = {"rompUuid": MGR, "seq": 1, "nodes":
              {MGR + ":g1": _node(MGR + ":g1", "Ship the staged-run verification", None,
                                  promptUuid=prompt_uuid)},
              "placements": {}, "status": {}}
        jd.save_goals(MGR, st)
        self.mpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def test_a_user_rooted_linked_delegate_links_into_the_ask_card(self):
        # T101 (the user 2026-08-26) supersedes the mint here: the courier's link resolved to the
        # sender's ask node, so the ASK CARD carries the dispatch — the tracker plants under it
        # (quiet: the reply-sweep owns its ending) and the recipient gets NO standalone top. The
        # mint survives only for the LINKLESS rooted shape (tests/test_ask_unit_cards.py holds
        # that fallback plus the fan-out matrix).
        self._mgr_store("hu", [uline(T0 - 600, "please verify the staged run references", "hu"),
                               aline(T0 - 540, "Dispatching.", "ha", "hu")])
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        self.assertEqual([nd for nd in w["nodes"].values()
                          if isinstance(nd.get("origin"), dict)], [],
                         "no recipient top — the ask card is the unit")
        self.assertIn("fyi", set(w["placements"].values()))
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1, "the sender tracking node plants either way")
        self.assertEqual(trackers[0].get("parentId"), MGR + ":g1", "…under the ask it serves")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "no recipient goal will carry this msgId — the reply-sweep owns the ending")

    def test_a_rooted_dispatch_whose_ask_card_is_gone_mints_the_fallback_top(self):
        # T101's FALLBACK, MADE SATISFIABLE: as `mint_recipient = rooted and not link_id` this
        # could never fire — the trace returns None without a link (test_no_link_quiets is the
        # pin), so `rooted` implied `link_id` and the apply_courier mint below it was dead code.
        # The condition keys on the trace's OWN answer now: the root record says whether the
        # chain's proof still sits on a live ask node (`carded`). Here the courier links the
        # sender's live follow-up node, but the human proof lives only in the ARCHIVE — the ask's
        # own card is gone, so the recipient card IS the ask's card: minted, origin-stamped, frame
        # + userAsk intact, tracker un-quiet (the origin back-link owns its ending, exactly the
        # pre-T101 rooted-mint wiring).
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 2, "nodes": {
            MGR + ":g2": _node(MGR + ":g2", "Verification follow-up", MGR + ":g1")},
            "placements": {}, "status": {}})
        jd.save_goal_archive(MGR, {"rompUuid": MGR, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "Ship the staged-run verification", None,
                               promptUuid="hu")},
            "placements": {}, "status": {}})
        self.mpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 - 600, "please verify the staged run references", "hu"),
            aline(T0 - 540, "Dispatching.", "ha", "hu")]) + "\n")
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        planted = [nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(len(planted), 1,
                         "no live ask card anywhere → the recipient card IS the ask's card")
        self.assertTrue(planted[0].get("frame"), "the fallback keeps the frame enrichment")
        self.assertEqual((planted[0].get("userAsk") or {}).get("sid"), MGR,
                         "…and stores the chain-proven root record (T105)")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1)
        self.assertFalse(trackers[0]["handoff"].get("quiet"),
                         "a recipient goal carries the msgId — the origin back-link owns the ending")

    def test_a_dispatch_linked_to_a_dangling_ask_mints(self):
        # END TO END: the courier's link resolves to a live ask whose parent was rewind-swept —
        # the node pierces the menu (a missing ancestor never seals) but renders on NO card (the
        # feed walks top subtrees). Reading it "carded" would link the fan-out into a card that
        # renders nowhere; it must take the fallback mint instead.
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 1, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "Ship the staged-run verification", "swept-away",
                               promptUuid="hu")},
            "placements": {}, "status": {}})
        self.mpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 - 600, "please verify the staged run references", "hu"),
            aline(T0 - 540, "Dispatching.", "ha", "hu")]) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        planted = [nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(len(planted), 1,
                         "a dangling ask has no visible card — the recipient card IS the ask's card")

    def test_a_cleared_live_ask_mints_like_an_archived_one(self):
        # END TO END + the compaction-boundary pin: the root ask is CLEARED but still live (the
        # compactor hasn't run). The chain reaches it through the origin hop of a courier-planted
        # mid-chain top (pre-T105: no userAsk on it). With bare live membership this read
        # carded=True and the courier filed quiet — the same world a compaction pass later minted,
        # so the decision flipped on bookkeeping. A cleared card renders nowhere: mint now, exactly
        # as the archived twin does.
        gpath = Path(self.td.name) / (GRAND + ".jsonl")
        gpath.write_text(json.dumps(uline(T0 - 900, "the dictated round", "hu")) + "\n")
        jd.discover = lambda now, window=None, forks=True: [
            (WKR, str(self.wpath), None, "api"), (MGR, str(self.mpath), None, "web"),
            (GRAND, str(gpath), None, "tests")]
        jd.save_goals(GRAND, {"rompUuid": GRAND, "seq": 2, "nodes": {
            GRAND + ":g9": _node(GRAND + ":g9", "the original ask", None, promptUuid="hu",
                                 cleared=True),
            GRAND + ":t1": _node(GRAND + ":t1", "↪ delegated", GRAND + ":g9")},
            "placements": {}, "status": {}})
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 1, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "mid-chain ask", None,
                               origin={"peer": GRAND, "goalId": GRAND + ":t1", "msgId": "m0"})},
            "placements": {}, "status": {}})
        self.mpath.write_text(json.dumps(aline(T0 - 540, "Working the round.", "ha")) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        planted = [nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(len(planted), 1,
                         "a cleared-live ask is exactly as card-less as its archived twin — mint")

    def test_an_origin_hop_to_a_live_carded_ask_links_instead_of_minting(self):
        # THE DECISIVE ARM END TO END: the cleared-live twin above mints; this is the same hop
        # shape with the grand-sender's ask node LIVE and VISIBLE, the half that must NOT mint. The
        # chain reaches the carded human root through the origin hop, so the recipient gets no
        # standalone top — the ask's own card carries the fan-out — and the tracker files quiet.
        gpath = Path(self.td.name) / (GRAND + ".jsonl")
        gpath.write_text(json.dumps(uline(T0 - 900, "the dictated round", "hu")) + "\n")
        jd.discover = lambda now, window=None, forks=True: [
            (WKR, str(self.wpath), None, "api"), (MGR, str(self.mpath), None, "web"),
            (GRAND, str(gpath), None, "tests")]
        jd.save_goals(GRAND, {"rompUuid": GRAND, "seq": 2, "nodes": {
            GRAND + ":g9": _node(GRAND + ":g9", "the original ask", None, promptUuid="hu"),
            GRAND + ":t1": _node(GRAND + ":t1", "↪ delegated", GRAND + ":g9")},
            "placements": {}, "status": {}})
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 1, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "mid-chain ask", None,
                               origin={"peer": GRAND, "goalId": GRAND + ":t1", "msgId": "m0"})},
            "placements": {}, "status": {}})
        self.mpath.write_text(json.dumps(aline(T0 - 540, "Working the round.", "ha")) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        planted = [nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(planted, [],
                         "the ask still renders on the grand-sender's live card — link, never a twin")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1, "the sender tracker still plants")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "no recipient goal will carry this msgId — the reply-sweep owns the ending")

    def test_a_genuinely_unlinked_delegate_still_files_quiet(self):
        # THE FALLBACK DECISION'S OTHER HALF, pinned: with no link there is no chain to trace, so a
        # linkless dispatch is never PROVABLY rooted — and at mint time uncertainty files quiet
        # (the 2026-08-25 verdict; test_no_link_quiets is the trace-side pin). The mint keys on
        # proof-without-a-live-card, never on linklessness alone.
        self._mgr_store("hu", [uline(T0 - 600, "please verify the staged run references", "hu"),
                               aline(T0 - 540, "Dispatching.", "ha", "hu")])
        jd.courier_llm = lambda text, menu, declared=None: \
            '{"verdict": "delegating", "goal": 0, "text": "verify staged run references"}'
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        self.assertEqual([nd for nd in w["nodes"].values()
                          if isinstance(nd.get("origin"), dict)], [],
                         "no recipient top on an unprovable chain")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1, "the sender tracker still plants")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "…quiet: the reply-sweep owns its ending")

    def test_an_untraceable_delegate_files_quiet(self):
        # the sender's linked goal roots at a COORDINATE mail record — team-internal, not the user
        self._mgr_store("cm", [uline(T0 - 600, "heads-up: refs regenerated\n"
                                     "<!-- romp-msg-id: 1787098000.000001_2.TESTHOST -->\n"
                                     "<!-- romp-msg-kind: coordinate -->", "cm", ps="sdk"),
                               aline(T0 - 540, "Noted; queueing a verification.", "ha", "cm")])
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        self.assertEqual([nd for nd in w["nodes"].values()
                          if isinstance(nd.get("origin"), dict)], [],
                         "no standalone recipient top for a team-internal chain")
        seg_vals = set(w["placements"].values())
        self.assertIn("fyi", seg_vals, "the segment is processed quietly — the coordinate treatment")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values()
                    if isinstance(nd.get("handoff"), dict) and nd["handoff"].get("msgId") == MID]
        self.assertEqual(len(trackers), 1,
                         "the delegation still lives one glance away on the SENDER's board")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "marked for report-back completion: no recipient goal will carry this msgId")


MID_B = "1787099000.000002_1.TESTHOST"
BODY_B = ("Verify the staged run references, second pass.\n"
          "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID_B)


class FanOutDedupe(unittest.TestCase):
    """The fallback mint was idempotent per msgId ONLY, so one ask fanned N times with archive-only
    proof minted N origin-stamped tops on the same recipient, and every origin-hop level could
    re-mint. The mint dedupes by ASK IDENTITY — the proof node's (sender sid, goal id), carried up
    the trace as askRef and stamped on the minted top — and an intermediate userAsk-bearing top
    that already shows stands the whole re-mint down (the trace's carded short-circuit). Each
    RECIPIENT session still gets its one card: the dedupe is per (recipient store, ask identity),
    never global."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.wpath = d / (WKR + ".jsonl")
        self.mpath = d / (MGR + ".jsonl")
        self.gpath = d / (GRAND + ".jsonl")
        self._msgs = jd.MESSAGES
        jd.MESSAGES = d / "messages.jsonl"
        self._disc = jd.discover
        roster = [(WKR, str(self.wpath), None, "api"), (MGR, str(self.mpath), None, "web"),
                  (GRAND, str(self.gpath), None, "tests")]
        jd.discover = lambda now, window=None, forks=True: roster
        self._llm = jd.courier_llm
        jd.courier_llm = lambda text, menu, declared=None: LINK_REPLY
        self.gpath.write_text(json.dumps(aline(T0 - 1000, "quiet.", "gz")) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def tearDown(self):
        jd.MESSAGES = self._msgs
        jd.discover = self._disc
        jd.courier_llm = self._llm
        for sid in (MGR, WKR, GRAND):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def test_one_ask_fanned_twice_to_one_recipient_mints_once(self):
        # the archive-only fallback shape, dispatched TWICE to the same worker: msgId idempotency
        # alone minted two near-duplicate tops (the exact card T101 exists to prevent)
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 2, "nodes": {
            MGR + ":g2": _node(MGR + ":g2", "Verification follow-up", MGR + ":g1")},
            "placements": {}, "status": {}})
        jd.save_goal_archive(MGR, {"rompUuid": MGR, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "Ship the staged-run verification", None,
                               promptUuid="hu")},
            "placements": {}, "status": {}})
        self.mpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 - 600, "please verify the staged run references", "hu"),
            aline(T0 - 540, "Dispatching.", "ha", "hu")]) + "\n")
        self.wpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, BODY, "m1", ps="sdk"), aline(T0 + 60, "On it.", "a1", "m1"),
            uline(T0 + 120, BODY_B, "m2", "a1", ps="sdk"),
            aline(T0 + 180, "And the second pass.", "a2", "m2")]) + "\n")
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in [
            {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate", "body": BODY.split("\n")[0]},
            {"t": T0 + 120, "ev": "sent", "id": MID_B, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate", "body": BODY_B.split("\n")[0]}]) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        planted = [nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(len(planted), 1, "one ask, one recipient card — however many dispatches")
        top = planted[0]
        self.assertEqual(top.get("askRef"), {"peer": MGR, "goalId": MGR + ":g1"},
                         "the mint carries the ask identity the dedupe keys on")
        self.assertIn(MID_B, [l.get("msgId") for l in (top.get("links") or [])],
                      "the second dispatch LINKS into the standing card, so its tracker still ends")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values() if isinstance(nd.get("handoff"), dict)]
        self.assertEqual(len(trackers), 2, "each dispatch keeps its sender-side tracker")
        self.assertFalse(any(t["handoff"].get("quiet") for t in trackers),
                         "both trackers have a completion event on the one recipient card")

    def test_a_hop_re_mint_stands_down_when_the_intermediate_ask_top_shows(self):
        # the hop shape: the root ask is archive-only on GRAND, but MGR's own courier-planted top
        # (userAsk-bearing, live, visible) IS the ask's card — dispatching onward must file quiet
        # under it, not re-mint an origin-stamped top on the worker at every hop level
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 1, "nodes": {
            MGR + ":gM": _node(MGR + ":gM", "mid-chain ask", None,
                               origin={"peer": GRAND, "goalId": GRAND + ":t1", "msgId": "m0"},
                               userAsk={"text": "the dictated round", "sid": GRAND})},
            "placements": {}, "status": {}})
        jd.save_goal_archive(GRAND, {"rompUuid": GRAND, "nodes": {
            GRAND + ":g9": _node(GRAND + ":g9", "the original ask", None, promptUuid="hu"),
            GRAND + ":t1": _node(GRAND + ":t1", "↪ delegated", GRAND + ":g9")},
            "placements": {}, "status": {}})
        self.gpath.write_text(json.dumps(uline(T0 - 900, "the dictated round", "hu")) + "\n")
        self.mpath.write_text(json.dumps(aline(T0 - 540, "Fanning the round out.", "ha")) + "\n")
        self.wpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, BODY, "m1", ps="sdk"), aline(T0 + 60, "On it.", "a1", "m1")]) + "\n")
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0, "ev": "sent", "id": MID, "from": "web", "from_id": MGR,
             "to_id": WKR, "kind": "delegate", "body": BODY.split("\n")[0]}) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd.run_courier(now=NOW)
        w = jd.load_goals(WKR)
        self.assertEqual([nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)],
                         [], "the ask already shows on the intermediate's board — no re-mint")
        self.assertIn("fyi", set(w["placements"].values()), "the recipient files quietly")
        m = jd.load_goals(MGR)
        trackers = [nd for nd in m["nodes"].values() if isinstance(nd.get("handoff"), dict)]
        self.assertEqual(len(trackers), 1)
        self.assertEqual(trackers[0].get("parentId"), MGR + ":gM",
                         "the fan-out lives INSIDE the intermediate's ask card")
        self.assertTrue(trackers[0]["handoff"].get("quiet"),
                        "no recipient goal will carry this msgId — the reply-sweep owns the ending")


class ConfirmingWindowDispatch(unittest.TestCase):
    """A done-verdict-filed top whose settle is pending — the rollup's `confirming` export — still
    renders visibly in Working (the steady doneConfirming cue), so a same-ask dispatch must LINK
    into it, not mint beside it. Bare nodeComplete would call the confirming window dead. Only a
    genuinely SETTLED completion stays uncarded — the sealed-open trade holds."""

    def setUp(self):
        self._saved = jd.parsed_session
        jd.parsed_session = lambda sid, files, now: _fake_session([
            {"uuid": "hu", "type": "user", "author": "human",
             "message": {"role": "user", "content": "verify the staged run references"}}])

    def tearDown(self):
        jd.parsed_session = self._saved
        for sid in (MGR, WKR):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass

    def _confirming_store(self):
        st = {"rompUuid": MGR, "seq": 1, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "Ship the staged-run verification", None,
                               promptUuid="hu")},
            "placements": {}, "status": {}, "lastNode": MGR + ":g1"}
        jd.record_verdict(st, st["nodes"][MGR + ":g1"], "closer", "done", T0 + 100,
                          why="both halves landed")
        jd.rollup_status(st, False)                    # focus held → the settle is still pending
        return st

    def test_a_confirming_ask_reads_carded_so_the_dispatch_links(self):
        st = self._confirming_store()
        self.assertIn(MGR + ":g1", st.get("confirming") or (),
                      "fixture: the rollup exports the done-confirming window")
        self.assertEqual(st["status"].get(MGR + ":g1", "working"), "working",
                         "fixture: the card still renders in Working")
        jd.save_goals(MGR, st)
        rec = jd._delegate_user_rooted(MGR, MGR + ":g1", {MGR: "/dev/null"}, NOW)
        self.assertTrue(rec)
        self.assertTrue(rec.get("carded"),
                        "the confirming card is visible — link into it, never mint beside it")

    def test_a_settled_completion_stays_uncarded(self):
        st = self._confirming_store()
        jd.rollup_status(st, True)                     # the session hands back the floor → settle
        self.assertEqual(st["status"].get(MGR + ":g1"), "completed")
        jd.save_goals(MGR, st)
        rec = jd._delegate_user_rooted(MGR, MGR + ":g1", {MGR: "/dev/null"}, NOW)
        self.assertTrue(rec, "the chain still proves the human root")
        self.assertFalse(rec.get("carded"),
                         "a settled completion renders in Completed, not Working — mint")

    def test_a_second_dispatch_links_into_the_confirming_recipient_top(self):
        # recipient side: the standing origin-stamped top is done-confirming — the ask dedupe
        # must accept it as the standing card rather than minting a twin beside the cue
        wtop = WKR + ":g1"
        wstore = {"rompUuid": WKR, "seq": 1, "nodes": {
            wtop: _node(wtop, "Verify the staged run references", None,
                        origin={"peer": MGR, "goalId": MGR + ":t1", "msgId": MID},
                        userAsk={"text": "verify the staged run references", "sid": MGR},
                        askRef={"peer": MGR, "goalId": MGR + ":g1"})},
            "placements": {}, "status": {}, "lastNode": wtop}
        jd.record_verdict(wstore, wstore["nodes"][wtop], "closer", "done", T0 + 100, why="landed")
        jd.rollup_status(wstore, False)
        self.assertIn(wtop, wstore.get("confirming") or ())
        rec = {"text": "verify the staged run references", "sid": MGR, "carded": False,
               "askRef": {"peer": MGR, "goalId": MGR + ":g1"}}
        before = set(wstore["nodes"])
        nid = jd.apply_courier(wstore, "seg2", NOW, "verify the refs, second pass",
                               {"peer": MGR, "goalId": MGR + ":t2", "msgId": MID_B},
                               prompt_uuid="anchor2", frame="second pass", user_ask=rec)
        self.assertEqual(nid, wtop, "the dispatch LINKS into the confirming card")
        self.assertEqual(set(wstore["nodes"]), before, "…and mints nothing beside it")
        self.assertIn(MID_B, [l.get("msgId") for l in (wstore["nodes"][wtop].get("links") or [])],
                      "the new dispatch's tracker still gets its completion event")


class AskRefRecycling(unittest.TestCase):
    """Goal ids are sid:gN with a per-store seq that RESETS when the sender's store file is lost,
    so a NEW ask can recycle an OLD ask's exact goal id — and a dedupe keyed on (sender sid, goal
    id) alone links the new ask's dispatch into the OLD ask's standing card: no card for the new
    ask, and the old card's completion would end the new dispatch's tracker. The standing top's
    own userAsk text is the identity cross-check — the stamp and the dedupe share one shaping
    (_ask_stamp_text) — so the same ask still links while a recycled id over DIFFERENT dictation
    mints its own card."""

    def _standing(self):
        wtop = WKR + ":g1"
        return wtop, {"rompUuid": WKR, "seq": 1, "nodes": {
            wtop: _node(wtop, "Ship the demo build", None,
                        origin={"peer": MGR, "goalId": MGR + ":t1", "msgId": "m1"},
                        userAsk={"text": "ship the demo build", "sid": MGR},
                        askRef={"peer": MGR, "goalId": MGR + ":g1"})},
            "placements": {}, "status": {}}

    def test_a_recycled_askref_over_different_dictation_mints_fresh(self):
        wtop, wstore = self._standing()
        rec = {"text": "audit the release checklist", "sid": MGR, "carded": False,
               "askRef": {"peer": MGR, "goalId": MGR + ":g1"}}   # the RECYCLED identity
        nid = jd.apply_courier(wstore, "seg2", NOW, "audit the release checklist",
                               {"peer": MGR, "goalId": MGR + ":t9", "msgId": "m2"},
                               prompt_uuid="anchor2", frame="audit the release checklist",
                               user_ask=rec)
        self.assertNotEqual(nid, wtop,
                            "different dictation behind the same goal id is a NEW ask — mint")
        self.assertEqual((wstore["nodes"][nid].get("userAsk") or {}).get("text"),
                         "audit the release checklist", "…carrying its own dictation evidence")
        self.assertEqual(wstore["nodes"][wtop].get("links") or [], [],
                         "…and the old ask's card is untouched — its completion ends nothing new")

    def test_the_same_dictation_still_links(self):
        wtop, wstore = self._standing()
        rec = {"text": "ship the demo build", "sid": MGR, "carded": False,
               "askRef": {"peer": MGR, "goalId": MGR + ":g1"}}
        nid = jd.apply_courier(wstore, "seg3", NOW, "ship the demo build",
                               {"peer": MGR, "goalId": MGR + ":t2", "msgId": "m3"},
                               prompt_uuid="anchor3", frame="ship it", user_ask=rec)
        self.assertEqual(nid, wtop, "the ask's identity holds — one card")
        self.assertIn("m3", [l.get("msgId")
                             for l in (wstore["nodes"][wtop].get("links") or [])])


class QuietReportBack(unittest.TestCase):
    """run_propagate's reply sweep: a LOCAL quiet tracker completes on the recipient's reply (the
    report-back event, the cross-host rule); a LINKED local tracker stays the origin back-link's."""

    def setUp(self):
        self._msgs = jd.MESSAGES
        self.td = tempfile.TemporaryDirectory()
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        self._disc = jd.discover
        jd.discover = lambda now, window=None, forks=True: [(MGR, "/dev/null", None, "web")]

    def tearDown(self):
        jd.MESSAGES = self._msgs
        jd.discover = self._disc
        for d in (jd.GOALDIR, jd.GOALARCHDIR):
            try:
                (d / (MGR + ".json")).unlink()
            except OSError:
                pass
        self.td.cleanup()

    def _world(self, quiet, reply_at):
        h = {"peer": WKR, "msgId": MID}
        if quiet:
            h["quiet"] = True
        st = {"rompUuid": MGR, "seq": 1, "nodes":
              {MGR + ":t1": _node(MGR + ":t1", "↪ delegated to api: verify refs", None,
                                  t=T0, handoff=h)},
              "placements": {}, "status": {}}
        jd.save_goals(MGR, st)
        rows = []
        if reply_at:
            rows.append({"t": reply_at, "ev": "sent", "id": "r1", "from_id": WKR,
                         "to_id": MGR, "kind": "coordinate", "body": "verified; drift is zero"})
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))

    def test_a_quiet_tracker_completes_on_the_reply(self):
        self._world(quiet=True, reply_at=T0 + 900)
        jd.run_propagate(now=NOW)
        nd = jd.load_goals(MGR)["nodes"][MGR + ":t1"]
        self.assertTrue(nd.get("nodeComplete"))
        self.assertIn("quiet-filed", nd.get("doneWhy") or "")

    def test_no_reply_no_completion(self):
        self._world(quiet=True, reply_at=None)
        jd.run_propagate(now=NOW)
        self.assertFalse(jd.load_goals(MGR)["nodes"][MGR + ":t1"].get("nodeComplete"))

    def test_a_reply_before_the_send_does_not_count(self):
        self._world(quiet=True, reply_at=T0 - 900)
        jd.run_propagate(now=NOW)
        self.assertFalse(jd.load_goals(MGR)["nodes"][MGR + ":t1"].get("nodeComplete"))

    def test_a_linked_local_tracker_stays_the_back_links(self):
        self._world(quiet=False, reply_at=T0 + 900)
        jd.run_propagate(now=NOW)
        self.assertFalse(jd.load_goals(MGR)["nodes"][MGR + ":t1"].get("nodeComplete"),
                         "a linked recipient goal exists — its completion is the event, not the reply")

    def test_a_reply_that_came_back_completes_nothing(self):
        # 2026-09-08: the worker's report was bounced back to it (the oversize push: the message is not
        # put back in the manager's box) or recalled before the manager read it — a report the manager
        # never received is not a report-back, so the tracker stays open. Guard:
        # test_a_quiet_tracker_completes_on_the_reply (the same world, nothing naming the reply).
        for name, terminal in (("bounced", {"t": T0 + 950, "ev": "bounced", "id": "r1", "to": "web", "host": "",
                                            "why": "your message is 90000 bytes as delivered, over the limit"}),
                               ("recalled", {"t": T0 + 950, "ev": "recall", "id": "r1"})):
            self._world(quiet=True, reply_at=T0 + 900)
            with jd.MESSAGES.open("a") as fh:
                fh.write(json.dumps(terminal) + "\n")
            jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
            jd.run_propagate(now=NOW)
            nd = jd.load_goals(MGR)["nodes"][MGR + ":t1"]
            self.assertFalse(nd.get("nodeComplete"),
                             "%s: base — marked done, reported back by a message the manager never got" % name)


class NeedsYouStillSurfaces(unittest.TestCase):
    """THE CRITICAL PIN: quiet filing leaves zero goal nodes, and the needs-you surfaces are
    goal-independent — the placeholder synthesizes a needs-input card from the LIVE prompt alone
    (tests/test_kernel_blocked_no_goal.py holds the full behavioral matrix; this pins the
    store-independence that makes quiet filing safe)."""

    def test_the_placeholder_takes_no_goal_store(self):
        params = list(signature(km._blocked_placeholder).parameters)
        self.assertNotIn("store", params, "a session with ZERO goal nodes still surfaces")
        card = km._blocked_placeholder({"path": "/dev/null"}, "api", None, WKR, True,
                                       NOW, "permission", NOW - 60)
        self.assertEqual(card["column"], "needs_input")
        self.assertTrue(card["blocked"])


class TheSplitIsRetired(unittest.TestCase):
    """Absence pins (the user 2026-08-25 ~19:4x verdict, paraphrased: no lens/button — the cards
    must not be created): the build-time classifier and the footer toggle are gone."""

    FEED = (Path(HERE).parent / "ui" / "webview" / "feed.ts").read_text()
    KERNEL = Path(os.path.join(BIN, "romp-kernel")).resolve().read_text()

    def test_the_kernel_walk_is_gone(self):
        for tok in ("_ProvenanceWalk", "_prov_atom_klass", '"internal": True'):
            self.assertNotIn(tok, self.KERNEL, tok)

    def test_the_footer_toggle_is_gone(self):
        for tok in ("feed-internal-lens", "teamInternals", "internalLensOn", "internal?:"):
            self.assertNotIn(tok, self.FEED, tok)   # the retirement note may SAY "team-internal";
            #                                         no mechanism may remain

    def test_the_tag_lens_slot_family_survives(self):
        for tok in ("function viewScope", "function viewBase", "function viewFiltered"):
            self.assertIn(tok, self.FEED, "the T70 tag lens keeps its slots — only the internals slot retired")


if __name__ == "__main__":
    unittest.main()
