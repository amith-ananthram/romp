#!/usr/bin/env python3
"""The timeline's bars and the feed cross the wire as DELTAS (2026-09-03).

Both slots used to be sent whole on every change — 2.95 MB and 0.86 MB on a seventeen-session board,
where something changes every few seconds — so a dashboard on a forwarded or tunnelled link streamed
about half a megabyte a second of mostly-unchanged JSON and its panes starved. A pane that connects with
?delta=1 (the shim does) now receives, per change, only the collection entries that changed, keyed the
way each collection is keyed; the shim reassembles the full message and hands the bundle exactly what it
received before. These tests drive the kernel encoder against a fake client and check the reassembly two
ways: with a Python mirror of the shim's decoder, and — when node is installed — with the shim's own
JavaScript, lifted verbatim from the served page. Synthetic payloads only.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import io
import threading
from contextlib import redirect_stderr
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_viewdelta", os.path.join(BIN, "romp-kernel"))

S1 = "11111111-2222-3333-4444-aaaaaaaaaaa1"
S2 = "11111111-2222-3333-4444-aaaaaaaaaaa2"
S3 = "11111111-2222-3333-4444-aaaaaaaaaaa3"


def _bars(turns, judging, messages, now=1000, warming=False):
    # judging rides per lane, compact (T278c); the fixtures here write the builder's flat entries and the same
    # compaction the builder applies turns them into the wire shape
    if isinstance(judging, list):
        judging = km._compact_judging(judging)
    return {"type": "bars", "turns": turns, "judging": judging, "messages": messages, "now": now, "warming": warming}


def _feed(asks, now=1000, **rest):
    d = {"type": "feed", "asks": asks, "now": now, "sessions": [{"sid": S1, "name": "web", "color": None}],
         "order": [S1], "working": [], "awaiting": []}
    d.update(rest)
    return d


class _Client(dict):
    """A delta-capable client whose frames are captured rather than written."""

    def __init__(self):
        super().__init__(app="timeline", wid="w1", alive=True, delta=True, sock=object())
        self.frames = []
        self["send"] = lambda s: self.frames.append(json.loads(s))


KINDS = {"bars": {"turns": "dictlist:id", "judging": "dictlist:k", "messages": "byid"}, "feed": {"asks": "byid:itemId"}}
SEP = "\u001f"


def _assemble(kind, order, items):
    if kind == "dict":
        return {kk: items[kk] for kk in order if kk in items}
    if kind.startswith("dictlist:"):
        d = {}
        for kk in order:
            if kk not in items:
                continue
            dk, _, rest = kk.partition(SEP)
            if rest == "" and not isinstance(items[kk], list):
                d[dk] = items[kk]; continue
            if rest == "":
                d[dk] = items[kk]; continue
            d.setdefault(dk, []).append(items[kk])
        return d
    return [items[kk] for kk in order if kk in items]


def _py_apply(last, d):
    """A faithful Python mirror of the shim's applyDelta/buildMaps/assemble — the reference the JS is held to."""
    if last is None or last["rev"] != d["base"]:
        return None
    m = dict(last["msg"])
    if d.get("rest") is not None and d.get("restAll"):
        for k in [k for k in m if k not in KINDS[d["slot"]] and k not in d["rest"]]:
            del m[k]                                    # a full remainder retires the keys it no longer carries
    for k, v in (d.get("rest") or {}).items():
        m[k] = v
    for name, c in (d.get("coll") or {}).items():
        kind = KINDS[d["slot"]].get(name)
        mp = last["maps"].get(name) or {"order": [], "items": {}}
        items = dict(mp["items"]); order = list(mp["order"])
        for kk in c.get("del") or []:
            items.pop(kk, None)
        new = [kk for kk in (c.get("set") or {}) if kk not in items]
        for kk, v in (c.get("set") or {}).items():
            items[kk] = v
        order += km._js_key_order(new)                  # JS enumerates integer-like keys first, ascending
        order = list(c["order"]) if c.get("order") else [kk for kk in order if kk in items]
        last["maps"][name] = {"order": order, "items": items}
        m[name] = _assemble(kind, order, items)
    last["rev"] = d["rev"]; last["msg"] = m
    return m


def _py_maps(msg, keys=None):
    """The shim's buildMaps (T278c): the keys DERIVED from the payload, per kind, exactly as the kernel's
    _delta_split mints them — a dictlist item is lane + SEP + its id field, a byid item its id field, an item
    without one (or a duplicate) takes the positional "#n", an empty or non-list lane is one entry under its
    bare prefix. `keys` is accepted and ignored: no full carries a key list any more."""
    maps = {}
    for name, kind in KINDS[msg["type"]].items():
        v = msg.get(name); order = []; items = {}

        def put(kk, val, pre=""):
            if kk is None or kk in items:
                n = len(order)
                while True:
                    kk = pre + "#%d" % n
                    if kk not in items:
                        break
                    n += 1
            items[kk] = val; order.append(kk)

        def key_of(it, pre=""):
            if not isinstance(it, dict):
                return None
            f = "id" if kind == "byid" else kind.split(":", 1)[1]
            x = it.get(f)
            return None if x is None or x == "" else pre + str(x)
        if kind == "dict":
            if isinstance(v, dict):
                for dk, val in v.items():
                    put(str(dk), val)
        elif kind.startswith("dictlist:"):
            if isinstance(v, dict):
                for lane, lst in v.items():
                    pre = str(lane) + SEP
                    if not isinstance(lst, list) or not lst:
                        put(pre, lst)
                        continue
                    for it in lst:
                        put(key_of(it, pre), it, pre)
        elif isinstance(v, list):
            for it in v:
                put(key_of(it), it)
        maps[name] = {"order": order, "items": items}
    return maps


class _Stream:
    """Drive the kernel encoder for one client and mirror the shim: every captured frame is applied."""

    def __init__(self, ftype):
        self.ftype, self.c, self.last, self.fulls, self.deltas = ftype, _Client(), None, 0, 0

    def push(self, payload):
        pre = json.dumps(payload); sig = km._dedup_sig(payload, pre)
        n0 = len(self.c.frames)
        km._send_slot(self.c, self.ftype, payload, pre, sig)
        for fr in self.c.frames[n0:]:
            if fr.get("type") == "delta":
                self.deltas += 1
                out = _py_apply(self.last, fr)
                assert out is not None, "the mirror rejected a delta the kernel sent: %r" % fr
            else:
                self.fulls += 1
                assert "_keys" not in fr, "no full carries a key list (T278c)"
                self.last = {"rev": 0, "msg": dict(fr), "maps": _py_maps(fr)}    # the client keys every full itself
        return self.c.frames[n0:]

    @property
    def held(self):
        return self.last["msg"] if self.last else None


