#!/usr/bin/env python3
"""A send fed into a RUNNING turn is held by the CLI until its next tool boundary, then spliced in as a
queued_command attachment stamped with the ENQUEUE time. Its atom is placed at the LANDING time — the
boundary's own record, the attachment's file-order predecessor — so it lands BELOW the tool calls that
streamed while it waited, where the model read it (T252d, the user 2026-09-08); the send time rides
along as `sentAt`. Until that splice the kernel's input echo is the message's only visible record, and
two things must hold end to end:

  1. the echo of a FED, unlanded text outlives its sibling's landing — no floor retires an SDK echo at
     all (sdk_backend.prune_live, 2026-09-06: the CLI's image-path extraction is a composer paste-hook
     behaviour that stream-json input never reaches) — and retires exactly when the absorbed atom's text
     lands: the kernel's _atom_user_texts reads the queued_command text off the parsed absorbed atom, so
     the by-text prune fires on it and the message never renders twice;
  2. the chat event for that atom says so (`absorbed`, its `ts` the landing, plus `sentAt`: when the
     user sent it), so the client's tail bubble is replaced in place and its hover can say when the
     message was sent.

The sdk_backend twin of the image-path predicate is pinned against the kernel's, and both against the
CLI paste hook's extension set (ImagePathPredicateTwins names the source). The TEXT KEY the kernel's
_atom_user_texts and the backend's scan + prune compare under is ONE function (session_backend.
echo_text_key), pinned in OneTextRuleAcrossKernelAndBackend: the backend's landing scan can never find a
text the kernel-fed prune cannot retire. SYNTHETIC fixtures only (a private synthetic sid, the notes-api
demo domain)."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_fed_echo", os.path.join(BIN, "romp-kernel")).load_module()
sb = SourceFileLoader("romp_sdk_backend_fed_echo", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
em = km.em

SID = "1f3e5d7c-9b1a-4c2d-8e6f-0a1b2c3d4e5f"   # private synthetic sid (goal-store fixtures rule)
T0 = 1_800_000_000
NOW = T0 + 3600
FED = ("Replying to this highlighted code (/tmp/notes-api/notes/api.py:42):\n"
       "> def list_notes():\n\nrename this to fetch_notes")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="sdk"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, tools=(), stop="end_turn"):
    content = [{"type": "text", "text": text}] if text else []
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n, "input": {"command": "true"}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def trline(t, tool_use_id, uuid, parent, content="ok"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                                                     "content": content}]}}


def attline(t, prompt, uuid, parent):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isSidechain": False, "attachment": {"type": "queued_command", "prompt": prompt}}


def running_turn():
    """The first staged comment lands at T0+39 and opens a turn whose tool call is still running."""
    return [
        uline(T0, "tighten the notes-api search", "u1"),
        aline(T0 + 10, "Done.", "a1", "u1"),
        uline(T0 + 39, "the first comment: drop the unused import", "u2", "a1"),
        aline(T0 + 41, "Removing it.", "a2", "u2", tools=("Bash",), stop="tool_use"),
        trline(T0 + 50, "tu_a2_0", "tr1", "a2"),
    ]


def spliced_tail():
    """The CLI took the second comment at the T0+50 boundary: the attachment carries the send time."""
    return [
        attline(T0 + 38, FED, "att1", "tr1"),
        aline(T0 + 75, "Renamed it as well.", "a3", "att1"),
    ]


class _World:
    """A real SdkBackend bound as the kernel's backend, owning SID, with a thread-less SdkSession."""

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
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True,
               "cwd": str(self.cwd), "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = self.s
        self.saved = km._sdk
        km._sdk = lambda: self.be

    def close(self):
        km._sdk = self.saved
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.td.cleanup()

    def write(self, recs):
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))

    def parse(self):
        return em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)],
                                postal_log=[], now=NOW, sdk_human=True)

    def echo(self, text, t):
        key = "echo:fed"
        self.be._live[SID] = {key: {"type": "user", "uuid": key, "session_id": SID, "t": t,
                                     "parentUuid": None, "author": "human", "_echo_text": text,
                                     "message": {"role": "user", "content": [{"type": "text", "text": text}]}}}
        return key


