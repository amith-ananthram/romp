#!/usr/bin/env python3
"""One client that stops draining must not stall the clients behind it.

The kernel's send loops are SHARED and serial: _keepalive_all walks every connected client in one
thread, handing each the ~40-byte heartbeat. When that walk wrote to sockets directly, a client whose
buffer was full parked the whole walk, so every client after it received nothing — and the browser shim
force-closes any socket silent for STALE_MS (30s).

That is what "the dashboard keeps losing its connection" turned out to be (the user 2026-07-28),
diagnosed from the panes' own drop logs: chat, feed and timeline dropping at IDENTICAL 35s intervals
(STALE_MS plus one 5s watchdog tick), in lockstep. Three independent sockets falling silent at the same
instant is one shared upstream stall, not three socket failures.

Why a send timeout is NOT the fix (asserted below so it is not "simplified" back in):
  - settimeout() puts the socket in non-blocking mode, and the client's handler thread is parked in a
    blocking read on that same socket; dup() shares the file-status flags, so it does not escape either.
  - select()-then-send() does not bound it: on Linux a BLOCKING send() transmits the whole buffer,
    blocking as needed. Short writes are non-blocking-socket behaviour. This test's first version hung
    for exactly that reason.
The fix is structural: each client owns a queue and a sender thread, and shared loops only enqueue.

Real TCP loopback throughout, matching the kernel's actual transport (AF_UNIX socketpairs behave
differently again and would not represent it). Synthetic only: no session data.
"""
import json
import os
import queue
import socket
import threading
import time
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_wsbound", os.path.join(BIN, "romp-kernel"))


