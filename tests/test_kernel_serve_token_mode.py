#!/usr/bin/env python3
"""The serve-token file is the same-user gate on the whole kernel, and every client holds a COPY of
it (bin/romp and the hooks read the file, the bus and each session's MCP process load it at start,
peers fetch it at attach). So `_load_token` has two jobs beyond returning a string: the file must be
0600 from its first byte, and an EXISTING token must never be replaced behind those clients' backs.

The old loader failed the second job in three ways, each pinned here by a case that fails on it:
  - ANY read fault fell through to the mint (`except OSError: pass`), so an EACCES or EIO rotated
    the token: the kernel came up with a credential nobody else held. UnreadableIsNotRotated.
  - Two starters (kernel and bus boot together) each minted their own, last writer winning while
    the loser served a token the file no longer held. ServeTokenFlock.
  - The mint opened the LIVE path with O_TRUNC, so a concurrent reader saw an empty file and
    minted its own. TokenBirth pins the temp-then-rename shape and that the live path is never
    opened for writing at all.
TightenMode (a loose existing file is chmod'd, its value kept), EmptyFileMints (a 0-byte or
whitespace file is a torn earlier mint and is minted over, aloud), LockFailureIsFailClosed (no
lock: a good 0600 token is returned, anything else is a refusal) and StaleTempsAreSwept (a crashed
attempt's temp, any pid's, is removed before the mint) pin the rest of the contract. From review
(2026-09-08): CheckinCarriesServedToken (the handshake announces TOKEN and never touches the file),
TempIsExclusive (O_EXCL is behaviour, not source text: a file already at the temp path is never
written over), SymlinkIsRefused (a link at the token path is a fault, its target untouched),
LockWaitIsAnnounced (a starter blocked by another holder says so before waiting), ImportRefusalIsLoud
(a read fault at import exits the kernel non-zero with the file untouched, in a subprocess), and the
tighter-mode cases in TightenMode and LockFailureIsFailClosed (0400 is left alone; only bits outside
0600 go).
ServeTokenLoadersMatch pins the bus's copy of the routine to the kernel's, since the bus imports
nothing from kernel/ and carries its own: AST identity with the docstring stripped (any divergence
is red), plus the named invariants as a readable second layer.

ServeTokenFileMode is the original mode case. It INTERPOSES on os.chmod because the old shape's
trailing chmod repaired the mode before anything could observe it; the shape now needs no chmod on
a fresh mint at all, and that is what the case asserts.

Synthetic only: hermetic temp STATE, placeholder tokens.
"""
import ast
import contextlib
import fcntl
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class _TokenFile(unittest.TestCase):
    """Every case starts and ends with no token, no lock and no temp under the test's own STATE.
    ROMP_SERVE_TOKEN is moved out of the way (with it set the loader returns it verbatim and never
    touches the file), and the umask is 0 so only the loader's own modes protect what it writes."""

    def setUp(self):
        self.f = km.jd.STATE / "serve-token"
        self.lock = self.f.with_name("serve-token.lock")
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        self._env = self._umask = None
        self.addCleanup(self._restore)      # registered FIRST: a failing _clear() must not leak a zeroed umask
        self._env = os.environ.pop("ROMP_SERVE_TOKEN", None)
        self._umask = os.umask(0)
        self._clear()

    def _clear(self):
        for p in [self.f, self.lock] + list(km.jd.STATE.glob("serve-token.*.tmp")):
            if p.is_dir():
                p.rmdir()                    # LockFailureIsFailClosed plants a directory at the lock path
            elif p.is_symlink() or p.exists():   # SymlinkIsRefused plants links, one of them dangling
                p.unlink()

    def _restore(self):
        self._clear()
        if self._umask is not None:
            os.umask(self._umask)
        if self._env is not None:
            os.environ["ROMP_SERVE_TOKEN"] = self._env

    def _temps(self):
        return sorted(p.name for p in km.jd.STATE.glob("serve-token.*.tmp"))


