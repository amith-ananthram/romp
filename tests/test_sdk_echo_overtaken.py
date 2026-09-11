#!/usr/bin/env python3
"""A send the transcript has OUTRUN is a loss, and reads as one (2026-09-11). An SDK input echo used to be
flagged `dropped` (the chat's "never delivered" bubble with its ✕) only when the process HOLDING the send
died: a spawn, a boot, a reconnect. A CLI that wedged for five minutes, swallowed a send and then carried on
left the echo painted as an ordinary sent bubble — solid, stamped with its send time, resurfacing above
every newer message as they landed — with no way to clear it: dismiss_echo takes dropped echoes only, by
design, and a kernel restart would have RE-SENT the swallowed message behind the newer ones (the boot
marker's re-delivery arm). The event that settles it: a GENUINE-HUMAN turn stamped strictly later than the
send, its own text landed nowhere and owed by no queue. The composer's messages travel one channel in
order, so the later one going through means the CLI took it while still holding this one.

Three surfaces, each pinned here:
  1. live — sdk_backend.settle_echoes marks the overtaken echo dropped at the build: whole seconds, never
     pruning, standing down for a text the backend's own queue or the CLI's queue ledger still lists,
     self-correcting on a landing;
  2. the kernel's _merge_live_atoms hands the settle the CLI's UNFILTERED ledger (_pending_ledger), and
     only when an unflagged, unlanded echo is actually overtaken;
  3. boot — _mark_dropped_echoes flags instead of re-feeding when a later human input landed after the
     send (_input_landed_after), reading the record the way the parse authors it (_human_input_record).
SYNTHETIC fixtures only; a private synthetic sid; the notes-api demo domain."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_echo_overtaken", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_overtaken", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "6c6c6c6c-7d7d-4e8e-9f9f-0a0a0a0a0a0a"      # private synthetic sid (goal-store fixtures rule)
SENT = "now name the full evaluation plan for the notes-api search"
T = 1_800_000_100                                  # the send (the echo's stamp)
KEY = "echo:0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a"


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _echo_atom(text=SENT, t=T, key=KEY, author="human", **extra):
    a = {"type": "user", "uuid": key, "session_id": SID, "t": t, "parentUuid": None, "author": author,
         "_echo_text": text, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    a.update(extra)
    return a


class _Queue:
    """A stand-in SdkSession holding a queue: what settle_echoes asks the live session for."""

    def __init__(self, metas, fed=()):
        self.metas = list(metas)
        self.fed = list(fed)
        self.forgotten = []

    def pending(self):
        return [m["md"] for m in self.metas]

    def fed_texts(self):
        return list(self.fed)

    def pending_meta(self):
        return [dict(m, qts=None, held=False, holder=None) for m in self.metas]

    def forget_fed(self, qid):
        self.forgotten.append(qid)


class _World(unittest.TestCase):
    """A registry + transcript for one SDK session under a hermetic state root, with the CLI's record shapes."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        self.tpath = sb.transcript_path(self.cwd, SID)
        os.makedirs(os.path.dirname(self.tpath), exist_ok=True)
        open(self.tpath, "w").close()

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    # -- the CLI's record shapes (keys real, content invented) --
    def _user(self, t, text, uuid, parent, **extra):
        rec = {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "sdk",
               "userType": "external", "isSidechain": False, "message": {"role": "user", "content": text}}
        rec.update(extra)
        return rec

    def _assistant(self, t, uuid, parent, text="Done.", tool=False):
        content = ([{"type": "tool_use", "id": "tu_" + uuid, "name": "Bash", "input": {"command": "true"}}]
                   if tool else [{"type": "text", "text": text}])
        return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "assistant", "content": content, "stop_reason": "tool_use" if tool else "end_turn"}}

    def _tool_result(self, t, uuid, parent, tool_uuid):
        return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + tool_uuid, "content": "ok"}]}}

    def _att(self, t, prompt, uuid, parent, **extra):
        # the CLI's shape for a mid-turn splice's record (keys real, content invented): no '"user"' literal
        # anywhere, and the prompt a plain string or a content-block list
        rec = {"type": "attachment", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False,
               "userType": "external", "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": "prompt",
                                                      "timestamp": int(t) * 1000}}
        rec["attachment"].update(extra)
        return rec

    def _qop(self, t, op, content=None):
        return {"type": "queue-operation", "timestamp": _iso(t), "operation": op, "content": content}

    def _running_turn(self):
        # the turn the send was fed into: an opener 38 s before the send, a tool call still out
        return [self._user(T - 38, "tighten the notes-api search", "u1", None),
                self._assistant(T - 28, "a1", "u1", tool=True)]

    def _write(self, recs):
        with open(self.tpath, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def _size(self):
        return os.path.getsize(self.tpath)

    def _reg(self, **fields):
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd, "lastSid": SID,
               "queue": [], "echoes": []}
        reg.update(fields)
        sb.write_reg(self.state, SID, reg)

    def _backend(self):
        return sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None)   # __init__ runs the boot reseed

    def _mirror_echo(self, text=SENT, t=T, **mark):
        e = {"t": t, "text": text, "author": "human", "rompAuto": False, "dropped": False, "uuid": KEY}
        e.update(mark)
        return e

    def _queue(self):
        return (sb.read_reg(self.state, SID) or {}).get("queue") or []

    def _mirror(self):
        return (sb.read_reg(self.state, SID) or {}).get("echoes") or []

    @staticmethod
    def _live(be):
        return list((be._live.get(SID) or {}).values())


