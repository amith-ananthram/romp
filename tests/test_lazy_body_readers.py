#!/usr/bin/env python3
"""T323 stage 4a: every reader of an atom's BODY (message, toolUseResult, skillMd) in the kernel and the judges is one the
audit named, and each either hydrates first (em.hydrate) or reads raw transcript records or live SDK atoms, which are
never lazy. A new consumer that reads a body from a parsed tree without hydrating trips the loud sentinel at run time;
this test trips it at review time: any body-reading site outside the audited functions fails here with its line.
Facts only: the audit of 2026-09-11 grouped the sites by walk scope; the names below are that list."""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
KERNEL = os.path.join(os.path.dirname(HERE), "kernel")

BODY_READ = re.compile(r'\.get\("message"\)|\["message"\]|"toolUseResult"|"skillMd"')

# kernel.py: the audited readers. Hydrating leaves (the comment names the stage), whole-turn hydrators, and readers of raw
# transcript records or live atoms (never lazy).
KERNEL_ALLOWED = {
    # leaves that hydrate the atom(s) they read
    "_atom_md", "_atom_user_text", "_atom_user_texts", "_atom_prose_chars", "_interrupt_cause", "_seg_anchors", "_seg_jump",
    "_seg_last_text", "_seg_prompt", "_seg_mids", "_open_turn_progress", "_seg_of_tool_uses", "_fold_tasks_turn", "_turn_landed",
    "_interrupt_settle",
    # the chat build hydrates the turns it renders before its loops
    "build_session",
    # readers of raw transcript records (jsonl rows), never atoms
    "_last_assistant_report", "_comment_prose_record", "_comment_cut_target", "_comment_msg_text", "_thread_messages",
    "_undelivered_wake_tail", "_gist_step", "_launch_ids_step", "_launch_step", "_api_error_pass", "_session_meta_step",
    "_transcript_tok_rows", "_rewind_target", "_subagent_meta_map", "_read_task_output",
    # live SDK atoms and echoes (constructed in-process, never lazy)
    "_merge_live_atoms", "_interrupt_marks_atoms", "_stamp_agents", "_ask_fill_answers", "_ask_fill_chosen", "_patch_rows",
    "_claudemd_paths", "_stamp_steps", "_hydrate_postal", "build_subagent", "_agent_alive",
}
JUDGE_ALLOWED = {
    "_atom_text", "_unit_text", "_has_asst_work", "_seg_launches", "_human_prompt_record", "_awaiting_bg_hold",
    # raw records, the states log, captions
    "transcript_head", "_bg_step", "_bg_unresolved",
}


def _enclosing(tree, lineno):
    """The module-level function holding `lineno` (a closure inside an audited function is that function's read)."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno):
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.lineno <= lineno <= sub.end_lineno:
                        return node.name + "." + sub.name
                return node.name
            return node.name
    return "<module>"


def _sites(path):
    src = open(path).read()
    tree = ast.parse(src)
    out = []
    in_doc = False
    for i, line in enumerate(src.split("\n"), start=1):
        stripped = line.strip()
        if stripped.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        code = line.split("#", 1)[0]
        if BODY_READ.search(code):
            out.append((i, _enclosing(tree, i), code.strip()[:110]))
    return out


class BodyReadersAreAudited(unittest.TestCase):
    def _check(self, name, allowed):
        sites = _sites(os.path.join(KERNEL, name))
        self.assertTrue(sites, "the scan finds body reads in %s" % name)
        strays = [(ln, fn, code) for ln, fn, code in sites if fn not in allowed]
        self.assertEqual(strays, [], "body reads outside the audited functions in %s (hydrate first, or add the function to the "
                                     "audited list with its reason):\n%s" % (name, "\n".join("  %s:%d %s: %s" % (name, ln, fn, code) for ln, fn, code in strays)))

    def test_kernel_body_readers_are_the_audited_ones(self):
        self._check("kernel.py", KERNEL_ALLOWED)

    def test_judge_body_readers_are_the_audited_ones(self):
        self._check("judge.py", JUDGE_ALLOWED)

    def test_every_hydrating_leaf_calls_hydrate_before_its_read(self):
        """The leaves the audit named hydrate at the top of their body: the call sits before any body read."""
        for name, fns in (("kernel.py", ["_atom_md", "_atom_user_text", "_atom_user_texts", "_atom_prose_chars", "_seg_anchors", "_seg_jump",
                                         "_seg_last_text", "_seg_prompt", "_seg_mids", "_open_turn_progress", "_fold_tasks_turn", "_turn_landed"]),
                          ("judge.py", ["_atom_text", "_unit_text", "_has_asst_work", "_seg_launches", "_human_prompt_record"])):
            src = open(os.path.join(KERNEL, name)).read()
            tree = ast.parse(src)
            defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
            lines = src.split("\n")
            for fn in fns:
                node = defs[fn]
                body = "\n".join(lines[node.lineno - 1:node.end_lineno])
                first_read = next((i for i, l in enumerate(body.split("\n")) if BODY_READ.search(l.split("#", 1)[0])), None)
                hyd = next((i for i, l in enumerate(body.split("\n")) if "em.hydrate(" in l), None)
                self.assertIsNotNone(hyd, "%s.%s hydrates" % (name, fn))
                if first_read is not None:
                    self.assertLess(hyd, first_read, "%s.%s hydrates before its first body read" % (name, fn))


if __name__ == "__main__":
    unittest.main()
