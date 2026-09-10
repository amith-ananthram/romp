#!/usr/bin/env python3
"""tools/loadsource-sweep.py rewrites a test module from `SourceFileLoader(name, path).load_module()`
to `load_source(name, path)` (tests/romp_load.py) from the AST, and
test_state_isolation_order.py's test_no_test_module_uses_the_removed_loader refuses what it leaves
behind. These tests drive the converter over synthetic modules in each shape the suite's modules
were written in (the top-level import with the three-line preamble, an alias imported inside a
function, the name imported beside another, a guarded `sys.modules.get(...) or ...` load, a call
broken across lines, a form feed above the import), check the idempotence a regenerated commit
depends on, the files it never touches and the reason it prints for each, the --check mode, and the
things it cannot convert: a loader reached other than through the imported name and a load_module()
call with an argument, which it reports for a hand edit, and the idiom inside a string, which is
invisible to it and to the refusal alike and is left as it is by design. Its output is checked against the refusal's own scan and its SKIP against the refusal's
LOADER_SKIP, so the two cannot drift apart.
"""
import ast
import contextlib
import io
import os
import shutil
import tempfile
import unittest

# Hermetic state BEFORE the loads (the ratchet's rule; neither module loaded below reads romp state,
# and the two lines cost nothing).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

from romp_load import load_source  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
tool = load_source("romp_loadsource_tool", os.path.join(ROOT, "tools", "loadsource-sweep.py"))
refusal = load_source("romp_loader_refusal", os.path.join(HERE, "test_state_isolation_order.py"))

OLD_IMPORT = "from importlib.machinery import SourceFileLoader\n"
NEW_IMPORT = "from romp_load import load_source\n"

# The suite's usual module head: a top-level import, the state floor, three loads by the bin/ names.
PREAMBLE_OLD = (
    '#!/usr/bin/env python3\n'
    '"""A module under test."""\n'
    'import os\n'
    'import tempfile\n'
    'import unittest\n'
    + OLD_IMPORT +
    '\n'
    'HERE = os.path.dirname(os.path.realpath(__file__))\n'
    'BIN = os.path.join(os.path.dirname(HERE), "bin")\n'
    '\n'
    'os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()\n'
    'SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()\n'
    'km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()\n'
    '\n'
    '\n'
    'class T(unittest.TestCase):\n'
    '    def test_x(self):\n'
    '        self.assertTrue(km)\n'
)
PREAMBLE_NEW = (PREAMBLE_OLD.replace(OLD_IMPORT, NEW_IMPORT)
                .replace('SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()',
                         'load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))')
                .replace('SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()',
                         'load_source("romp_judge", os.path.join(BIN, "romp-judge"))')
                .replace('SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()',
                         'load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))'))

# An alias bound inside a test method, the module's only import of the name.
NESTED_OLD = (
    'import os\n'
    'import tempfile\n'
    'import unittest\n'
    '\n'
    'BIN = "bin"\n'
    'os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    '\n'
    '\n'
    'class T(unittest.TestCase):\n'
    '    def test_x(self):\n'
    '        import os as _os\n'
    '        from importlib.machinery import SourceFileLoader as _L\n'
    '        sb = _L("romp_sdk_backend_voice", _os.path.join(BIN, "romp_sdk_backend.py")).load_module()\n'
    '        self.assertTrue(sb)\n'
)
NESTED_NEW = (
    'import os\n'
    'import tempfile\n'
    'import unittest\n'
    + NEW_IMPORT +
    '\n'
    'BIN = "bin"\n'
    'os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    '\n'
    '\n'
    'class T(unittest.TestCase):\n'
    '    def test_x(self):\n'
    '        import os as _os\n'
    '        sb = load_source("romp_sdk_backend_voice", _os.path.join(BIN, "romp_sdk_backend.py"))\n'
    '        self.assertTrue(sb)\n'
)

