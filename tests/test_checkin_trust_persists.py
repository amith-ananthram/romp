#!/usr/bin/env python3
"""A check-in handshake must not undo the trust you set (the user 2026-07-29).

Symptom: a remote was set to trusted, over and over, and kept coming back as directed — its mail
quarantining again minutes later. The setting was not being forgotten by the store; it was being
OVERWRITTEN. checkin_apply rebuilt the peer's row from scratch on every handshake with a hardcoded
"trust": "directed", and the handshake repeats once per tunnel INCARNATION: every reconnect, tunnel
respawn and kernel restart on the checking-in machine.

The mismatch is invisible from the sending end, which is why it read as a store that forgets: the level
a peer DECLARES comes from its own row, so the sender kept displaying "they hold yours: trusted" while
the receiver was quarantining. A re-check-in is the same relationship reconnecting, not a new one.

CheckinHostChecked / CheckinRoute (2026-09-08): the same handshake took the peer's DECLARED name as it
came — any length, any characters — and that string went on to key the registry, remotes.json, the
remembered-hosts file, the bus's peer table (and the mail it holds for that peer, a path component
there), the /remote/<host>/ routes and every row action's body lookup. Every name a romp mobile
declares clears _safe_id — _self_host()'s derived names always did, and its ROMP_HOST_NAME override
now does or is set aside aloud (test_postal_self_host.py) — so the hub refuses anything else at the
door with a 400 that says what a name may look like, recording, saving, popping and waking nothing,
and turns away only names no romp mobile produces.

The review of that door (2026-09-08) found the rest of the claim undelivered, and these classes pin the
fixes: TrustDoorHostChecked, the SECOND door into the remembered-hosts registry (POST /tunnels/trust on a
name with no tunnel here filed any string and told the bus); RegistriesLoadChecked, rows already on disk
from before the doors were guarded (read back verbatim at every boot, told to the bus, rendered: now set
aside, never loaded, never written back); RefusalSaidOnBothMachines, the hub says a refusal once per
distinct value with a clipped repr and a bell row, and the mobile reads the reason, says it once, and
stops re-sending the same name until the name or the hub changes; and a trailing newline, which the
rule's `$` anchor admitted, is not a hostname.

Synthetic only — placeholder hosts/ports/tokens, hermetic temp STATE, no ssh.
"""
import contextlib
import io
import json
import os
import socket as _socket
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_citrust", os.path.join(BIN, "romp-kernel"))

BODY = {"host": "TESTHOST", "kernelPort": 29855, "busPort": 25302, "token": "peertok"}


class CheckinTrust(unittest.TestCase):
    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()

    def tearDown(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()

    def test_a_first_checkin_is_directed_the_safe_default(self):
        payload, status = km.checkin_apply(dict(BODY))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "directed")

    def test_a_LEVEL_YOU_SET_survives_the_next_handshake(self):
        # this is the bug: the mobile reconnects (or its kernel restarts) and hands in the same details
        km.checkin_apply(dict(BODY))
        km.set_trust("TESTHOST", "trusted")
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted")
        km.checkin_apply(dict(BODY))
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted",
                         "a reconnect must not silently re-gate a host you trusted")

    def test_isolated_survives_too_the_refusal_is_a_boundary(self):
        # an isolation refusal is the user's boundary; a reconnect re-opening it would be worse than
        # the directed case, since isolation means no postal contact at all
        km.checkin_apply(dict(BODY))
        km.set_trust("TESTHOST", "isolated")
        km.checkin_apply(dict(BODY))
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "isolated")

    def test_the_level_is_remembered_so_it_survives_a_kernel_restart_too(self):
        km.checkin_apply(dict(BODY))
        km.set_trust("TESTHOST", "trusted")
        self.assertEqual(km.known_trust("TESTHOST"), "trusted", "the remembered entry tracks the choice")
        # a restart loses _remotes' live rows; the next handshake rebuilds from what was remembered
        km._remotes.clear()
        km.checkin_apply(dict(BODY))
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted")

    def test_a_checkin_under_a_NEW_name_carries_nothing_over(self):
        # the same mobile re-checking in as another name is a different key: it must not inherit a level
        # chosen for the old one, since trust is judged by origin name at the gate
        km.checkin_apply(dict(BODY))
        km.set_trust("TESTHOST", "trusted")
        km.checkin_apply(dict(BODY, host="OTHERHOST"))
        self.assertEqual(km._remotes["OTHERHOST"]["trust"], "directed")

    def test_an_ssh_attached_row_of_the_same_name_is_still_refused(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "trust": "trusted", "checkin_peer": False}
        payload, status = km.checkin_apply(dict(BODY))
        self.assertEqual(status, 409)
        self.assertFalse(payload["ok"])
        self.assertEqual(km._remotes["TESTHOST"]["trust"], "trusted", "the ssh row is untouched")