class ServeTokenFileMode(_TokenFile):
    def test_minted_0600_before_any_chmod_can_repair_it(self):
        chmods = []
        real_chmod = os.chmod

        def _no_chmod(path, mode, *a, **k):
            """Record the repair and DON'T perform it: the mode the file was created with is the
            whole question, and a chmod one line later hides the answer."""
            chmods.append((str(path), mode))

        os.chmod = _no_chmod
        try:
            tok = km._load_token()
        finally:
            os.chmod = real_chmod

        self.assertTrue(self.f.exists(), "the mint path never ran — this test proves nothing")
        self.assertEqual(self.f.read_text().strip(), tok, "the file must hold the token it returned")
        self.assertEqual(_mode(self.f), 0o600,
                         "the serve token must be 0600 from the open() that created it: writing it "
                         "first and chmod'ing after leaves the credential at the umask's mercy for "
                         "the gap between the two calls")
        self.assertEqual(chmods, [],
                         "a fresh mint needs no chmod at all: the temp is opened 0600 and renamed into "
                         "place, so there is no pre-existing inode to tighten (TightenMode covers the "
                         "one case where there is)")

    def test_a_loose_empty_file_is_minted_over_and_the_new_inode_is_born_0600(self):
        # An empty serve-token left at 0644 (a torn earlier mint, or one written before this change)
        # is minted over: the rename swaps in a NEW inode born 0600, so the loose mode goes with the
        # old one. (Named for what it exercises, the mint-over path, not the tighten: review find,
        # 2026-09-08. TightenMode covers a NON-empty loose file.)
        self.f.write_text("")                # empty → a torn earlier mint → falls through to the mint
        os.chmod(self.f, 0o644)
        with contextlib.redirect_stderr(io.StringIO()):
            tok = km._load_token()
        self.assertEqual(self.f.read_text().strip(), tok)
        self.assertEqual(_mode(self.f), 0o600,
                         "a token file that already existed at 0644 must not keep that mode")

    def test_the_env_override_never_writes_the_file(self):
        # Guards this test file's own premise: with ROMP_SERVE_TOKEN set there is nothing on disk to
        # have a mode, so the cases above would be vacuous if the pop in setUp ever stopped working.
        os.environ["ROMP_SERVE_TOKEN"] = "env-token-DO-NOT-USE"
        try:
            self.assertEqual(km._load_token(), "env-token-DO-NOT-USE")
        finally:
            os.environ.pop("ROMP_SERVE_TOKEN", None)
        self.assertFalse(self.f.exists(), "the env override must not mint or persist anything")
        self.assertFalse(self.lock.exists(), "nor take the lock: there is nothing on disk to guard")


class ServeTokenFlock(_TokenFile):
    def test_racing_starters_read_one_token_and_the_lock_file_is_0600(self):
        """N starters with no token on disk. Without the lock each one reads 'absent' and mints its
        own, so N distinct tokens come back and N-1 callers hold one the file no longer has. The
        barrier inside os.urandom widens that window deterministically: on the unlocked shape every
        racer reaches the mint before any of them writes. Under the lock only the FIRST racer ever
        gets there (the rest block on flock and then read its token), so it waits out the barrier
        alone and moves on. Expected first error on the old loader: 6 != 1 at the one-token check."""
        n = 6
        gate = threading.Barrier(n)
        real_urandom = os.urandom
        mints = []

        def _urandom(k):
            mints.append(threading.get_ident())
            try:
                gate.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass                          # under the lock the first racer is here alone: move on
            return real_urandom(k)

        out, errs = [], []

        def run():
            try:
                out.append(km._load_token())
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

        self.assertEqual(errs, [], "no racer may fail: a fresh mint is the ordinary first boot")
        self.assertEqual(len(out), n)
        self.assertEqual(len(set(out)), 1,
                         "every starter must come away holding the SAME token: %d distinct ones means "
                         "the losers now hold credentials the daemon will refuse" % len(set(out)))
        self.assertEqual(self.f.read_text().strip(), out[0])
        self.assertEqual(len(mints), 1, "exactly one starter minted; the rest read its token under the lock")
        self.assertTrue(self.lock.exists(), "the lock is a sibling file, serve-token.lock")
        self.assertEqual(_mode(self.lock), 0o600)
        self.assertEqual(self._temps(), [], "no temp survives a finished mint")


