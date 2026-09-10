"""What romp's automatic fleet sync DID reaches the Log (the user 2026-07-30).

romp moves commits between machines on its own — a push to a drifted remote, a pull from one that is
ahead, an ask that a checked-in peer fast-forward itself. The only trace was the network panel's live
phase line, which vanishes the instant the sync finishes. So a push that landed and a push that failed
while the user was looking elsewhere were, after the fact, equally invisible.

Every outcome now writes to a bounded ring, the feed payload carries it, and badge-mirror.ts turns it
into one 'sync' entry in the Log. SUCCESSES log too, deliberately: what was asked for is a record of
what romp did to your machines, not another alarm.

Also covered here: the version tag beside the commit, and the "Share my sessions there" memory — both
landed with this, and both are about a row saying something the user can actually read.

Synthetic hosts only; nothing dials anything.
"""
import inspect
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_syncnote", os.path.join(BIN, "romp-kernel"))


class Ring(unittest.TestCase):
    def setUp(self):
        km._SYNC_NOTICES.clear()
        km._SYNC_SEQ = 0

    def test_an_outcome_becomes_one_signed_row(self):
        km._sync_notice("pushed this machine's build to web; it is restarting into it")
        rows = km._sync_notice_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("web", rows[0]["text"])
        self.assertTrue(rows[0]["ok"])
        self.assertTrue(rows[0]["sig"].startswith("sync|"))
        self.assertGreater(rows[0]["t"], 0)

    def test_a_signature_is_unique_per_occurrence_so_a_repeat_logs_again(self):
        km._sync_notice("could not push to web: refused", ok=False)
        km._sync_notice("could not push to web: refused", ok=False)
        sigs = [r["sig"] for r in km._sync_notice_rows()]
        self.assertEqual(len(set(sigs)), 2, "the same failure twice is two events, not one re-render")

    def test_a_failure_is_marked_as_one(self):
        km._sync_notice("could not pull web's commits: dirty tree", ok=False)
        self.assertFalse(km._sync_notice_rows()[0]["ok"])

    def test_the_ring_cannot_grow_without_bound(self):
        for i in range(200):
            km._sync_notice("sync %d" % i)
        self.assertLessEqual(len(km._SYNC_NOTICES), km.SYNC_RING)
        self.assertLessEqual(len(km._sync_notice_rows()), 20)

    def test_the_count_moves_only_when_a_sync_finished(self):
        n = km._sync_notice_count()
        self.assertEqual(km._sync_notice_count(), n, "reading it twice is not an event")
        km._sync_notice("pushed to web")
        self.assertEqual(km._sync_notice_count(), n + 1)

    def test_the_text_is_capped_so_a_remote_error_cannot_flood_the_log(self):
        km._sync_notice("x" * 5000, ok=False)
        self.assertLessEqual(len(km._sync_notice_rows()[0]["text"]), 300)


class Wiring(unittest.TestCase):
    def test_every_sync_direction_files_its_outcome(self):
        for fn in (km._auto_push_remote, km._auto_pull_remote, km._auto_ask_peer):
            src = inspect.getsource(fn)
            self.assertIn("_sync_notice(", src, "%s must say what it did" % fn.__name__)
            self.assertIn("ok=False", src, "%s must log the failure too, not only the win" % fn.__name__)

    def test_a_success_is_logged_and_not_only_a_failure(self):
        # the point of the feature: an unwatched push that WORKED left no record either
        src = inspect.getsource(km._auto_push_remote)
        before = src.index("_sync_notice(")
        self.assertLess(before, src.index("ok=False"), "the success branch logs first")

    def test_the_payload_carries_the_ring_and_the_signature_rebuilds_on_it(self):
        self.assertIn('"syncNotices": _sync_notice_rows(),', inspect.getsource(km.build_feed))
        self.assertIn('sig["__syncn__"] = _sync_notice_count()', inspect.getsource(km._fleet_view_sig))

    def test_the_log_knows_the_kind(self):
        js = km._LANDING_ERRS_JS
        self.assertIn("'sdk','sync'", js, "the filter chips must include it")
        self.assertIn("sync:'fleet sync'", js)
        self.assertIn('sync:"romp moved commits', js, "the chip must explain itself on hover")
        self.assertIn(".rerr-chip.k-sync", km._landing(), "…and wear a chip colour of its own")


