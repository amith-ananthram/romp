#!/usr/bin/env python3
"""The self-host safety net: neither the bus nor the kernel may ever declare a machine name that
fails _safe_id. Peers key the mail they hold FOR this machine by that name, as a path component —
so a kern.hostname stomped with junk (control bytes, 2026-08-11) used to HALF-break peering:
presence still crossed (PEER_STATE is a dict), but outbox_put on every peer refused the name, so
each reply parked "unreachable" forever with only a server-log line as trace — and _unique() baked
the junk into message ids, killing outbound the same way. self_host (bus) and _self_host (kernel)
now validate and fall back loudly: the platform's user-set machine name, else a minted id persisted
at the state root's self-host file, SHARED between the two daemons so the machine can never appear
under two names. peer_exchange_handle refuses an unkeyable declared name (guarding against
un-updated dialers) — but only AFTER _canon_peer_name, so the checked-in-alias fold keeps
self-healing.

The override tests (2026-09-08): the kernel's ROMP_HOST_NAME override and the bus's ROMP_POSTAL_HOST
override were each the one branch that dodged the rule — returned exactly as set, directly under the
docstring saying the name MUST clear _safe_id — so a mobile whose operator set one to an unusable name
declared that name to every hub (which now refuses it at its check-in door, checkin_apply) while its
own mail parked unreachable. An unusable override is now set aside aloud, once per process, and the
derived name is used — on both daemons (KernelSelfHost and BusSelfHost).

SafeIdTwins (review find, 2026-09-08): the rule itself is duplicated in the two daemons, and its regex
ended in `$`, which in Python also matches before ONE trailing newline, so "TESTHOST\n" cleared it, and
the override branches on both daemons returned that name verbatim while every hub stripped it, leaving
the machine believing in a name no peer keyed. Both copies now fullmatch, both must stay the same
function, and a trailing newline is the one whitespace shape the junk-override cases now include.

Synthetic only — invented hostnames (TESTHOST, a control-byte junk form), hermetic temp state dir,
no real machine data.
"""
import ast
import contextlib
import inspect
import io
import os
import socket as _socket
import tempfile
import textwrap
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state dir BEFORE the loads: both daemons resolve their state root at import, and the
# minted-id file must land here — shared by the two module instances, never in real state.
# (ROMP_POSTAL_HOST / ROMP_HOST_NAME are deliberately NOT popped here: they are read at CALL time,
# other test modules set them at IMPORT time, and pytest imports every module before running any
# test — a module-level pop would erase theirs for the whole run. _HostnameSeams clears per test.)
STATE_HOME = os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
pm = load_source("romp_postal_selfhost", os.path.join(BIN, "romp-postal-service"))
km = load_source("romp_kernel_selfhost", os.path.join(BIN, "romp-kernel"))

JUNK = "TEST\x04HOST"          # a control byte mid-name, the 2026-08-11 shape — fails _safe_id


class _HostnameSeams(unittest.TestCase):
    """Shared seams: fake gethostname (the stdlib socket module is one object, shared by both
    loads), no platform name candidates unless a test supplies them, fallback caches reset — every
    test states its own hostname world and leaks nothing."""

    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ("ROMP_POSTAL_HOST", "ROMP_HOST_NAME")}
        self._gethostname = _socket.gethostname
        self._pc, self._kc = pm._host_name_candidates, km._host_name_candidates
        pm._host_name_candidates = km._host_name_candidates = lambda: []
        pm._self_host_fb = km._self_host_fb = None
        getattr(km, "_host_name_env_warned", set()).clear()   # the once-per-process override warnings, re-armed
        getattr(pm, "_postal_host_env_warned", set()).clear()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _socket.gethostname = self._gethostname
        pm._host_name_candidates, km._host_name_candidates = self._pc, self._kc
        pm._self_host_fb = km._self_host_fb = None
        getattr(km, "_host_name_env_warned", set()).clear()
        getattr(pm, "_postal_host_env_warned", set()).clear()


