#!/usr/bin/env python3
"""A relayed message's timeline connector landed at the read-receipt time — the relay handoff — never
at the recipient's true process turn, because the join key (the RECIPIENT's own delivery mid, which is
what its transcript markers record) never flowed back to the sender (the user 2026-08-06). The read
receipt now carries it: read_box stamps dmid at consume, the backflow forwards it hop-by-hop, and
_read_arrived logs it beside the exec row so the sender's timeline payload can hand it to the merged
view for the exact turn join. Synthetic only — hermetic temp state, placeholder mids, TESTHOST."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([{"id": "sess-web", "name": "web", "dir": "/tmp/notes-api",
                                    "state": "waiting", "working": ""}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = load_source("romp_postal_dmid", os.path.join(BIN, "romp-postal-service"))


class ReceiptCarriesDeliveryMid(unittest.TestCase):
    def test_consuming_relayed_mail_queues_a_dmid_receipt(self):
        # relayed-in mail: deliver with the relay headers, then the session reads its box
        ps.deliver("sess-web", "api", "id-api", "please review the schema", kind="question",
                   from_host="TESTHOST", relay_mid="px-1111.2222_3.TESTHOST", relay_via="TESTHOST")
        out = ps.read_box("sess-web", consume=True)
        self.assertEqual(len(out), 1)
        dmid = out[0]["id"]                                  # the delivery mid = the maildir name = the marker id
        recs = list((ps.READBOX / "TESTHOST").glob("*.json"))
        self.assertEqual(len(recs), 1, "one receipt queued for the direct peer")
        rec = json.loads(recs[0].read_text())
        self.assertEqual(rec["mid"], "px-1111.2222_3.TESTHOST")
        self.assertEqual(rec["dmid"], dmid, "the receipt names the RECIPIENT-side delivery mid")

    def test_read_arrived_logs_the_dmid_beside_the_exec(self):
        ps._read_arrived("TESTHOST", {"mid": "px-9999.8888_7.TESTHOST", "t": 1786000000,
                                      "dmid": "1786000000.11111_22.TESTHOST"})
        rows = [json.loads(l) for l in
                (ps.TLDIR / "messages.jsonl").read_text().splitlines() if l]
        row = next(r for r in rows if r.get("id") == "px-9999.8888_7.TESTHOST")
        self.assertEqual(row["ev"], "exec")
        self.assertEqual(row["dmid"], "1786000000.11111_22.TESTHOST")

    def test_the_kernel_payload_hands_dmid_to_the_merged_view(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn('execd[o["id"]] = (o.get("t"), o.get("dmid"))', src)
        self.assertIn('row["dmid"] = ex_dmid', src, "the connector carries the join key to the view")
        view = open(os.path.join(os.path.dirname(BIN), "ui", "romp-timeline-view.js")).read()
        self.assertIn("midStart[mm.toId + '|' + (mm.dmid || '')]", view,
                      "the merged view joins on id OR dmid — remote lanes included")


if __name__ == "__main__":
    unittest.main()