# The name imported beside another that stays in use, and a call broken across two lines.
BESIDE_OLD = (
    'import os\n'
    'from importlib.machinery import ModuleSpec, SourceFileLoader\n'
    '\n'
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'sb = SourceFileLoader("romp_sdk_backend_t",\n'
    '                      os.path.join("bin", "romp_sdk_backend.py")).load_module()\n'
    'spec = ModuleSpec("claude_agent_sdk", loader=None)\n'
)
BESIDE_NEW = (
    'import os\n'
    'from importlib.machinery import ModuleSpec\n'
    + NEW_IMPORT +
    '\n'
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'sb = load_source("romp_sdk_backend_t",\n'
    '                      os.path.join("bin", "romp_sdk_backend.py"))\n'
    'spec = ModuleSpec("claude_agent_sdk", loader=None)\n'
)

# A guarded load: the call sits inside an `or` expression, not at statement level.
GUARDED_OLD = (
    'import os\n'
    'import sys\n'
    + OLD_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'cred = sys.modules.get("romp_credentials") or SourceFileLoader("romp_credentials", "kernel/credentials.py").load_module()\n'
)
GUARDED_NEW = (
    'import os\n'
    'import sys\n'
    + NEW_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'cred = sys.modules.get("romp_credentials") or load_source("romp_credentials", "kernel/credentials.py")\n'
)

# The name still used in code (a loader built for another purpose): its import stays.
STILL_USED_OLD = (
    'import os\n'
    + OLD_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'km = SourceFileLoader("romp_kernel_t", "bin/romp-kernel").load_module()\n'
    'probe = SourceFileLoader("romp_probe", "bin/romp-judge")\n'
    'origin = probe.get_filename()\n'
)
STILL_USED_NEW = (
    'import os\n'
    + OLD_IMPORT + NEW_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'km = load_source("romp_kernel_t", "bin/romp-kernel")\n'
    'probe = SourceFileLoader("romp_probe", "bin/romp-judge")\n'
    'origin = probe.get_filename()\n'
)

# A loader reached through the package, not the imported name: outside the converter's rewrite.
UNREACHED = (
    'import importlib.machinery as machinery\n'
    'import os\n'
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'km = machinery.SourceFileLoader("romp_kernel_t", "bin/romp-kernel").load_module()\n'
)

# load_module() called with an argument: the rewrite would have to drop it, so the call is reported
# for a hand edit rather than converted.
WITH_AN_ARGUMENT = (
    'import os\n'
    + OLD_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'km = SourceFileLoader("romp_kernel_t", "bin/romp-kernel").load_module("romp_kernel_t")\n'
)

# A form feed on a line of its own above the import (a section separator, legal Python): ast counts
# the newline alone, and the line edits must count the same way.
FORM_FEED_OLD = (
    'import os\n'
    '\x0c\n'
    + OLD_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'km = SourceFileLoader("romp_kernel_t", "bin/romp-kernel").load_module()\n'
)
FORM_FEED_NEW = (
    'import os\n'
    '\x0c\n'
    + NEW_IMPORT +
    'os.environ["XDG_STATE_HOME"] = "x"\n'
    'os.environ.pop("ROMP_STATE_DIR", None)\n'
    'km = load_source("romp_kernel_t", "bin/romp-kernel")\n'
)

# The idiom inside a string handed to a child process: text, not code, to both tools.
IN_A_STRING = (
    'import subprocess\n'
    'import sys\n'
    'code = ("from importlib.machinery import SourceFileLoader\\n"\n'
    '        "SourceFileLoader(\'romp_kernel\', \'bin/romp-kernel\').load_module()\\n")\n'
    'subprocess.run([sys.executable, "-c", code])\n'
)

SAMPLES = [(PREAMBLE_OLD, PREAMBLE_NEW), (NESTED_OLD, NESTED_NEW), (BESIDE_OLD, BESIDE_NEW),
           (GUARDED_OLD, GUARDED_NEW), (STILL_USED_OLD, STILL_USED_NEW), (FORM_FEED_OLD, FORM_FEED_NEW)]


