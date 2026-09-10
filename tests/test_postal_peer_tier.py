#!/usr/bin/env python3
"""The exchange declares each side's trust tier (the user 2026-07-26): every peer-bus exchange carries
`tier` = how the sender holds the OTHER side's direct mail (bus `my_tier_of`), and each side stores the
peer's declaration as PEER_STATE theirTier — surfaced by peers_snapshot so the popover can show BOTH
directions of a trust pair (a half-open pair used to be invisible until mail quarantined). Display and
mirroring only: the delivery gate stays _relay_in's, receiver-evaluated, always.

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
ps = load_source("romp_postal_tier", os.path.join(BIN, "romp-postal-service"))


def _req(host, tier=None):
    r = {"host": host, "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
         "relays": [], "acks": [], "bounces": [], "wait": False}
    if tier:
        r["tier"] = tier
    return r


class TierDeclaration(unittest.TestCase):
    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        ps.PEERS.clear()
        ps.PEER_STATE.clear()

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def test_my_tier_of_matches_the_gate_resolution(self):
        self.assertEqual(ps.my_tier_of("STRANGER"), "trusted",
                         "no row + token-proven exchange partner = the gate's trusted default")
        ps.peer_update({"host": "HELD", "port": 1, "up": True, "trust": "directed"})
        self.assertEqual(ps.my_tier_of("HELD"), "directed")
        ps.peer_update({"host": "OPEN", "port": 2, "up": True, "trust": "trusted"})
        self.assertEqual(ps.my_tier_of("OPEN"), "trusted")

    def test_exchange_response_declares_our_tier_and_stores_theirs(self):
        ps.peer_update({"host": "BOXA", "port": 1, "up": True, "trust": "directed"})
        resp, status = ps.peer_exchange_handle(_req("BOXA", tier="trusted"))
        self.assertEqual(status, 200)
        self.assertEqual(resp["tier"], "directed", "the dialed side declares how it holds the dialer")
        self.assertEqual(ps.PEER_STATE["BOXA"]["theirTier"], "trusted",
                         "the dialer's declaration of how it holds US is stored")

    def test_request_carries_tier_and_apply_stores_the_responders(self):
        ps.peer_update({"host": "BOXB", "port": 1, "up": True, "trust": "trusted"})
        req = ps.build_exchange_request("BOXB", wait=False)
        self.assertEqual(req["tier"], "trusted")
        ps.peer_exchange_apply("BOXB", req, dict(_req("BOXB"), tier="directed", relays=[]))
        self.assertEqual(ps.PEER_STATE["BOXB"]["theirTier"], "directed")

    def test_older_peer_without_the_field_stores_nothing(self):
        ps.peer_update({"host": "OLDPEER", "port": 1, "up": True, "trust": "trusted"})
        ps.peer_exchange_handle(_req("OLDPEER"))
        self.assertNotIn("theirTier", ps.PEER_STATE["OLDPEER"])

    def test_peers_snapshot_merges_their_tier(self):
        ps.peer_update({"host": "BOXC", "port": 1, "up": True, "trust": "trusted"})
        ps.peer_exchange_handle(_req("BOXC", tier="directed"))
        snap = ps.peers_snapshot()
        self.assertEqual(snap["peers"]["BOXC"]["theirTier"], "directed")
        self.assertEqual(snap["peers"]["BOXC"]["trust"], "trusted",
                         "both directions ride one row: ours from PEERS, theirs from the exchange")


if __name__ == "__main__":
    unittest.main(verbosity=2)
