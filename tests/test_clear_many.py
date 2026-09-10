#!/usr/bin/env python3
"""askClearMany (2026-09-08): a multi-card clear gesture is ONE kernel batch, so one UndoClear restores it whole.

The feed's session-header Clear and its ask-group Clear remove N cards in one click and cache them as one
client-side Undo batch. Posted as N single askClear ops they became N kernel batches (_clear_all stamps one
t per call, and _undo_clear restores only the ids at the newest stamp), so an Undo brought back only the
last-posted card while the client had restored all N: N-1 phantom cards the kernel kept archived. The new
op hands _clear_all the whole list.

Synthetic sids (placeholder uuids) and node ids; hermetic state root; the socket client is a recording dict,
the views-dirty hook a counter, the chat send a recorder."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_clear_many", os.path.join(BIN, "romp-kernel"))

SID_A = "11111111-2222-3333-4444-555555555555"
IDS = [SID_A + ":g1", SID_A + ":g2", SID_A + ":g3"]


class ClearMany(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.jd.STATE, km._mark_views_dirty, km._send_to_app)
        km.jd.STATE = Path(self.td.name)
        self.dirty, self.chat, self.sent = [], [], []
        km._mark_views_dirty = lambda: self.dirty.append(1)
        km._send_to_app = lambda app, m: self.chat.append((app, m))
        self.client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: self.sent.append(json.loads(raw))}

    def tearDown(self):
        km.jd.STATE, km._mark_views_dirty, km._send_to_app = self._saved
        self.td.cleanup()

    def rows(self):
        p = km.jd.STATE / "cleared.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def test_the_batch_is_one_stamp_and_one_undo_restores_it_whole(self):
        km.Handler._dispatch_ws(None, {"type": "askClearMany", "itemIds": IDS, "sid": SID_A}, self.client)
        clears = [r for r in self.rows() if r.get("op") == "clear"]
        self.assertEqual(sorted(r["id"] for r in clears), sorted(IDS))
        self.assertEqual(len({r["t"] for r in clears}), 1, "one batch, one stamp")
        self.assertEqual(self.dirty, [1])
        self.assertEqual(len(self.chat), 1, "one citation drop for the whole batch")
        app, m = self.chat[0]
        self.assertEqual((app, m["type"], m["itemId"]), ("chat", "dropCitation", IDS[0]))
        self.assertEqual(sorted(m["itemIds"]), sorted(IDS), "every member's subtree (the id itself with no store)")
        km._undo_clear()
        self.assertEqual(len(km._cleared_ids()), 0, "the whole gesture comes back")

    def test_n_single_clears_are_n_stamps_which_is_why_the_batch_op_exists(self):
        for iid in IDS:
            km.Handler._dispatch_ws(None, {"type": "askClear", "itemId": iid, "sid": SID_A}, self.client)
        clears = [r for r in self.rows() if r.get("op") == "clear"]
        self.assertEqual(len({r["t"] for r in clears}), 3, "three gestures, three stamps")
        km._undo_clear()
        self.assertEqual(len(km._cleared_ids()), 2, "one undo restores only the last-posted card")

    def test_an_empty_or_junk_list_clears_nothing_and_says_nothing(self):
        km.Handler._dispatch_ws(None, {"type": "askClearMany", "itemIds": [None, ""], "sid": SID_A}, self.client)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.chat, [])




class TwoKernelsClearAllEachTheirOwn(unittest.TestCase):
    """The board-wide Clear all reaches every attached kernel (T286): the client broadcasts it (federation.ts, pinned
    in ui/webview/feed-clear-all-broadcast.test.ts) and each kernel clears ITS OWN feed's cards and appends ITS OWN
    ledger rows, so an Undo on each side restores that side whole. Two state roots stand in for two kernels."""

    SID_B = "11111111-2222-3333-4444-666666666666"

    def setUp(self):
        self.roots = [tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()]
        self._saved = (km.jd.STATE, km._mark_views_dirty, km._send_to_app)
        km._mark_views_dirty = lambda: None
        km._send_to_app = lambda app, m: None

    def tearDown(self):
        km.jd.STATE, km._mark_views_dirty, km._send_to_app = self._saved
        for r in self.roots:
            r.cleanup()

    def _on(self, i):
        km.jd.STATE = Path(self.roots[i].name)
        km._CLEARED_MEMO["slot"] = None

    def _ledger(self, i):
        p = Path(self.roots[i].name) / "cleared.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def test_each_kernel_clears_its_own_cards_writes_its_own_rows_and_undoes_its_own_batch(self):
        a_ids, b_ids = IDS, [self.SID_B + ":g1", self.SID_B + ":g2"]
        self._on(0); km._clear_all(a_ids)                 # the broadcast lands on kernel A: its cards
        self._on(1); km._clear_all(b_ids)                 # ...and on kernel B: its cards
        self.assertEqual([r["id"] for r in self._ledger(0)], a_ids, "A's ledger carries A's rows only")
        self.assertEqual([r["id"] for r in self._ledger(1)], b_ids, "B's ledger carries B's rows only")
        self._on(0); self.assertEqual(set(km._cleared_ids()), set(a_ids), "the board on A shows none of A's cards")
        self._on(1); self.assertEqual(set(km._cleared_ids()), set(b_ids))
        # Undo, sent to both kernels by the client: each restores its own batch whole
        self._on(0); km._undo_clear()
        self._on(1); km._undo_clear()
        self._on(0); self.assertEqual(km._cleared_ids(), {}, "A restored")
        self._on(1); self.assertEqual(km._cleared_ids(), {}, "B restored")
        self.assertEqual([r.get("op") for r in self._ledger(0)][-3:], ["undo"] * 3, "A's undo rows name A's batch")
        self.assertNotIn(self.SID_B, "".join(r["id"] for r in self._ledger(0)), "A never learned of B's cards")


if __name__ == "__main__":
    unittest.main()