def _run(argv):
    """main's exit status and what it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = tool.main(argv)
    return status, out.getvalue()


class Convert(unittest.TestCase):
    def test_the_usual_module_head_converts_to_the_load_source_idiom(self):
        self.assertEqual(tool.convert(PREAMBLE_OLD), PREAMBLE_NEW)

    def test_an_alias_bound_inside_a_function_converts_and_the_module_gains_one_top_level_import(self):
        # the nested import goes (its name is out of use), the call converts in place, and the module
        # gets `from romp_load import load_source` after its leading import block, where the
        # function's load_source resolves as a module global
        self.assertEqual(tool.convert(NESTED_OLD), NESTED_NEW)

    def test_a_name_imported_beside_another_leaves_the_other_and_a_split_call_keeps_its_lines(self):
        self.assertEqual(tool.convert(BESIDE_OLD), BESIDE_NEW)

    def test_a_guarded_load_converts_inside_its_expression(self):
        self.assertEqual(tool.convert(GUARDED_OLD), GUARDED_NEW)

    def test_a_name_still_used_in_code_keeps_its_import(self):
        # only load_module() is removed; a loader built for another purpose keeps its import, and
        # the refusal accepts the module (test_the_tool_reports_the_same_sites_the_refusal_would)
        self.assertEqual(tool.convert(STILL_USED_OLD), STILL_USED_NEW)

    def test_a_form_feed_line_does_not_shift_the_import_edits(self):
        # the converted text is edited by line; a separator str.splitlines counts and ast does not
        # would land the import edits a line early, leaving the stale import in place with the
        # new one on the form feed's line, and the output would still parse
        self.assertEqual(tool.convert(FORM_FEED_OLD), FORM_FEED_NEW)

    def test_conversion_is_idempotent(self):
        # a regenerated commit re-runs the tool over every module, converted ones included
        for old, new in SAMPLES:
            once = tool.convert(old)
            self.assertEqual(tool.convert(once), once)
            self.assertEqual(tool.convert(new), new)

    def test_a_module_without_the_idiom_comes_back_unchanged(self):
        for src in (PREAMBLE_NEW, "import os\nprint(os.sep)\n", IN_A_STRING, ""):
            self.assertIs(tool.convert(src), src)

    def test_the_idiom_inside_a_string_is_left_as_it_is_and_reported_by_nobody(self):
        # both tools read the AST: a child-process snippet is a string constant to each. It is one
        # of the two shapes the README leaves to a hand edit; pin that neither tool claims it.
        self.assertIs(tool.convert(IN_A_STRING), IN_A_STRING)
        self.assertEqual(tool.leftovers(ast.parse(IN_A_STRING)), [])
        self.assertEqual(refusal.removed_loader_sites(ast.parse(IN_A_STRING)), [])

    def test_a_loader_reached_other_than_through_the_imported_name_is_a_leftover(self):
        self.assertIs(tool.convert(UNREACHED), UNREACHED)
        self.assertEqual(tool.leftovers(ast.parse(UNREACHED)), [5])

    def test_a_call_with_an_argument_is_a_leftover_not_a_conversion(self):
        # load_source takes the constructor's arguments only; a rewrite would have to drop the
        # argument handed to load_module(), so the tool leaves the module as it is and reports the
        # line for a hand edit
        self.assertIs(tool.convert(WITH_AN_ARGUMENT), WITH_AN_ARGUMENT)
        self.assertEqual(tool.leftovers(ast.parse(WITH_AN_ARGUMENT)), [5])
        self.assertEqual(refusal.removed_loader_sites(ast.parse(WITH_AN_ARGUMENT)), [5])

    def test_converted_output_has_nothing_left_for_the_refusal(self):
        for old, _new in SAMPLES:
            out = tool.convert(old)
            self.assertEqual(refusal.removed_loader_sites(ast.parse(out)), [], out)
            self.assertEqual(tool.leftovers(ast.parse(out)), [], out)

    def test_the_tool_reports_the_same_sites_the_refusal_would(self):
        # the two scans are one rule in two files (tools/ cannot import from tests/); a change to
        # either shows up here
        for src in (PREAMBLE_OLD, NESTED_OLD, BESIDE_OLD, GUARDED_OLD, STILL_USED_OLD, FORM_FEED_OLD,
                    UNREACHED, WITH_AN_ARGUMENT):
            self.assertEqual(tool.leftovers(ast.parse(src)), refusal.removed_loader_sites(ast.parse(src)))
        self.assertEqual(tool.leftovers(ast.parse(PREAMBLE_OLD)), [13, 14, 15])
        self.assertEqual(tool.leftovers(ast.parse(STILL_USED_NEW)), [],
                         "SourceFileLoader itself stays in the language: an import kept for another "
                         "purpose is not the removed idiom")

    def test_the_skip_lists_are_one_list(self):
        self.assertEqual(set(tool.SKIP), set(refusal.LOADER_SKIP),
                         "a helper added to one list is added to the other, or the refusal flags a "
                         "file the tool refuses to rewrite")
        for name, reason in tool.SKIP.items():
            self.assertTrue(reason.strip(), "%s: a skipped file carries the reason it is skipped" % name)


class Main(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def _write(self, name, text):
        p = os.path.join(self.td, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _read(self, p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_check_mode_names_what_would_change_and_writes_nothing(self):
        p = self._write("test_a.py", PREAMBLE_OLD)
        status, out = _run(["--check", p])
        self.assertEqual(status, 1, out)
        self.assertIn("would rewrite", out)
        self.assertIn("test_a.py", out)
        self.assertEqual(self._read(p), PREAMBLE_OLD, "a dry run leaves the file as it was")

    def test_a_run_rewrites_and_the_next_run_and_check_find_nothing(self):
        p = self._write("test_a.py", PREAMBLE_OLD)
        status, out = _run([p])
        self.assertEqual(status, 0, out)
        self.assertIn("rewrote", out)
        self.assertEqual(self._read(p), PREAMBLE_NEW)
        status, out = _run([p])
        self.assertEqual(status, 0, out)
        self.assertNotIn("rewrote", out)
        self.assertEqual(self._read(p), PREAMBLE_NEW)
        status, out = _run(["--check", p])
        self.assertEqual(status, 0, "nothing to rewrite: the check passes; output:\n%s" % out)

    def test_a_directory_is_every_python_file_in_it_but_the_skipped_ones_each_with_its_reason(self):
        a = self._write("test_a.py", PREAMBLE_OLD)
        helper = self._write("helper.py", GUARDED_OLD)
        skipped = [self._write(name, PREAMBLE_OLD) for name in sorted(tool.SKIP)]
        self._write("notes.txt", OLD_IMPORT)
        status, out = _run([self.td])
        self.assertEqual(status, 0, out)
        self.assertEqual(self._read(a), PREAMBLE_NEW)
        self.assertEqual(self._read(helper), GUARDED_NEW)
        for p in skipped:
            self.assertEqual(self._read(p), PREAMBLE_OLD, "%s is never rewritten" % p)
            self.assertIn("skipped %s: %s" % (tool.display_path(p), tool.SKIP[os.path.basename(p)]), out)

    def test_a_skipped_file_named_on_the_command_line_is_skipped_too(self):
        p = self._write("conftest.py", PREAMBLE_OLD)
        status, out = _run([p])
        self.assertEqual(status, 0, out)
        self.assertIn("skipped", out)
        self.assertIn(tool.SKIP["conftest.py"], out)
        self.assertEqual(self._read(p), PREAMBLE_OLD)

    def test_a_leftover_is_reported_for_a_hand_edit_and_fails_the_run(self):
        p = self._write("test_a.py", UNREACHED)
        for argv in ([p], ["--check", p]):
            status, out = _run(argv)
            self.assertEqual(status, 1, out)
            self.assertIn("test_a.py:5 calls load_module()", out)
            self.assertIn("by hand", out)
        self.assertEqual(self._read(p), UNREACHED)

    def test_a_file_that_would_not_parse_after_conversion_is_left_alone_and_reported(self):
        # a function whose body is the import and nothing else cannot lose the import; the tool
        # must refuse rather than write a module that no longer parses
        src = ('import os\n' + OLD_IMPORT +
               'os.environ["XDG_STATE_HOME"] = "x"\n'
               'os.environ.pop("ROMP_STATE_DIR", None)\n'
               'def f():\n'
               '    from importlib.machinery import SourceFileLoader as _L\n'
               'km = SourceFileLoader("romp_kernel_t", "bin/romp-kernel").load_module()\n')
        p = self._write("test_a.py", src)
        status, out = _run([p])
        self.assertEqual(status, 1, out)
        self.assertIn("test_a.py", out)
        self.assertIn("by hand", out)
        self.assertEqual(self._read(p), src)
        ast.parse(self._read(p))

    def test_the_default_scan_is_the_suite_directory(self):
        self.assertEqual(os.path.realpath(tool.TESTS), os.path.realpath(HERE))


if __name__ == "__main__":
    unittest.main()