class TokenBirth(_TokenFile):
    def test_token_is_0600_from_its_first_byte_and_the_live_path_is_never_opened_for_writing(self):
        """Under a stock 022 umask. The mint must land by renaming a FINISHED temp onto the path:
        os.replace is interposed to stat and read the temp at the instant of the swap, and os.open is
        interposed to record every open of the live path (there must be none: O_TRUNC on it is the
        torn window a concurrent reader fell into). Expected first error on the old loader: the
        rename count is 0, because it wrote the live file in place."""
        os.umask(0o022)
        swaps, opens, synced = [], [], []
        real_replace, real_open, real_fsync = os.replace, os.open, os.fsync

        def _replace(src, dst, *a, **k):
            # what had been fsynced by the instant of the swap: the inode list is behaviour the source-text
            # pin on `os.fsync(` cannot see (review find, 2026-09-08)
            swaps.append((str(src), str(dst), _mode(src), Path(src).read_text(), os.stat(src).st_ino, list(synced)))
            return real_replace(src, dst, *a, **k)

        def _open(path, flags, *a, **k):
            opens.append((str(path), flags))
            return real_open(path, flags, *a, **k)

        def _fsync(fd):
            synced.append(os.fstat(fd).st_ino)
            return real_fsync(fd)

        os.replace, os.open, os.fsync = _replace, _open, _fsync
        try:
            tok = km._load_token()
        finally:
            os.replace, os.open, os.fsync = real_replace, real_open, real_fsync

        self.assertEqual(len(swaps), 1, "the mint must land by one rename of a finished temp file")
        src, dst, mode, body, ino, synced_before_swap = swaps[0]
        self.assertIn(ino, synced_before_swap, "the temp is fsynced BEFORE it is swapped in, or a crash right after the rename can leave an empty token")
        self.assertEqual(dst, str(self.f))
        self.assertEqual(mode, 0o600, "the temp is born 0600 under a 022 umask: the open mode, not the umask, decides")
        self.assertEqual(body, tok, "the temp already holds the whole token when it is swapped in")
        self.assertTrue(Path(src).name.startswith("serve-token.") and src.endswith(".tmp"), src)
        self.assertEqual(Path(src).parent, self.f.parent, "same directory, so the rename is atomic")
        self.assertFalse(Path(src).exists(), "the temp is gone after the swap")
        live = [flags for path, flags in opens if path == str(self.f)]
        self.assertEqual(live, [], "the live token path is never opened for writing at all")
        self.assertEqual(_mode(self.f), 0o600)
        self.assertEqual(self.f.read_text(), tok)


class UnreadableIsNotRotated(_TokenFile):
    @unittest.skipIf(os.geteuid() == 0, "root reads through mode 0, so the fault cannot be staged")
    def test_an_unreadable_existing_token_is_a_fault_not_a_rotation(self):
        """An existing token the loader cannot read is the one case that must NOT mint: every
        client still holds the old value, and a fresh one would strand them all. Expected first
        error on the old loader: RuntimeError not raised (it returned a new random token while the
        file kept the old one, which is exactly the strand)."""
        self.f.write_text("old-token-DO-NOT-USE\n")
        os.chmod(self.f, 0)
        with self.assertRaises(RuntimeError) as cm:
            km._load_token()
        msg = str(cm.exception)
        self.assertIn(str(self.f), msg, "the refusal names the file to fix")
        self.assertIn("EACCES", msg, "and the errno, by name")
        self.assertIn("NOT replace", msg, "and says the token every client holds is still the token")
        os.chmod(self.f, 0o600)
        self.assertEqual(self.f.read_text(), "old-token-DO-NOT-USE\n", "the file is untouched, byte for byte")
        self.assertEqual(self._temps(), [], "no temp left behind by a mint that must not have started")

    def test_a_token_that_is_not_utf8_text_is_a_fault_not_a_rotation(self):
        """A read fault that is not an OSError: bytes that do not decode. It must reach the same
        refusal, naming the path, with the file untouched. Old loader: UnicodeDecodeError escaped
        its `except OSError` and the loader raised THAT (the case errors rather than fails on it)."""
        raw = b"\xff\xfe\x00tok"
        self.f.write_bytes(raw)
        os.chmod(self.f, 0o600)
        with self.assertRaises(RuntimeError) as cm:
            km._load_token()
        self.assertIn(str(self.f), str(cm.exception), "the refusal names the file to fix")
        self.assertIn("UnicodeDecodeError", str(cm.exception), "and what was wrong with it")
        self.assertEqual(self.f.read_bytes(), raw, "the file is untouched, byte for byte")
        self.assertEqual(self._temps(), [])


