#!/usr/bin/env python3
"""The chat-payload FOLD (issue 903): build_session reuses the rendered events of whole ENDED turns and
reshapes only the tail, and the result is byte-identical to a from-scratch build at every step.

Two builds make the comparison honest: the folding build keeps the module's `_chat_fold` cache warm
(the live path), the reference build runs right after with that cache cleared (always the full path;
the parse cache is shared, since the fold sits above it). Every scenario appends a record group at a
time and compares the WHOLE payload as sorted JSON at each step; the clean-append case also pins the
exact fold count, so the fast path can never rot into full builds while the equivalence stays green.
Each demote gate is then tripped on purpose: equivalence must hold BY DEMOTION, pinned via the counter.

Synthetic only: placeholder ids, invented text, the notes-api demo world (CLAUDE.md)."""
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_chatfold", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                      # the kernel's OWN judge module (a second load would rebind nothing)
# a SECOND kernel instance for the deep replay: its fold cache is cleared before every build (always the
# full path) while the first keeps folding fold on fold — the assembly replay's emi/emr arrangement
kmr = load_source("romp_kernel_chatfold_ref", os.path.join(BIN, "romp-kernel"))
MODS = (km, kmr)

SID = "11111111-2222-3333-4444-555555555555"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text},
            "cwd": "/tmp/notes-api", "version": "2.1.0", "gitBranch": "main"}


def aline(t, text, uuid, parent, tools=(), stop="end_turn", model="claude-sonnet-4"):
    content = [{"type": "text", "text": text}] if text else []
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n,
                        "input": {"file_path": "/tmp/notes-api/search.py", "old_string": "a", "new_string": "b"}
                        if n == "Edit" else {"command": "uv run pytest -q"}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": model, "content": content, "stop_reason": stop},
            "cwd": "/tmp/notes-api", "version": "2.1.0", "gitBranch": "main"}


def trline(t, tool_use_id, uuid, parent, content="ok"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                                                     "content": content}]}}


class Sess:
    """A synthetic session discovery can see: names/ + projects/<cdir>/<SID>.jsonl under a hermetic state
    root, with the kernel's judge module rebound to it (the ViewBuilder fixture's shape)."""

    def __init__(self, working=True):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.cdir = td / "launchdir"; self.cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(self.cdir))
        self.saved = [(m.jd.NAMES, m.jd.PROJECTS, m.jd.CAPDIR, m.jd.ARCHDIR, m.jd.GOALDIR, m.jd.STATE,
                       m.NAMES, m._live_map, m._GLOBAL_CLAUDE_MD, m._msg_summaries) for m in MODS]
        self.now = int(__import__("time").time())        # discovery keys on the real clock
        self.t = self.now - 3 * 86400
        state = "working" if working else "idle"
        self.tm = {SID: {"state": state, "since": self.now - 100, "model": "", "effort": "",
                         "context": None, "compactPct": None, "color": None}}
        for m in MODS:                                   # both kernel instances see the same synthetic world
            m.jd.NAMES, m.jd.PROJECTS = names, proj
            m.jd.CAPDIR, m.jd.ARCHDIR, m.jd.GOALDIR = td / "captions", td / "archive", td / "goals"
            m.jd.STATE = td
            m.NAMES = names
            m._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
            m._live_map = lambda: self.tm
            m._chat_fold.clear()
            m._parse_cache.clear()
            m._PATH_LINK_CACHE.clear()                  # keyed (sid, uuid): fixtures reuse both across tests
            m._SPACE_PATH_CACHE.clear()
            m._postal_index_memo[0] = None
            m.jd._discover_cache.clear() if isinstance(m.jd._discover_cache, dict) else None
        self.n = 0
        self.last = None

    def close(self):
        for m, sv in zip(MODS, self.saved):
            (m.jd.NAMES, m.jd.PROJECTS, m.jd.CAPDIR, m.jd.ARCHDIR, m.jd.GOALDIR, m.jd.STATE,
             m.NAMES, m._live_map, m._GLOBAL_CLAUDE_MD, m._msg_summaries) = sv
            m._chat_fold.clear()
        self.td.cleanup()

    def uid(self):
        self.n += 1
        return "aaaaaaaa-0000-0000-0000-%012d" % self.n

    def tick(self, dt=5):
        self.t += dt
        return self.t

    def turn(self, i, tools=("Edit",), close=True, rounds=1):
        """One complete turn: prompt, `rounds` tool rounds (use + result), a closing reply."""
        recs = []
        u = self.uid(); recs.append(uline(self.tick(), "step %d: tighten the notes-api search" % i, u, self.last))
        prev = u
        for k in range(rounds):
            a = self.uid(); recs.append(aline(self.tick(), "Round %d: adjusting `search.py`." % k, a, prev, tools=tools, stop="tool_use"))
            r = self.uid(); recs.append(trline(self.tick(), "tu_%s_0" % a, r, a, content="ok\n"))
            prev = r
        r = prev
        self.last = r
        if close:
            b = self.uid(); recs.append(aline(self.tick(), "Step %d done." % i, b, r))
            self.last = b
        return recs

    def append(self, recs):
        with open(self.tpath, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        os.utime(self.tpath, None)

    def rewrite(self, recs):
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))
        os.utime(self.tpath, None)

    def build(self, mod=None):
        m = mod or km
        m._live_scope.names = m._names_snapshot()   # the pusher's names scope, as _pusher_cycle sets it: the fold's
        try:                                        # sealed postal cards are verified from it (a build without the
            return m.build_session(SID, self.now, self.tm)   # scope seals them unverified, see PostalCards)
        finally:
            m._live_scope.names = None


def _dump(m):
    return json.dumps(m, sort_keys=True, default=str)


class _Fold(unittest.TestCase):
    def setUp(self):
        self.s = Sess()

    def tearDown(self):
        self.s.close()

    def equiv(self, label):
        """Folding build == full build (cache cleared), both at the same clock; re-warm afterwards.
        Returns the folding build."""
        inc = self.s.build()
        km._chat_fold.clear()
        ref = self.s.build()
        self.assertEqual(_dump(ref), _dump(inc), "fold diverged from full at %s" % label)
        self.s.build()                                  # the reference cleared the cache; warm it again
        return inc

    def grow(self, n_turns):
        for i in range(n_turns):
            self.s.append(self.s.turn(i))
            self.equiv("turn %d" % i)