class BusSelfHost(_HostnameSeams):
    def test_safe_hostname_passes_through_live(self):
        _socket.gethostname = lambda: "TESTHOST.local"
        self.assertEqual(pm.self_host(), "TESTHOST")
        _socket.gethostname = lambda: "TESTHOST2"
        self.assertEqual(pm.self_host(), "TESTHOST2", "a hostname fix lands with no restart")
        self.assertIsNone(pm._self_host_fb, "no fallback engages for a healthy name")

    def test_junk_falls_back_to_the_platform_machine_name(self):
        _socket.gethostname = lambda: JUNK
        pm._host_name_candidates = lambda: ["Test Person's Laptop!!"]
        self.assertEqual(pm.self_host(), "Test-Person-s-Laptop", "sanitized, never raw")
        self.assertTrue(pm._safe_id(pm.self_host()))

    def test_junk_mints_a_stable_persisted_id(self):
        _socket.gethostname = lambda: JUNK
        a = pm.self_host()
        self.assertTrue(pm._safe_id(a))
        self.assertEqual(a, pm.self_host(), "cached within the process")
        # A fresh load of the same bin (= a restarted bus) must resolve the SAME identity: peers
        # key our mailbox by this name, so a per-process id would strand mail on every restart.
        # Pin XDG to THIS module's state dir for the load — a later-imported test module may have
        # re-pointed the env at its own tempdir during collection (imports all run before tests).
        saved = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = STATE_HOME
        try:
            pm2 = load_source("romp_postal_selfhost_reload",
                                   os.path.join(BIN, "romp-postal-service"))
        finally:
            os.environ["XDG_STATE_HOME"] = saved
        pm2._host_name_candidates = lambda: []
        pm2._self_host_fb = None
        self.assertEqual(pm2.self_host(), a, "the minted id is persisted, not per-process")

    def test_unique_mid_stays_path_safe(self):
        # Mids are path components at outbox_put on BOTH ends, so a junk hostname baked into them
        # killed outbound cross-host mail the same way the declared name killed inbound.
        _socket.gethostname = lambda: JUNK
        mid = pm._unique()
        self.assertTrue(pm._safe_id(mid), "the mid must survive the outbox path check")
        pm.outbox_put("TESTHOST", {"mid": mid, "body": "hi"})
        self.assertEqual((pm.outbox_get("TESTHOST", mid) or {}).get("body"), "hi")
        self.assertTrue(pm.outbox_del("TESTHOST", mid))

    def test_a_valid_override_of_any_real_shape_is_returned_verbatim(self):
        for name in ("TESTHOST", "build-box-01.example.com", "my_box", "host-1a2b3c4d"):
            os.environ["ROMP_POSTAL_HOST"] = name
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(pm.self_host(), name)
            self.assertEqual(err.getvalue(), "", "a usable override is taken quietly")

    def test_a_junk_override_is_set_aside_aloud_once_and_the_derived_name_is_used(self):
        # the bus's twin of the kernel gap: ROMP_POSTAL_HOST came back exactly as set, so a bus whose
        # operator set it to an unusable name declared that name in every peer exchange (2026-09-08)
        _socket.gethostname = lambda: "TESTHOST"
        for junk in ("my box", "user@host", "a" * 129, "TESTHOST2\n"):   # the trailing newline: review find, 2026-09-08
            os.environ["ROMP_POSTAL_HOST"] = junk
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                first, second = pm.self_host(), pm.self_host()
            self.assertEqual((first, second), ("TESTHOST", "TESTHOST"), repr(junk[:20]))
            self.assertTrue(pm._safe_id(first))
            lines = [l for l in err.getvalue().splitlines() if "ROMP_POSTAL_HOST" in l]
            self.assertEqual(len(lines), 1, "said once per process, not per call: %r" % err.getvalue())
            self.assertTrue(lines[0].startswith("[postal] self_host: "), "the bus's own log prefix")
            self.assertIn("not usable as a machine name", lines[0])
            self.assertIn("letters, digits, dots, hyphens or underscores", lines[0], "says what a name may look like")
            self.assertIn("declaring 'TESTHOST' to peers instead", lines[0], "names the name used instead")
            self.assertIn(repr(junk[:20])[:-1], lines[0], "and the name set aside (a long one is cut short)")

    def test_a_junk_override_with_a_junk_hostname_still_converges_with_the_kernel(self):
        os.environ["ROMP_POSTAL_HOST"] = "my box"
        _socket.gethostname = lambda: JUNK
        with contextlib.redirect_stderr(io.StringIO()):
            b = pm.self_host()
        self.assertTrue(pm._safe_id(b))
        self.assertNotIn(" ", b)
        self.assertEqual(b, km._self_host(), "bus and kernel still share the persisted identity")


