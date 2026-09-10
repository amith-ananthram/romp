#!/usr/bin/env python3
"""The rename ping (the user 2026-08-24; settle-boundary form 2026-08-25 pm): a renamed session
hears its OWN new name — rename() stamps the reg (renameNote, restart-proof, ONLY when prior turns
exist under the old name), and the ping delivers at a turn's SETTLE as its own machine-dressed
turn. The enqueue-AHEAD form folded: the CLI batches every message that arrives before a turn
starts into ONE user record, so the user's own words rendered inside the ping's machine bubble
(the 2026-08-25 screenshot: one romp-badged bubble holding the ping + the user's reply-quote +
their whole message). Three gates make that fold unreachable: settle-only delivery (the user is
answered first), the empty-queue guard (a queued message would share the pre-turn window), and the
drain's feed-hold until the ping's turn streams its first message — an exact event — after which a
racing send lands mid-turn as its own record by the CLI's own design. Voice pinned in
test_injected_voice.py. Deterministic: reg-level + a stub session, no real claude processes."""
import contextlib
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_renameping", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "aaaaaaaa-1111-2222-3333-444444444444"
SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


def _backend(d):
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)


@contextlib.contextmanager
def _disk_full(nf):
    """ENOSPC beneath the REAL names writer: the publish (os.replace onto names/<sid>) fails, and an
    in-place write to that file does what ENOSPC does to one — truncates, then fails. Every other path
    proceeds, so only the writer under test, and any restore aimed at its file, feel the full disk."""
    want = os.path.realpath(str(nf))
    real_replace, real_wb = os.replace, Path.write_bytes

    def replace(src, dst, *a, **k):
        if os.path.realpath(str(dst)) == want:
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *a, **k)

    def write_bytes(self, data, *a, **k):
        if os.path.realpath(str(self)) == want:
            open(self, "w").close()
            raise OSError(28, "No space left on device")
        return real_wb(self, data, *a, **k)
    with mock.patch.object(os, "replace", replace), mock.patch.object(Path, "write_bytes", write_bytes):
        yield