class BarsDeltas(unittest.TestCase):
    def setUp(self):
        km._delta_parts_cache.clear()
        self.t0 = time.time()
        # the size guard sends a delta that is not smaller than the whole as the whole; these synthetic payloads are
        # tiny, so the structural tests switch it off — test_d covers the guard with the real threshold
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def _turn(self, sid, n):
        return [{"id": "seg-%s-%d" % (sid[-1], i), "t": self.t0 - 60 * i, "end": self.t0 - 60 * i + 30, "open": False} for i in range(n)]

    def test_a_first_send_is_full_then_only_the_changed_lane_crosses(self):
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 3), S2: self._turn(S2, 2)},
                   [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}, {"sid": S2, "judge": "planner", "t": 3, "t1": 4}],
                   [{"id": "m1", "from": "web", "to": "api", "sent": 1}])
        frames = st.push(p1)
        self.assertEqual([f["type"] for f in frames], ["bars"], "a client holding nothing gets the whole slot")
        self.assertEqual(st.held, p1)
        p2 = json.loads(json.dumps(p1)); p2["turns"][S2] = self._turn(S2, 3); p2["now"] = 1005
        frames = st.push(p2)
        self.assertEqual([f["type"] for f in frames], ["delta"])
        d = frames[0]
        self.assertEqual(set(d["coll"]), {"turns"}, "only the lane that moved is in the frame")
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S2 + SEP + "seg-2-2"}, "one BAR crosses, not the lane")
        self.assertNotIn("del", d["coll"]["turns"]); self.assertNotIn("order", d["coll"]["turns"], "an appended bar needs no order")
        self.assertEqual(d.get("rest"), {"now": 1005}, "the clock rides, the unchanged remainder does not")
        self.assertEqual(st.held, p2, "the reassembled message is the new payload, exactly")
        self.assertLess(len(json.dumps(d)), len(json.dumps(p2)) / 2)

    def test_b_removed_added_and_reordered_entries(self):
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1), S2: self._turn(S2, 1), S3: self._turn(S3, 1)},
                   [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}, {"sid": S1, "judge": "planner", "t": 5, "t1": 6},
                    {"sid": S3, "judge": "closer", "t": 7, "t1": 8}],
                   [{"id": "m1", "from": "a", "to": "b", "sent": 1}, {"id": "m2", "from": "b", "to": "a", "sent": 2}])
        st.push(p1)
        # S2 gone, a new session S3 judging group grows, messages reversed and one added
        p2 = _bars({S1: self._turn(S1, 1), S3: self._turn(S3, 2)},
                   [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}, {"sid": S1, "judge": "planner", "t": 5, "t1": 6},
                    {"sid": S3, "judge": "closer", "t": 7, "t1": 8}, {"sid": S3, "judge": "courier", "t": 9, "t1": 10}],
                   [{"id": "m3", "from": "c", "to": "a", "sent": 3}, {"id": "m2", "from": "b", "to": "a", "sent": 2},
                    {"id": "m1", "from": "a", "to": "b", "sent": 1}], now=1010)
        frames = st.push(p2)
        self.assertEqual(frames[0]["type"], "delta")
        d = frames[0]
        self.assertEqual(d["coll"]["turns"].get("del"), [S2 + SEP + "seg-2-0"], "a removed lane deletes its bars")
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S3 + SEP + "seg-3-1"}, "a grown lane adds only its new bar")
        self.assertEqual(set(d["coll"]["judging"]["set"]), {S3 + SEP + "9" + SEP + "courier"}, "a judge call is one keyed item: lane, t, judge (T278c)")
        self.assertEqual(d["coll"]["messages"]["order"], ["m3", "m2", "m1"], "a reorder ships the key order")
        self.assertEqual(st.held, p2)
        # and a delta stream keeps going: a third push changes nothing but the clock
        p3 = json.loads(json.dumps(p2)); p3["now"] = 1011
        st.c.setdefault("dstate", {})["bars"]["at"] = time.time() - km._DEDUP_REPOST_S - 1   # the repost is due
        frames = st.push(p3)
        self.assertEqual(frames[0]["type"], "delta"); self.assertEqual(frames[0]["coll"], {})
        self.assertEqual(frames[0]["rest"], {"now": 1011})
        self.assertEqual(st.held, p3)

    def test_c_an_unchanged_payload_sends_nothing_until_the_repost_is_due(self):
        st = _Stream("bars")
        p = _bars({S1: self._turn(S1, 2)}, [], [])
        st.push(p)
        self.assertEqual(st.push(p), [], "nothing moved, nothing sent")
        self.assertEqual(st.push(dict(p, now=1002)), [], "…and the clock alone is not a change inside the repost window")
        st.c["dstate"]["bars"]["at"] -= km._DEDUP_REPOST_S + 1
        fr = st.push(dict(p, now=1003))
        self.assertEqual(len(fr), 1); self.assertEqual(fr[0]["type"], "delta"); self.assertEqual(fr[0]["coll"], {})

    def test_d_a_delta_not_worth_its_bytes_is_sent_whole_and_rebases(self):
        km._DELTA_MAX_FRACTION = self._frac                    # the real threshold, for this test alone
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], []))
        big = _bars({S1: self._turn(S1, 40), S2: self._turn(S2, 40), S3: self._turn(S3, 40)}, [], [], now=1020)
        frames = st.push(big)
        self.assertEqual([f["type"] for f in frames], ["bars"], "a near-total change crosses whole")
        self.assertEqual(st.held, big)
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 0, "…and the stream re-bases from it")
        small = json.loads(json.dumps(big)); small["turns"][S1] = self._turn(S1, 41); small["now"] = 1021
        self.assertEqual(st.push(small)[0]["type"], "delta")

    def test_e_a_client_without_delta_support_gets_whole_payloads_exactly_as_before(self):
        c = _Client(); c.pop("delta")
        p1 = _bars({S1: self._turn(S1, 1)}, [], []); pre = json.dumps(p1); sig = km._dedup_sig(p1, pre)
        km._send_slot(c, "bars", p1, pre, sig)
        p2 = _bars({S1: self._turn(S1, 2)}, [], [], now=1005); pre2 = json.dumps(p2); sig2 = km._dedup_sig(p2, pre2)
        km._send_slot(c, "bars", p2, pre2, sig2)
        self.assertEqual([f["type"] for f in c.frames], ["bars", "bars"])
        self.assertEqual(c.frames[1], p2)

    def test_f_needslot_resets_the_stream_to_a_full_frame(self):
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1)}, [], []); st.push(p1)
        p2 = _bars({S1: self._turn(S1, 2)}, [], [], now=1005); st.push(p2)
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 1)
        # the shim could not apply a delta → the socket handler flags the slot for the PUSHER (not its own
        # thread: two threads over one client's held state would rebase a full the dedup then swallowed)
        st.c.setdefault("resync", set()).add("bars")
        p3 = _bars({S1: self._turn(S1, 3)}, [], [], now=1010)
        fr = st.push(p3)
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertNotIn("_keys", fr[0], "a full carries no key list (T278c)")
        self.assertEqual(st.held, p3); self.assertEqual(st.c["resync"], set())
        src = open(km.__file__, encoding="utf-8").read()
        h = src[src.index('msg.get("type") == "needSlot"'):]
        h = h[:h.index("return")]
        self.assertIn('client.setdefault("resync", set()).add(', h, "the handler flags the slot…")
        self.assertIn("_pusher_wake.set()", h, "…and wakes the pusher, which sends on its own thread")
        self.assertNotIn("_push_one", h)

    def test_i_a_top_level_key_that_leaves_the_payload_leaves_the_client(self):
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], [], warming=True))
        p2 = _bars({S1: self._turn(S1, 1)}, [], [], now=1005); del p2["warming"]
        fr = st.push(p2)
        self.assertEqual(fr[0].get("restAll"), 1); self.assertNotIn("warming", fr[0]["rest"])
        self.assertEqual(st.held, p2, "the remainder is replaced, not merged: a dropped key is gone")

    def test_j_an_empty_id_and_a_positional_key_spelling_a_real_id_stay_exact(self):
        st = _Stream("bars")
        lane = [{"id": "", "t": 1}, {"id": "b2", "t": 2}]
        msgs = [{"id": "#2", "x": 0}, {"x": 1}, {"x": 2}]
        p1 = _bars({S1: lane}, [], msgs); st.push(p1)
        self.assertEqual(st.held, p1)
        keys = st.c["dstate"]["bars"]["order"]
        self.assertEqual(keys["turns"], [S1 + SEP + "#0", S1 + SEP + "b2"], "an empty id is positional, in its lane")
        self.assertEqual(keys["messages"], ["#2", "#1", "#3"], "the positional key steps past the real '#2'")
        p2 = _bars({S1: [{"id": "", "t": 1}, {"id": "b2", "t": 3}]}, [], [{"id": "#2", "x": 0}, {"x": 1}, {"x": 5}], now=1005)
        st.push(p2)
        self.assertEqual(st.held, p2)

    def test_k_javascript_index_keys_are_ascii_and_bounded(self):
        odd = ["\u00b2", "\u0661", "\uff11\uff12", "9" * 5000, "10", "9", "x", "4294967295", "4294967294"]
        self.assertEqual(km._js_key_order(odd), ["9", "10", "4294967294", "\u00b2", "\u0661", "\uff11\uff12", "9" * 5000, "x", "4294967295"])

    def test_l_a_failing_delta_never_takes_the_pusher_down(self):
        import io, contextlib
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1)}, [], [])
        real = km._delta_parts
        def boom(ftype, payload): raise RuntimeError("synthetic")
        km._delta_parts = boom
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                fr = st.push(p1)
        finally:
            km._delta_parts = real
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertNotIn("_keys", fr[0])
        self.assertIn("view-delta bars", err.getvalue()); self.assertIn("synthetic", err.getvalue())
        self.assertNotIn("bars", st.c.get("dstate", {}), "the kernel holds no base for it")
        self.assertIsNotNone(st.last, "the client keys any full it receives (T278c); the kernel decides whether a base is held")
        fr = st.push(_bars({S1: self._turn(S1, 2)}, [], [], now=1005))
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertIn("bars", st.c.get("dstate", {}), "the stream starts afresh: a full, and the kernel holds it")

    def test_m_after_a_keyless_whole_frame_the_keyed_full_still_goes_even_for_an_unchanged_payload(self):
        """The failure path sends the whole payload without keys, filling the dedup slot with its signature. If
        the next cycle's keyed full were deduped, the kernel would hold state for a client holding nothing, and
        its next delta would be refused (review 2026-09-03). The keyed full always goes once."""
        import io, contextlib
        st = _Stream("bars")
        p1 = _bars({S1: self._turn(S1, 1)}, [], [])
        real = km._delta_parts
        km._delta_parts = lambda ftype, payload: (_ for _ in ()).throw(RuntimeError("synthetic"))
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                st.push(p1)
        finally:
            km._delta_parts = real
        self.assertNotIn("bars", st.c.get("dstate", {}), "the whole frame left the kernel holding nothing")
        fr = st.push(dict(p1, now=1001))                            # the same payload, only the clock moved
        self.assertEqual([f["type"] for f in fr], ["bars"]); self.assertIn("bars", st.c.get("dstate", {}), "the full went and is held; not deduped away")
        self.assertEqual(st.held, dict(p1, now=1001))
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 0)
        fr = st.push(_bars({S1: self._turn(S1, 2)}, [], [], now=1005))
        self.assertEqual([f["type"] for f in fr], ["delta"], "…and the stream continues as deltas the client can apply")

    def test_n_the_same_payload_object_pushed_again_skips_the_per_entry_compare_until_the_repost(self):
        """The builders reuse an unchanged payload object across cycles (the pusher holds the bars by the cached
        timeline's identity), so the split the client's state was last written from is the split this cycle would
        compare against it: the compare is skipped and nothing is sent. Past the repost window the compare runs and
        the repost goes. An EQUAL payload in a new object (a content-equal rebuild) is compared once, which adopts its
        split, and that object is short-circuited from then on."""
        st = _Stream("bars")
        p = _bars({S1: self._turn(S1, 3), S2: self._turn(S2, 2)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [])
        st.push(p)
        self.assertIs(st.c["dstate"]["bars"]["parts"], km._delta_parts("bars", p), "the keyed full records the split it went from")
        real_order, real_shape, calls = km._client_order, km._order_shape, []
        def order(*a): calls.append("_client_order"); return real_order(*a)
        def shape(*a): calls.append("_order_shape"); return real_shape(*a)
        with mock.patch.object(km, "_client_order", order), mock.patch.object(km, "_order_shape", shape):
            self.assertEqual(st.push(p), [], "the same object: nothing to send")
            self.assertEqual(st.push(p), [])
            self.assertEqual(calls, [], "…and no per-entry compare ran to find that out")
            st.c["dstate"]["bars"]["at"] -= km._DEDUP_REPOST_S + 1
            fr = st.push(p)
            self.assertEqual(len(fr), 1); self.assertEqual(fr[0]["type"], "delta"); self.assertEqual(fr[0]["coll"], {})
            self.assertTrue(calls, "past the repost window the same object is compared and the repost goes")
            self.assertIs(st.c["dstate"]["bars"]["parts"], km._delta_parts("bars", p), "a delta that went records its split")
            del calls[:]
            q = dict(p, now=1002)                              # an equal payload in a NEW object: a content-equal rebuild
            self.assertEqual(st.push(q), [], "an equal payload in a new object still sends nothing…")
            self.assertTrue(calls, "…by comparing once: identity is the short-circuit, not equality")
            self.assertIs(st.c["dstate"]["bars"]["parts"], km._delta_parts("bars", q), "…and the compare adopts the new split")
            del calls[:]
            self.assertEqual(st.push(q), [], "the same new object again: nothing to send")
            self.assertEqual(calls, [], "…and no compare: the adopted split is an identity hit")
        p2 = json.loads(json.dumps(p)); p2["turns"][S2] = self._turn(S2, 3); p2["now"] = 1005
        self.assertEqual([f["type"] for f in st.push(p2)], ["delta"])
        self.assertIs(st.c["dstate"]["bars"]["parts"], km._delta_parts("bars", p2), "the held split follows the change")
        self.assertEqual(st.push(p2), []); self.assertEqual(st.held, p2)

    def test_o_a_raising_send_on_the_delta_path_leaves_the_revision_and_the_held_split_unchanged(self):
        """A frame that did not go does not advance what the client holds: the revision stays, and the held split
        stays the one the last frame that went was written from, so the payload that failed is not an identity hit
        next cycle and the compare runs against the state the client really holds."""
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], []))
        fr = st.push(_bars({S1: self._turn(S1, 2)}, [], [], now=1005))
        self.assertEqual([f["type"] for f in fr], ["delta"])
        rev, held = st.c["dstate"]["bars"]["rev"], st.c["dstate"]["bars"]["parts"]
        def boom(s): raise RuntimeError("synthetic socket failure")   # not a "bytes behind" drop: no bell row
        st.c["send"] = boom
        p3 = _bars({S1: self._turn(S1, 3)}, [], [], now=1010)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(st.push(p3), [])
        self.assertFalse(st.c["alive"], "a failed send marks the client dead")
        self.assertEqual(st.c["dstate"]["bars"]["rev"], rev, "a frame that did not go does not advance what the client holds")
        self.assertIs(st.c["dstate"]["bars"]["parts"], held, "…nor the split its state was written from")
        self.assertIsNot(st.c["dstate"]["bars"]["parts"], km._delta_parts("bars", p3))

    def test_p_an_unchanged_compare_leaves_the_repost_timer_on_the_last_frame_that_went(self):
        """The unchanged branch adopts the split but leaves `at` alone, and so does the identity short-circuit: the
        repost timer counts from the last frame that WENT, so a content-equal rebuild every cycle cannot postpone the
        repost that keeps the pane's clock and fade alive."""
        st = _Stream("bars")
        p = _bars({S1: self._turn(S1, 2)}, [], [])
        st.push(p)                                                 # the keyed full: the frame that went
        ds = st.c["dstate"]["bars"]
        ds["at"] -= km._DEDUP_REPOST_S - 5                         # …55 s ago
        at0 = ds["at"]
        q = dict(p, now=1002)                                      # a content-equal rebuild: compared once, adopted
        self.assertEqual(st.push(q), [])
        self.assertIs(ds["parts"], km._delta_parts("bars", q), "the compare adopted the new split")
        self.assertEqual(st.push(q), [], "…so the same object is an identity hit")
        self.assertEqual(ds["at"], at0, "neither branch moved the repost timer")
        with mock.patch.object(km, "_DEDUP_REPOST_S", km._DEDUP_REPOST_S - 10):   # the window closes on the 55 s old frame
            fr = st.push(q)
        self.assertEqual([f["type"] for f in fr], ["delta"], "the repost goes, timed from the frame that went")
        self.assertEqual(fr[0]["coll"], {}); self.assertEqual(fr[0]["rest"], {"now": 1002})


    def test_g_a_bar_appended_to_an_earlier_lane_crosses_alone_and_lands_in_its_lane(self):
        """The flat key order changes (the new bar sits before the later lanes' bars) but the assembled
        lanes do not — so no order crosses, and the client's derived order must still assemble right."""
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 2), S2: self._turn(S2, 2), S3: self._turn(S3, 1)}, [], []))
        p2 = _bars({S1: self._turn(S1, 3), S2: self._turn(S2, 2), S3: self._turn(S3, 1)}, [], [], now=1001)
        fr = st.push(p2)
        self.assertEqual(len(fr), 1); d = fr[0]
        self.assertEqual(set(d["coll"]["turns"]["set"]), {S1 + SEP + "seg-1-2"})
        self.assertNotIn("order", d["coll"]["turns"], "same lanes, same relative order: nothing to say")
        self.assertEqual(st.held, p2, "…and the client's own derived order assembles the exact payload")
        self.assertEqual(list(st.held["turns"]), [S1, S2, S3], "lane order kept")
        # a lane whose bars all go, then a bar in a lane that never existed: deletes and a fresh group
        p3 = _bars({S1: self._turn(S1, 3), S3: self._turn(S3, 1), "11111111-2222-3333-4444-aaaaaaaaaaa4": self._turn("x4", 1)}, [], [], now=1002)
        st.push(p3)
        self.assertEqual(st.held, p3)

    def test_h_numeric_ids_enumerate_ascending_in_javascript_so_the_order_crosses_when_that_would_misplace_them(self):
        """JS enumerates integer-like object keys ascending before the rest — the shim appends a delta's new
        keys in THAT order. Two new messages with ids "10" then "9" would land as "9","10"; the kernel must
        predict the misplacement and send the order. Numeric ids that arrive ascending need none."""
        def msg(i, when): return {"id": i, "sent": when, "text": "m"}
        st = _Stream("bars")
        st.push(_bars({S1: self._turn(S1, 1)}, [], [msg("10", 1)]))
        p2 = _bars({S1: self._turn(S1, 1)}, [], [msg("10", 1), msg("11", 2)], now=1001)
        d = st.push(p2)[0]
        self.assertNotIn("order", d["coll"]["messages"], "an ascending numeric id appends where JS puts it anyway")
        self.assertEqual(st.held, p2)
        p3 = _bars({S1: self._turn(S1, 1)}, [], [msg("10", 1), msg("11", 2), msg("30", 3), msg("9", 4)], now=1002)
        d = st.push(p3)[0]
        self.assertEqual(d["coll"]["messages"].get("order"), ["10", "11", "30", "9"], "JS would put 9 first: the order must cross")
        self.assertEqual(st.held, p3)
        self.assertEqual([m["id"] for m in st.held["messages"]], ["10", "11", "30", "9"])

