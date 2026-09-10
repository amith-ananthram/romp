#!/usr/bin/env python3
"""Cross-host read receipts (the user 2026-07-28): a message relayed to a session on a peer host
showed "pending (not read yet)" forever — the recipient's bus logged the exec into ITS OWN
messages.jsonl and the sender's host never learned. Now the delivering bus stamps the receipt
route into the maildir headers (X-Peer-Mid/X-Peer-Via), reading the mail parks {mid, t} in a
durable readbox (outbox's mirror), and the exchange carries `reads`/`readAcks` additively back
to the origin, one forwarding hop max — where the exec finally joins the sent event.

Synthetic only — hermetic temp state dir, placeholder hostnames, invented notes-domain sessions."""
import json
import os
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_POSTAL_HOST"] = "TESTHOST"
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([{"id": "sess-web", "name": "web", "dir": "/tmp/notes-api",
                                    "state": "waiting", "working": ""}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = load_source("romp_postal_read_receipts", os.path.join(BIN, "romp-postal-service"))

_MIDS = iter("px-%05d.mail.peerbox" % i for i in range(10000))


def _relay(to="web", origin=None):
    m = {"mid": next(_MIDS), "to": to, "frm": "api", "frm_id": "sess-api",
         "body": "synthetic ping", "kind": "coordinate", "t": 5}
    if origin:
        m["origin"] = origin
    return m


def _req(host, relays=(), reads=(), read_acks=(), wait=False):
    return {"host": host, "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
            "relays": list(relays), "acks": [], "bounces": [],
            "reads": list(reads), "readAcks": list(read_acks), "wait": wait}


def _resp(host, **kw):
    r = {"host": host, "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
         "relays": [], "acks": [], "bounces": [], "reads": []}
    r.update(kw)
    return r


class _Base(unittest.TestCase):
    def setUp(self):
        # the sessions-file seam is read per call, so the import-time assignment above holds only until
        # another module's test changes it: bind this module's file for the duration of each test
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        ps.PEERS.clear()
        ps.PEER_STATE.clear()
        ps._peer_pending.clear()
        for d in (ps.READBOX, ps.MAILROOT):
            shutil.rmtree(d, ignore_errors=True)
        try:
            (ps.TLDIR / "messages.jsonl").unlink()
        except OSError:
            pass

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def _trusted_peer(self, host="boxalias"):
        ps.peer_update({"host": host, "port": 19999, "up": True, "trust": "trusted"})


class RecipientQueuesReceipt(_Base):
    """The host where the mail LANDS: reading it must park a receipt for the direct peer."""

    def test_reading_relayed_mail_queues_a_receipt_for_the_direct_peer(self):
        self._trusted_peer()
        m = _relay()
        resp, status = ps.peer_exchange_handle(_req("boxalias", relays=[m]))
        self.assertEqual(status, 200)
        self.assertIn(m["mid"], resp["acks"])
        got = ps.read_box("sess-web", consume=True)
        self.assertEqual(len(got), 1)
        recs = ps.readbox_list("boxalias")
        self.assertEqual([r["mid"] for r in recs], [m["mid"]])
        self.assertNotIn("origin", recs[0], "direct mail routes straight back — no origin stamp")
        self.assertFalse(recs[0].get("unread"))
        self.assertEqual([r["mid"] for r in ps.build_exchange_request("boxalias", wait=False)["reads"]],
                         [m["mid"]], "the next dial carries the receipt")

    def test_peeking_queues_nothing(self):
        self._trusted_peer()
        ps.peer_exchange_handle(_req("boxalias", relays=[_relay()]))
        ps.read_box("sess-web", consume=False)
        self.assertEqual(ps.readbox_list("boxalias"), [], "a peek is not a read")

    def test_forwarded_mail_stamps_the_origin(self):
        # boxalias only FORWARDED it: the receipt must carry the true origin so the hop relays it on.
        self._trusted_peer()
        ps.peer_update({"host": "farhost", "port": 19998, "up": True, "trust": "trusted"})
        m = _relay(origin="farhost")
        ps.peer_exchange_handle(_req("boxalias", relays=[m]))
        ps.read_box("sess-web", consume=True)
        recs = ps.readbox_list("boxalias")
        self.assertEqual([(r["mid"], r.get("origin")) for r in recs], [(m["mid"], "farhost")])

    def test_rolled_back_claim_supersedes_the_parked_read(self):
        self._trusted_peer()
        m = _relay()
        ps.peer_exchange_handle(_req("boxalias", relays=[m]))
        local_id = ps.read_box("sess-web", consume=True)[0]["id"]
        self.assertTrue(ps.restore("sess-web", local_id))
        recs = ps.readbox_list("boxalias")
        self.assertEqual([(r["mid"], bool(r.get("unread"))) for r in recs], [(m["mid"], True)],
                         "one file per mid — the retraction replaces the never-sent read")
        # ...and actually reading it later flips the file back to a read.
        ps.read_box("sess-web", consume=True)
        recs = ps.readbox_list("boxalias")
        self.assertEqual([(r["mid"], bool(r.get("unread"))) for r in recs], [(m["mid"], False)])

    def test_response_arrival_clears_request_carried_reads(self):
        self._trusted_peer()
        m = _relay()
        ps.peer_exchange_handle(_req("boxalias", relays=[m]))
        ps.read_box("sess-web", consume=True)
        req = ps.build_exchange_request("boxalias", wait=False)
        ps.peer_exchange_apply("boxalias", req, _resp("boxalias"))
        self.assertEqual(ps.readbox_list("boxalias"), [],
                         "the dialed side answered, so it processed the reads — no explicit ack needed")

    def test_confirmation_for_a_read_never_deletes_its_retraction(self):
        # The claim rolled back while the read was in flight: the ack that comes back confirms the
        # READ, but the file now says unread — it must survive to carry the retraction.
        self._trusted_peer()
        m = _relay()
        ps.peer_exchange_handle(_req("boxalias", relays=[m]))
        local_id = ps.read_box("sess-web", consume=True)[0]["id"]
        req = ps.build_exchange_request("boxalias", wait=False)   # carries the read
        ps.restore("sess-web", local_id)                          # supersedes it mid-flight
        ps.peer_exchange_apply("boxalias", req, _resp("boxalias"))
        recs = ps.readbox_list("boxalias")
        self.assertEqual([(r["mid"], bool(r.get("unread"))) for r in recs], [(m["mid"], True)])

    def test_approved_quarantine_mail_still_carries_the_receipt_route(self):
        ps.peer_update({"host": "boxalias", "port": 19999, "up": True, "trust": "directed"})
        m = _relay()
        ps.peer_exchange_handle(_req("boxalias", relays=[m]))
        self.assertEqual(ps.read_box("sess-web", consume=True), [], "directed mail is held, not delivered")
        ok, err = ps.quarantine_decide(m["mid"], "approve")
        self.assertTrue(ok, err)
        got = ps.read_box("sess-web", consume=True)
        self.assertEqual(len(got), 1)
        self.assertEqual([r["mid"] for r in ps.readbox_list("boxalias")], [m["mid"]])


class SenderAppliesReceipt(_Base):
    """The host that SENT the mail: an arriving receipt finally sets the exec its view joins on."""

    def _cross_host_sent(self):
        mid = next(_MIDS)
        ps._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": mid, "from": "web",
                                         "from_id": "sess-web", "to_id": "peer:boxalias",
                                         "toName": "boxalias:api", "body": "synthetic ask",
                                         "kind": "question"})
        ps._tl_append("messages.jsonl", {"t": 2, "ev": "relayed", "id": mid, "host": "boxalias"})
        return mid

    def test_inbound_read_marks_the_sent_message_read(self):
        self._trusted_peer()
        mid = self._cross_host_sent()
        self.assertIsNone(ps._sent_receipts("sess-web")[0]["exec"])
        ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": mid, "t": 7}]))
        rec = ps._sent_receipts("sess-web")[0]
        self.assertEqual(rec["exec"], 7)
        self.assertIn("read", ps.format_receipts([rec]))

    def test_unread_retraction_returns_the_receipt_to_pending(self):
        self._trusted_peer()
        mid = self._cross_host_sent()
        ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": mid, "t": 7}]))
        ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": mid, "t": 8, "unread": True}]))
        self.assertIsNone(ps._sent_receipts("sess-web")[0]["exec"])

    def test_dialer_applies_response_reads_and_acks_on_the_next_dial(self):
        self._trusted_peer()
        mid = self._cross_host_sent()
        req = ps.build_exchange_request("boxalias", wait=False)
        ps.peer_exchange_apply("boxalias", req, _resp("boxalias", reads=[{"mid": mid, "t": 7}]))
        self.assertEqual(ps._sent_receipts("sess-web")[0]["exec"], 7)
        nxt = ps.build_exchange_request("boxalias", wait=False)
        self.assertEqual(nxt["readAcks"], [{"mid": mid, "unread": False}])
        ps.peer_exchange_apply("boxalias", nxt, _resp("boxalias"))
        self.assertEqual(ps.build_exchange_request("boxalias", wait=False)["readAcks"], [],
                         "a delivered ack leaves the pending queue")

    def test_dialed_side_resends_until_the_readack_lands(self):
        self._trusted_peer()
        rec = {"mid": next(_MIDS), "t": 7}
        ps.readbox_put("boxalias", rec)
        first, _ = ps.peer_exchange_handle(_req("boxalias"))
        again, _ = ps.peer_exchange_handle(_req("boxalias"))
        self.assertEqual([r["mid"] for r in first["reads"]], [rec["mid"]])
        self.assertEqual([r["mid"] for r in again["reads"]], [rec["mid"]],
                         "a response can vanish in flight — never clear on send")
        acked, _ = ps.peer_exchange_handle(_req("boxalias", read_acks=[{"mid": rec["mid"], "unread": False}]))
        self.assertEqual(acked["reads"], [])


