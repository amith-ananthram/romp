#!/usr/bin/env python3
"""The kernel restart path (2026-09-11): the boot row carries the census and attach phases and waits for attachDone
(or a backstop); the producer's first pass waits, bounded, for the boot's attaches; the fold checkpoints are written
periodically for a session whose leaf moves with no settle evidence; the exit's cut row carries its phase timings and
the exit's assembly-document writes are bounded. Hermetic: a temp state root, no kernel, no sessions."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load (bin/romp-kernel resolves its state root at import)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_malloc_arena_cap", os.path.join(BIN, "romp-kernel"))


class MallocArenaCap(unittest.TestCase):
    """A kernel-only restart never sees the service unit's MALLOC_ARENA_MAX (the manager's environment is read at ITS start):
    2026-09-11 the kernel ran 125 arena heaps of 64 MiB, 12 GB resident over a 1 GiB record cache. The kernel caps its arenas
    from inside, before any thread exists."""

    def test_the_cap_is_applied_before_the_first_module_load(self):
        src = open(km.__file__).read()
        self.assertLess(src.index("_MALLOC_ARENAS_CAPPED = _cap_malloc_arenas()"), src.index("load_source("),
                        "mallopt(M_ARENA_MAX) must run before any module of ours could start a thread")

    def test_an_environment_that_sets_the_cap_wins(self):
        with mock.patch.dict(os.environ, {"MALLOC_ARENA_MAX": "4"}):
            self.assertFalse(km._cap_malloc_arenas())

    @unittest.skipUnless(sys.platform.startswith("linux"), "glibc mallopt")
    def test_the_cap_applies_on_glibc(self):
        env = {k: v for k, v in os.environ.items() if k != "MALLOC_ARENA_MAX"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(km._cap_malloc_arenas(2), "mallopt(M_ARENA_MAX, 2) returned failure")


if __name__ == "__main__":
    unittest.main()