class StaleTempsAreSwept(_TokenFile):
    def test_another_pids_crashed_temp_is_removed_by_the_next_mint(self):
        """A `serve-token.<pid>.tmp` left by a starter that died between open and rename is swept
        before the next mint writes its own: the lock is held, so any temp beside the token is a
        crashed attempt, whichever pid named it. Old loader: no temps at all, so the planted file
        simply stays (assertFalse on its existence fails)."""
        stale = self.f.with_name("serve-token.99999.tmp")
        stale.write_text("half-written-DO-NOT-USE")
        tok = km._load_token()
        self.assertFalse(stale.exists(), "the stale temp is gone")
        self.assertEqual(self.f.read_text(), tok, "and the token was minted")
        self.assertEqual(_mode(self.f), 0o600)
        self.assertEqual(self._temps(), [])


class TightenMode(_TokenFile):
    def test_a_loose_existing_token_is_tightened_and_returned_unchanged(self):
        """A readable token at 0644 (written by hand, or by a loader older than the 0600 open) is
        the gate with the door open. Its VALUE is fine, so it is kept and the mode is repaired, and
        the repair is said once on stderr. Expected first error on the old loader: 0o644 != 0o600,
        since it only chmod'd on the mint path and a readable file never reached it."""
        self.f.write_text("keep-me-DO-NOT-USE\n")
        os.chmod(self.f, 0o644)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tok = km._load_token()
        self.assertEqual(tok, "keep-me-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o600)
        self.assertEqual(self.f.read_text(), "keep-me-DO-NOT-USE\n", "tightening the mode rewrites nothing")
        self.assertIn("644", err.getvalue())
        self.assertIn("0600", err.getvalue())
        self.assertEqual(self._temps(), [], "no mint happened")

    def test_a_tighter_token_is_left_alone(self):
        """0400 has no bit outside 0600, so there is nothing to tighten. The check used to be EQUALITY
        with 0600, which widened a read-only token to 0600 and reported the change as a repair
        (review find, 2026-09-08). Expected first error on that shape: 0o600 != 0o400."""
        self.f.write_text("tight-DO-NOT-USE\n")
        os.chmod(self.f, 0o400)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._load_token(), "tight-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o400, "a mode tighter than 0600 is not widened")
        self.assertEqual(err.getvalue(), "", "and nothing is said: nothing was changed")
        self.assertEqual(self.f.read_text(), "tight-DO-NOT-USE\n")

    def test_only_the_bits_outside_0600_are_stripped(self):
        """0640 keeps its owner bits and loses the group read; 0444 keeps the owner's read only. The
        repair removes what is loose and adds nothing, and the notice names both modes. Expected
        first error on the equality shape: 0o600 != 0o400 at the 0444 case (it was widened)."""
        for before, after in ((0o640, 0o600), (0o444, 0o400)):
            with self.subTest(mode="%04o" % before):
                self._clear()
                self.f.write_text("keep-me-DO-NOT-USE\n")
                os.chmod(self.f, before)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(km._load_token(), "keep-me-DO-NOT-USE")
                self.assertEqual(_mode(self.f), after)
                self.assertIn("%04o" % before, err.getvalue(), "the notice names the mode it found")
                self.assertIn("%04o" % after, err.getvalue(), "and the one it left")
                self.assertEqual(self.f.read_text(), "keep-me-DO-NOT-USE\n", "tightening the mode rewrites nothing")
                self.assertEqual(self._temps(), [])

    def test_a_good_0600_token_is_returned_silently(self):
        self.f.write_text("fine-DO-NOT-USE\n")
        os.chmod(self.f, 0o600)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._load_token(), "fine-DO-NOT-USE")
        self.assertEqual(err.getvalue(), "", "the ordinary boot says nothing")
        self.assertEqual(_mode(self.f), 0o600)


