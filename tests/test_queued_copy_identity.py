#!/usr/bin/env python3
"""Every copy in the kernel's queued group carries an identity (T252c, the user via the manager 2026-09-08): the
chat's pending bubble placed itself by TEXT and ORDINAL because the group carried texts and nothing else, and text
cannot tell a press-time copy from a same-text copy another client queued later, nor pair two landings that arrive
in one frame after a reconnect. Under the authoritative-source rule the identity comes from the store that owns the
queue:

  SDK backend — send() mints the copy's id (the echo key it already minted for the optimistic echo) BEFORE the
  enqueue, so the queued copy, the echo atom and the landed atom share one id; the feed moves the id to a fed
  ledger, and the landed user record is paired with it FIFO per text, at or after the feed time (qid on the chat
  event). The registry mirrors carry the identity across a kernel death (second review): the queue mirror keeps
  each identified copy's id and stamp, the echo mirror keeps the echo's uuid, and a fed copy the dead CLI was
  holding goes back into the queue under its own id. An older kernel's mirror (texts only) restores id-less
  copies, and a copy the backend itself queued (a death notice, the rename ping) carries none: text decides.
  Parked sends — a copy parked in the kernel's own FIFO (compaction, a usage-limit hold, a parked drive op) carries
  the id the CLIENT minted at the press (send-pending.ts newPending posts it with the send; _send_or_park parks it
  as the op's fourth slot and hands it to send() at the drain), so the chip, the bubble and the ✕ agree from the
  press. A copy the kernel parks itself (a nudge, a re-delivery, an older mirror's three-slot record) carries none
  until it reaches the backend, where send() mints one. The kernel takes a client's id only in its own echo form
  and only when the session does not already hold it (a queued copy, a live echo, a parked op); otherwise it mints
  as before. A cancel that names the id removes exactly that copy (tests/test_queued_copy_press_id.py).
  tmux — the CLI's queue-operation records carry timestamps but no ids: each copy carries its enqueue stamp and NO
  id (an id only the ledger copy wore would make the chat reject the tmux echo as another send's); nothing pairs the
  landed record (the kernel does not see the CLI take it), so the chat reads this route by text.

SYNTHETIC fixtures only: a private synthetic sid, the notes-api demo world, hostname-free.
"""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_qid", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_qid", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "5a6b7c8d-1e2f-4a3b-9c4d-5e6f7a8b9c0d"   # private synthetic sid (goal-store fixtures rule)
T0 = 1_800_000_000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class _World:
    """A real SdkBackend bound as the kernel's backend, owning SID, with a thread-less SdkSession, plus the
    discovery a build_session needs (names/ + projects/<cdir>/<SID>.jsonl under a hermetic state root)."""

    def __init__(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        (root / "sdk").mkdir()
        self.cwd = root / "proj"; self.cwd.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(root / "claude")
        self.tpath = Path(sb.transcript_path(str(self.cwd), SID))
        self.tpath.parent.mkdir(parents=True, exist_ok=True)
        self.tpath.write_text("")
        self.be = sb.SdkBackend(str(root), "/bin/true", lambda *a, **k: None)
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": str(self.cwd), "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        # _ensure hands a send to THIS session only while its thread is alive (a dead one is respawned and
        # the spawn dies on the stand-in binary): park a daemon thread in its place for the test's life
        import threading
        self._park = threading.Event()
        self.s.thread = threading.Thread(target=self._park.wait, daemon=True)
        self.s.thread.start()
        self.be.sessions[SID] = self.s
        self.saved_sdk = km._sdk
        km._sdk = lambda: self.be
        proj = root / "projects"
        names = root / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(self.cwd))
        self.saved = (km.jd.NAMES, km.jd.PROJECTS, km.jd.CAPDIR, km.jd.ARCHDIR, km.jd.GOALDIR, km.jd.STATE,
                      km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD)
        km.jd.NAMES, km.jd.PROJECTS = names, proj
        km.jd.CAPDIR, km.jd.ARCHDIR, km.jd.GOALDIR = root / "captions", root / "archive", root / "goals"
        km.jd.STATE = root
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = root / "no-global-claude.md"
        self.now = int(time.time())
        self.tm = {SID: {"state": "working", "since": self.now - 100, "model": "", "effort": "",
                         "context": None, "compactPct": None, "color": None}}
        km._tmux_sessions = lambda: self.tm
        km._chat_fold.clear(); km._parse_cache.clear()
        km._PATH_LINK_CACHE.clear(); km._SPACE_PATH_CACHE.clear()
        km._postal_index_memo[0] = None
        if isinstance(km.jd._discover_cache, dict):
            km.jd._discover_cache.clear()
        # the build_session transcript lives under the kernel's project dir for the cwd
        self.kpath = proj / km.jd._proj_dir(str(self.cwd)).name / (SID + ".jsonl")
        self.kpath.parent.mkdir(parents=True, exist_ok=True)

    def close(self):
        self._park.set()
        (km.jd.NAMES, km.jd.PROJECTS, km.jd.CAPDIR, km.jd.ARCHDIR, km.jd.GOALDIR, km.jd.STATE,
         km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD) = self.saved
        km._sdk = self.saved_sdk
        km._chat_fold.clear(); km._parse_cache.clear()
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        import shutil
        shutil.rmtree(self.td.name, ignore_errors=True)   # a backend writer (the echo mirror) may still be finishing

    def write(self, recs, shift=None):
        # discovery keys on the real clock: shift the fixture to "just now"; a landing that must pair with a
        # feed made during the test is written AT the clock (shift=now-T0), since the CLI's enqueue stamp is
        # never earlier than the feed that handed it the text
        shift = (self.now - 600) - T0 if shift is None else shift
        out = []
        for r in recs:
            r = dict(r)
            r["timestamp"] = iso(datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc).timestamp() + shift)
            out.append(r)
        self.kpath.write_text("".join(json.dumps(r) + "\n" for r in out))
        return shift

    def build(self):
        km._chat_fold.clear(); km._parse_cache.clear()
        return km.build_session(SID, self.now, self.tm)


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "sdk",
            "sessionId": SID, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, tools=(), stop="end_turn"):
    content = [{"type": "text", "text": text}] if text else []
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n, "input": {"command": "true"}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": SID,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def trline(t, tool_use_id, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": SID,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]}}


