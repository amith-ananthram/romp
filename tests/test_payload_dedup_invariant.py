#!/usr/bin/env python3
"""No pane payload may defeat _send_client's dedup by ticking with the clock.

test_chat_payload_clock_invariant.py asserts this for the CHAT payload, where the bug was first
found (a `firstSeen` that fell back to `now`). It came straight back on the FEED, which carries the
host clock as a deliberate top-level field: `now` (the client's hostNow, for age math) plus
`buildId` (sequences optimistic move acks). Both tick on every build, and _send_client compared the
whole serialization, so no feed payload ever matched the previous one and the kernel re-sent the
entire view to every client forever.

Measured on the wire before the fix (the user 2026-07-28, whose dashboard kept dropping the live
connection): app=feed shipped ~46 KB every ~2s on a fleet that wasn't changing — 12 of 12
consecutive frames byte-distinct, differing ONLY in `now` and `buildId`.

So the invariant here is one level up from the chat file's: a payload MAY carry the clock, but
everything the dedup actually compares must be clock-invariant. Any future field that ticks — an age
folded into a card, a "seconds since" counter — fails here instead of becoming another silent
firehose.

Synthetic only: placeholder uuid, invented prompt text, temp dirs.
"""
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_dedupinv", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_dedupinv", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


class DedupSuppressesAClockOnlyDifference(unittest.TestCase):
    """The wire behaviour, asserted directly on _send_client — no fixture needed."""

    def _client(self):
        sent = []
        return {"send": sent.append, "alive": True}, sent

    def test_a_feed_payload_differing_only_in_the_clock_is_sent_once(self):
        """The exact measured regression: same cards, new `now` + `buildId` on every build."""
        client, sent = self._client()
        for i in range(12):                      # 12 pushes ≈ the 12 frames captured on the wire
            km._send_client(client, ("feed",),
                            {"type": "feed", "asks": [{"itemId": "g1", "col": "working"}],
                             "now": NOW + i, "buildId": 100 + i})
        self.assertEqual(len(sent), 1,
                         "an unchanged feed must go out ONCE; a fresh `now`/`buildId` per build is not "
                         "a change and must not re-send the whole view")

    def test_the_payload_that_IS_sent_still_carries_the_current_clock(self):
        """Dedup compares a stripped signature; it must not strip the bytes actually sent, or the
        client's hostNow/buildId would go stale."""
        client, sent = self._client()
        km._send_client(client, ("feed",), {"type": "feed", "asks": [], "now": NOW, "buildId": 1})
        km._send_client(client, ("feed",), {"type": "feed", "asks": [{"itemId": "g1"}],
                                            "now": NOW + 5, "buildId": 2})
        self.assertEqual(len(sent), 2)
        self.assertEqual(json.loads(sent[1])["now"], NOW + 5, "the sent bytes keep the fresh clock")
        self.assertEqual(json.loads(sent[1])["buildId"], 2, "…and the fresh buildId")

    def test_a_real_change_still_sends_even_when_the_clock_is_frozen(self):
        """The other half: dedup must key on CONTENT, so a change lands even on an identical clock."""
        client, sent = self._client()
        km._send_client(client, ("feed",), {"type": "feed", "asks": [], "now": NOW, "buildId": 1})
        km._send_client(client, ("feed",), {"type": "feed", "asks": [{"itemId": "g1"}],
                                            "now": NOW, "buildId": 1})
        self.assertEqual(len(sent), 2, "a changed card list must be sent")

    def test_an_unchanged_view_is_reposted_about_once_a_minute_for_colour_fade(self):
        """The feed fades card colour against the payload's `now`, so a view that never re-sends would
        hold its colours frozen. feed.ts documents the contract: 'host reposts ~1x/min for color fade'."""
        client, sent = self._client()
        payload = {"type": "feed", "asks": [], "now": NOW, "buildId": 1}
        km._send_client(client, ("feed",), payload)
        self.assertEqual(len(sent), 1)
        # Age the client's record past the repost window rather than sleeping.
        sig, when = client["sent"][("feed",)]
        client["sent"][("feed",)] = (sig, when - km._DEDUP_REPOST_S - 1)
        km._send_client(client, ("feed",), dict(payload, now=NOW + 61, buildId=2))
        self.assertEqual(len(sent), 2, "an unchanged view must still repost so the colour fade advances")

    def test_a_payload_with_no_clock_field_dedups_on_its_full_serialization(self):
        """Clock-invariant payloads (chat) must keep comparing their cached serialization untouched."""
        client, sent = self._client()
        km._send_client(client, ("chat", SID), {"type": "session", "id": SID, "events": []})
        km._send_client(client, ("chat", SID), {"type": "session", "id": SID, "events": []})
        self.assertEqual(len(sent), 1)