class EmptyFileMints(_TokenFile):
    def test_an_empty_or_whitespace_file_is_a_torn_mint_and_is_minted_over_aloud(self):
        """A 0-byte (or whitespace-only) token file is treated as ABSENT, not as a fault: no client
        can be holding a token that was never written, so nothing is stranded by minting over it,
        and the old in-place shape could leave exactly this behind. It is said on stderr because
        the file's existence is evidence of an earlier interrupted start. Expected first error on
        the old loader: 'empty' not found in '' (it minted, but silently)."""
        for body in ("", "  \n"):
            self._clear()
            self.f.write_text(body)
            os.chmod(self.f, 0o644)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                tok = km._load_token()
            self.assertTrue(tok, "a token was minted")
            self.assertEqual(self.f.read_text(), tok)
            self.assertEqual(_mode(self.f), 0o600, "the swapped-in inode is born 0600; the loose mode left with the old one")
            self.assertIn("empty", err.getvalue(), "the torn earlier mint is said on stderr")
            self.assertEqual(self._temps(), [])


class LockFailureIsFailClosed(_TokenFile):
    def test_no_lock_tolerates_only_a_good_0600_token(self):
        """A directory planted at the lock path makes os.open(O_RDWR|O_CREAT) fail with EISDIR, the
        same way an unwritable state dir or a foreign file there would. Without the lock the loader
        may return an existing, non-empty, 0600 token (nothing to mint, nothing to tighten) and must
        refuse everything else: minting or chmod'ing unlocked is the race again. Expected first
        error on the old loader: RuntimeError not raised at (b), since it had no lock to fail."""
        self.lock.mkdir()
        # (a) a good token: returned as is, and the missing lock is said on stderr
        self.f.write_text("good-DO-NOT-USE")
        os.chmod(self.f, 0o600)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._load_token(), "good-DO-NOT-USE")
        self.assertIn(str(self.lock), err.getvalue())
        # (b) a loose token: cannot be tightened without the lock, so it is a refusal, and no chmod happens
        os.chmod(self.f, 0o644)
        with self.assertRaises(RuntimeError) as cm:
            km._load_token()
        self.assertIn(str(self.lock), str(cm.exception), "the refusal names the lock path")
        self.assertIn("EISDIR", str(cm.exception))
        self.assertIn("0644", str(cm.exception), "and says what the lock was needed for: tightening that mode")
        self.assertEqual(_mode(self.f), 0o644, "no unlocked write of any kind, not even a chmod")
        # (d) a TIGHTER token (0400): nothing to tighten either, so it is returned under the missing
        # lock like a 0600 one (the equality check refused it: review find, 2026-09-08)
        os.chmod(self.f, 0o400)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._load_token(), "good-DO-NOT-USE")
        self.assertEqual(_mode(self.f), 0o400)
        # (c) no token: cannot be minted without the lock, so it is a refusal, and nothing appears.
        # The refusal names the LOCK and the fault it hit, and says there is no token file: the old
        # message sent the operator to make a file that did not exist 0600 (review find, 2026-09-08).
        self.f.unlink()
        with self.assertRaises(RuntimeError) as cm:
            km._load_token()
        msg = str(cm.exception)
        self.assertIn(str(self.lock), msg, "the lock is the thing to repair, and it is named")
        self.assertIn("EISDIR", msg, "with the actual fault")
        self.assertIn("no token file", msg, "and the fact that there is nothing on disk to fall back on")
        self.assertNotIn("Make the file yours", msg, "it does not send the operator to fix a token file that does not exist")
        self.assertFalse(self.f.exists(), "nothing minted unlocked")
        self.assertEqual(self._temps(), [])


class LockWaitIsAnnounced(_TokenFile):
    def test_a_starter_blocked_by_another_holder_says_so_before_waiting(self):
        """The kernel and the bus boot together, so the second starter blocks on serve-token.lock
        until the first has read or minted. Blocking flock is the right (event-based) wait, but it
        said nothing, so a starter stuck behind a wedged holder looked hung (review find,
        2026-09-08): the lock is probed LOCK_NB first and one stderr line names it before the
        blocking take. Expected first error on the silent shape: the line never appears while the
        loader sits in flock, and the wait below runs out."""
        self.f.write_text("held-DO-NOT-USE\n")
        os.chmod(self.f, 0o600)
        holder = os.open(str(self.lock), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)   # a separate open file description, so the loader's flock conflicts
        err, out = io.StringIO(), []

        def run():
            with contextlib.redirect_stderr(err):
                out.append(km._load_token())

        t = threading.Thread(target=run, daemon=True)
        try:
            t.start()
            deadline = time.monotonic() + 5
            while str(self.lock) not in err.getvalue() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIn(str(self.lock), err.getvalue(), "the wait is said on stderr, naming the lock")
            self.assertIn("waiting", err.getvalue())
            self.assertTrue(t.is_alive(), "and the starter IS waiting: the line announces the block, it does not skip it")
            self.assertEqual(out, [])
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        t.join(10)
        self.assertEqual(out, ["held-DO-NOT-USE"], "the holder's release lets it through to the token")