class FedEchoSurvivesUntilTheSpliceLands(unittest.TestCase):
    def setUp(self):
        self.w = _World()
        self.assertTrue(self.w.be.owns(SID))
        self.assertIs(km.Sessions.backend_for(SID), self.w.be)

    def tearDown(self):
        self.w.close()

    def _texts(self, session):
        return [t for turn in session["turns"] for a in turn["atoms"] for t in km._atom_user_texts(a)]

    def test_the_fed_echo_outlives_the_siblings_landing(self):
        # the second comment was fed at second 38; the first landed at 39 and is the human floor.
        # Pre-change: the quote chip's path made the echo "path-bearing" and the floor retired it —
        # the store emptied with the message still in the CLI's queue.
        self.w.write(running_turn())
        key = self.w.echo(FED, T0 + 38)
        self.w.s.inflight = 1
        self.w.s._inflight_texts.append(FED)
        parsed = self.w.parse()
        self.assertEqual(km._human_turn_floor(parsed), T0 + 39, "the sibling's landing IS the floor")
        merged = km._merge_live_atoms(parsed, SID)
        self.assertIn(key, self.w.be._live.get(SID, {}), "the fed echo survives the floor")
        self.assertIn(FED, self._texts(merged), "…and the chat still shows the message, once")
        self.assertEqual(self._texts(merged).count(FED), 1)

    def test_the_landing_retires_it_by_text_through_the_queued_command_atom(self):
        self.w.write(running_turn() + spliced_tail())
        key = self.w.echo(FED, T0 + 38)
        self.w.s.inflight = 1
        self.w.s._inflight_texts.append(FED)          # still fed: the turn has not settled
        parsed = self.w.parse()
        absorbed = [a for t in parsed["turns"] for a in t["atoms"] if a.get("absorbed")]
        self.assertEqual([a["uuid"] for a in absorbed], ["att1"])
        self.assertIn(FED, km._atom_user_texts(absorbed[0]),
                      "the kernel's landing set covers the queued_command shape")
        merged = km._merge_live_atoms(parsed, SID)
        self.assertNotIn(SID, self.w.be._live, "the absorbed atom's text landed → the echo retires")
        self.assertEqual(self._texts(merged).count(FED), 1, "never twice: the atom, not the echo")

    def test_an_unfed_image_echo_survives_the_floor_too(self):
        # nobody holds it, nothing landed it: until 2026-09-06 the image-extraction floor retired this
        # echo the moment the sibling landed — but on this route the CLI never rewrites the path, so the
        # text WILL land verbatim, and the echo waits for that (or for the dropped marking)
        self.w.write(running_turn())
        key = self.w.echo("compare with /tmp/notes-api/docs/before.png", T0 + 38)
        merged = km._merge_live_atoms(self.w.parse(), SID)
        self.assertIn(key, self.w.be._live.get(SID, {}))
        self.assertEqual(self._texts(merged).count("compare with /tmp/notes-api/docs/before.png"), 1)



