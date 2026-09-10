#!/usr/bin/env python3
"""One machine, one registry row (the user 2026-07-27, whose box sat in the registry as both its
hostname and its ssh alias — every remote session listed twice, bare postal names ambiguous). The
serve token IS the remote kernel's identity, so:
  - attach_remote absorbs an existing row under a DIFFERENT name when the fetched token matches,
    carrying the machine's trust level onto the surviving name;
  - checkin_apply files no second row when the handshaking machine is already ssh-attached here.

Synthetic only — hermetic temp STATE, placeholder hosts/tokens, ssh fully stubbed out."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_identity", os.path.join(BIN, "romp-kernel"))


class _Proc:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 1 if self.terminated else None

    def terminate(self):
        self.terminated = True


class RemoteIdentity(unittest.TestCase):
    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        self.tokens = {}
        self.bus_down = []
        km._fetch_remote_token = lambda h: self.tokens.get(h, "")
        km._remote_kernel_up = lambda h, p: True
        km._spawn_tunnel = lambda r: r.update(proc=_Proc(), status="starting", detail="")
        km._notify_bus_peer = (lambda host, port, up, tok="", trust="directed":
                               (self.bus_down.append(host) if not up else None) or True)

    def test_second_alias_absorbs_the_old_row(self):
        self.tokens = {"box-hostname": "TOK-SAME", "boxalias": "TOK-SAME"}
        km.attach_remote("box-hostname")
        old_proc = km._remotes["box-hostname"]["proc"]
        km.attach_remote("boxalias")
        self.assertEqual(set(km._remotes), {"boxalias"}, "one machine, one row")
        self.assertTrue(old_proc.terminated, "the absorbed row's tunnel is shut down")
        self.assertIn("box-hostname", self.bus_down, "the bus is told the old peer name is gone")
        self.assertNotIn("box-hostname", [k["host"] for k in km.list_known()],
                         "the popover must not re-offer the duplicate name")

    def test_absorb_carries_the_machines_trust_level(self):
        self.tokens = {"box-hostname": "TOK-SAME", "boxalias": "TOK-SAME"}
        km.attach_remote("box-hostname")
        km.set_trust("box-hostname", "trusted")
        km.attach_remote("boxalias")
        self.assertEqual(km._remotes["boxalias"]["trust"], "trusted",
                         "the level was chosen for the MACHINE; a rename must not drop it to directed")
        self.assertEqual(km.known_trust("boxalias"), "trusted", "and the surviving name remembers it")

    def test_an_explicitly_tiered_alias_keeps_its_own_level(self):
        self.tokens = {"box-hostname": "TOK-SAME", "boxalias": "TOK-SAME"}
        km.attach_remote("box-hostname")
        km.set_trust("box-hostname", "trusted")
        km._known_note("boxalias", "isolated")     # the user chose a level for THIS name before
        km.attach_remote("boxalias")
        self.assertEqual(km._remotes["boxalias"]["trust"], "isolated")

    def test_different_tokens_stay_separate(self):
        self.tokens = {"boxalias": "TOK-A", "otherbox": "TOK-B"}
        km.attach_remote("boxalias")
        km.attach_remote("otherbox")
        self.assertEqual(set(km._remotes), {"boxalias", "otherbox"})

    def test_checkin_from_an_ssh_attached_machine_files_no_second_row(self):
        self.tokens = {"boxalias": "TOK-SAME"}
        km.attach_remote("boxalias")
        body = {"host": "box-hostname", "kernelPort": 12345, "busPort": 12346, "token": "TOK-SAME"}
        payload, status = km.checkin_apply(body)
        self.assertEqual(status, 200, "an error would make the mobile retry forever with nothing to fix")
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("host"), "boxalias", "answered with the name it is known by here")
        self.assertEqual(set(km._remotes), {"boxalias"})

    def test_checkin_from_an_unknown_machine_still_lands(self):
        self.tokens = {"boxalias": "TOK-A"}
        km.attach_remote("boxalias")
        payload, status = km.checkin_apply(
            {"host": "mobile", "kernelPort": 12345, "busPort": 12346, "token": "TOK-OTHER"})
        self.assertEqual(status, 200)
        self.assertEqual(set(km._remotes), {"boxalias", "mobile"})

    def test_checkin_rename_replaces_its_own_old_row(self):
        payload, status = km.checkin_apply(
            {"host": "mobile-old", "kernelPort": 12345, "busPort": 12346, "token": "TOK-M"})
        self.assertEqual(status, 200)
        payload, status = km.checkin_apply(
            {"host": "mobile-new", "kernelPort": 12345, "busPort": 12346, "token": "TOK-M"})
        self.assertEqual(status, 200)
        self.assertEqual(set(km._remotes), {"mobile-new"}, "a renamed mobile keeps one row")


if __name__ == "__main__":
    unittest.main()