class ExchangeUnkeyableHostGate(_HostnameSeams):
    """peer_exchange_handle refuses a declared name that fails _safe_id — an un-updated dialer
    could still declare one — but only AFTER _canon_peer_name, so the checked-in-alias fold (the
    existing self-heal for a bus we already peer with under a dialable name) keeps working."""

    def setUp(self):
        super().setUp()
        self._peers, self._pstate = dict(pm.PEERS), dict(pm.PEER_STATE)
        self._agents = pm.local_agents
        pm.local_agents = lambda threads=False: []          # presence enumeration is not under test; stay hermetic

    def tearDown(self):
        pm.local_agents = self._agents
        pm.PEERS.clear(); pm.PEERS.update(self._peers)
        pm.PEER_STATE.clear(); pm.PEER_STATE.update(self._pstate)
        super().tearDown()

    def test_unkeyable_declared_host_is_refused_400(self):
        payload, status = pm.peer_exchange_handle({"host": JUNK, "proto": pm.PEER_PROTO})
        self.assertEqual(status, 400, "refuse whole — half-working (presence without replies) is worse")
        self.assertIn("error", payload)
        self.assertNotIn(JUNK, pm.PEER_STATE, "no half-registered row for an unkeyable name")

    def test_canon_fold_to_checked_in_alias_still_accepted(self):
        pm.PEERS["safealias"] = {"port": 1}                       # dialable: the kernel notified this alias
        pm.PEER_STATE["safealias"] = {"busId": "bus-11111111"}    # same bus, seen before under the alias
        payload, status = pm.peer_exchange_handle(
            {"host": JUNK, "busId": "bus-11111111", "proto": pm.PEER_PROTO})
        self.assertEqual(status, 200, "the alias fold self-heals ahead of the gate")
        self.assertIn("safealias", pm.PEER_STATE)
        self.assertNotIn(JUNK, pm.PEER_STATE)


class KernelSelfHost(_HostnameSeams):
    def test_env_override_wins(self):
        os.environ["ROMP_HOST_NAME"] = "TESTHOST"
        try:
            self.assertEqual(km._self_host(), "TESTHOST")
        finally:
            os.environ.pop("ROMP_HOST_NAME", None)

    def test_safe_hostname_passes_through_live(self):
        _socket.gethostname = lambda: "TESTHOST"
        self.assertEqual(km._self_host(), "TESTHOST")
        self.assertIsNone(km._self_host_fb, "no fallback engages for a healthy name")

    def test_junk_is_never_declared_and_kernel_matches_bus(self):
        _socket.gethostname = lambda: JUNK
        k = km._self_host()
        self.assertTrue(km._safe_id(k))
        self.assertNotIn("\x04", k)
        self.assertEqual(k, pm.self_host(),
                         "kernel and bus share the persisted identity — one machine, one name")

    def test_a_valid_override_of_any_real_shape_is_returned_verbatim(self):
        # a dotted name with hyphens, an underscore, a minted-style id: what an operator might set
        for name in ("TESTHOST", "build-box-01.example.com", "my_box", "host-1a2b3c4d"):
            os.environ["ROMP_HOST_NAME"] = name
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(km._self_host(), name)
            self.assertEqual(err.getvalue(), "", "a usable override is taken quietly")

    def test_a_junk_override_is_set_aside_aloud_once_and_the_derived_name_is_used(self):
        # the one branch that dodged the rule: the override came back exactly as set, so a mobile whose
        # operator set ROMP_HOST_NAME to an unusable name declared it to every hub (2026-09-08)
        _socket.gethostname = lambda: "TESTHOST"
        for junk in ("my box", "user@host", "a" * 129, "TESTHOST2\n"):   # the trailing newline: review find, 2026-09-08
            os.environ["ROMP_HOST_NAME"] = junk
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                first, second = km._self_host(), km._self_host()
            self.assertEqual((first, second), ("TESTHOST", "TESTHOST"), repr(junk[:20]))
            self.assertTrue(km._safe_id(first))
            lines = [l for l in err.getvalue().splitlines() if "ROMP_HOST_NAME" in l]
            self.assertEqual(len(lines), 1, "said once per process, not per call: %r" % err.getvalue())
            self.assertIn("not usable as a machine name", lines[0])
            self.assertIn("letters, digits, dots, hyphens or underscores", lines[0], "says what a name may look like")
            self.assertIn("using 'TESTHOST' for peering instead", lines[0], "names the name used instead")
            self.assertIn(repr(junk[:20])[:-1], lines[0], "and the name set aside (a long one is cut short)")

    def test_a_junk_override_with_a_junk_hostname_still_converges_with_the_bus(self):
        os.environ["ROMP_HOST_NAME"] = "my box"
        _socket.gethostname = lambda: JUNK
        with contextlib.redirect_stderr(io.StringIO()):
            k = km._self_host()
        self.assertTrue(km._safe_id(k))
        self.assertNotIn(" ", k)
        self.assertEqual(k, pm.self_host(), "kernel and bus still share the persisted identity")