class AbsorbedAtomCarriesItsLandingTime(unittest.TestCase):
    """The attachment record is stamped with the SEND time; the CLI wrote it at the splice, right
    after the tool boundary it waited for — so the file-order predecessor (the tool result at T0+50)
    is when the session took the message. Stamped at emit time from the ingest-order (seq, ts) list,
    on the full parse and on the assembly fold alike."""

    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()

    def _absorbed(self, session):
        return [a for t in session["turns"] for a in t["atoms"] if a.get("absorbed")]

    def test_full_parse_stamps_the_predecessors_time(self):
        self.w.write(running_turn() + spliced_tail())
        a = self._absorbed(self.w.parse())
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["t"], T0 + 50, "placed at the LANDING time — the boundary before it in file order — below the steps that ran meanwhile (T252d)")
        self.assertEqual(a[0]["sentAt"], T0 + 38, "the send time rides along for the bubble's hover")
        self.assertNotIn("landedT", a[0], "one time is the placement; the other is sentAt")
        self.assertNotIn("_seq", a[0], "the parse still strips its private ordering key")

    def test_the_fold_stamps_it_too(self):
        # the live shape: the running turn is already parsed and cached when the splice records append
        self.w.write(running_turn())
        mode = []
        em.parse_session(str(self.w.tpath), rompuuid=SID, candidate_files=[str(self.w.tpath)],
                         postal_log=[], now=NOW, sdk_human=True, asm_mode_out=mode)
        self.assertEqual(mode, ["full"])
        with open(self.w.tpath, "a") as f:
            for r in spliced_tail():
                f.write(json.dumps(r) + "\n")
        mode = []
        out = em.parse_session(str(self.w.tpath), rompuuid=SID, candidate_files=[str(self.w.tpath)],
                               postal_log=[], now=NOW, sdk_human=True, asm_mode_out=mode)
        self.assertEqual(mode, ["fold"], "the appended splice folds onto the cached parse")
        a = self._absorbed(out)
        self.assertEqual([(x["t"], x["sentAt"]) for x in a], [(T0 + 50, T0 + 38)])

    def test_two_splices_at_one_boundary_read_that_boundary(self):
        # the CLI drains its queue at a boundary: the second attachment's file-order neighbour is the
        # FIRST attachment, whose stamp is its own send time — not a landing. Attachments are skipped
        # as witnesses, so both read the tool result they waited for.
        second = "and update the docstring"
        recs = running_turn() + [attline(T0 + 38, FED, "att1", "tr1"),
                                 attline(T0 + 44, second, "att2", "att1"),
                                 aline(T0 + 75, "Both done.", "a3", "att2")]
        self.w.write(recs)
        a = self._absorbed(self.w.parse())
        self.assertEqual([(x["uuid"], x["t"], x["sentAt"]) for x in a], [("att1", T0 + 50, T0 + 38), ("att2", T0 + 50, T0 + 44)],
                         "both placed at the boundary they waited for, in send order (the attachment's file order breaks the tie)")

    def test_a_witness_stamped_before_the_send_clamps_to_the_send(self):
        # whole-second stamps can invert a sub-second gap between the tool_result and the enqueue (the
        # corpus's worst case is -0.2 s); the landing then reads as the send, never earlier, so the
        # chat's `landedAt` can never precede the bubble's own `ts`
        recs = running_turn()[:-1] + [trline(T0 + 37, "tu_a2_0", "tr1", "a2"),
                                      attline(T0 + 38, FED, "att1", "tr1"),
                                      aline(T0 + 75, "Renamed.", "a3", "att1")]
        self.w.write(recs)
        a = self._absorbed(self.w.parse())
        self.assertEqual([(x["t"], x["sentAt"]) for x in a], [(T0 + 38, T0 + 38)])

    def test_an_attachment_with_no_predecessor_carries_no_stamp(self):
        # nothing before it in the read → nothing truthful to stamp; the field is simply absent
        self.w.write([attline(T0 + 38, FED, "att1", None), aline(T0 + 75, "ok", "a1", "att1")])
        a = self._absorbed(self.w.parse())
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["t"], a[0]["sentAt"]), (T0 + 38, T0 + 38), "no landing witness: the send time is the only truthful place")