class RenamePing(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name)
        (Path(self.td.name) / "names").mkdir(parents=True, exist_ok=True)
        self.cwd = str(Path(self.td.name) / "proj")
        Path(self.cwd).mkdir()
        sb.write_reg(Path(self.td.name), SID, {"sid": SID, "name": "web", "cwd": self.cwd,
                                               "lastSid": SID})

    def tearDown(self):
        self.td.cleanup()

    def _write_history(self):
        # one prior turn under the old name — the transcript IS the record of prior turns
        tp = Path(sb.transcript_path(self.cwd, SID))
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text('{"type": "user", "uuid": "u1"}\n')

    def test_rename_with_history_stamps_the_pending_note_beside_the_name(self):
        self._write_history()
        self.assertTrue(self.be.rename(SID, "tests"))
        reg = sb.read_reg(Path(self.td.name), SID)
        self.assertEqual(reg.get("name"), "tests")
        self.assertEqual(reg.get("renameNote"), "tests",
                         "reg-persisted, so the ping survives a kernel restart unspoken")

    def test_rename_before_the_first_turn_pings_nothing(self):
        # the 2026-08-25 sighting's second half: a brand-new session has no stale self-knowledge
        # to correct — it learns its name the normal way, and its user's first words stay first
        self.assertTrue(self.be.rename(SID, "tests"))
        reg = sb.read_reg(Path(self.td.name), SID)
        self.assertEqual(reg.get("name"), "tests", "the rename itself still lands")
        self.assertIsNone(reg.get("renameNote"), "…but no ping is owed")

    def test_rename_of_an_unknown_sid_stamps_nothing(self):
        self.assertFalse(self.be.rename("99999999-0000-1111-2222-333333333333", "tests"))

    class _StubSession:
        def __init__(self, sid, pending=()):
            import threading
            self.sid = sid
            self._lock = threading.Lock()
            self._pending = list(pending)
            self.sent = []

        def enqueue(self, text):
            self.sent.append(text)

        def enqueue_if_empty(self, text):
            # the real session's atomic gate: emptiness check + append under ONE lock hold
            with self._lock:
                if self._pending:
                    return False
                self.enqueue(text)
                return True

    def test_settle_delivery_ships_the_dressed_ping_alone_and_once(self):
        self._write_history()
        self.be.rename(SID, "tests")
        s = self._StubSession(SID)
        self.assertTrue(self.be._deliver_rename_ping(s))
        self.assertEqual(len(s.sent), 1, "one turn of its own")
        self.assertTrue(s.sent[0].startswith(sb.RENAME_PING_HEAD), "the machine dress leads the record")
        self.assertIn("'tests'", s.sent[0], "…and it names the new name")
        self.assertIsNone(sb.read_reg(Path(self.td.name), SID).get("renameNote"), "the note is spent")
        self.assertFalse(self.be._deliver_rename_ping(s), "…and a second settle delivers nothing")

    def test_a_queued_turn_holds_the_note_for_a_later_settle(self):
        # the empty-queue guard: a queued message would share the ping's pre-turn window, which is
        # exactly the fold that put the user's words in the machine bubble
        self._write_history()
        self.be.rename(SID, "tests")
        s = self._StubSession(SID, pending=["already queued"])
        self.assertFalse(self.be._deliver_rename_ping(s))
        self.assertEqual(s.sent, [], "nothing fed beside a queued turn")
        self.assertEqual(sb.read_reg(Path(self.td.name), SID).get("renameNote"), "tests",
                         "the note survives for a later, empty-queue settle")

    def test_a_send_racing_the_gate_cannot_queue_ahead_of_the_ping(self):
        # the gate's TOCTOU (found 2026-08-26): the empty-queue check and the ping's enqueue held
        # the session lock SEPARATELY, with the renameNote reg write between them — a send landing
        # in that window queued AHEAD of the ping, and the CLI's pre-turn batching folded the
        # user's words into the ping's machine record (the exact 2026-08-25 fold the gate exists
        # to prevent). Deterministic interleave: the reg write IS the window, so a hook there
        # plays the racing send. A real (unstarted) session — the race is in its lock discipline.
        self._write_history()
        self.be.rename(SID, "tests")
        s = sb.SdkSession(self.be, {"sid": SID, "name": "web", "cwd": self.cwd,
                                    "mode": "acceptEdits"})
        orig, raced = self.be._update_reg, []

        def racing_update(sid, **fields):
            if "renameNote" in fields and not raced:
                raced.append(True)
                s.enqueue("the user's own racing words")   # a concurrent send() in the window
            orig(sid, **fields)

        self.be._update_reg = racing_update
        try:
            delivered = self.be._deliver_rename_ping(s)
        finally:
            self.be._update_reg = orig
        pending = s.pending()
        if delivered:
            self.assertTrue(pending and pending[0].startswith(sb.RENAME_PING_HEAD),
                            "a racing send must never queue AHEAD of the ping — that is the fold")
            self.assertIn("the user's own racing words", pending,
                          "…and the racing send itself is queued behind it, not lost")
        else:
            self.assertEqual(sb.read_reg(Path(self.td.name), SID).get("renameNote"), "tests",
                             "an undelivered ping keeps its note for a later settle")

    def test_the_fold_gates_are_pinned_at_source(self):
        # the fold's mechanics live in async plumbing a unit test can't drive — pin the three gates
        self.assertNotIn("RENAME_NUDGE % _reg", SRC, "no send()-time delivery of any form remains")
        self.assertNotIn('text = "%s\\n\\n%s" % (RENAME_NUDGE', SRC, "the string-prepend form stays gone")
        self.assertIn("self.backend._deliver_rename_ping(self)", SRC, "delivery hooks the turn's settle")
        self.assertIn("blocked = blocked or self._ping_feeding", SRC,
                      "the drain holds every feed while the ping's turn has not started")
        self.assertIn("if item.startswith(RENAME_PING_HEAD):", SRC, "…armed exactly when a ping is fed")
        self.assertIn("self._ping_feeding = False   # a reconnect restarts the feed", SRC,
                      "…and a reconnect clears a stale hold instead of wedging the queue")
        self.assertNotIn("romp-injected", sb.RENAME_NUDGE,
                         "the constant stays bare prose; the dress is added only on the separate record")