class LiveSettleFlagsAnOvertakenSend(_World):
    """Surface 1: settle_echoes at the build, on a running backend with one unlanded echo."""

    def _be(self, atom=None):
        self._reg()
        be = self._backend()
        be._live[SID] = {KEY: atom or _echo_atom()}
        be._persist_echoes(SID)
        self.assertFalse(self._mirror()[0].get("dropped"), "the fixture starts as a pending send")
        return be

    def test_a_later_human_turn_flags_the_echo_and_the_x_can_then_clear_it(self):
        be = self._be()
        be.settle_echoes(SID, T + 60)
        [a] = self._live(be)
        self.assertTrue(a.get("dropped"), "overtaken by a later human turn, landed nowhere: never delivered")
        self.assertTrue(self._mirror()[0].get("dropped"), "the verdict rides the mirror across a restart")
        self.assertEqual(be.dismiss_echo(SID, uuid=KEY), SENT, "a dropped echo is the user's to clear")
        self.assertEqual(self._live(be), [], "…and it is gone")
        self.assertEqual(self._mirror(), [])

    def test_a_pending_echo_cannot_be_dismissed_which_is_why_the_flag_matters(self):
        be = self._be()
        self.assertIsNone(be.dismiss_echo(SID, uuid=KEY), "dismiss_echo takes DROPPED echoes only, by design")
        self.assertEqual(len(self._live(be)), 1)

    def test_the_turn_must_fall_in_a_later_second(self):
        be = self._be()
        be.settle_echoes(SID, 0)
        self.assertFalse(self._live(be)[0].get("dropped"), "no floor, no verdict")
        be.settle_echoes(SID, T)
        self.assertFalse(self._live(be)[0].get("dropped"), "the same second is not later")
        be.settle_echoes(SID, T + 0.9)
        self.assertFalse(self._live(be)[0].get("dropped"),
                         "a record written in the send's second (the previous message landing as the user pressed "
                         "enter twice) is not a later turn: the echo is stamped to the second, the record to the ms")
        be.settle_echoes(SID, T + 1)
        self.assertTrue(self._live(be)[0].get("dropped"), "the next second is")

    def test_a_text_the_cli_ledger_still_owes_is_waiting_not_lost(self):
        be = self._be()
        be.settle_echoes(SID, T + 60, still_queued=[SENT])
        self.assertFalse(self._live(be)[0].get("dropped"),
                         "an older queued sibling delivering raises the floor past a send still in the CLI's queue")
        be.settle_echoes(SID, T + 60, still_queued=[{"md": SENT + "\n", "qid": None}])
        self.assertFalse(self._live(be)[0].get("dropped"), "ledger copies as dicts, under the shared text key")
        be.settle_echoes(SID, T + 60, still_queued=[])
        self.assertTrue(self._live(be)[0].get("dropped"), "once the ledger releases the text, the next build rules")

    def test_a_text_the_backends_own_queue_holds_is_waiting_not_lost(self):
        be = self._be()
        be.sessions[SID] = _Queue([{"md": SENT, "qid": KEY}])
        be.settle_echoes(SID, T + 60)
        self.assertFalse(self._live(be)[0].get("dropped"), "held in the kernel's queue (an editor open on it): waiting")
        # the same text queued under ANOTHER copy's id does not hide this copy's loss (T252c's identity reading)
        be.sessions[SID] = _Queue([{"md": SENT, "qid": "echo:ffffffffffffffffffffffffffffffff"}])
        be.settle_echoes(SID, T + 60)
        self.assertTrue(self._live(be)[0].get("dropped"), "the second of two identical sends does not hide the first's loss")
        self.assertEqual(be.sessions[SID].forgotten, [KEY], "its fed copy leaves the ledger: the landing will never come")

    def test_a_text_fed_into_the_running_turn_is_waiting_until_that_turn_settles(self):
        be = self._be()
        q = _Queue([], fed=[SENT])                    # fed to the CLI, its ResultMessage not yet landed
        be.sessions[SID] = q
        be.settle_echoes(SID, T + 60)
        self.assertFalse(self._live(be)[0].get("dropped"),
                         "between the feed and the CLI's own record nothing on disk owes the text: the fed ledger does")
        q.fed = []                                    # the turn settled without recording it
        be.settle_echoes(SID, T + 60)
        self.assertTrue(self._live(be)[0].get("dropped"))

    def test_the_flag_advances_the_live_revision_once(self):
        be = self._be()
        rev = be.live_rev(SID)
        be.settle_echoes(SID, T + 60)
        self.assertEqual(be.live_rev(SID), rev + 1, "a flag is a change to the tail: the chat's signature must move")

    def test_untrusted_identities_fall_back_to_the_text(self):
        be = self._be()
        q = _Queue([{"md": SENT, "qid": KEY}])
        q.pending_meta = lambda: None                 # the two lists disagree: identities cannot be trusted
        be.sessions[SID] = q
        be.settle_echoes(SID, T + 60)
        self.assertFalse(self._live(be)[0].get("dropped"), "by text, the side that never flags a waiting send")

    def test_a_landing_still_prunes_a_flagged_echo(self):
        be = self._be()
        be.settle_echoes(SID, T + 60)
        self.assertTrue(self._live(be)[0].get("dropped"))
        be.prune_live(SID, tx_uuids=set(), tx_user_texts={sb.echo_text_key(SENT): T + 5}, human_floor=T + 60)
        self.assertNotIn(SID, be._live, "self-correcting: a text that lands after all retires the echo, flag and all")
        self.assertEqual(self._mirror(), [])

    def test_marking_is_never_pruning(self):
        be = self._be()
        be.settle_echoes(SID, T + 60)
        for _ in range(3):
            be.prune_live(SID, tx_uuids=set(), tx_user_texts={}, human_floor=T + 600)
        self.assertEqual(len(self._live(be)), 1, "a dropped send's echo stays until the user dismisses it")

    def test_a_settled_or_landed_echo_is_left_alone(self):
        be = self._be()
        be.settle_echoes(SID, T + 60)
        rev = be.live_rev(SID)
        writes = []
        be._persist_echoes = lambda sid: writes.append(sid)
        be.settle_echoes(SID, T + 120)
        self.assertEqual((be.live_rev(SID), writes), (rev, []), "a mark already made is no change: no revision, no reg write")
        be2 = self._be(_echo_atom(_landed=True))
        be2.settle_echoes(SID, T + 60)
        self.assertFalse(self._live(be2)[0].get("dropped"), "a recorded landing is never a loss (prune_live retires it)")

    def test_a_romp_authored_echo_is_flagged_too(self):
        be = self._be(_echo_atom(text="<!-- romp-injected --> where does this stand?", author="romp", rompAuto=True))
        be.settle_echoes(SID, T + 60)
        self.assertTrue(self._live(be)[0].get("dropped"), "a lost nudge is a loss as well; nothing is re-sent here")

    def test_the_loss_is_reported_as_a_problem_once(self):
        be = self._be()
        lines = []
        be._log = lambda m, problem=None, **k: lines.append((m, problem))
        be.settle_echoes(SID, T + 60)
        be.settle_echoes(SID, T + 61)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0][1], "a lost send reaches the error center")
        self.assertIn("never reached its conversation", lines[0][0])
        self.assertNotIn(SENT, lines[0][0][:20], "the sid prefix leads; the text is a capped repr")