class ChatEventSaysAbsorbed(unittest.TestCase):
    """build_session's kind:"user" event for the absorbed atom carries `absorbed`, its landing as `ts`
    and the send as `sentAt`, so the client's tail bubble is replaced in place and its hover can say
    when the message was sent. A synthetic session discovery can see
    (names/ + projects/<cdir>/<SID>.jsonl under a hermetic state root), the test_chat_fold shape."""

    def setUp(self):
        self.w = _World()
        root = Path(self.w.td.name)
        proj = root / "projects"
        self.tpath = proj / km.jd._proj_dir(str(self.w.cwd)).name / (SID + ".jsonl")
        self.tpath.parent.mkdir(parents=True, exist_ok=True)
        names = root / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(self.w.cwd))
        self.saved = (km.jd.NAMES, km.jd.PROJECTS, km.jd.CAPDIR, km.jd.ARCHDIR, km.jd.GOALDIR, km.jd.STATE,
                      km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD)
        km.jd.NAMES, km.jd.PROJECTS = names, proj
        km.jd.CAPDIR, km.jd.ARCHDIR, km.jd.GOALDIR = root / "captions", root / "archive", root / "goals"
        km.jd.STATE = root
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = root / "no-global-claude.md"
        self.now = int(__import__("time").time())       # discovery keys on the real clock
        self.tm = {SID: {"state": "working", "since": self.now - 100, "model": "", "effort": "",
                         "context": None, "compactPct": None, "color": None}}
        km._tmux_sessions = lambda: self.tm
        km._chat_fold.clear(); km._parse_cache.clear()
        km._PATH_LINK_CACHE.clear(); km._SPACE_PATH_CACHE.clear()
        km._postal_index_memo[0] = None
        if isinstance(km.jd._discover_cache, dict):
            km.jd._discover_cache.clear()

    def tearDown(self):
        (km.jd.NAMES, km.jd.PROJECTS, km.jd.CAPDIR, km.jd.ARCHDIR, km.jd.GOALDIR, km.jd.STATE,
         km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD) = self.saved
        km._chat_fold.clear(); km._parse_cache.clear()
        self.w.close()

    def _recs(self, base):
        # discovery keys on the real clock: shift the fixture to "just now"
        shift = (self.now - 600) - T0
        out = []
        for r in base:
            r = dict(r)
            r["timestamp"] = iso(datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%S.000Z")
                                 .replace(tzinfo=timezone.utc).timestamp() + shift)
            out.append(r)
        return out, shift

    def test_the_user_event_carries_absorbed_and_landed_at(self):
        recs, shift = self._recs(running_turn() + spliced_tail())
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))
        m = km.build_session(SID, self.now, self.tm)
        self.assertIsNotNone(m)
        users = [e for e in m["events"] if e.get("kind") == "user" and e.get("md")]
        ab = [e for e in users if e.get("absorbed")]
        self.assertEqual([e["uuid"] for e in ab], ["att1"])
        self.assertEqual(ab[0]["md"], FED)
        self.assertEqual(ab[0]["ts"][:19], iso(T0 + 50 + shift)[:19], "ts is the LANDING: the event sits below the steps that ran while the send waited (T252d)")
        self.assertEqual(ab[0]["sentAt"], T0 + 38 + shift, "the send time rides along for the hover")
        self.assertNotIn("landedAt", ab[0])
        self.assertTrue(all("absorbed" not in e for e in users if e["uuid"] != "att1"),
                        "a native user record never wears the flag")

    def test_a_record_of_several_sends_carries_its_blocks(self):
        """Back-to-back sends the CLI takes together land as ONE user record with a text block each (the
        shape _atom_user_texts prunes the kernel's echoes against). The chat's own pending bubbles end
        on an exact match with a landed event's text, and `md` is the blocks joined, which matches
        none of them — so the event ships `blocks`, each under the one text key, and only when there
        are two or more (2026-09-07 review)."""
        first, second = "rename this to fetch_notes", "and update the docstring  "
        batched = {"type": "user", "timestamp": iso(T0 + 90), "uuid": "u9", "parentUuid": "a3", "promptSource": "sdk",
                   "message": {"role": "user", "content": [{"type": "text", "text": first},
                                                           {"type": "text", "text": second}]}}
        recs, shift = self._recs(running_turn() + spliced_tail() + [batched, aline(T0 + 95, "Both done.", "a4", "u9")])
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))
        m = km.build_session(SID, self.now, self.tm)
        self.assertIsNotNone(m)
        users = {e["uuid"]: e for e in m["events"] if e.get("kind") == "user" and e.get("md")}
        self.assertEqual(users["u9"]["blocks"], [first, second.strip()], "one entry per block, under echo_text_key")
        self.assertEqual(users["u9"]["md"], (first + " " + second).strip(), "md stays the joined text")
        self.assertEqual([u for u, e in users.items() if "blocks" in e], ["u9"],
                         "a single-block record and the absorbed attachment ship no blocks")