# What the door must say when it refuses a name — the person's words, naming the shape a host may take.
# A KEY PHRASE, not the whole sentence (review find, 2026-09-08): ten tests pinned the full wording by
# equality, so the sentence could never be improved without editing every one of them.
RULE_KEY = "must be a machine name"

# Names every consumer downstream would choke on, or silently mis-key. Each is a path/URL/JSON hazard a
# hostile or merely broken peer could declare; none is anything _self_host() can produce.
BAD_HOSTS = {
    "a slash": "mobile/hub", "a slash with dot-dot": "mobile/../hub",
    "whitespace inside": "my host", "a tab": "host\tname", "a newline": "host\nname",
    "a NUL": "host\x00name", "a control char": "host\x1bname",
    "just over the limit": "a" * 129, "ten kilobytes": "b" * 10_000,
    "a leading dot": ".hidden", "dot-dot": "..", "a lone dot": ".",
    "a leading hyphen": "-oProxyCommand=x", "a quote": 'host"name', "a backslash": "host\\name",
    "a colon": "host:22", "an at-sign": "user@host", "a percent": "host%2Fname", "a query": "host?x=1",
}

# Names machines really declare, and every fixture name the other check-in tests use. A dotted FQDN
# with hyphens, an underscore (an ssh alias or a ROMP_HOST_NAME override may carry one), a minted
# last-resort id, and single-character or digit-led labels all pass.
GOOD_HOSTS = ("TESTHOST", "OTHERHOST", "mobile1", "hub", "laptop", "build-box-01.example.com",
              "my_box", "host-1a2b3c4d", "a", "9box", "a" * 128)