class VersionTag(unittest.TestCase):
    """A bare sha means nothing without your own beside it (the user 2026-07-30)."""

    def test_the_kernel_reports_the_release_its_code_descends_from(self):
        v = km._kernel_ver()
        self.assertTrue(v is None or isinstance(v, str))
        if v:
            self.assertTrue(v.startswith("v"), "a release reads as a version, not a bare number")

    def test_the_version_route_publishes_it(self):
        self.assertIn("kernel_ver", km._version_info())

    def test_the_version_route_publishes_parse_stats(self):
        # the assembly cache's counters ride /version (T210): /healthz's body is a frozen
        # liveness contract, so this is the designed runtime-observability home — deploy
        # verification reads the live fold rate here
        v = km._version_info()
        self.assertIn("parse", v)
        for key in ("full", "fold", "serve", "bypass", "fallback"):
            self.assertIsInstance(v["parse"].get(key), int)
        before = km.em._ASM_STATS["fold"]
        km.em._ASM_STATS["fold"] = before + 7
        try:
            self.assertEqual(km._version_info()["parse"]["fold"], before + 7,
                             "the payload reads the LIVE counters, not a boot-time copy")
        finally:
            km.em._ASM_STATS["fold"] = before

    def test_a_remote_poll_carries_the_tag_alongside_the_sha(self):
        src = inspect.getsource(km._poll_remote_version)
        # the tag is read off /version beside the sha and returned as `ver` — since 2026-09-08 only when it
        # fits _kernel_ver's shape (a peer's word, judged before it is stored; test_peer_payloads_as_text)
        self.assertIn('ver = j.get("kernel_ver") or ""', src)
        self.assertIn("if ver and not _peer_ver(ver):", src)
        self.assertIn('"ver": ver,', src)
        self.assertIn('"sha": sha', src)

    def test_the_row_publishes_both_sides(self):
        src = inspect.getsource(km._remote_public)
        for k in ('"kernelVer"', '"localVer"', '"kernelSha"', '"localSha"'):
            self.assertIn(k, src, "%s is what makes the two builds comparable" % k)

    def test_the_panel_names_this_machines_build_too(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("function buildWord(v,s)", js)
        self.assertIn("rnet-me", js, "the panel says what THIS machine is on")
        self.assertIn("rnet-mybuild", km._landing())

    def test_the_drift_says_ahead_and_behind_in_words(self):
        # an arrow only reads as a direction if you already know which way romp points it, and this is
        # where a reader decides whether to push or pull (the user 2026-07-30, replacing the arrows)
        js = km._LANDING_REMOTES_JS
        self.assertIn("up=ab>0?('ahead '+ab):''", js)
        self.assertIn("down=bb>0?('behind '+bb):''", js)
        self.assertIn("function driftCounts(t)", js)
        self.assertNotIn("\\u2191", js, "no arrow spelling may survive alongside the words")
        self.assertNotIn("\\u2193", js)


class OneCommitOneVersion(unittest.TestCase):
    """Two machines on the SAME commit read as two different releases in the network panel (the user
    2026-08-02: this machine v0.3.0 at 4a0beaa, the host beside it v0.2.0+ at 4a0beaa). The release is a
    property of the code, so that pairing cannot both be true — and the sha said which reading was the
    artifact: `romp update` pushes COMMITS over ssh, never tags, so the far clone was naming the release
    off whatever older tag it had ever fetched."""

    def test_the_release_is_read_off_the_committed_tree_not_the_local_tags(self):
        body = inspect.getsource(km._kernel_ver).split('"""')[2]   # past the docstring, which says why
        self.assertIn('"VERSION"', body, "the release name has to travel with the commit")
        self.assertNotIn("describe", body,
                         "tags are refs; a pushed commit arrives without them, so describing is a "
                         "per-clone answer to a per-commit question")

    def test_the_version_matches_the_file_every_machine_gets(self):
        v = km._kernel_ver()
        if v is None:
            return                      # not a git checkout / no VERSION file — nothing to agree on
        root = os.path.dirname(HERE)
        with open(os.path.join(root, "VERSION")) as f:
            want = "v" + f.read().strip()
        self.assertEqual(v.rstrip("+"), want, "the number both machines can see in their own tree")

    def test_a_host_on_this_commit_wears_this_machines_release(self):
        row = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 51000, "token": "tok",
               "status": "up", "sids": [], "kernel_sha": (km._local_head(short=True) or "abc1234"),
               "kernel_ver": "v0.1.0+", "proc": None}
        pub = km._remote_public(row)
        self.assertFalse(pub["outOfDate"], "same commit — nothing to push")
        self.assertEqual(pub["kernelVer"], pub["localVer"],
                         "one commit cannot be two releases; the stale tag graph doesn't get a vote")

    def test_a_host_on_another_commit_keeps_its_own_release(self):
        row = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 51000, "token": "tok",
               "status": "up", "sids": [], "kernel_sha": "0000000", "kernel_ver": "v0.1.0", "proc": None}
        pub = km._remote_public(row)
        self.assertEqual(pub["kernelVer"], "v0.1.0",
                         "a genuinely different build must still say what IT is running")


