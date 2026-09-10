#!/usr/bin/env python3
"""Rewrite test modules from `SourceFileLoader(name, path).load_module()` to `load_source(name, path)`.

`load_module()` warns on Python 3.10 and later and its removal is documented for 3.15.
kernel/loadsource.py is the replacement, and a test module reaches it as
`from romp_load import load_source` (tests/romp_load.py). This tool converts a module mechanically,
from its AST, so the conversion of the whole directory is regenerated rather than merged: run it
again after a branch lands a module in the old idiom, and commit the result. A converted module comes
back unchanged, so a run over the whole directory is safe at any time;
tests/test_state_isolation_order.py refuses the old idiom until the tool has been run.

Per module, from the AST (never text inside a string or a comment):
- every `X(<args>).load_module()` where X is a name bound by `from importlib.machinery import
  SourceFileLoader [as X]`, at any depth, becomes `load_source(<args>)`, the arguments verbatim;
- an import whose name is then out of use goes: a top-level statement importing only that name
  becomes `from romp_load import load_source`, one that imports other names too keeps them and gains
  that line after it, one inside a function is deleted; a name still used in code (a loader built
  for another purpose) keeps its import, since only load_module() is removed;
- a module that converted a call and has no top-level `from romp_load import load_source` gets one,
  after the last import of its leading import block, so a converted call inside a function resolves
  the name as a module global.

Outside its reach, reported for a hand edit: a `load_module()` call reached other than through the
imported name (`importlib.machinery.SourceFileLoader(...)`, a loader held in a variable), a call
with arguments, and a module the rewrite would leave unparseable (a function whose whole body is the
import). Invisible to it, as to the refusal: the idiom inside a string handed to a child process;
convert that by hand. One more thing is the module's own to carry: `romp_load` resolves under pytest
(the package's __init__.py registers it) and in a script run from tests/, but a module that another
test executes by file path from outside tests/ puts its own directory on sys.path first, as
tests/smoke_codex_live.py does; the tool never removes such a line.

Usage: tools/loadsource-sweep.py [--check] [path ...]
  Each path is a file or a directory (every *.py in it, sorted); no path means tests/. The files in
  SKIP are never rewritten, even when named, and each is reported with its reason.
  --check rewrites nothing, names each file that would change, and exits 1 when one would (or a
  leftover needs a hand edit); exit 0 otherwise. Without --check a leftover is the only nonzero exit.
"""
import argparse
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TESTS = os.path.join(ROOT, "tests")
IMPORT_LINE = "from romp_load import load_source"

# The suite's own plumbing: never rewritten, even when named on the command line, each with the
# reason printed when it is met. tests/test_state_isolation_order.py's LOADER_SKIP is the same list,
# and tests/test_loadsource_sweep.py pins the two equal.
SKIP = {
    "romp_load.py": "the loader helper itself; it defines load_source",
    "__init__.py": "the package's state floor, which registers romp_load; edited by hand",
    "conftest.py": "pytest's state floor and report hook; edited by hand",
    "credential_patterns.py": "the report hook's pattern table; edited by hand",
}


class Unconvertible(Exception):
    """The rewrite would leave the module unparseable; nothing is written."""


def _loader_imports(tree):
    """(the names bound to SourceFileLoader, the ImportFrom nodes that bind them)."""
    names, nodes = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib.machinery":
            bound = [a.asname or a.name for a in node.names if a.name == "SourceFileLoader"]
            if bound:
                names.update(bound)
                nodes.append(node)
    return names, nodes


def _load_calls(tree, names):
    """Every `X(...).load_module()` node with X one of `names` and no arguments to load_module()."""
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and not node.args and not node.keywords):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "load_module" and isinstance(f.value, ast.Call)
                and isinstance(f.value.func, ast.Name) and f.value.func.id in names):
            out.append(node)
    return out


def leftovers(tree):
    """The line of every `<expr>.load_module()` call in a parsed module, in source order: what the
    conversion did not reach. The same scan as tests/test_state_isolation_order.py's
    removed_loader_sites (tools/ cannot import from tests/); tests/test_loadsource_sweep.py pins them
    equal."""
    return sorted(node.lineno for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "load_module")


def _names_used(tree, names):
    """The alias names that occur as expressions; an ImportFrom binds through alias nodes, not
    ast.Name, so the import statements themselves never count."""
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in names}


def _has_import_line(tree):
    return any(isinstance(n, ast.ImportFrom) and n.module == "romp_load"
               and any(a.name == "load_source" for a in n.names) for n in tree.body)


def _line_starts(data):
    starts = [0]
    for i, b in enumerate(data):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _offset(starts, lineno, col):
    """ast positions are 1-based lines and UTF-8 byte columns."""
    return starts[lineno - 1] + col


def _rewrite_calls(src, calls):
    """`X(<args>).load_module()` to `load_source(<args>)`, by byte span, last edit first."""
    data = src.encode("utf-8")
    starts = _line_starts(data)
    edits = []
    for node in calls:
        ctor = node.func.value                      # X(<args>)
        start = _offset(starts, ctor.func.lineno, ctor.func.col_offset)
        args_from = _offset(starts, ctor.func.end_lineno, ctor.func.end_col_offset)
        args_to = _offset(starts, ctor.end_lineno, ctor.end_col_offset)
        end = _offset(starts, node.end_lineno, node.end_col_offset)
        edits.append((start, end, b"load_source" + data[args_from:args_to]))
    for start, end, text in sorted(edits, reverse=True):
        data = data[:start] + text + data[end:]
    return data.decode("utf-8")