def attline(t, prompt, uuid, parent):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False,
            "sessionId": SID, "attachment": {"type": "queued_command", "prompt": prompt}}


RUNNING = [uline(T0, "tighten the notes-api search", "u1"), aline(T0 + 10, "Done.", "a1", "u1"),
           uline(T0 + 39, "drop the unused import", "u2", "a1"),
           aline(T0 + 41, "Removing it.", "a2", "u2", tools=("Bash",), stop="tool_use"), trline(T0 + 50, "tu_a2_0", "tr1", "a2")]


class TheSdkQueueCarriesIds(unittest.TestCase):
    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()

    def test_send_mints_the_id_before_the_enqueue_and_the_echo_shares_it(self):
        self.assertTrue(self.w.be.send(SID, "first"))
        self.assertTrue(self.w.be.send(SID, "second"))
        meta = self.w.be.pending_queued_meta(SID)
        self.assertEqual(self.w.be.pending_queued(SID), ["first", "second"])
        self.assertEqual([m["md"] for m in meta], ["first", "second"])
        echoes = {a["uuid"] for a in self.w.be.live_atoms(SID) if a.get("_echo_text")}
        self.assertEqual({m["qid"] for m in meta}, echoes, "the copy's id IS its echo's uuid")
        self.assertTrue(all(isinstance(m["qts"], int) and m["qts"] > 1_700_000_000_000 for m in meta), "an epoch-ms enqueue stamp")

    def test_unqueue_keeps_the_ids_aligned(self):
        self.w.be.send(SID, "a"); self.w.be.send(SID, "b"); self.w.be.send(SID, "c")
        before = self.w.be.pending_queued_meta(SID)
        self.assertEqual(self.w.be.unqueue(SID, 1, expect="b"), "b")
        after = self.w.be.pending_queued_meta(SID)
        self.assertEqual([m["md"] for m in after], ["a", "c"])
        self.assertEqual([m["qid"] for m in after], [before[0]["qid"], before[2]["qid"]])

    def test_a_copy_the_backend_queued_itself_carries_no_id_and_a_restored_queue_keeps_what_its_mirror_carries(self):
        self.w.be.send(SID, "typed")
        self.w.s.enqueue_if_empty("ping")           # the queue is not empty: refused, nothing changes
        self.w.s.enqueue("a backend-minted notice")  # no id: the chat falls back to text for it
        meta = self.w.be.pending_queued_meta(SID)
        self.assertEqual([m["md"] for m in meta], ["typed", "a backend-minted notice"])
        self.assertIsNotNone(meta[0]["qid"]); self.assertIsNone(meta[1]["qid"])
        # a queue restored from the registry after a kernel death: the mirror carries each identified copy's id
        # (second review: a chat that latched the id before the death must find it in the new life)
        del self.w.be.sessions[SID]
        self.assertEqual(self.w.be.pending_queued(SID), ["typed", "a backend-minted notice"], "the persisted mirror")
        self.assertEqual([(m["md"], m["qid"]) for m in self.w.be.pending_queued_meta(SID)],
                         [("typed", meta[0]["qid"]), ("a backend-minted notice", None)], "…with the ids it carried")
        # an older kernel's mirror (texts only) restores id-less copies: legacy, text decides
        reg = sb.read_reg(self.w.be.state_dir, SID); reg.pop("queueMeta", None); sb.write_reg(self.w.be.state_dir, SID, reg)
        self.assertEqual([m["qid"] for m in self.w.be.pending_queued_meta(SID)], [None, None])

    def test_the_feed_moves_the_id_to_the_fed_ledger_and_the_landing_is_paired_fifo_per_text(self):
        self.w.be.send(SID, "ok"); self.w.be.send(SID, "other"); self.w.be.send(SID, "ok")
        ids = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            fed = [self.w.s._pop_for_feed_locked() for _ in range(3)]
        self.assertEqual([f[0] for f in fed], ["ok", "other", "ok"])
        self.assertEqual(self.w.be.pending_queued(SID), [])
        now = int(time.time())
        self.assertEqual(self.w.be.qid_for_landing(SID, "uA", "ok", now + 1), ids[0], "the first landing of 'ok' is the first fed 'ok'")
        self.assertEqual(self.w.be.qid_for_landing(SID, "uA", "ok", now + 1), ids[0], "memoised per record")
        self.assertEqual(self.w.be.qid_for_landing(SID, "uB", "ok", now + 2), ids[2], "the second landing is the second fed 'ok'")
        self.assertIsNone(self.w.be.qid_for_landing(SID, "uC", "ok", now + 3), "no third fed copy: nothing to pair")
        self.assertIsNone(self.w.be.qid_for_landing(SID, "uOld", "other", now - 3600), "a record stamped long before the feed is not this copy's landing")
        self.assertEqual(self.w.be.qid_for_landing(SID, "uD", "other", now), ids[1])

    def test_the_kernels_own_echo_never_consumes_a_fed_entry(self):
        # the echo atom is a user atom too, stamped at the send; between the feed and the CLI's record it is the
        # visible copy — asked as a landing it took the fed entry and the real landing then carried no id (review)
        self.w.be.send(SID, "hello there")
        [qid] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()
        now = int(time.time())
        self.assertIsNone(self.w.be.qid_for_landing(SID, qid, "hello there", now), "an echo uuid is refused")
        self.assertEqual(self.w.be.qid_for_landing(SID, "uReal", "hello there", now + 1), qid, "…so the record still pairs")

    def test_a_batched_record_pairs_every_block_and_a_dropped_copy_leaves_the_ledger(self):
        self.w.be.send(SID, "ok"); self.w.be.send(SID, "ok"); self.w.be.send(SID, "ok")
        ids = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            for _ in range(3): self.w.s._pop_for_feed_locked()
        now = int(time.time())
        self.assertEqual(self.w.be.qids_for_landing(SID, "uJ", ["ok", "ok"], now), [ids[0], ids[1]], "two blocks, two copies, in order")
        self.assertEqual(self.w.be.qids_for_landing(SID, "uJ", ["ok", "ok"], now), [ids[0], ids[1]], "memoised per record")
        self.assertEqual(self.w.be.qid_for_landing(SID, "u3", "ok", now + 1), ids[2], "the third landing is the third copy's, not a stale first")
        # a copy fed and then dropped for good leaves the ledger, so a later same-text landing is not paired with it
        self.w.be.send(SID, "again"); self.w.be.send(SID, "again")
        [d1, d2] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            for _ in range(2): self.w.s._pop_for_feed_locked()
        self.w.be.forget_fed(SID, d1)          # what the never-delivered marking calls
        self.assertEqual(self.w.be.qid_for_landing(SID, "uAg", "again", now + 2), d2)


