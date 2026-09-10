#!/usr/bin/env python3
"""One bus, one PEER_STATE row (the user 2026-07-27): a machine reaches a fleet under TWO names — the
alias its kernel dials (the ssh-config name) and the hostname it declares about itself on its own
inbound dials — and name-keyed PEER_STATE then lists every remote session twice and makes bare names
ambiguous. Every exchange now carries `busId` (this bus process's identity, additive like `tier`);
the receiver files a known bus under its DIALABLE alias and drops the self-declared duplicate.

Synthetic only — hermetic temp state dir, placeholder hostnames, invented notes-domain sessions."""
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
ps = load_source("romp_postal_peer_identity", os.path.join(BIN, "romp-postal-service"))

REMOTE_BUS = "f" * 32   # the peer machine's busId, stable across both of its names


def _req(host, bus_id=None, presence=None):
    r = {"host": host, "epoch": 1, "proto": ps.PEER_PROTO,
         "presence": presence if presence is not None else [{"name": "api", "id": "sess-api"}],
         "holds": [], "relays": [], "acks": [], "bounces": [], "wait": False}
    if bus_id:
        r["busId"] = bus_id
    return r


class PeerIdentityFold(unittest.TestCase):
    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        ps.PEERS.clear()
        ps.PEER_STATE.clear()

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def _peer_alias(self):
        """The kernel-notified, dialable alias row + one exchange that stamps its busId."""
        ps.peer_update({"host": "boxalias", "port": 19999, "up": True, "trust": "trusted"})
        req = ps.build_exchange_request("boxalias", wait=False)
        ps.peer_exchange_apply("boxalias", req, dict(_req("boxalias", bus_id=REMOTE_BUS), tier="trusted"))

    def test_inbound_dial_under_self_declared_name_files_under_the_alias(self):
        self._peer_alias()
        resp, status = ps.peer_exchange_handle(_req("box-hostname", bus_id=REMOTE_BUS))
        self.assertEqual(status, 200)
        self.assertNotIn("box-hostname", ps.PEER_STATE, "the second name must not become a second row")
        self.assertIn("boxalias", ps.PEER_STATE)
        self.assertEqual([a["name"] for a in ps.PEER_STATE["boxalias"]["presence"]], ["api"],
                         "the inbound exchange's fresh presence lands on the alias row")

    def test_no_duplicate_sessions_in_the_agents_view(self):
        self._peer_alias()
        ps.peer_exchange_handle(_req("box-hostname", bus_id=REMOTE_BUS))
        names = [(h, a.get("name")) for h, st in ps.PEER_STATE.items() for a in st["presence"]]
        self.assertEqual(names, [("boxalias", "api")], "one bus, one presence row per session")

    def test_apply_drops_a_stale_self_declared_row(self):
        # The self-declared name arrived FIRST (inbound dial before this side ever attached), then the
        # user attached the alias: the dialer's next apply folds the stale hostname row away.
        ps.peer_exchange_handle(_req("box-hostname", bus_id=REMOTE_BUS))
        self.assertIn("box-hostname", ps.PEER_STATE, "unknown busId, no dialable twin — stands alone")
        self._peer_alias()
        self.assertNotIn("box-hostname", ps.PEER_STATE, "the alias attach folds the hostname row")
        self.assertIn("boxalias", ps.PEER_STATE)

    def test_relays_are_trust_judged_under_the_alias(self):
        # The user tiered the ALIAS trusted; mail arriving on the machine's own inbound dial (declared
        # hostname) must be judged under that alias, not quarantined under an unknown name.
        self._peer_alias()
        seen = {}
        orig = ps._relay_in
        ps._relay_in = lambda host, m, token_proven=False: (seen.setdefault("host", host), ("ack", None))[1]
        try:
            ps.peer_exchange_handle(dict(_req("box-hostname", bus_id=REMOTE_BUS),
                                         relays=[{"mid": "m1", "to": "web", "frm": "api", "body": "hi"}]))
        finally:
            ps._relay_in = orig
        self.assertEqual(seen.get("host"), "boxalias")

    def test_older_peer_without_busid_is_untouched(self):
        self._peer_alias()
        ps.peer_exchange_handle(_req("box-hostname"))   # no busId — pre-fold peer
        self.assertIn("box-hostname", ps.PEER_STATE, "no identity proof, no fold — behavior unchanged")
        self.assertIn("boxalias", ps.PEER_STATE)

    def test_two_dialable_names_are_left_to_the_kernel(self):
        # Both names have kernel-notified ports (a kernel-level duplicate): the bus must not pick a
        # winner — attach_remote's token dedupe owns that fix.
        ps.peer_update({"host": "boxalias", "port": 19999, "up": True, "trust": "trusted"})
        ps.peer_update({"host": "box-hostname", "port": 19998, "up": True, "trust": "directed"})
        req = ps.build_exchange_request("boxalias", wait=False)
        ps.peer_exchange_apply("boxalias", req, _req("boxalias", bus_id=REMOTE_BUS))
        ps.peer_exchange_handle(_req("box-hostname", bus_id=REMOTE_BUS))
        self.assertIn("boxalias", ps.PEER_STATE)
        self.assertIn("box-hostname", ps.PEER_STATE)

    def test_exchange_payloads_carry_this_bus_identity(self):
        req = ps.build_exchange_request("boxalias", wait=False)
        self.assertEqual(req["busId"], ps.BUS_ID)
        resp, _ = ps.peer_exchange_handle(_req("someone"))
        self.assertEqual(resp["busId"], ps.BUS_ID)


if __name__ == "__main__":
    unittest.main()