class Replay(_Fold):
    def test_clean_append_stream_matches_full_and_actually_folds(self):
        # the red-first pin for the feature: equivalence alone would pass if every build were full
        f0 = km._CHAT_FOLD_STATS["fold"]
        n = 8
        self.grow(n)
        # per step from the 3rd turn on: the folding build and the re-warm build both fold; at 2 turns the
        # cache is still cold (nothing sealed while there was one turn) so only the re-warm folds
        self.assertEqual(km._CHAT_FOLD_STATS["fold"] - f0, 1 + 2 * (n - 2),
                         "fold count drifted — some record kind silently demotes to full")

    def test_the_last_turn_is_never_sealed(self):
        self.grow(3)
        e = km._chat_fold_get(SID)
        self.assertIsNotNone(e)
        self.assertEqual(e["n"], 2, "three turns → a prefix of the two ENDED ones; the last stays live")

    def test_prefix_dicts_are_handed_back_by_identity_and_never_written(self):
        self.grow(3)
        a = self.s.build()
        e = km._chat_fold_get(SID)
        before = _dump(e["events"])
        self.s.append(self.s.turn(3))
        b = self.s.build()
        self.assertGreater(len(e["events"]), 0)
        # the payload opens with the head system card, so the prefix sits at an offset: find it by identity
        off = next(i for i, ev in enumerate(b["events"]) if ev is e["events"][0])
        for i, ev in enumerate(e["events"]):
            self.assertIs(b["events"][off + i], ev, "prefix event %d must be the SAME dict (the diff's identity path)" % i)
        self.assertEqual(_dump(e["events"]), before, "a fold build wrote into a sealed prefix event")
        off_a = next(i for i, ev in enumerate(a["events"]) if ev is e["events"][0])
        self.assertIs(a["events"][off_a], b["events"][off])

    def test_an_open_turn_folds_too(self):
        self.grow(2)
        self.s.append(self.s.turn(2, close=False))     # the tail turn is OPEN (a tool round with no reply yet)
        self.equiv("open tail")
        self.assertEqual(km._chat_fold_get(SID)["n"], 2)

    def test_episode_render_bypasses_the_cache(self):
        self.grow(3)
        b0 = km._CHAT_FOLD_STATS["bypass"]
        e = km._chat_fold_get(SID)
        self.assertIsNotNone(e, "three turns sealed an entry to protect")
        before = _dump(e["events"])
        m = km.build_session(SID, self.s.now, self.s.tm, path_override=str(self.s.tpath))
        self.assertEqual(m.get("type"), "session")
        self.assertEqual(km._CHAT_FOLD_STATS["bypass"], b0 + 1)
        self.assertIs(km._chat_fold_get(SID), e, "an override render must not touch the live prefix")
        self.assertEqual(_dump(e["events"]), before)


