"""The filesystem clock, forced: a test that wants a file's ctime to move (a permissions repair the memos must see) cannot
sleep and hope, since a coarse filesystem clock can hand two chmods one timestamp. Shared by the memo tests and the
thread mail-off test (lifted from their identical copies, 2026-09-12)."""
import os
import time


def move_ctime(path):
    """Move a file's ctime and nothing else: flip its mode between 0o600 and 0o644, checking the stat after each
    chmod, until the ctime differs. mtime, size and inode stand. Bounded at 5 s: a filesystem that never ticks ctime
    under chmod fails the test loudly rather than passing it."""
    before = cur = os.stat(path)
    deadline = time.monotonic() + 5
    while cur.st_ctime_ns == before.st_ctime_ns:
        if time.monotonic() > deadline:
            raise AssertionError("ctime did not move under chmod within 5 s")
        os.chmod(path, 0o644 if (cur.st_mode & 0o777) == 0o600 else 0o600)
        cur = os.stat(path)
    return cur
