#!/usr/bin/env python3
"""Kernel-built event timestamps carry an explicit UTC offset — never a naive wall clock.

The chat payload's `ts` strings (kernel `iso()`) are recovered client-side with Date.parse
(render.ts eventEpoch), which interprets an OFFSET-LESS string in the BROWSER's timezone. The old
`iso()` emitted the kernel box's naive local wall clock, so every rail stamp rendered at the
KERNEL's wall time on every viewer's screen — invisible while the box and the viewer shared a
timezone, wrong by the whole UTC offset the moment they didn't (found reading a UTC box's
dashboard from a laptop in another timezone). The transcript's own records were never affected: the CLI
writes '…Z' and parse_z reads the offset; only romp-built events took the naive path.

The round-trip test pins the semantics, not the spelling: format under a non-UTC process timezone,
parse the way the browser does, and require the original epoch back.
"""
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_tz", os.path.join(BIN, "romp-kernel"))
em = load_source("romp_event_model_tz", os.path.join(ROOT, "kernel", "event_model.py"))

EPOCH = 1781100000   # an arbitrary fixed instant


class IsoCarriesItsOffset(unittest.TestCase):
    def _with_tz(self, tz):
        old = os.environ.get("TZ")
        os.environ["TZ"] = tz
        time.tzset()
        self.addCleanup(lambda: (os.environ.update(TZ=old) if old else os.environ.pop("TZ", None),
                                 time.tzset()))

    def test_round_trips_regardless_of_the_boxes_timezone(self):
        # parse_z is the offset-honoring parse — the same semantics as the browser's Date.parse on
        # an offset-carrying string. Under a naive iso() this recovers a skewed epoch (the bug);
        # with the offset present it must be exact, whatever TZ the kernel process runs in.
        for tz in ("UTC", "America/New_York", "Asia/Tokyo"):
            self._with_tz(tz)
            s = km.iso(EPOCH)
            self.assertTrue(s.endswith("Z") or "+" in s,
                            "iso() must carry an explicit offset; got %r under TZ=%s" % (s, tz))
            self.assertEqual(em.parse_z(s), EPOCH, "round-trip skewed under TZ=%s: %r" % (tz, s))

    def test_matches_the_transcripts_own_form(self):
        # one wire format for event `ts`, whether the CLI wrote it or romp built it
        self._with_tz("America/New_York")
        self.assertRegex(km.iso(EPOCH), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_unparseable_input_still_returns_empty(self):
        self.assertEqual(km.iso(None), "")
        self.assertEqual(km.iso(float("nan")), "")


if __name__ == "__main__":
    unittest.main()