class Gates(_Fold):
    def assert_folding(self):
        """The fast path is live right now: one more build folds. Called BEFORE a gate event, so a build with
        the fold disabled (which still runs the gate code and bumps g:*) cannot pass these tests."""
        f0 = km._CHAT_FOLD_STATS["fold"]
        self.s.build()
        self.assertEqual(km._CHAT_FOLD_STATS["fold"], f0 + 1, "the fast path must be live before the gate event")

    def _demotes(self, reason, label):
        """The gate fires: the build demotes with g:<reason>."""
        g = "g:" + reason
        n0 = km._CHAT_FOLD_STATS.get(g, 0)
        inc = self.equiv(label)
        self.assertGreater(km._CHAT_FOLD_STATS.get(g, 0), n0, "the %s gate must demote here" % reason)
        return inc

    def test_a_tool_result_landing_in_a_later_turn_demotes(self):
        # turn 1 ENDS with a tool call still unanswered (a typed prompt only opens a new turn after an
        # ended one — an unfinished turn absorbs it); the prompt opens turn 2; THEN the result lands, in
        # turn 2 (non-openers join the current turn) — it fills turn 1's card, which the prefix holds
        s = self.s
        u1 = s.uid(); s.append([uline(s.tick(), "start", u1, None)])
        a1 = s.uid(); s.append([aline(s.tick(), "Editing.", a1, u1, tools=("Edit",), stop="end_turn")])
        u2 = s.uid(); s.append([uline(s.tick(), "also check the tests", u2, a1)])
        a2 = s.uid(); s.append([aline(s.tick(), "Will do.", a2, u2)])
        self.equiv("two turns, tool open")
        self.assertIn("tu_%s_0" % a1, km._chat_fold_get(SID)["open_tools"])
        self.assert_folding()
        r = s.uid(); s.append([trline(s.tick(), "tu_%s_0" % a1, r, a2, content="edited late")])
        inc = self._demotes("tool-fill", "late tool result")
        card = next(ev for ev in inc["events"] if ev.get("kind") == "tool")
        self.assertEqual(card.get("output"), "edited late")

    def test_a_side_store_note_inside_the_prefix_demotes(self):
        self.grow(3)
        st = jd.STATE / "states"; st.mkdir(exist_ok=True)
        e = km._chat_fold_get(SID)
        t_in = e["last_t"] - 3                          # a recovery note timed INSIDE the sealed prefix
        self.assert_folding()
        with open(st / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": t_in, "retriesRecovered": 2}) + "\n")
        n0 = km._CHAT_FOLD_STATS.get("g:note", 0) + km._CHAT_FOLD_STATS.get("g:turnfp", 0)
        inc = self.equiv("note in prefix")
        n1 = km._CHAT_FOLD_STATS.get("g:note", 0) + km._CHAT_FOLD_STATS.get("g:turnfp", 0)
        self.assertGreater(n1, n0, "a side-store row inside the prefix must demote")
        self.assertTrue(any(ev.get("kind") == "retried" for ev in inc["events"]))

    def test_a_seam_change_demotes(self):
        # a seam that really splits a sealed segment (its t strictly inside the segment's span, its segs
        # naming the segment): the tail past the seam gets new seg ids, so old events' tlIds move and the
        # payload itself differs without the gate — not only the counter
        s = self.s
        s.append(s.turn(0, rounds=2))                     # two tool rounds: a span with a result strictly inside it
        s.append(s.turn(1))
        s.append(s.turn(2))
        pre = self.equiv("three turns, two rounds in the first")
        before = {ev.get("uuid"): ev.get("tlId") for ev in pre["events"] if ev.get("uuid")}
        e = km._chat_fold_get(SID)
        seg_keys = list(e["seg"][2].keys())               # _seg_key(seg id) of every sealed segment
        self.assertTrue(seg_keys)
        parsed = km._parse(str(s.tpath), SID, s.now)
        t0 = parsed["turns"][0]
        first_result_t = next(a["t"] for a in t0["atoms"] if a.get("type") == "user" and any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in ((a.get("message") or {}).get("content") or []) if isinstance((a.get("message") or {}).get("content"), list)))
        self.assert_folding()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "nodes": {}, "placements": {}, "status": {},
            "seams": [{"segs": seg_keys, "t": first_result_t, "top": SID + ":g1", "text": "Ship it"}]}))
        inc = self._demotes("seam", "seam written")
        after = {ev.get("uuid"): ev.get("tlId") for ev in inc["events"] if ev.get("uuid")}
        self.assertNotEqual(before, after, "the seam moved at least one sealed event's timeline anchor")

    def test_a_path_token_that_resolves_later_demotes(self):
        s = self.s
        u1 = s.uid(); s.append([uline(s.tick(), "please write the summary to `docs/notes.md` when done", u1, None)])
        a1 = s.uid(); s.append([aline(s.tick(), "Noted.", a1, u1)])
        s.last = a1                                     # the next turn chains from this reply (one graph, one leaf)
        s.append(s.turn(1))
        inc = self.equiv("mention before the file exists")
        ev = next(ev for ev in inc["events"] if ev.get("kind") == "user" and "notes.md" in (ev.get("md") or ""))
        self.assertEqual(ev.get("pathLinks"), {}, "tokens exist but none resolved yet")
        self.assertTrue(km._chat_fold_get(SID)["pl_pending"], "the sealed prefix remembers the pending token")
        self.assert_folding()
        (s.cdir / "docs").mkdir()
        (s.cdir / "docs" / "notes.md").write_text("# summary\n")
        inc2 = self._demotes("path-link", "file appeared")
        ev2 = next(ev for ev in inc2["events"] if ev.get("kind") == "user" and "notes.md" in (ev.get("md") or ""))
        self.assertEqual(ev2.get("pathLinks"), {"docs/notes.md": "docs/notes.md"})

    def test_a_rewrite_that_shortens_the_transcript_demotes(self):
        self.grow(4)
        recs = [json.loads(l) for l in self.s.tpath.read_text().splitlines() if l.strip()]
        self.assert_folding()
        self.s.rewrite(recs[:-4])                       # the last turn is gone: a rewind, not an append
        n0 = km._CHAT_FOLD_STATS.get("g:parse", 0) + km._CHAT_FOLD_STATS.get("g:shrink", 0)
        self.equiv("shortened")
        self.assertGreater(km._CHAT_FOLD_STATS.get("g:parse", 0) + km._CHAT_FOLD_STATS.get("g:shrink", 0), n0)

    def test_an_orphan_that_already_landed_does_not_demote_forever(self):
        # a states orphanReply row whose uuid is a reply the disk KEPT: the flush skips it (landed), so it
        # renders nothing — and a sealed prefix must not remember it as rendered, or the landed gate fires
        # on every later build for good (landedTextUuids only grows): a silent return to full rebuilds
        self.grow(3)
        parsed = km._parse(str(self.s.tpath), SID, self.s.now)
        landed = parsed["landedTextUuids"]
        self.assertTrue(landed)
        st = jd.STATE / "states"; st.mkdir(exist_ok=True)
        with open(st / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": self.s.t - 50, "orphanReply": {"uuid": landed[0], "text": "a reply the disk kept"}}) + "\n")
        self.equiv("landed orphan row")                   # this build may demote once (the note set moved)
        self.s.append(self.s.turn(3))
        self.equiv("next turn")
        f0, l0 = km._CHAT_FOLD_STATS["fold"], km._CHAT_FOLD_STATS.get("g:landed", 0)
        for i in range(4, 7):
            self.s.append(self.s.turn(i))
            self.equiv("turn %d" % i)
        self.assertGreaterEqual(km._CHAT_FOLD_STATS["fold"] - f0, 3, "the fast path must resume after a landed orphan")
        self.assertEqual(km._CHAT_FOLD_STATS.get("g:landed", 0), l0, "a never-rendered orphan must not trip the landed gate")

    def test_a_new_complete_turn_extends_the_prefix(self):
        self.grow(2)
        self.assertEqual(km._chat_fold_get(SID)["n"], 1)
        self.s.append(self.s.turn(2) + self.s.turn(3))  # two turns land between builds
        self.equiv("two turns at once")
        self.assertEqual(km._chat_fold_get(SID)["n"], 3, "every ended turn before the last is sealed")


ROMP_AUTO = "<!-- romp-injected --><!-- romp-auto -->Where does this stand? If nothing is blocking you, keep going."
ROMP_RESTART = ("<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted and cut this "
                "session's in-flight turn; the session has been resumed with its history intact.")


class Seams(_Fold):
    def _cut_turn(self, i):
        """A turn cut by a kernel restart: prompt, a tool call in flight, the CLI's stop record, its
        synthetic settle reply — the shape every restart mints."""
        s = self.s
        u = s.uid(); recs = [uline(s.tick(), "step %d: reindex the notes" % i, u, s.last)]
        a = s.uid(); recs.append(aline(s.tick(), "Reindexing.", a, u, tools=("Bash",), stop="tool_use"))
        m = s.uid(); recs.append(uline(s.tick(), "[Request interrupted by user]", m, a))
        z = s.uid(); recs.append(aline(s.tick(), "No response requested.", z, m, model="<synthetic>"))
        s.last = z
        return recs

    def test_an_undecided_seam_is_never_sealed_and_its_cause_lands_later(self):
        # T0-T1 normal; T2 is cut; T3 opens with a romp auto-nudge (not a decider); the fold must hold the
        # boundary BEFORE T2 through every build — including the one where T2 stops being the first tail
        # turn — so that when the restart notice lands in T4 the marker is stamped and the settle dropped
        s = self.s
        self.grow(2)
        s.append(self._cut_turn(2))
        self.equiv("cut turn is last")
        def romp_turn(text, reply):                       # a romp-injected prompt and the agent's reply: one ended turn
            u_ = s.uid(); s.append([uline(s.tick(), text, u_, s.last)])
            a_ = s.uid(); s.append([aline(s.tick(), reply, a_, u_)]); s.last = a_
        romp_turn(ROMP_AUTO, "Still on the reindex.")
        self.equiv("nudge turn after the cut")
        self.assertLessEqual(km._chat_fold_get(SID)["n"], 2, "the undecided seam holds the boundary before its turn")
        romp_turn(ROMP_AUTO, "Nothing new yet.")          # a second non-deciding turn: the seam stays undecided
        self.equiv("a second nudge turn")
        self.assertLessEqual(km._chat_fold_get(SID)["n"], 2, "…and keeps holding it while more non-deciding turns land")
        romp_turn(ROMP_RESTART, "Resuming where I left off.")
        inc = self.equiv("restart notice landed")
        marker = next(ev for ev in inc["events"] if ev.get("interruptMarker"))
        self.assertEqual(marker.get("interruptCause"), "restart")
        self.assertTrue(marker.get("settleUuids"))
        self.assertFalse(any(ev.get("interruptSettle") for ev in inc["events"]), "the machine cut's settle line is dropped")
        s.append(s.turn(6))
        self.equiv("decided seam seals with the next turn")
        self.assertGreaterEqual(km._chat_fold_get(SID)["n"], 4, "once decided, the seam's turn seals like any other")


class PostalCards(_Fold):
    MID = "1788400000.100_1.TESTHOST"
    PEER = "22222222-3333-4444-5555-666666666666"

    def _log(self):
        d = jd.STATE / "timeline"; d.mkdir(exist_ok=True)
        (d / "messages.jsonl").write_text(json.dumps({"ev": "sent", "id": self.MID, "from": "api", "from_id": self.PEER,
                                                      "to_id": SID, "body": "the api tests are green now",
                                                      "kind": "coordinate", "t": self.s.t}) + "\n")

    def test_a_caption_rewritten_after_the_seal_refreshes_the_card(self):
        # the judge writes a LIVE caption under an id it later overwrites with the final one: a card
        # sealed complete between the two must not keep the live gloss forever. The caption is one of the
        # values the sealed cards are keyed on (2026-09-09), so the card refreshes from the caption's change
        # in _msg_summaries() alone: no judge generation is bumped here, and no transcript, log or states
        # file changes
        s = self.s
        self._log()
        caps = {self.MID: "peer: tests green (live)"}
        for m in MODS:
            m._msg_summaries = lambda caps=caps: dict(caps)
        self.grow(1)
        u = s.uid(); s.append([uline(s.tick(), "<!-- romp-msg-id: %s -->\nthe api tests are green now" % self.MID, u, s.last)])
        a = s.uid(); s.append([aline(s.tick(), "Noted, thanks.", a, u)]); s.last = a
        s.append(s.turn(2))
        inc = self.equiv("card sealed with the live caption")
        card = next(ev for ev in inc["events"] if ev.get("kind") == "postal-service")
        self.assertEqual(card.get("summary"), "peer: tests green (live)")
        self.assertEqual(km._chat_fold_get(SID)["postal_deps"], ((self.MID, "peer: tests green (live)", None, None),),
                         "the entry records the values its card embeds: mid, caption, the sender's name and colour")
        caps[self.MID] = "peer: api tests green"          # the FINAL caption lands
        n0, g0 = km._CHAT_FOLD_STATS.get("g:postal", 0), km._chat_postal_stats["gate"]
        inc2 = self.equiv("caption rewritten")
        self.assertGreater(km._CHAT_FOLD_STATS.get("g:postal", 0), n0)
        self.assertGreater(km._chat_postal_stats["gate"], g0, "the gate re-hydrated the sealed card")
        card2 = next(ev for ev in inc2["events"] if ev.get("kind") == "postal-service")
        self.assertEqual(card2.get("summary"), "peer: api tests green")
        self.assertEqual(km._chat_fold_get(SID)["postal_deps"], ((self.MID, "peer: api tests green", None, None),))

    # ── the seal keeps only REAL postal Bash events raw (2026-09-06) ──────────────────────────────────
    def _bash_turn(self, i, command, result="ok\n"):
        """One complete turn whose single tool round is a Bash call running `command`, its result `result`.
        build_session stores the input as JSON ({"command", "description"}), truncated at 4000 chars."""
        recs = self.s.turn(i, tools=("Bash",))
        recs[1]["message"]["content"][1]["input"] = {"command": command, "description": "step %d" % i}
        recs[2]["message"]["content"][0]["content"] = result
        return recs

    def _count_hydrate(self):
        """Every _hydrate_postal call build_session makes from here on, as the event lists it was handed."""
        calls = []
        orig = km._hydrate_postal
        def counting(events, index, sid=None, captions=None):
            calls.append(list(events))
            return orig(events, index, sid, captions=captions)
        km._hydrate_postal = counting
        self.addCleanup(setattr, km, "_hydrate_postal", orig)
        return calls

    def test_sealing_plain_bash_turns_leaves_no_raw_postal_event(self):
        # Every plain Bash event used to ride the sealed entry's postal_raw and be re-hydrated on every
        # judge pass, to the same raw row each time. Only a send or a mail read belongs there.
        s = self.s
        self._log()
        for m in MODS:
            m._msg_summaries = lambda: {}
        s.append(self._bash_turn(0, "uv run pytest -q"))
        self.equiv("plain bash")
        s.append(self._bash_turn(1, "echo " + "x" * 5000))        # its stored input is cut at 4000 chars: not JSON
        self.equiv("truncated bash")
        # a marker in a plain Bash OUTPUT is text the agent read (a grep hit), not mail: the row stays raw
        s.append(self._bash_turn(2, "grep -rn romp-msg-id notes.md",
                                 result="notes.md:3:<!-- romp-msg-id: %s -->\n" % self.MID))
        self.equiv("marker in a bash output")
        s.append(self._bash_turn(3, "uv run pytest -q"))
        inc = self.equiv("sealed")
        entry = km._chat_fold_get(SID)
        self.assertEqual(entry["n"], 3, "three ended turns sealed; the last is never")
        self.assertEqual(entry["postal_raw"], [], "no plain Bash event rides the entry raw")
        self.assertEqual(entry["postal_cards"], [])
        bash_rows = [e for e in entry["events"] if e.get("kind") == "tool" and e.get("name") == "Bash"]
        self.assertEqual(len(bash_rows), 3, "the three Bash events are sealed as rendered rows")
        self.assertTrue(any(self.MID in (e.get("output") or "") for e in bash_rows), "the grep hit is one of them")
        self.assertEqual([e for e in inc["events"] if e.get("kind") == "postal-service"], [])

    def test_a_warm_build_hydrates_once_with_and_without_a_judge_pass(self):
        # With nothing postal sealed, the tail pass is the ONE hydration a warm build makes: the gate has
        # nothing to re-hydrate on a judge pass and the re-commit has nothing to hydrate either.
        s = self.s
        for i in range(4):
            s.append(self._bash_turn(i, "uv run pytest -q"))
        self.equiv("sealed")
        self.assertEqual(km._chat_fold_get(SID)["postal_raw"], [])
        calls = self._count_hydrate()
        n_fold, n_postal = km._CHAT_FOLD_STATS["fold"], km._CHAT_FOLD_STATS.get("g:postal", 0)
        s.build()
        self.assertEqual(km._CHAT_FOLD_STATS["fold"], n_fold + 1, "the warm build folded")
        self.assertEqual(len(calls), 1, "one hydration: the tail pass")
        for m in MODS:
            m._judge_gen[0] += 1
        s.build()
        self.assertEqual(km._CHAT_FOLD_STATS["fold"], n_fold + 2, "the judge pass did not demote")
        self.assertEqual(len(calls), 2, "a judge pass with nothing postal sealed adds no hydration")
        self.assertEqual(km._CHAT_FOLD_STATS.get("g:postal", 0), n_postal, "and filed no postal demotion")

    def test_a_bash_send_stays_raw_and_is_rehydrated_when_the_log_moves(self):
        # The `romp mail send` twin of the caption-rewrite test above: the send is the one Bash event the
        # seal keeps raw. Its rendering depends on the message log alone (no card renders here: build_session
        # stores a Bash input as JSON, which _cli_send_card does not unwrap, a limit that predates this
        # change), so a judge pass re-hydrates nothing and a log append re-hydrates exactly it. The commit
        # reuses the sealed cards and hydrates only raw events new since the seal, so a warm build makes one
        # hydration, the tail pass (it made two before, the second over the whole sealed raw list).
        s = self.s
        self._log()
        for m in MODS:
            m._msg_summaries = lambda: {}
        s.append(self._bash_turn(0, "uv run pytest -q"))
        s.append(self._bash_turn(1, 'romp mail send api "the api tests are green now"',
                                 result="[romp mail] delivered to api\n"))
        s.append(self._bash_turn(2, "uv run pytest -q"))
        s.append(self._bash_turn(3, "uv run pytest -q"))
        self.equiv("send sealed")
        raw = km._chat_fold_get(SID)["postal_raw"]
        self.assertEqual(len(raw), 1, "the send, and only the send, rides the entry raw")
        self.assertEqual((raw[0]["kind"], raw[0]["name"]), ("tool", "Bash"))
        self.assertIsNotNone(km._cli_send_match(raw[0]), "kept by the matcher the card renders from")
        self.assertEqual(km._chat_fold_get(SID)["postal_deps"], (None,), "a raw event that did not hydrate: the log alone")
        calls = self._count_hydrate()
        h0, g0, c0 = km._chat_postal_stats["hit"], km._chat_postal_stats["gate"], km._chat_postal_stats["commit_new"]
        s.build()
        self.assertEqual(len(calls), 1, "the tail pass only: the commit reuses the sealed cards")
        self.assertEqual((km._chat_postal_stats["hit"], km._chat_postal_stats["gate"], km._chat_postal_stats["commit_new"]),
                         (h0 + 1, g0, c0))
        for m in MODS:
            m._judge_gen[0] += 1
        s.build()
        self.assertEqual(len(calls), 2, "a judge pass re-hydrates nothing: the send embeds no judge-written value")
        self.assertEqual((km._chat_postal_stats["hit"], km._chat_postal_stats["gate"]), (h0 + 2, g0))
        # the log gains a row: the index the send would join moved, so the sealed send is re-hydrated
        with open(jd.STATE / "timeline" / "messages.jsonl", "a") as f:
            f.write(json.dumps({"ev": "sent", "id": self.MID + "b", "from": "web", "from_id": SID, "to_id": self.PEER,
                                "body": "the api tests are green now", "kind": "coordinate", "t": s.t}) + "\n")
        s.build()
        self.assertEqual([len(c) == 1 and c[0] is raw[0] for c in calls[2:]], [True, False], "the gate, then the tail pass")
        self.assertEqual((km._chat_postal_stats["hit"], km._chat_postal_stats["gate"]), (h0 + 2, g0 + 1))
        self.assertEqual(km._chat_postal_stats["commit_new"], c0, "nothing new was sealed, so nothing new was hydrated")

class PostalRelevance(_Fold):
    """_chat_postal_relevant decides what the fold keeps raw; _hydrate_postal decides what renders. The
    invariant tying them: an event the predicate rejects comes back from hydration as the same object."""
    MID = PostalCards.MID
    PEER = PostalCards.PEER
    BODY = "the api tests are green now"

    def _index(self, **rows):
        idx = {self.MID: {"id": self.MID, "from": "api", "fromId": self.PEER, "fromHost": "", "toId": SID,
                          "body": self.BODY, "kind": "coordinate", "t": 1788400000, "park": False}}
        idx.update(rows)
        return idx

    def _cases(self):
        """(label, event, relevant, renders a card) — one row per shape the plan names, plus the neighbours
        that must NOT count: an echo of a marker in an assistant reply, a Read whose output carries one."""
        marker = "<!-- romp-msg-id: %s -->" % self.MID
        ts = iso(1788400010)
        def bash(command, output="ok\n", input=None):
            inp = input if input is not None else json.dumps({"command": command, "description": "d"})
            return {"kind": "tool", "name": "Bash", "input": inp, "output": output, "isError": False,
                    "uuid": "t", "ts": ts}
        def tool(name, output, **inp):
            return {"kind": "tool", "name": name, "input": json.dumps(inp), "output": output,
                    "isError": False, "uuid": "t", "ts": ts}
        return [
            ("plain bash", bash("uv run pytest -q"), False, False),
            ("truncated bash", bash(None, input=json.dumps({"command": "echo " + "x" * 5000})[:4000]), False, False),
            ("bash output carrying a marker", bash("grep -rn romp-msg-id notes.md", output="notes.md:3:" + marker), False, False),
            ("bash send, raw input", bash(None, input='romp mail send api "%s"' % self.BODY,
                                          output="[romp mail] delivered to api"), True, True),
            ("bash send, json input", bash('romp mail send api "%s"' % self.BODY,
                                           output="[romp mail] delivered to api"), True, False),
            ("bash inbox read", bash("romp mail inbox", output="from api:\n" + marker), True, True),
            ("check_inbox", tool("mcp__romp-postal-service__check_inbox", marker), True, True),
            ("send_message", tool("mcp__romp-postal-service__send_message", "Delivered to 'api'.",
                                  to="api", body=self.BODY), True, True),
            ("read whose output carries a marker", tool("Read", marker, file_path="/tmp/notes-api/notes.md"), False, False),
            ("user text with a marker", {"kind": "user", "md": "####\nfrom api\n####\n%s\n%s" % (self.BODY, marker),
                                         "uuid": "u", "ts": ts, "human": False}, True, True),
            ("plain user text", {"kind": "user", "md": "please run the tests", "uuid": "u", "ts": ts, "human": True}, False, False),
            ("assistant echo of a marker", {"kind": "assistant", "md": "noted " + marker, "uuid": "a", "ts": ts}, False, False),
        ]

    def test_only_postal_events_are_relevant_and_the_rest_pass_through_by_identity(self):
        for m in MODS:
            m._msg_summaries = lambda: {}
        index = self._index()
        for label, ev, relevant, renders in self._cases():
            with self.subTest(label):
                self.assertEqual(km._chat_postal_relevant(ev), relevant, label)
                out = km._hydrate_postal([ev], index, SID)
                self.assertEqual(len(out), 1)
                if not relevant:
                    self.assertIs(out[0], ev, "a rejected event comes back as the same object")
                self.assertEqual(out[0].get("kind") == "postal-service", renders, label)
                if ev.get("name") == "Bash":
                    # the two Bash predicates agree with the renderers they stand for
                    self.assertEqual(km._cli_send_match(ev) is not None or km._reads_mail(ev), relevant)
                    if km._cli_send_card(ev) is not None:
                        self.assertTrue(relevant, "a Bash event that renders a send card is never sealed away")

    def test_a_bash_send_card_follows_the_recipients_caption(self):
        # the hydrator-level half of PostalCards' Bash-send test: the card joins the log row wearing its
        # body and wears whatever caption the recipient's judge has filed by now
        ev = {"kind": "tool", "name": "Bash", "input": 'romp mail send api "%s"' % self.BODY,
              "output": "[romp mail] delivered to api", "isError": False, "uuid": "t", "ts": iso(1788400010)}
        caps = {self.MID: "peer: tests green (live)"}
        for m in MODS:
            m._msg_summaries = lambda caps=caps: dict(caps)
        card = km._hydrate_postal([ev], self._index(), SID)[0]
        self.assertEqual((card["kind"], card["direction"], card["mid"], card["summary"]),
                         ("postal-service", "out", self.MID, "peer: tests green (live)"))
        caps[self.MID] = "peer: api tests green"
        card2 = km._hydrate_postal([ev], self._index(), SID)[0]
        self.assertEqual(card2["summary"], "peer: api tests green")

    def test_the_body_map_joins_the_row_the_linear_scan_found(self):
        # enrich_out used to scan every index row per outgoing card; the body-keyed map must pick the
        # same row: closest in time to the send, the FIRST such row on a tie, the last row when the send
        # carries no time
        body = "please pick up the notes-api deploy"
        def row(mid, t, b=body):
            return {"id": mid, "from": "web", "fromId": "", "fromHost": "", "toId": "", "body": b,
                    "kind": "", "t": t, "park": False}
        index = {"m1": row("m1", 100), "m2": row("m2", 5000), "m9": row("m9", 5000, "a different message"),
                 "m3": row("m3", 9000), "m4": row("m4", 5000)}
        def linear(et):
            recs = [r for r in index.values() if r["body"] == body]
            return (min(recs, key=lambda r: abs((r["t"] or 0) - et)) if et else recs[-1])["id"]
        def send(ts):
            return {"kind": "tool", "name": "mcp__romp-postal-service__send_message",
                    "input": json.dumps({"to": "api", "body": body}), "output": "Delivered to 'api'.",
                    "isError": False, "uuid": "t", "ts": ts}
        for m in MODS:
            m._msg_summaries = lambda: {}
        for et, want in ((4980, "m2"), (120, "m1"), (8000, "m3"), (2550, "m1"), (0, "m4")):
            with self.subTest(et=et):
                card = km._hydrate_postal([send(iso(et) if et else None)], index)[0]
                self.assertEqual(card["mid"], want)
                self.assertEqual(card["mid"], linear(et), "the same row the linear scan picked")
        plain = km._hydrate_postal([send(iso(4980))], {"m9": index["m9"]})[0]
        self.assertNotIn("mid", plain, "no row wearing the body: the card stays unjoined")

    def test_the_memoized_index_carries_one_body_map_per_version(self):
        self.addCleanup(km._postal_index_memo.__setitem__, 0, None)
        d = jd.STATE / "timeline"; d.mkdir(exist_ok=True)
        log = d / "messages.jsonl"
        def line(mid, t):
            return json.dumps({"ev": "sent", "id": mid, "from": "web", "from_id": self.PEER, "to_id": SID,
                               "body": "deploy please", "kind": "coordinate", "t": t}) + "\n"
        log.write_text(line("m1", 100) + line("m2", 5000))
        idx = km._postal_index()
        bm = km._postal_body_rows(idx)
        self.assertIs(km._postal_body_rows(km._postal_index()), bm, "one map per index version, not per call")
        self.assertEqual([r["id"] for r in bm["deploy please"]], ["m1", "m2"])
        with open(log, "a") as f:
            f.write(line("m3", 9000))
        idx2 = km._postal_index()
        self.assertIsNot(idx2, idx, "the log grew: a new index version")
        bm2 = km._postal_body_rows(idx2)
        self.assertIsNot(bm2, bm)
        self.assertEqual([r["id"] for r in bm2["deploy please"]], ["m1", "m2", "m3"])
        own = dict(idx2)                                  # a caller's own dict never borrows the memo's map
        self.assertIsNot(km._postal_body_rows(own), bm2)
        self.assertEqual(km._postal_body_rows(own), bm2)

    def test_a_sent_row_whose_body_is_not_a_string_neither_breaks_the_index_nor_the_join(self):
        # the far-host relay logs a /send request's body with no type check, so a sent row can carry a
        # JSON list or object; the body map must skip it (unhashable) rather than raise inside every
        # session's chat build, and an outgoing card still joins its own string-bodied row
        self.addCleanup(km._postal_index_memo.__setitem__, 0, None)
        d = jd.STATE / "timeline"; d.mkdir(exist_ok=True)
        def line(mid, body):
            return json.dumps({"ev": "sent", "id": mid, "from": "web", "from_id": self.PEER, "to_id": SID,
                               "body": body, "kind": "coordinate", "t": 5000}) + "\n"
        (d / "messages.jsonl").write_text(line("m1", "deploy please") + line("m2", ["not", "a", "string"])
                                          + line("m3", {"text": "an object"}))
        idx = km._postal_index()
        self.assertEqual(sorted(idx), ["m1", "m2", "m3"], "every row is indexed, as before")
        bm = km._postal_body_rows(idx)
        self.assertEqual({k: [r["id"] for r in v] for k, v in bm.items()}, {"deploy please": ["m1"]})
        for m in MODS:
            m._msg_summaries = lambda: {}
        def send(body):
            return {"kind": "tool", "name": "mcp__romp-postal-service__send_message",
                    "input": json.dumps({"to": "api", "body": body}), "output": "Delivered to 'api'.",
                    "isError": False, "uuid": "t", "ts": iso(5010)}
        card = km._hydrate_postal([send("deploy please")], idx, SID)[0]
        self.assertEqual((card["kind"], card["direction"], card["mid"]), ("postal-service", "out", "m1"))
        other = km._hydrate_postal([send("a body no row wears")], idx, SID)[0]
        self.assertEqual(other["kind"], "postal-service")
        self.assertNotIn("mid", other, "no row wearing the body: the card passes through unjoined")


    def test_outgoing_cards_join_through_one_body_map_not_a_scan_per_card(self):
        # the saving the body map buys, pinned: a hydration walks a caller's own index ONCE to build the
        # map, and the memoized index not at all (its map rides the memo entry) — never a walk of every
        # row per outgoing card, which the linear scan it replaces did
        body = "please pick up the notes-api deploy"
        def row(mid, t, b=body):
            return {"id": mid, "from": "web", "fromId": "", "fromHost": "", "toId": "", "body": b,
                    "kind": "", "t": t, "park": False}
        def send(t):
            return {"kind": "tool", "name": "mcp__romp-postal-service__send_message",
                    "input": json.dumps({"to": "api", "body": body}), "output": "Delivered to 'api'.",
                    "isError": False, "uuid": "t", "ts": iso(t)}
        sends = [send(t) for t in (1010, 2010, 3010, 4010, 5010)]
        for m in MODS:
            m._msg_summaries = lambda: {}
        walks = [0]
        class Counting(dict):
            def values(self):
                walks[0] += 1
                return super().values()
        own = Counting({"m1": row("m1", 1000), "x1": row("x1", 1500, "another message"), "m2": row("m2", 3000),
                        "x2": row("x2", 3500, "a third message"), "m3": row("m3", 5000), "x3": row("x3", 5500, "a fourth")})
        cards = km._hydrate_postal(sends, own, SID)
        self.assertEqual([c.get("kind") for c in cards], ["postal-service"] * 5)
        self.assertEqual([c["mid"] for c in cards], ["m1", "m2", "m2", "m3", "m3"])
        self.assertEqual(walks[0], 1, "one walk of the index builds the map; no card walks it again")
        # the memoized index: its map was built beside it, so a hydration builds none at all
        self.addCleanup(km._postal_index_memo.__setitem__, 0, None)
        d = jd.STATE / "timeline"; d.mkdir(exist_ok=True)
        (d / "messages.jsonl").write_text("".join(
            json.dumps({"ev": "sent", "id": mid, "from": "web", "from_id": self.PEER, "to_id": SID,
                        "body": body, "kind": "coordinate", "t": t}) + "\n"
            for mid, t in (("m1", 1000), ("m2", 2000), ("m3", 3000))))
        idx = km._postal_index()
        builds = []
        orig = km._postal_body_map
        km._postal_body_map = lambda index: (builds.append(len(index)), orig(index))[1]
        self.addCleanup(setattr, km, "_postal_body_map", orig)
        cards = km._hydrate_postal(sends, idx, SID)
        self.assertEqual([c["mid"] for c in cards], ["m1", "m2", "m3", "m3", "m3"])
        self.assertEqual(builds, [], "the memo's map is reused: no build per hydration, none per card")

    def test_a_send_awaiting_its_result_is_relevant_but_renders_no_card_yet(self):
        # relevance reads only the input: a `romp mail send` whose tool_result has not landed is kept raw
        # (relevant) and renders no card until the CLI confirms delivery; the same event with its result
        # hydrates to the card. Had relevance read the output, the fold would have sealed the pending
        # send as a plain Bash row and its card could never appear.
        ev = {"kind": "tool", "name": "Bash", "input": 'romp mail send api "%s"' % self.BODY,
              "output": "", "isError": False, "uuid": "t", "ts": iso(1788400010)}
        for m in MODS:
            m._msg_summaries = lambda: {}
        self.assertTrue(km._chat_postal_relevant(ev), "a send is postal before its result lands")
        self.assertIsNone(km._cli_send_card(ev), "no delivery confirmed: no card yet")
        self.assertIs(km._hydrate_postal([ev], {}, SID)[0], ev, "and the raw event passes through as itself")
        landed = dict(ev, output="[romp mail] delivered to api")
        self.assertTrue(km._chat_postal_relevant(landed), "the verdict did not move with the result")
        card = km._hydrate_postal([landed], self._index(), SID)[0]
        self.assertEqual((card["kind"], card["direction"], card["peer"], card["mid"]),
                         ("postal-service", "out", "api", self.MID))


class TaskOutputs(_Fold):
    def test_a_notification_whose_output_file_grows_after_the_seal_demotes(self):
        s = self.s
        out = s.cdir / "nightly.out"
        out.write_text("line 1\n")
        note = ("<task-notification>\n<status>completed</status>\n<summary>nightly check</summary>\n"
                "<output-file>%s</output-file>\n<tool-use-id>toolu_bg_1</tool-use-id>\n</task-notification>" % out)
        self.grow(1)
        u = s.uid(); s.append([uline(s.tick(), note, u, s.last)])
        a = s.uid(); s.append([aline(s.tick(), "The nightly check passed.", a, u)]); s.last = a
        s.append(s.turn(2))
        inc = self.equiv("notification sealed")
        card = next(ev for ev in inc["events"] if ev.get("taskOutputs"))
        self.assertIn("line 1", card["taskOutputs"]["toolu_bg_1"]["output"])
        self.assertTrue(km._chat_fold_get(SID)["task_outs"], "the sealed card's output file is remembered")
        with open(out, "a") as f:
            f.write("line 2\n")
        os.utime(out, None)
        n0 = km._CHAT_FOLD_STATS.get("g:task-output", 0)
        inc2 = self.equiv("output grew")
        self.assertGreater(km._CHAT_FOLD_STATS.get("g:task-output", 0), n0)
        card2 = next(ev for ev in inc2["events"] if ev.get("taskOutputs"))
        self.assertIn("line 2", card2["taskOutputs"]["toolu_bg_1"]["output"])
        out.unlink()                                     # …and a vanished file renders differently too
        self.equiv("output vanished")


class DeepReplay(_Fold):
    def test_record_by_record_fold_on_fold_equals_a_full_build_at_every_step(self):
        # the folding kernel is never reset (fold on fold, deep chains); the reference kernel clears its
        # fold cache before every build — two module instances, one synthetic world
        s = self.s
        recs = []
        for i in range(6):
            recs += s.turn(i)
        s.last = None
        recs += self.__class__._interleave(self, recs)
        f0 = km._CHAT_FOLD_STATS["fold"]
        for i, r in enumerate(recs):
            s.append([r])
            inc = s.build(km)
            kmr._chat_fold.clear()
            ref = s.build(kmr)
            self.assertEqual(_dump(ref), _dump(inc), "fold-on-fold diverged from full at record %d" % (i + 1))
        self.assertGreater(km._CHAT_FOLD_STATS["fold"] - f0, len(recs) // 2, "the chain must actually fold")

    @staticmethod
    def _interleave(self, recs):
        # a nudge, a cut turn and its late restart notice, in the same stream
        s = self.s
        s.last = recs[-1]["uuid"]
        extra = []
        n_ = s.uid(); extra.append(uline(s.tick(), ROMP_AUTO, n_, s.last)); s.last = n_
        u = s.uid(); extra.append(uline(s.tick(), "step 6: reindex", u, s.last))
        a = s.uid(); extra.append(aline(s.tick(), "Reindexing.", a, u, tools=("Bash",), stop="tool_use"))
        m = s.uid(); extra.append(uline(s.tick(), "[Request interrupted by user]", m, a))
        z = s.uid(); extra.append(aline(s.tick(), "No response requested.", z, m, model="<synthetic>"))
        n2 = s.uid(); extra.append(uline(s.tick(), ROMP_AUTO, n2, z)); s.last = n2
        r_ = s.uid(); extra.append(uline(s.tick(), ROMP_RESTART, r_, s.last)); s.last = r_   # decides the seam
        extra += s.turn(7)
        extra += s.turn(8)
        return extra


class Fallback(_Fold):
    def test_a_gate_check_failure_counts_once_drops_the_entry_and_builds_in_full(self):
        self.grow(3)
        class Boom(dict):                             # read only by the gate check (the parse writes it)
            def get(self, *a, **k):
                raise RuntimeError("boom")
        saved = km._parse_mode
        km._parse_mode = Boom(saved)
        try:
            fb0, g0 = km._CHAT_FOLD_STATS["fallback"], km._CHAT_FOLD_STATS.get("g:fallback", 0)
            self.s.append(self.s.turn(3))
            inc = self.s.build()
        finally:
            km._parse_mode = saved
        self.assertEqual(km._CHAT_FOLD_STATS["fallback"], fb0 + 1)
        self.assertEqual(km._CHAT_FOLD_STATS.get("g:fallback", 0), g0, "a fallback is never also a demote")
        km._chat_fold.clear()
        self.assertEqual(_dump(self.s.build()), _dump(inc), "the fallback build is a correct full build")


class DeltaSend(_Fold):
    def test_a_fold_build_ships_as_a_tail_from_the_first_changed_index(self):
        self.grow(2)
        m1 = self.s.build()
        sent = []
        c = {"send": lambda body: sent.append(json.loads(body)), "sent": {}, "echat": {}}
        km._send_chat(c, m1, None, 0, False)
        self.assertEqual(sent[-1]["type"], "session")
        self.s.append(self.s.turn(2))
        m2 = self.s.build()                              # a fold: the prefix dicts are m1's
        change_from = km._chat_diff(m1["events"], m2["events"])
        self.assertGreater(change_from, 0)
        km._send_chat(c, m2, None, change_from, False)
        tail = sent[-1]
        self.assertEqual(tail["type"], "chatTail")
        self.assertEqual(tail["from"], change_from)
        self.assertEqual(len(tail["events"]), len(m2["events"]) - change_from)
        self.assertTrue(any(ev.get("md", "").startswith("step 2") for ev in tail["events"]))


class Diff(unittest.TestCase):
    def test_chat_diff_takes_the_identity_path_before_comparing(self):
        class Bomb(dict):
            def __eq__(self, other):
                raise AssertionError("value compare reached on an identical object")
            __hash__ = dict.__hash__
        b = Bomb(uuid="1")
        self.assertEqual(km._chat_diff([b], [b]), 1, "same object → equal without a value compare")
        a = {"uuid": "1", "x": 1}
        self.assertEqual(km._chat_diff([a, {"uuid": "2"}], [a, {"uuid": "2"}, {"uuid": "3"}]), 2)
        self.assertEqual(km._chat_diff([a, {"uuid": "2"}], [a, {"uuid": "2", "output": "x"}]), 1)


class SessionMetaFold(unittest.TestCase):
    def test_meta_folds_appends_and_refolds_on_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.jsonl")
            recs = [uline(1000, "hi", "u1"), aline(1005, "yo", "a1", "u1", tools=("Edit",), stop="tool_use")]
            Path(p).write_text("".join(json.dumps(r) + "\n" for r in recs))
            km._session_meta_cache.pop(p, None)
            m1 = km._session_meta(p)
            self.assertEqual((m1["cwd"], m1["gitBranch"], m1["lastEditPath"]),
                             ("/tmp/notes-api", "main", "/tmp/notes-api/search.py"))
            r3 = uline(1010, "next", "u2", "a1"); r3["gitBranch"] = "feature"
            with open(p, "a") as f:
                f.write(json.dumps(r3) + "\n")
            m2 = km._session_meta(p)
            km._session_meta_cache.pop(p, None)
            fresh = km._session_meta(p)
            self.assertEqual(m2, fresh, "the folded meta must equal a from-scratch read")
            self.assertEqual(m2["gitBranch"], "feature")
            Path(p).write_text(json.dumps(recs[0]) + "\n")   # a rewrite: prefix identity is gone
            m3 = km._session_meta(p)
            self.assertEqual(m3["gitBranch"], "main")
            self.assertEqual(m3["lastEditPath"], "")


class Wiring(unittest.TestCase):
    def test_version_reports_the_fold_counters_beside_parse(self):
        src = inspect.getsource(km)
        self.assertIn('"chatfold": dict(_CHAT_FOLD_STATS)', src)
        self.assertIn('"parse": dict(em._ASM_STATS)', src)

    def test_a_tab_switch_wakes_the_pusher(self):
        src = inspect.getsource(km)
        i = src.index('msg.get("type") == "activeTab"')
        self.assertIn("_pusher_wake.set()", src[i:i + 400], "the tab switch IS the event; no 0.5 s backstop wait")

    def test_perf_line_carries_the_fold_fields(self):
        src = inspect.getsource(km)
        self.assertIn('fold=_chat_fold_last_info().get("fold", 0)', src)


class PlacedEchoes(Gates):
    """A STALE live echo placed into a sealed turn (T344: an echo stamped before the last turn's start sits
    where its send time belongs) changes state without a parse change: dismissed (dismissEcho pops it from
    the live tail), flagged dropped, or landed. The fold keys its sealed prefix on the merge's `_placed`
    report and refolds ("echo") on a change, so the sealed events never hold a ghost of a dismissed echo
    or a stale copy of its flag; the equivalence with a full build holds through every step."""

    ECHO = "echo:" + "d" * 32
    NOTICE = "[romp] The condition you asked romp to watch now HOLDS: the notes-api search suite has its verdict."

    class _Backend(km._UnownedBackend):
        """The owning backend as the chat build reads it: a live tail this test edits, a prune that retires nothing."""

        def __init__(self):
            self.live = []

        def owns(self, sid):
            return True

        def live_atoms(self, sid):
            return sorted(self.live, key=lambda a: a.get("t", 0))

        def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
            return None

    def setUp(self):
        super().setUp()
        self.be = self._Backend()
        self._saved_bf = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)

    def tearDown(self):
        km.Sessions.backend_for = self._saved_bf
        super().tearDown()

    def _echo(self, t, **extra):
        a = {"type": "user", "uuid": self.ECHO, "session_id": SID, "t": t, "parentUuid": None, "author": "romp",
             "_echo_text": self.NOTICE, "message": {"role": "user", "content": [{"type": "text", "text": self.NOTICE}]}}
        a.update(extra)
        return a

    def _sealed_uuids(self):
        return [ev.get("uuid") for ev in km._chat_fold_get(SID)["events"]]

    def test_a_placed_echo_is_sealed_and_its_dismissal_refolds(self):
        self.grow(3)
        t_first = self.s.now - 3 * 86400 + 6           # inside the FIRST turn's window: placed there, sealed with it
        self.be.live.append(self._echo(t_first))
        self.equiv("stale echo placed into a sealed turn")
        e = km._chat_fold_get(SID)
        self.assertIn(self.ECHO, self._sealed_uuids(), "the placed echo's event is part of the sealed prefix")
        self.assertEqual(e["placed"], ((0, self.ECHO, False),), "…and the entry records its placement and state")
        self.assert_folding()
        self.be.live.clear()                            # dismissEcho: the live tail no longer holds it
        inc = self._demotes("echo", "the placed echo dismissed")
        self.assertNotIn(self.ECHO, self._sealed_uuids(), "the refold dropped the ghost from the sealed prefix")
        self.assertNotIn(self.ECHO, [ev.get("uuid") for ev in inc["events"]], "…and from the payload")
        self.assertEqual(km._chat_fold_get(SID)["placed"], ())

    def test_a_placed_echo_flagged_dropped_refolds_and_the_sealed_copy_reads_dropped(self):
        self.grow(3)
        t_first = self.s.now - 3 * 86400 + 6
        self.be.live.append(self._echo(t_first))
        self.equiv("stale echo placed")
        self.assert_folding()
        self.be.live[0]["dropped"] = True                # _mark_dropped_echoes / settle_echoes flagged it
        inc = self._demotes("echo", "the placed echo flagged dropped")
        ev = next(x for x in inc["events"] if x.get("uuid") == self.ECHO)
        self.assertTrue(ev.get("undelivered"), "the payload reads the flag (`undelivered`): %r" % {k: ev[k] for k in ev if k != "md"})
        self.assertEqual(km._chat_fold_get(SID)["placed"], ((0, self.ECHO, True),))
        self.assert_folding()                           # and folds again once the state is sealed anew

    def test_a_placed_echo_that_lands_refolds_and_leaves_the_prefix(self):
        # the third exit the fold's docstring names: the echo's TEXT lands in the transcript (a record carrying
        # it), the display dedup drops the echo from the merge, `placed` empties, the sealed copy must go
        self.grow(3)
        t_first = self.s.now - 3 * 86400 + 6
        self.be.live.append(self._echo(t_first))
        self.equiv("stale echo placed")
        self.assert_folding()
        s = self.s
        u = s.uid(); s.append([uline(s.tick(), self.NOTICE, u, s.last)])   # the notice lands as a user record
        # The landing is a parse change too, and the sealed turn's fingerprint moves with the echo's departure
        # (one atom fewer), so the gate chain demotes on "turnfp" before it reaches "echo"; either way the
        # build refolds, and `placed` records the emptiness. What matters is that no ghost survives.
        n_fp, n_echo = km._CHAT_FOLD_STATS.get("g:turnfp", 0), km._CHAT_FOLD_STATS.get("g:echo", 0)
        inc = self.equiv("the placed echo landed")
        self.assertTrue(km._CHAT_FOLD_STATS.get("g:turnfp", 0) > n_fp or km._CHAT_FOLD_STATS.get("g:echo", 0) > n_echo,
                        "the landing refolds: the turn fingerprint moved with the echo's departure, or the echo gate fired")
        self.assertNotIn(self.ECHO, self._sealed_uuids(), "the landed echo's sealed copy is gone")
        self.assertNotIn(self.ECHO, [ev.get("uuid") for ev in inc["events"]], "…and the payload shows the record, not the echo")
        self.assertEqual(km._chat_fold_get(SID)["placed"], ())

    def test_a_fresh_echo_in_the_last_turn_never_touches_the_fold(self):
        self.grow(3)
        self.assert_folding()
        self.be.live.append(self._echo(self.s.t + 1))    # after the last turn's start: the tail, as before
        f0 = km._CHAT_FOLD_STATS["fold"]
        inc = self.equiv("fresh echo in the tail")
        self.assertIn(self.ECHO, [ev.get("uuid") for ev in inc["events"]])
        self.assertGreater(km._CHAT_FOLD_STATS["fold"], f0, "a tail echo folds like any live tail")
        self.assertEqual(km._chat_fold_get(SID)["placed"], ())

if __name__ == "__main__":
    unittest.main()
