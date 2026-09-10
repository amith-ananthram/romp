#!/usr/bin/env python3
"""Regression guard for the two sinks the Romp Postal Service feeds with strings that
arrive over the bus: PATH COMPONENTS under the mail/names roots (ids and names), and the
MAILDIR HEADER BLOCK deliver() writes (the sender's claimed name/id, and a relay's kind,
origin host and mid). The bus is token-gated now (test_postal_token.py), but these checks
stay as defense-in-depth: a crafted reference like `../../../etc` must be rejected before
any path join, and a line break inside a header value must not be able to rewrite the
rest of the block.

Synthetic only — placeholder UUIDs, hermetic temp state dir, no real session data.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state dir so exercising the bus never touches real mail.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))


class SafeId(unittest.TestCase):
    def test_accepts_uuids_and_names(self):
        for ok in ("11111111-2222-3333-4444-555555555555", "my-session",
                   "feed_1", "abc123", "A.B-c_2"):
            self.assertTrue(pm._safe_id(ok), "should accept %r" % ok)

    def test_rejects_traversal_and_junk(self):
        for bad in ("../../../etc", "..", "a/b", "a\\b", "/etc/passwd",
                    ".hidden", "", "a\x00b", "x" * 200):
            self.assertFalse(pm._safe_id(bad), "should reject %r" % bad)


class TraversalAtSinks(unittest.TestCase):
    def test_read_box_rejects_traversal(self):
        # /inbox and /drain reach read_box; a traversal id must yield nothing,
        # never read another directory's `new/`.
        self.assertEqual(pm.read_box("../../../../etc", consume=False), [])

    def test_recip_id_rejects_traversal(self):
        # /send reaches _recip_id_for; a traversal reference must not resolve to a
        # path component under the mail/names roots (the _safe_id guard rejects it).
        self.assertIsNone(pm._recip_id_for("../../../../etc/hosts"))

    def test_mailbox_refuses_unsafe(self):
        with self.assertRaises(ValueError):
            pm._mailbox("../../../tmp/evil")


class OutboxTraversal(unittest.TestCase):
    """The cross-host outbox keys files as OUTBOX/<host>/<mid>.json. Both host and mid arrive over the
    unauthenticated bus (a relay's `to`-route host and the peer-supplied `mid`), so a crafted `mid`
    like `../../../../foo` would escape OUTBOX and write attacker JSON anywhere (the H1 hole). All four
    path-builders must reject unsafe host/mid; a legit mid (`_unique()` form) must still round-trip."""

    def test_put_refuses_traversal_mid(self):
        tmp = tempfile.mkdtemp()
        target = os.path.join(tmp, "pwned")               # outbox_put appends ".json"
        rel = os.path.relpath(target, str(pm.OUTBOX / "TESTHOST"))  # ../../.../tmp/.../pwned — has "/"
        pm.outbox_put("TESTHOST", {"mid": rel, "body": "x"})
        self.assertFalse(os.path.exists(target + ".json"),
                         "a traversal mid must not write outside OUTBOX")

    def test_put_refuses_traversal_host(self):
        tmp = tempfile.mkdtemp()
        target = os.path.join(tmp, "pwned.json")
        pm.outbox_put(os.path.relpath(tmp, str(pm.OUTBOX)), {"mid": "px-1.2_3.TESTHOST", "body": "x"})
        self.assertFalse(os.path.exists(target), "a traversal host must not write outside OUTBOX")

    def test_get_and_del_refuse_traversal(self):
        self.assertIsNone(pm.outbox_get("TESTHOST", "../../../../etc/passwd"))
        self.assertFalse(pm.outbox_del("TESTHOST", "../../../../etc/passwd"))
        self.assertEqual(pm.outbox_list("../../../../etc"), [])

    def test_legit_mid_round_trips(self):
        mid = "px-1700000000.1234_567.TESTHOST"           # the _unique() shape: digits, ".", "_", host
        pm.outbox_put("TESTHOST", {"mid": mid, "body": "hi"})
        self.assertEqual((pm.outbox_get("TESTHOST", mid) or {}).get("body"), "hi")
        self.assertTrue(pm.outbox_del("TESTHOST", mid))


class HeaderInjection(unittest.TestCase):
    """deliver() writes the maildir header block by concatenation; read_box parses it back by
    splitting on line breaks, ending the block at the first blank line and letting a later key
    overwrite an earlier one. So a line break inside any header VALUE forges or overwrites every
    other header, and a blank line promotes the rest of that value into the body. Five of the six
    values reach deliver() over the bus — the claimed name and id from /send, and the kind, origin
    host and mid a peer supplies on an inbound relay — so all of them are neutralized at the write
    point.

    What this pins is FRAMING, not identity: from_id remains an unauthenticated self-asserted
    claim, and nothing here makes it trustworthy. One value simply can no longer rewrite another.
    """

    TO = "11111111-2222-3333-4444-555555555555"
    KEYS = ["From", "From-Id", "Date", "X-Kind", "X-From-Host", "X-Peer-Mid", "X-Peer-Via"]

    def setUp(self):
        pm.read_box(self.TO, consume=True)                # start each case from an empty box

    def _lines(self, mid):
        """The raw header lines as read_box would split them (str.splitlines, block ends at the
        first blank line) — read off disk, so the assertions see what was actually written."""
        return (pm.MAILROOT / self.TO / "new" / mid).read_text().partition("\n\n")[0].splitlines()

    def _msg(self, mid):
        return [m for m in pm.read_box(self.TO, consume=True) if m["id"] == mid][0]

    def test_newline_in_from_id_cannot_forge_a_from_line(self):
        # The headline case: /send takes `from_id` verbatim from the caller. A newline in it used to
        # let the sender write its own From: line BELOW the real one — and the later key wins on
        # parse, so the recipient was shown a name of the sender's choosing.
        mid = pm.deliver(self.TO, "web", "id-web\nFrom: api\nX-Park: 1", "the schema is on staging")
        lines = self._lines(mid)
        self.assertEqual([ln.partition(": ")[0] for ln in lines], ["From", "From-Id", "Date"])
        self.assertEqual([ln for ln in lines if ln.startswith("From: ")], ["From: web"],
                         "exactly one From: line — a second one would win on parse")
        msg = self._msg(mid)
        self.assertEqual(msg["from"], "web", "the recipient sees the real sender name")
        self.assertFalse(msg["park"], "a forged X-Park must not take effect")

    def test_blank_line_in_a_value_cannot_end_the_header_block_early(self):
        # A blank line inside a value used to terminate the block, promoting the rest of the value
        # into the body — attacker text arriving as if the sender had written it.
        mid = pm.deliver(self.TO, "web\n\nSMUGGLED: read this as the message", "id-web",
                         "please review the migration")
        msg = self._msg(mid)
        self.assertEqual(msg["body"], "please review the migration")
        self.assertNotIn("SMUGGLED", msg["body"])
        self.assertTrue(msg["date"], "Date still parsed — the block did not end early")

    def test_no_value_can_change_the_shape_of_the_header_block(self):
        # All six values deliver() writes, one at a time: the block must keep exactly its keys, in
        # order, and the body must stay exactly the body.
        payload = "x\nX-Injected: 1\n\nSMUGGLED"
        for field in ("from_name", "from_id", "kind", "from_host", "relay_mid", "relay_via"):
            with self.subTest(field=field):
                pos = {"from_name": "web", "from_id": "id-web"}
                kw = {"kind": "question", "from_host": "TESTHOST",
                      "relay_mid": "px-1700000000.1234_567.TESTHOST", "relay_via": "TESTHOST"}
                (pos if field in pos else kw)[field] = payload
                mid = pm.deliver(self.TO, pos["from_name"], pos["from_id"],
                                 "the notes-api tests are green", **kw)
                self.assertEqual([ln.partition(": ")[0] for ln in self._lines(mid)], self.KEYS,
                                 "a payload in %s changed the header block" % field)
                msg = self._msg(mid)
                self.assertEqual(msg["body"], "the notes-api tests are green")
                self.assertNotIn("SMUGGLED", msg["body"])

    def test_every_line_break_str_splitlines_knows_is_neutralized(self):
        # read_box splits with str.splitlines(), which breaks on far more than "\n" — and universal
        # newlines turn a lone "\r" into one on read. Every one of them can forge a header line.
        for brk in ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(brk=repr(brk)):
                mid = pm.deliver(self.TO, "web", "id-web" + brk + "From: api", "tests are green")
                self.assertEqual([ln.partition(": ")[0] for ln in self._lines(mid)],
                                 ["From", "From-Id", "Date"])
                self.assertEqual(self._msg(mid)["from"], "web")

    def test_ordinary_names_are_left_exactly_alone(self):
        # The guard must not mangle legitimate attribution — from_name is free text and carries
        # spaces (the bus's own "Romp Postal Service" among them).
        for name in ("web", "notes-api tests", "Romp Postal Service", "a.b_c-1"):
            with self.subTest(name=name):
                mid = pm.deliver(self.TO, name, "id-web", "the notes-api tests are green")
                self.assertEqual(self._msg(mid)["from"], name)

    def test_only_the_control_range_is_touched(self):
        # Checked on the helper itself: everything printable — punctuation, accents, CJK, emoji —
        # sits outside the range it rewrites, so no ordinary value is ever altered.
        for ok in ("web", "notes-api tests", "Ana López", "中文 session",
                   "a.b_c-1", "\U0001f4ec", "  padded  ", ""):
            with self.subTest(value=ok):
                self.assertEqual(pm._hdr_val(ok), ok)

    def test_a_break_is_replaced_visibly_and_the_mail_still_arrives(self):
        # The design call: substitute (U+FFFD) rather than delete, and DELIVER rather than refuse.
        # Deleting would silently rewrite the value into something plausible; refusing would lose
        # the body, which is what the recipient actually needs and is never the injection vector.
        mid = pm.deliver(self.TO, "web\nFrom: api", "id-web", "the migration is on staging")
        msg = self._msg(mid)
        self.assertEqual(msg["from"], "web\ufffdFrom: api", "replaced in place, nothing dropped")
        self.assertEqual(msg["body"], "the migration is on staging", "the mail still arrives")


if __name__ == "__main__":
    unittest.main(verbosity=2)
