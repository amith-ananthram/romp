#!/usr/bin/env python3
"""A safeguards refusal the CLI retried on a fallback model is filed, not logged as unhandled (T279).

The CLI streams a system/model_refusal_fallback frame when the model's classifier declined the request
and the turn was re-run on the configured fallback model. The SDK backend had no branch for it, so the
frame fell to the once-per-life "unhandled SystemMessage subtype" log line and nothing was filed: the
judges' only record of the swap was the capacity-fallback card the fallback reply's own model learn
minted — with the wrong cause. Now the frame files the refusal through a kernel-wired hook (the
on_model_fallback idiom) into a completed card naming the refusal category and the API's explanation,
and the capacity card the same turn's model learn minted for the swap is FOLDED into it with the store's
merge machinery (claimed by exact id, only while the user has not acted on it), so one swap is one card. Field names follow the CLI's stream-json schema (bundled CLI 2.1.259): original_model,
fallback_model, api_refusal_category (open string, nullable), api_refusal_explanation (display-only
prose, nullable), scope ('session' | 'local'; absent on older CLIs = session). SYNTHETIC only."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
sb = load_source("romp_sdk_backend",
                      os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))

SID = "aaaaaaaa-1111-2222-3333-444444444444"
CATEGORY = "synthetic-category"
EXPLANATION = "a synthetic explanation of the refusal"


def wire_frame(**over):
    """The stream frame, snake_case per the CLI's schema; every value invented."""
    d = {"type": "system", "subtype": "model_refusal_fallback", "direction": "retry",
         "trigger": "refusal", "scope": "session",
         "original_model": "claude-fable-5", "fallback_model": "claude-opus-5",
         "request_id": "req_synthetic", "api_refusal_category": CATEGORY,
         "api_refusal_explanation": EXPLANATION,
         "retracted_message_uuids": ["aaaaaaaa-1111-2222-3333-444444444401"],
         "refused_user_message_uuid": "aaaaaaaa-1111-2222-3333-444444444400",
         "content": "The model's safeguards flagged this message. Switched to a fallback model.",
         "uuid": "aaaaaaaa-1111-2222-3333-444444444402", "session_id": SID}
    d.update(over)
    return d


