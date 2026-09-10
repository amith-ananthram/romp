#!/usr/bin/env python3
"""T301 (the user 2026-09-10): the API-health signal covers EVERY connected kernel. Kernel half:
  - GET /api-health/frame serves this kernel's last apiHealth shell frame, its local half only (no `hosts`),
    authed, 503 before the first cycle;
  - the tunnel supervisor's poll of an attached host's frame (_poll_remote_api_health) is rate-gated, keeps
    the last reading on a blip and clears it when the host answers that it has none;
  - the shell frame carries every cached remote frame under `hosts` as a per-host MAP with a `stale` mark,
    and an unchanged fleet yields an identical frame (no push);
  - GET /remote/<host>/api-health relays one read of an attached host's document: 404 for an unknown host, the
    remote's own token in the forwarded request, the status and JSON passed through, 502 on a dead tunnel;
  - the snapshot's additive per-bucket `series` (api_health_series) bins attempts per minute over the slow
    window, oldest first, with the last bin ending at asOf.
Synthetic hosts (TESTHOST, PEERHOST), placeholder ids, hermetic state."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
# the rail test's kernel load (hermetic XDG, ROMP_SERVE_TOKEN, NO_OPEN) and its frame fixture: ONE kernel module for
# the fixture's patched seams and these routes alike
from tests.test_api_health_rail import _Fixture, km  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "kernel"))
import sdk_backend as sb  # noqa: E402

REMOTE_TOKEN = "remote-token-DO-NOT-USE"
FRAME_A = {"type": "apiHealth", "state": "degraded", "cls": "429", "text": "rate limited · 2 waiting", "waiting": 2,
           "retrying": 2, "blocked": 0, "since": 1000, "reason": "", "tmux": 0, "sessions": [], "seq": 3}
DOC = {"schema": 1, "asOf": 2000.0, "overall": {"state": "healthy", "worstBucket": "key:helper|fable"}, "buckets": {}}


class _FakeRemote(BaseHTTPRequestHandler):
    """A remote kernel that serves /api-health (the document) and /api-health/frame (its frame) to the right
    token only, and records what it was asked."""
    frame = FRAME_A
    doc = DOC
    seen = []

    def do_GET(self):
        _FakeRemote.seen.append((self.path, self.headers.get("X-Romp-Token")))
        tok = self.headers.get("X-Romp-Token") or (self.path.split("token=")[1].split("&")[0] if "token=" in self.path else "")
        if tok != REMOTE_TOKEN:
            self.send_response(401); self.end_headers(); self.wfile.write(b"bad token"); return
        if self.path.startswith("/api-health/frame"):
            body = json.dumps(_FakeRemote.frame).encode() if _FakeRemote.frame else b""
            if not _FakeRemote.frame:
                self.send_response(503); self.end_headers(); self.wfile.write(b'{"error":"no frame"}'); return
        elif self.path.startswith("/api-health"):
            body = json.dumps(_FakeRemote.doc).encode()
        else:
            self.send_response(404); self.end_headers(); self.wfile.write(b"nope"); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class HostsMap(_Fixture):
    """The frame's per-host map, from the supervisor's cached remote frames."""

    def setUp(self):
        super().setUp()
        with km._remotes_lock:
            self._saved = dict(km._remotes)
            km._remotes.clear()

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved)
        super().tearDown()

    def _row(self, host, frame, status="up"):
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "kernel_port": 1, "local_port": 1, "token": REMOTE_TOKEN,
                                 "status": status, "apiHealth": frame}

    def test_no_attached_host_gives_an_empty_map_and_the_local_scalars_stand(self):
        f = self.frame()
        self.assertEqual(f["hosts"], {})
        self.assertEqual(f["state"], "ok")
        self.assertIs(f["quiet"], True, "the fixture has no SDK backend traffic: quiet")

    def test_quiet_follows_the_aggregator_s_last_event(self):
        ah = sb.ApiHealth(Path(self.td.name) / "api-health-state.json") if hasattr(sb, "ApiHealth") else None
        self.assertIsNotNone(ah)
        self.assertTrue(ah.quiet(1000.0), "no event yet")
        with ah._lock:
            ah._last_event_at = 1000.0 - 100
        self.assertFalse(ah.quiet(1000.0), "an event inside the longest window")
        self.assertTrue(ah.quiet(1000.0 + 901), "aged out of the 900 s window")

    def test_every_cached_remote_frame_rides_under_its_host_with_the_frame_keys_and_a_stale_mark(self):
        self._row("TESTHOST", FRAME_A)
        self._row("PEERHOST", dict(FRAME_A, state="ok", cls="", text="ok", waiting=0, retrying=0), status="down")
        f = self.frame()
        self.assertEqual(sorted(f["hosts"]), ["PEERHOST", "TESTHOST"])
        t = f["hosts"]["TESTHOST"]
        self.assertEqual((t["state"], t["cls"], t["waiting"], t["since"], t["stale"]), ("degraded", "429", 2, 1000, False))
        self.assertNotIn("quiet", t, "a peer frame that carries no flag gets none invented")
        self.assertNotIn("sessions", t, "the rows stay on their own kernel: the map carries the summary")
        self.assertNotIn("seq", t, "a pause-file counter is per kernel and never compared across hosts")
        self.assertTrue(f["hosts"]["PEERHOST"]["stale"], "a row whose tunnel is not up is marked, its last frame kept")
        # the LOCAL scalars are this kernel's alone: a remote storm does not change them
        self.assertEqual((f["state"], f["waiting"]), ("ok", 0))

    def test_an_unchanged_fleet_yields_an_identical_frame_so_nothing_is_pushed_twice(self):
        self._row("TESTHOST", FRAME_A)
        a = json.dumps(self.frame(), sort_keys=True)
        b = json.dumps(self.frame(), sort_keys=True)
        self.assertEqual(a, b)
        km._api_health_push(self.frame())
        km._api_health_push(self.frame())
        self.assertEqual(len(self.sent), 1, "the second identical frame is not sent")

    def test_the_local_frame_route_serves_the_last_frame_minus_hosts_and_503_before_the_first(self):
        km._APIH_LAST[0] = None
        self.assertIsNone(km._apih_local_frame())
        self._row("TESTHOST", FRAME_A)
        km._api_health_push(self.frame())
        f = km._apih_local_frame()
        self.assertNotIn("hosts", f, "a peer gets this kernel's local half only: no nesting between two attached kernels")
        self.assertEqual(f["state"], "ok")


class RemotePoll(unittest.TestCase):
    """_poll_remote_api_health against a fake remote."""

    def setUp(self):
        _FakeRemote.frame, _FakeRemote.seen = FRAME_A, []
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRemote)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        self.row = {"host": "TESTHOST", "local_port": self.fake.server_address[1], "token": REMOTE_TOKEN, "status": "up"}

    def tearDown(self):
        self.fake.shutdown(); self.fake.server_close()

    def test_the_poll_reads_the_frame_with_the_remote_token_and_is_rate_gated(self):
        f = km._poll_remote_api_health(self.row)
        self.assertEqual(f["state"], "degraded")
        self.assertEqual(len(_FakeRemote.seen), 1)
        self.assertIn("token=" + REMOTE_TOKEN, _FakeRemote.seen[0][0])
        self.row["apiHealth"] = f
        again = km._poll_remote_api_health(self.row)
        self.assertEqual(again, f, "inside the gate the cached reading comes back")
        self.assertEqual(len(_FakeRemote.seen), 1, "and the remote is not asked again")

    def test_an_answered_no_frame_clears_and_a_dead_port_keeps_the_last_reading(self):
        _FakeRemote.frame = None
        self.assertEqual(km._poll_remote_api_health(dict(self.row)), {}, "503 = answered with no frame: clear")
        dead = {"host": "TESTHOST", "local_port": _free_port(), "token": REMOTE_TOKEN, "apiHealth": FRAME_A}
        self.assertEqual(km._poll_remote_api_health(dead), FRAME_A, "no answer keeps the last reading")


class Relay(unittest.TestCase):
    """GET /remote/<host>/api-health through km.Handler."""

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        _FakeRemote.frame, _FakeRemote.seen = FRAME_A, []
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRemote)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        with km._remotes_lock:
            self._saved = dict(km._remotes)
            km._remotes.clear()
            km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": self.fake.server_address[1],
                                       "token": REMOTE_TOKEN, "status": "up", "sids": [], "trust": "directed"}
            km._remotes["DEADHOST"] = {"host": "DEADHOST", "kernel_port": 29855, "local_port": _free_port(),
                                       "token": REMOTE_TOKEN, "status": "up", "sids": [], "trust": "directed"}

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear(); km._remotes.update(self._saved)
        self.srv.shutdown(); self.srv.server_close()
        self.fake.shutdown(); self.fake.server_close()

    def _get(self, path, token=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request("GET", path, headers=({"X-Romp-Token": km.TOKEN} if token else {}))
        r = c.getresponse(); body = r.read(); ct = r.getheader("Content-Type") or ""; c.close()
        return r.status, body, ct

    def test_the_document_passes_through_under_our_content_type_with_the_remote_token_rewritten_in(self):
        st, body, ct = self._get("/remote/TESTHOST/api-health")
        self.assertEqual(st, 200, body[:200])
        self.assertEqual(json.loads(body), DOC)
        self.assertTrue(ct.startswith("application/json"))
        self.assertEqual(_FakeRemote.seen[-1], ("/api-health", REMOTE_TOKEN), "the remote saw its OWN token, never the browser's")

    def test_an_unknown_host_is_404_and_a_dead_tunnel_is_502(self):
        st, body, _ = self._get("/remote/NOSUCHHOST/api-health")
        self.assertEqual(st, 404)
        self.assertIn(b"no attached host", body)
        st, body, _ = self._get("/remote/DEADHOST/api-health")
        self.assertEqual(st, 502)
        self.assertIn(b"not answering", body)

    def test_the_relay_is_behind_the_local_auth_gate(self):
        st, _, _ = self._get("/remote/TESTHOST/api-health", token=False)
        self.assertIn(st, (401, 403))
        self.assertEqual([s for s in _FakeRemote.seen if s[0] == "/api-health"], [], "an unauthenticated read never reaches the remote")


class Series(unittest.TestCase):
    """api_health_series: per-minute bins over the slow window, additive to the payload."""

    class E:
        def __init__(self, t, kind, cls="", status=None):
            self.t, self.kind, self.cls, self.status, self.category = t, kind, cls, status, None
            self.auth, self.family, self.sid, self.turn = "key:helper", "fable", "s", "t"

    def test_bins_are_oldest_first_with_the_last_ending_at_now_and_the_classes_land_in_their_arrays(self):
        now = 10000.0
        E = self.E
        evs = [E(now - 5, "ok"), E(now - 30, "retry", "429"), E(now - 61, "retry", "5xx"), E(now - 62, "retry", "529"),
               E(now - 899, "retry", "none"), E(now - 901, "ok"), E(now - 100, "gaveup", "other")]
        s = sb.api_health_series(evs, now, 900)
        self.assertEqual((s["binS"], len(s["ok"])), (60, 15))
        self.assertEqual(s["from"], now - 900)
        self.assertEqual(s["ok"][-1], 1, "the newest bin holds the event 5 s ago")
        self.assertEqual(s["rateLimited"][-1], 1)
        self.assertEqual(s["serverErrors"][-2], 2, "529 and 5xx both count as server errors, in the bin 61-62 s back")
        self.assertEqual(s["noStatus"][0], 1, "the oldest bin holds the event 899 s back")
        self.assertEqual(sum(s["ok"]), 1, "an event older than the window is outside every bin")
        self.assertEqual(s["other"][-2], 1)

    def test_the_snapshot_carries_it_per_bucket_and_the_documented_keys_hold(self):
        src = Path(ROOT, "kernel", "sdk_backend.py").read_text()
        self.assertIn('"lastError": last_err, "series": series}', src)
        doc = Path(ROOT, "docs", "reference.md").read_text()
        self.assertIn("`series`", doc, "the reference names the additive field")


class CellCss(unittest.TestCase):
    """The rail cell (T301): a dot alone, three states through tokens, no second API word and no ok text."""

    def test_the_dot_s_three_states_wear_the_tokens_and_the_cell_carries_no_text(self):
        html = km._landing()
        html = html if isinstance(html, str) else html.decode("utf-8")
        self.assertIn("#rail-api[data-dot=fine] .ah-dot,.ah-dot[data-dot=fine]{background:var(--accent,#9cd2ff);opacity:1}", html)
        self.assertIn("#rail-api[data-dot=errors] .ah-dot,.ah-dot[data-dot=errors]{background:var(--st-blocked-bg,#e5484d);opacity:1}", html)
        self.assertIn("#rail-api[data-dot=quiet] .ah-dot,.ah-dot[data-dot=quiet]{background:var(--dim,#9aa4ad);opacity:.55}", html)
        self.assertNotIn(".ah-text", html, "no word beside the dot")
        self.assertNotIn('<span class=ru-name>API</span>', html.split("id=rail-api")[1][:200], "no second API label: the readout's own is the label")
        self.assertIn('<div id=rail-api class="ru-w ru-ah" hidden role=button tabindex=0 aria-label="API health" data-dot=fine><i class=ah-dot></i></div>', html)
        # the readout's slot the dot moves into, right after its API label
        self.assertIn("<div class=ru-name>API</div><span class=ah-slot></span>", html)
        # the dot lives inside #rail-usage while the readout renders, and that cell's innerHTML is rewritten on every
        # pull: the usage script parks the dot outside before any write and moves it into the fresh slot after
        usage = km._LANDING_USAGE_JS
        self.assertIn("function renderRows(rows,selfHost){ROWS=rows||[];LAST=[];parkApiCell();", usage)
        self.assertIn("function parkApiCell(){var c=document.getElementById('rail-api');if(c&&el.contains(c)&&RAIL_HOME)RAIL_HOME.insertBefore(c,el.nextSibling);}", usage)
        self.assertIn("var slot=el.querySelector('.ah-slot');\nif(slot){slot.appendChild(cell);}", usage)
        self.assertIn("body.theme-light #rail-api[data-dot=quiet] .ah-dot,body.theme-light .ah-dot[data-dot=quiet]{background:#5D574E}", html)
        self.assertIn("/dist/api-health-global.js?v=", html)

    def test_the_shell_script_reads_every_connected_kernel_and_never_says_unknown(self):
        js = km._LANDING_APIH_JS
        self.assertIn("fetchDoc('/remote/'+encodeURIComponent(h)+'/api-health')", js)
        self.assertIn("MERGE.mergeFrames(LAST,LAST.hosts||{},READINGS)", js)
        self.assertIn("MERGE.readHistory(d)", js)
        self.assertNotIn("'unknown'", js.replace("unknown:'quiet'", ""), "the word appears only as the key the plain word replaces")
        self.assertNotIn("API · this machine", js)
        self.assertIn("var HIST_ROWS=4;", js)
        self.assertIn("429 = the API told us to slow down (rate limit)", js)


if __name__ == "__main__":
    unittest.main()