class TheKernelHandsTheSettleTheLedger(unittest.TestCase):
    """Surface 2: _merge_live_atoms calls settle_echoes with the floor and the CLI's unfiltered ledger, and
    only when an unflagged, unlanded echo is overtaken."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.path = os.path.join(self.td, SID + ".jsonl")
        self._saved = (km.Sessions.__dict__["backend_for"], km.__dict__["_path_of"], dict(km._merge_sets_memo))
        km._merge_sets_memo.clear()
        self.calls, self.prunes, self.live = [], [], []
        test = self

        class Fake:
            def live_atoms(self, sid):
                return list(test.live)

            def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
                test.prunes.append(human_floor)

            def settle_echoes(self, sid, human_floor, still_queued=()):
                test.calls.append((sid, human_floor, list(still_queued)))
        self.Fake = Fake
        km.Sessions.backend_for = staticmethod(lambda sid: Fake())
        km._path_of = lambda sid, now=None: test.path

    def tearDown(self):
        km.Sessions.backend_for = self._saved[0]
        km._path_of = self._saved[1]
        km._merge_sets_memo.clear(); km._merge_sets_memo.update(self._saved[2])

    @staticmethod
    def _user(text, uid, t, author="human"):
        return {"type": "user", "uuid": uid, "t": t, "author": author,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}}

    @staticmethod
    def _assistant(uid, t):
        return {"type": "assistant", "uuid": uid, "t": t, "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}}

    def _session(self, newest_human_t):
        return {"turns": [
            {"id": "t1", "trigger": "u1", "t": T - 38, "end": T - 20, "ended": True,
             "atoms": [self._user("tighten the notes-api search", "u1", T - 38), self._assistant("a1", T - 20)]},
            {"id": "t2", "trigger": "u2", "t": newest_human_t, "end": newest_human_t + 9, "ended": True,
             "atoms": [self._user("the newer message", "u2", newest_human_t), self._assistant("a2", newest_human_t + 9)]}]}

    def _ledger(self, ops):
        with open(self.path, "w") as f:
            for op, content in ops:
                f.write(json.dumps({"type": "queue-operation", "timestamp": _iso(T + 5), "operation": op, "content": content}) + "\n")

    def test_an_overtaken_echo_brings_one_settle_call_with_the_unfiltered_ledger(self):
        mail = "<!-- romp-msg-id: m1 -->hello from web"          # agent mail, not the user's typed input: owed all the same
        self._ledger([("enqueue", "still waiting"), ("enqueue", mail), ("enqueue", "taken"), ("remove", "taken")])
        self.live = [_echo_atom()]
        km._merge_live_atoms(self._session(T + 60), SID)
        self.assertEqual(self.prunes, [T + 60], "the prune runs first, with the same floor")
        self.assertEqual(self.calls, [(SID, T + 60, ["still waiting", mail])])
        self.assertEqual(km._pending_ledger(self.path), ["still waiting", mail], "the settle's owed read keeps every pending text")

    def test_no_settle_without_something_to_rule_on(self):
        self._ledger([])
        for label, live, floor_t in [
                ("nothing overtaken", [_echo_atom()], T - 10),
                ("the same second", [_echo_atom()], T),
                ("already dropped", [_echo_atom(dropped=True)], T + 60),
                ("already landed", [_echo_atom(_landed=True)], T + 60),
                ("no echo at all", [self._assistant("w1", T + 70)], T + 60)]:
            self.calls.clear(); self.live = live
            km._merge_live_atoms(self._session(floor_t), SID)
            self.assertEqual(self.calls, [], label)

    def test_a_missing_path_settles_with_no_ledger_and_a_backend_without_a_settle_is_left_alone(self):
        km._path_of = lambda sid, now=None: None
        self.live = [_echo_atom()]
        km._merge_live_atoms(self._session(T + 60), SID)
        self.assertEqual(self.calls, [(SID, T + 60, [])], "no transcript path: nothing owed by a ledger")
        NoSettle = type("NoSettle", (), {"live_atoms": self.Fake.live_atoms, "prune_live": self.Fake.prune_live})
        km.Sessions.backend_for = staticmethod(lambda sid: NoSettle())
        self.calls.clear()
        km._merge_live_atoms(self._session(T + 60), SID)        # no settle_echoes on this backend (the unowned route): nothing to call
        self.assertEqual(self.calls, [])


class BootFlagsAnOutrunSendInsteadOfRefeeding(_World):
    """Surface 3: the boot reseed's marker. A human send whose text never landed used to be RE-FED at boot
    (restarts never lose typed input); when a later human input has landed after it, the send is not waiting
    anywhere and a re-feed would run it after the conversation moved on — it is flagged instead."""

    def _after_the_send(self, later):
        """The transcript: the running turn, the send's mark taken, no record of the send ever, then `later`."""
        self._write(self._running_turn())
        off = self._size()
        with open(self.tpath, "a") as f:
            for r in later:
                f.write(json.dumps(r) + "\n")
        return off

    def _closing(self, t0=T + 12):
        # the running turn closes without the send: its tool result, then the reply
        return [self._tool_result(t0, "tr1", "a1", "a1"), self._assistant(t0 + 8, "a2", "tr1", "Tightened.")]

    def test_a_later_human_turn_means_flag_not_refeed(self):
        off = self._after_the_send(self._closing() + [self._user(T + 357, "the newer message", "u2", "a2"),
                                                       self._assistant(T + 380, "a3", "u2", "On it.")])
        self._reg(echoes=[self._mirror_echo(off=off, fsid=SID)])
        be = self._backend()
        self.assertEqual(self._queue(), [], "never re-fed: the CLI took a later message while holding this one")
        [a] = self._live(be)
        self.assertTrue(a.get("dropped"), "flagged, so the chat says never delivered and offers the ✕")
        self.assertTrue(self._mirror()[0].get("dropped"))
        self.assertEqual(be.dismiss_echo(SID, uuid=KEY), SENT)

    def test_a_later_message_spliced_in_as_an_attachment_counts_too(self):
        # the most common SDK landing: the later message was fed into a running turn and the CLI spliced it in at
        # a tool boundary — an attachment stamped with its enqueue time, no user record for it ever
        later = [self._qop(T + 300, "enqueue", None), self._tool_result(T + 312, "tr1", "a1", "a1"),
                 self._qop(T + 312, "remove"), self._att(T + 300, "the newer message", "att1", "tr1"),
                 self._assistant(T + 330, "a2", "att1", "On it.")]
        off = self._after_the_send(later)
        self._reg(echoes=[self._mirror_echo(off=off, fsid=SID)])
        be = self._backend()
        self.assertEqual(self._queue(), [], "the spliced message outran the send exactly as a native record would")
        self.assertTrue(self._live(be)[0].get("dropped"))

    def test_only_non_human_records_after_the_send_keep_the_refeed(self):
        later = self._closing() + [
            self._att(T + 38, "<!-- romp-injected --><!-- romp-auto -->Where does this stand?", "att8", "a2"),
            self._att(T + 39, "<task-notification>done</task-notification>", "att9", "att8", commandMode="task-notification"),
            self._user(T + 40, "<task-notification>done</task-notification>", "n1", "a2"),
            self._user(T + 41, "<!-- romp-injected --><!-- romp-auto -->Where does this stand?", "n2", "n1"),
            self._user(T + 42, "[SYSTEM NOTIFICATION - NOT USER INPUT] a hook fired", "n3", "n2"),
            self._user(T + 43, "a skill's markdown payload", "n4", "n3", isMeta=True),
            self._user(T + 44, "<!-- romp-msg-id: m9 -->mail from another session", "n5", "n4", isMeta=True),
            self._user(T + 45, "background task finished", "n6", "n5", origin={"kind": "task-notification"}),
            self._user(T + 46, "[Request interrupted by user]", "n7", "n6"),
            self._tool_result(T + 47, "tr9", "n7", "a1")]
        off = self._after_the_send(later)
        self._reg(echoes=[self._mirror_echo(off=off, fsid=SID)])
        be = self._backend()
        self.assertEqual(self._queue(), [SENT], "nothing the person sent went through: the send is re-fed, as before")
        self.assertFalse(self._live(be)[0].get("dropped"), "a re-queued send renders as queued, never as lost")

    def test_a_human_record_in_the_sends_second_or_before_it_keeps_the_refeed(self):
        off = self._after_the_send(self._closing(t0=T - 5) + [self._user(T, "the same-second message", "u2", "a2")])
        self._reg(echoes=[self._mirror_echo(off=off, fsid=SID)])
        self._backend()
        self.assertEqual(self._queue(), [SENT], "not strictly later: this is not evidence the send was skipped")

    def test_the_scan_starts_at_the_mark_and_a_missing_transcript_answers_none(self):
        # a human record BEFORE the mark, however stamped, is not read: everything that could outrun the send was written after it
        self._write(self._running_turn() + [self._user(T + 300, "an oddly stamped earlier line", "u0", "a1")])
        off = self._size()
        self._reg()
        self.assertIs(sb._input_landed_after(self.state, SID, T, off, SID), False)
        self.assertIs(sb._input_landed_after(self.state, SID, T, None, None), True, "no mark: the whole file is read")
        self.assertIs(sb._input_landed_after(self.state, SID, T, off, "some-other-file"), True, "a mark from another file reads from the start")
        os.remove(self.tpath)
        self.assertIsNone(sb._input_landed_after(self.state, SID, T, None, None), "unreadable: no evidence either way")

    def test_the_human_input_rule_is_the_parses(self):
        def rec(content, **extra):
            r = {"type": "user", "message": {"role": "user", "content": content}}
            r.update(extra)
            return r
        human = sb._human_input_record
        self.assertTrue(human(rec("plain words", promptSource="sdk")), "an unmarked sdk prompt is the composer's")
        self.assertTrue(human(rec("plain words", promptSource="typed")))
        self.assertTrue(human(rec("plain words")), "no promptSource: presumed human, as the parse does")
        self.assertTrue(human(rec([{"type": "text", "text": "in a block"}], promptSource="sdk")))
        self.assertTrue(human(rec("/model opus", promptSource="sdk")), "a typed slash command travelled the same channel")
        wrap = "<command-name>/deploy</command-name>\n<command-message>deploy</command-message>\n<command-args>staging</command-args>"
        self.assertTrue(human(rec(wrap, promptSource="sdk")), "the CLI's wrapper for a typed slash command is the human's command atom")
        self.assertTrue(human(rec(wrap, promptSource="sdk", isMeta=True)), "…before any isMeta skip, as the parse reads it")
        att = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "spliced words"}}
        self.assertTrue(human(att), "a mid-turn splice's attachment is the message's only record")
        self.assertTrue(human({"type": "attachment", "attachment": {"type": "queued_command",
                                                                     "prompt": [{"type": "text", "text": "in a block"}]}}))
        for label, r in [
                ("tool_result", rec([{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}], promptSource="sdk")),
                ("tool_result with text", rec([{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"},
                                               {"type": "text", "text": "and a note"}], promptSource="sdk")),
                ("command stdout", rec("<local-command-stdout>ok</local-command-stdout>")),
                ("command caveat", rec("<local-command-caveat>Caveat: …</local-command-caveat>", promptSource="sdk")),
                ("skill payload", rec("Base directory for this skill: /tmp/notes-api/.claude/skills/deploy\n# Deploy", promptSource="sdk")),
                ("skill payload by link", rec("# Deploy", promptSource="sdk", sourceToolUseID="tu_skill")),
                ("injected attachment", {"type": "attachment", "attachment": {"type": "queued_command",
                                                                                 "prompt": "<!-- romp-injected -->Where does this stand?"}}),
                ("task attachment", {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "done",
                                                                             "commandMode": "task-notification"}}),
                ("stamped attachment", {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "hi",
                                                                                "origin": {"kind": "peer"}}}),
                ("other attachment", {"type": "attachment", "attachment": {"type": "image", "prompt": "x"}}),
                ("empty attachment", {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "  "}}),
                ("isMeta", rec("a skill payload", isMeta=True)),
                ("compact summary", rec("summary", isCompactSummary=True)),
                ("interrupt", rec("[Request interrupted by user for tool use]")),
                ("system wrapper", rec("<system-reminder>note</system-reminder>", promptSource="sdk")),
                ("romp-injected", rec("<!-- romp-injected -->Where does this stand?", promptSource="sdk")),
                ("postal", rec("<!-- romp-msg-id: m1 -->hi", promptSource="sdk")),
                ("origin stamped", rec("done", promptSource="sdk", origin={"kind": "task-notification"})),
                ("peer origin", rec("hi", promptSource="sdk", origin={"kind": "peer"})),
                ("teammate", rec("Another Claude session sent a message: hi", promptSource="sdk")),
                ("empty", rec("   ")),
                ("no content", {"type": "user", "message": {}}),
                ("not a user record", {"type": "assistant", "message": {"content": "x"}})]:
            self.assertFalse(human(r), label)


if __name__ == "__main__":
    unittest.main()