class CheckinHostChecked(unittest.TestCase):
    """A check-in is refused when the declared host is not a hostname, and nothing is recorded."""

    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        self.saves, self.notes = [], []
        self._saved = (km._remotes_save, km._known_note)
        km._remotes_save = lambda: self.saves.append(1)
        km._known_note = lambda *a, **k: self.notes.append((a, k))
        km._tunnel_wake.clear()
        self.disk = self._disk()

    def tearDown(self):
        km._remotes_save, km._known_note = self._saved
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()

    @staticmethod
    def _disk():
        # the two persisted registries, as bytes (None = absent), so "nothing saved" is checked on disk too
        out = []
        for f in (km.REMOTES_FILE, km.KNOWN_FILE):
            try:
                out.append(f.read_bytes())
            except OSError:
                out.append(None)
        return out

    def _refused(self, why, host):
        payload, status = km.checkin_apply(dict(BODY, host=host))
        self.assertEqual(status, 400, why)
        self.assertIs(payload["ok"], False, why)
        self.assertIn(RULE_KEY, payload["error"], why)
        if len(host) > 3:
            self.assertNotIn(host, payload["error"], "the refused string is not echoed back (%s)" % why)
        self.assertEqual(km._remotes, {}, "nothing filed in the registry (%s)" % why)
        with km._known_lock:
            self.assertEqual(km._known, {}, "nothing remembered (%s)" % why)
        self.assertEqual(self.saves, [], "remotes.json not written (%s)" % why)
        self.assertEqual(self.notes, [], "no remembered-hosts entry (%s)" % why)
        self.assertEqual(self._disk(), self.disk, "neither state file changed (%s)" % why)
        self.assertFalse(km._tunnel_wake.is_set(), "the supervisor is not woken for nothing (%s)" % why)

    def test_a_slash_is_refused_and_nothing_is_recorded(self):
        for why in ("a slash", "a slash with dot-dot"):
            self._refused(why, BAD_HOSTS[why])

    def test_whitespace_inside_is_refused(self):
        for why in ("whitespace inside", "a tab", "a newline"):
            self._refused(why, BAD_HOSTS[why])

    def test_a_control_char_or_nul_is_refused(self):
        for why in ("a NUL", "a control char"):
            self._refused(why, BAD_HOSTS[why])

    def test_an_over_long_name_is_refused(self):
        for why in ("just over the limit", "ten kilobytes"):
            self._refused(why, BAD_HOSTS[why])

    def test_a_leading_dot_or_dot_dot_is_refused(self):
        for why in ("a leading dot", "dot-dot", "a lone dot"):
            self._refused(why, BAD_HOSTS[why])

    def test_every_other_hazard_is_refused_too(self):
        for why, host in BAD_HOSTS.items():
            self._refused(why, host)

    def test_a_non_string_host_is_refused(self):
        # str() used to coerce these into names — a JSON 1.5 became the host "1.5" and was filed
        for host in (["mobile"], {"host": "mobile"}, 1.5, 5, True, None):
            payload, status = km.checkin_apply(dict(BODY, host=host))
            self.assertEqual(status, 400, repr(host))
            self.assertIs(payload["ok"], False, repr(host))
            self.assertEqual(payload["error"], "host, kernelPort, busPort required", repr(host))
        self.assertEqual((km._remotes, self.saves, self.notes), ({}, [], []))
        self.assertFalse(km._tunnel_wake.is_set())

    def test_a_junk_name_with_a_known_token_pops_nothing(self):
        # the same-token sweep ("this mobile re-checked in under a new name") ran BEFORE anything looked
        # at the name, so a junk re-check-in would have dropped the mobile's good row on its way in
        km.checkin_apply(dict(BODY))
        self.assertEqual(set(km._remotes), {"TESTHOST"})
        self.saves.clear(), self.notes.clear()
        km._tunnel_wake.clear()
        payload, status = km.checkin_apply(dict(BODY, host=BAD_HOSTS["a slash with dot-dot"]))
        self.assertEqual((status, payload["ok"]), (400, False))
        self.assertIn(RULE_KEY, payload["error"])
        self.assertEqual(set(km._remotes), {"TESTHOST"}, "the good row is untouched")
        self.assertEqual(km._remotes["TESTHOST"]["token"], "peertok")
        self.assertEqual((self.saves, self.notes), ([], []))
        self.assertFalse(km._tunnel_wake.is_set())

    def test_the_names_machines_declare_still_land(self):
        for h in GOOD_HOSTS:
            payload, status = km.checkin_apply(dict(BODY, host=h, token="tok-" + h))
            self.assertEqual(status, 200, h)
            self.assertIs(payload["ok"], True, h)
            self.assertEqual(payload["host"], h)
            self.assertTrue(km._remotes[h]["checkin_peer"], h)
            self.assertEqual(km._remotes[h]["host"], h)
        self.assertEqual(len(self.saves), len(GOOD_HOSTS), "each landing is persisted, as before")
        self.assertEqual([a[0] for a, k in self.notes], list(GOOD_HOSTS), "…and remembered, as before")
        self.assertTrue(km._tunnel_wake.is_set())

    def test_this_machines_own_declared_name_clears_the_rule(self):
        # what a real mobile sends IS _self_host(); every shape it can produce must land. The hostname
        # source is STUBBED (review find, 2026-09-08: read live, this could not fail on a healthy box and
        # ran scutil on macOS): a short name, a dotted one, and a junk one that sends _self_host to its
        # fallbacks (no platform name here, so the minted id), with the override out of the way.
        saved_env = os.environ.pop("ROMP_HOST_NAME", None)
        saved = (_socket.gethostname, km._host_name_candidates, km._self_host_fb)
        km._host_name_candidates = lambda: []
        try:
            for raw, expect in (("TESTHOST", "TESTHOST"), ("build-box-01.example.com", "build-box-01"),
                                ("TEST\x04HOST", None)):
                _socket.gethostname = lambda raw=raw: raw
                km._self_host_fb = None
                with contextlib.redirect_stderr(io.StringIO()):
                    me = km._self_host()
                if expect is not None:
                    self.assertEqual(me, expect, repr(raw))
                self.assertTrue(km._safe_id(me), "the rule IS the promise _self_host makes: %r" % raw)
                payload, status = km.checkin_apply(dict(BODY, host=me, token="t-" + me))
                self.assertEqual(status, 200, "a real machine's own name must never be refused: %r" % raw)
                self.assertIn(me, km._remotes)
        finally:
            _socket.gethostname, km._host_name_candidates, km._self_host_fb = saved
            if saved_env is not None:
                os.environ["ROMP_HOST_NAME"] = saved_env
        # …and the two fallbacks _self_host reaches for when the kernel hostname fails path-safety
        for h in (km._sanitize_host_name("Some Body's Mac (2)"), "host-%08x" % 0xDEADBEEF):
            self.assertTrue(h and km._safe_id(h), h)
            self.assertEqual(km.checkin_apply(dict(BODY, host=h, token="t-" + h))[1], 200, h)

    def test_a_trailing_newline_is_not_part_of_a_name(self):
        # the rule's regex ended in `$`, which also matches before ONE trailing newline, so "TESTHOST\n"
        # cleared _safe_id (review find, 2026-09-08). The door trims whitespace at the edges like every
        # route, so the name lands as "TESTHOST": no key with a newline in it is ever filed
        self.assertFalse(km._safe_id("TESTHOST\n"), "a trailing newline is not a hostname")
        self.assertFalse(km._safe_id("TESTHOST\r\n"))
        payload, status = km.checkin_apply(dict(BODY, host="TESTHOST\n"))
        self.assertEqual((status, payload["host"]), (200, "TESTHOST"))
        self.assertEqual(set(km._remotes), {"TESTHOST"})