def _owns_its_lines(node, lines):
    """True when the statement is alone on its lines (only whitespace before it, only whitespace or
    a comment after it), so the lines can be replaced whole."""
    if lines[node.lineno - 1][:node.col_offset].strip():
        return False
    tail = lines[node.end_lineno - 1][node.end_col_offset:].strip()
    return not tail or tail.startswith("#")


def _lines(src):
    """The lines of `src` with their newlines, split on a newline alone, the way ast numbers them
    (str.splitlines also breaks on a form feed and the other separators, which would put every edit
    below one a line early)."""
    return io.StringIO(src).readlines()


def _retire_imports(src):
    """Second pass, over the converted text: drop each import whose name went out of use and make
    sure one top-level `from romp_load import load_source` exists."""
    tree = ast.parse(src)
    names, nodes = _loader_imports(tree)
    used = _names_used(tree, names)
    lines = _lines(src)
    top = {id(n) for n in tree.body}
    need = not _has_import_line(tree)
    edits = []                                      # (first line index, last line index, replacement)
    for node in nodes:
        keep = [a for a in node.names
                if not (a.name == "SourceFileLoader" and (a.asname or a.name) not in used)]
        if len(keep) == len(node.names) or not _owns_its_lines(node, lines):
            # the name stays in use, or the statement shares a line with other code: leave it, and
            # hang the new import after it when the module still lacks one
            if id(node) in top and need:
                edits.append((node.end_lineno, node.end_lineno - 1, IMPORT_LINE + "\n"))
                need = False
            continue
        text = ""
        if keep:
            text = lines[node.lineno - 1][:node.col_offset] + "from importlib.machinery import " + ", ".join(
                a.name + (" as " + a.asname if a.asname else "") for a in keep) + "\n"
        if id(node) in top and need:
            text += IMPORT_LINE + "\n"
            need = False
        edits.append((node.lineno - 1, node.end_lineno - 1, text))
    if need:
        # every retired import was nested: the line goes after the module's leading import block
        # (after the docstring when there is no import at all)
        after = 0
        body = tree.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            after = body[0].end_lineno
            body = body[1:]
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                after = node.end_lineno
            else:
                break
        edits.append((after, after - 1, IMPORT_LINE + "\n"))
    for first, last, text in sorted(edits, key=lambda e: e[0], reverse=True):
        lines[first:last + 1] = [text] if text else []
    return "".join(lines)


def convert(src):
    """The converted source, or `src` itself when nothing applies. Raises Unconvertible when the
    result would not parse; the caller writes nothing then."""
    tree = ast.parse(src)
    names, _nodes = _loader_imports(tree)
    if not names:
        return src
    calls = _load_calls(tree, names)
    if not calls:
        return src
    out = _retire_imports(_rewrite_calls(src, calls))
    try:
        ast.parse(out)
    except SyntaxError as e:
        raise Unconvertible("the rewrite would not parse at line %s (%s)" % (e.lineno, e.msg))
    return out


def display_path(path):
    """Relative to the repo root when under it, else as given."""
    real = os.path.realpath(path)
    if real == ROOT or real.startswith(ROOT + os.sep):
        return os.path.relpath(real, ROOT)
    return path


def _targets(paths):
    """(path, skip reason or None) for every file to visit, directories expanded to their *.py."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            files = [os.path.join(p, fn) for fn in sorted(os.listdir(p)) if fn.endswith(".py")]
        else:
            files = [p]
        out.extend((f, SKIP.get(os.path.basename(f))) for f in files)
    return out


def main(argv):
    ap = argparse.ArgumentParser(
        description="Rewrite test modules from SourceFileLoader(...).load_module() to load_source(...).")
    ap.add_argument("--check", action="store_true",
                    help="rewrite nothing; name each file that would change and exit 1 when one would")
    ap.add_argument("paths", nargs="*", help="files or directories (default: tests/)")
    args = ap.parse_args(argv)
    rewritten, left, skipped = [], [], 0
    for path, reason in _targets(args.paths or [TESTS]):
        shown = display_path(path)
        if reason:
            print("skipped %s: %s" % (shown, reason))
            skipped += 1
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            out = convert(src)
        except Unconvertible as e:
            left.append("%s: %s; convert it by hand" % (shown, e))
            continue
        if out != src:
            rewritten.append(shown)
            if not args.check:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(out)
            print(("would rewrite " if args.check else "rewrote ") + shown)
        left.extend("%s:%d calls load_module(); convert it by hand" % (shown, lineno)
                    for lineno in leftovers(ast.parse(out)))
    for line in left:
        print("left " + line)
    print("%d file(s) %s, %d left for a hand edit, %d skipped"
          % (len(rewritten), "to rewrite" if args.check else "rewritten", len(left), skipped))
    return 1 if left or (args.check and rewritten) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