class OneTextRuleAcrossKernelAndBackend(unittest.TestCase):
    """The by-text prune has two sides in two modules: the kernel builds the keys (_atom_user_texts →
    tx_user_texts) and the SDK backend compares an echo's text against them (prune_live), while its boot
    scan (_text_landed via _landed_texts) decides which echoes are "landed, merely un-pruned". Until
    2026-09-06 the scan collapsed whitespace, the kernel stripped, and the prune compared raw — three
    rules, so a trailing-newline send was found yet never retired. One function now, imported by both
    modules; the property tested here is that the scan's match set for a record IS the kernel's key set
    for the same record, and that an echo the scan would find retires through the real merge. A raw
    command WRAPPER record (<command-name>, <command-args>) is the one designed exception and is not
    listed: the kernel keys the PARSED command atom, while the scan reads the raw record and derives the
    same "/name args" itself (sdk_backend._command_invocation); that agreement is pinned through the real
    parse in test_kernel_slash_echo_retire.BackendScanAgrees."""

    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()

    def _texts(self, session):
        return [t for turn in session["turns"] for a in turn["atoms"] for t in km._atom_user_texts(a)]

    def test_the_kernel_and_the_backend_import_the_same_key_function(self):
        k, b = km.sb.echo_text_key, sb.echo_text_key
        self.assertEqual(os.path.realpath(k.__code__.co_filename), os.path.realpath(b.__code__.co_filename),
                         "one definition, in session_backend.py")
        self.assertEqual(k.__code__.co_code, b.__code__.co_code)
        for probe in (" a\n", "a  b", "a\r\nb", "\n\t", "", None, 7):
            self.assertEqual(k(probe), b(probe))
        self.assertEqual(k(" fix the tests \n"), "fix the tests")
        self.assertEqual(k("one  two\nthree"), "one  two\nthree", "outer whitespace only")

    def test_the_scan_match_set_is_the_kernel_key_set_for_every_record_shape(self):
        recs = [
            uline(T0, " fix the tests \n", "u9"),                                       # edge whitespace, verbatim
            uline(T0, [{"type": "text", "text": "one  two"}, {"type": "text", "text": " three "}], "u9"),
            uline(T0, "a\r\nb", "u9"),                                                  # a bare CR, preserved
            uline(T0, [{"type": "text", "text": "solo"}], "u9"),
            uline(T0, [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], "u9"),
            uline(T0, "/deploy  staging\nnow", "u9"),                                   # slash-shaped: both add the command key
            uline(T0, [{"type": "text", "text": "/deploy staging"}, {"type": "text", "text": "/rollback \n now"}], "u9"),
            uline(T0, "/tmp/notes-api/api.py  is broken", "u9"),                         # a path: a plain message, no command key
            # not listed: a raw <command-name> WRAPPER record. The kernel keys the parsed command atom and the
            # scan derives "/name args" from the raw record itself (the class docstring; BackendScanAgrees pins it).
        ]
        for rec in recs:
            self.assertEqual(sb._landed_texts(rec), set(km._atom_user_texts(rec)), json.dumps(rec)[:120])
        # the absorbed shape: the scan reads the queued_command attachment, the kernel the parsed atom
        self.w.write(running_turn() + spliced_tail())
        absorbed = [a for t in self.w.parse()["turns"] for a in t["atoms"] if a.get("absorbed")]
        self.assertEqual([a["uuid"] for a in absorbed], ["att1"])
        self.assertEqual(sb._landed_texts(attline(T0 + 38, FED, "att1", "tr1")), set(km._atom_user_texts(absorbed[0])))

    def test_an_edge_whitespace_echo_retires_when_its_text_lands(self):
        # `romp send` passes its argument verbatim; the record stores the trailing newline verbatim; the
        # kernel's key strips it. Pre-fix the raw echo text never matched the key and the echo stayed in
        # the store forever (hidden by the display dedup, so the user saw nothing — and no exit).
        text = FED + "\n"
        self.w.write(running_turn() + [uline(T0 + 80, text, "u3", "tr1")])
        key = self.w.echo(text, T0 + 38)
        merged = km._merge_live_atoms(self.w.parse(), SID)
        self.assertNotIn(SID, self.w.be._live, "the landed text retires the echo under the shared key")
        self.assertEqual(self._texts(merged).count(km.sb.echo_text_key(text)), 1, "shown once: the record")

    def test_a_recorded_landed_verdict_retires_through_the_merge_without_a_text_match(self):
        # the backend's boot scan found the record and recorded `_landed` on the echo; the parse has no
        # atom with that text (the two texts cannot meet) — the verdict alone is the exit
        self.w.write(running_turn())
        text = "a send the scan found but no key can match"
        key = self.w.echo(text, T0 + 38)
        self.w.be._live[SID][key]["_landed"] = True
        merged = km._merge_live_atoms(self.w.parse(), SID)
        self.assertNotIn(SID, self.w.be._live, "prune_live honours the recorded verdict")
        self.assertNotIn(text, self._texts(merged), "…and nothing paints it as fresh")

    def test_an_unlanded_echo_still_survives_the_merge(self):
        # the control: no record, no verdict → the echo stays (guarantee 2: no floor retires it)
        self.w.write(running_turn())
        key = self.w.echo(FED + "\n", T0 + 38)
        merged = km._merge_live_atoms(self.w.parse(), SID)
        self.assertIn(key, self.w.be._live.get(SID, {}))
        self.assertEqual(self._texts(merged).count(FED), 1, "the echo itself is shown, once")


