#!/usr/bin/env python3
"""Restart means the FLEET (the user 2026-07-29).

Restart used to restart this machine's kernel and nothing else, which is a half-truth once remotes are
attached: they kept running their old processes on their old code, and nothing said so. Restart now
covers every reachable kernel, syncs on the way wherever a clean fast-forward can be PROVEN in either
direction, and reports per host what it did and what it refused.

The refusals are the point. A diverged remote, a dirty tree on either side, a relationship this repo
cannot evaluate: skipped, with the reason named, never guessed at. The report is written to disk before
the local restart, because that restart takes the reporting process down with it.

Synthetic only — placeholder hosts/shas, no ssh, no restarts.
"""
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_fleet", os.path.join(BIN, "romp-kernel"))

LOCAL_SHA = "a" * 40
REMOTE_SHA = "b" * 40


def row(**kw):
    r = {"host": "TESTHOST", "status": "up", "kernel_sha": "", "trust": "directed"}
    r.update(kw)
    return r


class Plan(unittest.TestCase):
    """_fleet_restart_plan is the whole decision, pure, so the report and these tests read the same rule."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_remote_out_of_date", "_is_fast_forward", "_is_fast_pull", "_is_ask_pull",
                        "_behind_info", "_local_branch")}
        km._local_branch = lambda: "main"

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)

    def _stub(self, out_of_date=False, ff=False, pull=False, ask=False, behind=None, ahead=None):
        km._remote_out_of_date = lambda r, head=None: out_of_date
        km._is_fast_forward = lambda r, head=None: ff
        km._is_fast_pull = lambda r, head=None: pull
        km._is_ask_pull = lambda r, head=None: ask
        km._behind_info = lambda sha, head=None: {"behind": behind, "ahead": ahead}

    def test_a_disconnected_host_is_skipped_and_says_romp_is_still_dialing(self):
        self._stub()
        action, why = km._fleet_restart_plan(row(status="down"))
        self.assertEqual(action, "skip")
        self.assertIn("still dialing", why)

    def test_a_host_on_this_build_is_simply_restarted(self):
        self._stub(out_of_date=False)
        action, why = km._fleet_restart_plan(row())
        self.assertEqual(action, "restart")
        self.assertIn("already on this build", why)

    def test_a_host_strictly_behind_is_pushed_and_restarted(self):
        self._stub(out_of_date=True, ff=True)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "sync-push")
        self.assertIn("behind", why)

    def test_a_host_strictly_ahead_fast_forwards_THIS_machine_onto_it(self):
        # "make sure that both kernels have the latest code via fast forwarding from each other"
        self._stub(out_of_date=True, pull=True)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "sync-pull")
        self.assertIn("ahead", why)

    def test_a_pull_is_refused_when_this_checkout_is_not_on_main(self):
        self._stub(out_of_date=True, pull=True)
        km._local_branch = lambda: "some-feature"
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "skip")
        self.assertIn("isn't on main", why)

    def test_a_diverged_host_is_skipped_and_the_reason_counts_its_commits(self):
        self._stub(out_of_date=True, behind=3, ahead=4)
        action, why = km._fleet_restart_plan(row(kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "skip")
        self.assertIn("diverged", why)
        self.assertIn("4 commit", why, "it names what would be clobbered")
        self.assertIn("not clobbering", why)

    def test_an_unknown_relationship_is_skipped_rather_than_guessed(self):
        # the remote's sha isn't in this repo at all (updated from a third machine): None is not zero
        self._stub(out_of_date=True, behind=None, ahead=None)
        action, why = km._fleet_restart_plan(row(kernel_sha="c" * 40))
        self.assertEqual(action, "skip")
        self.assertIn("nothing can be proven safe", why)

    def test_a_checked_in_peer_is_ASKED_because_there_is_no_ssh_route_here(self):
        self._stub(out_of_date=True, ask=True)
        action, _ = km._fleet_restart_plan(row(checkin_peer=True, kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "ask")
        self._stub(out_of_date=True, ask=False)
        action, why = km._fleet_restart_plan(row(checkin_peer=True, kernel_sha=REMOTE_SHA))
        self.assertEqual(action, "skip")
        self.assertIn("its own dashboard", why)


class ReportSurvivesTheRestart(unittest.TestCase):
    def test_the_run_writes_every_host_then_restarts_this_machine_last(self):
        src = inspect.getsource(km._fleet_restart_run)
        self.assertIn("_atomic_write(FLEET_REPORT", src)
        self.assertLess(src.index("_atomic_write(FLEET_REPORT"), src.index("_restart_this_kernel("),
                        "the report must be on disk BEFORE this process is taken down")
        self.assertIn("except Exception as e:", src)   # one bad host never strands the rest of the fleet

    def test_a_failing_host_lands_in_the_report_and_the_sweep_continues(self):
        saved = {n: getattr(km, n) for n in ("_fleet_restart_plan", "_restart_remote_kernel",
                                            "_restart_this_kernel", "_fresh_local_head", "_local_branch")}
        km._fleet_restart_plan = lambda r, head=None: ("restart", "already on this build")
        km._restart_remote_kernel = lambda h: (_ for _ in ()).throw(RuntimeError("ssh exploded"))
        # audits its reason since 2026-07-31; carries the handler's ack-time port since 2026-08-27
        km._restart_this_kernel = lambda reason="", manager_port=None: None
        km._fresh_local_head = lambda: "abc1234" + "0" * 33     # the run reads the head once, from git
        km._local_branch = lambda: "main"
        km._remotes.clear()
        km._remotes["web"] = row(host="web")
        km._remotes["api"] = row(host="api")
        try:
            km._fleet_restart_run()
            report = json.loads(km.FLEET_REPORT.read_text())
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
            km._remotes.clear()
        hosts = sorted(x["host"] for x in report["rows"])
        self.assertEqual(hosts, ["api", "web"], "both hosts reported, neither stranded by the other")
        for x in report["rows"]:
            self.assertFalse(x["ok"])
            self.assertIn("ssh exploded", x["detail"])
        self.assertEqual(report["local"]["head"], "abc1234")

    def test_the_plan_is_judged_against_the_head_this_checkout_is_at_now(self):
        # the polls' head cache can be 15 s behind a commit made just before Restart: a peer sitting on the
        # previous commit then reads as "already on this build" and gets a bare restart where a push was owed
        stale, fresh = "3" * 40, "4" * 40
        saved = {n: getattr(km, n) for n in ("_update_remote", "_restart_remote_kernel", "_restart_this_kernel",
                                            "_behind_info", "_local_branch")}
        hc, run = dict(km._HEAD_CACHE), km.subprocess.run
        did = []
        km._update_remote = lambda h: did.append(("push", h)) or (True, "synced")
        km._restart_remote_kernel = lambda h: did.append(("restart", h)) or (True, "restarted")
        km._restart_this_kernel = lambda reason="", manager_port=None: None
        km._behind_info = lambda sha, head=None: {"behind": 1, "ahead": 0, "date": ""}   # strictly behind: a push only adds
        km._local_branch = lambda: "main"
        km._HEAD_CACHE.update(ts=9e18, full=stale, short=stale[:7])             # what the polls last read
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:                        # what git says NOW
                return subprocess.CompletedProcess(argv, 0, fresh[:7] if "--short" in argv else fresh, "")
            return run(argv, **kw)
        km.subprocess.run = fake
        km._remotes.clear()
        km._remotes["TESTHOST"] = row(kernel_sha=stale[:7])                      # the peer is on the OLD commit
        try:
            km._fleet_restart_run()
            report = json.loads(km.FLEET_REPORT.read_text())
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
            km.subprocess.run = run
            km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(hc)
            km._remotes.clear()
        self.assertEqual(did, [("push", "TESTHOST")], "behind the head the user has: pushed, not merely restarted")
        self.assertEqual(report["rows"][0]["action"], "sync-push")
        self.assertEqual(report["local"]["head"], fresh[:7], "the report names the head the plan was judged against")

    def _run_two_rows(self, first, second, git_head, on_restart=None, on_pull=None, drift=None):
        """Drive _fleet_restart_run over peers `web` (on `first`) and `api` (on `second`) with git answering
        `git_head[0]` for HEAD; the mocked actions record what happened and may move `git_head` or the cache
        as a real row's minutes of ssh would. `drift` = how a peer's sha relates to the head it is judged
        against (strictly behind unless given). Returns (actions done, the report)."""
        saved = {n: getattr(km, n) for n in ("_update_remote", "_pull_remote", "_restart_remote_kernel",
                                            "_restart_this_kernel", "_behind_info", "_local_branch")}
        hc, run = dict(km._HEAD_CACHE), km.subprocess.run
        did = []
        def restart(h):
            did.append(("restart", h))
            if on_restart:
                on_restart()
            return True, "restarted"
        def pull(h):
            did.append(("pull", h))
            if on_pull:
                on_pull()
            return True, "pulled"
        km._update_remote = lambda h, **kw: did.append(("push", h)) or (True, "synced")
        km._pull_remote, km._restart_remote_kernel = pull, restart
        km._restart_this_kernel = lambda reason="", manager_port=None: None
        km._behind_info = drift or (lambda sha, head=None: {"behind": 1, "ahead": 0, "date": ""})
        km._local_branch = lambda: "main"
        def fake(argv, **kw):
            if argv[0] == "git" and "rev-parse" in argv:                         # what git says NOW
                h = git_head[0]
                return subprocess.CompletedProcess(argv, 0, h[:7] if "--short" in argv else h, "")
            return run(argv, **kw)
        km.subprocess.run = fake
        km._remotes.clear()
        km._remotes["web"] = row(host="web", kernel_sha=first[:7])
        km._remotes["api"] = row(host="api", kernel_sha=second[:7])
        try:
            km._fleet_restart_run()
            return did, json.loads(km.FLEET_REPORT.read_text())
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
            km.subprocess.run = run
            km._HEAD_CACHE.clear(); km._HEAD_CACHE.update(hc)
            km._remotes.clear()

    def test_every_row_is_judged_against_the_one_head_read_before_the_plan(self):
        # Two peers in the identical state, both on the head this checkout is at. The first row's ssh runs
        # long enough for the polls' 15 s head cache to expire, and a commit lands locally meanwhile. Judged
        # per row THROUGH the cache, the second peer read as behind the new commit and was pushed to where its
        # twin got a bare restart, and the report named a head neither row had been planned against. The run
        # reads the head once and carries the value (review find, 2026-09-08).
        before, after = "5" * 40, "6" * 40
        git_head = [before]
        def twenty_seconds_of_ssh():
            git_head[0] = after                    # a commit lands mid-run...
            km._HEAD_CACHE["ts"] -= 20             # ...and the first row outlives the cache's TTL
        did, report = self._run_two_rows(before, before, git_head, on_restart=twenty_seconds_of_ssh)
        self.assertEqual(did, [("restart", "web"), ("restart", "api")], "twins get the same verdict")
        self.assertEqual([x["action"] for x in report["rows"]], ["restart", "restart"])
        self.assertEqual(report["local"]["head"], before[:7], "the report names the head every row was judged against")

    def test_a_sync_pull_moves_the_head_the_rows_after_it_are_judged_against(self):
        # web is one commit ahead; api sits where this checkout started. The pull fast-forwards this machine
        # onto web's commit, and that IS new information: api is now behind it and owed a push. A head pinned
        # across the pull would have called api "already on this build" and bare-restarted it one commit
        # behind, so the run re-reads the head after a pull and only there.
        old, new = "7" * 40, "8" * 40
        git_head = [old]
        def fast_forwarded():
            git_head[0] = new
        ahead = lambda sha, head=None: ({"behind": 0, "ahead": 1, "date": ""} if sha == new[:7]   # web, before the pull
                                        else {"behind": 1, "ahead": 0, "date": ""})              # api, judged after it
        did, report = self._run_two_rows(new, old, git_head, on_pull=fast_forwarded, drift=ahead)
        self.assertEqual(did, [("pull", "web"), ("push", "api")], "the peer still on the old head is pushed the pulled commit")
        self.assertEqual(report["local"]["head"], new[:7], "the report names the head this machine restarts on")

    def test_the_route_hands_the_report_back_and_the_page_shows_it_once(self):
        src = inspect.getsource(km.Handler)
        self.assertIn('if u.path == "/fleet-restart":', src)
        self.assertIn("FLEET_REPORT.read_text()", src)
        self.assertIn("threading.Thread(target=_fleet_restart_run,", src)
        self.assertIn('kwargs={"manager_port": _mport}, daemon=True).start()', src,
                      "the remote leg carries the port resolved before the ack, like the local one")
        # the page reads it back AFTER reloading and shows it exactly once (keyed on the report's stamp).
        # It rides the block that owns the Restart button itself, so the two can never drift apart.
        js = km._LANDING_SETTINGS_JS
        self.assertIn("window.__rompRestart", js, "the report lives with the button that causes it")
        self.assertIn("fetch('/fleet-restart'", js)
        self.assertIn("romp:fleetSeen", js)
        self.assertIn("_fleetReport();", js, "and runs on load, after the restart brought the page back")

    def test_a_kernel_with_no_remotes_restarts_exactly_as_before(self):
        src = inspect.getsource(km.Handler)
        self.assertIn("if _fleet and _remotes:", src)
        self.assertIn("else:\n                    _restart_this_kernel(\"http /restart (local-only)\", "
                      "manager_port=_mport)", src)


class AnAskStaysOnThatPeer(unittest.TestCase):
    """The sweep's "ask" leg tells a checked-in peer to pull and then to restart — and that restart
    must name the peer-only scope. The peer's /restart defaults to the broad kind, and the peer always
    holds at least one row: this hub. So the empty body the ask used to send made the freshly updated
    peer fan out and restart the hub back, mid-sweep, before the report was on disk — a restart that
    cascaded onto machines nobody asked to restart. The hub walks its own rows; each ask is one host."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_fleet_restart_plan", "_peer_call", "_peer_hub_name", "_local_head",
                        "_local_branch", "_restart_this_kernel")}
        self._remotes = dict(km._remotes)
        # the sweep hands the plan the head it read once (#1025); the stub takes it like the real function
        km._fleet_restart_plan = lambda r, head=None: ("ask", "checked in here; asking it to fast-forward itself")
        km._peer_hub_name = lambda r: "hubname"
        km._local_head = lambda short=False: ("abc1234" if short else LOCAL_SHA)
        km._local_branch = lambda: "main"
        km._restart_this_kernel = lambda reason="", manager_port=None: None   # never a real restart
        self.calls = []

        def _peer_call(r, method, path, body=None, timeout=8):
            self.calls.append((method, path, body))
            return 200, ({"ok": True, "detail": "pulled 3 commits from hubname"}
                         if path == "/tunnels/pull" else {"ok": True, "restarting": True, "fleet": False})
        km._peer_call = _peer_call
        km._remotes.clear()
        km._remotes["TESTHOST"] = row(checkin_peer=True, kernel_sha=REMOTE_SHA, local_port=52025,
                                      token="peertok")

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km._remotes.clear()
        km._remotes.update(self._remotes)

    def test_the_sweep_asks_each_peer_for_a_restart_of_itself_only(self):
        km._fleet_restart_run()
        report = json.loads(km.FLEET_REPORT.read_text())
        self.assertEqual([(m, p) for m, p, _ in self.calls], [("POST", "/tunnels/pull"), ("POST", "/restart")])
        self.assertEqual(self.calls[1][2], {"fleet": False},
                         "the peer restarts ITSELF; an empty body would let it fan out onto this hub")
        self.assertEqual([(x["host"], x["ok"], x["action"]) for x in report["rows"]],
                         [("TESTHOST", True, "ask")])
        self.assertIn("restarting it", report["rows"][0]["detail"])

    def test_the_ask_alone_carries_the_same_scope(self):
        ok, detail = km._ask_peer_to_pull("TESTHOST")
        self.assertTrue(ok)
        self.assertEqual(self.calls[-1], ("POST", "/restart", {"fleet": False}))

    def test_a_restart_the_peer_refuses_lands_in_the_report_with_its_reason(self):
        """The peer's /restart can refuse (a 400 naming why, since it stopped taking a malformed body
        as the broad default), and the sweep's report row is that answer's only reader. It used to
        drop the text and say just "did not ack": the row keeps the peer's own words, next to what is
        left to do by hand (review find, 2026-09-08)."""
        def _peer_call(r, method, path, body=None, timeout=8):
            self.calls.append((method, path, body))
            if path == "/tunnels/pull":
                return 200, {"ok": True, "detail": "pulled 3 commits from hubname"}
            return 400, {"ok": False, "error": "body could not be read: read 0 of the 16 bytes announced"}
        km._peer_call = _peer_call
        km._fleet_restart_run()
        report = json.loads(km.FLEET_REPORT.read_text())
        [x] = report["rows"]
        self.assertEqual((x["host"], x["ok"], x["action"]), ("TESTHOST", True, "ask"),
                         "the commits DID land; that is not a failed row")
        self.assertIn("pulled 3 commits", x["detail"])
        self.assertIn("did not ack the restart", x["detail"])
        self.assertIn("body could not be read", x["detail"], "the peer's reason, not a bare 'did not ack'")
        self.assertIn("restart romp on TESTHOST", x["detail"], "and what is left to do")


