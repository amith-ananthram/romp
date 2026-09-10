#!/usr/bin/env python3
"""deliver() stamps `from_host` on EVERY messages.jsonl "sent" row — "" for local delivery, the origin
host for relayed mail — so a row says outright which it is. Written only when set before, a local row
and a relayed row from before the field existed (2026-07-20..26) had the same shape, and the kernel's
postal card read both as "" = this kernel's own: a pre-field remote sender was presented as local, and
the chat linked its PR references against a local homonym's repository (review find, 2026-09-06). Now
a row WITHOUT the key is one from before the stamp, and the card carries no host for it (the kernel
side: tests/test_github_repo.py PostalCardSenderHost). Synthetic ids and TESTHOST only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)                 # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_from_host_row", os.path.join(BIN, "romp-postal-service"))

TO = "11111111-2222-3333-4444-555555555555"
FROM = "66666666-7777-8888-9999-aaaaaaaaaaaa"


def _sent_rows():
    p = pm.TLDIR / "messages.jsonl"
    if not p.exists():
        return {}
    return {r["id"]: r for r in (json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()) if r.get("ev") == "sent"}


class FromHostOnEveryRow(unittest.TestCase):
    def test_local_delivery_says_local_outright(self):
        mid = pm.deliver(TO, "api", FROM, "the notes-api tests are green")
        row = _sent_rows()[mid]
        self.assertIn("from_host", row, "the key is written, not left to absence")
        self.assertEqual(row["from_host"], "")

    def test_relayed_mail_keeps_its_origin_host(self):
        mid = pm.deliver(TO, "api", FROM, "please review #12", from_host="TESTHOST")
        self.assertEqual(_sent_rows()[mid]["from_host"], "TESTHOST")

    def test_a_caller_passing_none_still_writes_the_empty_string(self):
        mid = pm.deliver(TO, "api", FROM, "hello", from_host=None)
        self.assertEqual(_sent_rows()[mid]["from_host"], "")

    def test_the_header_block_is_unchanged_for_local_mail(self):
        # the maildir header carries X-From-Host only when there is a host; the log row is the durable record
        mid = pm.deliver(TO, "api", FROM, "hello again")
        msgs = {m["id"]: m for m in pm.read_box(TO, consume=False)}
        self.assertEqual(msgs[mid]["from_host"], "", "read_box's default for a header that is absent")


if __name__ == "__main__":
    unittest.main()
