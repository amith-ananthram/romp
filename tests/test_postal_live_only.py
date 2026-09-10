#!/usr/bin/env python3
"""Postal addressing is LIVE-ONLY (the user 2026-06-29): the Romp Postal Service no longer
reaches outside the live fleet. find_sessions and revive_session are GONE, and a recipient
name resolves only to a currently-live session — there is no dead-session resurrection and
no parking mail for a session that isn't running. This pins that simplification so the
removed surfaces can't quietly creep back. No real session data here (synthetic UUIDs).
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
SKILL = os.path.join(ROOT, "claude", "skills", "romp-postal", "SKILL.md")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))

ALPHA = "11111111-2222-3333-4444-555555555555"
GHOST = "99999999-8888-7777-6666-555555555555"
THREAD = "11111111-2222-3333-4444-777777777777"
PEER = "99999999-8888-7777-6666-555555555557"    # a federated peer: known here only by its heartbeat


def _tool_names():
    return {t["name"] for t in pm.MCP_TOOLS}


def _set_live(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(rows, f)
    f.close()
    os.environ["ROMP_SESSIONS_FILE"] = f.name
    return f.name


class LiveOnlyAddressing(unittest.TestCase):
    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()

    def test_live_name_resolves(self):
        _set_live([{"id": ALPHA, "name": "alpha"}])
        self.assertEqual(pm._recip_id_for("alpha"), ALPHA)

    def test_unknown_name_is_unresolvable(self):
        _set_live([{"id": ALPHA, "name": "alpha"}])
        # a name no LIVE session has resolves to nothing — no dead-history fallback
        self.assertIsNone(pm._recip_id_for("ghost"))

    def test_uuid_without_mailbox_is_unresolvable(self):
        # live-only: a bare UUID that isn't live and has no in-flight mailbox does not resolve
        _set_live([{"id": ALPHA, "name": "alpha"}])
        self.assertIsNone(pm._recip_id_for(GHOST))

    def test_a_live_thread_row_resolves_by_its_own_name(self):
        # a comment thread is hidden from the default listing; recipient resolution asks for thread
        # rows (the 2026-08-22 rule), so recall by the thread's name still finds it now that its
        # heartbeat no longer leaves a phantom remote row (2026-09-06)
        _set_live([{"id": ALPHA, "name": "alpha"},
                   {"id": THREAD, "name": "alpha-t1", "thread": True, "parent": ALPHA}])
        self.assertEqual(pm._recip_id_for("alpha-t1"), THREAD)
        self.assertEqual(pm._name_for_id(THREAD), "alpha-t1", "the thread's name lives only on its row")

    def test_heartbeat_remote_resolves(self):
        # a heartbeating remote peer is LIVE for addressing purposes
        _set_live([])
        pm.HEARTBEATS[GHOST] = ("beta", pm.time.time())
        self.assertEqual(pm._recip_id_for("beta"), GHOST)


class RecallReachesParkedMailForTheDead(unittest.TestCase):
    """The ONE deliberate carve-out from live-only addressing (2026-08-29): RECALL is not
    addressing. The sender is unsending their OWN bytes, which sit locally in the recipient's
    new/ — no delivery, no resurrection. A handoff parked for a session that then died could not
    be unsent by NAME (only the raw box id worked, which nobody has at hand); _recall now falls
    back to the durable name map when the name no longer resolves live. Ambiguity still refuses:
    two dead boxes wearing one name is not a guess the sender authorized."""

    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        _set_live([{"id": ALPHA, "name": "alpha"}])

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        for rid in (GHOST, "99999999-8888-7777-6666-555555555556", PEER):
            box = pm.MAILROOT / rid / "new"
            if box.is_dir():
                for f in box.iterdir():
                    f.unlink()

    def _park(self, rid, name, mid="m1"):
        box = pm.MAILROOT / rid / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / mid).write_text("From: alpha\nFrom-Id: %s\nX-Park: 1\n\nthe handoff body" % ALPHA)
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)
        (pm.NAMES_DIR / rid).write_text("%s\thost" % name)

    def test_recall_by_dead_name_finds_the_parked_box(self):
        self._park(GHOST, "ghost")
        removed = pm._recall(ALPHA, "ghost", None)
        self.assertEqual([r["id"] for r in removed], ["m1"])
        self.assertEqual(list((pm.MAILROOT / GHOST / "new").iterdir()), [])

    def test_two_dead_boxes_one_name_refuses(self):
        twin = "99999999-8888-7777-6666-555555555556"
        self._park(GHOST, "ghost", mid="m1")
        self._park(twin, "ghost", mid="m2")
        self.assertEqual(pm._recall(ALPHA, "ghost", None), [])

    def test_only_the_senders_own_mail_comes_back(self):
        self._park(GHOST, "ghost")
        self.assertEqual(pm._recall("00000000-0000-0000-0000-000000000001", "ghost", None), [])

    def test_recall_by_a_live_thread_name(self):
        # a comment thread withholds its names entry, so only its live row can carry the name
        _set_live([{"id": ALPHA, "name": "alpha"},
                   {"id": THREAD, "name": "alpha-t1", "thread": True, "parent": ALPHA}])
        box = pm.MAILROOT / THREAD / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "m7").write_text("From: alpha\nFrom-Id: %s\n\nthe reply body" % ALPHA)
        try:
            removed = pm._recall(ALPHA, "alpha-t1", None)
            self.assertEqual([(r["id"], r["to"]) for r in removed], [("m7", "alpha-t1")])
            self.assertEqual(list(box.iterdir()), [])
        finally:
            for f in box.iterdir():
                f.unlink()


    def test_recall_by_a_remote_peers_name(self):
        # a peer known here only through its heartbeat (a federated session: no local row, no names
        # entry): recall by its name resolves through the remote presence _recip_id_for folds into the
        # listing it is handed, as it did when it fetched the listing itself (2026-09-06)
        pm.HEARTBEATS[PEER] = ("ghost", pm.time.time())
        self.addCleanup(pm.HEARTBEATS.clear)
        (pm.NAMES_DIR / PEER).unlink(missing_ok=True)       # only the heartbeat may name it
        box = pm.MAILROOT / PEER / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "m8").write_text("From: alpha\nFrom-Id: %s\n\nthe body" % ALPHA)
        removed = pm._recall(ALPHA, "ghost", None)
        self.assertEqual([r["id"] for r in removed], ["m8"])
        self.assertEqual(list(box.iterdir()), [])


class KernelSilenceIsNotDeadness(unittest.TestCase):
    """The liveness source not ANSWERING is different information from "nobody by that name is
    live" (the authoritative-sources rule: fail loudly, never degrade silently). _kernel_sessions
    collapsed both to [], so a send during a kernel restart was refused with a false deadness
    claim about a demonstrably live peer (sighting 2026-08-29; the retry 101s later delivered).
    resolve_recipient now probes the source once on the refusal path and answers 503-honestly."""

    def setUp(self):
        self._prior_seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # no seam: the source is the kernel
        self._base = pm.KERNEL_BASE
        pm.KERNEL_BASE = "http://127.0.0.1:9"      # nothing listens: every fetch fails fast

    def tearDown(self):
        pm.KERNEL_BASE = self._base
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()

    def test_unanswered_source_refuses_without_claiming_death(self):
        res = pm.resolve_recipient("ghost")
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("didn't answer", res["error"])
        self.assertNotIn("no live romp session", res["error"], "the false deadness claim is the bug")

    def test_an_answered_empty_listing_still_refuses_as_not_live(self):
        _set_live([])
        res = pm.resolve_recipient("ghost")
        self.assertEqual((res["kind"], res["status"]), ("error", 404))
        self.assertIn("no live romp session named 'ghost'", res["error"])


class AnsweredButAbsentIsNotDeadness(unittest.TestCase):
    """The OTHER false-refusal arm (2026-08-31): the kernel ANSWERED but its listing transiently
    omitted a live session (a restart-settle blink), and the bus converted that into a hard "not
    live" — two verified specimens, one by id and one by name, one of which mis-routed a warning
    mail. A pool miss now corroborates against the DURABLE per-session registry (and the probe
    fetch's own rows) before ruling death: reg alive=true → soft retry-shortly, never a death
    claim. A name with no reg anywhere keeps the hard 404 — a typo stays a typo."""

    GHOST_SID = "99999999-8888-7777-6666-000000000001"

    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        _set_live([{"id": ALPHA, "name": "alpha"}])     # answered listing, target absent
        self._sdk = pm.STATE.parent / "sdk"
        self._sdk.mkdir(parents=True, exist_ok=True)
        pm.NAMES_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)
        pm.HEARTBEATS.clear()
        for f in list(self._sdk.iterdir()) + list(pm.NAMES_DIR.iterdir()):
            f.unlink()

    def _reg(self, sid, name, alive=True):
        (self._sdk / (sid + ".json")).write_text(json.dumps({"sid": sid, "name": name, "alive": alive}))
        (pm.NAMES_DIR / sid).write_text("%s\t/tmp" % name)

    def test_id_addressed_with_a_live_reg_refuses_soft(self):
        self._reg(self.GHOST_SID, "blinky")
        res = pm.resolve_recipient(self.GHOST_SID)
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])
        self.assertIn("NOT a claim", res["error"])

    def test_name_addressed_with_a_live_reg_refuses_soft(self):
        self._reg(self.GHOST_SID, "blinky")
        res = pm.resolve_recipient("blinky")
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])

    def test_a_dead_reg_keeps_the_hard_404_both_forms(self):
        self._reg(self.GHOST_SID, "gone4good", alive=False)
        for who in (self.GHOST_SID, "gone4good"):
            res = pm.resolve_recipient(who)
            self.assertEqual((res["kind"], res["status"]), ("error", 404), who)
            self.assertIn("no live romp session", res["error"])

    def test_no_reg_anywhere_keeps_the_hard_404(self):
        res = pm.resolve_recipient("typo-name")
        self.assertEqual((res["kind"], res["status"]), ("error", 404))

    def test_the_probe_fetchs_own_rows_count_as_presence(self):
        # the pool (all_agents) missed the target but the probe's fresh listing has it — the
        # kernel came back between the two reads; that is a blink, never a death ruling
        saved = pm.all_agents
        pm.all_agents = lambda threads=False: [{"id": ALPHA, "name": "alpha", "remote": False}]
        try:
            _set_live([{"id": ALPHA, "name": "alpha"}, {"id": self.GHOST_SID, "name": "blinky"}])
            res = pm.resolve_recipient("blinky")
        finally:
            pm.all_agents = saved
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])

    def test_a_host_qualified_miss_keeps_the_hard_404(self):
        # a host qualifier naming somebody ELSE takes every local out of the running by design —
        # the local listing/registry can say nothing about that host, so the blink arms must not
        # fire on a same-named LOCAL row (review find: a typo'd host became an endless retry-shortly)
        self._reg(self.GHOST_SID, "blinky")
        _set_live([{"id": self.GHOST_SID, "name": "blinky"}])
        res = pm.resolve_recipient("otherhost:blinky")
        self.assertEqual((res["kind"], res["status"]), ("error", 404))

    def test_short_id_blink_refuses_soft(self):
        # the third address form (the 8-char id prefix every list_agents row shows) is
        # blink-protected like the other two
        self._reg(self.GHOST_SID, "blinky")
        res = pm.resolve_recipient(self.GHOST_SID[:8])
        self.assertEqual((res["kind"], res["status"]), ("error", 503))
        self.assertIn("restart-settle blink", res["error"])

    def test_short_id_with_two_matching_regs_keeps_the_404(self):
        twin = self.GHOST_SID[:-1] + "f"
        self._reg(self.GHOST_SID, "blinky")
        self._reg(twin, "blinky2")
        res = pm.resolve_recipient(self.GHOST_SID[:8])
        self.assertEqual((res["kind"], res["status"]), ("error", 404),
                         "two registry hits on one prefix is the ambiguity family: refuse, never guess")

    def test_durable_session_reads(self):
        self._reg(self.GHOST_SID, "blinky")
        self.assertTrue(pm._durable_session(self.GHOST_SID, by_id=True))
        self.assertTrue(pm._durable_session("blinky", by_id=False))
        self.assertFalse(pm._durable_session("nobody", by_id=False))
        self.assertFalse(pm._durable_session("99999999-8888-7777-6666-000000000002", by_id=True))


class RemovedSurfacesAreGone(unittest.TestCase):
    def test_removed_mcp_tools_absent(self):
        names = _tool_names()
        self.assertNotIn("find_sessions", names)
        self.assertNotIn("revive_session", names)

    def test_live_tools_still_present(self):
        names = _tool_names()
        for n in ("send_message", "check_inbox", "list_agents",
                  "set_working", "check_sent", "recall_message"):
            self.assertIn(n, names)

    def test_removed_functions_absent(self):
        for fn in ("_dead_id_for_name", "_revive", "_resolve_session",
                   "_find_sessions", "_session_records", "format_find"):
            self.assertFalse(hasattr(pm, fn), f"{fn} should be removed from the postal service")

    def test_instructions_are_live_only(self):
        ins = pm.MCP_INSTRUCTIONS.lower()
        self.assertNotIn("find_sessions", ins)
        self.assertNotIn("revive_session", ins)
        self.assertIn("live-only", ins)

    def test_skill_prose_is_live_only(self):
        with open(SKILL, encoding="utf-8") as f:
            text = f.read().lower()
        self.assertNotIn("find_sessions", text)
        self.assertNotIn("revive_session", text)
        self.assertIn("live-only", text)


if __name__ == "__main__":
    unittest.main()