class TheChatCarriesTheIds(unittest.TestCase):
    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()
        km._pending_ops.pop(SID, None)

    def test_the_queued_group_and_the_landed_atom_share_the_copys_id(self):
        live = self.w.now - T0            # this test's records sit at the clock: the landing follows the feed in time
        self.w.write(RUNNING, shift=live)
        fed_text = "and also update the docstring"
        self.assertTrue(self.w.be.send(SID, fed_text))
        self.assertTrue(self.w.be.send(SID, "later one"))
        qid_fed, qid_later = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        m = self.w.build()
        q = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual(len(q), 1, [e.get("kind") for e in m["events"]])
        self.assertEqual([(t["md"], t.get("qid"), isinstance(t.get("qts"), int)) for t in q[0]["texts"]],
                         [(fed_text, qid_fed, True), ("later one", qid_later, True)])
        # the CLI takes the first copy at the boundary: the feed pops it, the splice record lands it
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()
        recs = RUNNING + [attline(T0 + 55, fed_text, "att1", "tr1"), aline(T0 + 75, "Updated.", "a3", "att1", tools=("Bash",), stop="tool_use")]
        self.w.write(recs, shift=live)
        m = self.w.build()
        landed = [e for e in m["events"] if e.get("kind") == "user" and e.get("md") == fed_text]
        self.assertEqual(len(landed), 1)
        self.assertTrue(landed[0].get("absorbed"))
        self.assertEqual(landed[0].get("qid"), qid_fed, "the landed atom carries the copy's id")
        q = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([t.get("qid") for t in q[0]["texts"]], [qid_later], "the queue now holds the other copy only")

    def test_an_intermediate_build_between_the_feed_and_the_landing_keeps_the_pairing(self):
        live = self.w.now - T0
        self.w.write(RUNNING, shift=live)
        fed_text = "and also update the docstring"
        self.w.be.send(SID, fed_text)
        [qid] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()
        mid = self.w.build()                    # the echo is the visible copy now: a build here must not pair it
        echoes = [e for e in mid["events"] if e.get("kind") == "user" and str(e.get("uuid", "")).startswith("echo:")]
        self.assertEqual([e.get("qid") for e in echoes], [None] * len(echoes), "an echo event carries no landing id (its uuid IS the copy's id)")
        self.w.write(RUNNING + [attline(T0 + 55, fed_text, "att1", "tr1"), aline(T0 + 75, "Updated.", "a3", "att1", tools=("Bash",), stop="tool_use")], shift=live)
        m = self.w.build()
        landed = [e for e in m["events"] if e.get("kind") == "user" and e.get("md") == fed_text and not str(e.get("uuid", "")).startswith("echo:")]
        self.assertEqual([e.get("qid") for e in landed], [qid])

    def test_a_slash_sends_wrapper_record_carries_the_typed_copys_id(self):
        # A slash or skill command typed into the composer with its arguments on the next line: the CLI
        # records it as a <command-name> wrapper (no verbatim copy of the typed text), which the kernel reads
        # as "/deploy staging now" with one space. The chat's own pending bubble retires by id once it has
        # latched the copy's, so the landed event must carry it although the texts differ in whitespace;
        # and the kernel's echo, the other visible copy, retires on the same record (2026-09-10).
        live = self.w.now - T0
        self.w.write(RUNNING, shift=live)
        typed = "/deploy \n\nstaging now"
        self.assertTrue(self.w.be.send(SID, typed))
        [qid] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()
        wrap = ("<command-message>deploy</command-message>\n<command-name>/deploy</command-name>\n"
                "<command-args>staging now</command-args>\n<skill-format>true</skill-format>")
        recs = RUNNING + [dict(uline(T0 + 55, wrap, "c1", "tr1"), isMeta=True, promptId="p1"),
                          dict(uline(T0 + 55, "Base directory for this skill: /tmp/notes-api/.claude/skills/deploy\n\nDeploy the service.",
                                     "c2", "c1"), isMeta=True, promptId="p1"),
                          aline(T0 + 75, "Deploying staging now.", "a3", "c2")]
        self.w.write(recs, shift=live)
        m = self.w.build()
        landed = [e for e in m["events"] if e.get("kind") == "user" and e.get("md") == "/deploy staging now"
                  and not str(e.get("uuid", "")).startswith("echo:")]
        self.assertEqual([e.get("qid") for e in landed], [qid], "the wrapper's event carries the typed copy's id")
        echoes = [e for e in m["events"] if str(e.get("uuid", "")).startswith("echo:")]
        self.assertEqual(echoes, [], "the kernel's echo retired on the same record")
        self.assertNotIn(SID, self.w.be._live, "…and left the live store")

    def test_a_two_block_record_carries_both_copies_ids(self):
        live = self.w.now - T0
        self.w.write(RUNNING, shift=live)
        self.w.be.send(SID, "first of two"); self.w.be.send(SID, "second of two")
        [q1, q2] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            for _ in range(2): self.w.s._pop_for_feed_locked()
        batched = {"type": "user", "timestamp": iso(T0 + 90), "uuid": "u9", "parentUuid": "tr1", "promptSource": "sdk", "sessionId": SID,
                   "message": {"role": "user", "content": [{"type": "text", "text": "first of two"}, {"type": "text", "text": "second of two"}]}}
        self.w.write(RUNNING + [batched], shift=live)
        m = self.w.build()
        rec = [e for e in m["events"] if e.get("kind") == "user" and e.get("uuid") == "u9"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0].get("blocks"), ["first of two", "second of two"])
        self.assertEqual(rec[0].get("qids"), [q1, q2], "one id per block, in block order")
        self.assertIsNone(rec[0].get("qid"), "no single id claims the whole record")

    def test_ids_ride_only_when_each_one_sits_beside_its_own_text(self):
        # the queue can move between the two reads a build makes (a pop and an append keep the length): an id set
        # that does not match the texts one for one is not shipped, so no copy wears another copy's id
        self.w.write(RUNNING)
        self.w.be.send(SID, "A"); self.w.be.send(SID, "B")
        real = self.w.be.pending_queued_meta
        self.w.be.pending_queued_meta = lambda sid: [{"md": "B", "qid": "echo:b", "qts": 1}, {"md": "C", "qid": "echo:c", "qts": 2}]
        try:
            m = self.w.build()
        finally:
            self.w.be.pending_queued_meta = real
        q = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([(x["md"], x.get("qid")) for x in q[0]["texts"]], [("A", None), ("B", None)])

    def test_a_stamp_without_an_id_rides_the_queued_copy_too(self):
        # the tmux route's copies carry an enqueue stamp and no id (TheTmuxQueueCarriesStamps): the group ships the
        # stamp on its own — it rode only beside an id (third review)
        self.w.write(RUNNING)
        self.w.s.enqueue("stamped only")
        self.w.be.pending_queued_meta = lambda sid: [{"md": "stamped only", "qid": None, "qts": 1_700_000_000_000}]
        m = self.w.build()
        q = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([(t["md"], t.get("qid"), t.get("qts")) for t in q[0]["texts"]], [("stamped only", None, 1_700_000_000_000)])

    def test_a_parked_copy_carries_the_id_it_was_pressed_with_and_a_kernel_parked_one_none_until_the_backend(self):
        # the client mints the copy's id at the press and posts it with the send: parked, the op carries it as its
        # fourth slot and the chip shows it, so the chat's bubble and its ✕ name the copy before the drain; at the
        # drain the same id enters the backend's queue and keys the echo. A copy the kernel parks itself (a
        # nudge, a re-delivery) has no press: it carries none until send() mints one.
        self.w.write(RUNNING)
        pressed = "echo:" + "d" * 32
        saved = km._compacting_now
        km._compacting_now = lambda sid: True
        try:
            self.assertTrue(km._send_or_park(self.w.be, SID, "parked words", echo="human", qid=pressed))
        finally:
            km._compacting_now = saved
        km._park_op(SID, ("send", "parked words", None))
        m = self.w.build()
        q = [e for e in m["events"] if e.get("kind") == "queued"]
        self.assertEqual([(x["md"], x.get("qid"), x.get("qts")) for x in q[0]["texts"]],
                         [("parked words", pressed, None), ("parked words", None, None)])
        km._deliver_send_batch(self.w.be, SID, list(km._pending_ops[SID]))
        km._pending_ops.pop(SID, None)
        meta = self.w.be.pending_queued_meta(SID)
        self.assertEqual([m_["md"] for m_ in meta], ["parked words", "parked words"])
        self.assertEqual(meta[0]["qid"], pressed, "the copy enters the backend's queue under the id it was pressed with")
        self.assertTrue(meta[1]["qid"] and meta[1]["qid"].startswith("echo:") and meta[1]["qid"] != pressed,
                        "the kernel-parked copy gains a kernel-minted one there")
        echoes = {a["uuid"] for a in self.w.be.live_atoms(SID) if a.get("_echo_text")}
        self.assertIn(pressed, echoes, "the echo's uuid IS the pressed id")