class CheckinRoute(unittest.TestCase):
    """The same refusal through the door the mobile actually knocks on: POST /checkin on the real Handler
    (the test_tag_route.py harness pattern)."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        self.saves, self.notes = [], []
        self._saved = (km._remotes_save, km._known_note)
        km._remotes_save = lambda: self.saves.append(1)
        km._known_note = lambda *a, **k: self.notes.append((a, k))

    def tearDown(self):
        km._remotes_save, km._known_note = self._saved
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/checkin" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_over_http_a_junk_name_is_a_400_and_a_real_one_lands(self):
        for why in ("a slash with dot-dot", "whitespace inside", "a NUL", "ten kilobytes", "a leading dot"):
            st, r = self._post(dict(BODY, host=BAD_HOSTS[why]))
            self.assertEqual(st, 400, why)
            self.assertIs(r.get("ok"), False, why)
            self.assertIn(RULE_KEY, r.get("error") or "", why)
        self.assertEqual((km._remotes, self.saves, self.notes), ({}, [], []), "nothing recorded over HTTP either")
        st, r = self._post(dict(BODY, host="build-box-01.example.com"))
        self.assertEqual(st, 200)
        self.assertEqual((r["ok"], r["host"]), (True, "build-box-01.example.com"))
        self.assertTrue(km._remotes["build-box-01.example.com"]["checkin_peer"])
        self.assertEqual((len(self.saves), len(self.notes)), (1, 1))


def _serve(cls):
    """A real Handler on a loopback port, the door a peer or a dashboard actually knocks on."""
    cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
    cls.port = cls.srv.server_address[1]
    threading.Thread(target=cls.srv.serve_forever, daemon=True).start()


class TrustDoorHostChecked(unittest.TestCase):
    """The SECOND door into the remembered-hosts registry (review find, 2026-09-08): POST /tunnels/trust on
    a name with no tunnel here files it as an origin-only remembered entry and tells the bus, and it took
    any string the serve token's holder sent; every checked-in mobile holds that token. The name must now
    clear one of the two rules the registry's writers apply: the machine-name rule (a peer's own declared
    name) or the ssh-alias rule (the alias a hub attached a peer by, which remote_trust carries here and
    which stamps that peer's relayed mail as its origin), so a slash, whitespace, a NUL, a quote or ten
    kilobytes is refused, nothing is recorded, and user@box still lands."""

    @classmethod
    def setUpClass(cls):
        _serve(cls)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        self.pushed, self.saves = [], []
        self._saved = (km._notify_bus_origin_trust, km._known_save)
        km._notify_bus_origin_trust = lambda h, t: self.pushed.append((h, t)) or True
        km._known_save = lambda: self.saves.append(1)
        km._host_refused_said.clear()
        km._tunnel_wake.clear()

    def tearDown(self):
        km._notify_bus_origin_trust, km._known_save = self._saved
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()

    def test_a_junk_name_is_refused_and_nothing_is_recorded(self):
        # every shape NEITHER rule admits; "a leading dot", "an at-sign" and "a colon" are ssh-alias shapes
        # (attach files them), so they are the landing cases below, not refusals here
        seq0 = km._sync_notice_count()
        err_out = io.StringIO()
        with contextlib.redirect_stderr(err_out):
            for why in ("a slash", "a slash with dot-dot", "whitespace inside", "a tab", "a newline", "a NUL",
                        "a control char", "ten kilobytes", "a leading hyphen", "a quote", "a backslash",
                        "a percent", "a query"):
                host = BAD_HOSTS[why]
                pub, err = km.set_trust(host, "trusted")
                self.assertIsNone(pub, why)
                self.assertIn(RULE_KEY, err or "", why)
                self.assertIn("ssh alias", err, "this door's sentence names the wider rule it holds (%s)" % why)
                if len(host) > 3:
                    self.assertNotIn(host, err, "the refused string is not echoed back (%s)" % why)
        with km._known_lock:
            self.assertEqual(km._known, {}, "nothing remembered")
        self.assertEqual((self.pushed, self.saves), ([], []), "the bus is not told, the file is not written")
        self.assertFalse(km._tunnel_wake.is_set())
        # said on this machine: one stderr line and one bell row per distinct value, naming the door
        lines = [l for l in err_out.getvalue().splitlines() if "trust route refused" in l]
        self.assertEqual(len(lines), 13, err_out.getvalue())
        self.assertNotIn("b" * 10_000, err_out.getvalue(), "ten kilobytes never reach the log")
        self.assertTrue(all("ssh alias" in l for l in lines))
        rows = km._sync_notice_rows()[-(km._sync_notice_count() - seq0):]
        self.assertEqual(len(rows), 13)
        self.assertTrue(all((r["kind"], r["ok"]) == ("refused", False) for r in rows))

    def test_a_good_unattached_name_still_files_an_origin_only_row(self):
        pub, err = km.set_trust("FARBOX", "trusted")
        self.assertIsNone(err)
        self.assertEqual(pub, {"host": "FARBOX", "trust": "trusted", "originOnly": True})
        self.assertEqual(km.known_trust("FARBOX"), "trusted")
        self.assertEqual(self.pushed, [("FARBOX", "trusted")])

    def test_an_ssh_alias_a_hub_holds_a_peer_under_lands_unremembered(self):
        # the relay case: a hub attached a peer as user@box (the ssh rule admits it, _safe_id does not), its
        # bus stamps that peer's relayed mail with that alias as origin, and remote_trust carries the alias
        # to THIS machine's trust route the first time the user tiers the pair, so it must land here with
        # nothing remembered beforehand; a bracketed address is the other alias shape attach admits
        for alias in ("user@build-box", "[fe80::1]"):
            pub, err = km.set_trust(alias, "isolated")
            self.assertIsNone(err, alias)
            self.assertEqual(pub, {"host": alias, "trust": "isolated", "originOnly": True})
            self.assertEqual(km.known_trust(alias), "isolated")
        self.assertEqual(self.pushed, [("user@build-box", "isolated"), ("[fe80::1]", "isolated")])
        # ...and the same alias is what the popover re-tiers later, remembered now
        pub, err = km.set_trust("user@build-box", "trusted")
        self.assertEqual((err, pub["trust"], km.known_trust("user@build-box")), (None, "trusted", "trusted"))

    def test_over_http_a_junk_name_is_a_400_that_echoes_nothing(self):
        def post(body):
            req = urllib.request.Request(
                "http://127.0.0.1:%d/tunnels/trust" % self.port, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode() or "{}")
        with contextlib.redirect_stderr(io.StringIO()):
            for why in ("a slash with dot-dot", "whitespace inside", "ten kilobytes"):
                st, r = post({"host": BAD_HOSTS[why], "trust": "trusted"})
                self.assertEqual(st, 400, why)
                self.assertIs(r.get("ok"), False, why)
                self.assertIn(RULE_KEY, r.get("error") or "", why)
                self.assertNotIn(BAD_HOSTS[why], json.dumps(r), why)
        with km._known_lock:
            self.assertEqual(km._known, {})
        self.assertEqual(self.pushed, [])
        st, r = post({"host": "FARBOX", "trust": "isolated"})
        self.assertEqual((st, r.get("ok")), (200, True))
        self.assertEqual(self.pushed, [("FARBOX", "isolated")])


class RegistriesLoadChecked(unittest.TestCase):
    """Rows already on disk from before the doors were guarded (review find, 2026-09-08): _remotes_load and
    _known_load restored any row with a truthy host, so a junk key filed by a pre-fix hub came back at every
    boot, was told to the bus, and rendered in the panel. A row whose host fails the rule its own door
    applies (check-in rows: _safe_id; ssh rows: _safe_ssh_host; remembered rows: either) is now set aside
    to a sidecar beside the file, never loaded, never written back, said once with a clipped repr, and
    filed as one bell row of kind refused."""

    JUNK = "mobile/../hub name\x00"      # a path-traversal shape, with whitespace and a NUL

    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        km._origin_trust_pushed.clear()
        km._host_refused_said.clear()
        self.pushed = []
        self._saved = (km._notify_bus_origin_trust, km.REMOTES_FILE, km.KNOWN_FILE)
        km._notify_bus_origin_trust = lambda h, t: self.pushed.append((h, t)) or True
        self.td = tempfile.TemporaryDirectory()
        km.REMOTES_FILE = Path(self.td.name) / "remotes.json"
        km.KNOWN_FILE = Path(self.td.name) / "remotes-known.json"

    def tearDown(self):
        km._notify_bus_origin_trust, km.REMOTES_FILE, km.KNOWN_FILE = self._saved
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        self.td.cleanup()

    @staticmethod
    def _row(host, checkin=True, token="tok"):
        return {"host": host, "checkin_peer": checkin, "kernel_port": 29855, "local_port": 29855,
                "bus_port": 25302, "token": token, "trust": "directed", "sids": [], "status": "up"}

    def _plant(self):
        km.REMOTES_FILE.write_text(json.dumps([self._row(self.JUNK, token="junk-tok"), self._row("TESTHOST"),
                                               self._row("user@build-box", checkin=False)]))
        os.chmod(km.REMOTES_FILE, 0o600)
        km.KNOWN_FILE.write_text(json.dumps([{"host": h, "lastAttachedAt": 1, "trust": "trusted", "share": False,
                                              "attached": True} for h in (self.JUNK, "TESTHOST", "user@build-box")]))

    def test_a_junk_row_is_set_aside_and_reaches_neither_registry_nor_bus_nor_panel(self):
        self._plant()
        seq0 = km._sync_notice_count()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._known_load()
            km._remotes_load()
        # the registries hold the rows their doors would have filed, and nothing else
        self.assertEqual(set(km._remotes), {"TESTHOST", "user@build-box"})
        with km._known_lock:
            self.assertEqual(set(km._known), {"TESTHOST", "user@build-box"})
        # rendered rows: what GET /tunnels and the popover show
        hosts = [t["host"] for t in km.list_remotes()] + [k["host"] for k in km.list_known()]
        self.assertNotIn(self.JUNK, hosts)
        self.assertEqual(sorted(hosts), ["TESTHOST", "user@build-box"])
        # the bus: the origin-trust push walks every remembered-but-unattached row; the junk one is not there
        km._remotes.clear()
        km._push_origin_trust_rows()
        self.assertEqual(sorted(h for h, t in self.pushed), ["TESTHOST", "user@build-box"])
        # disk: neither file carries the row any more (never written back)…
        for f in (km.REMOTES_FILE, km.KNOWN_FILE):
            self.assertNotIn("mobile/../hub", f.read_text(), f.name)
        self.assertEqual({r["host"] for r in json.loads(km.REMOTES_FILE.read_text())}, {"TESTHOST", "user@build-box"})
        self.assertEqual({r["host"] for r in json.loads(km.KNOWN_FILE.read_text())}, {"TESTHOST", "user@build-box"})
        # …and each sits in a sidecar beside the file it came from, the remotes one 0600 (it carries a token)
        aside = sorted(Path(self.td.name).glob("remotes.json.refused-*"))
        self.assertEqual(len(aside), 1, aside)
        self.assertEqual([r["host"] for r in json.loads(aside[0].read_text())], [self.JUNK])
        self.assertEqual(json.loads(aside[0].read_text())[0]["token"], "junk-tok", "the evidence survives whole")
        self.assertEqual(stat.S_IMODE(aside[0].stat().st_mode), 0o600)
        kaside = sorted(Path(self.td.name).glob("remotes-known.json.refused-*"))
        self.assertEqual(len(kaside), 1, kaside)
        self.assertEqual([r["host"] for r in json.loads(kaside[0].read_text())], [self.JUNK])
        # said once per file, with a clipped repr and never the raw string
        lines = [l for l in err.getvalue().splitlines() if "refused" in l]
        self.assertEqual(len(lines), 2, err.getvalue())
        self.assertTrue(any("remotes.json" in l for l in lines) and any("remotes-known.json" in l for l in lines))
        for l in lines:
            self.assertNotIn(self.JUNK, l, "the raw string (a NUL in it) never reaches a log")
            self.assertIn(repr(self.JUNK), l, "its repr does")
            self.assertLess(len(l), 400)
        # …and as bell rows of kind refused
        rows = km._sync_notice_rows()[-(km._sync_notice_count() - seq0):]
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual((r["kind"], r["ok"]), ("refused", False))
            self.assertNotIn(self.JUNK, r["text"])
        # a second boot finds clean files: nothing more to say
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            km._known_load()
            km._remotes_load()
        self.assertEqual(err2.getvalue(), "")
        self.assertEqual(km._sync_notice_count() - seq0, 2)
        self.assertEqual(len(list(Path(self.td.name).glob("*.refused-*"))), 2, "no second sidecar")

    def test_an_over_long_or_non_string_host_is_set_aside_too(self):
        # ...and an ssh row whose alias ends in a newline: the ssh rule's `$` admitted it as _safe_id's did
        # (review find, 2026-09-08), and nothing strips a row read from disk
        self.assertFalse(km._safe_ssh_host("user@build-box\n"), "a trailing newline is not an ssh alias")
        km.REMOTES_FILE.write_text(json.dumps([self._row("a" * 129), dict(self._row("x"), host=5),
                                               self._row("-oProxyCommand=x", checkin=False),
                                               self._row("user@build-box\n", checkin=False), self._row("TESTHOST")]))
        with contextlib.redirect_stderr(io.StringIO()):
            km._remotes_load()
        self.assertEqual(set(km._remotes), {"TESTHOST"})
        self.assertEqual([r["host"] for r in json.loads(km.REMOTES_FILE.read_text())], ["TESTHOST"])
        aside = sorted(Path(self.td.name).glob("remotes.json.refused-*"))
        self.assertEqual(len(aside), 1)
        self.assertEqual(len(json.loads(aside[0].read_text())), 4, "every refused row is in the sidecar")

    def test_clean_files_load_as_before_and_are_not_rewritten(self):
        km.REMOTES_FILE.write_text(json.dumps([self._row("TESTHOST"), self._row("user@build-box", checkin=False)]))
        km.KNOWN_FILE.write_text(json.dumps([{"host": "TESTHOST", "lastAttachedAt": 1, "trust": "trusted"}]))
        before = (km.REMOTES_FILE.read_bytes(), km.KNOWN_FILE.read_bytes())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._known_load()
            km._remotes_load()
        self.assertEqual(set(km._remotes), {"TESTHOST", "user@build-box"})
        self.assertEqual(km.known_trust("TESTHOST"), "trusted")
        self.assertEqual((km.REMOTES_FILE.read_bytes(), km.KNOWN_FILE.read_bytes()), before)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(list(Path(self.td.name).glob("*.refused-*")), [])


class RefusalSaidOnBothMachines(unittest.TestCase):
    """A refused check-in was recorded nowhere (review find, 2026-09-08): the hub answered 400 with a reason
    and logged nothing, and the mobile read only the status and re-sent the same name every supervisor pass
    forever. Hub side: one stderr line per distinct offending value (a clipped repr, never the raw string)
    and one bell row of kind refused. Mobile side: _checkin_handshake reads the reason, says it once per
    distinct reason (stderr, the dial log, the bell), and does not re-send the same declared name until the
    name or the hub's kernel incarnation changes; a hub that is simply unreachable keeps retrying quietly."""

    @classmethod
    def setUpClass(cls):
        _serve(cls)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        km._host_refused_said.clear()
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.TUNNEL_LOG, km._checkin_payload, km.checkin_apply, km._remotes_save, km._known_note)
        km.TUNNEL_LOG = Path(self.td.name) / "tunnels.jsonl"
        km._remotes_save = lambda: None
        km._known_note = lambda *a, **k: None
        self.asked = []
        real = self._saved[2]
        km.checkin_apply = lambda body: (self.asked.append(body.get("host")), real(body))[1]   # the Handler
        #                                                                          resolves the name at call time

    def tearDown(self):
        km.TUNNEL_LOG, km._checkin_payload, km.checkin_apply, km._remotes_save, km._known_note = self._saved
        km._remotes.clear()
        with km._known_lock:
            km._known.clear()
        self.td.cleanup()

    def _row(self, hub_pid=4242):
        return {"host": "hub", "local_port": self.port, "token": os.environ["ROMP_SERVE_TOKEN"],
                "rk_port": 29855, "rb_port": 25302, "hub_pid": hub_pid, "status": "up", "detail": ""}

    def _dial_log(self):
        if not km.TUNNEL_LOG.exists():
            return []
        return [json.loads(x) for x in km.TUNNEL_LOG.read_text().splitlines() if x.strip()]

    def test_the_hub_says_a_refusal_once_per_value_with_a_clipped_repr(self):
        big, nul = "b" * 10_000, "mobile/../hub name\x00"
        seq0 = km._sync_notice_count()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for h in (big, big, nul, big, nul):
                self.assertEqual(km.checkin_apply(dict(BODY, host=h))[1], 400)
        lines = [l for l in err.getvalue().splitlines() if "check-in" in l and "refused" in l]
        self.assertEqual(len(lines), 2, "once per distinct value, not per attempt: %r" % err.getvalue())
        self.assertNotIn(big, err.getvalue(), "ten kilobytes never reach the log")
        self.assertNotIn(nul, err.getvalue(), "nor a raw NUL")
        for l in lines:
            self.assertLess(len(l), 400)
            self.assertIn("machine name", l)
        self.assertTrue(any(repr(nul) in l for l in lines), "the clipped repr names the value")
        self.assertTrue(any("10000 characters" in l for l in lines), "the long one says how long it was")
        rows = km._sync_notice_rows()[-(km._sync_notice_count() - seq0):]
        self.assertEqual(len(rows), 2, "one bell row per distinct value")
        for r in rows:
            self.assertEqual((r["kind"], r["ok"]), ("refused", False))
            self.assertNotIn(big, r["text"])
            self.assertIn("machine name", r["text"])

    def test_the_mobile_says_the_reason_once_and_stops_re_sending_that_name(self):
        declared = ["mobile/../hub"]
        km._checkin_payload = lambda r: dict(BODY, host=declared[0])
        r = self._row()
        seq0 = km._sync_notice_count()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            results = [km._checkin_handshake(r) for _ in range(3)]
        self.assertEqual(results, [False, False, False])
        self.assertEqual(self.asked, ["mobile/../hub"], "the hub was asked ONCE; the same name is not re-sent")
        lines = [l for l in err.getvalue().splitlines() if "refused by" in l]      # the mobile's line
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("hub", lines[0])
        self.assertIn(RULE_KEY, lines[0], "the hub's reason, read at last")
        recs = [x for x in self._dial_log() if x.get("event") == "checkin-refused"]
        self.assertEqual(len(recs), 1, recs)
        self.assertEqual((recs[0]["host"], recs[0]["status"]), ("hub", 400))
        self.assertIn(RULE_KEY, recs[0]["error"])
        rows = km._sync_notice_rows()[-(km._sync_notice_count() - seq0):]
        mine = [x for x in rows if "refused by" in x["text"]]
        self.assertEqual(len(mine), 1, rows)
        self.assertEqual((mine[0]["kind"], mine[0]["ok"]), ("refused", False))
        self.assertEqual(km._remotes, {}, "nothing landed on the hub")
        # a changed name is the event that re-arms the send: it goes out, lands, and the memo clears
        declared[0] = "mobile1"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(km._checkin_handshake(r))
        self.assertEqual(self.asked, ["mobile/../hub", "mobile1"])
        self.assertNotIn("_checkin_refused", r)
        self.assertIn("mobile1", km._remotes)

    def test_a_hub_kernel_restart_re_arms_the_send(self):
        km._checkin_payload = lambda r: dict(BODY, host="mobile/../hub")
        r = self._row(hub_pid=4242)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(km._checkin_handshake(r))
            self.assertFalse(km._checkin_handshake(r))
            r["hub_pid"] = 4243                    # the supervisor's /version poll saw a new incarnation
            self.assertFalse(km._checkin_handshake(r))
        self.assertEqual(len(self.asked), 2, "once per hub incarnation for an unchanged name")
        recs = [x for x in self._dial_log() if x.get("event") == "checkin-refused"]
        self.assertEqual(len(recs), 1, "the same reason is not said twice")

    def test_an_unreachable_hub_keeps_retrying_quietly(self):
        # a hub mid-boot answers nothing: that is not a refusal, so no memo, no line, and the next pass dials
        km._checkin_payload = lambda r: dict(BODY, host="mobile1")
        s = _socket.socket()
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
        s.close()
        r = dict(self._row(), local_port=closed)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual([km._checkin_handshake(r) for _ in range(2)], [False, False])
        self.assertNotIn("_checkin_refused", r)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self._dial_log(), [])

    def test_the_memo_never_reaches_disk_or_the_panel(self):
        # a boot is an event that re-arms the send, and the panel has no field for it
        self.assertIn("_checkin_refused", km._NOT_SAVED)
        r = dict(self._row(), _checkin_refused={"host": "x", "hub": 1, "status": 400, "error": "e", "hold": True},
                 kernel_port=29855, trust="directed", sids=[], checkin=True, checkin_peer=False)
        self.assertNotIn("_checkin_refused", km._remote_public(r))
        km._remotes["hub"] = r
        self.assertNotIn("_checkin_refused", json.dumps(km._remotes_rows_for_save()))


if __name__ == "__main__":
    unittest.main()
