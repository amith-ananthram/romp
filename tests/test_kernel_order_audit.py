#!/usr/bin/env python3
"""The session-order AUDIT LOG (the user 2026-07-02): tabs/lanes still occasionally reorder themselves
and code reading has never found why, so the kernel instruments the order itself. Every mutation of
session-order.json (_write_session_order), every PERMUTED push (_push's tab_order check), and every
client-reported permutation (the orderAudit WS message) appends one JSON line to order-audit.jsonl with
the stack that made the change. Pure adds/drops are routine churn: recorded for persists, skipped for
pushes (only_permuted). SYNTHETIC sids only."""
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_orderaudit", os.path.join(BIN, "romp-kernel"))

A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"
C = "cccccccc-0000-0000-0000-000000000003"


class OrderAudit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)     # _order_audit_path resolves per call → redirected too

    def tearDown(self):
        km.jd.STATE = self._state
        self.td.cleanup()

    def records(self):
        p = km._order_audit_path()
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    # ── the permutation predicate ───────────────────────────────────────────────────────────────────
    def test_permuted_only_when_survivors_swap(self):
        self.assertTrue(km._order_permuted([A, B, C], [B, A, C]), "two survivors swapped → the bug")
        self.assertFalse(km._order_permuted([A, B], [A, B, C]), "a pure append is routine churn")
        self.assertFalse(km._order_permuted([A, B, C], [A, C]), "a pure drop is routine churn")
        self.assertFalse(km._order_permuted([A, C], [A, B, C]), "an insert keeps survivors' relative order")

    # ── persist-side: every session-order.json mutation is recorded, with a stack ──────────────────
    def test_write_session_order_logs_a_permutation_with_stack(self):
        km._write_session_order([A, B, C])
        km._write_session_order([B, A, C])
        recs = [r for r in self.records() if r["kind"] == "persist"]
        self.assertEqual(len(recs), 2, "the initial write (an add) AND the permutation both log")
        perm = recs[-1]
        self.assertTrue(perm["permuted"])
        self.assertEqual(perm["old"], [A, B, C])
        self.assertEqual(perm["new"], [B, A, C])
        self.assertIn("_write_session_order", perm["stack"], "the Python stack names the mutation path")

    def test_unchanged_write_logs_nothing(self):
        km._write_session_order([A, B])
        km._write_session_order([A, B])
        self.assertEqual(len(self.records()), 1, "a no-op rewrite is not an order change")

    def test_append_logs_as_unpermuted_change(self):
        km._write_session_order([A, B])
        km._write_session_order([A, B, C])
        rec = self.records()[-1]
        self.assertFalse(rec["permuted"])
        self.assertEqual(rec["added"], [C])
        self.assertEqual(rec["dropped"], [])

    # ── push-side: only permutations log (set churn on pushes is constant background noise) ────────
    def test_only_permuted_skips_routine_churn(self):
        km._order_audit("push", [A, B], [A, B, C], only_permuted=True)
        self.assertEqual(self.records(), [], "an appearing tab is not the bug")
        km._order_audit("push", [A, B, C], [B, A, C], only_permuted=True)
        recs = self.records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["kind"], "push")
        self.assertTrue(recs[0]["permuted"])

    def test_push_loop_feeds_the_audit(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn('_order_audit("push", _last_tab_order, tab_order, only_permuted=True)', src,
                      "_push checks every tab-order push against the previous one")
        self.assertIn("_last_tab_order[:] = tab_order", src)

    # ── client-side: a webview report lands in the SAME log, carrying its own JS stack ─────────────
    def test_client_report_keeps_its_js_stack(self):
        km._order_audit("client:chat-tabs", [A, B], [B, A], stack="Error: tab order permuted\n  at renderTabs")
        rec = self.records()[0]
        self.assertEqual(rec["kind"], "client:chat-tabs")
        self.assertIn("at renderTabs", rec["stack"], "the client's JS stack is stored verbatim")

    def test_ws_handler_routes_orderaudit_reports(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn('msg.get("type") == "orderAudit"', src, "the kernel accepts client order reports")

    # ── the log is bounded and its writer never raises ──────────────────────────────────────────────
    def test_audit_never_raises_on_garbage(self):
        km._order_audit("persist", None, [A])            # type garbage must not take down the write path
        km._order_audit("persist", [A], {"not": "a list"})

    def test_cap_drops_the_oldest_half(self):
        p = km._order_audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        filler = json.dumps({"ts": 0, "kind": "persist", "old": [], "new": [A]}) + "\n"
        p.write_text(filler * (km._ORDER_AUDIT_CAP // len(filler) + 10))
        km._order_audit("persist", [A, B], [B, A])
        lines = p.read_text().splitlines()
        self.assertLess(len(lines) * len(filler), km._ORDER_AUDIT_CAP * 0.8, "the oldest half was dropped")
        self.assertEqual(json.loads(lines[-1])["new"], [B, A], "the fresh record survives the trim")


if __name__ == "__main__":
    unittest.main()