class SymlinkIsRefused(_TokenFile):
    def test_a_symlink_at_the_token_path_is_a_fault_and_its_target_is_untouched(self):
        """chmod and stat follow a link, so a symlink at the token path had the loader reading some
        other file as the token and rewriting THAT file's mode (review find, 2026-09-08). A link is
        refused outright by lstat before any read, naming the path; the target keeps its bytes and
        its mode. Expected first error on the following shape: RuntimeError not raised (it returned
        the target's content and chmod'd the target to 0600)."""
        target = km.jd.STATE / "elsewhere-DO-NOT-USE"
        target.write_text("linked-DO-NOT-USE\n")
        os.chmod(target, 0o644)
        self.addCleanup(target.unlink)
        self.f.symlink_to(target)
        with self.assertRaises(RuntimeError) as cm:
            km._load_token()
        msg = str(cm.exception)
        self.assertIn(str(self.f), msg, "the refusal names the token path")
        self.assertIn("symlink", msg, "and says what is wrong with it")
        self.assertEqual(_mode(target), 0o644, "the target's mode is not rewritten through the link")
        self.assertEqual(target.read_text(), "linked-DO-NOT-USE\n", "nor its bytes")
        self.assertTrue(self.f.is_symlink(), "the link is left for the operator to remove")
        self.assertEqual(self._temps(), [])

    def test_a_dangling_symlink_is_refused_too_not_minted_over(self):
        """A link to nothing reads as FileNotFoundError, the one mint trigger, so the following shape
        minted and os.replace'd the fresh file over the link. It is a symlink all the same, and the
        same refusal; nothing is minted."""
        self.f.symlink_to(km.jd.STATE / "nowhere-DO-NOT-USE")
        with self.assertRaises(RuntimeError) as cm:
            km._load_token()
        self.assertIn("symlink", str(cm.exception))
        self.assertTrue(self.f.is_symlink(), "the link is still a link: nothing was renamed over it")
        self.assertFalse(self.f.exists(), "and nothing was minted at its target")
        self.assertEqual(self._temps(), [])


class TempIsExclusive(_TokenFile):
    def test_a_file_already_at_this_pids_temp_path_is_not_written_over(self):
        """O_EXCL on the temp was pinned by source text alone, and an O_EXCL-to-O_TRUNC mutant passed
        every behavioural case, because the sweep removes any temp before the open ever meets one
        (review find, 2026-09-08). With the sweep held off (os.unlink interposed to leave the planted
        file), a file already at this pid's temp path must make the open fail EEXIST, not be
        truncated and written over. Expected first error on the O_TRUNC mutant: RuntimeError not
        raised (the planted bytes were replaced by a fresh token and the mint went through)."""
        planted = self.f.with_name("serve-token.%d.tmp" % os.getpid())
        planted.write_text("planted-DO-NOT-USE")
        real_unlink = os.unlink

        def _keep_planted(path, *a, **k):
            if str(path) == str(planted):
                return                        # the sweep (and the failed mint's cleanup) would remove it
            return real_unlink(path, *a, **k)

        os.unlink = _keep_planted
        try:
            with self.assertRaises(RuntimeError) as cm:
                km._load_token()
        finally:
            os.unlink = real_unlink
        self.assertIn("EEXIST", str(cm.exception), "the exclusive open met the existing file and said so")
        self.assertEqual(planted.read_text(), "planted-DO-NOT-USE", "the existing temp was not truncated or written")
        self.assertFalse(self.f.exists(), "and no token was minted from it")