class FeedDeltas(unittest.TestCase):
    def setUp(self):
        km._delta_parts_cache.clear()
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def _ask(self, i, column="working", text="do the thing"):
        return {"itemId": "awaiting:g%d" % i, "sid": S1, "column": column, "text": text, "color": None, "trail": [1, 2, 3]}

    def test_a_only_the_card_that_moved_crosses(self):
        st = _Stream("feed")
        p1 = _feed([self._ask(1), self._ask(2), self._ask(3)])
        self.assertEqual([f["type"] for f in st.push(p1)], ["feed"])
        p2 = _feed([self._ask(1), self._ask(2, column="done"), self._ask(3)], now=1005)
        frames = st.push(p2)
        self.assertEqual(frames[0]["type"], "delta")
        self.assertEqual(set(frames[0]["coll"]["asks"]["set"]), {"awaiting:g2"})
        self.assertEqual(st.held, p2)

    def test_b_the_remainder_rides_only_when_it_changed(self):
        st = _Stream("feed")
        st.push(_feed([self._ask(1)]))
        p2 = _feed([self._ask(1)], now=1005, showDismissed=True)
        fr = st.push(p2)
        self.assertEqual(fr[0]["type"], "delta")
        self.assertIn("showDismissed", fr[0]["rest"]); self.assertEqual(fr[0]["coll"], {})
        self.assertEqual(st.held, p2)


    def test_c_a_card_inserted_at_the_top_costs_one_card_and_the_order(self):
        """Cards are keyed by itemId, so an insert anywhere ships that card plus the key order — never the feed."""
        km._DELTA_MAX_FRACTION = self._frac                    # the real threshold
        st = _Stream("feed")
        st.push(_feed([self._ask(i, text="x" * 300) for i in range(30)]))
        p2 = _feed([self._ask(99, text="y" * 300)] + [self._ask(i, text="x" * 300) for i in range(30)], now=1005)
        fr = st.push(p2)
        self.assertEqual(fr[0]["type"], "delta")
        self.assertEqual(set(fr[0]["coll"]["asks"]["set"]), {"awaiting:g99"})
        self.assertEqual(fr[0]["coll"]["asks"]["order"][0], "awaiting:g99")
        self.assertEqual(st.held, p2)