class StreamBranchFilesTheRefusal(unittest.TestCase):
    """_on_message takes the SDK message classes as PARAMETERS (the lazy-import seam), so the branch
    runs hermetically with stand-in classes — the SidechainNeverLearns idiom."""

    class _SM:
        def __init__(self, subtype, data):
            self.subtype, self.data = subtype, data
            self.parent_tool_use_id = None

    class _AM:                       # AssistantMessage stand-in (the learn path)
        def __init__(self, model, ptid=None):
            self.model = model
            self.parent_tool_use_id = ptid
            self.error = None
            self.content = []

    class _RM:
        pass

    def setUp(self):
        sb.SdkSession._sys_subtypes_seen = set()

    def tearDown(self):
        sb.SdkSession._sys_subtypes_seen = set()

    def _session(self, hook_raises=False):
        calls = {"refusal": [], "fallback": [], "log": []}

        class Backend:
            state_dir = None

            def on_model_refusal_fallback(sid, frm, to, category, explanation, scope, capacity_gids, episode):
                if hook_raises:                          # looked up on the CLASS, called unbound —
                    raise RuntimeError("synthetic hook failure")   # exactly how the kernel wires it
                calls["refusal"].append((sid, frm, to, category, explanation, scope, capacity_gids, episode))

            def on_model_fallback(sid, frm, to):
                calls["fallback"].append((frm, to))
                return (SID + ":g7", None)               # the kernel's lambda returns (gid, _push_soon())

            def _update_reg(self, sid, **kw):
                pass

            def _poke(self):
                pass

            def _forward(self, sess, msg):
                pass

            def _log(self, line, *a, **k):
                calls["log"].append((line, bool(k.get("problem"))))

        s = object.__new__(sb.SdkSession)
        s.backend = Backend()
        s.sid = SID
        s.name = "web"
        s.model = "Fable 5"
        s._model_id = "claude-fable-5"
        s._model_pending = ""
        s.retrying = False
        s.retry_count = 0
        s.retry_info = None
        return s, calls

    def _feed(self, s, data):
        s._on_message(self._SM("model_refusal_fallback", data), self._AM, self._RM, self._SM)

    def test_the_frame_files_the_refusal_through_the_hook_with_pretty_names(self):
        s, calls = self._session()
        self._feed(s, wire_frame())
        self.assertEqual(calls["refusal"],
                         [(SID, "Fable 5", "Opus 5", CATEGORY, EXPLANATION, "session", [],
                           "aaaaaaaa-1111-2222-3333-444444444400")],
                         "the same pretty names the capacity path files; no capacity card was learned this turn")
        self.assertEqual(calls["fallback"], [], "the branch files a refusal, never a capacity swap")

    def test_the_frame_is_no_longer_logged_as_unhandled(self):
        s, calls = self._session()
        self._feed(s, wire_frame())
        self.assertFalse(any("unhandled SystemMessage" in line for line, _ in calls["log"]), calls["log"])
        self.assertNotIn("model_refusal_fallback", sb.SdkSession._sys_subtypes_seen)

    def test_null_fields_and_an_absent_scope_normalise(self):
        # the schema: category/explanation null when the response carried none (normal, not an error);
        # scope absent on older CLIs = the session model was swapped
        s, calls = self._session()
        d = wire_frame(api_refusal_category=None, api_refusal_explanation=None)
        del d["scope"]
        self._feed(s, d)
        self.assertEqual(calls["refusal"][0][:7], (SID, "Fable 5", "Opus 5", "", "", "session", []))

    def test_a_local_scope_rides_through_and_claims_no_capacity_card(self):
        # 'local': a subagent / side question fell back — only that reply came from the fallback model;
        # the session's own model never changed, so no capacity card of this turn is the swap's
        s, calls = self._session()
        s._swap_cards = [("Fable 5", "Opus 5", SID + ":g7")]
        self._feed(s, wire_frame(scope="local"))
        self.assertEqual(calls["refusal"][0][5:7], ("local", []))

    def test_the_learn_captures_the_capacity_card_the_fallback_reply_minted(self):
        # the fallback model's reply streams first: its learn mints the capacity card; the branch keeps
        # the swap and the card's id so the end-of-turn notice can name exactly that card
        s, calls = self._session()
        s._on_message(self._AM("claude-opus-5"), self._AM, self._RM, self._SM)
        self.assertEqual(calls["fallback"], [("Fable 5", "Opus 5")])
        self.assertEqual(s._swap_cards, [("Fable 5", "Opus 5", SID + ":g7")])
        self._feed(s, wire_frame())
        self.assertEqual(calls["refusal"][0][6], [SID + ":g7"], "the notice names the card the learn minted")

    def test_every_card_learned_this_turn_rides_the_hook_for_the_store_to_judge(self):
        # a multi-hop chain learns a card per hop (origin -> hop 1, hop 1 -> hop 2); the store decides
        # which of them the episode's final frame folds, so all of this turn's cards ride along
        s, calls = self._session()
        s._swap_cards = [("Fable 5", "Opus 5", SID + ":g7"), ("Opus 5", "Sonnet 5", SID + ":g8")]
        self._feed(s, wire_frame(fallback_model="claude-sonnet-5"))
        self.assertEqual(calls["refusal"][0][6], [SID + ":g7", SID + ":g8"])

    def test_a_provisional_frame_files_too(self):
        # an intermediate hop of a multi-hop chain (the first fallback refused too) may arrive marked
        # provisional, and on the CLI's chain path no final frame need follow it — so it files like any
        # other frame; a later hop of the same episode folds its card (the store's episode fold). Skipping
        # it would drop the refusal's filing entirely (review, 2026-09-09).
        s, calls = self._session()
        self._feed(s, wire_frame(provisional=True))
        self.assertEqual(len(calls["refusal"]), 1)
        self.assertEqual(calls["refusal"][0][7], "aaaaaaaa-1111-2222-3333-444444444400", "the episode key rides")
        self.assertFalse(any("unhandled SystemMessage" in line for line, _ in calls["log"]), calls["log"])

    def test_the_settle_forgets_the_captured_swap(self):
        # the turn's end is the deciding event: a card learned in an earlier turn is never claimed later
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i = src.index("        elif isinstance(msg, ResultMessage):" + chr(10) + "            try:")
        j = src.index('        elif getattr(msg, "rate_limit_info", None) is not None:', i)
        self.assertIn("self._swap_cards = []", src[i:j], "the settle branch clears the captured swaps")

    def test_a_failing_hook_is_logged_as_a_problem_and_never_raises(self):
        s, calls = self._session(hook_raises=True)
        self._feed(s, wire_frame())
        probs = [line for line, problem in calls["log"] if problem]
        self.assertEqual(len(probs), 1, calls["log"])
        self.assertIn("refusal-fallback card", probs[0])
        self.assertIn("synthetic hook failure", probs[0])

    def test_the_branch_sits_between_the_task_tuple_and_the_unhandled_memo(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        i_tasks = src.index('"task_started", "task_progress", "task_updated", "task_notification"')
        i_branch = src.index('msg.subtype == "model_refusal_fallback"')
        i_trail = src.index("elif isinstance(msg, SystemMessage):", i_branch)   # the bare catch-all after it
        self.assertLess(i_tasks, i_branch)
        self.assertLess(i_branch, i_trail, "handled before the memo, so the memo never names it")
        self.assertIn("_sys_subtypes_seen", src[i_trail:i_trail + 1200], "that bare elif IS the memo")

    def test_an_unwired_hook_is_said_once_never_swallowed(self):
        # a backend without the kernel wiring must not turn the frame into a silent drop now that the
        # unhandled memo no longer names it: one line per kernel life says so
        s, calls = self._session()
        del type(s.backend).on_model_refusal_fallback
        self._feed(s, wire_frame())
        self._feed(s, wire_frame())
        hits = [line for line, _ in calls["log"] if "no on_model_refusal_fallback hook" in line]
        self.assertEqual(len(hits), 1, calls["log"])
        self.assertEqual(calls["refusal"], [])

    def test_a_frame_without_model_ids_is_a_logged_problem_not_a_card(self):
        # the schema marks both model fields required: a frame without them is a broken frame, and a
        # card reading "? → a fallback model" would hide the breakage behind a plausible card
        s, calls = self._session()
        d = wire_frame()
        del d["original_model"]; del d["fallback_model"]
        self._feed(s, d)
        self.assertEqual(calls["refusal"], [], "no card from a frame with no models")
        probs = [line for line, problem in calls["log"] if problem]
        self.assertEqual(len(probs), 1, calls["log"])
        self.assertIn("model_refusal_fallback", probs[0])
        self.assertIn("keys=", probs[0], "keys only, never the values")
        self.assertNotIn(EXPLANATION, probs[0])

    def test_the_episode_key_rides_the_hook(self):
        # the refused prompt's uuid keys the episode: a multi-hop refusal (the first fallback refused too)
        # streams a frame per hop, and the filing folds the earlier hop's card into the final one by it
        s, calls = self._session()
        self._feed(s, wire_frame())
        self.assertEqual(calls["refusal"][0][7], "aaaaaaaa-1111-2222-3333-444444444400")
        s2, calls2 = self._session()
        d = wire_frame(); d["refused_user_message_uuid"] = None
        self._feed(s2, d)
        self.assertEqual(calls2["refusal"][0][7], "", "a null key is no key")


class RefusalCard(unittest.TestCase):
    """OWN sid, like test_model_fallback_card's FallbackCard and for the same reason: load_goals replays
    the per-sid user-override journal, other modules journal gestures against the shared placeholder
    sid, and a fresh store's first node id collides."""

    RSID = "7b7b7b7b-8c8c-9d9d-0e0e-1f1f1f1f1f1f"
    T = 1_787_500_000

    def tearDown(self):
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        try:
            (jd._overrides_dir() / (self.RSID + ".jsonl")).unlink()
        except OSError:
            pass

    EPISODE = "aaaaaaaa-1111-2222-3333-444444444400"

    def _mint(self, to="Opus 5", **over):
        kw = dict(category=CATEGORY, explanation=EXPLANATION, scope="session", ev_t=self.T, episode=self.EPISODE)
        kw.update(over)
        return jd.mint_refusal_fallback_card(self.RSID, "Fable 5", to, **kw)

    def test_a_later_hop_of_the_same_episode_folds_the_earlier_hops_card(self):
        # a multi-hop refusal: the first fallback model refused too, so the CLI streams a frame per hop
        # (origin -> hop 1, then origin -> hop 2); the final hop's card is the record, the earlier one
        # folds into it — same episode key, different swap — instead of two cards for one episode
        g1 = self._mint(to="Opus 5")
        g2 = self._mint(to="Sonnet 5", ev_t=self.T + 3)
        self.assertTrue(g1 and g2 and g1 != g2)
        store = jd.load_goals(self.RSID)
        self.assertEqual(list(store["nodes"]), [g2])
        nd = store["nodes"][g2]
        self.assertIn("Fable 5 → Sonnet 5", nd["text"])
        self.assertEqual([m["id"] for m in nd["mergedFrom"]], [g1])
        self.assertEqual(nd["episode"], self.EPISODE)

    def test_another_episodes_card_is_never_folded(self):
        g1 = self._mint(to="Opus 5")
        g2 = self._mint(to="Sonnet 5", ev_t=self.T + 3, episode="aaaaaaaa-1111-2222-3333-444444444499")
        self.assertEqual(sorted(jd.load_goals(self.RSID)["nodes"]), sorted([g1, g2]))

    def test_no_episode_key_folds_nothing(self):
        g1 = self._mint(to="Opus 5", episode="")
        g2 = self._mint(to="Sonnet 5", ev_t=self.T + 3, episode="")
        self.assertEqual(sorted(jd.load_goals(self.RSID)["nodes"]), sorted([g1, g2]))

    def _capacity(self, t=None):
        gid = jd.mint_fallback_card(self.RSID, "Fable 5", "Opus 5", ev_t=t or self.T)
        self.assertTrue(gid)
        return gid

    def test_mints_a_completed_card_naming_the_refusal_category_and_explanation(self):
        gid = self._mint()
        self.assertTrue(gid)
        store = jd.load_goals(self.RSID)
        nd = store["nodes"][gid]
        self.assertEqual(nd["text"], "Model changed after a safeguards refusal (%s): Fable 5 → Opus 5" % CATEGORY)
        self.assertTrue(nd["nodeComplete"])
        self.assertEqual(store["status"].get(gid), "completed", "pops straight into Completed")
        self.assertIn(CATEGORY, nd["doneWhy"], "the head: the category")
        self.assertIn(EXPLANATION, nd["doneWhy"], "the fold: the API's explanation")
        self.assertIn("Opus 5", nd["doneWhy"])
        self.assertNotIn("capacity", nd["doneWhy"].lower(), "a refusal is never filed as a capacity fallback")
        self.assertEqual(nd["why"], jd.REFUSAL_FALLBACK_WHY)
        self.assertEqual(nd["swap"], {"from": "Fable 5", "to": "Opus 5"}, "the swap as data, for the dedupes")
        dones = [e for e in nd["log"] if e.get("kind") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["src"], "romp", "kernel-authored bookkeeping, never a question")

    def test_null_category_and_explanation_still_mint_cleanly(self):
        gid = self._mint(category=None, explanation=None)
        nd = jd.load_goals(self.RSID)["nodes"][gid]
        self.assertEqual(nd["text"], "Model changed after a safeguards refusal: Fable 5 → Opus 5")
        self.assertNotIn("None", nd["doneWhy"])
        self.assertNotIn("()", nd["doneWhy"])
        self.assertIn("safeguards", nd["doneWhy"])

    def test_a_local_scope_names_only_the_reply_and_leaves_the_session_model_alone(self):
        gid = self._mint(scope="local")
        nd = jd.load_goals(self.RSID)["nodes"][gid]
        self.assertTrue(nd["text"].startswith("A reply came from Opus 5 after a safeguards refusal"))
        self.assertIn("unchanged", nd["doneWhy"], "the session's model is unchanged")
        self.assertNotIn("switch back", nd["doneWhy"].lower())

    def test_an_identical_uncleared_card_dedupes_the_mint(self):
        self.assertTrue(self._mint())
        self.assertIsNone(self._mint(ev_t=self.T + 5))
        self.assertEqual(len(jd.load_goals(self.RSID)["nodes"]), 1)

    def test_folds_the_capacity_card_the_fallback_reply_minted_into_the_refusal_card(self):
        # the fallback model's reply streams BEFORE the CLI's end-of-turn notice, and its model learn
        # files the swap as a capacity fallback; the notice names that card by id and the store's
        # merge folds it into the refusal card — a fresh node any concurrent save adopts wholesale,
        # and a durable tombstone for the capacity card
        gid_cap = self._capacity()
        gid = self._mint(capacity_gids=[gid_cap], ev_t=self.T + 1)
        self.assertTrue(gid)
        self.assertNotEqual(gid, gid_cap, "the refusal card is its own node")
        store = jd.load_goals(self.RSID)
        self.assertEqual(list(store["nodes"]), [gid], "one swap, one card")
        nd = store["nodes"][gid]
        self.assertEqual(nd["text"], "Model changed after a safeguards refusal (%s): Fable 5 → Opus 5" % CATEGORY)
        self.assertEqual(nd["why"], jd.REFUSAL_FALLBACK_WHY)
        self.assertIn(CATEGORY, nd["doneWhy"])
        self.assertNotIn("capacity", nd["doneWhy"].lower())
        self.assertEqual([m["id"] for m in nd["mergedFrom"]], [gid_cap], "the capacity card's tombstone")
        self.assertTrue(nd["nodeComplete"])
        self.assertEqual(store["status"].get(gid), "completed")
        self.assertNotIn(gid_cap, store["status"])

    def test_a_capacity_card_the_user_followed_up_on_is_left_alone(self):
        # the user acted on the card mid-turn: their gesture outranks the bookkeeping — the refusal
        # files fresh and the capacity card keeps its state (two cards, no move on the user's card)
        gid_cap = self._capacity()
        store = jd.load_goals(self.RSID)
        nd = store["nodes"][gid_cap]
        jd.record_verdict(store, nd, "user", "reopen", self.T + 1, msg=True)
        jd.rollup_status(store, True)
        jd.save_goals(self.RSID, store)
        gid = self._mint(capacity_gids=[gid_cap], ev_t=self.T + 2)
        self.assertTrue(gid)
        store = jd.load_goals(self.RSID)
        self.assertEqual(sorted(store["nodes"]), sorted([gid_cap, gid]))
        self.assertEqual(store["nodes"][gid_cap]["text"], "Model changed automatically: Fable 5 → Opus 5")
        self.assertNotEqual(store["status"].get(gid_cap), "completed", "the user's reopen still stands")

    def test_an_unknown_capacity_id_files_the_refusal_fresh(self):
        gid = self._mint(capacity_gids=[self.RSID + ":g99"])
        self.assertTrue(gid)
        self.assertEqual(list(jd.load_goals(self.RSID)["nodes"]), [gid])

    def test_a_multi_hop_chain_folds_every_hops_capacity_card(self):
        # origin -> hop 1 -> hop 2: the learn minted a card per hop; the final frame (origin -> hop 2)
        # folds the cards that chain between its two models, and only those
        g1 = jd.mint_fallback_card(self.RSID, "Fable 5", "Opus 5", ev_t=self.T)
        g2 = jd.mint_fallback_card(self.RSID, "Opus 5", "Sonnet 5", ev_t=self.T + 1)
        g_other = jd.mint_fallback_card(self.RSID, "Opus 5", "Haiku 4.5", ev_t=self.T + 1)   # not in the chain
        gid = self._mint(to="Sonnet 5", capacity_gids=[g1, g2, g_other], ev_t=self.T + 2)
        store = jd.load_goals(self.RSID)
        self.assertEqual(sorted(store["nodes"]), sorted([gid, g_other]))
        self.assertEqual(sorted(m["id"] for m in store["nodes"][gid]["mergedFrom"]), sorted([g1, g2]))

    def test_a_capacity_mint_after_the_refusal_card_stands_down(self):
        # the other order (a CLI that streams the notice first): the swap is already filed with its cause
        self.assertTrue(self._mint())
        self.assertIsNone(jd.mint_fallback_card(self.RSID, "Fable 5", "Opus 5", ev_t=self.T + 1))
        self.assertEqual(len(jd.load_goals(self.RSID)["nodes"]), 1)

    def test_a_cleared_capacity_card_is_left_alone(self):
        # the user crossed the capacity card off; the refusal is filed fresh, never folded onto it
        gid_cap = self._capacity()
        store = jd.load_goals(self.RSID)
        nd = store["nodes"][gid_cap]
        jd.record_verdict(store, nd, "user", "clear", self.T + 1)
        jd.rollup_status(store, True)
        jd.save_goals(self.RSID, store)
        gid = self._mint(capacity_gids=[gid_cap], ev_t=self.T + 2)
        self.assertTrue(gid)
        self.assertNotEqual(gid, gid_cap)
        self.assertIn(gid_cap, jd.load_goals(self.RSID)["nodes"], "the cleared card stays as the user left it")


class KernelWiring(unittest.TestCase):
    def test_the_kernel_wires_the_hook_to_the_judge_store_at_boot(self):
        ksrc = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("type(_sdk_backend).on_model_refusal_fallback = staticmethod(", ksrc)
        i = ksrc.index("on_model_refusal_fallback = staticmethod(")
        j = ksrc.index("jd.mint_refusal_fallback_card(", i)
        self.assertLess(i, j, "the hook's body is the judge mint")
        self.assertIn("capacity_gids=", ksrc[j:j + 200], "this turn's capacity cards flow through")
        self.assertIn("episode=", ksrc[j:j + 200], "and the episode key")


if __name__ == "__main__":
    unittest.main()