class CheckinCarriesServedToken(_TokenFile):
    """The check-in handshake hands the hub the token this kernel SERVES: `TOKEN`, the value the request
    gate compares against (ROMP_SERVE_TOKEN is already folded into it at import). `_checkin_payload`
    used to call `_load_token()` at runtime, so a token file missing at handshake time minted a fresh
    one onto disk and announced it: the hub and every local reader of the file then held a token this
    kernel refused; and a read fault there raised into `_checkin_handshake`'s broad except and became
    a silent 15 s retry (review find, 2026-09-08). Expected first error on that shape: the file exists
    after the call, holding a token that is not km.TOKEN."""
    ROW = {"rk_port": 50003, "rb_port": 50004, "local_port": 50001}

    def setUp(self):
        super().setUp()
        self._host = os.environ.get("ROMP_HOST_NAME")
        os.environ["ROMP_HOST_NAME"] = "TESTHOST"    # keeps _self_host() off the machine's name and its fallback file

    def tearDown(self):
        if self._host is None:
            os.environ.pop("ROMP_HOST_NAME", None)
        else:
            os.environ["ROMP_HOST_NAME"] = self._host

    def test_with_no_token_file_the_payload_carries_TOKEN_and_mints_nothing(self):
        p = km._checkin_payload(dict(self.ROW))
        self.assertEqual(p["token"], km.TOKEN, "the hub gets the token this kernel's gate accepts")
        self.assertFalse(self.f.exists(), "the handshake never mints: a runtime mint is a token nobody serves")
        self.assertEqual(self._temps(), [])
        self.assertFalse(self.lock.exists(), "nor takes the lock: the handshake touches nothing on disk")

    @unittest.skipIf(os.geteuid() == 0, "root reads through mode 0, so the fault cannot be staged")
    def test_an_unreadable_token_file_is_neither_read_nor_a_swallowed_refusal(self):
        self.f.write_text("on-disk-DO-NOT-USE\n")
        os.chmod(self.f, 0)
        p = km._checkin_payload(dict(self.ROW))     # must not raise: the payload reads no file at all
        self.assertEqual(p["token"], km.TOKEN)
        os.chmod(self.f, 0o600)
        self.assertEqual(self.f.read_text(), "on-disk-DO-NOT-USE\n", "the file is untouched")
        self.assertEqual(self._temps(), [])
        self.assertFalse(self.lock.exists(), "the handshake never touches the file, so it never needs the lock")


class ImportRefusalIsLoud(unittest.TestCase):
    """The docstrings say a read fault at import refuses to start the daemon, and that under
    bin/romp-manager the refusal repeats on the respawn backoff until the file is repaired (review find,
    2026-09-08: the sentence used to call it one visible message). The import half is pinned here, in a
    subprocess since the in-process `km` is already loaded: a mode-0 token under a fresh ROMP_STATE_DIR
    makes the kernel's import exit non-zero with the refusal on stderr, and the file's bytes and mode
    are as they were, no temp minted beside it. The manager half is bin/romp-manager's own contract
    (MAX_BACKOFF_MS), not this module's. Expected first error on a loader that mints over a read fault:
    returncode 0, the file holding a fresh token."""

    @unittest.skipIf(os.geteuid() == 0, "root reads through mode 0, so the fault cannot be staged")
    def test_a_read_fault_at_import_exits_nonzero_with_the_refusal_and_leaves_the_file(self):
        state = Path(tempfile.mkdtemp())
        f = state / "serve-token"
        f.write_text("old-token-DO-NOT-USE\n")
        os.chmod(f, 0)
        self.addCleanup(lambda: (os.chmod(f, 0o600), f.unlink()))
        env = dict(os.environ)
        env.pop("ROMP_SERVE_TOKEN", None)    # with it set the loader never touches the file
        env["ROMP_STATE_DIR"] = str(state)
        env["ROMP_KERNEL_NO_OPEN"] = "1"
        loads = [("romp_event_model", os.path.join(BIN, "romp-event-model")),
                 ("romp_judge", os.path.join(BIN, "romp-judge")),
                 ("romp_kernel", os.path.join(BIN, "romp-kernel"))]   # the same three loads as this module's own
        code = ("from importlib.machinery import SourceFileLoader\n"
                "for name, path in %r:\n"
                "    SourceFileLoader(name, path).load_module()\n" % (loads,))
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0, "the import refuses to start the kernel; stderr:\n%s" % r.stderr[-2000:])
        self.assertIn("did NOT replace the serve token", r.stderr, "and says so, in the loader's own words")
        self.assertIn(str(f), r.stderr, "naming the path")
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0, "the file's mode is as it was")
        os.chmod(f, 0o600)
        self.assertEqual(f.read_text(), "old-token-DO-NOT-USE\n", "and so are its bytes: nothing was rotated")
        self.assertEqual(sorted(p.name for p in state.glob("serve-token.*.tmp")), [],
                         "no temp beside it: nothing was minted (the 0600 lock the read was taken under may stay)")


