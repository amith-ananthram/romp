#!/usr/bin/env python3
"""One delivery, two messages: the sender, the message id and the declared kind must describe the
SAME message.

A drain concatenates every pending message into one injected text — postal_service's format_inbox
and format_push both loop over the batch, writing an id marker and a kind marker per message — so a
two-message delivery carries two of each. Three separate scans of that text used to pick three
different answers: author_of took the LAST marker (the trailing one is the real sender's, since a
forwarded body can carry an earlier one), while the judge's _seg_peer and _seg_peer_kind each did
their own leftmost regex search. The result paired one peer's identity with another peer's message
id and kind, so the delegation-tracking node was planted on the wrong session's board, keyed on a
message that session never sent — and the real delegator got no link back at all.

The three now agree by construction: author_of publishes the marker it resolved, and the judge reads
that rather than re-scanning. Synthetic only (placeholder UUIDs, invented text, hostname TESTHOST).
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_multimark", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_multimark", os.path.join(BIN, "romp-judge"))

BOB, ALICE = "sid-bob-1111", "sid-alice-2222"
MID_BOB = "1781100000.111_222.TESTHOST"
MID_ALICE = "1781100050.333_444.TESTHOST"
INDEX = {MID_BOB: BOB, MID_ALICE: ALICE}


def drain_text():
    """What the bus injects when two messages are pending — format_inbox's shape: a banner and body
    per message, each followed by its own id and kind markers."""
    return (
        "#################### \n## \U0001F4EC from bob\n#################### \n"
        "take the migration, it is yours\n"
        "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->\n"
        "#################### \n## \U0001F4EC from alice\n#################### \n"
        "which auth method did we settle on?\n"
        "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: question -->\n"
        % (MID_BOB, MID_ALICE))


def _seg(text):
    """A one-atom segment whose trigger carries the delivered text, authored the way the kernel
    authors it — which is the point: the judge must read the author's marker, not rescan."""
    # The atom shape the judge actually reads: _atom_text pulls message.content[].text.
    atom = {"uuid": "u1", "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            "author": em.author_of([{"type": "text", "text": text}], "sdk", INDEX, sdk_human=True)}
    return {"atoms": [atom], "trigger": "u1"}


class OneDeliveryTwoMessages(unittest.TestCase):
    def test_pairs_are_read_in_delivery_order(self):
        self.assertEqual(em.postal_pairs(drain_text()),
                         [(MID_BOB, "delegate"), (MID_ALICE, "question")])

    def test_a_kind_binds_to_the_id_it_follows(self):
        # The markers carry no cross-reference; position is the only pairing. A kind must never
        # attach to a later id, or bob's delegate would be read as alice's.
        pairs = em.postal_pairs(drain_text())
        self.assertEqual(dict(pairs)[MID_BOB], "delegate")
        self.assertEqual(dict(pairs)[MID_ALICE], "question")

    def test_an_id_with_no_declared_kind_stays_empty(self):
        # CLI mail (`romp mail send`) declares no kind; it must not inherit the next message's.
        text = ("first\n<!-- romp-msg-id: %s -->\n"
                "second\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: question -->\n"
                % (MID_BOB, MID_ALICE))
        self.assertEqual(em.postal_pairs(text), [(MID_BOB, ""), (MID_ALICE, "question")])

    def test_sender_id_and_kind_all_name_the_same_message(self):
        """The regression. Whichever message the author is filed under, the id and the kind must be
        that same message's — never a blend of two."""
        seg = _seg(drain_text())
        peer, mid = jd._seg_peer(seg)
        kind = jd._seg_peer_kind(seg)
        self.assertEqual(peer, ALICE, "the trailing marker is the real sender's")
        self.assertEqual(mid, MID_ALICE, "the id must be the SAME message's, not the first in the batch")
        self.assertEqual(kind, "question", "the kind must be the SAME message's too")
        self.assertEqual(INDEX[mid], peer, "the id resolves back to the peer it was filed under")

    def test_an_empty_declared_kind_is_a_value_not_a_missing_marker(self):
        """`romp mail send` leaves --kind optional, so a resolved message legitimately carries "".
        Reading that as "no marker on the author" sent the lookup back to a rescan, which returned
        a DIFFERENT message's kind — and a coordinate/question read off the wrong message files the
        segment fyi with no courier call at all, so a real handover in it is never tracked.

        The second message here is unresolvable on purpose: mail sent from a plain terminal has no
        session identity, so its row is skipped when the postal index is built."""
        text = ("take the migration\n<!-- romp-msg-id: %s -->\n"
                "which auth method?\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: question -->\n"
                % (MID_BOB, MID_ALICE))
        atom = {"uuid": "u1", "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                "author": em.author_of([{"type": "text", "text": text}], "sdk",
                                       {MID_BOB: BOB}, sdk_human=True)}   # ALICE deliberately absent
        seg = {"atoms": [atom], "trigger": "u1"}
        peer, mid = jd._seg_peer(seg)
        self.assertEqual((peer, mid), (BOB, MID_BOB))
        self.assertEqual(jd._seg_peer_kind(seg), "",
                         "an undeclared kind is that message's answer — never the other message's")

    def test_a_single_message_delivery_is_unchanged(self):
        text = ("just this one\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->\n" % MID_BOB)
        seg = _seg(text)
        self.assertEqual(jd._seg_peer(seg), (BOB, MID_BOB))
        self.assertEqual(jd._seg_peer_kind(seg), "delegate")

    def test_an_atom_with_no_marker_on_its_author_still_resolves(self):
        # Atoms built before the author carried the marker (a transcript already on disk) fall back
        # to the same last-marker rule, so a rebuild of old history stays coherent.
        seg = _seg(drain_text())
        seg["atoms"][0]["author"] = {"peer": ALICE}          # the pre-2026-08-05 shape
        self.assertEqual(jd._seg_peer(seg), (ALICE, MID_ALICE))
        self.assertEqual(jd._seg_peer_kind(seg), "question")


if __name__ == "__main__":
    unittest.main()
