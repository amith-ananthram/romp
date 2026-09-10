"""A feed card's hover lights its source turns in the chat (glowTurns), but a turn outside the chat's resident
tail has no row to light: the chat ships only the newest WIRE_TAIL events and streams older history in on
demand. So the glow's group also carries each uuid's GLOBAL index in the chat payload and the payload's length
(T318b, 2026-09-10), and the pane marks the unloaded ones on its overview ruler's history strip. Pinned here:
the group builder reads the pusher's built payload, and every glow site in the socket handler goes through it.
SYNTHETIC fixtures only: a private synthetic sid, placeholder uuids."""
import inspect
import os
import tempfile
import unittest

# the state root is hermetic BEFORE any romp code loads (tests/test_state_isolation_order.py): under a bare unittest
# or script run the kernel would otherwise operate on the real state directory
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

from romp_load import load_source   # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_glow_hist", os.path.join(BIN, "romp-kernel"))

SID = "f318b001-1111-4222-8333-000000000001"


class GlowGroupsCarryPositions(unittest.TestCase):
    def setUp(self):
        self.saved = km._built_chat.pop(SID, None)

    def tearDown(self):
        km._built_chat.pop(SID, None)
        if self.saved is not None:
            km._built_chat[SID] = self.saved

    def test_a_group_carries_each_uuid_s_global_index_and_the_payload_length(self):
        evs = [{"uuid": "u%d" % i} for i in range(400)]
        evs[7] = {"uuid": "u6"}          # a multi-block atom: two events share one uuid; the FIRST is the row's position
        km._built_chat[SID] = ("sig", {"events": evs}, "", None)
        groups = km._glow_groups(SID, ["u6", "u380", "zz"])
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual((g["sid"], g["uuids"]), (SID, ["u6", "u380", "zz"]))
        self.assertEqual(g["idx"], {"u6": 6, "u380": 380}, "an unknown uuid has no position; a repeated one keeps its first")
        self.assertEqual(g["total"], 400)

    def test_no_built_payload_means_a_group_without_positions(self):
        self.assertEqual(km._glow_groups(SID, ["u1"]), [{"sid": SID, "uuids": ["u1"]}])

    def test_no_uuids_means_no_group(self):
        self.assertEqual(km._glow_groups(SID, []), [])

    def test_every_glow_site_in_the_socket_handler_builds_its_groups_here(self):
        src = inspect.getsource(km.Handler._dispatch_ws)
        self.assertEqual(src.count("_glow_groups("), 3, "the feed-card hover, the chat-dot hover and the timeline-bar hover")
        self.assertNotIn('{"sid": gsid, "uuids": uuids}', src)
        self.assertNotIn('{"sid": hsid, "uuids": uuids}', src)
        self.assertNotIn('{"sid": hsid, "uuids": seg_uuids}', src)


if __name__ == "__main__":
    unittest.main()