class ShimDecoderMatchesTheKernel(unittest.TestCase):
    """The shim's own JavaScript, lifted verbatim from the served page, reassembles the kernel's frames to the
    exact new payload — the property the whole scheme rests on."""

    def _shim_functions(self):
        js = km._shim("timeline", 1)
        m = re.search(r"var DELTA_KINDS=.*?last\.rev=d\.rev;last\.msg=m;return m;\}", js, re.S)
        self.assertIsNotNone(m, "the delta functions must be present in the shim")
        return m.group(0)

    def test_a_node_reassembles_every_frame_of_a_stream_to_the_new_payload(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        km._delta_parts_cache.clear()
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        st = _Stream("bars")
        t0 = 1000
        def turn(sid, n): return [{"id": "s%s-%d" % (sid[-1], i), "t": t0 - 60 * i, "end": t0 - 60 * i + 30} for i in range(n)]
        def msg(i, when): return {"id": i, "sent": when, "text": "m"}
        payloads = [
            _bars({S1: turn(S1, 2), S2: turn(S2, 1)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "to": "x"}]),
            _bars({S1: turn(S1, 3), S2: turn(S2, 1)}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "to": "x"}], now=1005),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [{"sid": S3, "judge": "planner", "t": 3, "t1": 4}, {"sid": S1, "judge": "closer", "t": 1, "t1": 2}],
                  [{"id": "m2", "to": "y"}, {"id": "m1", "to": "x"}], now=1010, warming=True),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [{"id": "m2", "to": "y"}], now=1015),
            # numeric ids: "11" appends where JS puts it; then "30" followed by "9" needs the order to cross
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [msg("10", 1), msg("11", 2)], now=1020),
            _bars({S1: turn(S1, 3), S3: turn(S3, 1)}, [], [msg("10", 1), msg("11", 2), msg("30", 3), msg("9", 4)], now=1025),
            # the first lane empties to a bare value, the last lane grows: bare-prefix entry + append
            _bars({S1: [], S3: turn(S3, 2)}, [], [msg("9", 4)], now=1030),
            # an empty id and a real id spelling a positional key; then the `warming` key leaves the payload
            _bars({S1: [{"id": "", "t": 1}, {"id": "b2", "t": 2}], S3: turn(S3, 2)}, [], [{"id": "#1", "x": 0}, {"x": 1}], now=1035, warming=True),
            dict((kk, v) for kk, v in _bars({S1: [{"id": "", "t": 1}, {"id": "b2", "t": 3}], S3: turn(S3, 2)}, [], [{"id": "#1", "x": 0}, {"x": 5}], now=1040).items() if kk != "warming"),
            # duplicate ids in one lane (a segment split by a host sleep gives every piece the segment's id): the
            # second takes a positional key on both sides (T278c: the shim derives keys, so it must agree here too)
            _bars({S1: [{"id": "dup", "t": 1}, {"id": "dup", "t": 2}, {"id": "b3", "t": 3}], S3: turn(S3, 2)}, [], [{"id": "#1", "x": 0}, {"x": 5}], now=1045),
            _bars({S1: [{"id": "dup", "t": 1}, {"id": "dup", "t": 9}, {"id": "b3", "t": 3}], S3: turn(S3, 2)}, [], [{"id": "#1", "x": 0}, {"x": 5}], now=1050),
        ]
        frames = []
        for p in payloads:
            frames += st.push(p)
        self.assertGreaterEqual(sum(1 for f in frames if f["type"] == "delta"), 5)
        self.assertTrue(any("order" in (f.get("coll") or {}).get("messages", {}) for f in frames if f["type"] == "delta"),
                        "the numeric-id step must have shipped an order for the shim to be tested on it")
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "frames.json"), "w") as f:
            json.dump(frames, f)
        script = self._shim_functions() + r"""
var frames=JSON.parse(require("fs").readFileSync(process.argv[2],"utf8"));var out=[];
for(var i=0;i<frames.length;i++){var msg=frames[i];
if(msg.type==="delta"){var full=applyDelta(msg);if(!full){out.push({error:"rejected",at:i});break;}msg=full;}
else if(DELTA_KINDS[msg.type]){delete msg._keys;LAST[msg.type]={rev:0,msg:msg,maps:buildMaps(msg)};}
out.push(msg);}
process.stdout.write(JSON.stringify(out));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "frames.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(len(out), len(frames))
        # every frame reassembles to the payload the kernel had at that moment
        self.assertEqual(out[0], payloads[0])
        for i in range(1, len(payloads)):
            self.assertEqual(out[i], payloads[i], "frame %d reassembled differently in the shim than the kernel built" % i)
        self.assertTrue(any("dictlist" in f.get("coll", {}).get("turns", {}).__class__.__name__ or True for f in frames))

    def test_c_untouched_lanes_keep_their_array_identity_across_a_delta(self):
        """A delta renews only the lane arrays it touched: the lane prefix of every set/del key, plus — when the
        frame carries an `order` — every lane whose key subsequence changed. Every other lane's array is the very
        object the previous message held (===), so a pane can read `turns[sid]` identity as "unchanged". The
        assembled VALUE is unchanged either way (test_a pins that)."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        km._delta_parts_cache.clear()
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        S4 = "11111111-2222-3333-4444-aaaaaaaaaaa4"
        st = _Stream("bars")
        t0 = 1000
        def turn(sid, n): return [{"id": "s%s-%d" % (sid[-1], i), "t": t0 - 60 * i, "end": t0 - 60 * i + 30} for i in range(n)]
        s3rev = list(reversed(turn(S3, 3)))                       # the same three bars, the other way round
        payloads = [
            _bars({S1: turn(S1, 2), S2: turn(S2, 1), S3: turn(S3, 3)}, [], []),
            _bars({S1: turn(S1, 3), S2: turn(S2, 1), S3: turn(S3, 3)}, [], [], now=1005),                   # S1 grows
            _bars({S1: turn(S1, 3), S2: [{"id": "s2-0", "t": t0, "end": t0 + 45}], S3: turn(S3, 3)}, [], [], now=1010),   # S2's bar changes
            _bars({S1: [], S2: [{"id": "s2-0", "t": t0, "end": t0 + 45}], S3: turn(S3, 3)}, [], [], now=1015),           # S1 empties
            _bars({S1: [], S2: [{"id": "s2-0", "t": t0, "end": t0 + 45}], S3: s3rev}, [], [], now=1020),                 # S3 reorders
            _bars({S1: [], S2: [{"id": "s2-0", "t": t0, "end": t0 + 45}], S3: s3rev, S4: turn(S4, 1)}, [], [], now=1025),   # S4 appears
        ]
        frames = []
        for p in payloads:
            frames += st.push(p)
        deltas = [f for f in frames if f["type"] == "delta"]
        self.assertEqual(len(deltas), len(payloads) - 1, "every step after the first must have shipped as a delta")
        self.assertIn("order", deltas[3].get("coll", {}).get("turns", {}), "a within-lane reorder must ship an order")
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "frames.json"), "w") as f:
            json.dump(frames, f)
        script = self._shim_functions() + r"""
var frames=JSON.parse(require("fs").readFileSync(process.argv[2],"utf8"));var out=[];var prev=null;
for(var i=0;i<frames.length;i++){var msg=frames[i];
if(msg.type==="delta"){var full=applyDelta(msg);if(!full){out.push({error:"rejected",at:i});break;}
var same=[],renewed=[];for(var ln in full.turns){if(prev&&Object.prototype.hasOwnProperty.call(prev.turns,ln)&&prev.turns[ln]===full.turns[ln])same.push(ln);else renewed.push(ln);}
same.sort();renewed.sort();out.push({same:same,renewed:renewed,turns:full.turns});prev=full;}
else if(DELTA_KINDS[msg.type]){delete msg._keys;LAST[msg.type]={rev:0,msg:msg,maps:buildMaps(msg)};prev=msg;out.push({full:true});}}
process.stdout.write(JSON.stringify(out));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "frames.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out[0], {"full": True})
        steps = out[1:]
        self.assertEqual(len(steps), 5)
        for k, step in enumerate(steps):
            self.assertEqual(step["turns"], payloads[k + 1]["turns"], "step %d assembled a different value" % (k + 1))
        self.assertEqual((steps[0]["same"], steps[0]["renewed"]), (sorted([S2, S3]), [S1]))       # S1 grew
        self.assertEqual((steps[1]["same"], steps[1]["renewed"]), (sorted([S1, S3]), [S2]))       # S2's bar changed
        self.assertEqual((steps[2]["same"], steps[2]["renewed"]), (sorted([S2, S3]), [S1]))       # S1 emptied to the bare marker
        self.assertEqual(steps[2]["turns"][S1], [], "an emptied lane carries the kernel's [] value")
        self.assertEqual((steps[3]["same"], steps[3]["renewed"]), (sorted([S1, S2]), [S3]))       # S3's keys reordered: same set, new array
        self.assertEqual((steps[4]["same"], steps[4]["renewed"]), (sorted([S1, S2, S3]), [S4]))   # S4 appeared; nothing else moved

    def test_e_untouched_feed_cards_keep_their_object_identity_across_a_delta(self):
        """The feed's per-card update gate (ui/webview/feed-card-gate.ts) repaints a card when its OBJECT changed,
        so the shim's reassembly must hand every untouched card through as the very object the previous message
        held (===) and mint a new one only for a card the delta set. Held here, in the lane that owns the shim
        (review find, 2026-09-08: the TypeScript lane had lifted kernel.py's shim by source text to check this,
        and a kernel edit must not break that lane). The assembled VALUE is the kernel's payload either way."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        km._delta_parts_cache.clear()
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        def ask(i, column="working"):
            return {"itemId": "awaiting:g%d" % i, "sid": S1, "column": column, "text": "do the thing", "color": None, "trail": [1, 2, 3]}
        st = _Stream("feed")
        payloads = [_feed([ask(1), ask(2), ask(3)]),
                    _feed([ask(1), ask(2, column="done"), ask(3)], now=1005)]   # one card moves; the other two are untouched
        frames = []
        for p in payloads:
            frames += st.push(p)
        self.assertEqual([f["type"] for f in frames], ["feed", "delta"])
        self.assertEqual(set(frames[1]["coll"]["asks"]["set"]), {"awaiting:g2"}, "only the moved card crosses")
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "frames.json"), "w") as f:
            json.dump(frames, f)
        script = self._shim_functions() + r"""
var frames=JSON.parse(require("fs").readFileSync(process.argv[2],"utf8"));
var full=frames[0];delete full._keys;LAST[full.type]={rev:0,msg:full,maps:buildMaps(full)};
var next=applyDelta(frames[1]);if(!next){process.stdout.write(JSON.stringify({error:"rejected"}));process.exit(0);}
var same=[];for(var i=0;i<next.asks.length;i++)same.push(next.asks[i]===full.asks[i]);
process.stdout.write(JSON.stringify({newMessage:next!==full,same:same,asks:next.asks}));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "frames.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertNotIn("error", out, "the delta applies over the keyed full frame")
        self.assertTrue(out["newMessage"], "a delta builds a NEW message object (the bundle may still hold the previous one)")
        self.assertEqual(out["same"], [True, False, True],
                         "the untouched cards are the same objects (the gate skips them); the moved card is a new one (the gate repaints it)")
        self.assertEqual(out["asks"], payloads[1]["asks"], "…and the value is the kernel's")

    def test_d_a_lane_that_only_loses_a_bar_is_renewed_and_carries_the_shorter_array(self):
        """A lane whose delta is `del` alone (no `set`, no `order`: a bar retired with nothing replacing it and
        no key crossing another) is a TOUCHED lane: it is renewed and carries the shorter array. Without the
        `del` clause in touchedLanes the shim would hand the pane the held array, so the timeline kept showing
        a bar the kernel removed, a value bug the identity test above never reaches because every shrinking
        lane there also gets a `set` or an `order`."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        km._delta_parts_cache.clear()
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        st = _Stream("bars")
        t0 = 1000
        def turn(sid, n): return [{"id": "s%s-%d" % (sid[-1], i), "t": t0 - 60 * i, "end": t0 - 60 * i + 30} for i in range(n)]
        payloads = [
            _bars({S1: turn(S1, 2), S2: turn(S2, 1), S3: turn(S3, 3)}, [], []),
            _bars({S1: turn(S1, 2), S2: turn(S2, 1), S3: turn(S3, 3)[:2]}, [], [], now=1005),      # S3 loses its last bar
            _bars({S1: turn(S1, 2), S2: turn(S2, 1), S3: [turn(S3, 3)[1]]}, [], [], now=1010),     # S3 loses its first bar
            _bars({S1: [turn(S1, 2)[1]], S2: turn(S2, 1), S3: [turn(S3, 3)[1]]}, [], [], now=1015),   # S1 loses its first bar
        ]
        frames = []
        for p in payloads:
            frames += st.push(p)
        deltas = [f for f in frames if f["type"] == "delta"]
        self.assertEqual(len(deltas), len(payloads) - 1, "every step after the first must have shipped as a delta")
        for k, d in enumerate(deltas):
            turns = d.get("coll", {}).get("turns", {})
            self.assertIn("del", turns, "step %d must retire a key" % (k + 1))
            self.assertNotIn("set", turns, "step %d is del-only by construction: no value changed" % (k + 1))
            self.assertNotIn("order", turns, "step %d is del-only by construction: no key crossed another" % (k + 1))
        fx = tempfile.mkdtemp()
        with open(os.path.join(fx, "frames.json"), "w") as f:
            json.dump(frames, f)
        script = self._shim_functions() + r"""
var frames=JSON.parse(require("fs").readFileSync(process.argv[2],"utf8"));var out=[];var prev=null;
for(var i=0;i<frames.length;i++){var msg=frames[i];
if(msg.type==="delta"){var full=applyDelta(msg);if(!full){out.push({error:"rejected",at:i});break;}
var same=[],renewed=[];for(var ln in full.turns){if(prev&&Object.prototype.hasOwnProperty.call(prev.turns,ln)&&prev.turns[ln]===full.turns[ln])same.push(ln);else renewed.push(ln);}
same.sort();renewed.sort();out.push({same:same,renewed:renewed,turns:full.turns});prev=full;}
else if(DELTA_KINDS[msg.type]){delete msg._keys;LAST[msg.type]={rev:0,msg:msg,maps:buildMaps(msg)};prev=msg;out.push({full:true});}}
process.stdout.write(JSON.stringify(out));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "frames.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out[0], {"full": True})
        steps = out[1:]
        self.assertEqual(len(steps), 3)
        for k, step in enumerate(steps):
            self.assertEqual(step["turns"], payloads[k + 1]["turns"], "step %d assembled a different value (a retired bar still shown?)" % (k + 1))
        self.assertEqual((steps[0]["same"], steps[0]["renewed"]), (sorted([S1, S2]), [S3]))       # S3 shrank from the end
        self.assertEqual((steps[1]["same"], steps[1]["renewed"]), (sorted([S1, S2]), [S3]))       # S3 shrank from the front
        self.assertEqual((steps[2]["same"], steps[2]["renewed"]), (sorted([S2, S3]), [S1]))       # S1 shrank; S3 kept its new array

    def test_b_a_delta_whose_base_is_not_held_is_refused(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        fx = tempfile.mkdtemp()
        script = self._shim_functions() + r"""