class SafeIdTwins(unittest.TestCase):
    """_safe_id lives in both daemons on purpose (each loads alone), so the two copies must stay ONE
    function; and a trailing newline is not a name on either (review find, 2026-09-08: `$` matched
    before it, and the override branches returned "TESTHOST\n" verbatim)."""

    @staticmethod
    def _body(fn):
        # the function minus its docstring: the two copies explain themselves differently, and may
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        f = tree.body[0]
        f.body = [n for n in f.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                            and isinstance(n.value.value, str))]
        return ast.dump(f)

    def test_the_two_copies_are_the_same_function(self):
        self.assertEqual(km._SAFE_ID_RE.pattern, pm._SAFE_ID_RE.pattern, "one regex on both daemons")
        self.assertEqual(self._body(km._safe_id), self._body(pm._safe_id), "one rule on both daemons")

    def test_a_trailing_newline_is_not_a_name_on_either_daemon(self):
        for s in ("TESTHOST\n", "abc\n", "a\n", "TESTHOST\r\n", "TESTHOST\n\n"):
            self.assertFalse(km._safe_id(s), repr(s))
            self.assertFalse(pm._safe_id(s), repr(s))
        for s in ("TESTHOST", "a", "A.B-c_2", "11111111-2222-3333-4444-555555555555", "a" * 128):
            self.assertTrue(km._safe_id(s), repr(s))
            self.assertTrue(pm._safe_id(s), repr(s))

    def test_an_override_with_a_trailing_newline_is_set_aside_on_both_daemons(self):
        # the ONE whitespace shape the rule admitted: returned verbatim by both override branches, then
        # stripped by every hub, so the machine's own name and the name peers keyed it by differed by a
        # newline, and the bus baked it into every message id
        saved = {k: os.environ.pop(k, None) for k in ("ROMP_POSTAL_HOST", "ROMP_HOST_NAME")}
        seams = (_socket.gethostname, pm._host_name_candidates, km._host_name_candidates,
                 pm._self_host_fb, km._self_host_fb)
        _socket.gethostname = lambda: "TESTHOST"
        pm._host_name_candidates = km._host_name_candidates = lambda: []
        pm._self_host_fb = km._self_host_fb = None
        getattr(km, "_host_name_env_warned", set()).clear()
        getattr(pm, "_postal_host_env_warned", set()).clear()
        try:
            os.environ["ROMP_HOST_NAME"] = os.environ["ROMP_POSTAL_HOST"] = "TESTHOST2\n"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                k, b, mid = km._self_host(), pm.self_host(), pm._unique()
            self.assertEqual((k, b), ("TESTHOST", "TESTHOST"), "the derived name, never the newline one")
            self.assertNotIn("\n", mid, "no message id carries a newline")
            self.assertTrue(pm._safe_id(mid))
            self.assertEqual(len([l for l in err.getvalue().splitlines() if "ROMP_HOST_NAME" in l]), 1)
            self.assertEqual(len([l for l in err.getvalue().splitlines() if "ROMP_POSTAL_HOST" in l]), 1)
        finally:
            for k_, v in saved.items():
                if v is None:
                    os.environ.pop(k_, None)
                else:
                    os.environ[k_] = v
            (_socket.gethostname, pm._host_name_candidates, km._host_name_candidates,
             pm._self_host_fb, km._self_host_fb) = seams
            getattr(km, "_host_name_env_warned", set()).clear()
            getattr(pm, "_postal_host_env_warned", set()).clear()