class BuiltFeedIsClockInvariantApartFromTheClockItself(unittest.TestCase):
    """Guards the BUILDER, not just the sender: build_feed at two clocks may differ in `now`/`buildId`
    and nothing else. A future age-derived field folded into a card fails here."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        (pdir / (SID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in [
            uline(T0, "start the notes-api spike", "u1"),
            aline(T0 + 40, "Spike is up.", "a1", "u1"),
        ]) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))

        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._GLOBAL_CLAUDE_MD, km.jd.NAMES, km.jd.PROJECTS)
        self.saved_kernel_state = km.jd.STATE
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        # The KERNEL's judge module is a separate object from this module's `jd` (each is its own
        # SourceFileLoader load), so discover() — which the kernel runs through km.jd — never saw this
        # fixture's names/ or transcript: the session was invisible and the invariant held hollowly. Reach
        # the kernel's copy too, so the fixture's transcript is the one the feed builds from (T258).
        # And the kernel's judge is ONE module object for every test module in the process (each kernel
        # load re-executes bin/romp-judge into the same sys.modules entry), so its STATE — goals, the
        # override journals, cleared.jsonl, session-order.json — is a directory the whole run shares.
        # Built over it, this feed carried whatever store an earlier module left for the placeholder sid,
        # and a writer of that world between the two builds re-sent the feed (T281, CI 2026-09-09). The
        # feed builds over THIS fixture's state root and nothing else: _rebind_state moves GOALDIR and
        # every derived dir with it (assigning STATE alone would not).
        km.jd._rebind_state(td)
        km.jd.NAMES, km.jd.PROJECTS = names, proj
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        # Fixed so the stub itself contributes nothing clock-derived (mirrors the chat invariant test).
        self.tmux = {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None}}
        # The invariant is over an UNCHANGING fleet. A session's FIRST build adopts it into the persisted
        # session order (feed `order` reads that file before _ordered appends the newcomer), so the first
        # sight is a genuine change; warm once so the two compared builds are both post-adoption (T258).
        km.jd._discover_cache.clear()
        km.build_feed(NOW - 1, self.tmux)

    def tearDown(self):
        km.jd._rebind_state(self.saved_kernel_state)
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._GLOBAL_CLAUDE_MD, km.jd.NAMES, km.jd.PROJECTS) = self.saved
        self.td.cleanup()

    def test_only_the_declared_volatile_fields_move_with_the_clock(self):
        a = km.build_feed(NOW, self.tmux)
        b = km.build_feed(NOW + 600, self.tmux)
        varying = sorted(k for k in set(list(a) + list(b))
                         if json.dumps(a.get(k), sort_keys=True, default=str)
                         != json.dumps(b.get(k), sort_keys=True, default=str))
        self.assertTrue(set(varying) <= set(km._DEDUP_VOLATILE),
                        "these feed fields move with the clock but are not declared volatile, so the "
                        "dedup can never hit and the whole feed re-sends on every push: %s"
                        % [k for k in varying if k not in km._DEDUP_VOLATILE])

    def test_two_builds_ten_minutes_apart_dedup_to_a_single_send(self):
        """End to end: the builder's output, run through the real sender, must send once."""
        sent = []
        client = {"send": sent.append, "alive": True}
        km._send_client(client, ("feed",), km.build_feed(NOW, self.tmux))
        km._send_client(client, ("feed",), km.build_feed(NOW + 600, self.tmux))
        self.assertEqual(len(sent), 1,
                         "an unchanging fleet must not re-send the feed just because time passed")

    def test_the_feed_is_built_over_this_fixtures_world_not_the_runs_shared_one(self):
        """The kernel's judge module is shared by every test module in the process, so a goal store another
        module left for the placeholder sid at the run-wide GOALDIR used to be part of THIS feed (T281). A
        store planted there now changes nothing here: the feed reads this fixture's goals directory."""
        foreign = Path(self.saved_kernel_state) / "goals"
        planted = foreign / (SID + ".json")
        original = planted.read_bytes() if planted.exists() else None     # another module's store, if one is there
        made_dir = not foreign.exists()
        foreign.mkdir(parents=True, exist_ok=True)
        store = json.loads(original) if original else {"nodes": {}, "status": {}}
        store.setdefault("nodes", {})["t281-foreign"] = {"id": "t281-foreign", "title": "a card another module left",
                                                          "parentId": None, "status": "open"}
        planted.write_text(json.dumps(store))                              # always planted, so the assertion has teeth

        def restore():
            if original is not None:
                planted.write_bytes(original)
            else:
                planted.unlink(missing_ok=True)
                if made_dir:
                    try:
                        os.rmdir(foreign)
                    except OSError:
                        pass
        self.addCleanup(restore)
        self.assertNotIn("t281-foreign", json.dumps(km.build_feed(NOW, self.tmux), default=str),
                         "a store at the run-wide goals directory is not this feed's input")
        self.assertEqual(km.jd.GOALDIR, Path(self.td.name) / "goals", "the kernel's judge reads this fixture's goals")


if __name__ == "__main__":
    unittest.main()