class TmuxEchoPrunesUnderTheOneKey(unittest.TestCase):
    """The tmux route's by-text prune (_tmux_echo_prune) is another reader of the key, and until 2026-09-06
    it compared the echo's RAW text against the kernel's stripped keys: a tmux send with a trailing newline
    (`romp send` passes its argument verbatim; the CLI records it verbatim) was never pruned by text once
    it landed. The display dedup hid the echo behind the record, so nothing showed — until a later human
    turn landed and the settle marked the still-resident echo `dropped`: a "never delivered" bubble, with
    restore and dismiss, for a message the transcript holds."""

    def setUp(self):
        self._saved = km._sdk
        km._sdk = lambda: None                 # the tmux route: no SDK backend owns the sid
        km._tmux_echo.clear()

    def tearDown(self):
        km._sdk = self._saved
        km._tmux_echo.clear()

    @staticmethod
    def _session(atoms):
        return {"turns": [{"id": "t", "trigger": None, "t": T0, "end": T0, "ended": True, "atoms": atoms}]}

    @staticmethod
    def _human(text, uid, t):
        return {"type": "user", "uuid": uid, "author": "human", "t": t,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}}

    def _echo(self, text, sent_at):
        km._tmux_echo_add(SID, text)
        atom = next(a for a in km._tmux_echo[SID].values() if a.get("_echo_text") == text)
        atom["t"] = sent_at
        return atom

    def test_a_trailing_newline_tmux_echo_is_pruned_when_its_text_lands(self):
        text = "rename this to fetch_notes\n"
        echo = self._echo(text, T0 + 10)
        # the record holds the text verbatim; a later human turn has landed since
        merged = km._merge_live_atoms(self._session([self._human(text, "u1", T0 + 12),
                                                     self._human("and the tests", "u2", T0 + 500)]), SID)
        self.assertNotIn(SID, km._tmux_echo, "the echo's key and the record's key agree: pruned by text")
        self.assertFalse(echo.get("dropped"), "a delivered send is never marked never-delivered")
        texts = [t for turn in merged["turns"] for a in turn["atoms"] for t in km._atom_user_texts(a)]
        self.assertEqual(texts.count(km.sb.echo_text_key(text)), 1, "shown once: the record")

    def test_an_unlanded_tmux_echo_still_survives(self):
        # the control: the record carries a different text, so the echo stays (and is overtaken → dropped)
        echo = self._echo("this Enter dropped at the prompt\n", T0 + 10)
        km._merge_live_atoms(self._session([self._human("and the tests", "u2", T0 + 500)]), SID)
        self.assertIn(SID, km._tmux_echo)
        self.assertTrue(echo.get("dropped"))


