"""Image-pin associations survive kernel restarts (the user 2026-08-28, catching history rewrite
AGAIN: the 2026-08-16 pin fix latched message→pin only in in-memory caches, so every routine
restart re-resolved old messages and re-pinned the file's CURRENT bytes). The first-ever resolve
now wins forever: each latch appends (uuid, target → pin id) to a per-sid jsonl sidecar under
mention-pins/assoc, consulted before any re-pin. The blob store's bound/eviction and the
evicted-pin → live-file fallback are untouched. Synthetic data only; hermetic state."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_pindur", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
# a real 1×1 PNG and a DIFFERENT 1×1 PNG (changed pixel) — content-addressing must tell them apart
PNG_A = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                      "01f15c4890000000d49444154789c62f8cfc0000000ffff0300000600"
                      "0557bfabd40000000049454e44ae426082")
PNG_B = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                      "01f15c4890000000d49444154789c62f80f040000ffff0300000600"
                      "0557bfabd40000000049454e44ae426082")


def _cold_restart_resolve_state():
    """What a kernel restart does to the resolve layer: every in-memory cache empties."""
    km._PATH_LINK_CACHE.clear()
    km._SPACE_PATH_CACHE.clear()
    km._PIN_ASSOC_MEMO.clear()          # the memo is a cache OF the sidecar — the FILE is the survivor


class PinDurability(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cwd = Path(self.td.name)
        self._saved_cwd_of = km._cwd_of
        km._cwd_of = lambda sid: str(self.cwd) if sid == SID else ""
        self.img = self.cwd / "plot.png"
        self.img.write_bytes(PNG_A)
        self._n = 0
        _cold_restart_resolve_state()
        for e in os.listdir(km._pin_dir()):          # a hermetic BLOB store per test — the module
            fp = km._pin_dir() / e                   # shares one XDG root across the whole file
            if fp.is_file():
                fp.unlink()
        af = km._pin_assoc_dir() / (SID + ".jsonl")   # and a hermetic sidecar per test
        af.unlink(missing_ok=True)

    def tearDown(self):
        km._cwd_of = self._saved_cwd_of
        _cold_restart_resolve_state()
        self.td.cleanup()

    def _uuid(self):
        self._n += 1
        return "aaaaaaa%d-1111-2222-3333-444444444444" % self._n

    def _resolve(self, uuid):
        md = "the plot is at %s" % self.img
        self.assertIsNotNone(km._path_links(md, SID, uuid, {}))
        return dict(km._path_pins(SID, uuid))

    def test_a_restart_must_not_rewrite_history(self):
        u = self._uuid()
        pins1 = self._resolve(u)
        self.assertEqual(len(pins1), 1, "the mention latched a pin")
        original = next(iter(pins1.values()))
        # the agent regenerates the plot under the same name…
        self.img.write_bytes(PNG_B)
        # …and the kernel restarts (routine here): every in-memory resolve cache is gone
        _cold_restart_resolve_state()
        pins2 = self._resolve(u)
        self.assertEqual(next(iter(pins2.values())), original,
                         "the old message ships the ORIGINAL pin id — the first-ever resolve wins forever")

    def test_the_runaway_clear_no_longer_orphans_associations(self):
        u = self._uuid()
        original = next(iter(self._resolve(u).values()))
        self.img.write_bytes(PNG_B)
        km._PATH_LINK_CACHE.clear()          # the 50k backstop's exact effect
        pins = self._resolve(u)
        self.assertEqual(next(iter(pins.values())), original)

    def test_unchanged_file_dedupes_to_one_blob_and_one_id(self):
        u1, u2 = self._uuid(), self._uuid()
        p1 = next(iter(self._resolve(u1).values()))
        _cold_restart_resolve_state()
        p2 = next(iter(self._resolve(u2).values()))
        self.assertEqual(p1, p2, "unchanged bytes = the same content-addressed id (covers 'use the file directly')")
        blobs = [e for e in os.listdir(km._pin_dir()) if e.endswith(".png")]
        self.assertEqual(len(blobs), 1, "…and exactly one stored blob")

    def test_a_new_message_after_the_change_pins_the_new_bytes(self):
        u1 = self._uuid()
        old = next(iter(self._resolve(u1).values()))
        self.img.write_bytes(PNG_B)
        u2 = self._uuid()
        new = next(iter(self._resolve(u2).values()))
        self.assertNotEqual(old, new, "history is pinned; the PRESENT is always the live file's bytes")

    def test_the_sidecar_is_the_survivor_and_a_torn_line_loses_one_row_only(self):
        u = self._uuid()
        original = next(iter(self._resolve(u).values()))
        f = km._pin_assoc_dir() / (SID + ".jsonl")
        self.assertTrue(f.exists(), "the association is on disk, not only in memory")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write('{"u": "torn')        # a crash mid-append
        _cold_restart_resolve_state()
        pins = self._resolve(u)
        self.assertEqual(next(iter(pins.values())), original, "the intact rows still load")