class GlyphSaysTheFleetState(unittest.TestCase):
    """The rail's network glyph carries the whole verdict, so drift/disconnection reads without opening
    the panel (the user 2026-07-29): top node = this machine, the two below = the remotes."""

    def setUp(self):
        self.js = km._LANDING_REMOTES_JS

    def test_severity_is_ranked_worst_first_so_a_sick_host_cannot_hide(self):
        self.assertIn("var SEVRANK={warn:0,wait:1,ok:2};", self.js)
        self.assertIn("return [sev[0],sev.length>1?sev[1]:sev[0]];", self.js,
                      "one attached host colours BOTH nodes — its state is the fleet's")

    def test_connected_and_in_sync_is_accent_drift_is_red(self):
        self.assertIn("if(t.status==='up')return t.outOfDate?'warn':'ok';", self.js)

    def test_a_fresh_drop_is_grey_but_a_persistent_one_needs_you(self):
        # romp is retrying either way; the difference is whether it is still plausibly a blip
        self.assertIn("if((t.fails||0)>=12)return 'warn';", self.js)
        self.assertIn("return 'wait';", self.js)

    def test_nothing_attached_leaves_the_glyph_exactly_as_it_was(self):
        self.assertIn("if(!ts.length)return null;", self.js)

    def test_both_glyphs_carry_paintable_nodes_and_the_colours_are_defined(self):
        html = km._landing()
        self.assertEqual(html.count("class=rn-me"), 2, "the rail glyph and the mobile one")
        self.assertEqual(html.count("class=rn-a"), 2)
        self.assertEqual(html.count("class=rn-b"), 2)
        self.assertIn(".rn-ok{fill:var(--accent)}", html)
        self.assertIn(".rn-wait{fill:#8a8a8a}", html)
        self.assertIn(".rn-warn{fill:#e5484d}", html)
        self.assertIn("paintNodes(icon,nodes)", html)

    def test_the_tooltip_says_the_verdict_in_words(self):
        # the colour is the glance; the words behind it are one hover away (progressive disclosure)
        self.assertIn("' attention'", self.js)
        self.assertIn("disconnected (romp is retrying)", self.js)
        self.assertIn("all hosts connected and in sync", self.js)


if __name__ == "__main__":
    unittest.main()