class ImagePathPredicateTwins(unittest.TestCase):
    """The extension set is the CLI's, not romp's. SOURCE OF TRUTH: the installed Claude Code bundle
    (2.1.261) carries exactly one image-path test, `/\\.(png|jpe?g|gif|webp)$/i`; its callers are the
    terminal composer's bracketed-paste handler — which reads a pasted path with one of those extensions
    and rewrites the token to "[Image #N]" (the "Failed to read pasted image file" path) — and two
    attachment uploaders' isImage. Nothing on the stream-json input path an SDK session uses reaches it,
    which is why sdk_backend.prune_live floors no echo; the kernel's tmux settle borrows the predicate
    for the route where the hook does run. When the CLI's set changes, change BOTH twins here: the kernel
    waits on the rewrite (_injected_img_paths), the backend exports the predicate — a drift between them
    or from the CLI makes an echo the CLI did rewrite persist forever, or one it did not rewrite get
    retired as an extraction. The kernel's bare-path PREVIEW (_user_images) is deliberately NOT a
    reader: it is romp's own feature on its own set (PreviewSetIsTheHydrationRoutes below)."""

    CLI_SET = "png|jpe?g|gif|webp"

    def test_the_backend_regex_is_the_kernels(self):
        self.assertEqual(sb._IMG_PATH_RE.pattern, km._IMG_PATH_RE.pattern)
        self.assertEqual(sb._IMG_PATH_RE.flags, km._IMG_PATH_RE.flags)

    def test_the_set_is_the_cli_paste_hooks(self):
        for rx in (sb._IMG_PATH_RE, km._IMG_PATH_RE):
            m = re.search(r"\\\.\(\?:([a-z|?]+)\)", rx.pattern)
            self.assertIsNotNone(m, rx.pattern)
            self.assertEqual(m.group(1), self.CLI_SET, "the alternation is the CLI's, verbatim")
            self.assertTrue(rx.flags & re.IGNORECASE, "the CLI's test is case-insensitive (/i)")
        for ext in ("png", "PNG", "jpg", "JPEG", "jpeg", "gif", "webp", "WebP"):
            self.assertTrue(sb._path_bearing("see /tmp/notes-api/docs/shot.%s now" % ext), ext)
            self.assertEqual(km._injected_img_paths("see /tmp/notes-api/docs/shot.%s now" % ext),
                             ["/tmp/notes-api/docs/shot.%s" % ext])
        for ext in ("svg", "bmp", "ico", "avif", "heic", "tiff", "txt", "md"):
            self.assertFalse(sb._path_bearing("see /tmp/notes-api/docs/shot.%s now" % ext),
                             "%s: the CLI never rewrites it" % ext)
            self.assertEqual(km._injected_img_paths("see /tmp/notes-api/docs/shot.%s now" % ext), [])


class PreviewSetIsTheHydrationRoutes(unittest.TestCase):
    """_user_images scans a human turn's text for a bare image path to preview — on an SDK session every
    image path lands as text, so this scan is the only way a referenced picture gets shown. Its set is
    NOT the CLI twin's: it is exactly what the imgRequest route can serve (_IMG_MIME's keys, bmp and svg
    included), derived from that map so the scan can never propose a path _img_data_url would refuse.
    Pinned because narrowing the shared regex to the CLI's set on 2026-09-06 would otherwise have dropped
    svg/bmp previews without any test noticing."""

    def test_the_preview_set_is_derived_from_the_mime_map(self):
        m = re.search(r"\\\.\(\?:([a-z|]+)\)", km._PREVIEW_IMG_RE.pattern)
        self.assertIsNotNone(m, km._PREVIEW_IMG_RE.pattern)
        self.assertEqual(set(m.group(1).split("|")), {k[1:] for k in km._IMG_MIME})
        self.assertTrue(km._PREVIEW_IMG_RE.flags & re.IGNORECASE, "_img_data_url lowercases the extension")
        self.assertIn("svg", m.group(1)); self.assertIn("bmp", m.group(1))

    def test_a_bare_svg_or_bmp_path_still_previews(self):
        for ext in ("svg", "bmp", "BMP", "png", "jpeg", "JPG", "gif", "webp"):
            p = "/tmp/notes-api/docs/diagram.%s" % ext
            self.assertEqual(km._user_images([], "compare with %s please" % p, True),
                             [{"src": "path:" + p, "path": p}], ext)
        self.assertEqual(km._user_images([], "~/notes/todo.md is stale, see /tmp/x.ico", True), [],
                         "an extension the route cannot serve is not proposed")

    def test_the_preview_is_wider_than_the_cli_twin_and_the_twin_is_unchanged(self):
        # the extraction reader still answers with the CLI's set: an svg path is previewed, yet the tmux
        # pre-Enter wait does not wait for a rewrite the CLI never performs
        text = "compare with /tmp/notes-api/docs/diagram.svg"
        self.assertEqual(len(km._user_images([], text, True)), 1)
        self.assertEqual(km._injected_img_paths(text), [])
        self.assertFalse(sb._path_bearing(text))


if __name__ == "__main__":
    unittest.main()