class WedgedClientCannotStallTheSendLoop(unittest.TestCase):
    def setUp(self):
        self.closeables = []

    def tearDown(self):
        for s in self.closeables:
            try:
                s.close()
            except OSError:
                pass

    def _tcp_pair(self, sndbuf=4096):
        """A connected loopback pair with a small send buffer, so a modest payload fills it once the peer
        stops reading — the wedged-client condition, reproduced honestly."""
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        client = socket.socket()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
        client.connect(srv.getsockname())
        peer, _ = srv.accept()
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, sndbuf)
        srv.close()
        self.closeables += [client, peer]
        return client, peer

    def _wire(self, sock):
        """Build the client dict exactly as the WS handler does: queue + sender thread + enqueueing send."""
        q = queue.Queue()
        c = {"app": "feed", "alive": True, "qbytes": 0, "qlock": threading.Lock()}
        c["send"] = km._mk_ws_send(q, sock, c)
        t = threading.Thread(target=km._ws_sender, args=(q, sock, threading.Lock(), c), daemon=True)
        t.start()
        return c, q, t

    def test_sending_to_a_wedged_client_never_blocks_the_caller(self):
        """The core guarantee: the shared loop enqueues and returns, however stuck the peer is."""
        sock, _peer_never_reads = self._tcp_pair()
        c, _q, _t = self._wire(sock)
        big = "x" * (256 * 1024)               # far past the 4 KB buffer; the sender thread will park
        started = time.monotonic()
        dropped = False
        for _ in range(200):                   # 200 x 256 KB overruns the 16 MB budget
            try:
                c["send"](big)
            except OSError:
                dropped = True
                break
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0,
                        "enqueueing to a wedged client must not block the shared walk (took %.1fs)" % elapsed)
        self.assertTrue(dropped, "a client that falls far enough behind must be dropped")
        self.assertFalse(c["alive"], "…and marked dead so the walk stops carrying it")

    def test_a_healthy_client_is_NOT_dropped_by_a_burst_of_frames(self):
        """The false positive that a frame-COUNT budget caused: a producer enqueueing faster than the
        sender thread is scheduled looks identical to a wedged client under a count cap, and a healthy
        pane was dropped after 40 keepalives it would have drained without noticing."""
        sock, reader = self._tcp_pair(sndbuf=65536)
        c, _q, _t = self._wire(sock)
        stop = threading.Event()

        def drain():
            reader.settimeout(0.2)
            while not stop.is_set():
                try:
                    if not reader.recv(65536):
                        return
                except (socket.timeout, TimeoutError):
                    continue
                except OSError:
                    return

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        try:
            for _ in range(500):               # a burst far past any plausible frame count
                c["send"]("ka")
        except OSError as e:
            self.fail("a healthy client must survive a burst of keepalives, got: %s" % e)
        finally:
            stop.set()
        self.assertTrue(c["alive"], "a burst is not a wedge — the client must stay alive")

    def test_a_wedged_client_does_not_starve_the_NEXT_client_in_the_walk(self):
        """The regression in its real shape: a serial walk over [wedged, healthy]. The healthy client
        still gets its frame, well inside the 30s the shim would give up after."""
        wedged, _never_reads = self._tcp_pair()
        healthy, reader = self._tcp_pair(sndbuf=65536)
        cw, _qw, _tw = self._wire(wedged)
        ch, _qh, _th = self._wire(healthy)

        got = []
        stop = threading.Event()

        def drain():
            reader.settimeout(0.2)
            while not stop.is_set():
                try:
                    b = reader.recv(65536)
                except (socket.timeout, TimeoutError):
                    continue
                except OSError:
                    return
                if not b:
                    return
                got.append(b)

        t = threading.Thread(target=drain, daemon=True)
        t.start()

        started = time.monotonic()
        for _ in range(120):                             # the shared, serial keepalive walk, repeated
            for c in (cw, ch):
                try:
                    c["send"]("x" * (256 * 1024) if c is cw else "ka")
                except OSError:
                    pass                                 # caller marks it dead and moves on
        elapsed = time.monotonic() - started

        deadline = time.monotonic() + 5
        while not got and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()

        self.assertLess(elapsed, 30.0,
                        "the walk must not park behind the wedged client (took %.1fs)" % elapsed)
        self.assertTrue(got, "the healthy client must still receive its frames")
        self.assertTrue(ch["alive"], "the healthy client must survive its neighbour being dropped")

    def test_a_draining_client_receives_the_frame_intact(self):
        """The queue must not disturb the ordinary path: header + payload arrive unaltered."""
        sock, peer = self._tcp_pair(sndbuf=65536)
        c, _q, _t = self._wire(sock)
        c["send"]("hello")
        peer.settimeout(3)
        frame = peer.recv(64)
        self.assertEqual(frame[0], 0x81, "FIN + text opcode")
        self.assertEqual(frame[1], 5, "unmasked length of 'hello'")
        self.assertEqual(frame[2:7], b"hello")

    def test_a_large_frame_round_trips_byte_exact(self):
        """Past the 126-byte and 64 KiB header boundaries, where a botched write loop would corrupt the
        stream — the size range the feed payload actually lives in."""
        sock, peer = self._tcp_pair(sndbuf=1 << 20)
        c, _q, _t = self._wire(sock)
        body = "y" * 70000
        got = bytearray()

        def drain():
            peer.settimeout(5)
            while len(got) < 70010:
                try:
                    b = peer.recv(65536)
                except OSError:
                    return
                if not b:
                    return
                got.extend(b)

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        c["send"](body)
        t.join(10)

        self.assertEqual(got[0], 0x81)
        self.assertEqual(got[1], 127, "70000 bytes uses the 64-bit length form")
        self.assertEqual(bytes(got[10:70010]), body.encode(), "payload must arrive byte-exact")

    # T278 (2026-09-08): the budget bounds a client that has STOPPED DRAINING, not a frame that is simply
    # big. The devbox's timeline-bars frame grew to 17.7 MB against the 16 MiB budget, so every fresh
    # connection was dropped on its FIRST frame ("0 bytes behind"), reconnected, and was dropped again: 299
    # drops in one evening, a 17 MB frame rebuilt and serialized several times a minute for nothing, and a
    # laptop that never saw this machine's bars. "Behind" is the bytes queued beyond the LARGEST frame in the
    # queue (one frame may be arbitrarily big; the rest is the backlog): an empty queue accepts any frame, a
    # healthy peer drains the big frame at wire speed while small frames queue behind it, and a wedged
    # peer's stuck head lets the backlog behind it grow past the budget, which is the drop.
    def _budget(self, n):
        old = km.WS_QUEUE_BYTES
        km.WS_QUEUE_BYTES = n
        self.addCleanup(setattr, km, "WS_QUEUE_BYTES", old)

    @staticmethod
    def _bars_payload(target_bytes):
        """A synthetic {type:"bars"} frame, invented prompts under a placeholder sid, at least target_bytes long."""
        sid = "11111111-2222-3333-4444-555555555555"
        bars, i = [], 0
        while len(json.dumps({"turns": {sid: bars}})) < target_bytes:
            bars.append({"id": "t%d" % i, "promptId": "p%d" % i, "workId": "w%d" % i, "start": 1000 + 10 * i,
                         "end": 1005 + 10 * i, "open": False, "cont": False, "prompt": "invented prompt %d" % i,
                         "summary": "invented work caption %d" % i, "msgCaption": "gist %d" % i, "src": "typed",
                         "mids": [], "pending": False, "nudgeAuto": False, "romp": False, "replyUuid": "r%d" % i})
            i += 1
        return {"type": "bars", "turns": {sid: bars}, "judging": [], "messages": [], "now": 2000, "warming": False}

    def test_a_first_frame_larger_than_the_budget_is_delivered_whole(self):
        """A fresh connection, an empty queue, a bars frame three times the budget: delivered byte-exact."""
        self._budget(64 * 1024)
        payload = self._bars_payload(3 * 64 * 1024)
        body = json.dumps(payload)
        self.assertGreater(len(body), km.WS_QUEUE_BYTES, "the fixture is bigger than the budget, as the devbox's frame is")
        sock, peer = self._tcp_pair(sndbuf=1 << 20)
        c, _q, _t = self._wire(sock)
        got = bytearray()
        want = len(body) + 10

        def drain():
            peer.settimeout(5)
            while len(got) < want:
                try:
                    b = peer.recv(65536)
                except OSError:
                    return
                if not b:
                    return
                got.extend(b)

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        try:
            c["send"](body)
        except OSError as e:
            self.fail("a first frame on an empty queue must never be dropped, got: %s" % e)
        t.join(10)
        self.assertTrue(c["alive"])
        self.assertEqual(got[0], 0x81)
        self.assertEqual(got[1], 127, "the 64-bit length form")
        n = int.from_bytes(bytes(got[2:10]), "big")
        self.assertEqual(n, len(body))
        self.assertEqual(json.loads(bytes(got[10:10 + n]).decode()), payload, "the whole frame, reassembled by the peer")

    def test_a_fresh_connection_is_never_dropped_on_its_first_frame_even_to_a_wedged_peer(self):
        """The budget is not a frame-size cap: with nothing queued, a frame five times the budget is accepted."""
        self._budget(64 * 1024)
        sock, _peer_never_reads = self._tcp_pair()
        c, _q, _t = self._wire(sock)
        big = "x" * (5 * 64 * 1024)
        try:
            c["send"](big)
        except OSError as e:
            self.fail("an empty queue accepts any frame, got: %s" % e)
        self.assertTrue(c["alive"])
        self.assertEqual(c["qbytes"], len(big))
        self.assertEqual(c["qsizes"][0], len(big), "the size deque mirrors the queue")

    def test_a_small_frame_ahead_of_the_big_one_does_not_make_the_big_one_backlog(self):
        """The connect push's shape: a small skeleton first, the big bars frame behind it, then two small
        frames, all enqueued before the sender thread has necessarily written the skeleton. The big frame is
        never the backlog whatever its position: the largest queued frame is the one that is merely big."""
        self._budget(64 * 1024)
        sock, _peer_never_reads = self._tcp_pair()
        c, _q, _t = self._wire(sock)
        c["send"]("s" * 4096)                                  # the skeleton, possibly still at the head
        c["send"]("b" * (200 * 1024))                          # the bars, three times the budget
        try:
            c["send"]("t" * 512)                               # tabOrder
            c["send"]("k" * 512)                               # caps
        except OSError as e:
            self.fail("the frames around a big one on a fresh connection must not be dropped, got: %s" % e)
        self.assertTrue(c["alive"])

    def test_the_size_append_and_the_enqueue_are_one_step_under_the_queue_lock(self):
        """Two producers (the pusher and the heartbeat, or a handler's direct reply) interleaving between the
        size append and the queue put would leave the size deque in a different order from the queue, and the
        sender's positional pop would then credit the wrong frame. The put happens under the lock."""
        test = self

        class LockedPutQueue(queue.Queue):
            def put(self, item, block=True, timeout=None):
                test.assertTrue(c["qlock"].locked(), "q.put must run while the client's queue lock is held")
                super().put(item, block, timeout)

        q = LockedPutQueue()
        c = {"app": "feed", "alive": True, "qbytes": 0, "qlock": threading.Lock()}
        c["send"] = km._mk_ws_send(q, object(), c)             # no sender thread: the items stay queued
        c["send"]("one")
        c["send"]("two")
        self.assertEqual(list(c["qsizes"]), [3, 3])
        self.assertEqual([q.get_nowait(), q.get_nowait()], ["one", "two"])

    def test_bytes_behind_exclude_the_largest_queued_frame(self):
        """A wedged peer with a big frame stuck at the head: the small frames queued BEHIND it are the
        backlog, and the drop comes when that backlog, not the big frame, passes the budget."""
        self._budget(64 * 1024)
        sock, _peer_never_reads = self._tcp_pair()
        c, _q, _t = self._wire(sock)
        head = "x" * (200 * 1024)
        c["send"](head)
        small = "y" * (8 * 1024)
        accepted, why = 0, ""
        for _ in range(40):
            try:
                c["send"](small)
                accepted += 1
            except OSError as e:
                why = str(e)
                break
        self.assertFalse(c["alive"], "the backlog behind the stuck head did pass the budget")
        # exact and deterministic: before the k-th small frame the backlog is (k-1) x 8 KiB, and the drop comes
        # when it passes 64 KiB, at k = 10, so nine small frames were accepted behind the head
        self.assertEqual(accepted, 9, "the frames that fit behind the head were accepted (%d)" % accepted)
        self.assertIn("bytes behind", why)
        behind = int(why.split(" is ")[1].split(" bytes")[0])
        self.assertLess(behind, len(head), "the count names the backlog, not the frame in flight")
        self.assertGreater(behind, km.WS_QUEUE_BYTES)

    def test_the_sender_thread_ends_on_the_teardown_sentinel(self):
        """The handler's finally puts None so a closed pane does not leak its thread."""
        sock, _peer = self._tcp_pair(sndbuf=65536)
        _c, q, t = self._wire(sock)
        q.put_nowait(None)
        t.join(3)
        self.assertFalse(t.is_alive(), "the sender thread must exit on the sentinel")


if __name__ == "__main__":
    unittest.main()