class ForwardHop(_Base):
    """A receipt for forwarded mail relays one hop backward, exactly like the ack did."""

    def test_origin_stamped_receipt_requeues_one_hop_backward(self):
        self._trusted_peer()
        ps.peer_update({"host": "srchost", "port": 19998, "up": True, "trust": "trusted"})
        mid = next(_MIDS)
        ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": mid, "t": 7, "origin": "srchost"}]))
        recs = ps.readbox_list("srchost")
        self.assertEqual([r["mid"] for r in recs], [mid])
        self.assertNotIn("origin", recs[0], "the stamp is stripped — one hop max, it can never loop")
        self.assertEqual(ps._sent_receipts("sess-web"), [], "nothing execs locally on the hop")

    def test_unknown_origin_is_dropped(self):
        self._trusted_peer()
        ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": next(_MIDS), "t": 7, "origin": "nosuch"}]))
        self.assertEqual(ps.readbox_list("nosuch"), [], "only toward a peer the kernel told us about")

    def test_origin_naming_ourselves_applies_locally(self):
        self._trusted_peer()
        mid = next(_MIDS)
        ps._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": mid, "from": "web",
                                         "from_id": "sess-web", "to_id": "peer:boxalias",
                                         "toName": "boxalias:api", "body": "b", "kind": "coordinate"})
        ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": mid, "t": 7, "origin": "TESTHOST"}]))
        self.assertEqual(ps._sent_receipts("sess-web")[0]["exec"], 7)