class ServeTokenLoadersMatch(unittest.TestCase):
    """The bus imports nothing from kernel/ (by design: it is one self-contained file, and the two
    daemons boot together), so postal_service.py carries its OWN copy of _serve_token_read_or_mint.
    Two layers pin the copies together. The AST identity (docstring stripped) is the gate: ANY
    divergence is red, so a fix to one copy that forgets the other cannot land. The named invariants
    are the readable layer that says WHAT a divergence broke. The gate exists because three
    postal-only mutants got past the invariants alone in review: the tighten removed, the
    lock-failure arm returning any non-empty token regardless of mode, and the empty file raising
    instead of minting; each is also killed by its own postal case in tests/test_postal_token.py."""
    KERNEL = Path(HERE).parent / "kernel" / "kernel.py"
    POSTAL = Path(HERE).parent / "postal" / "postal_service.py"

    @staticmethod
    def _body(src, name):
        m = re.search(r"^def %s\(.*?(?=^\S)" % re.escape(name), src, re.S | re.M)
        assert m, "no top-level def %s" % name
        return m.group(0)

    @staticmethod
    def _func(path, name):
        """The top-level def as an AST, with a leading docstring removed (the kernel's copy carries
        the reasoning; the bus's copy points at it)."""
        for node in ast.parse(path.read_text(), filename=str(path)).body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    node.body = node.body[1:]
                return node
        raise AssertionError("no top-level def %s in %s" % (name, path.name))

    def test_both_copies_are_one_function_once_the_docstring_is_stripped(self):
        k = self._func(self.KERNEL, "_serve_token_read_or_mint")
        p = self._func(self.POSTAL, "_serve_token_read_or_mint")
        # unparse first for a readable line diff; ast.dump is the strict gate (it sees what unparse normalizes)
        self.assertEqual(ast.unparse(k).splitlines(), ast.unparse(p).splitlines(),
                         "the bus's copy has drifted from the kernel's")
        self.assertEqual(ast.dump(k), ast.dump(p))

    def test_both_copies_keep_the_invariants(self):
        for path in (self.KERNEL, self.POSTAL):
            body = self._body(path.read_text(), "_serve_token_read_or_mint")
            with self.subTest(file=path.name):
                self.assertIn("fcntl.flock(", body, "the read-or-mint runs under an flock")
                self.assertIn("fcntl.LOCK_EX", body)
                self.assertIn('".lock"', body, "the lock is the sibling serve-token.lock")
                self.assertIn("except FileNotFoundError", body, "absence is the ONLY mint trigger")
                self.assertIn("RuntimeError", body, "any other fault is a refusal, never a rotation")
                self.assertIn("os.O_EXCL", body, "the temp is created exclusively")
                self.assertIn("0o600", body)
                self.assertIn("n != len(data)", body, "the short-write check")
                self.assertIn("os.fsync(", body)
                self.assertIn("os.replace(", body, "the mint lands by rename")
                self.assertIn("os.lstat(", body, "the mode is the token's own: a link at the path is refused, never followed")
                self.assertNotIn("os.stat(", body, "stat follows a link")
                self.assertIn("fcntl.LOCK_NB", body, "the lock is probed before the blocking take, so a wait is said")
                self.assertNotIn("O_TRUNC", body, "the live path is never truncated")
                self.assertNotIn("write_text(", body, "no umask-mode write of the token")

    def test_both_loaders_route_through_the_shared_shape(self):
        k = self._body(self.KERNEL.read_text(), "_load_token")
        p = self._body(self.POSTAL.read_text(), "_load_serve_token")
        self.assertIn("_serve_token_read_or_mint(", k)
        self.assertIn("_serve_token_read_or_mint(", p)
        for body in (k, p):
            self.assertNotIn("read_text(", body, "the loader itself touches no file; the helper does, under the lock")


if __name__ == "__main__":
    unittest.main(verbosity=2)
