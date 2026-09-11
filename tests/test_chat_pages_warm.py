#!/usr/bin/env python3
"""Warming the chat's pages cache for the feed's cards (2026-09-11; the user 2026-09-10: a click on a distilled summary
far in the past took long to load, and they asked whether the transcript behind the active cards could be cached). The
pusher, after its send stage, with a board client and a proto-2 chat client connected, renders into the pages cache the
pages the cards' anchors would ask for (what loadAround serves), so the click lands from the cache. Pinned: the anchors
are the summary's own click targets first (a completed card's too), then the active columns' cards' heads and open rows
(a done row and a handoff row are not); every anchor's pages are probed, so an unchanged board settles to a probe and an
evicted page is warmed again; the render budget per cycle is half the cache and the rest waits, counted; the warm stands
down while the pusher is over its budget; a pre-floor page's key survives a turn; the counts are the warm's own; a warmed
window is a cache hit for loadAround with no render. Synthetic transcript only."""
import json
import os
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import test_chat_pages as P                                   # noqa: E402  the render-floor harness and its kernel load

km, em, jd = P.km, P.em, P.jd
SID, NOW = P.SID, P.NOW


class WarmTheCards(P.Harness):
    def setUp(self):
        super().setUp()
        km._PAGE_STATS.update(warmed=0, warmMs=0.0, warmCycles=0, warmSkipped=0, warmPending=0)   # the warm counters, per test
        km._WARM_MEMO.update(anchors=(), keys=frozenset(), sigs={})
        km._PERF_STATS.pusher["cycle_ms_last"] = 20.0

    def _restored(self, turns=200, compact_every=25):
        """The whole build's events (a cold parse), then the document, then the pusher's floor'd build (its floor set)."""
        recs = P.transcript(NOW - 86400, turns=turns, compact_every=compact_every)
        self.write(recs)
        whole = self.whole()
        self.document()
        m = self.restored()
        self.assertGreater(m["floor"], 0)
        return whole, m

    @staticmethod
    def _card(anchors, column="working", done=False, item="n0", summary=None, paras=None):
        rows = [{"id": "%s-%d" % (item, i), "kind": "ask", "status": "done" if (done and i) else "open", "anchorUuid": u, "promptAnchorUuid": None}
                for i, u in enumerate(anchors)]
        return {"itemId": item, "sid": SID, "column": column, "tree": rows, "summaryAnchorUuid": summary, "summaryAnchorsPara": paras}

    def _feed(self, *cards):
        return {"type": "feed", "asks": list(cards)}

    def _hit(self, uuid):
        """loadAround on the anchor, asserting the window came from the cache: no render."""
        misses = km._PAGE_STATS["misses"]
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": uuid}, NOW)
        self.assertIn(uuid, [e["uuid"] for e in r["events"]])
        return km._PAGE_STATS["misses"] == misses

    def test_the_summarys_own_targets_are_warmed_first_a_completed_cards_too_and_the_click_hits(self):
        whole, m = self._restored()
        deep, para, row = whole[5]["uuid"], whole[60]["uuid"], whole[120]["uuid"]
        feed = self._feed(self._card([], column="completed", item="c", summary=deep, paras=[{"u": para, "q": "quoted"}, None]),
                          self._card([row], column="working", item="w"))
        self.assertEqual(km._card_anchors(feed), [(SID, deep), (SID, para), (SID, row)],
                         "the completed card's summary line and paragraph targets first, then the active card's row")
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0, "pages rendered for the anchors' windows")
        st = dict(km._PAGE_STATS)
        self.assertEqual((st["warmed"], st["warmCycles"], st["warmSkipped"], st["warmPending"]), (n, 1, 0, 0))
        for u in (deep, para, row):
            self.assertTrue(self._hit(u), "the click's window came from the cache: no render (%s)" % u)
        self.assertGreater(km._PAGE_STATS["hits"], 0)

    def test_the_probe_costs_nothing_while_resident_and_warms_again_after_an_eviction(self):
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"], whole[60]["uuid"]]))
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0)
        self.assertEqual(km._WARM_MEMO["anchors"], tuple(km._card_anchors(feed)), "the set is fully resident: remembered")
        self.assertEqual(len(km._WARM_MEMO["keys"]), n)
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "every page resident: the probe renders nothing")
        self.assertEqual(km._PAGE_STATS["warmCycles"], 2, "…and the cycle is counted")
        with km._page_lock:                                           # a reader's scrolling evicted the pages (or a floor flip re-keyed them)
            km._PAGE_CACHE.clear(); km._PAGE_STATS["pages"] = 0; km._PAGE_STATS["bytes"] = 0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), n, "the same pages are rendered again")
        self.assertEqual(km._PAGE_STATS["warmed"], 2 * n)
        with km._page_lock:
            k0 = next(iter(km._WARM_MEMO["keys"])); km._PAGE_CACHE.pop(k0); km._PAGE_STATS["pages"] -= 1
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 1, "one page evicted: the remembered set is probed and that page alone rendered")
        km._RENDER_FLOOR[SID] = m["floor"] - 16                       # a floor move re-keys the pages: warmed again
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)

    def test_the_warm_set_is_bounded_to_half_the_cache_and_settles_and_a_board_change_admits_the_rest(self):
        """The warming review (item 3): an unbounded set over the cache re-rendered itself every cycle for the kernel's life
        (23 windows: 14 to 16 renders on each of eight cycles, 92 evictions). The SET is bounded: anchors past it wait."""
        whole, m = self._restored(turns=600, compact_every=150)
        floor = m["floor"]
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        anchors = []                                                  # one per two pages: disjoint windows of two pages each
        for p in range(0, floor - 2 * km.PAGE_TURNS, 2 * km.PAGE_TURNS):
            j = p + km.PAGE_TURNS // 2                                # a turn in the page's second half: its window is [p, p+32)
            anchors.append(next(a["uuid"] for a in turns[j]["atoms"] if a.get("uuid")))
        self.assertGreater(2 * len(anchors), km.WARM_PAGES_MAX, "more pages than the set's bound")
        feed = self._feed(self._card(anchors))
        n1 = km._warm_history_pages(feed, NOW, {})
        admitted = km.WARM_PAGES_MAX // 2                             # windows of two pages each
        self.assertEqual(n1, km.WARM_PAGES_MAX, "one cycle renders the set's bound")
        self.assertEqual(km._PAGE_STATS["warmPending"], len(anchors) - admitted, "the anchors past the bound wait")
        ev0 = km._PAGE_STATS["evictions"]
        for _ in range(4):                                            # the cycles after: the set settles, nothing re-rendered
            self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "a bounded set settles: no render")
        self.assertEqual(km._PAGE_STATS["evictions"], ev0, "…and evicts nothing")
        self.assertEqual(km._PAGE_STATS["warmCycles"], 5)
        self.assertTrue(self._hit(anchors[0]), "the first anchor's window stays resident")
        self.assertFalse(self._hit(anchors[-1]), "the last anchor's waits for a board change")
        # a board change: the first four cards left; their windows' place admits the next four
        feed2 = self._feed(self._card(anchors[4:]))
        n2 = km._warm_history_pages(feed2, NOW, {})
        self.assertEqual(n2, 2 * 4, "the next four windows admitted and rendered (the click above rendered the LAST anchor's, still waiting)")
        self.assertEqual(km._PAGE_STATS["warmPending"], len(anchors) - 4 - admitted)
        self.assertEqual(km.WARM_PAGES_MAX, km._PAGE_CACHE_MAX // 2)

    def test_done_rows_handoffs_completed_rows_and_tail_anchors_are_not_warmed(self):
        whole, m = self._restored()
        deep = whole[5]["uuid"]
        tail_anchor = m["events"][-1]["uuid"]                         # in the resident tail: nothing to warm
        feed = {"type": "feed", "asks": [
            {"itemId": "c1", "sid": SID, "column": "completed", "tree": [{"id": "a", "kind": "ask", "status": "done", "anchorUuid": deep}]},
            {"itemId": "c2", "sid": SID, "column": "working", "tree": [{"id": "b", "kind": "ask", "status": "open", "anchorUuid": tail_anchor},
                                                                        {"id": "c", "kind": "ask", "status": "done", "anchorUuid": deep},
                                                                        {"id": "d", "kind": "handoff", "status": "open", "anchorUuid": deep}]}]}
        self.assertEqual(km._card_anchors(feed), [(SID, tail_anchor)], "a completed card's rows, a done row and a handoff row do not count")
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "a tail anchor is resident already")
        self.assertEqual(km._warm_history_pages({"type": "feed", "asks": []}, NOW, {}), 0, "no anchors: no cycle")
        self.assertEqual(km._PAGE_STATS["warmCycles"], 1)

    def test_a_slow_pusher_stands_down_and_the_next_cycle_in_budget_warms(self):
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"]]))
        km._PERF_STATS.pusher["cycle_ms_last"] = 5000.0              # the pusher is behind
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertEqual((km._PAGE_STATS["warmSkipped"], km._PAGE_STATS["warmCycles"]), (1, 0), "counted, nothing rendered")
        km._PERF_STATS.pusher["cycle_ms_last"] = 20.0
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0, "the next cycle in budget warms")

    def test_a_pre_floor_pages_key_survives_a_turn_and_the_render_count_is_the_calls_own(self):
        """A page rendered from the pre-cut prefix cannot change while a turn streams: its key reads none of the live tail."""
        whole, m = self._restored()
        st = {}
        page = km._chat_history_page(SID, 0, km.PAGE_TURNS, NOW, stats=st)
        self.assertEqual(st, {"rendered": 1})
        self.assertEqual(P._strip(page), whole[:len(page)], "the first page is the whole's first slice")
        km._chat_history_page(SID, 0, km.PAGE_TURNS, NOW, stats=st)
        self.assertEqual(st, {"rendered": 1}, "a hit is not a render")
        recs = P.transcript(NOW - 86400, turns=200, compact_every=25)
        last = next(r for r in reversed(recs) if r.get("uuid"))
        t_last = em.parse_z(last["timestamp"])
        more = [P.G.uline(t_last + 60, "one more step", "u_more_1", last["uuid"]),
                P.G.aline(t_last + 90, "done with the step", "a_more_1", "u_more_1", stop="end_turn")]
        self.write(recs + more)                                       # a turn streamed: the transcript's stat moved
        km._live_scope.chat_floor0 = False
        try:
            m2 = km.build_session(SID, NOW + 100, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(len(m2["events"]), len(m["events"]) + 2, "the tail grew")
        misses = km._PAGE_STATS["misses"]
        self.assertEqual(P._strip(km._chat_history_page(SID, 0, km.PAGE_TURNS, NOW + 100, stats=st)), P._strip(page))
        self.assertEqual((km._PAGE_STATS["misses"], st["rendered"]), (misses, 1), "the page's key survived the turn: a hit")

    def test_a_pages_key_reads_the_regs_fork_value_not_the_file(self):
        """The warming review (item 2): the queue and echo mirrors rewrite STATE/sdk/<sid>.json on every send, so a key on
        the file's identity re-keyed every page of the session the user was prompting."""
        whole, m = self._restored()
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.write_text(json.dumps({"forkedFrom": None, "queue": []}))
        k1 = km._page_sig(self.rows[0], SID, NOW)
        reg.write_text(json.dumps({"forkedFrom": None, "queue": [{"text": "a send"}], "echoes": [1]}))   # a send: the mirror rewrote
        self.assertEqual(km._page_sig(self.rows[0], SID, NOW), k1, "the same fork value: the same key")
        reg.write_text(json.dumps({"forkedFrom": {"sid": "22222222-3333-4444-5555-666666666666", "uuid": whole[3]["uuid"]}}))
        self.assertNotEqual(km._page_sig(self.rows[0], SID, NOW), k1, "a fork lineage: another key (the branch marker moves)")

    def test_a_cycle_with_no_floor_never_settles_and_the_floors_return_warms(self):
        """Round 3, A: with an index client connected every tab's floor is 0, so no anchor resolves; the memo must not call the
        empty set settled, or the floor's return would find the warm asleep and the click miss."""
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"], whole[60]["uuid"]]))
        km._RENDER_FLOOR[SID] = 0                                     # a proto-1 client holds every tab at turn 0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "nothing to warm below a floor of 0")
        self.assertEqual(km._WARM_MEMO["anchors"], (), "…and the empty set is not remembered as settled")
        self.assertEqual(km._PAGE_STATS["warmPending"], 0)
        km._RENDER_FLOOR[SID] = m["floor"]                            # it left: the floor is back
        n = km._warm_history_pages(feed, NOW, {})
        self.assertGreater(n, 0, "the next cycle warms the windows")
        self.assertTrue(self._hit(whole[5]["uuid"]))
        # a second session with no row beside a resolved one: the set is unresolved, not settled
        feed2 = {"type": "feed", "asks": feed["asks"] + [{"itemId": "z", "sid": "99999999-0000-4000-8000-000000000099", "column": "working",
                                                           "tree": [{"id": "r", "kind": "ask", "status": "open", "anchorUuid": "u-nowhere"}]}]}
        km._warm_history_pages(feed2, NOW, {})
        self.assertEqual(km._WARM_MEMO["anchors"], (), "an anchor with no session row leaves the set unresolved")

    def test_the_set_is_bounded_in_bytes_too_and_a_readers_page_beside_it_survives(self):
        """Round 3, B: the cache evicts on bytes as well as count; a set bounded by count alone re-rendered itself every cycle
        once its pages passed the byte bound."""
        whole, m = self._restored(turns=600, compact_every=150)
        floor = m["floor"]
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        anchors = [next(a["uuid"] for a in turns[p + km.PAGE_TURNS // 2]["atoms"] if a.get("uuid"))
                   for p in range(0, floor - 2 * km.PAGE_TURNS, 2 * km.PAGE_TURNS)]
        reader = km._chat_history_page(SID, floor - km.PAGE_TURNS, floor, NOW)        # a page the reader scrolled into, beside the set
        self.assertTrue(reader)
        with km._page_lock:
            page_bytes = max(b for _, b in km._PAGE_CACHE.values())
        saved = km._PAGE_CACHE_BYTES
        km._PAGE_CACHE_BYTES = page_bytes * 9                        # nine pages of room: the set may take half (four to five)
        try:
            feed = self._feed(self._card(anchors))
            n1 = km._warm_history_pages(feed, NOW, {})
            self.assertGreater(n1, 0); self.assertLess(n1, km.WARM_PAGES_MAX, "the byte bound closed the set before the page bound")
            self.assertGreater(km._PAGE_STATS["warmPending"], 0)
            ev0 = km._PAGE_STATS["evictions"]
            for _ in range(4):
                self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "the set settles under the byte bound")
            self.assertEqual(km._PAGE_STATS["evictions"], ev0, "…and evicts nothing")
            misses = km._PAGE_STATS["misses"]
            km._chat_history_page(SID, floor - km.PAGE_TURNS, floor, NOW)
            self.assertEqual(km._PAGE_STATS["misses"], misses, "the reader's page beside the set survived the warm")
            self.assertTrue(self._hit(anchors[0]))
        finally:
            km._PAGE_CACHE_BYTES = saved

    def test_an_over_budget_pusher_stands_down_before_the_probe(self):
        """Round 3, C: the stand-down comes first, so a slow pusher pays not even the settled probe; the probe's time is the warm's."""
        whole, m = self._restored()
        feed = self._feed(self._card([whole[5]["uuid"]]))
        self.assertGreater(km._warm_history_pages(feed, NOW, {}), 0)
        cycles, ms = km._PAGE_STATS["warmCycles"], km._PAGE_STATS["warmMs"]
        km._PERF_STATS.pusher["cycle_ms_last"] = 5000.0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0)
        self.assertEqual((km._PAGE_STATS["warmSkipped"], km._PAGE_STATS["warmCycles"], km._PAGE_STATS["warmMs"]), (1, cycles, ms),
                         "stood down before the probe: no cycle, no time")
        km._PERF_STATS.pusher["cycle_ms_last"] = 20.0
        self.assertEqual(km._warm_history_pages(feed, NOW, {}), 0, "the remembered set: a probe")
        self.assertEqual(km._PAGE_STATS["warmCycles"], cycles + 1); self.assertGreater(km._PAGE_STATS["warmMs"], ms, "…counted")
        src = open(os.path.join(P.BIN, "romp-kernel")).read()
        fn = src[src.index("def _warm_history_pages("):src.index("def _turn_of_uuid(")]
        self.assertLess(fn.index("WARM_SKIP_MS:"), fn.index('_WARM_MEMO["anchors"]:'), "the stand-down precedes the probe")

    def test_the_window_is_two_aligned_pages_around_the_anchor(self):
        w = km._window_turns
        self.assertEqual(w(3, 400), (0, 32), "the head's page and the next")
        self.assertEqual(w(20, 400), (0, 32), "the first half of a page: the page before it")
        self.assertEqual(w(24, 400), (16, 48), "the second half: the page after it")
        self.assertEqual(w(390, 400), (368, 400), "clipped at the floor")
        self.assertEqual(w(8, 12), (0, 12))

    def test_the_wiring(self):
        src = open(os.path.join(P.BIN, "romp-kernel")).read()
        send = src.index('_PERF_STATS.stage("push.send", time.monotonic() - _t_stage)')
        block = src[send:src.index("def _broadcast_restarting", send)]
        self.assertIn("_warm_history_pages(_fs, now, live_map)", block, "the pusher warms AFTER its send stage")
        self.assertIn('if any(c["app"] in ("feed", "fleet") for c in targets) and _fs and not connect and any(c.get("proto") == 2 for c in chat_clients):', block,
                      "only with a board client (feed or fleet, never the chat alone) among the targets and a proto-2 chat client connected")
        self.assertIn('_PERF_STATS.stage("push.warm", time.monotonic() - _t_stage)', block, "its own stage key")
        self.assertNotIn("_warm_history_pages(feed_src", src, "the hook before the ledgers and the timeline is gone")
        self.assertIn("lo, hi = _window_turns(j, floor)", src, "loadAround's window is the warm's")
        self.assertEqual(sorted(k for k in km._PAGE_STATS if k.startswith("warm")), ["warmCycles", "warmMs", "warmPending", "warmSkipped", "warmed"])
        self.assertEqual(km.WARM_ANCHORS_MAX, 32)
        self.assertEqual(sorted(km._WARM_MEMO), ["anchors", "keys", "sigs"], "the remembered resident set: the anchor list, its page keys, the signatures")


if __name__ == "__main__":
    unittest.main()