class SpinWhileWorking(unittest.TestCase):
    """The romp loader shows wherever the panel is WAITING on work (the user 2026-07-30)."""

    def test_one_loader_definition_serves_the_whole_panel(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("function spin()", js)
        self.assertIn("romp-swirl-glyph.svg", js)
        self.assertEqual(js.count("romp-swirl-glyph.svg"), 1, "one definition, not a copy per site")

    def test_a_sync_in_flight_spins_and_a_settled_one_does_not(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("function apBusy(p)", js)
        for phase in ("'pushing'", "'pulling'", "'asking'", "'waiting'"):
            self.assertIn(phase, js.split("function apBusy(p)")[1].split("\n")[0])
        # failed / pulled are outcomes: they are read, not waited on
        head = js.split("function apBusy(p)")[1].split("\n")[0]
        self.assertNotIn("'failed'", head)
        self.assertNotIn("'pulled'", head)

    def test_the_places_that_wait_on_the_link_all_wear_it(self):
        js = km._LANDING_REMOTES_JS
        self.assertIn("apBusy(t.autoPush.phase)?spin():''", js, "a sync mid-flight")
        self.assertIn("busyStatus(t.status)?spin():''", js, "a connecting tunnel")
        self.assertIn("'<span class=rnet-pend>'+spin()+'applying", js, "a trust change crossing the link")
        self.assertIn("+spin()+'Matching", js, "…and the mirror of it on the far side")


class SharePublishMemory(unittest.TestCase):
    """"Share my sessions there" kept coming back unchecked (the user 2026-07-30). Detach POPS the live
    row, which was the only place the flag lived — the same disease trust had before known_trust."""

    def setUp(self):
        with km._known_lock:
            km._known.clear()

    def test_turning_it_on_is_remembered_for_the_host(self):
        km._known_note("web", "trusted", share=True)
        self.assertTrue(km.known_share("web"))
        self.assertEqual(km.known_trust("web"), "trusted")

    def test_a_trust_only_note_does_not_clear_it(self):
        km._known_note("web", share=True)
        km._known_note("web", "isolated")          # an attach refreshing the level, nothing more
        self.assertTrue(km.known_share("web"), "None means don't touch, not off")

    def test_turning_it_off_is_remembered_too(self):
        km._known_note("web", share=True)
        km._known_note("web", share=False)
        self.assertFalse(km.known_share("web"))

    def test_an_unknown_host_shares_nothing(self):
        self.assertFalse(km.known_share("never-seen"))

    def test_the_flag_survives_a_save_and_load(self):
        km._known_note("web", "trusted", share=True)
        with km._known_lock:
            km._known.clear()
        km._known_load()
        self.assertTrue(km.known_share("web"), "the memory is on disk, not just in this process")

    def test_setting_the_box_records_it_and_detach_captures_it_on_the_way_out(self):
        # attached=True rides along since the known store split attach-path rows from
        # trust-only ones (test_known_attached_flag) — the share memory itself is unchanged
        self.assertIn("_known_note(host, share=bool(on), attached=True)", inspect.getsource(km.checkin_set))
        self.assertIn('share=bool(r.get("checkin"))', inspect.getsource(km.detach_remote))

    def test_re_attaching_restores_the_publish_and_its_ports(self):
        src = inspect.getsource(km.attach_remote)
        self.assertIn('"checkin": known_share(host),', src)
        # the reverse forwards go in the tunnel argv at SPAWN, and checkin_set is not what runs here
        self.assertIn('r["rk_port"], r["rb_port"] = _free_port(), _free_port()', src)


if __name__ == "__main__":
    unittest.main()