class FormatHonesty(_Base):
    """check_sent must say where a cross-host message actually is, not a blanket 'pending'."""

    def _row(self, **kw):
        r = {"to": "boxalias:api", "id": "px-1.mail.peerbox", "sent": 1, "exec": None,
             "recalled": None, "relayed": None, "bounced": None, "parked": None}
        r.update(kw)
        return r

    def test_states_read_delivered_parked_bounced(self):
        txt = ps.format_receipts([self._row(exec=7)])
        self.assertIn("read", txt)
        txt = ps.format_receipts([self._row(relayed=7)])
        self.assertIn("delivered", txt)
        self.assertIn("not read yet", txt)
        txt = ps.format_receipts([self._row(parked="boxalias")])
        self.assertIn("parked for boxalias", txt)
        self.assertNotIn("unreachable", txt, "an old bus omits the link bit — claim nothing about it")
        txt = ps.format_receipts([self._row(bounced=7)])
        self.assertIn("bounced", txt)
        txt = ps.format_receipts([self._row()])
        self.assertIn("pending (not read yet)", txt)

    def test_parked_labels_follow_the_link_state(self):
        # "(unreachable)" ONLY on an actual dial failure (the user 2026-08-24): a message queued
        # ahead of the next exchange on a HEALTHY link is normal transit, and labeling it
        # unreachable cried wolf on every ordinary cross-host send
        txt = ps.format_receipts([self._row(parked="boxalias", parkedUp=True)])
        self.assertIn("queued for relay to boxalias", txt)
        self.assertNotIn("unreachable", txt)
        txt = ps.format_receipts([self._row(parked="boxalias", parkedUp=False)])
        self.assertIn("parked for boxalias (unreachable) — delivers on reconnect", txt)