class TheSdkQueueTakesTheClientsId(unittest.TestCase):
    """SdkBackend.send takes the id the client minted at the press and unqueue removes a copy BY that id: of two
    same-text copies the one named leaves, and its echo (keyed by the same id) goes with it while the other's stays.
    The handler's shape check reads this backend's queue and live echoes, so an id the session already holds is
    refused and the kernel mints instead; a queue read or a live-echo read that raises refuses too, and the line it
    logs names the session alone."""

    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()
        km._pending_ops.pop(SID, None)

    def test_send_keys_the_copy_and_its_echo_by_the_pressed_id(self):
        a, b = "echo:" + "a" * 32, "echo:" + "b" * 32
        self.assertTrue(self.w.be.send(SID, "same words", qid=a))
        self.assertTrue(self.w.be.send(SID, "same words", qid=b))
        self.assertEqual([(m["md"], m["qid"]) for m in self.w.be.pending_queued_meta(SID)], [("same words", a), ("same words", b)])
        self.assertEqual({x["uuid"] for x in self.w.be.live_atoms(SID) if x.get("_echo_text")}, {a, b})

    def test_unqueue_by_id_pops_the_named_copy_and_its_echo_leaving_the_same_text_other(self):
        a, b = "echo:" + "a" * 32, "echo:" + "b" * 32
        self.w.be.send(SID, "same words", qid=a)
        self.w.be.send(SID, "same words", qid=b)
        self.assertEqual(self.w.be.unqueue(SID, 0, "same words", qid=b), "same words", "a stale index and the shared words: the id wins")
        self.assertEqual([m["qid"] for m in self.w.be.pending_queued_meta(SID)], [a])
        self.assertEqual({x["uuid"] for x in self.w.be.live_atoms(SID) if x.get("_echo_text")}, {a},
                         "the cancelled copy's echo went with it; the other copy's echo stays")
        self.assertIsNone(self.w.be.unqueue(SID, 0, "same words", qid=b), "an id the queue no longer holds: a miss")
        self.assertEqual([m["qid"] for m in self.w.be.pending_queued_meta(SID)], [a], "and nothing else was popped")
        self.assertEqual(self.w.be.unqueue(SID, 0, "same words"), "same words", "an id-less cancel keeps the text path")
        self.assertEqual(self.w.be.pending_queued(SID), [])

    def test_unqueue_by_id_whose_echo_is_already_gone_leaves_the_same_text_neighbours_echo(self):
        a, b = "echo:" + "a" * 32, "echo:" + "b" * 32
        self.w.be.send(SID, "same words", qid=a)
        self.w.be.send(SID, "same words", qid=b)
        with self.w.be._live_lock:
            self.w.be._live[SID].pop(b)                   # b's echo retired ahead of its copy
        self.assertEqual(self.w.be.unqueue(SID, 0, "same words", qid=b), "same words")
        self.assertEqual([m["qid"] for m in self.w.be.pending_queued_meta(SID)], [a])
        self.assertEqual({x["uuid"] for x in self.w.be.live_atoms(SID) if x.get("_echo_text")}, {a},
                         "the id said whose echo to drop, and it was gone: a's, the same-words neighbour's, stays")

    def test_the_handler_takes_a_fresh_id_in_the_echo_form_and_refuses_one_the_session_holds(self):
        fresh, queued, fed = "echo:" + "f" * 32, "echo:" + "1" * 32, "echo:" + "2" * 32
        self.w.be.send(SID, "queued copy", qid=queued)
        self.w.be.send(SID, "fed copy", qid=fed)
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()           # the CLI took the first: its echo is live, its id is in _fed_meta
        self.assertEqual(km._client_qid({"qid": fresh}, SID, self.w.be), fresh)
        self.assertIsNone(km._client_qid({"qid": queued}, SID, self.w.be), "held by the queue")
        self.assertIsNone(km._client_qid({"qid": fed}, SID, self.w.be), "held by a live echo (fed, not landed)")
        for bad in ("", "s-1", "echo:", "echo:" + "F" * 32, "echo:" + "f" * 8, "echo:" + "f" * 70, "echo:" + "f" * 32 + "\n", 7, None):
            self.assertIsNone(km._client_qid({"qid": bad}, SID, self.w.be), repr(bad))
        self.assertIsNone(km._client_qid({}, SID, self.w.be))

    def test_the_handler_refuses_a_fresh_id_when_the_hold_cannot_be_checked_and_names_the_sid_only(self):
        # fails toward the kernel's own id: a queue read or a live-echo read that raises is taken as "held", the
        # client's id is refused, and the stderr line names the session and nothing the client sent (the id, the words)
        fresh = "echo:" + "f" * 32
        msg = {"qid": fresh, "md": "private words"}

        def boom(*a, **k):
            raise RuntimeError("queue unreadable")

        real_meta, real_atoms = self.w.be.pending_queued_meta, self.w.be.live_atoms
        try:
            self.w.be.pending_queued_meta = boom
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._client_qid(msg, SID, self.w.be), "the queue could not be read")
            self.assertIn(SID, err.getvalue())
            self.assertNotIn(fresh, err.getvalue())
            self.assertNotIn("private words", err.getvalue())
            self.w.be.pending_queued_meta = lambda sid: []
            self.w.be.live_atoms = boom
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._client_qid(msg, SID, self.w.be), "the live echoes could not be read")
            self.assertIn(SID, err.getvalue())
            self.assertNotIn(fresh, err.getvalue())
            self.assertNotIn("private words", err.getvalue())
            self.assertNotIn("queue unreadable", err.getvalue(), "the fault's text is not the line's either")
        finally:
            self.w.be.pending_queued_meta, self.w.be.live_atoms = real_meta, real_atoms
        self.assertEqual(km._client_qid(msg, SID, self.w.be), fresh, "the same id is taken once both reads answer")


