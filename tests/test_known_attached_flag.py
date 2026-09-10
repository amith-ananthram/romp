#!/usr/bin/env python3
"""The known store distinguishes hosts that were ATTACHED here from hosts it only remembers a
mail-trust tier for. A trust-only row (a relayed origin the user tiered) used to render as
"Previously attached", which reads as a past ssh session that never happened (2026-08-12, a hub
whose known row for a spoke came from the relay era). _known_note's attach/detach/check-in callers
stamp attached=True; the trust-only writers never touch it; absent-on-legacy reads as
not-proven-attached — the softer claim is the true one, and the popover labels it that way.

Synthetic hosts, hermetic state dir.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_knownflag", os.path.join(BIN, "romp-kernel"))


class AttachedFlag(unittest.TestCase):
    def setUp(self):
        with km._known_lock:
            self._saved = dict(km._known)
            km._known.clear()

    def tearDown(self):
        with km._known_lock:
            km._known.clear()
            km._known.update(self._saved)

    def _row(self, host):
        return next((r for r in km.list_known() if r["host"] == host), None)

    def test_a_trust_only_note_never_claims_an_attachment(self):
        km._known_note("TESTHOST", "trusted")              # the origin-trust writers' shape
        row = self._row("TESTHOST")
        self.assertIsNotNone(row)
        self.assertFalse(row.get("attached"), "a remembered tier is not a remembered tunnel")

    def test_an_attach_path_note_stamps_the_flag_and_trust_refreshes_keep_it(self):
        km._known_note("TESTHOST", "directed", attached=True)   # attach/detach/check-in writers
        self.assertTrue(self._row("TESTHOST").get("attached"))
        km._known_note("TESTHOST", "isolated")                  # a later trust-only refresh
        row = self._row("TESTHOST")
        self.assertEqual(row["trust"], "isolated")
        self.assertTrue(row.get("attached"), "a trust refresh must not erase the attachment record")

    def test_every_attach_path_caller_stamps_the_flag(self):
        # the writers are inline in attach/detach/checkin flows — pin them at the source, behavior
        # for the flag itself is above
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        import re
        # calls carry nested parens (bool(on), r.get(...)) — match to end of the single-line call
        stamped = re.findall(r"_known_note\(.*attached=True\)", src)
        self.assertGreaterEqual(len(stamped), 5,
                                "attach, alias-fold, detach, checkin_set and checkin_apply all stamp it")

    def test_the_flag_survives_a_store_round_trip(self):
        km._known_note("TESTHOST", "directed", attached=True)
        km._known_note("TESTHOST2", "trusted")
        with km._known_lock:
            km._known.clear()
        km._known_load()
        self.assertTrue(self._row("TESTHOST").get("attached"))
        self.assertFalse(self._row("TESTHOST2").get("attached"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
