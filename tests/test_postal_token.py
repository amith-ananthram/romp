#!/usr/bin/env python3
"""The postal bus is token-gated (Jupyter's model, shared with the kernel): every route
except the /ping liveness probe requires the machine's serve token — loopback included,
since loopback is reachable by every local user and the bus can wake sessions and inject
mail into their prompts. Accepted forms: X-Romp-Token (same-machine clients, read from
the 0600 file) and ?token= (a peer bus dialing through the ssh forward with the DIALED
machine's token). Also pins the peer-token plumbing: /peer notifies carry the peer's
token, a token-less down notify keeps the last known one, and the dialer sends ?token=.

The Postal* classes pin the bus's COPY of the serve-token read-or-mint (`_serve_token_read_or_mint`,
the same shape as the kernel's; the bus imports nothing from kernel/) to the same contract the
kernel's tests pin in tests/test_kernel_serve_token_mode.py, whose docstring carries the reasoning:
born 0600 by rename of a finished temp with the live path never opened for writing, one mint among
racing starters under serve-token.lock, an unreadable existing token is a refusal, never a rotation
(the old bus loader minted its OWN token on any read fault, and every request it then made was a
silent 403), a loose token is tightened and kept (only the bits outside 0600 go; a tighter 0400 is
left alone), a symlink at the token path is refused with its target untouched, an empty file is
minted over aloud, and without the lock only a token with nothing to tighten is returned, the
refusal otherwise naming the lock. The tighten, empty-file and lock-failure cases each kill a
postal-only mutant that the kernel tests' invariant pins let through in review; the kernel tests'
AST-identity pin now catches any such drift as well, and these say what it broke.

Synthetic only — hermetic temp state dir, placeholder names, no real session data.
"""
import contextlib
import io
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state dir; the sessions-file seam signals "no live kernel" to the bus.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text("[]")
os.environ["ROMP_SESSIONS_FILE"] = _SESS
# An INVENTED token, assigned (not setdefault) for the load only: with the runner's own ROMP_SERVE_TOKEN
# exported, TOK was that real token, and _PostalTokenFile._restore wrote it to disk under the test's
# state dir (review find, 2026-09-08). Nothing real ever reaches this file now. The variable is put
# back the moment the bus has captured it: pytest imports every collected module before any test
# runs, so an import-time write that STAYED would change what every sibling module sends or captures
# from os.environ (the hazard test_color_route.py and test_perf_stats.py describe); a write this
# module made for itself must not outlive its own import.
_prev_serve_token = os.environ.get("ROMP_SERVE_TOKEN")
os.environ["ROMP_SERVE_TOKEN"] = "bus-test-token-DO-NOT-USE"
try:
    ps = load_source("romp_postal_token", os.path.join(BIN, "romp-postal-service"))
finally:
    if _prev_serve_token is None:
        os.environ.pop("ROMP_SERVE_TOKEN", None)
    else:
        os.environ["ROMP_SERVE_TOKEN"] = _prev_serve_token

TOK = ps.SERVE_TOKEN
assert TOK == "bus-test-token-DO-NOT-USE", "the gate's constant is the invented token, never the runner's"


