#!/usr/bin/env python3
"""The judge-limit banner names WHO the window actually touches (the user 2026-08-28): the payload's
_judge_limit_view enriches the latch with the sessions billing the login account — read from the
authoritative per-session billing (the CLI's own authLive report, then the picked intent), with a
live session whose billing is unknowable (a tmux CLI) listed honestly, never silently omitted. All
fixtures SYNTHETIC."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_jlview", os.path.join(BIN, "romp-kernel"))

S_LOGIN = "aaaa2828-0000-0000-0000-000000000001"   # authLive says login
S_KEY = "aaaa2828-0000-0000-0000-000000000002"     # authLive says key → untouched by the window
S_INTENT = "aaaa2828-0000-0000-0000-000000000003"  # no init yet; the picked intent says login
S_TMUX = "aaaa2828-0000-0000-0000-000000000004"    # reports nothing → billing unknown


class JudgeLimitView(unittest.TestCase):
    def setUp(self):
        self._saved = (km._tmux_sessions, km._name_of, km.jd._limit_down)
        km._name_of = lambda sid: {S_LOGIN: "web", S_KEY: "api", S_INTENT: "tests",
                                   S_TMUX: "notes"}.get(str(sid))
        km._tmux_sessions = lambda: {
            S_LOGIN: {"authLive": "login", "auth": "key"},   # the CLI's own report OUTRANKS the intent
            S_KEY: {"authLive": "key", "auth": "login"},
            S_INTENT: {"authLive": "", "auth": "login"},
            S_TMUX: {},
        }
        km.jd._limit_down = lambda: {"bucket": "five_hour", "resets_at": 1_780_000_000, "pct": 100}

    def tearDown(self):
        (km._tmux_sessions, km._name_of, km.jd._limit_down) = self._saved

    def test_no_latch_no_view(self):
        km.jd._limit_down = lambda: None
        self.assertIsNone(km._judge_limit_view())

    def test_login_billed_sessions_ride_the_latch_key_billed_do_not(self):
        v = km._judge_limit_view()
        self.assertEqual([d["name"] for d in v["loginSessions"]], ["tests", "web"],
                         "authLive wins where it exists; the intent covers a pre-init session; sorted")
        self.assertEqual(v["loginSessions"][1],
                         {"name": "web", "host": "", "sid": S_LOGIN, "color": None},
                         "the ONE identity ladder (_peer_identity) — the banner wears the standard chip")
        self.assertNotIn("api", [d["name"] for d in v["loginSessions"]],
                         "a key-billed session is untouched by this window")

    def test_unreadable_billing_is_said_not_omitted(self):
        self.assertEqual([d["name"] for d in km._judge_limit_view()["billingUnknown"]], ["notes"],
                         "a tmux CLI reports nothing — the fail-loud rule lists it as unknown")

    def test_the_latch_fields_pass_through_intact(self):
        v = km._judge_limit_view()
        self.assertEqual((v["bucket"], v["resets_at"], v["pct"]), ("five_hour", 1_780_000_000, 100))


if __name__ == "__main__":
    unittest.main()