class MessageIdsNeverCollide(unittest.TestCase):
    """_unique() carries 128 bits of randomness (2026-09-08) — random.randint(0, 99999) gave 100k
    names per second per process — and deliver() refuses to publish over a standing new/<name>: a
    collision, however unlikely, is detected loudly, never a silent replace of somebody's unread
    mail (rename() overwrote; the publish is a link() now, which refuses an existing target)."""

    RCP = "22222222-3333-4444-5555-666666666666"
    SND = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        # set, never popped: other modules set ROMP_POSTAL_HOST at import time (see the module head)
        self._prev = os.environ.get("ROMP_POSTAL_HOST")
        os.environ["ROMP_POSTAL_HOST"] = "TESTHOST"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ROMP_POSTAL_HOST", None)
        else:
            os.environ["ROMP_POSTAL_HOST"] = self._prev

    def test_the_id_carries_128_bits_of_randomness(self):
        mid = pm._unique()
        self.assertTrue(pm._safe_id(mid), "still a path component the outbox accepts")
        self.assertRegex(mid, r"^\d+\.\d+_[0-9a-f]{32}\.TESTHOST$")
        self.assertEqual(len({pm._unique() for _ in range(10000)}), 10000, "ten thousand mints, zero collisions")

    def test_a_standing_message_is_never_replaced(self):
        import json
        import shutil
        shutil.rmtree(pm.MAILROOT, ignore_errors=True)
        td = tempfile.mkdtemp()
        saved_tl, saved_unique = pm.TLDIR, pm._unique
        pm.TLDIR = type(pm.TLDIR)(td)
        pm._unique = lambda: "1700000000.4242_deadbeef.TESTHOST"       # a forced collision
        try:
            first = pm.deliver(self.RCP, "web", self.SND, "the first message")
            with self.assertRaises(pm.DeliveryNotRecorded) as cm:
                pm.deliver(self.RCP, "web", self.SND, "an impostor with the same id")
            box = pm.read_box(self.RCP, consume=False)
            self.assertEqual([(m["id"], m["body"]) for m in box], [(first, "the first message")],
                             "the standing message is untouched; the collision was refused, not tiebroken")
            self.assertIn("refusing to replace", str(cm.exception))
            rows = [json.loads(l) for l in (pm.TLDIR / "messages.jsonl").read_text().splitlines() if l]
            self.assertEqual([(r["ev"], r["id"]) for r in rows], [("sent", first)],
                             "the refusal wrote no row: the id is the standing message's, never the impostor's")
            self.assertEqual([p.name for p in (pm.MAILROOT / self.RCP / "tmp").iterdir()], [],
                             "the temp is removed")
        finally:
            pm.TLDIR, pm._unique = saved_tl, saved_unique
            shutil.rmtree(td, ignore_errors=True)

    def test_publish_without_hard_links_falls_back_once_and_real_faults_refuse(self):
        # Every link() refusal but a collision takes the checked rename (review find, 2026-09-08: the
        # first cut named six errnos as "no hard links here" and re-raised the rest, so a mount that
        # answers link() with EACCES or EINVAL refused EVERY send). A real fault fails the rename the
        # same way, so it still refuses with the ledger closed. Mutants: the fallback narrowed back to
        # a list (EACCES/EINVAL refuse); the rename's failure swallowed (a fault "delivers" nothing and
        # leaves the sent row open); the collision folded into the fallback (a standing message replaced).
        import errno
        import json
        import shutil
        shutil.rmtree(pm.MAILROOT, ignore_errors=True)
        td = tempfile.mkdtemp()
        saved_tl, saved_link, saved_rename, saved_log = pm.TLDIR, os.link, os.rename, pm._log
        pm.TLDIR = type(pm.TLDIR)(td)
        logged = []
        pm._log = lambda m: logged.append(m)

        def rows():
            return [json.loads(l) for l in (pm.TLDIR / "messages.jsonl").read_text().splitlines() if l]

        def box():
            newd = pm.MAILROOT / self.RCP / "new"
            return sorted(p.name for p in newd.iterdir()) if newd.is_dir() else []

        def tmpd():
            t = pm.MAILROOT / self.RCP / "tmp"
            return [p.name for p in t.iterdir()] if t.is_dir() else []

        def raising(code):
            def _f(*a, **k):
                raise OSError(code, "staged by the test")
            return _f

        try:
            for code in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EMLINK, errno.EXDEV,
                         errno.EACCES, errno.EINVAL):
                os.link = raising(code)
                pm._LINK_FALLBACK_SAID[0] = False
                logged.clear()
                mid = pm.deliver(self.RCP, "web", self.SND, "via rename (%d)" % code)
                self.assertIn(mid, box(), "errno %d: delivered by the checked rename" % code)
                self.assertEqual(tmpd(), [], "errno %d: no temp left" % code)
                pm.deliver(self.RCP, "web", self.SND, "again (%d)" % code)
                fallback = [m for m in logged if "hard links unavailable" in m]
                self.assertEqual(len(fallback), 1, "errno %d: said once per bus run, not per send" % code)
                self.assertIn("[Errno %d]" % code, fallback[0], "errno %d: the line names the errno" % code)
            for code in (errno.EIO, errno.ENOSPC):
                os.link, os.rename = raising(code), raising(code)     # a REAL fault: the rename meets it too
                before, before_rows = box(), rows()
                with self.assertRaises(pm.DeliveryNotRecorded):
                    pm.deliver(self.RCP, "web", self.SND, "never lands (%d)" % code)
                self.assertEqual(box(), before, "errno %d: nothing new in the inbox" % code)
                self.assertEqual(tmpd(), [], "errno %d: the temp is removed" % code)
                self.assertEqual(rows(), before_rows, "errno %d: a refused publish records nothing" % code)
            self.assertEqual(len([m for m in logged if "refused, nothing recorded" in m]), 2,
                             "each refusal is said on stderr, since no row says it")
            os.rename = saved_rename
            os.link = raising(errno.EEXIST)                          # EEXIST IS the collision: never a fallback
            before, before_rows = box(), rows()
            with self.assertRaises(pm.DeliveryNotRecorded) as cm:
                pm.deliver(self.RCP, "web", self.SND, "a collision at the link")
            self.assertIn("refusing to replace", str(cm.exception))
            self.assertEqual((box(), rows()), (before, before_rows), "a collision at the link writes no row either")
            self.assertEqual(len([m for m in logged if "refused, nothing recorded" in m]), 3)
            # a forced collision under the fallback is refused too, and the first message stands
            os.link = raising(errno.EPERM)
            saved_unique = pm._unique
            pm._unique = lambda: "1700000000.4242_c0ffee.TESTHOST"
            try:
                first = pm.deliver(self.RCP, "web", self.SND, "first under the fallback")
                with self.assertRaises(pm.DeliveryNotRecorded) as cm:
                    pm.deliver(self.RCP, "web", self.SND, "an impostor under the fallback")
            finally:
                pm._unique = saved_unique
            self.assertIn("refusing to replace", str(cm.exception))
            standing = [m["body"] for m in pm.read_box(self.RCP, consume=False) if m["id"] == first]
            self.assertEqual(standing, ["first under the fallback"])
        finally:
            os.link, os.rename, pm.TLDIR, pm._log = saved_link, saved_rename, saved_tl, saved_log
            pm._LINK_FALLBACK_SAID[0] = False
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
