#!/usr/bin/env python3
"""Per-host trust model, bus side (postal_service): the inbound gate in _relay_in holds mail from a
DIRECTED peer for human approval instead of injecting it, delivers a TRUSTED peer's mail as today, and
silently drops an ISOLATED peer's mail. The quarantine store + quarantine_decide (approve/deny) back the
feed's blocked card. peer_update carries the per-host trust the gate reads.

Synthetic only — hermetic temp state dir, placeholder mids, invented notes-domain sessions, no real data.
"""
import errno
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# One live local session ("web") via the sessions-file seam (no live kernel needed).
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([{"id": "sess-web", "name": "web", "dir": "/tmp/notes-api",
                                    "state": "waiting", "working": ""}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = load_source("romp_postal_quar", os.path.join(BIN, "romp-postal-service"))


def _relay(mid, body="ship it", frm="api", origin=None):
    m = {"mid": mid, "to": "web", "frm": frm, "frm_id": "id-" + frm, "body": body, "kind": "coordinate"}
    if origin:
        m["origin"] = origin
    return m


class InboundTrustGate(unittest.TestCase):
    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS   # pin OUR sessions seam (read live; a later-collected postal test clobbers it)
        # fresh peer table + empty stores each test
        ps.PEERS.clear()
        ps._REFUSAL_SAID.clear()                     # no refusal episode left open by an earlier test
        for d in (ps.QUARANTINE, ps.MAILROOT / "sess-web" / "new"):
            try:
                for f in d.glob("*"):
                    f.unlink()
            except OSError:
                pass

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def _set_trust(self, host, level, up=True):
        ps.peer_update({"host": host, "port": 47101, "up": up, "trust": level})

    def test_trusted_delivers(self):
        self._set_trust("TESTHOST", "trusted")
        verdict, _ = ps._relay_in("TESTHOST", _relay("q-trusted-1"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [], "trusted mail must NOT be quarantined")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("ship it" in (msg.get("body") or "") for msg in box),
                        "trusted mail must be delivered to the recipient's maildir")

    def test_directed_quarantines(self):
        self._set_trust("TESTHOST", "directed")
        verdict, _ = ps._relay_in("TESTHOST", _relay("q-directed-1"))
        self.assertEqual(verdict, "ack", "the sender is ack'd (stops resending) even though it's held")
        held = ps.quarantine_list()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["mid"], "q-directed-1")
        self.assertEqual(held[0]["to"], "web")
        self.assertEqual(held[0]["origin"], "TESTHOST")
        self.assertEqual(ps.read_box("sess-web", consume=False), [],
                         "directed mail must NOT reach the session until approved")

    def test_a_hold_that_could_not_be_written_answers_retry_not_ack(self):
        """_quarantine_put says False when the hold file could not be written (ENOSPC, a permission
        bit, a store path that is not a directory), and the directed arm ignored it: the sender was
        ack'd, deleted its record and read 'delivered' forever, the mid was marked seen so the
        re-relay was deduped away, and no hold existed for anyone to approve. The False is now read
        the way the trusted arm reads DeliveryNotRecorded: silence on the wire, nothing marked seen,
        so the sender keeps its record and the re-relay is held once the store writes again. The fault
        also reaches the USER, the way deliver() says a refused publish: one bell row per episode (the
        re-relay that meets the same store is said in the log only), re-armed by the next hold that lands."""
        self._set_trust("TESTHOST", "directed")
        blocker = ps.QUARANTINE.parent / "hold-blocker"
        blocker.parent.mkdir(parents=True, exist_ok=True)   # nothing creates the state dir at import
        blocker.write_text("")                       # a regular file where the store's parent must be
        saved, saved_log, saved_post, logged, told = ps.QUARANTINE, ps._log, ps._kernel_post, [], []
        ps.QUARANTINE = blocker / "quarantine"       # every mkdir/write under it fails with ENOTDIR
        ps._log = lambda line: logged.append(line)
        ps._kernel_post = lambda path, body, timeout=2: told.append((path, body)) or {"ok": True}
        try:
            # twice: the sender re-relays next exchange, and the store is still blocked
            verdicts = [ps._relay_in("TESTHOST", _relay("q-hold-fail")) for _ in range(2)]
        finally:
            ps.QUARANTINE, ps._log, ps._kernel_post = saved, saved_log, saved_post
            blocker.unlink()
        self.assertEqual(verdicts, [("retry", None)] * 2, "silence on the wire, both times: the sender keeps it parked and re-relays")
        cause = [l for l in logged if "q-hold-fail" in l and "could not be written" in l and "[Errno %d]" % errno.ENOTDIR in l]
        self.assertEqual(len(cause), 2, "the store says WHY the hold did not land, with the errno, on every refusal: %r" % logged)
        self.assertTrue(any("the sender re-relays" in l for l in logged), "and the arm says what follows: %r" % logged)
        notices = [b["text"] for path, b in told if path == "/postal-notice"]
        self.assertEqual(len(notices), 1, "the user hears it once per episode, not once per exchange: %r" % told)
        self.assertIn("q-hold-fail", notices[0])
        self.assertIn("could not be written", notices[0])
        self.assertIn("quarantine", ps._REFUSAL_SAID, "the episode stays open while the store is blocked")
        self.assertFalse(ps.peer_seen_check("q-hold-fail"), "not marked seen, so the re-relay is processed in full")
        self.assertEqual(ps.quarantine_list(), [], "nothing is held")
        self.assertEqual(ps.read_box("sess-web", consume=False), [], "and nothing reached the session")
        # the re-relay, with the store writing again, is held exactly as a first arrival would be
        self.assertEqual(ps._relay_in("TESTHOST", _relay("q-hold-fail")), ("ack", None))
        self.assertEqual([h["mid"] for h in ps.quarantine_list()], ["q-hold-fail"])
        self.assertTrue(ps.peer_seen_check("q-hold-fail"))
        self.assertNotIn("quarantine", ps._REFUSAL_SAID, "a hold that landed closes the episode: the next refusal is said again")

    def test_a_mid_no_hold_can_be_named_by_bounces_instead_of_retrying_forever(self):
        # The hold is a file named by the mid, so _quarantine_put also says False for an id that
        # cannot be a path component. That False must not read as 'retry': a peer that keeps
        # sending the crafted id would be re-relaying it every exchange. Final refusal instead,
        # and (as before) nothing held, nothing delivered; unlike before, not marked seen or ack'd.
        self._set_trust("TESTHOST", "directed")
        verdict, bounce = ps._relay_in("TESTHOST", _relay("../q-hold-crafted"))
        self.assertEqual(verdict, "bounce", "final: silence would have the sender re-relay it every exchange")
        self.assertEqual(bounce["mid"], "../q-hold-crafted")
        self.assertFalse(ps.peer_seen_check("../q-hold-crafted"))
        self.assertEqual(ps.quarantine_list(), [])
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_isolated_drops(self):
        self._set_trust("TESTHOST", "isolated")
        verdict, _ = ps._relay_in("TESTHOST", _relay("q-iso-1"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [], "isolated mail is dropped, not held")
        self.assertEqual(ps.read_box("sess-web", consume=False), [], "isolated mail is not delivered")

    def test_unknown_origin_defaults_to_directed(self):
        # No PEERS entry (a race before the kernel notify lands) → safe default: hold, never auto-inject.
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-unknown-1"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1)
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_origin_only_trust_row_governs_relayed_mail(self):
        # Trust-by-origin end to end (the user 2026-07-25): the user tiers a host they have NO
        # tunnel to (an origin-only, portless row); its mail arriving relayed through a hub is
        # judged by that tier — trusted injects instead of holding.
        self._set_trust("EDGE", "trusted")                      # the hub we ARE connected to
        ps.peer_update({"host": "ORIGIN", "trust": "trusted", "originOnly": True})
        verdict, _ = ps._relay_in("EDGE", _relay("q-origin-1", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(list(ps.QUARANTINE.glob("*.json"))), 0,
                         "an origin-only trusted tier delivers, no hold")

    def test_forwarded_origin_is_the_trust_key(self):
        # A 2-hop message carries m["origin"] = the true origin; the gate keys on it, not the direct peer.
        self._set_trust("EDGE", "trusted")       # the direct peer we received from
        self._set_trust("ORIGIN", "directed")    # the true origin — its level governs
        verdict, _ = ps._relay_in("EDGE", _relay("q-fwd-1", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1, "the ORIGIN's directed level must hold it")

    def test_a_forwarder_cannot_outrank_itself_by_stamping_a_trusted_origin(self):
        """The origin stamp is written BY the forwarder, so trust keyed on it alone is a claim the
        claimant makes about itself. A peer you tiered `directed` — one you attached but do not let
        drive your sessions — answers one exchange with origin set to a host you DID tier trusted,
        and its mail is auto-injected: pasted into a live session and entered. The names to guess
        are not secret either; presence gossip hands them out. A forwarded message is now capped at
        the forwarder's own tier, so a directed relay stays directed however it labels its cargo."""
        self._set_trust("EDGE", "directed")       # the peer we actually received from
        self._set_trust("ORIGIN", "trusted")      # the name it claims to be speaking for
        verdict, _ = ps._relay_in("EDGE", _relay("q-forge-1", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1,
                         "a directed forwarder's mail must hold, whatever origin it stamps")
        self.assertEqual(len(list((ps.MAILROOT / "sess-web" / "new").glob("*"))), 0,
                         "nothing may reach the session's mailbox")

    def test_an_isolated_forwarder_stays_dropped_however_it_labels_its_mail(self):
        # isolated is the strongest refusal the user can express: no communication at all. It must
        # not become a hold (visible, approvable) by stamping a trusted origin.
        self._set_trust("EDGE", "isolated")
        self._set_trust("ORIGIN", "trusted")
        verdict, _ = ps._relay_in("EDGE", _relay("q-forge-2", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 0, "isolated drops; it never even holds")
        self.assertEqual(len(list((ps.MAILROOT / "sess-web" / "new").glob("*"))), 0)

    def test_the_cap_never_promotes_a_forwarders_mail(self):
        # The cap is a floor-lowering, not a lookup swap: a trusted forwarder relaying a DIRECTED
        # origin's mail must still hold it (the origin's own tier still applies).
        self._set_trust("EDGE", "trusted")
        self._set_trust("ORIGIN", "directed")
        verdict, _ = ps._relay_in("EDGE", _relay("q-forge-3", origin="ORIGIN"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1)


class LeastTrust(unittest.TestCase):
    def test_the_more_restrictive_tier_wins_either_way_round(self):
        self.assertEqual(ps.least_trust("trusted", "directed"), "directed")
        self.assertEqual(ps.least_trust("directed", "trusted"), "directed")
        self.assertEqual(ps.least_trust("isolated", "trusted"), "isolated")
        self.assertEqual(ps.least_trust("trusted", "trusted"), "trusted")

    def test_an_unrecognised_tier_is_treated_as_directed_not_trusted(self):
        # A tier this build does not know must never read as permission.
        self.assertEqual(ps.least_trust("nonsense", "trusted"), "nonsense")
        self.assertEqual(ps.least_trust("isolated", "nonsense"), "isolated")


class TokenProvenDialerGate(InboundTrustGate):
    """The DIALED side of an exchange (token_proven=True): the dialer showed OUR serve token, which
    already grants full control of this machine, so holding its own mail protects nothing — the user's
    outgoing delegation to a machine they attached used to sit quarantined THERE (2026-07-26). The
    proof exempts only the unknown-origin default: an explicit tier still wins, and forwarded mail is
    still judged by its origin's tier. Inherits InboundTrustGate so every explicit-tier test above
    reruns with token_proven=True — trusted/directed/isolated rows must gate identically."""

    def setUp(self):
        super().setUp()
        ps._seen_ids = None                          # the inherited tests reuse their mids — reset the
        try:                                         # dedupe window so the rerun isn't swallowed as dupes
            ps.PEER_SEEN.unlink()
        except OSError:
            pass
        self._orig_relay_in = ps._relay_in
        ps._relay_in = lambda host, m, token_proven=False: self._orig_relay_in(host, m, token_proven=True)

    def tearDown(self):
        ps._relay_in = self._orig_relay_in
        super().tearDown()

    def test_unknown_origin_defaults_to_directed(self):
        # OVERRIDES the inherited default-hold test: with the dialer token-proven, unknown-origin
        # DIRECT mail delivers instead of holding — that is the point of this gate.
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-tok-1", body="from the attacher"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(ps.quarantine_list(), [], "a token-proven dialer's own mail is not held")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("from the attacher" in (m.get("body") or "") for m in box),
                        "it is delivered like trusted mail")

    def test_forwarded_unknown_origin_still_held(self):
        # The token proof covers the DIALER only: mail it forwarded from an unknown third machine
        # keeps the safe default — the third machine never proved anything.
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-tok-2", origin="FARBOX"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1, "forwarded mail is judged by its origin's tier")
        self.assertEqual(ps.read_box("sess-web", consume=False), [])

    def test_explicit_directed_row_still_holds(self):
        # A tier the user SET for the dialer outranks the token proof — an explicit hold is a choice.
        self._set_trust("MYSTERY", "directed")
        verdict, _ = ps._relay_in("MYSTERY", _relay("q-tok-3"))
        self.assertEqual(verdict, "ack")
        self.assertEqual(len(ps.quarantine_list()), 1)
        self.assertEqual(ps.read_box("sess-web", consume=False), [])


class ExchangeHandleIsTokenProven(unittest.TestCase):
    """peer_exchange_handle passes token_proven=True — it only runs for requests past the HTTP serve-token
    gate — so an attached machine's own relays deliver instead of quarantining on the dialed side."""

    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        ps.PEERS.clear()
        ps.PEER_STATE.clear()
        ps._seen_ids = None
        for d in (ps.QUARANTINE, ps.MAILROOT / "sess-web" / "new"):
            try:
                for f in d.glob("*"):
                    f.unlink()
            except OSError:
                pass
        try:
            ps.PEER_SEEN.unlink()
        except OSError:
            pass

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def test_handle_delivers_unknown_dialers_direct_relay(self):
        req = {"host": "MYSTERY", "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
               "relays": [{"mid": "q-hx-1", "to": "web", "frm": "api", "frm_id": "id-api",
                           "body": "checking the deploy", "kind": "coordinate"}],
               "acks": [], "bounces": [], "wait": False}
        resp, status = ps.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        self.assertIn("q-hx-1", resp["acks"])
        self.assertEqual(ps.quarantine_list(), [], "the dialed side does not hold the dialer's own mail")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("checking the deploy" in (m.get("body") or "") for m in box))

    def test_handle_stamps_senders_origin_host_on_delivered_mail(self):
        """Cross-host delivery stamps from_host = the sender's ORIGIN host (the forwarder's stamp when
        the mail hopped, else the dialing peer) — the only durable record of where a federated sender
        lives; the courier snapshots it into a planted goal's origin (the user 2026-07-26). It rides
        the maildir header AND the messages.jsonl "sent" row."""
        req = {"host": "MYSTERY", "epoch": 1, "proto": ps.PEER_PROTO, "presence": [], "holds": [],
               "relays": [{"mid": "q-hx-2", "to": "web", "frm": "signal", "frm_id": "id-signal",
                           "body": "apply the fix", "kind": "delegate"},
                          {"mid": "q-hx-3", "to": "web", "frm": "api", "frm_id": "id-api",
                           "body": "forwarded along", "kind": "coordinate", "origin": "FARHOST"}],
               "acks": [], "bounces": [], "wait": False}
        ps.peer_update({"host": "FARHOST", "port": 47102, "up": True, "trust": "trusted"})
        resp, status = ps.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        box = {m["body"]: m for m in ps.read_box("sess-web", consume=False)}
        self.assertEqual(box["apply the fix"]["from_host"], "MYSTERY", "direct relay → the dialer's host")
        self.assertEqual(box["forwarded along"]["from_host"], "FARHOST", "hopped mail → the TRUE origin")
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        sent = {r["from"]: r for r in rows if r.get("ev") == "sent" and r.get("from_host")}
        self.assertEqual(sent["signal"]["from_host"], "MYSTERY")
        self.assertEqual(sent["api"]["from_host"], "FARHOST")


class QuarantineDecide(unittest.TestCase):
    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS   # pin OUR sessions seam (see InboundTrustGate.setUp)
        ps.PEERS.clear()
        ps.peer_update({"host": "TESTHOST", "port": 47101, "up": True, "trust": "directed"})
        for d in (ps.QUARANTINE, ps.MAILROOT / "sess-web" / "new"):
            try:
                for f in d.glob("*"):
                    f.unlink()
            except OSError:
                pass

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def test_approve_delivers_and_clears(self):
        ps._relay_in("TESTHOST", _relay("q-appr-1", body="original text"))
        ok, err = ps.quarantine_decide("q-appr-1", "approve")
        self.assertTrue(ok, err)
        self.assertEqual(ps.quarantine_list(), [], "approved message leaves the hold")
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("original text" in (m.get("body") or "") for m in box),
                        "approve delivers the held message")
        approved = next(m for m in box if "original text" in (m.get("body") or ""))
        self.assertEqual(approved["from_host"], "TESTHOST",
                         "approve replays the trusted deliver, origin-host stamp included")

    def test_approve_with_edited_text(self):
        ps._relay_in("TESTHOST", _relay("q-appr-2", body="raw peer text"))
        ok, err = ps.quarantine_decide("q-appr-2", "approve", text="edited by the human")
        self.assertTrue(ok, err)
        box = ps.read_box("sess-web", consume=False)
        self.assertTrue(any("edited by the human" in (m.get("body") or "") for m in box))
        self.assertFalse(any("raw peer text" in (m.get("body") or "") for m in box),
                         "the edited text replaces the peer's original")

    def test_deny_drops_without_delivering(self):
        ps._relay_in("TESTHOST", _relay("q-deny-1"))
        ok, err = ps.quarantine_decide("q-deny-1", "deny")
        self.assertTrue(ok, err)
        self.assertEqual(ps.quarantine_list(), [])
        self.assertEqual(ps.read_box("sess-web", consume=False), [], "deny delivers nothing")
        self.assertEqual(list((ps.OUTBOX / "TESTHOST").glob("*.json")) if (ps.OUTBOX / "TESTHOST").is_dir() else [],
                         [], "a bare deny sends nothing back")

    def test_deny_with_feedback_mails_the_sender(self):
        """Deny + a note (the user 2026-07-26): the note parks in the ORIGIN host's outbox as ordinary
        store-and-forward mail addressed to the sender session, so their agent learns why instead of
        waiting forever. The body names the recipient and quotes a gist of what was declined."""
        ps._relay_in("TESTHOST", _relay("q-deny-2", body="please rewrite the ingest job tonight"))
        ok, err = ps.quarantine_decide("q-deny-2", "deny", feedback="not tonight, we freeze before the demo")
        self.assertTrue(ok, err)
        self.assertEqual(ps.quarantine_list(), [])
        rows = [json.loads(f.read_text()) for f in (ps.OUTBOX / "TESTHOST").glob("*.json")]
        self.assertEqual(len(rows), 1, "exactly one note back to the sender")
        m = rows[0]
        self.assertEqual(m["to"], "api", "addressed to the SENDER session by name")
        self.assertEqual(m["frm"], "Romp Postal Service")
        self.assertIn("declined", m["body"])
        self.assertIn("not tonight, we freeze before the demo", m["body"])
        self.assertIn("please rewrite the ingest job", m["body"], "the gist anchors which message this was")
        for f in (ps.OUTBOX / "TESTHOST").glob("*.json"):
            f.unlink()

    def test_a_refused_approve_leaves_the_hold_in_place(self):
        # mutant: no except → deliver's refusal propagates out of the approve (or, worse, quarantine_del
        # runs) and the held message is gone with nothing in new/. Kept at the public-call level on
        # purpose: another change edits this function's body.
        ps._relay_in("TESTHOST", _relay("q-appr-refused", body="held text"))
        self.assertIsNotNone(ps.quarantine_get("q-appr-refused"))
        fd, path = tempfile.mkstemp()
        os.close(fd)
        saved_tl = ps.TLDIR
        ps.TLDIR = Path(path) / "timeline"                   # under a regular file: the REAL append fails
        try:
            ok, err = ps.quarantine_decide("q-appr-refused", "approve")
        finally:
            ps.TLDIR = saved_tl
            ps._TL_FAULT[0] = False
            os.unlink(path)
        self.assertFalse(ok)
        self.assertIn("the held message is untouched", err)
        self.assertIn("not delivered", err)
        self.assertIsNotNone(ps.quarantine_get("q-appr-refused"), "the hold stands")
        box = ps.read_box("sess-web", consume=False)
        self.assertFalse(any("held text" in (m.get("body") or "") for m in box), "nothing landed in new/")

    def test_decide_unknown_mid_errors(self):
        ok, err = ps.quarantine_decide("no-such-mid", "approve")
        self.assertFalse(ok)
        self.assertIn("no held message", err)

    def test_a_deny_whose_note_cannot_park_refuses_and_keeps_the_hold(self):
        # mutant: outbox_put's False ignored (review find, 2026-09-08) → the hold is dropped, ok is
        # answered, and the reviewer's note goes nowhere with nothing saying so
        ps._relay_in("TESTHOST", _relay("q-deny-3", body="please rewrite the ingest job tonight"))
        saved = ps.outbox_put
        ps.outbox_put = lambda h, m: False                   # the outbox could not be written
        try:
            ok, err = ps.quarantine_decide("q-deny-3", "deny", feedback="not tonight, we freeze before the demo")
        finally:
            ps.outbox_put = saved
        self.assertFalse(ok)
        self.assertIn("the held message is untouched", err)
        self.assertIn("deny without a note", err, "the way out is named")
        self.assertIsNotNone(ps.quarantine_get("q-deny-3"), "the hold stands")
        self.assertEqual(list((ps.OUTBOX / "TESTHOST").glob("*.json")) if (ps.OUTBOX / "TESTHOST").is_dir() else [],
                         [], "nothing was parked")
        ok, err = ps.quarantine_decide("q-deny-3", "deny", feedback="not tonight, we freeze before the demo")
        self.assertTrue(ok, err)
        self.assertIsNone(ps.quarantine_get("q-deny-3"), "the retry completes the deny")
        rows = [json.loads(f.read_text()) for f in (ps.OUTBOX / "TESTHOST").glob("*.json")]
        self.assertEqual(len(rows), 1, "and parks exactly one note")
        for f in (ps.OUTBOX / "TESTHOST").glob("*.json"):
            f.unlink()


class PeerUpdateTrust(unittest.TestCase):
    def test_default_and_keep_last_known(self):
        ps.PEERS.clear()
        ps.peer_update({"host": "H", "port": 1, "up": True})                 # no trust → directed
        self.assertEqual(ps.PEERS["H"]["trust"], "directed")
        ps.peer_update({"host": "H", "port": 1, "up": True, "trust": "trusted"})
        self.assertEqual(ps.PEERS["H"]["trust"], "trusted")
        ps.peer_update({"host": "H", "port": 1, "up": False})                # trustless down-notify keeps it
        self.assertEqual(ps.PEERS["H"]["trust"], "trusted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
