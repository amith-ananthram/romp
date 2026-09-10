#!/usr/bin/env python3
"""Copying a built dist/ into a test lab skips the staging files a concurrent build holds.

vscode-extension/esbuild.js writes each bundle whole to a hidden sibling of its served name,
`.<name>.tmp-<pid>-<n>` (stagingPath there), and renames it into place once every output is
staged; a staging file an exited build left behind is removed at the start of the next build. The
served-page test classes under tests/ each run `node esbuild.js` into the shared dist/ and copy it
in setUpClass, and under a parallel local run (`pytest -n`) two of them build at once: one build's
rename or removal can land between another class's listing of dist/ and its copy of that entry,
and shutil.copytree collects the vanished file into a shutil.Error, so the class errors before its
first test. A staging file is never a served asset, so tests/dist_copy.py's copy_dist skips every
name of that shape. Three cases over scratch trees: a staging file present throughout is not
copied; one removed after the listing (a stand-in for os.scandir removes it once the listing is
taken) fails a plain copytree, the defect, and not copy_dist; and every name the script's own
STAGING pattern matches is skipped while the served names are kept, a hidden name of another shape
among them, the pattern read from the script so the copy's shape cannot drift from the script's
unnoticed. A guard closes the module: no test module copies the built dist/ with a plain copytree,
since the race fails no test of the class that does until a parallel run lands on it. All fixtures
synthetic.
"""
import glob
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
ESBUILD = os.path.join(ROOT, "vscode-extension", "esbuild.js")

# The served names a dist/ carries: bundles, a sourcemap, and a file-loader asset in its subdirectory.
SERVED = ["extension.js", "render.js", "render.js.map", os.path.join("fonts", "glyphs.woff2")]


def staging_name(served, pid, n):
    """The hidden name esbuild.js stages `served` under: a dot, the name, `.tmp-`, the pid, a counter."""
    return "." + os.path.basename(served) + ".tmp-%d-%d" % (pid, n)


def _tree(names):
    """A scratch dist/ holding `names` (relative paths, parents created), each file's content its name."""
    d = os.path.join(tempfile.mkdtemp(prefix="dist-copy-"), "dist")
    for rel in names:
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(rel)
    return d


def _files(d):
    out = []
    for base, _dirs, names in os.walk(d):
        out += [os.path.relpath(os.path.join(base, n), d) for n in names]
    return sorted(out)


class _Listed:
    """What copytree takes from os.scandir(src): a context manager it iterates once and closes."""

    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._entries)

    def close(self):
        pass


def _scandir_removing_after_listing(directory, victim):
    """os.scandir with one difference: a listing of `directory` is taken whole and `victim` is then
    removed, which is where a concurrent build's rename lands: the entry is in the listing, its file
    is gone before the copy reaches it. Every other directory is listed as before."""
    real = os.scandir

    def scandir(path=".", *args, **kwargs):
        it = real(path, *args, **kwargs)
        if not isinstance(path, (str, os.PathLike)) or os.path.realpath(path) != os.path.realpath(directory):
            return it
        with it:
            entries = list(it)
        os.remove(victim)
        return _Listed(entries)

    return scandir


class StagingFilesAreNotCopied(unittest.TestCase):
    def test_a_staging_file_present_throughout_is_not_copied(self):
        # a concurrent build mid-write: one staging file beside a bundle, one beside the asset in its subdirectory
        staged = [staging_name("render.js", 4242, 0), os.path.join("fonts", staging_name("glyphs.woff2", 4242, 3))]
        src = _tree(SERVED + staged)
        dst = os.path.join(os.path.dirname(src), "lab-dist")
        copy_dist(src, dst)
        self.assertEqual(_files(dst), sorted(SERVED))
        for rel in SERVED:
            with open(os.path.join(dst, rel), encoding="utf-8") as f:
                self.assertEqual(f.read(), rel)

    def test_a_staging_file_removed_after_the_listing_does_not_fail_the_copy(self):
        staged = staging_name("render.js", 4242, 0)
        src = _tree(SERVED + [staged])
        lab = os.path.dirname(src)
        victim = os.path.join(src, staged)
        stand_in = _scandir_removing_after_listing(src, victim)

        # the defect, with the same stand-in: a plain copytree lists the staging file, finds it gone at the
        # copy, and raises at the end with the served files already copied
        with mock.patch("os.scandir", stand_in):
            with self.assertRaises(shutil.Error) as cm:
                shutil.copytree(src, os.path.join(lab, "plain"))
        self.assertFalse(os.path.exists(victim), "the stand-in removed the staging file after the listing")
        self.assertIn(staged, str(cm.exception))

        # planted again, the copy under test takes the listing with the staging file in it and skips it
        with open(victim, "w", encoding="utf-8") as f:
            f.write(staged)
        dst = os.path.join(lab, "lab-dist")
        with mock.patch("os.scandir", stand_in):
            copy_dist(src, dst)
        self.assertFalse(os.path.exists(victim), "the stand-in fired for the copy under test too")
        self.assertEqual(_files(dst), sorted(SERVED))

    def test_every_name_the_script_stages_is_skipped_and_every_served_name_kept(self):
        # the script's own test for a staging name, read from its source: the copy skips what it matches
        with open(ESBUILD, encoding="utf-8") as f:
            m = re.search(r"^const STAGING = /(.+)/;$", f.read(), re.M)
        self.assertIsNotNone(m, "esbuild.js names its staging shape in STAGING")
        staging = re.compile(m.group(1))   # the expression reads the same in JavaScript and Python
        staged = [staging_name(s, pid, n) for n, s in enumerate(SERVED) for pid in (1, os.getpid(), 4194304)]
        for name in staged:
            self.assertRegex(name, staging)
        for name in map(os.path.basename, SERVED):
            self.assertNotRegex(name, staging)
        # and only that shape: a hidden name the script does not stage (none is served today) is a served file
        # as far as the copy knows, and is kept; the copy skips staging files, not every dotfile
        hidden = ".keep"
        self.assertNotRegex(hidden, staging)

        src = _tree(SERVED + staged + [hidden])
        dst = os.path.join(os.path.dirname(src), "lab-dist")
        copy_dist(src, dst)
        self.assertEqual(_files(dst), sorted(SERVED + [hidden]))


class EveryTestModuleCopiesTheBuiltDistThroughCopyDist(unittest.TestCase):
    """A guard. The race is between two classes' builds and lands on a plain copy in about one parallel run in
    two, so a class that copies the built dist/ with shutil.copytree itself fails no test until a run lands
    on it. This reads the test modules and refuses a copytree call that names dist."""

    def test_no_test_module_copies_dist_with_a_plain_copytree(self):
        raw = []
        for p in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
            with open(p, encoding="utf-8") as f:
                text = f.read()
            # a call and its arguments, one level of nested parentheses deep (os.path.join(EXT, "dist"))
            for m in re.finditer(r"copytree\(((?:[^()]|\([^()]*\))*)\)", text):
                if "dist" in m.group(1):
                    raw.append("%s:%d: %s" % (os.path.basename(p), text.count("\n", 0, m.start()) + 1, m.group(0)))
        self.assertEqual(raw, [], "copy the built dist/ with tests.dist_copy.copy_dist, which skips a "
                                  "concurrent build's staging files:\n" + "\n".join(raw))


if __name__ == "__main__":
    unittest.main()