class NamesWriteFailure(unittest.TestCase):
    """rename() moves the durable registry FIRST, then the shared names/<sid> identity file through
    write_name — tmp + os.replace, so a raise from it leaves names/<sid> exactly as it was. What a raise
    USED to leave: the registry holding the new name (and a renameNote the rename stamped), the writer's
    temp beside the file (the names scanners read the dir: a phantom session), and the exception
    escaping with no compensation. Now the temp is gone, the registry write is re-run with the old
    fields, the in-memory name never moves, and the exception still reaches the caller — with
    deliberately NO restore write: an in-place rewrite would be the one non-atomic write on the path,
    and under ENOSPC it truncates the very file it means to save, which is what _disk_full models
    (2026-09-08). Synthetic: placeholder sid, the demo names."""

    class _Live:
        name = "web"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.be = _backend(self.td.name)
        self.cwd = str(self.root / "proj")
        Path(self.cwd).mkdir()
        (self.root / "names").mkdir()
        self.nf = self.root / "names" / SID
        self.line = "web\t%s\t#112233\t#ffffff\n" % self.cwd
        self.nf.write_text(self.line)
        sb.write_reg(self.root, SID, {"sid": SID, "name": "web", "cwd": self.cwd, "lastSid": SID})
        tp = Path(sb.transcript_path(self.cwd, SID))          # prior turns: the rename also stamps
        tp.parent.mkdir(parents=True, exist_ok=True)         # renameNote, which must come back out
        tp.write_text('{"type": "user", "uuid": "u1"}\n')
        self.before = sb.read_reg(self.root, SID)
        self.live = self._Live()
        self.be.sessions[SID] = self.live

    def tearDown(self):
        self.td.cleanup()

    def test_a_failed_publish_leaves_every_store_as_it_was(self):
        # on origin/main: the writer's temp stayed beside the file and the registry moved to
        # {'name': 'tests', 'renameNote': 'tests'}
        with _disk_full(self.nf):
            with self.assertRaises(OSError):
                self.be.rename(SID, "tests")
        self.assertEqual(self.nf.read_text(), self.line,
                         "names/<sid> byte-identical: the writer is atomic and nothing rewrote it in place")
        self.assertEqual(sorted(p.name for p in self.nf.parent.iterdir()), [SID], "the writer's temp is gone")
        self.assertEqual(sb.read_reg(self.root, SID), self.before,
                         "the registry keeps its old fields — no new name, no renameNote")
        self.assertEqual(self.live.name, "web", "the in-memory name never moved")

    def test_a_failed_first_publish_leaves_nothing_behind(self):
        # on origin/main: the writer's temp stayed as the only thing in names/
        self.nf.unlink()                                      # a row predating the names write
        with _disk_full(self.nf):
            with self.assertRaises(OSError):
                self.be.rename(SID, "tests")
        self.assertEqual(sorted(p.name for p in self.nf.parent.iterdir()), [],
                         "neither names/<sid> nor its temp exists: a failed rename publishes nothing")
        self.assertEqual(sb.read_reg(self.root, SID), self.before)
        self.assertEqual(self.live.name, "web")

    def test_a_landing_write_still_moves_all_three_stores(self):
        # the control: with the write landing, the registry, the file and the live name all move
        self.assertTrue(self.be.rename(SID, "tests"))
        self.assertEqual(sb.read_reg(self.root, SID).get("name"), "tests")
        self.assertEqual(self.nf.read_text(), "tests\t%s\t#112233\t#ffffff\n" % self.cwd, "colours preserved")
        self.assertEqual(self.live.name, "tests")


if __name__ == "__main__":
    unittest.main()
