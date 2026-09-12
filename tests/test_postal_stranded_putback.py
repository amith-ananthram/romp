#!/usr/bin/env python3
"""Peer mail fed to a session and stranded by a connection rebuild is HANDED BACK to the bus, never dropped.

The bus delivers a message by writing it into the recipient's maildir, CLAIMING it (new/ -> cur/), and POSTing
the banner to the kernel's /deliver; the kernel enqueues it and answers `injected: true`, on which the bus treats
its durable copy as spent. `injected` only ever meant "queued in kernel memory". When the session's connection is
rebuilt (a model or effort pin does this silently) after the banner was FED but before its turn resulted,
SdkSession._reconcile_stranded takes the fed text off the in-flight list; on a RESUMABLE conversation its documented
policy is flag-only (a re-feed could duplicate a turn that genuinely landed), and the flag it flips is the send's
input ECHO. Peer mail has no echo by design (SdkBackend.deliver), so until 2026-09-12 the banner was dropped with no
queue entry, no flag, no log line and no word to the bus, whose only copy sat in cur/ where nothing reads it again:
fifteen messages to two sessions vanished in twenty minutes while every sender was told "delivered".

The fix: a banner names its own messages (`<!-- romp-msg-id: <id> -->`), and the bus already knows how to put a
claimed message back under its original id (restore, the roll-back the not-injected push takes). On the resumable
branch a stranded banner's ids are handed back to the bus (SdkBackend.postal_restore, the kernel-installed hook that
POSTs the bus's /restore), which puts them back in new/ and wakes the session, so the mail re-delivers under the same
identity. A banner the bus could not take back (no bus, a refusal), or whose ids it holds NONE of (another host's bus
does: the wake-router forwarded it here), is re-headed in the queue instead: a banner landing twice in the resumed
conversation beats a peer told "delivered" for mail nobody read. The duplicate is accepted only when the transcript
scan (_text_landed) cannot rule it out: a banner that already landed before the teardown stays where it is. Every
OTHER fed text keeps the flag-only path exactly as before (ReconnectStrandIsFlagOnly in test_sdk_echo_durability
pins it).

SYNTHETIC fixtures only: invented text, placeholder uuids, no real session names or message ids."""
import json
import os
import tempfile
import threading
import unittest
import uuid
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — both modules resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_putback", os.path.join(BIN, "romp_sdk_backend.py"))
pm = load_source("romp_postal_putback", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-3333-4444-555555555555"
PEER = "22222222-3333-4444-5555-666666666666"
MID1 = "1700000000.111111.TESTHOST"
MID2 = "1700000001.222222.TESTHOST"


def _banner(*mids, body="the pod is up; please take the next fire"):
    """A bus banner exactly as _push hands it to /deliver: one message per id, the bus's own formatter."""
    return pm.format_push([{"from": "alpha", "from_id": PEER, "body": body, "id": m, "date": ""} for m in mids])


class _StrandWorld(unittest.TestCase):
    """A registry + empty transcript for one RESUMABLE SDK session, registered without its thread (no loop), the
    same harness ReconnectStrandIsFlagOnly uses: _text_landed answers False, the exact 'missed scan' shape the
    resumable branch refuses to trust with a re-feed."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                            # a self-minted state root pins per-session hosts off
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()
        self.logged = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=self.logged.append)

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def _sess(self, **extra):
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits",
               "alive": True, "cwd": self.cwd, "lastSid": SID}
        reg.update(extra)
        sb.write_reg(self.be.state_dir, SID, reg)
        s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = s
        return s

    def _strand(self, s, *texts):
        s.inflight = len(texts)
        s._inflight_texts.extend(texts)
        s._reconcile_stranded()

    def _stash_echo(self, text, t=100):
        e = {"type": "user", "uuid": "echo:" + text[:10], "session_id": SID, "t": t,
             "parentUuid": None, "author": "human", "_echo_text": text,
             "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        self.be._live.setdefault(SID, {})[e["uuid"]] = e
        return e


class StrandedMailGoesBackToTheBus(_StrandWorld):

    def test_a_stranded_banner_is_handed_back_to_the_bus_by_message_id(self):
        s = self._sess()                                  # lastSid set → resumable: the flag-only branch
        self.assertEqual(s.resume_sid, SID)
        handed = []
        self.be.postal_restore = lambda sid, mids: (handed.append((sid, list(mids))), set(mids))[1]
        self._strand(s, _banner(MID1, MID2))
        self.assertEqual(handed, [(SID, [MID1, MID2])],
                         "a fed-but-unresulted postal banner is handed back to the bus by its message ids; "
                         "before the fix it was dropped: no echo to flag, no queue entry, no word to the bus")
        self.assertEqual(s.pending(), [], "the bus re-delivers under the same ids; nothing is re-fed here")
        self.assertEqual((sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or [], [])
        self.assertEqual(s.inflight, 0, "the strand still settles the counters")
        self.assertTrue(any(MID1 in ln and MID2 in ln for ln in self.logged),
                        "one log line names the ids handed back: %r" % (self.logged,))

    def test_a_banner_the_bus_cannot_take_back_is_re_headed_not_dropped(self):
        s = self._sess()
        def refuse(sid, mids):
            raise ConnectionRefusedError("no bus on the port")
        self.be.postal_restore = refuse
        banner = _banner(MID1)
        self._strand(s, banner)
        self.assertEqual(s.pending(), [banner],
                         "with no bus to hold it, the banner goes back to the head of the queue for the new "
                         "client — a duplicate in the resumed conversation beats a silent loss")
        self.assertEqual((sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or [], [banner],
                         "…and it is persisted, so a restart in between cannot lose it either")
        self.assertTrue(any(MID1 in ln and "re-head" in ln for ln in self.logged),
                        "the log names the id and says it was re-headed: %r" % (self.logged,))

    def test_an_id_the_bus_no_longer_holds_is_not_re_fed(self):
        # The bus's answer is authoritative about its own files: in a PARTIAL answer, an id it did not put back is
        # gone from its box (recalled by its sender, or swept) and must not come back through the queue on the
        # kernel's say-so. (An answer holding none of the ids is another matter: the next case.)
        s = self._sess()
        self.be.postal_restore = lambda sid, mids: {MID1}            # MID2 was recalled meanwhile
        self._strand(s, _banner(MID1, MID2))
        self.assertEqual(s.pending(), [], "the bus answered: what it holds re-delivers, what it lost is gone")
        self.assertTrue(any(MID2 in ln for ln in self.logged), "the missing id is named: %r" % (self.logged,))

    def test_a_bus_that_holds_none_of_the_ids_gets_the_banner_re_headed(self):
        # The wake-router case: the banner was forwarded from another host, whose bus holds the claimed file;
        # the local bus answers with nothing put back. Nothing else removes a live session's cur/ file, so an
        # empty answer is "never held", not "gone", and the banner text is the last copy of the mail.
        s = self._sess()
        self.be.postal_restore = lambda sid, mids: set()
        banner = _banner(MID1, MID2)
        self._strand(s, banner)
        self.assertEqual(s.pending(), [banner], "re-headed for the new client, not dropped")
        self.assertEqual((sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or [], [banner], "and persisted")
        self.assertTrue(any(MID1 in ln and MID2 in ln and "re-head" in ln for ln in self.logged),
                        "a problem line names both ids and says it was re-headed: %r" % (self.logged,))

    def test_a_banner_that_landed_before_the_teardown_is_not_handed_back(self):
        # The teardown can come AFTER the CLI wrote the banner's user record (a stream error or timeout mid-turn,
        # the record written before the model call). The resumed conversation already carries the mail; the bus
        # re-delivering it under the same id would put the delegate in front of the agent twice. The transcript
        # scan answers True here, which is definitive, so the banner is left where it is.
        s = self._sess()
        banner = _banner(MID1)
        with open(sb.transcript_path(self.cwd, SID), "a") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
                                "message": {"role": "user", "content": [{"type": "text", "text": banner}]}}) + "\n")
        self.assertIs(self.be._text_landed(SID, banner), True)
        called = []
        self.be.postal_restore = lambda sid, mids: called.append(list(mids)) or set(mids)
        self._strand(s, banner)
        self.assertEqual(called, [], "the bus is not asked to put back mail the conversation already carries")
        self.assertEqual(s.pending(), [], "and nothing is re-fed")
        self.assertEqual((sb.read_reg(self.be.state_dir, SID) or {}).get("queue") or [], [])
        self.assertEqual(len([ln for ln in self.logged if MID1 in ln and "landed" in ln]), 1,
                         "one log line names the id and says it landed: %r" % (self.logged,))

    def test_a_banner_the_scan_cannot_place_is_still_handed_back(self):
        # The skip needs a DEFINITIVE answer. The harness's empty transcript reads False (readable, nothing landed:
        # the incident's shape) and a transcript that cannot be read reads None; neither proves the banner landed,
        # since the abandoned client may still have been flushing its record, so both hand back as before.
        handed = []
        self.be.postal_restore = lambda sid, mids: handed.append(list(mids)) or set(mids)
        s = self._sess()
        self.assertIs(self.be._text_landed(SID, _banner(MID1)), False)
        self._strand(s, _banner(MID1))
        os.remove(sb.transcript_path(self.cwd, SID))
        s = self._sess()
        self.assertIsNone(self.be._text_landed(SID, _banner(MID2)))
        self._strand(s, _banner(MID2))
        self.assertEqual(handed, [[MID1], [MID2]], "False and None both proceed to the bus; only True skips")
        self.assertEqual(s.pending(), [])

    def test_without_a_hook_installed_the_banner_is_still_not_lost(self):
        # A stand-in backend (older kernels, tests) with no postal_restore: the fallback is the re-head.
        s = self._sess()
        self.assertFalse(callable(getattr(self.be, "postal_restore", None)))
        banner = _banner(MID1)
        self._strand(s, banner)
        self.assertEqual(s.pending(), [banner])

    def test_a_typed_send_keeps_the_flag_only_path(self):
        # Unchanged for everything that is not a postal banner (ReconnectStrandIsFlagOnly's contract).
        s = self._sess()
        called = []
        self.be.postal_restore = lambda sid, mids: called.append(mids) or set(mids)
        text = "typed while the reconnect tore the client down"
        e = self._stash_echo(text, t=300)
        self._strand(s, text)
        self.assertEqual(called, [], "a typed send carries no message id; the bus is never asked about it")
        self.assertEqual(s.pending(), [], "flag-only: never re-fed")
        self.assertTrue(e.get("dropped"), "the loss surfaces on the echo as before")

    def test_a_mixed_strand_hands_back_the_mail_and_flags_the_typing(self):
        s = self._sess()
        handed = []
        self.be.postal_restore = lambda sid, mids: (handed.append(list(mids)), set(mids))[1]
        text = "and the human's follow-up, fed the same second"
        e = self._stash_echo(text, t=300)
        self._strand(s, _banner(MID1), text)
        self.assertEqual(handed, [[MID1]])
        self.assertEqual(s.pending(), [])
        self.assertTrue(e.get("dropped"))

    def test_a_fresh_conversation_still_re_heads_everything(self):
        # The other branch is untouched: no init ever streamed → the queue re-head is the loss-proof path.
        s = self._sess(lastSid="")
        self.assertIsNone(s.resume_sid)
        called = []
        self.be.postal_restore = lambda sid, mids: called.append(mids) or set(mids)
        banner = _banner(MID1)
        self._strand(s, banner)
        self.assertEqual(s.pending(), [banner])
        self.assertEqual(called, [], "re-headed here, not handed back: the new client feeds it itself")

    def test_postal_mids_reads_the_banner_and_nothing_else(self):
        self.assertEqual(sb.postal_mids(_banner(MID1, MID2)), [MID1, MID2])
        self.assertEqual(sb.postal_mids(_banner(MID1, MID1)), [MID1], "deduped, order kept")
        self.assertEqual(sb.postal_mids("a typed line with no marker"), [])
        self.assertEqual(sb.postal_mids(""), [])
        self.assertEqual(sb.postal_mids(None), [])


class TheBusPutsAStrandedMessageBack(unittest.TestCase):
    """The bus side of the hand-back: POST /restore {id, mids} moves each named message from cur/ back to new/
    under its ORIGINAL id (restore), retracts the exec row, and wakes the session so it re-delivers."""

    def setUp(self):
        self.saved = (pm._wake_when_ready, pm.SERVE_TOKEN)
        self.woken = []
        pm._wake_when_ready = lambda sid: self.woken.append(sid)
        self.sid = str(uuid.uuid4())                  # a fresh synthetic mailbox per test: no leak between claims

    def tearDown(self):
        pm._wake_when_ready, pm.SERVE_TOKEN = self.saved

    def _claimed(self, body):
        """Deliver one message to self.sid and CLAIM it the way the push does: it now sits in cur/."""
        mid = pm.deliver(self.sid, "alpha", PEER, body)
        got = pm.read_box(self.sid, consume=True)
        self.assertEqual([m["id"] for m in got], [mid])
        self.assertTrue((pm.MAILROOT / self.sid / "cur" / mid).is_file())
        self.assertFalse((pm.MAILROOT / self.sid / "new" / mid).exists())
        return mid

    def test_restore_stranded_puts_the_named_messages_back_in_new(self):
        m1 = self._claimed("first, fed and lost")
        m2 = self._claimed("second, fed and lost")
        payload, status = pm.restore_stranded({"id": self.sid, "mids": [m1, m2, "1700000002.333333.TESTHOST"]})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["restored"], [m1, m2])
        self.assertEqual(payload["missing"], ["1700000002.333333.TESTHOST"], "an id not in cur/ is named, not invented")
        for m in (m1, m2):
            self.assertTrue((pm.MAILROOT / self.sid / "new" / m).is_file(), "back in new/ under its ORIGINAL id")
            self.assertFalse((pm.MAILROOT / self.sid / "cur" / m).exists())
        self.assertEqual(self.woken, [self.sid], "the session is woken so the mail re-delivers now")
        rows = [json.loads(ln) for ln in (pm.TLDIR / "messages.jsonl").read_text().splitlines() if ln.strip()]
        self.assertEqual([r["ev"] for r in rows if r.get("id") == m1], ["sent", "exec", "unexec"],
                         "the claim is retracted on the ledger, so the sender's receipt reads pending again")

    def test_nothing_to_restore_wakes_nobody(self):
        payload, status = pm.restore_stranded({"id": self.sid, "mids": ["1700000003.444444.TESTHOST"]})
        self.assertEqual((status, payload["restored"], payload["missing"]), (200, [], ["1700000003.444444.TESTHOST"]))
        self.assertEqual(self.woken, [])

    def test_a_malformed_ask_is_refused(self):
        for bad in ({"id": "", "mids": ["x"]}, {"id": self.sid, "mids": "x"}, {"id": self.sid, "mids": [1]},
                    {"id": "../etc", "mids": ["x"]}, {"id": self.sid}):
            payload, status = pm.restore_stranded(bad)
            self.assertEqual(status, 400, bad)
            self.assertFalse(payload["ok"])
        payload, status = pm.restore_stranded({"id": self.sid, "mids": ["../escape"]})
        self.assertEqual((status, payload["restored"], payload["missing"]), (200, [], ["../escape"]),
                         "an unsafe message id is reported missing, never resolved to a path")

    def test_the_route_answers_over_http_behind_the_token(self):
        m1 = self._claimed("over the wire")
        pm.SERVE_TOKEN = "test-token-DO-NOT-USE"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        try:
            url = "http://127.0.0.1:%d/restore" % srv.server_address[1]
            body = json.dumps({"id": self.sid, "mids": [m1]}).encode()
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=5)
            self.assertEqual(cm.exception.code, 403, "no token, no route")
            self.assertTrue((pm.MAILROOT / self.sid / "cur" / m1).is_file(), "…and nothing moved")
            req = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                                     "X-Romp-Token": "test-token-DO-NOT-USE"})
            with urllib.request.urlopen(req, timeout=5) as r:
                ans = json.loads(r.read())
            self.assertEqual((ans["ok"], ans["restored"], ans["missing"]), (True, [m1], []))
            self.assertTrue((pm.MAILROOT / self.sid / "new" / m1).is_file())
            self.assertEqual(self.woken, [self.sid])
        finally:
            srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    unittest.main()