LAST.bars={rev:0,msg:{type:"bars",turns:{},judging:[],messages:[],now:1},maps:buildMaps({type:"bars",turns:{},judging:[],messages:[]},{turns:[],judging:[],messages:[]})};
var r=applyDelta({type:"delta",slot:"bars",base:3,rev:4,coll:{}});
process.stdout.write(JSON.stringify({refused:r===null,rev:LAST.bars.rev}));"""
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(script)
        r = subprocess.run([node, os.path.join(fx, "run.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"refused": True, "rev": 0}, "a base mismatch is refused and nothing moves")

class TwoThreadsOneClient(unittest.TestCase):
    """The socket handler's connect push (`ready` runs _push_one on the handler thread) and the pusher's cycle
    both reach _send_slot for the same client, and both read and write that client's held delta state.
    Review find (2026-09-04): unserialized, one interleaving — both see nothing held, a rebuild lands between
    them, the handler's dedup slot is written first but its bytes go second — left the client holding payload
    A while the kernel believed B, and every later delta was computed against B: silent divergence until the
    next full. The pre-PR race was on the stateless dedup dict alone, where a double full was harmless."""

    def setUp(self):
        km._delta_parts_cache.clear()
        km._delta_unkeyable_said.clear()      # the said-once latch is module-global: each test starts unsaid
        self._frac = km._DELTA_MAX_FRACTION
        km._DELTA_MAX_FRACTION = 10.0

    def tearDown(self):
        km._DELTA_MAX_FRACTION = self._frac

    def test_a_a_second_sender_waits_for_the_first_frame_and_the_client_ends_holding_the_newer_payload(self):
        c = _Client()
        gate, entered = threading.Event(), threading.Event()
        real_send = c["send"]

        def parked_send(s):                     # thread A parks INSIDE its frame: dedup slot written, state not yet
            entered.set()
            gate.wait(5)
            real_send(s)
        c["send"] = parked_send
        bar = lambda i: {"id": "seg-%d" % i, "t": i, "end": i + 1, "open": False}
        pa = _bars({S1: [bar(1)]}, [], [], now=1)
        pb = _bars({S1: [bar(1), bar(2)]}, [], [], now=2)

        def push(p):
            pre = json.dumps(p)
            km._send_slot(c, "bars", p, pre, km._dedup_sig(p, pre))
        ta = threading.Thread(target=push, args=(pa,)); ta.start()
        self.assertTrue(entered.wait(5), "thread A is inside its frame")
        tb = threading.Thread(target=push, args=(pb,)); tb.start()
        tb.join(0.3)
        self.assertTrue(tb.is_alive(), "thread B is still inside _send_slot (with the lock, waiting for A; the frame "
                                       "order below is what pins the lock — without it B parks in the same gate)")
        self.assertEqual(c.frames, [], "nothing has crossed yet")
        gate.set(); ta.join(5); tb.join(5)
        self.assertFalse(ta.is_alive() or tb.is_alive())
        last = None                             # replay what the wire carried through the shim's mirror
        for fr in c.frames:
            if fr.get("type") == "delta":
                out = _py_apply(last, fr)
                self.assertIsNotNone(out, "the client could apply every delta it was sent")
            else:
                self.assertNotIn("_keys", fr)
                last = {"rev": 0, "msg": dict(fr), "maps": _py_maps(fr)}
        self.assertEqual([f["type"] for f in c.frames], ["bars", "delta"], "A's keyed full, then B's delta against it")
        self.assertEqual(last["msg"], pb, "the client holds the newer payload…")
        self.assertEqual(c["dstate"]["bars"]["rev"], 1, "…and the kernel's belief about it agrees")
        self.assertEqual(c["dstate"]["bars"]["coll"]["turns"], {S1 + SEP + "seg-1": json.dumps(bar(1)), S1 + SEP + "seg-2": json.dumps(bar(2))})

    def test_b_a_collection_that_is_neither_list_nor_dict_goes_whole_and_is_logged_once(self):
        # _delta_split used to split an unexpected value (None where a list belongs) into ZERO entries, and the
        # remainder did not carry it either: the client kept its assembled [] while the kernel held None, with no
        # resync ever asked. Now the payload is unkeyable → the whole frame goes, and stderr says so once per shape.
        st = _Stream("bars")
        p1 = _bars({S1: [{"id": "seg-1", "t": 1, "end": 2, "open": False}]}, [], [{"id": "m1", "sent": 1}], now=1)
        st.push(p1)
        p2 = dict(p1, messages=None, now=2)
        p3 = dict(p2, now=3, warming=True)      # a real change (`now` alone is volatile and dedups)
        err = io.StringIO()
        with redirect_stderr(err):
            frames = st.push(p2)
            frames2 = st.push(p3)
        self.assertEqual(frames, [p2], "unkeyable: the whole payload crossed, no keys, no empty split")
        self.assertEqual(frames2, [p3])
        self.assertNotIn("bars", st.c.get("dstate", {}), "the kernel holds nothing for a client it sent a keyless whole")
        self.assertEqual(err.getvalue().count("cannot be keyed"), 1, "said once, not once per cycle")
        self.assertIn("messages", err.getvalue())

    def test_c_the_beat_reads_the_read_state_before_the_ping_stamp(self):
        # the handler ends a dispatch with two writes: pingAt = None, THEN inRead = True. A beat reading pingAt
        # first could pair the stale stamp with the fresh read state and drop a live peer as silent. Modelled
        # exactly: whichever of the two keys the beat reads FIRST sees the pre-end state, the second the post state.
        class _Handoff(dict):
            def __init__(self):
                super().__init__(app="timeline", wid="w1", alive=True, sock=object(), send=lambda s: None,
                                 qlock=threading.Lock(), lastIn=0.0)
                self.reads = 0

            def get(self, k, d=None):
                if k in ("pingAt", "inRead"):
                    self.reads += 1
                    pre = self.reads == 1
                    return (0.0 if pre else None) if k == "pingAt" else (False if pre else True)
                return super().get(k, d)
        c = _Handoff()
        dropped = []
        with mock.patch.object(km, "_drop_dead_ws_client", lambda cl, why: dropped.append(why)), \
             mock.patch.object(km, "_clients", [c]):
            km._keepalive_all(now=km.WS_DEAD_S + 1)
        self.assertEqual(dropped, [], "a live peer finishing its dispatch is never judged silent")
        self.assertEqual(c.reads, 2, "both keys were read once, read state first")

    def test_d_the_ready_reset_and_a_chat_send_serialize_on_the_clients_lock(self):
        # pre-existing (predates the PR), closed with the same lock: the handler thread's `ready` reset iterated the
        # client's dedup dict while the pusher's _send_client inserted keys into it — "dictionary changed size during
        # iteration", the ready dispatch dying in the handler's generic except, and the _push_one repair skipped.
        client, _q, _lock = km._new_ws_client("chat", "w1", object(), start_sender=False)
        client["send"] = lambda s: None
        client.setdefault("sent", {})[("chat", S1)] = ("sig", 0.0); client["echat"] = {S1: 1}
        client["dlock"].acquire()                       # "another thread" holds the client's lock…
        done = []
        t1 = threading.Thread(target=lambda: (km._client_reset_chat_base(client), done.append("reset")))
        t2 = threading.Thread(target=lambda: (km._send_client(client, ("chat", S2), {"type": "chat"}), done.append("send")))
        t1.start(); t2.start(); t1.join(0.3); t2.join(0.3)
        try:
            self.assertEqual(done, [], "…and both the reset and the send wait for it")
        finally:
            client["dlock"].release()
        t1.join(5); t2.join(5)
        self.assertEqual(sorted(done), ["reset", "send"])
        self.assertNotIn(("chat", S1), client["sent"], "the reset cleared the slot it found (whichever ran first)")
        self.assertEqual(client["echat"], {})
        # and the interleaving itself, driven hard: many resets against many inserts, no RuntimeError
        client2, _q2, _l2 = km._new_ws_client("chat", "w2", object(), start_sender=False)
        client2["send"] = lambda s: None
        errors = []
        stop = threading.Event()

        def inserter():
            i = 0
            while not stop.is_set():
                try:
                    km._send_client(client2, ("chat", "s%d" % i), {"type": "chat"})
                except RuntimeError as e:
                    errors.append(e)
                i += 1

        def resetter():
            for _ in range(300):
                try:
                    km._client_reset_chat_base(client2)
                except RuntimeError as e:
                    errors.append(e)
        prev = __import__("sys").getswitchinterval()
        __import__("sys").setswitchinterval(1e-6)
        try:
            ti = threading.Thread(target=inserter); tr = threading.Thread(target=resetter)
            ti.start(); tr.start(); tr.join(30); stop.set(); ti.join(30)
        finally:
            __import__("sys").setswitchinterval(prev)
        self.assertFalse(tr.is_alive() or ti.is_alive())
        self.assertEqual(errors, [])

    def test_f_the_chat_sender_holds_the_clients_lock_across_its_read_decide_send_write(self):
        # the reset clears echat AND the slots; a pusher chat send DECIDED before the reset (tail branch chosen from
        # the old echat) and landed after it slipped its tail through the popped slot and wrote echat back, and the
        # connect push then sent a chatTail to a renderer holding nothing. Discriminating shape: echat holds a tail
        # base, the send is parked at the lock before it can read echat, the reset lands (re-entrant, from the lock's
        # holder) and the lock is released — the fold sends the FULL session; a lock only around the inner
        # _send_client would have read echat first and sent the chatTail.
        client, _q, _lock = km._new_ws_client("chat", "w1", object(), start_sender=False)
        frames = []
        client["send"] = lambda s: frames.append(json.loads(s)["type"])
        client["echat"] = {S1: ("u1", 0)}
        client.setdefault("sent", {})[("chat", S1)] = ("sig", time.time())
        m = {"type": "session", "id": S1, "events": [{"uuid": "u1", "kind": "prompt"}, {"uuid": "u2", "kind": "text"}],
             "status": "ready"}
        client["dlock"].acquire()
        t = threading.Thread(target=lambda: km._send_chat(client, m, None, 1, False), daemon=True)
        t.start(); t.join(0.3)
        try:
            self.assertTrue(t.is_alive(), "the chat send waits for the client's lock before reading anything")
            self.assertEqual(frames, [])
            km._client_reset_chat_base(client)          # the reset lands first (re-entrant: this thread holds the lock)
            self.assertEqual(client["echat"], {})
        finally:
            client["dlock"].release()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(frames, ["session"], "decided after the reset: the whole session, not a tail")
        self.assertEqual(client["echat"][S1], ("u1", 0))

    def test_g_the_needfull_reset_takes_the_same_lock(self):
        # the per-sid reset behind needFull popped echat[sid] and the dedup slot with no lock — the same race as the
        # ready reset, and worse on the client: render.ts latches awaitingFull until a full session lands, so a
        # chatTail sent instead left the tab frozen until reconnect. It now runs under the client's slot lock.
        client, _q, _lock = km._new_ws_client("chat", "w1", object(), start_sender=False)
        frames = []
        client["send"] = lambda s: frames.append(json.loads(s)["type"])
        client["echat"] = {S1: ("u1", 0), S2: ("x1", 0)}
        client.setdefault("sent", {})[("chat", S1)] = ("sig", time.time())
        client["dlock"].acquire()
        done = []
        tr = threading.Thread(target=lambda: (km._client_reset_chat_sid(client, S2), done.append("reset")), daemon=True)
        tr.start(); tr.join(0.3)
        self.assertTrue(tr.is_alive(), "the per-sid reset waits for the lock")
        m = {"type": "session", "id": S1, "events": [{"uuid": "u1", "kind": "prompt"}, {"uuid": "u2", "kind": "text"}],
             "status": "ready"}
        ts = threading.Thread(target=lambda: km._send_chat(client, m, None, 1, False), daemon=True)
        ts.start(); ts.join(0.3)
        try:
            self.assertTrue(ts.is_alive())
            km._client_reset_chat_sid(client, S1)       # needFull for S1 lands before the parked send decides
        finally:
            client["dlock"].release()
        tr.join(5); ts.join(5)
        self.assertEqual(done, ["reset"])
        self.assertNotIn(S2, client["echat"], "the parked per-sid reset ran once released")
        self.assertEqual(frames, ["session"], "the send, decided after the needFull reset, is the whole session")

    def test_h_the_lock_spans_the_send_decision_and_write_back_not_just_the_echat_read(self):
        # test_f/test_g park the sender BEFORE it reads echat, so a lock around the read alone would also pass them.
        # Here the sender has already DECIDED the tail and is parked inside _send_client: a reset arriving now must
        # still wait, or the tail lands after the pops, echat is written back, and the repair push finds a held tail.
        client, _q, _lock = km._new_ws_client("chat", "w1", object(), start_sender=False)
        client["send"] = lambda s: None
        client["echat"] = {S1: ("u1", 0)}
        client.setdefault("sent", {})[("chat", S1)] = ("sig", time.time())
        m = {"type": "session", "id": S1, "events": [{"uuid": "u1", "kind": "prompt"}, {"uuid": "u2", "kind": "text"}],
             "status": "ready"}
        gate, inside = threading.Event(), threading.Event()
        real = km._send_client

        def parked(c, key, msg, pre=None, sig=None, **kw):    # the tail branch was chosen; park before the bytes go
            inside.set()                                    # (**kw: the tail send names its /perf kind, "delta")
            gate.wait(5)
            return real(c, key, msg, pre=pre, sig=sig, **kw)
        done = []
        with mock.patch.object(km, "_send_client", parked):
            ts = threading.Thread(target=lambda: km._send_chat(client, m, None, 1, False), daemon=True); ts.start()
            self.assertTrue(inside.wait(5), "the sender decided its tail and is parked inside the send")
            tr = threading.Thread(target=lambda: (km._client_reset_chat_base(client), done.append("reset")), daemon=True)
            tr.start(); tr.join(0.3)
            self.assertEqual(done, [], "the reset waits: the sender holds the lock through decision, send and write-back")
            gate.set(); ts.join(5); tr.join(5)
        self.assertFalse(ts.is_alive() or tr.is_alive())
        self.assertEqual(done, ["reset"])
        self.assertEqual(client["echat"], {}, "the reset ran after the whole send: the write-back did not survive it")
        self.assertNotIn(("chat", S1), client["sent"])

    def test_e_the_size_fallback_re_enters_the_slot_send_under_the_held_lock_and_completes(self):
        # _send_slot_delta's 60% fallback calls _send_slot again on the SAME thread while the client's lock is held:
        # the lock must be re-entrant — with a plain Lock the pusher hangs here forever (verified writing the fold)
        km._DELTA_MAX_FRACTION = self._frac             # the real threshold
        bar = lambda i: {"id": "seg-%d" % i, "t": i, "end": i + 1, "open": False}
        st = _Stream("bars")
        p1 = _bars({S1: [bar(i) for i in range(3)]}, [], [], now=1)
        p2 = _bars({S1: [bar(i + 10) for i in range(3)]}, [], [], now=2)   # everything changed: no delta is smaller
        done = []
        # BOTH pushes on the guarded thread: with a plain Lock even the first (full) frame deadlocks in-thread
        # (_send_slot → _send_client both take it), so a push on the main thread would hang the suite, not fail it
        t = threading.Thread(target=lambda: (st.push(p1), done.append(st.push(p2))), daemon=True); t.start(); t.join(5)
        self.assertFalse(t.is_alive(), "the fallback re-entered the lock and returned")
        self.assertEqual([f["type"] for f in done[0]], ["bars"], "sent whole, and the stream re-based")
        self.assertEqual(st.held, p2)
        self.assertEqual(st.c["dstate"]["bars"]["rev"], 0)


def _bar(i):
    return {"id": "seg-%d" % i, "t": i, "end": i + 1, "open": False}


def _sig(p):
    return km._parts_sig(km._delta_parts("bars", p))


class TupleSignatureForTheSlots(unittest.TestCase):
    """The pusher dedups a slot payload on _parts_sig — (rest_sig, the split's (collection, key order, entry
    strings)) from the _delta_parts split it makes at the wire fill — not on json.dumps(payload) plus a sort_keys
    re-dump of the payload minus its clock. Equal tuples mean equal entries, key order and remainder."""

    def test_the_tuple_ignores_the_clock_and_moves_on_any_entry_lane_or_remainder(self):
        p = _bars({S1: [_bar(1)], S2: [_bar(2)]}, [{"sid": S1, "judge": "closer", "t": 1, "t1": 2}], [{"id": "m1", "sent": 1}])
        self.assertEqual(_sig(p), _sig(dict(p, now=2000)), "the clock is not part of the signature")
        self.assertEqual(_sig(p), _sig(json.loads(json.dumps(p))), "an equal payload in a new object compares equal")
        r = json.loads(json.dumps(p)); r["turns"][S1][0]["end"] = 9
        self.assertNotEqual(_sig(p), _sig(r), "a bar's field")
        r = json.loads(json.dumps(p)); r["turns"][S1].append(_bar(3))
        self.assertNotEqual(_sig(p), _sig(r), "a bar appended")
        r = json.loads(json.dumps(p)); r["turns"][S3] = r["turns"].pop(S1)
        self.assertNotEqual(_sig(p), _sig(r), "the same bars under another lane: the key order carries the lane")
        self.assertNotEqual(_sig(p), _sig(dict(p, warming=True)), "the remainder")
        r = json.loads(json.dumps(p)); next(iter(r["judging"].values()))[0]["t1"] = 3     # judging rides per lane (T278c)
        self.assertNotEqual(_sig(p), _sig(r), "a judging entry")
        r = json.loads(json.dumps(p)); r["messages"] = []
        self.assertNotEqual(_sig(p), _sig(r), "a message removed")
        self.assertEqual(_sig(p)[0], km._delta_parts("bars", p)[2])
        self.assertEqual([name for name, _o, _e in _sig(p)[1]], ["turns", "judging", "messages"], "collections in table order")

    def test_a_lane_reorder_is_stricter_than_the_old_signature_and_stays_exact_on_both_paths(self):
        p = _bars({S1: [_bar(1)], S2: [_bar(2)]}, [], [])
        q = _bars({S2: [_bar(2)], S1: [_bar(1)]}, [], [], now=1001)
        self.assertEqual(km._dedup_sig(p, json.dumps(p)), km._dedup_sig(q, json.dumps(q)), "the sort_keys form hid dict order")
        self.assertNotEqual(_sig(p), _sig(q), "the tuple does not: a reorder re-sends (towards more sends, never fewer)")
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0   # synthetic payloads are tiny: the size guard would send wholes
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        st = _Stream("bars")                          # a delta client: an order-only delta, no set, no del
        st.push(p)
        fr = st.push(q)
        self.assertEqual([f["type"] for f in fr], ["delta"])
        self.assertEqual(fr[0]["coll"], {"turns": {"order": [S2 + SEP + "seg-2", S1 + SEP + "seg-1"]}})
        self.assertEqual(list(st.held["turns"]), [S2, S1], "the mirror assembles the new lane order")
        c = _Client(); c.pop("delta")                 # a legacy client: one full frame, then deduped
        km._send_slot(c, "bars", p, json.dumps(p), _sig(p))
        km._send_slot(c, "bars", q, json.dumps(q), _sig(q))
        q2 = dict(q, now=1002)
        km._send_slot(c, "bars", q2, json.dumps(q2), _sig(q2))
        self.assertEqual([f["type"] for f in c.frames], ["bars", "bars"])
        self.assertEqual(list(c.frames[1]["turns"]), [S2, S1])

    def test_the_split_made_at_the_fill_is_handed_down_and_recorded_by_identity(self):
        p = _bars({S1: [_bar(1)]}, [], [])
        parts = km._delta_parts("bars", p)
        km._delta_parts_cache.clear()                 # a connect push on another thread evicted the single slot
        c = _Client()
        with mock.patch.object(km, "_delta_parts", side_effect=AssertionError("re-split")):
            km._send_slot(c, "bars", p, json.dumps(p), km._parts_sig(parts), parts)
            self.assertIs(c["dstate"]["bars"]["parts"], parts, "the keyed full records the split it was handed")
            km._send_slot(c, "bars", p, json.dumps(p), km._parts_sig(parts), parts)
            self.assertEqual(len(c.frames), 1, "the same split: the identity short-circuit, no compare, no re-split")
        # without a split handed down the path splits for itself, as every caller before the fill did
        q = _bars({S1: [_bar(1), _bar(2)]}, [], [], now=1001)
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        km._send_slot(c, "bars", q, json.dumps(q), _sig(q))
        self.assertEqual([f["type"] for f in c.frames], ["bars", "delta"])
        self.assertIs(c["dstate"]["bars"]["parts"], km._delta_parts("bars", q))

    def test_a_lazy_pre_is_serialized_only_when_a_whole_frame_goes(self):
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        p = _bars({S1: [_bar(1)]}, [], []); parts = km._delta_parts("bars", p); calls = []
        lazy = km._LazyWire(lambda: (calls.append("p"), json.dumps(p))[1], km._parts_est(parts))
        c = _Client()
        km._send_slot(c, "bars", p, lazy, km._parts_sig(parts), parts)      # the keyed full: made once, keyed
        self.assertEqual(calls, ["p"]); self.assertNotIn("_keys", c.frames[0]); self.assertEqual(lazy.size(), len(json.dumps(p)))
        km._send_slot(c, "bars", p, lazy, km._parts_sig(parts), parts)      # unchanged: the size only
        self.assertEqual(calls, ["p"])
        q = _bars({S1: [_bar(1), _bar(2)]}, [], [], now=1001); qparts = km._delta_parts("bars", q)
        qlazy = km._LazyWire(lambda: (calls.append("q"), json.dumps(q))[1], km._parts_est(qparts))
        km._send_slot(c, "bars", q, qlazy, km._parts_sig(qparts), qparts)   # a delta goes: the whole frame is never made
        self.assertEqual([f["type"] for f in c.frames], ["bars", "delta"])
        self.assertEqual(calls, ["p"]); self.assertFalse(qlazy.materialized())
        self.assertLess(qlazy.size(), len(json.dumps(q)), "unmade: the estimate (the frame's own keys are not in it)")
        self.assertGreater(qlazy.size(), 0)
        legacy = _Client(); legacy.pop("delta")                            # a whole-frame client makes it, once
        km._send_slot(legacy, "bars", q, qlazy, km._parts_sig(qparts), qparts)
        km._send_slot(legacy, "bars", q, qlazy, km._parts_sig(qparts), qparts)
        self.assertEqual(calls, ["p", "q"]); self.assertEqual([f["type"] for f in legacy.frames], ["bars"])
        self.assertEqual(legacy.frames[0], q)

    def test_the_size_guard_reads_the_estimate_and_falls_back_to_a_whole_frame(self):
        # _DELTA_MAX_FRACTION at its real value: a near-total change against a lazy `pre` crosses whole (the
        # estimate a little under the frame only makes that slightly more eager), and the whole frame is then
        # made — once — for the keyed full
        st = _Stream("bars")
        st.push(_bars({S1: [_bar(1)]}, [], []))
        big = _bars({S1: [_bar(i) for i in range(40)], S2: [_bar(i) for i in range(40)]}, [], [], now=1020)
        parts = km._delta_parts("bars", big); calls = []
        lazy = km._LazyWire(lambda: (calls.append(1), json.dumps(big))[1], km._parts_est(parts))
        n0 = len(st.c.frames)
        km._send_slot(st.c, "bars", big, lazy, km._parts_sig(parts), parts)
        self.assertEqual([f["type"] for f in st.c.frames[n0:]], ["bars"], "a near-total change crosses whole")
        self.assertEqual(calls, [1]); self.assertEqual(st.c.frames[-1]["type"], "bars"); self.assertIn("bars", st.c.get("dstate", {}))

    def test_the_feed_slot_takes_the_same_tuple_and_a_reordered_card_re_sends_once_then_dedups(self):
        def card(i, **f):
            d = {"itemId": "%s:g%d" % (S1, i), "sid": S1, "text": "Synthetic goal %d" % i, "t": i, "column": "working",
                 "tree": [{"id": "%s:g%d.1" % (S1, i), "text": "step", "status": "done"}]}
            d.update(f)
            return d
        fsig = lambda p: km._parts_sig(km._delta_parts("feed", p))
        p = _feed([card(1), card(2)])
        self.assertEqual(fsig(p), fsig(_feed([card(1), card(2)], now=2000, buildId=2)), "the clock and the build id are not in it")
        self.assertNotEqual(fsig(p), fsig(_feed([card(1), card(2, text="changed")])), "a card's field")
        self.assertNotEqual(fsig(p), fsig(_feed([card(1), card(2), card(3)])), "a card appended")
        self.assertNotEqual(fsig(p), fsig(_feed([card(1), card(2)], working=[S1])), "the remainder")
        self.assertEqual([name for name, _o, _e in fsig(p)[1]], ["asks"])
        # the sort_keys form was key-order-insensitive at every level; the tuple compares the strings a client
        # receives, so a card whose nested keys are reordered with equal content is one spurious frame (never a
        # stale client), then deduped — on the legacy path a whole frame, on the delta path a one-card set
        f1 = _feed([card(1), card(2), card(3)])
        f2 = _feed([card(1), dict(reversed(list(card(2).items()))), card(3)], now=1001)
        f3 = _feed([dict(a) for a in f2["asks"]], now=1002)
        self.assertEqual(km._dedup_sig(f1, json.dumps(f1)), km._dedup_sig(f2, json.dumps(f2)), "the old signature deduped this")
        self.assertNotEqual(fsig(f1), fsig(f2)); self.assertEqual(fsig(f2), fsig(f3))
        c = _Client(); c.pop("delta")
        for f in (f1, f2, f3):
            km._send_slot(c, "feed", f, json.dumps(f), fsig(f))
        self.assertEqual([f["type"] for f in c.frames], ["feed", "feed"], "the reordered card re-sends once, then dedups")
        self.assertEqual(list(c.frames[1]["asks"][1]), list(f2["asks"][1]), "the frame carries the new order")
        frac = km._DELTA_MAX_FRACTION; km._DELTA_MAX_FRACTION = 10.0
        self.addCleanup(setattr, km, "_DELTA_MAX_FRACTION", frac)
        d = _Client()
        for f in (f1, f2, f3):
            km._send_slot(d, "feed", f, json.dumps(f), fsig(f))
        self.assertEqual([f["type"] for f in d.frames], ["feed", "delta"])
        self.assertEqual(d.frames[1]["coll"], {"asks": {"set": {S1 + ":g2": f2["asks"][1]}}}, "the one card whose string moved")

    def test_a_lane_renamed_in_place_with_equal_bars_moves_the_tuple_and_re_sends(self):
        # the key order carries the lanes: the same bars under another lane name, in the same position, are the same
        # entry strings in the same order — only the keys tell them apart (the lane-move case above pops and appends,
        # which reorders the strings too, so it passes without the key order)
        p = _bars({S1: [_bar(1)], S2: [_bar(2)]}, [], [])
        r = _bars({S3: [_bar(1)], S2: [_bar(2)]}, [], [], now=1001)
        self.assertEqual([e for _n, _o, e in _sig(p)[1]], [e for _n, _o, e in _sig(r)[1]], "equal entry strings, in order")
        self.assertNotEqual(_sig(p), _sig(r), "…and the key order tells the lanes apart")
        c = _Client(); c.pop("delta")                 # a legacy client dedups on the tuple alone
        km._send_slot(c, "bars", p, json.dumps(p), _sig(p))
        km._send_slot(c, "bars", r, json.dumps(r), _sig(r))
        self.assertEqual([list(f["turns"]) for f in c.frames], [[S1, S2], [S3, S2]], "the renamed lane crosses; the client is not left holding the old one")

    def test_a_deduped_whole_frame_client_never_materializes_a_fresh_cell(self):
        # a content-equal rebuild mints a new payload and a new lazy cell; a legacy client that holds the frame dedups on
        # the tuple, and the dedup costs the cell its size() only — the frame is never serialized
        p = _bars({S1: [_bar(1)]}, [], []); parts = km._delta_parts("bars", p)
        legacy = _Client(); legacy.pop("delta")
        a = km._LazyWire(lambda: json.dumps(p), km._parts_est(parts))
        km._send_slot(legacy, "bars", p, a, km._parts_sig(parts), parts)
        self.assertTrue(a.materialized()); self.assertEqual(len(legacy.frames), 1, "the first frame goes, made once")
        q = json.loads(json.dumps(p)); q["now"] = 1001; qparts = km._delta_parts("bars", q); calls = []
        b = km._LazyWire(lambda: (calls.append(1), json.dumps(q))[1], km._parts_est(qparts))
        km._send_slot(legacy, "bars", q, b, km._parts_sig(qparts), qparts)
        self.assertEqual(len(legacy.frames), 1, "deduped"); self.assertEqual(calls, [], "…without serializing the new cell")
        self.assertFalse(b.materialized()); self.assertEqual(b.size(), km._parts_est(qparts), "the estimate answered the size guard")

    def test_a_whole_frame_whose_encode_raises_leaves_the_dedup_slot_for_a_retry(self):
        # _send_client materializes a lazy frame BEFORE it writes the client's dedup slot: an encode that raises leaves
        # the slot empty, so the next cycle's send is not deduped against a frame that never went
        p = _bars({S1: [_bar(1)]}, [], []); parts = km._delta_parts("bars", p)
        legacy = _Client(); legacy.pop("delta")
        boom = [True]

        def encode():
            if boom[0]:
                boom[0] = False
                raise ValueError("synthetic encode failure")
            return json.dumps(p)
        cell = km._LazyWire(encode, km._parts_est(parts))
        with self.assertRaises(ValueError):
            km._send_slot(legacy, "bars", p, cell, km._parts_sig(parts), parts)
        self.assertEqual(legacy.frames, []); self.assertFalse(cell.materialized())
        self.assertNotIn(("timelinebars",), legacy.get("sent", {}), "no frame went: the dedup slot is not written")
        km._send_slot(legacy, "bars", p, cell, km._parts_sig(parts), parts)      # the retry: the encode works, the frame goes
        self.assertEqual([f["type"] for f in legacy.frames], ["bars"]); self.assertTrue(cell.materialized())
        self.assertEqual(legacy["sent"][("timelinebars",)][0], km._parts_sig(parts))


class KeyerParsesTheKindOnce(unittest.TestCase):
    """_delta_split parses a collection's kind once (_delta_keyer) instead of once per item; the keys are
    byte-identical to the per-item form (_delta_key, kept as its wrapper)."""

    def test_delta_key_and_the_keyer_agree_on_every_kind_and_shape(self):
        items = [{"id": "a"}, {"id": ""}, {"id": None}, {"id": 1.0}, {"id": "#1"}, {"itemId": "x:g1"}, {"itemId": ""},
                 {"sid": S1, "t": 1, "judge": "closer", "t1": None}, {"sid": S1}, "not a dict", 7, None, {}]
        for kind in ("byid", "byid:itemId", "dictlist:id", "bykeys:sid,t,judge,t1", "dict", "weird"):
            keyer = km._delta_keyer(kind)
            for it in items:
                for prefix in ("", "lane" + SEP):
                    self.assertEqual(keyer(it, prefix), km._delta_key(kind, it, prefix), (kind, it, prefix))
        self.assertEqual(km._delta_keyer("bykeys:sid,t,judge,t1")({"sid": S1, "t": 1, "judge": "closer", "t1": None}),
                         S1 + SEP + "1" + SEP + "closer" + SEP + "None")
        self.assertEqual(km._delta_keyer("bykeys:sid,t,judge,t1")({"sid": S1}, "p"), "p" + S1 + SEP + "None" + SEP + "None" + SEP + "None")
        self.assertEqual(km._delta_keyer("byid:itemId")({"itemId": 1.0}, "p"), "p1.0")
        self.assertIsNone(km._delta_keyer("byid")({"id": ""}), "an empty id would spell the bare-prefix marker")
        self.assertIsNone(km._delta_keyer("dict")({"id": "a"}))

    def test_delta_split_output_is_pinned_on_duplicate_unkeyable_and_empty_entries(self):
        # the (entries, key order) a split produces, spelled out: positional keys for a duplicate or unkeyable
        # item, a bare-prefix entry for an empty or non-list lane — the shape the shim reassembles from
        enc = lambda v: json.dumps(v, default=str)
        ents, order = km._delta_split("dictlist:id", {S1: [_bar(1), _bar(1), {"t": 3}, "str"], S2: [], S3: None})
        self.assertEqual(order, [S1 + SEP + "seg-1", S1 + SEP + "#1", S1 + SEP + "#2", S1 + SEP + "#3", S2 + SEP, S3 + SEP])
        self.assertEqual(ents[S1 + SEP + "seg-1"], (_bar(1), enc(_bar(1))))
        self.assertEqual(ents[S1 + SEP + "#1"], (_bar(1), enc(_bar(1))), "the duplicate keeps a positional key")
        self.assertEqual(ents[S1 + SEP + "#2"], ({"t": 3}, enc({"t": 3})))
        self.assertEqual(ents[S1 + SEP + "#3"], ("str", '"str"'))
        self.assertEqual(ents[S2 + SEP], ([], "[]")); self.assertEqual(ents[S3 + SEP], (None, "null"))
        ents, order = km._delta_split("byid", [{"id": "a"}, {"id": "a"}, {"id": ""}, {"id": "#1"}, 5])
        self.assertEqual(order, ["a", "#1", "#2", "#3", "#4"])
        self.assertEqual(ents["#1"][0], {"id": "a"}); self.assertEqual(ents["#3"][0], {"id": "#1"}, "a real id spelling #1 is displaced, never overwritten")
        self.assertEqual(ents["#4"], (5, "5"))
        j = {"sid": S1, "t": 1, "judge": "j", "t1": 2}
        ents, order = km._delta_split("bykeys:sid,t,judge,t1", [j, dict(j), {"sid": S1, "t": 1, "judge": "j", "t1": 2.0}])
        self.assertEqual(order, [S1 + SEP + "1" + SEP + "j" + SEP + "2", "#1", S1 + SEP + "1" + SEP + "j" + SEP + "2.0"])
        ents, order = km._delta_split("dict", {"b": 1, "a": [2]})
        self.assertEqual(order, ["b", "a"]); self.assertEqual(ents["a"], ([2], "[2]"))
        with self.assertRaises(ValueError):
            km._delta_split("byid", None)
        with self.assertRaises(ValueError):
            km._delta_split("dictlist:id", {"la" + SEP + "ne": []})

    def test_the_keyer_is_built_once_per_split(self):
        # the perf claim itself: one _delta_keyer call per _delta_split, however many items — parsing the kind string
        # per item was a visible share of the per-entry pass on a board of thousands of bars
        calls = []; real = km._delta_keyer
        with mock.patch.object(km, "_delta_keyer", side_effect=lambda kind: calls.append(kind) or real(kind)):
            ents, order = km._delta_split("dictlist:id", {S1: [_bar(i) for i in range(50)], S2: [_bar(i) for i in range(50)]})
        self.assertEqual(len(order), 100); self.assertEqual(len(ents), 100)
        self.assertEqual(calls, ["dictlist:id"], "the kind is parsed once per split, not once per item")



if __name__ == "__main__":
    unittest.main()


class HandlerWiring(unittest.TestCase):
    """The handshake end to end, with the real handler: the shim asks for deltas and carries its page id, the
    connect handler records both, a needSlot is flagged for the pusher, and the shim reacts to a refused delta.
    A mutation review (2026-09-03) turned each of these off with every test still green."""

    def _fake_self(self, path):
        import io
        class FakeSelf:
            headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
            rfile = io.BytesIO(); wfile = io.BytesIO()
            connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
            close_connection = False
            def send_response(self, *a): pass
            def send_header(self, *a): pass
            def end_headers(self): pass
        FakeSelf.path = path
        return FakeSelf()

    def test_a_the_connect_handler_records_the_delta_flag_and_the_page_id(self):
        import contextlib, io
        got = []
        real_reg, real_recv = km._register_ws_client, km._ws_recv
        km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
        km._ws_recv = lambda rfile: (0x8, b"", True)              # the peer closes at once
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._ws(self._fake_self("/ws?app=feed&delta=1&iid=page-9&wid=w1"))
        finally:
            km._register_ws_client, km._ws_recv = real_reg, real_recv
            for c in got:
                if c in km._clients:
                    km._clients.remove(c)
        self.assertEqual(len(got), 1)
        c = got[0]
        self.assertTrue(c.get("delta")); self.assertEqual(c.get("iid"), "page-9"); self.assertEqual(c.get("wid"), "w1")
        self.assertEqual(c.get("app"), "feed")

    def test_b_a_needslot_is_flagged_for_the_pusher_and_wakes_it(self):
        client = _Client(); client["dstate"] = {"bars": {"rev": 3}}
        km._pusher_wake.clear()
        km.Handler._dispatch_ws(self._fake_self("/ws?app=timeline"), {"type": "needSlot", "slot": "bars"}, client)
        self.assertEqual(client.get("resync"), {"bars"})
        self.assertTrue(km._pusher_wake.is_set(), "the pusher is woken to re-base on its own thread")
        self.assertEqual(client["dstate"], {"bars": {"rev": 3}}, "the handler itself touches no held state")
        km.Handler._dispatch_ws(self._fake_self("/ws?app=timeline"), {"type": "needSlot", "slot": "nope"}, client)
        self.assertEqual(client.get("resync"), {"bars"}, "an unknown slot is ignored")

    def test_c_the_shim_asks_for_deltas_carries_its_page_id_and_asks_again_when_refused(self):
        js = km._shim("timeline", 1)
        self.assertIn('/ws?app=timeline&delta=1&iid="+encodeURIComponent(IID)', js)
        self.assertIn('send({type:"needSlot",slot:msg.slot})', js)
        self.assertIn("var IID=", js)
        self.assertNotIn('sessionStorage.setItem("romp:iid"', js, "the page id is never stored: a duplicated tab must not inherit it")
