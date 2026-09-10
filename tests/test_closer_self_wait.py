#!/usr/bin/env python3
"""The closer never files awaiting on SELF-work (the 2026-08-18 awaiting audit).

A session that says it will finish the remaining checks/cleanup ITSELF got a kindless awaiting
stamp — which stands the nudge ladder down and buys a six-hour sleep for work only the session's
own next turn can move (the awaited event already happened two minutes later and nothing lifted
the stamp). CLOSER_SYS now names the case as an omit: awaiting is only for waits that end with a
result ARRIVING from outside; self-work stays working so the normal follow-through cadence
produces the next turn. Prompt pins, matching the existing closer-rule test idiom."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_selfwait", os.path.join(BIN, "romp-judge"))


class CloserSelfWait(unittest.TestCase):
    def test_self_work_is_named_as_an_omit(self):
        self.assertIn("work the assistant says it will do NEXT ITSELF", jd.CLOSER_SYS)
        self.assertIn("only its own next turn moves it, so omit it", jd.CLOSER_SYS)
        self.assertIn("waits that end with a result ARRIVING from outside", jd.CLOSER_SYS)

    def test_the_omit_default_still_stands(self):
        self.assertIn("When unsure between awaiting and omitting, omit", jd.CLOSER_SYS)


if __name__ == "__main__":
    unittest.main()