def _code(port, path, headers=None, method="GET", data=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 headers=dict(headers or {}), method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class BusTokenGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_ping_is_exempt(self):
        self.assertEqual(_code(self.port, "/ping"), 200)

    def test_tokenless_requests_denied(self):
        self.assertEqual(_code(self.port, "/agents"), 403)
        self.assertEqual(_code(self.port, "/peers"), 403)
        self.assertEqual(_code(self.port, "/inbox?id=x"), 403)
        self.assertEqual(_code(self.port, "/send", method="POST", data=b"{}"), 403)
        self.assertEqual(_code(self.port, "/peer", method="POST", data=b"{}"), 403)

    def test_header_token_authorizes(self):
        self.assertEqual(_code(self.port, "/peers", headers={"X-Romp-Token": TOK}), 200)

    def test_query_token_authorizes_the_peer_dial_form(self):
        # A peer bus dials /peer-exchange through the ssh forward with ?token= — same
        # acceptance on any route (here /peers, which needs no exchange payload).
        self.assertEqual(_code(self.port, "/peers?token=" + TOK), 200)

    def test_wrong_token_denied(self):
        self.assertEqual(_code(self.port, "/peers", headers={"X-Romp-Token": "wrong"}), 403)
        self.assertEqual(_code(self.port, "/peers?token=wrong"), 403)


class PeerTokenPlumbing(unittest.TestCase):
    def test_peer_update_stores_token_and_down_notify_keeps_it(self):
        ps.peer_update({"host": "TESTHOST", "port": 45001, "up": True, "token": "peer-tok"})
        self.assertEqual(ps.PEERS["TESTHOST"]["token"], "peer-tok")
        ps.peer_update({"host": "TESTHOST", "port": 45001, "up": False})   # down carries no token
        self.assertEqual(ps.PEERS["TESTHOST"]["token"], "peer-tok",
                         "a token-less transition must keep the last known peer token")
        ps.PEERS.pop("TESTHOST", None)

    def test_peer_http_sends_the_peer_token_as_query(self):
        seen = {}

        class Capture(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                body = json.dumps({}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ps._peer_http(srv.server_address[1], {"host": "TESTHOST"}, token="peer-tok")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(seen.get("path"), "/peer-exchange?token=peer-tok")


class _BusServer(unittest.TestCase):
    """A live bus for the request-body classes below (no tests of its own): one server per class."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, path, raw):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=raw, method="POST",
                                     headers={"X-Romp-Token": TOK, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception as e:                        # the handler raised and the socket closed under us
            return None, {"error": repr(e)}

    def _raw(self, path, headers, body=b"", half_close=True):
        """One hand-built POST over a real socket -> (status, JSON body, response head): a header can be
        sent twice, and `half_close` ends the sending side after `body` so a short body reads as EOF."""
        lines = ["POST %s HTTP/1.1" % path, "Host: 127.0.0.1", "Connection: close",
                 "X-Romp-Token: " + TOK] + ["%s: %s" % kv for kv in headers]
        req = ("\r\n".join(lines) + "\r\n\r\n").encode() + body
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            s.sendall(req)
            if half_close:
                s.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            s.close()
        head, _, payload = data.partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload.decode() or "{}"), head.decode()


class BusBodiesAreObjects(_BusServer):
    """Every bus route takes a JSON OBJECT. _body() used to hand back whatever json.loads produced, so
    an array, string, number or null body reached `data.get(...)` and raised AttributeError out of the
    handler: the connection dropped with a traceback on the bus's stderr. Now: the bus's own JSON 400,
    naming what arrived; undecodable JSON keeps its refusal."""

    def test_a_non_object_body_is_a_400_naming_what_arrived(self):
        for raw, echo in ((b"[]", "[]"), (b'"x"', '"x"'), (b"1", "1"), (b"null", "null")):
            for path in ("/send", "/peer", "/peer-exchange"):
                st, r = self._post(path, raw)
                self.assertEqual(st, 400, (path, raw, r))
                self.assertEqual(r.get("error"), "body must be a JSON object, got " + echo, (path, raw))
        st, r = self._post("/send", b"{nope")
        self.assertEqual((st, r.get("error")), (400, "bad json"), "undecodable JSON keeps its refusal")

    def test_a_long_string_body_echoes_clipped_inside_its_quotes(self):
        st, r = self._post("/send", json.dumps("x" * 100_000).encode())
        self.assertEqual(st, 400, r)
        self.assertEqual(r["error"], 'body must be a JSON object, got "' + "x" * 60 + '\u2026"')
        self.assertLess(len(r["error"]), 100)

    def test_a_container_holding_a_long_string_echoes_well_formed(self):
        # review find, 2026-09-08: a slice of the serialized container cut mid-token, so the echo came
        # back with its quote and bracket open; the cut now lands inside the string, at any depth
        st, r = self._post("/send", json.dumps(["x" * 100_000]).encode())
        self.assertEqual(st, 400, r)
        self.assertEqual(r["error"], 'body must be a JSON object, got ["' + "x" * 60 + '\u2026"]')
        st, r = self._post("/send", json.dumps(list(range(1000))).encode())
        self.assertEqual(st, 400, r)
        echo = r["error"].split("got ", 1)[1]
        self.assertEqual(json.loads(echo), [0, 1, 2, 3, 4, 5, 6, 7, "\u2026"], "cut at the element level")
        self.assertLess(len(r["error"]), 300)
        st, r = self._post("/send", json.dumps(int("7" * 4000)).encode())
        self.assertEqual(st, 400, r)
        self.assertEqual(r["error"], 'body must be a JSON object, got "' + "7" * 60 + '\u2026"',
                         "a number past the bound echoes as a marked string: a cut decimal would be a mid-token cut")


class BusBodyGate(_BusServer):
    """The bus reads a body only within bounds -- the kernel's gate, mirrored. _body() used to trust the
    first of two Content-Length headers, cap nothing, and pass whatever bytes arrived as the body."""

    def test_two_content_length_headers_are_400_and_close(self):
        st, r, head = self._raw("/send", [("Content-Length", "2"), ("Content-Length", "4")], body=b"{}{}")
        self.assertEqual((st, r.get("error")), (400, "body could not be read: more than one Content-Length header"))
        self.assertIn("Connection: close", head)

    def test_a_non_decimal_content_length_is_400(self):
        st, r, head = self._raw("/send", [("Content-Length", "12abc")], body=b"{}")
        self.assertEqual(st, 400, r)
        self.assertIn("invalid literal for a byte count", r.get("error", ""))

    def test_an_oversize_announcement_is_413_before_any_read(self):
        # announced past the cap, nothing sent: refused on the declared length before any read (the old
        # bus read to EOF, got b"", and answered "bad json")
        st, r, head = self._raw("/send", [("Content-Length", str(ps._POST_MAX_BYTES + 1))])
        self.assertEqual(st, 413, r)
        self.assertIn(str(ps._POST_MAX_BYTES), r.get("error", ""))
        self.assertIn("Connection: close", head)

    def test_a_body_shorter_than_announced_is_400_naming_the_shortfall(self):
        st, r, head = self._raw("/send", [("Content-Length", "100")], body=b'{"to": "ap')
        self.assertEqual((st, r.get("error")), (400, "body could not be read: read 10 of the 100 bytes Content-Length announced"))
        self.assertIn("Connection: close", head)

    def test_transfer_encoding_is_411(self):
        st, r, head = self._raw("/send", [("Transfer-Encoding", "chunked")], body=b"2\r\n{}\r\n0\r\n\r\n")
        self.assertEqual(st, 411, r)
        self.assertIn("Transfer-Encoding", r.get("error", ""))

    def test_a_trickling_body_is_408_and_closes(self):
        # review find, 2026-09-08: the in-bounds read had no socket timeout, so an AUTHORIZED client that
        # announced a body and trickled it pinned a handler thread for as long as it liked. 100 bytes
        # announced, 10 sent, the sending side left OPEN (no half-close, so no EOF to end the read)
        saved = ps._POST_BODY_TIMEOUT
        ps._POST_BODY_TIMEOUT = 0.5
        try:
            t0 = time.monotonic()
            st, r, head = self._raw("/send", [("Content-Length", "100")], body=b'{"to": "ap', half_close=False)
            took = time.monotonic() - t0
        finally:
            ps._POST_BODY_TIMEOUT = saved
        self.assertEqual((st, r.get("error")),
                         (408, "body could not be read: 100 bytes announced, not all of it arrived within 0.5 s"))
        self.assertIn("Connection: close", head)
        self.assertLess(took, 5, "answered on the body timeout, not on the client giving up")
        # the server is still serving, and a body that arrives whole is read as before
        st, r, head = self._raw("/send", [("Content-Length", "2")], body=b"{}")
        self.assertEqual(st, 400, r)
        self.assertIn("sender identity required", r.get("error", ""))


class TrackedIsABoolean(_BusServer):
    """`tracked` arms a report-back delegation. Both doors coerced it with bool(), so the STRING "false"
    tracked a send. Now a non-boolean is refused -- HTTP /send with a 400 naming the field, the MCP tool
    in its error return -- before any recipient is resolved or the bus dialed. The MCP schema declares
    the field boolean and `romp mail send --tracked` sends True, so real callers are unchanged."""
    SID = "11111111-2222-3333-4444-555555555555"

    def test_http_send_refuses_a_string_tracked_before_resolving_anyone(self):
        for bad in ("false", "true", 1, "yes"):
            st, r = self._post("/send", json.dumps({"to": "api", "from": "web", "from_id": self.SID,
                                                    "body": "hello", "kind": "delegate", "tracked": bad}).encode())
            self.assertEqual(st, 400, (bad, r))
            self.assertEqual(r.get("error"), "'tracked' must be true or false, got %s" % json.dumps(bad))

    def test_the_mcp_tool_refuses_a_string_tracked_before_dialing_the_bus(self):
        dialed = []

        def http(*a, **k):
            dialed.append(a)
            raise ps.BusError("stubbed: the bus is not dialed here")
        # _mcp_call resolves its identity through _self_identity (the one resolution behind my_id and
        # my_name), so that is the seam to stub; stubbing the two wrappers leaves the call unresolved.
        saved = (ps._http, ps._self_identity, ps._heartbeat)
        ps._http, ps._self_identity, ps._heartbeat = http, (lambda: (self.SID, "web")), (lambda mid, me: None)
        try:
            for bad in ("false", "true", 1):
                text, is_err = ps._mcp_call("send_message", {"to": "api", "body": "hello", "kind": "delegate", "tracked": bad})
                self.assertTrue(is_err, (bad, text))
                self.assertIn("'tracked' must be true or false, got %s" % json.dumps(bad), text)
            self.assertEqual(dialed, [], "the bus is never dialed with a malformed flag")
            ps._mcp_call("send_message", {"to": "api", "body": "hello", "kind": "delegate", "tracked": True})
            self.assertEqual(dialed[0][:2], ("POST", "/send"))
            self.assertIs(dialed[0][2]["tracked"], True, "a real true rides the wire as itself")
        finally:
            ps._http, ps._self_identity, ps._heartbeat = saved
def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class _PostalTokenFile(unittest.TestCase):
    """Drives _load_serve_token against the file the kernel mints (STATE.parent / "serve-token").
    SERVE_TOKEN, the module constant the gate compares against, is the invented TOK from import and
    is not touched; each case ends by putting TOK back so the file and the constant agree again. The
    umask is 0 so only the loader's own modes protect what it writes."""

    def setUp(self):
        self.f = ps.STATE.parent / "serve-token"
        self.lock = self.f.with_name("serve-token.lock")
        # The fixture owns its state directory (T274, 2026-09-08): the module's XDG_STATE_HOME is a fresh temp dir,
        # and only the two minting cases (birth, the flock race) create STATE.parent through the loader. Run alone —
        # a parallel worker scheduling one of the other six by itself — those wrote the token, the lock, or the
        # symlink into a directory that did not exist yet and failed on the parent, then again in _restore. Serially
        # they passed on a sibling's side effect, which is not a fixture.
        self.f.parent.mkdir(parents=True, exist_ok=True)
        self._env = self._umask = None
        self.addCleanup(self._restore)      # registered FIRST: a failing _clear() must not leak a zeroed umask
        self._env = os.environ.pop("ROMP_SERVE_TOKEN", None)
        self._umask = os.umask(0)
        self._clear()

    def _clear(self):
        for p in [self.f, self.lock] + list(self.f.parent.glob("serve-token.*.tmp")):
            if p.is_dir():
                p.rmdir()                    # PostalLockFailureIsFailClosed plants a directory at the lock path
            elif p.is_symlink() or p.exists():   # PostalSymlinkIsRefused plants a link
                p.unlink()

    def _restore(self):
        self._clear()
        self.f.write_text(TOK)
        os.chmod(self.f, 0o600)
        if self._umask is not None:
            os.umask(self._umask)
        if self._env is not None:
            os.environ["ROMP_SERVE_TOKEN"] = self._env

    def _temps(self):
        return sorted(p.name for p in self.f.parent.glob("serve-token.*.tmp"))


class PostalTokenBirth(_PostalTokenFile):
    def test_token_is_0600_from_its_first_byte_and_the_live_path_is_never_opened_for_writing(self):
        """Expected first error on the old loader: the rename count is 0 (it wrote the live file with
        write_text, at the umask's mode, and chmod'd it a line later)."""
        os.umask(0o022)
        swaps, opens = [], []
        real_replace, real_open = os.replace, os.open

        def _replace(src, dst, *a, **k):
            swaps.append((str(src), str(dst), _mode(src), Path(src).read_text()))
            return real_replace(src, dst, *a, **k)

        def _open(path, flags, *a, **k):
            opens.append((str(path), flags))
            return real_open(path, flags, *a, **k)

        os.replace, os.open = _replace, _open
        try:
            tok = ps._load_serve_token()
        finally:
            os.replace, os.open = real_replace, real_open

        self.assertEqual(len(swaps), 1, "the mint must land by one rename of a finished temp file")
        src, dst, mode, body = swaps[0]
        self.assertEqual(dst, str(self.f))
        self.assertEqual(mode, 0o600, "the temp is born 0600 under a 022 umask")
        self.assertEqual(body, tok, "the temp already holds the whole token when it is swapped in")
        self.assertEqual(Path(src).parent, self.f.parent, "same directory, so the rename is atomic")
        self.assertFalse(Path(src).exists(), "the temp is gone after the swap")
        self.assertEqual([fl for path, fl in opens if path == str(self.f)], [],
                         "the live token path is never opened for writing at all")
        self.assertEqual(_mode(self.f), 0o600)
        self.assertEqual(self.f.read_text(), tok)


class PostalTokenFlock(_PostalTokenFile):
    def test_racing_starters_read_one_token_and_the_lock_file_is_0600(self):
        """The barrier inside os.urandom holds every unlocked racer at the mint until all have read
        'absent'; under the lock only the first racer gets there and waits it out alone. Expected
        first error on the old loader: 6 != 1 at the one-token check."""
        n = 6
        gate = threading.Barrier(n)
        real_urandom = os.urandom
        mints = []

        def _urandom(k):
            mints.append(threading.get_ident())
            try:
                gate.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass
            return real_urandom(k)

        out, errs = [], []

        def run():
            try:
                out.append(ps._load_serve_token())
            except BaseException as e:        # surfaced by the assertion below, never swallowed
                errs.append(repr(e))

        os.urandom = _urandom
        try:
            ts = [threading.Thread(target=run) for _ in range(n)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(15)
        finally:
            os.urandom = real_urandom

        self.assertEqual(errs, [])
        self.assertEqual(len(out), n)
        self.assertEqual(len(set(out)), 1, "every starter must come away holding the SAME token")
        self.assertEqual(self.f.read_text().strip(), out[0])
        self.assertEqual(len(mints), 1, "exactly one starter minted; the rest read its token under the lock")
        self.assertTrue(self.lock.exists())
        self.assertEqual(_mode(self.lock), 0o600)
        self.assertEqual(self._temps(), [])


class PostalUnreadableIsNotRotated(_PostalTokenFile):
    @unittest.skipIf(os.geteuid() == 0, "root reads through mode 0, so the fault cannot be staged")
    def test_an_unreadable_existing_token_is_a_fault_not_a_rotation(self):
        """Expected first error on the old loader: RuntimeError not raised (it returned a fresh
        random token the kernel would refuse, while the file kept the real one)."""
        self.f.write_text("old-token-DO-NOT-USE\n")
        os.chmod(self.f, 0)
        with self.assertRaises(RuntimeError) as cm:
            ps._load_serve_token()
        msg = str(cm.exception)
        self.assertIn(str(self.f), msg, "the refusal names the file to fix")
        self.assertIn("EACCES", msg, "and the errno, by name")
        self.assertIn("NOT replace", msg)
        os.chmod(self.f, 0o600)
        self.assertEqual(self.f.read_text(), "old-token-DO-NOT-USE\n", "the file is untouched, byte for byte")
        self.assertEqual(self._temps(), [])


class PostalTightenMode(_PostalTokenFile):
    def test_a_loose_existing_token_is_tightened_and_returned_unchanged(self):
        """Kills the postal-only mutant that drops the tighten. Old loader: 0o644 != 0o600 (a
        readable file never reached its chmod)."""
        self.f.write_text("keep-me-DO-NOT-USE\n")
        os.chmod(self.f, 0o644)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tok = ps._load_serve_token()
        self.assertEqual(tok, "keep-me-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o600)
        self.assertEqual(self.f.read_text(), "keep-me-DO-NOT-USE\n", "tightening the mode rewrites nothing")
        self.assertIn("644", err.getvalue())
        self.assertIn("0600", err.getvalue())
        self.assertEqual(self._temps(), [])

    def test_a_tighter_token_is_left_alone_and_only_loose_bits_are_stripped(self):
        """0400 has nothing outside 0600 and is left as is, silently; 0640 loses its group read and
        keeps its owner bits. The equality check widened 0400 to 0600 and called it a repair (review
        find, 2026-09-08). Expected first error on that shape: 0o600 != 0o400."""
        self.f.write_text("tight-DO-NOT-USE\n")
        os.chmod(self.f, 0o400)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ps._load_serve_token(), "tight-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o400, "a mode tighter than 0600 is not widened")
        self.assertEqual(err.getvalue(), "", "and nothing is said: nothing was changed")
        self._clear()
        self.f.write_text("keep-me-DO-NOT-USE\n")
        os.chmod(self.f, 0o640)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ps._load_serve_token(), "keep-me-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o600)
        self.assertIn("0640", err.getvalue())
        self.assertIn("0600", err.getvalue())


class PostalSymlinkIsRefused(_PostalTokenFile):
    def test_a_symlink_at_the_token_path_is_a_fault_and_its_target_is_untouched(self):
        """stat and chmod follow a link, so the following shape read some other file as the token and
        rewrote that file's mode (review find, 2026-09-08). Expected first error on it: RuntimeError
        not raised."""
        target = self.f.parent / "elsewhere-DO-NOT-USE"
        target.write_text("linked-DO-NOT-USE\n")
        os.chmod(target, 0o644)
        self.addCleanup(target.unlink)
        self.f.symlink_to(target)
        with self.assertRaises(RuntimeError) as cm:
            ps._load_serve_token()
        self.assertIn(str(self.f), str(cm.exception), "the refusal names the token path")
        self.assertIn("symlink", str(cm.exception))
        self.assertEqual(_mode(target), 0o644, "the target's mode is not rewritten through the link")
        self.assertEqual(target.read_text(), "linked-DO-NOT-USE\n")
        self.assertTrue(self.f.is_symlink(), "the link is left for the operator to remove")
        self.assertEqual(self._temps(), [])


class PostalEmptyFileMints(_PostalTokenFile):
    def test_an_empty_or_whitespace_file_is_a_torn_mint_and_is_minted_over_aloud(self):
        """Kills the postal-only mutant that raises on an empty file instead of minting over it.
        Old loader: 'empty' not found in '' (it minted, but silently)."""
        for body in ("", "  \n"):
            self._clear()
            self.f.write_text(body)
            os.chmod(self.f, 0o644)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                tok = ps._load_serve_token()
            self.assertTrue(tok, "a token was minted")
            self.assertEqual(self.f.read_text(), tok)
            self.assertEqual(_mode(self.f), 0o600)
            self.assertIn("empty", err.getvalue(), "the torn earlier mint is said on stderr")
            self.assertEqual(self._temps(), [])


class PostalLockFailureIsFailClosed(_PostalTokenFile):
    def test_no_lock_tolerates_only_a_good_0600_token(self):
        """Kills the postal-only mutant whose lock-failure arm returns any non-empty token
        regardless of mode (case b). Old loader: the lock path is not said on stderr at (a), and
        the refusals at (b) and (c) are not raised either."""
        self.lock.mkdir()
        self.f.write_text("good-DO-NOT-USE")
        os.chmod(self.f, 0o600)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ps._load_serve_token(), "good-DO-NOT-USE")
        self.assertIn(str(self.lock), err.getvalue())
        os.chmod(self.f, 0o644)
        with self.assertRaises(RuntimeError) as cm:
            ps._load_serve_token()
        self.assertIn(str(self.lock), str(cm.exception), "the refusal names the lock path")
        self.assertIn("EISDIR", str(cm.exception))
        self.assertEqual(_mode(self.f), 0o644, "no unlocked write of any kind, not even a chmod")
        os.chmod(self.f, 0o400)               # (d) tighter than 0600: nothing to tighten, so returned like a 0600 one
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ps._load_serve_token(), "good-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o400)
        self.f.unlink()
        with self.assertRaises(RuntimeError) as cm:
            ps._load_serve_token()
        self.assertIn(str(self.lock), str(cm.exception), "with no token file, the refusal names the lock and the fault")
        self.assertIn("no token file", str(cm.exception))
        self.assertNotIn("Make the file yours", str(cm.exception), "not a token file that does not exist (review find, 2026-09-08)")
        self.assertFalse(self.f.exists(), "nothing minted unlocked")
        self.assertEqual(self._temps(), [])


def _call(port, path, method="GET", payload=None):
    """(status, body) over the real Handler, token in hand — a refusal answers a JSON body too."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
                                 headers={"X-Romp-Token": TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


_RCP = "22222222-3333-4444-5555-666666666666"
_SND = "11111111-2222-3333-4444-555555555555"


class _LiveBus(unittest.TestCase):
    """A real ThreadingHTTPServer on ps.Handler: the routes below are pinned by their HTTP answers,
    not by calling read_box / deliver directly — the defects were in what the socket saw."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ps.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self._saved = (ps.TLDIR, ps._log, ps.resolve_recipient)
        self.logged = []
        ps._log = lambda m: self.logged.append(m)
        if hasattr(ps, "_TL_FAULT"):
            ps._TL_FAULT[0] = False
        getattr(ps, "_UNREADABLE_SAID", set()).clear()

    def tearDown(self):
        ps.TLDIR, ps._log, ps.resolve_recipient = self._saved
        if hasattr(ps, "_TL_FAULT"):
            ps._TL_FAULT[0] = False
        ps.PEERS.pop("farhost", None)

    def _break_the_log(self):
        # TLDIR under a regular FILE: mkdir raises (ENOTDIR), so the REAL _tl_append fails the way a
        # full or read-only disk fails it
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.unlink(path))
        ps.TLDIR = Path(path) / "timeline"


class InboxSurvivesAnUnreadableFile(_LiveBus):
    """One unreadable file in new/ (EACCES here; EIO in the wild) used to raise out of read_box, and
    do_GET has no handler: socketserver printed the traceback and closed the socket with NO HTTP
    answer, on every /inbox and /drain, so the session got no mail at all (2026-09-08). The rest of
    the box is served, and the file is moved ASIDE once (review find, 2026-09-08): to
    `<mailbox>/<name>.corrupt-<stamp>`, beside new/ and out of every listing, never deleted; with one
    log line, one bell row through the kernel, a terminal row that closes the sender's receipt as
    refused, and the pending marker no longer latched. The first cut left the file in place, said
    once: re-skipped every poll, the marker up forever, the receipt pending forever, and nothing the
    user could see. Mutants: the move dropped (the file stays, the marker latches); the row dropped
    (the receipt reads pending); the notice dropped (the log alone knows)."""

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-0 file; the fault cannot be staged")
    def test_the_file_is_moved_aside_once_the_rest_is_served_and_the_sender_hears_refused(self):
        import errno
        import shutil
        shutil.rmtree(ps.MAILROOT / _RCP, ignore_errors=True)
        shutil.rmtree(ps.MAILPENDING, ignore_errors=True)
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        ps.TLDIR = Path(td)
        told, saved_post = [], ps._kernel_post
        ps._kernel_post = lambda path, body, timeout=2: told.append((path, body)) or {"ok": True}
        self.addCleanup(lambda: setattr(ps, "_kernel_post", saved_post))
        ps.deliver(_RCP, "web", _SND, "the readable one", kind="coordinate")
        bad = ps.deliver(_RCP, "web", _SND, "never readable", kind="question")   # a real send: its sent row stands
        badf = ps.MAILROOT / _RCP / "new" / bad
        os.chmod(badf, 0)
        status, body = _call(self.port, "/inbox?id=%s&peek=1" % _RCP)
        self.assertEqual(status, 200, "the inbox answers")
        self.assertEqual([m["body"] for m in body["messages"]], ["the readable one"],
                         "the rest of the box is served")
        self.assertFalse(badf.exists(), "the unreadable file leaves new/")
        aside = [p.name for p in (ps.MAILROOT / _RCP).iterdir() if p.name.startswith(bad + ".corrupt-")]
        self.assertEqual(len(aside), 1, "moved aside beside new/, kept as evidence")
        status, body = _call(self.port, "/inbox?id=%s&peek=1" % _RCP)
        self.assertEqual((status, [m["body"] for m in body["messages"]]), (200, ["the readable one"]))
        said = [m for m in self.logged if bad in m]
        self.assertEqual(len(said), 1, "said once across two polls, not per poll")
        self.assertIn("errno %d" % errno.EACCES, said[0], "naming the errno")
        self.assertIn(aside[0], said[0], "and where it went")
        self.assertEqual([(p, "moved aside" in b.get("text", "")) for p, b in told], [("/postal-notice", True)],
                         "one bell row through the kernel, not one per poll")
        rows = [json.loads(l) for l in (Path(td) / "messages.jsonl").read_text().splitlines() if l]
        self.assertEqual([r["ev"] for r in rows if r.get("id") == bad], ["sent", "bounced"],
                         "the ledger closes on the id nobody can read")
        rec = [r for r in ps._sent_receipts(_SND) if r["id"] == bad][0]
        self.assertTrue(rec["bounced"])
        self.assertTrue(rec["bouncedWhy"].startswith(ps.WHY_INBOX_UNREADABLE))
        txt = ps.format_receipts([rec])
        self.assertIn("refused", txt)
        self.assertNotIn("returned to you", txt, "no return note exists for a refusal")
        _call(self.port, "/inbox?id=%s" % _RCP)                        # the drain consumes the readable one
        self.assertFalse((ps.MAILPENDING / _RCP).exists(),
                         "the pending marker is not latched by a file nobody can read")


class SendRefusesWhenTheRowCannotLand(_LiveBus):
    """The /send route answers ok:false — 503, so the tool and CLI clients, which raise only on a
    non-2xx, surface it — when the sent row cannot be written, on BOTH legs: the relay park and the
    local delivery. On origin/main both answered ok:true: the relay leg parked the message and
    appended the row afterwards regardless, and deliver() published before its best-effort row."""

    def _far(self):
        ps.resolve_recipient = lambda to, frm_id="": {"kind": "relay", "host": "farhost",
                                                       "agent": {"name": to, "id": _RCP}}
        ps.PEERS["farhost"] = {"up": True, "port": 1}

    def _send(self, kind="coordinate"):
        return _call(self.port, "/send", "POST", {"to": "api", "from": "web", "from_id": _SND,
                                                  "body": "the staging port?", "kind": kind})

    def test_the_relay_leg_answers_ok_false_and_parks_nothing(self):
        import shutil
        shutil.rmtree(ps.OUTBOX / "farhost", ignore_errors=True)
        self._far()
        self._break_the_log()
        status, body = self._send()
        self.assertEqual(status, 503)
        self.assertFalse(body.get("ok"))
        self.assertIn("not recorded", body.get("error", ""))
        self.assertIn("retry", body.get("error", ""))
        self.assertEqual(ps.outbox_list("farhost"), [], "nothing parked without its row")

    def test_the_relay_leg_still_answers_ok_when_the_row_lands(self):
        import shutil
        shutil.rmtree(ps.OUTBOX / "farhost", ignore_errors=True)
        self._far()
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        ps.TLDIR = Path(td)
        status, body = self._send()
        self.assertEqual((status, body.get("ok")), (200, True))
        self.assertEqual([m["mid"] for m in ps.outbox_list("farhost")], [body["id"]], "parked, with its row")
        rows = [json.loads(l) for l in (Path(td) / "messages.jsonl").read_text().splitlines() if l]
        self.assertEqual([(r["ev"], r["id"]) for r in rows], [("sent", body["id"])])

    def test_the_local_leg_answers_ok_false_and_delivers_nothing(self):
        import shutil
        shutil.rmtree(ps.MAILROOT / _RCP, ignore_errors=True)
        ps.resolve_recipient = lambda to, frm_id="": {"kind": "direct",
                                                       "agent": {"name": to, "id": _RCP, "remote": False}}
        self._break_the_log()
        status, body = self._send(kind="question")
        self.assertEqual(status, 503)
        self.assertFalse(body.get("ok"))
        self.assertIn("not delivered", body.get("error", ""))
        newd = ps.MAILROOT / _RCP / "new"
        self.assertEqual([p.name for p in newd.iterdir()] if newd.is_dir() else [], [], "no mail without its row")

    def test_a_park_that_fails_after_the_row_closes_the_ledger_and_refuses(self):
        # mutant: outbox_put's False ignored → 200 "relaying" with a sent row and nothing parked
        import shutil
        self._far()
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        ps.TLDIR = Path(td)
        saved = ps.outbox_put
        ps.outbox_put = lambda h, m: False                  # the record could not be written
        try:
            status, body = self._send()
        finally:
            ps.outbox_put = saved
        self.assertEqual(status, 503)
        self.assertFalse(body.get("ok"))
        self.assertIn("could not be parked", body.get("error", ""))
        rows = [json.loads(l) for l in (Path(td) / "messages.jsonl").read_text().splitlines() if l]
        self.assertEqual([r["ev"] for r in rows], ["sent", "bounced"], "the ledger closes on the refused id")
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        self.assertEqual(rows[1]["why"], ps.WHY_NOT_PARKED)



if __name__ == "__main__":
    unittest.main(verbosity=2)
