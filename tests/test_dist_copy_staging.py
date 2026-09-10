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
unnoticed. A guard closes the module, reading each test module's call tree: none copies the built
dist/ with a plain copytree, reached as an attribute, by the bare name or by an imported alias and
however its arguments nest, since the race fails no test of the class that does until a parallel run
lands on it. All fixtures synthetic.
"""
import ast
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


def raw_dist_copies(text, filename="<test module>"):
    """The (line, source) of every copytree call in a test module's `text` whose arguments name dist.

    A module whose text lacks the token holds no such call and is not parsed, so the guard's cost over
    every test module stays at a text scan's. The rest are read as a call tree: a call of `copytree` as
    an attribute of whatever it is reached through (shutil, shutil under another name, an object holding
    it), of the name `copytree` itself, or of a name `from shutil import copytree as ...` binds, wherever
    the call sits and however deeply its arguments nest. The source of every argument and keyword value
    is searched for "dist" in any case, so a path bound to a name such as DIST is caught with the
    literal. A string holding a call is not a call. Hits come in line order; text that carries the token
    and does not parse raises SyntaxError under `filename`.
    """
    if "copytree" not in text:
        return []
    tree = ast.parse(text, filename)
    names = {"copytree"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "shutil":
            names.update(a.asname for a in node.names if a.name == "copytree" and a.asname)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr != "copytree":
                continue
        elif isinstance(func, ast.Name):
            if func.id not in names:
                continue
        else:
            continue
        values = node.args + [k.value for k in node.keywords]
        arguments = " ".join(ast.get_source_segment(text, v) or "" for v in values)
        if "dist" in arguments.lower():
            hits.append((node.lineno, ast.get_source_segment(text, node)))
    return sorted(hits)


def raw_dist_copies_under(directory):
    """The roster of raw dist copies in every test_*.py under `directory`: `module:line: call`, in path order."""
    raw = []
    for p in sorted(glob.glob(os.path.join(directory, "test_*.py"))):
        with open(p, encoding="utf-8") as f:
            text = f.read()
        for line, source in raw_dist_copies(text, p):
            raw.append("%s:%d: %s" % (os.path.basename(p), line, source))
    return raw


class EveryTestModuleCopiesTheBuiltDistThroughCopyDist(unittest.TestCase):
    """A guard. The race is between two classes' builds and lands on a plain copy in about one parallel run in
    two, so a class that copies the built dist/ with shutil.copytree itself fails no test until a run lands
    on it. This reads each test module's call tree and refuses a copytree call whose arguments name dist,
    however the arguments nest, whether the call is reached as an attribute, by name or by an imported
    alias."""

    def test_no_test_module_copies_dist_with_a_plain_copytree(self):
        raw = raw_dist_copies_under(HERE)
        self.assertEqual(raw, [], "copy the built dist/ with tests.dist_copy.copy_dist, which skips a "
                                  "concurrent build's staging files:\n" + "\n".join(raw))

    def test_the_scan_reads_a_nested_call_an_aliased_import_and_a_module_alias(self):
        # Synthetic modules. The function's name is spelled in two pieces so that this module holds no
        # copytree call that names dist for a reader of its raw text; the scan reads calls, and a string
        # holding one (the last clean module) is not a call.
        ct = "copy" + "tree"
        raw = [
            'import shutil\nshutil.%s(os.path.join(EXT, "dist"), dist)' % ct,
            'import shutil\nshutil.%s(os.path.join(os.path.dirname(EXT), "dist"), dist)' % ct,
            'import shutil\nshutil.%s(\n    os.path.join(EXT, "dist"),\n    os.path.join(cls.lab, "dist"),\n)' % ct,
            'import shutil\nshutil.%s(src=os.path.join(EXT, "dist"), dst=dist)' % ct,
            'import shutil\nshutil.%s(built, os.path.join(cls.lab, "dist"))' % ct,
            'from shutil import %s as ct\nct(os.path.join(EXT, "dist"), dist)' % ct,
            'from shutil import *\n%s(os.path.join(EXT, "dist"), dist)' % ct,
            'import shutil as sh\nsh.%s(os.path.join(EXT, "dist"), dist)' % ct,
            'DIST = os.path.join(EXT, "dist")\nshutil.%s(DIST, dst)' % ct,
        ]
        clean = [
            'from tests.dist_copy import copy_dist\ncopy_dist(os.path.join(EXT, "dist"), dist)',
            'import shutil\nshutil.%s(main, backup, symlinks=True)' % ct,
            'import shutil\nshutil.%s(src, dst, ignore=shutil.ignore_patterns(".*.tmp-*"))' % ct,
            'from unittest import mock\nmock.patch.object(shutil, "%s", side_effect=vanished)' % ct,
            'text = \'shutil.%s(os.path.join(EXT, "dist"), dist)\'' % ct,
        ]
        # every raw module: one hit, at the call's first line (the second line of each); every clean one: none
        lines = {text: [line for line, _source in raw_dist_copies(text)] for text in raw + clean}
        self.maxDiff = None
        self.assertEqual(lines, {**{text: [2] for text in raw}, **{text: [] for text in clean}})
        # the roster's source is the call as written, from the module's name to the closing parenthesis
        self.assertEqual(raw_dist_copies(raw[2]), [(2, raw[2].split("\n", 1)[1])])
        # wherever the call sits, and in line order: a call inside a function is read, and listed before
        # the module-level call below it (a walk of the tree reaches the shallower call first)
        nested = 'import shutil\ndef build():\n    shutil.%s(src, "dist")\nshutil.%s(EXT + "/dist", dst)' % (ct, ct)
        self.assertEqual([line for line, _source in raw_dist_copies(nested)], [3, 4])
        # a module whose text lacks the token is not parsed: unparseable text without it is not this
        # guard's to refuse, and text with it that does not parse fails under the module's name
        self.assertEqual(raw_dist_copies("def ("), [])
        with self.assertRaises(SyntaxError) as cm:
            raw_dist_copies("shutil.%s(" % ct, "test_x.py")
        self.assertEqual(cm.exception.filename, "test_x.py")

    def test_the_walk_names_the_module_line_and_call_of_a_raw_copy(self):
        # a scratch tests directory: one module routes its copy through copy_dist, one copies with a
        # two-level plain call inside setUpClass; the roster names the second by module, line and call
        ct = "copy" + "tree"
        d = tempfile.mkdtemp(prefix="dist-guard-")
        self.addCleanup(shutil.rmtree, d)
        call = 'shutil.%s(os.path.join(os.path.dirname(EXT), "dist"), cls.dist)' % ct
        modules = {
            "test_routed.py": 'from tests.dist_copy import copy_dist\n\n\ndef setUpClass(cls):\n'
                              '    copy_dist(os.path.join(EXT, "dist"), cls.dist)\n',
            "test_served.py": 'import os\nimport shutil\n\n\ndef setUpClass(cls):\n    %s\n' % call,
        }
        for name, text in modules.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
        self.assertEqual(raw_dist_copies_under(d), ["test_served.py:6: " + call])


if __name__ == "__main__":
    unittest.main()
