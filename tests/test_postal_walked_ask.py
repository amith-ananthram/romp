#!/usr/bin/env python3
"""Kernel-walked-at-relay provenance rides the postal wire (the user 2026-08-27, T126): a
cross-host delegate's chain evidence lives on the ORIGIN machine's disk, so the receiving walk
rightly refuses the hop — but at send time the origin kernel holds everything, walks its own
chain, and the bus stamps the proof ({text, sid, host}, kernel-written, never agent prose) onto
the relayed mail. Pins: the /send helper degrades to nothing when the kernel can't answer; the
receiving deliver() whitelists the record into the recipient's ledger row; the trusted relay
branch passes it through; a directed-host hold keeps it and the approve replay re-delivers it.
SYNTHETIC fixtures only; hostname TESTHOST; private synthetic sids."""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_walkedask", os.path.join(BIN, "romp-postal-service"))

SND = "b66a0001-1111-4222-8333-000000000001"    # private synthetic sids — never the shared placeholder
RCP = "b66a0001-1111-4222-8333-000000000002"
ASK = "make the two graph views draw identically"
REC = {"text": ASK, "sid": SND, "host": "TESTHOST"}


class WalkHelper(unittest.TestCase):
    """_walk_root_record: one kernel HTTP ask, degrade-to-nothing — mail never blocks on it."""

    def setUp(self):
        self.saved = pm._kernel_post

    def tearDown(self):
        pm._kernel_post = self.saved

    def test_a_kernel_record_is_shaped_and_host_stamped(self):
        pm._kernel_post = lambda path, body, timeout=2: {"text": ASK, "sid": SND}
        rec = pm._walk_root_record(SND)
        self.assertEqual(rec["text"], ASK)
        self.assertEqual(rec["sid"], SND)
        self.assertTrue(rec["host"], "the proving kernel names itself")

    def test_no_kernel_or_no_record_enriches_nothing(self):
        pm._kernel_post = lambda path, body, timeout=2: None
        self.assertIsNone(pm._walk_root_record(SND))
        pm._kernel_post = lambda path, body, timeout=2: {}
        self.assertIsNone(pm._walk_root_record(SND))

    def test_the_relay_branch_attaches_it_to_delegates_only(self):
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        self.assertIn('if kind == "delegate":', src)
        self.assertIn('relay_msg["userAsk"] = ua', src,
                      "the walked record rides the outbox payload, never the body prose")


class DeliverRow(unittest.TestCase):
    """deliver() whitelists the record into the recipient-side ledger row — the surface the
    receiving courier reads (_postal_row)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._tl, self._md = pm.TLDIR, pm.MAILROOT
        pm.TLDIR = type(pm.TLDIR)(self.td.name)
        pm.MAILROOT = type(pm.MAILROOT)(self.td.name) / "mail"

    def tearDown(self):
        pm.TLDIR, pm.MAILROOT = self._tl, self._md
        self.td.cleanup()

    def _rows(self):
        p = pm.TLDIR / "messages.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_the_record_lands_whitelisted_on_the_row(self):
        pm.deliver(RCP, "web", SND, "please do the thing", kind="delegate",
                   from_host="TESTHOST", user_ask=dict(REC, junk="never-copied"))
        row = self._rows()[-1]
        self.assertEqual(row["userAsk"], REC, "a whitelisted copy — no foreign keys ride along")

    def test_absent_or_textless_records_write_nothing(self):
        pm.deliver(RCP, "web", SND, "hello", kind="coordinate")
        pm.deliver(RCP, "web", SND, "hello again", kind="delegate", user_ask={"text": "  "})
        for row in self._rows():
            self.assertNotIn("userAsk", row)


class RelayInPassesItThrough(unittest.TestCase):
    """The trusted relay branch hands the wire record to deliver(); a directed hold keeps it and
    the approve replay re-delivers it — the record survives every receive-side path."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (pm.deliver, pm.local_agents, pm._postal_off, pm.peer_seen_check,
                       pm.peer_seen_add, dict(pm.PEERS), pm.QUARANTINE, pm.local_agents_checked)
        self.delivered = []
        pm.deliver = lambda *a, **k: self.delivered.append((a, k)) or "m-local"
        pm.local_agents = lambda threads=False: [{"id": RCP, "name": "api", "remote": False}]
        # _relay_in rules from the CHECKED seam now (the one-snapshot honesty arms, 2026-08-31);
        # wrap the same stub rows with answered=True so the harness's world stays authoritative
        pm.local_agents_checked = lambda threads=False: (pm.local_agents(threads=threads), True)
        pm._postal_off = lambda sid: False
        pm.peer_seen_check = lambda mid: False
        pm.peer_seen_add = lambda mid: None
        pm.QUARANTINE = type(pm.QUARANTINE)(self.td.name) / "q"

    def tearDown(self):
        (pm.deliver, pm.local_agents, pm._postal_off, pm.peer_seen_check,
         pm.peer_seen_add, peers, pm.QUARANTINE, pm.local_agents_checked) = self._saved
        pm.PEERS.clear()
        pm.PEERS.update(peers)
        self.td.cleanup()

    def _mail(self):
        return {"mid": "px-1", "to": "api", "frm": "web", "frm_id": SND,
                "body": "please do the thing", "kind": "delegate", "userAsk": dict(REC)}

    def test_trusted_delivery_carries_the_record(self):
        pm.PEERS["TESTHOST"] = {"trust": "trusted", "up": True}
        verdict, _ = pm._relay_in("TESTHOST", self._mail())
        self.assertEqual(verdict, "ack")
        self.assertEqual(self.delivered[0][1].get("user_ask"), REC)

    def test_a_directed_hold_keeps_it_and_the_approve_replay_re_delivers(self):
        pm.PEERS["TESTHOST"] = {"trust": "directed", "up": True}
        verdict, _ = pm._relay_in("TESTHOST", self._mail())
        self.assertEqual(verdict, "ack")
        self.assertEqual(self.delivered, [], "directed → held, nothing injected")
        rec = pm.quarantine_get("px-1")
        self.assertEqual(rec.get("userAsk"), REC, "the hold keeps the provenance")
        ok, err = pm.quarantine_decide("px-1", "approve")
        self.assertTrue(ok, err)
        self.assertEqual(self.delivered[0][1].get("user_ask"), REC,
                         "the approve replay re-delivers with the proof intact")


if __name__ == "__main__":
    unittest.main()