class RefusalReceipts(_Base):
    """A terminal bounced row that records a REFUSAL (nothing left this machine; no return note) must
    not read as "undeliverable, returned to you" — no note is coming (2026-09-08). Mutant: the
    refusal branch dropped → every bounce promises a note."""

    def _row(self, **kw):
        r = {"to": "boxalias:api", "id": "px-1.mail.peerbox", "sent": 1, "exec": None,
             "recalled": None, "relayed": None, "bounced": None, "parked": None}
        r.update(kw)
        return r

    def test_a_refusal_bounce_promises_no_return_note(self):
        for why in (ps.WHY_STOPPED_BEFORE_PUBLISH, ps.WHY_NOT_PARKED, ps.WHY_OUTBOX_UNREADABLE):
            txt = ps.format_receipts([self._row(bounced=7, bouncedWhy=why)])
            self.assertIn("refused — " + why, txt)
            self.assertNotIn("returned to you", txt, why)

    def test_a_peers_refusal_still_reads_as_returned(self):
        txt = ps.format_receipts([self._row(bounced=7, bouncedWhy="no live session named 'api' on boxalias")])
        self.assertIn("undeliverable, returned to you", txt, "a peer's bounce did come back as a note")
        txt = ps.format_receipts([self._row(bounced=7)])
        self.assertIn("undeliverable, returned to you", txt, "an older row with no why keeps the old line")

    def test_sent_receipts_carries_the_why(self):
        ps._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "px-r", "from": "web", "from_id": "sid-w",
                                         "to_id": "peer:boxalias", "toName": "boxalias:api", "body": "hi",
                                         "kind": ""})
        ps._tl_append("messages.jsonl", {"t": 2, "ev": "bounced", "id": "px-r", "host": "boxalias",
                                         "why": ps.WHY_NOT_PARKED})
        row = ps._sent_receipts("sid-w")[-1]
        self.assertEqual((row["bounced"], row["bouncedWhy"]), (2, ps.WHY_NOT_PARKED))