class TheTmuxQueueCarriesStamps(unittest.TestCase):
    def test_each_copy_carries_its_enqueue_stamp_and_no_id_since_nothing_on_this_route_could_share_one(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "t.jsonl"
        recs = [{"type": "queue-operation", "operation": "enqueue", "content": "one", "timestamp": iso(T0 + 5)},
                {"type": "queue-operation", "operation": "enqueue", "content": "two", "timestamp": iso(T0 + 9)},
                {"type": "queue-operation", "operation": "dequeue", "timestamp": iso(T0 + 12)}]
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        km._queued_parse_cache.clear()
        self.assertEqual(km._pending_queued(str(p)), ["two"])
        meta = km._pending_queued_meta(str(p))
        self.assertEqual([m["md"] for m in meta], ["two"])
        self.assertEqual(meta[0]["qts"], (T0 + 9) * 1000)
        # no id: the tmux echo is minted before the CLI writes its enqueue record and the landing carries nothing,
        # so an id only the ledger copy wore would make the chat reject the echo as another send's (review)
        self.assertIsNone(meta[0]["qid"])
        td.cleanup()


class IdentitySurvivesTheKernelsDeath(unittest.TestCase):
    """A kernel restart used to re-mint everything (second review): the queue mirror carried texts only, so the
    restored copies were id-less; the echo mirror carried no uuid, so the reseeded echo wore a new one; and a fed
    copy re-headed or re-delivered after the death lost its id on the way back into the queue. A chat that had
    latched the copy's id before the death then found nothing in the frame wearing it. Both mirrors carry the
    identity now, and the two ways back into the queue carry it too, so the restored copy, the reseeded echo and
    the eventual landing still share one id."""

    def setUp(self):
        self.w = _World()
        self.w.write(RUNNING, shift=self.w.now - T0)   # at the clock: a landing must follow the feed in time
        self._parks = []

    def tearDown(self):
        for park in self._parks:
            park.set()
        self.w.close()

    def _restart(self):
        """The kernel dies and boots: a fresh backend over the same state root re-seeds the echo mirror (its
        __init__), and the session is rebuilt from its registry record, the way the boot reconcile seeds it."""
        import threading
        self.w._park.set()
        self.w.be.sessions.pop(SID, None)
        be2 = sb.SdkBackend(self.w.be.state_dir, "/bin/true", lambda *a, **k: None)
        s2 = sb.SdkSession(be2, dict(sb.read_reg(be2.state_dir, SID)))
        park = threading.Event(); self._parks.append(park)
        s2.thread = threading.Thread(target=park.wait, daemon=True); s2.thread.start()
        be2.sessions[SID] = s2
        km._sdk = lambda: be2
        return be2, s2

    def test_the_restored_queue_and_the_reseeded_echo_keep_the_copys_id_and_the_landing_pairs_with_it(self):
        self.assertTrue(self.w.be.send(SID, "rename it"))
        [qid] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        be2, s2 = self._restart()
        self.assertEqual([(m["md"], m["qid"]) for m in be2.pending_queued_meta(SID)], [("rename it", qid)],
                         "the queue mirror carries the id across the death")
        self.assertEqual([(a["uuid"], bool(a.get("dropped"))) for a in be2.live_atoms(SID) if a.get("_echo_text")], [(qid, False)],
                         "the reseeded echo keeps its uuid, which IS the id")
        with s2._lock:
            s2._pop_for_feed_locked()
        self.w.write(RUNNING + [uline(T0 + 55, "rename it", "u3", "tr1")], shift=self.w.now - T0)
        m = self.w.build()
        landed = [e for e in m["events"] if e.get("kind") == "user" and e.get("uuid") == "u3"]
        self.assertEqual([e.get("qid") for e in landed], [qid], "the landing pairs with the same id in the new life")

    def test_a_fed_copy_the_dead_cli_was_holding_is_re_delivered_under_its_own_id(self):
        self.assertTrue(self.w.be.send(SID, "go on"))
        [qid] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()           # the CLI took it…
        self.w.s._persist_queue()                     # …so the mirror holds an empty queue and the unlanded echo
        be2, s2 = self._restart()                     # boot: the echo's text is in no queue and never landed: re-delivered
        self.assertEqual([(m["md"], m["qid"]) for m in be2.pending_queued_meta(SID)], [("go on", qid)],
                         "back in the queue under the echo's own uuid")
        self.assertEqual([(a["uuid"], bool(a.get("dropped"))) for a in be2.live_atoms(SID) if a.get("_echo_text")], [(qid, False)])

    def test_a_re_delivered_copy_joins_a_surviving_identified_queue_with_both_ids_intact(self):
        self.assertTrue(self.w.be.send(SID, "go on"))
        self.assertTrue(self.w.be.send(SID, "keep this"))
        [qid_go, qid_keep] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()           # the CLI took the first…
        self.w.s._persist_queue()                     # …so the mirror holds the second, and the first's unlanded echo
        be2, s2 = self._restart()
        self.assertEqual([(m["md"], m["qid"]) for m in be2.pending_queued_meta(SID)], [("keep this", qid_keep), ("go on", qid_go)],
                         "the survivor first, the re-delivered copy behind it, each under its own id (third review)")

    def test_the_lost_first_of_two_identical_sends_is_seen_lost_beside_the_queued_second(self):
        self.assertTrue(self.w.be.send(SID, "ok"))
        self.assertTrue(self.w.be.send(SID, "ok"))
        [qa, qb] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()           # the CLI took the first copy…
        self.w.s._persist_queue()                     # …the mirror holds the second, and both echoes
        be2, s2 = self._restart()                     # the first died with the CLI; by TEXT it looked queued (third review)
        self.assertEqual([(m["md"], m["qid"]) for m in be2.pending_queued_meta(SID)], [("ok", qb), ("ok", qa)],
                         "the survivor, then the lost copy re-delivered under its own id")
        self.assertEqual(sorted((a["uuid"], bool(a.get("dropped"))) for a in be2.live_atoms(SID) if a.get("_echo_text")),
                         sorted([(qa, False), (qb, False)]))

    def test_a_dead_spawns_re_delivery_into_a_live_session_carries_the_id_and_clears_the_stale_ledger_entry(self):
        self.assertTrue(self.w.be.send(SID, "go on"))
        [qid] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        with self.w.s._lock:
            self.w.s._pop_for_feed_locked()
        self.assertEqual([f["qid"] for f in self.w.s._fed_meta], [qid])
        self.w.be._mark_dropped_echoes(SID, self.w.s.pending())    # the dead-spawn caller: the live session re-queues it
        self.assertEqual([(m["md"], m["qid"]) for m in self.w.s.pending_meta()], [("go on", qid)])
        self.assertEqual(self.w.s._fed_meta, [], "a copy back in the queue is no longer fed: its stale entry cannot pair a later landing")

    def test_a_stranded_fed_copy_re_heads_the_queue_with_its_id(self):
        self.assertTrue(self.w.be.send(SID, "go on"))
        self.assertTrue(self.w.be.send(SID, "then this"))
        [qid, later] = [m["qid"] for m in self.w.be.pending_queued_meta(SID)]
        s = self.w.s
        with s._lock:
            s._pop_for_feed_locked()
        s._inflight_texts.append("go on"); s.inflight = 1; s.resume_sid = None   # fed to a client that is now gone
        s._reconcile_stranded()
        self.assertEqual([(m["md"], m["qid"]) for m in s.pending_meta()], [("go on", qid), ("then this", later)],
                         "the fed copy is back at the head under its own id")
        self.assertEqual(s._fed_meta, [], "…and out of the fed ledger")


class TheQueueMirrorAlignsByPosition(unittest.TestCase):
    """The mirror lists every position (text alone for an id-less copy), so a restore aligns the mirrored run as one
    block of the queue: the boot paths that edit reg['queue'] by text — a notice prepended, a re-delivered send
    appended — shift it whole. Only when no block matches (a copy removed by text) does it fall to first-in-first-out
    by text over the identified entries, and an older mirror restores nothing (third review)."""

    def test_the_mirrored_run_aligns_as_a_block_after_text_only_edits_else_by_text(self):
        meta = [{"text": "go"}, {"text": "go", "qid": "echo:b", "qts": 5}]
        self.assertEqual(sb.queue_meta_from_reg({"queue": ["go", "go"], "queueMeta": meta}), [None, {"qid": "echo:b", "qts": 5}])
        self.assertEqual(sb.queue_meta_from_reg({"queue": ["a notice", "go", "go", "later"],
                                                 "queueMeta": meta + [{"text": "later", "qid": "echo:c", "qts": None}]}),
                         [None, None, {"qid": "echo:b", "qts": 5}, {"qid": "echo:c", "qts": None}],
                         "prepended and appended around the block: the id stays on the SECOND go")
        self.assertEqual(sb.queue_meta_from_reg({"queue": ["go"], "queueMeta": meta}), [{"qid": "echo:b", "qts": 5}],
                         "no block matches: the identified entry goes to the first copy of its text")
        self.assertEqual(sb.queue_meta_from_reg({"queue": ["go"]}), [None], "an older mirror")
        self.assertEqual(sb.queue_meta_from_reg({"queue": ["go"], "queueMeta": "junk"}), [None])


if __name__ == "__main__":
    unittest.main()
