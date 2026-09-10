#!/usr/bin/env python3
"""The suite-wide model-catalog floor (tests/conftest.py, 2026-09-02): no test kernel fetches the
Models API. The kernel's lazy `_sdk()` build fires the T222 catalog refresh — an async GET on any
credential the process carries — so under pytest the switch is off for every test as a DEFENSIVE floor
(no test reached the network before it, but only because none carried a credential the fetch could
use; a developer's exported key must not change that), and it STAYS off across a test that pops it
(the catalog suite's own fetch tests do exactly that in setUp/tearDown, and a module-level pop would
otherwise hold for the rest of the run). Synthetic throughout; the kernel is loaded only to prove the
refresh is inert under the floor."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_catalog_floor", os.path.join(BIN, "romp-kernel"))


class CatalogFloor(unittest.TestCase):
    """Ordered on purpose (unittest runs methods by name): the first test pops the switch the way the
    catalog suite's tearDown does; the second proves the per-test re-assert put it back before the
    next test ran, and that the refresh does nothing under it."""

    def test_1_a_test_may_pop_the_switch(self):
        self.assertEqual(os.environ.get("ROMP_MODEL_CATALOG"), "off", "conftest's import-time floor")
        os.environ.pop("ROMP_MODEL_CATALOG", None)

    def test_2_the_floor_is_back_and_the_refresh_is_inert(self):
        self.assertEqual(os.environ.get("ROMP_MODEL_CATALOG"), "off",
                         "conftest's per-test re-assert must restore the switch a test popped")
        # Synchronous so a missing floor would show as True (a credential lookup and a stderr line),
        # never as a thread that outlives the assertion.
        self.assertFalse(km._refresh_model_catalog("floor probe", _async=False))
        self.assertFalse(km._catalog_status["inflight"])


if __name__ == "__main__":
    unittest.main()