class ReceiptsNeverDropSilently(_Base):
    """_read_arrived reports whether the receipt APPLIED, and both halves of the exchange keep an
    unapplied one on the wire (review find, 2026-09-08): the dialed side names it in `readsKept`
    (additive) so the dialer keeps its record for the next request, and the dialer withholds its
    readAck so the dialed side re-sends. Before this readbox_put's new False was ignored: a
    forwarded receipt the store could not take was acked and gone, where the base's raised OSError
    had aborted the exchange and left it to retry. Mutants: the return ignored on either side."""

    def _break_the_log(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        saved = ps.TLDIR
        ps.TLDIR = Path(path) / "timeline"                 # under a regular file: the REAL append fails
        self.addCleanup(lambda: (setattr(ps, "TLDIR", saved), ps._TL_FAULT.__setitem__(0, False), os.unlink(path)))
        return saved

    def test_a_forward_the_store_cannot_take_is_named_kept_and_the_dialer_keeps_it(self):
        self._trusted_peer()
        ps.peer_update({"host": "srchost", "port": 19998, "up": True, "trust": "trusted"})
        mid = next(_MIDS)
        saved = ps.readbox_put
        ps.readbox_put = lambda h, r: False                # the readbox could not be written
        try:
            resp, status = ps.peer_exchange_handle(_req("boxalias", reads=[{"mid": mid, "t": 7, "origin": "srchost"}]))
        finally:
            ps.readbox_put = saved
        self.assertEqual(status, 200)
        self.assertEqual(resp["readsKept"], [{"mid": mid, "unread": False}], "named kept, not silently acked")
        self.assertEqual(ps.readbox_list("srchost"), [], "nothing was parked for the hop")
        # the dialer: its parked read, named kept by the response, survives for the next request
        ps.readbox_put("boxalias", {"mid": mid, "t": 7})
        req = ps.build_exchange_request("boxalias", wait=False)
        self.assertEqual([r["mid"] for r in req["reads"]], [mid])
        ps.peer_exchange_apply("boxalias", req, _resp("boxalias", readsKept=[{"mid": mid, "unread": False}]))
        self.assertEqual([r["mid"] for r in ps.readbox_list("boxalias")], [mid], "kept for the next request")
        ps.peer_exchange_apply("boxalias", req, _resp("boxalias"))
        self.assertEqual(ps.readbox_list("boxalias"), [], "a response that kept nothing clears it as before")

    def test_a_response_carried_read_that_cannot_apply_gets_no_readack(self):
        self._trusted_peer()
        mid = next(_MIDS)
        ps._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": mid, "from": "web", "from_id": "sess-web",
                                         "to_id": "peer:boxalias", "toName": "boxalias:api", "body": "b",
                                         "kind": "question"})
        good = self._break_the_log()
        ps.peer_exchange_apply("boxalias", _req("boxalias"), _resp("boxalias", reads=[{"mid": mid, "t": 7}]))
        self.assertEqual(ps._pending("boxalias")["readAcks"], [], "no ack for a receipt whose row did not land")
        ps.TLDIR = good                                    # the log writes again: the dialed side re-sent it
        ps._TL_FAULT[0] = False
        ps.peer_exchange_apply("boxalias", _req("boxalias"), _resp("boxalias", reads=[{"mid": mid, "t": 7}]))
        self.assertEqual(ps._pending("boxalias")["readAcks"], [{"mid": mid, "unread": False}])
        self.assertEqual(ps._sent_receipts("sess-web")[0]["exec"], 7)

    def test_read_arrived_reports_the_outcome(self):
        self._trusted_peer()
        ps.peer_update({"host": "srchost", "port": 19998, "up": True, "trust": "trusted"})
        mid = next(_MIDS)
        self.assertTrue(ps._read_arrived("boxalias", {"mid": "../nope", "t": 1}), "unaddressable: nothing a retry could change")
        self.assertTrue(ps._read_arrived("boxalias", {"mid": mid, "t": 1, "origin": "nosuch"}), "no peer owns it: dropped on purpose")
        self.assertTrue(ps._read_arrived("boxalias", {"mid": mid, "t": 1, "origin": "srchost"}), "forwarded and parked")
        self.assertEqual([r["mid"] for r in ps.readbox_list("srchost")], [mid])
        ps._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": mid, "from": "web", "from_id": "sess-web",
                                         "to_id": "peer:boxalias", "toName": "boxalias:api", "body": "b", "kind": ""})
        self.assertTrue(ps._read_arrived("boxalias", {"mid": mid, "t": 7}), "ours: the exec row landed")
        self._break_the_log()
        self.assertFalse(ps._read_arrived("boxalias", {"mid": mid, "t": 8}), "ours, but the row could not land")


if __name__ == "__main__":
    unittest.main()
