#!/usr/bin/env python3
"""A slash or skill command typed into the composer keeps its "sending" bubble although the turn runs
(reported from a live dashboard, 2026-09-10). Claude Code records a slash send as a wrapper record
(<command-message>, <command-name>, <command-args>) and, for a skill, a second record with the skill
body; a verbatim copy of the typed text is not guaranteed. The event model reads the wrapper back as a
command atom whose text is "/name args" with ONE space between the name and the args, whatever the sender
typed there. The input echo holds the typed text, and the by-text landing rule is outer-strip only, so a
send like "/deploy \\n\\nstaging now" never met its own record: the kernel's prune kept the echo forever,
the backend's boot/spawn scan, which reads the raw records, called the send lost and RE-DELIVERED it, so
the command ran twice after a kernel restart, and the fed-copy pairing left the landed event without the
copy's id, so the chat's own pending bubble stayed as well.

session_backend.command_text_key is the shared rule for slash-shaped texts: the whitespace-delimited
tokens joined by single spaces, applied to the echo AND to the record on both paths (the kernel's
_atom_user_texts and the backend's _landed_texts), so the two agree on the same records. The plain rule
(echo_text_key) is unchanged for every other text. SYNTHETIC fixtures only: an invented command, a private
synthetic sid, the notes-api demo domain."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_slash_echo", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_slash_echo", os.path.join(BIN, "romp_sdk_backend.py"))
em = km.em

SID = "3b5d7f9a-2c4e-4a6b-8d0f-1e3a5c7b9d2f"   # private synthetic sid (goal-store fixtures rule)
TMUX_SID = "4c6e8a0b-3d5f-4b7c-9e1a-2f4b6d8c0e3a"
T0 = 1_800_000_000
NOW = T0 + 3600

# The typed send: a space and two newlines between the command and its arguments.
TYPED = "/deploy \n\nstaging now"


def wrapper(name, args, order="message-first", skill=True):
    """The record Claude Code writes for a slash send. A skill writes <command-message> first and marks
    the record with <skill-format>; a built-in writes <command-name> first. Neither carries the typed
    whitespace between the name and the arguments."""
    tags = ["<command-message>%s</command-message>" % name.lstrip("/"),
            "<command-name>%s</command-name>" % name]
    if order == "name-first":
        tags.reverse()
    if args:
        tags.append("<command-args>%s</command-args>" % args)
    if skill:
        tags.append("<skill-format>true</skill-format>")
    return "\n".join(tags)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, meta=False, prompt_id=None):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "promptSource": "sdk", "message": {"role": "user", "content": text}}
    if meta:
        r["isMeta"] = True
    if prompt_id:
        r["promptId"] = prompt_id
    return r


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def exchange():
    """An earlier exchange, then the slash send's records: the wrapper and the skill body (both isMeta,
    one promptId, no raw copy of the typed text anywhere), then the reply the command produced."""
    return [uline(T0, "tighten the notes-api search", "u1"),
            aline(T0 + 10, "Done.", "a1", "u1")]


def slash_records(wrap, t=T0 + 100):
    return [uline(t, wrap, "u2", "a1", meta=True, prompt_id="p1"),
            uline(t, "Base directory for this skill: /tmp/notes-api/.claude/skills/deploy\n\nDeploy the service.",
                  "u3", "u2", meta=True, prompt_id="p1"),
            aline(t + 30, "Deploying staging now.", "a2", "u3")]


class _World:
    """A real SdkBackend bound as the kernel's backend, owning SID, with a thread-less SdkSession."""

    def __init__(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        (root / "sdk").mkdir()
        self.cwd = root / "proj"; self.cwd.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(root / "claude")
        self.tpath = Path(sb.transcript_path(str(self.cwd), SID))
        self.tpath.parent.mkdir(parents=True, exist_ok=True)
        self.tpath.write_text("")
        self.be = sb.SdkBackend(str(root), "/bin/true", lambda *a, **k: None)
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True,
               "cwd": str(self.cwd), "lastSid": SID}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[SID] = self.s
        self.saved = km._sdk
        km._sdk = lambda: self.be

    def close(self):
        km._sdk = self.saved
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.td.cleanup()

    def write(self, recs):
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))

    def parse(self):
        return em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)],
                                postal_log=[], now=NOW, sdk_human=True)

    def echo(self, text, t):
        key = "echo:slash"
        self.be._live[SID] = {key: {"type": "user", "uuid": key, "session_id": SID, "t": t,
                                     "parentUuid": None, "author": "human", "_echo_text": text,
                                     "message": {"role": "user", "content": [{"type": "text", "text": text}]}}}
        return key


def _texts(session):
    return [t for turn in session["turns"] for a in turn["atoms"] for t in km._atom_user_texts(a)]


class TheSharedKey(unittest.TestCase):
    """command_text_key: one rule, read by the kernel and the backend, for slash-shaped texts only."""

    def test_a_slash_send_keys_to_its_words(self):
        for mod in (km.sb, sb):
            self.assertEqual(mod.command_text_key(TYPED), "/deploy staging now")
            self.assertEqual(mod.command_text_key("  /deploy   staging\nnow \n"), "/deploy staging now")
            self.assertEqual(mod.command_text_key("/deploy"), "/deploy")
            self.assertEqual(mod.command_text_key("/plugin:skill  review the draft"), "/plugin:skill review the draft")

    def test_anything_else_has_no_command_key(self):
        for mod in (km.sb, sb):
            self.assertEqual(mod.command_text_key("deploy \n\nstaging now"), "")
            self.assertEqual(mod.command_text_key("/"), "")
            self.assertEqual(mod.command_text_key(""), "")
            self.assertEqual(mod.command_text_key(None), "")
            # a message that starts with a PATH is a plain message: its first token has a further slash
            self.assertEqual(mod.command_text_key("/tmp/build.log\n\nlook at this"), "")
            self.assertEqual(mod.command_text_key("/tmp/notes-api/api.py is broken"), "")

    def test_the_plain_rule_is_unchanged(self):
        # outer whitespace stripped, nothing else: a plain send's internal whitespace still decides
        self.assertEqual(km.sb.echo_text_key(TYPED), TYPED)
        self.assertEqual(km.sb.echo_text_key("deploy \n\nstaging now\n"), "deploy \n\nstaging now")

    def test_echo_keys_is_the_either_key_rule(self):
        for mod in (km.sb, sb):
            self.assertEqual(mod.echo_keys(TYPED), (TYPED, "/deploy staging now"))
            self.assertEqual(mod.echo_keys("/deploy staging now"), ("/deploy staging now",), "one key when the two agree")
            self.assertEqual(mod.echo_keys(" deploy staging now\n"), ("deploy staging now",))
            self.assertEqual(mod.echo_keys(""), ())
            self.assertEqual(mod.echo_keys(None), ())


class WrapperReadingMatchesTheEventModel(unittest.TestCase):
    """sdk_backend._command_invocation gives the landing scan (and the live atom) the "/name args" the event
    model's file adapter gives the parsed command atom. The two are separate code; the scan is right only
    while they agree, so they are compared on every wrapper shape the CLI writes."""

    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()

    def test_every_wrapper_shape_reads_the_same_on_both_sides(self):
        shapes = [wrapper("/deploy", "staging now"), wrapper("/deploy", "staging now", "name-first", skill=False),
                  wrapper("/deploy", ""), wrapper("deploy", "staging now"),   # a name written without its slash
                  wrapper("/deploy", "staging\n  now\nplease"),               # arguments over several lines
                  "<command-name>/deploy</command-name>\n<command-message>deploy</command-message>\n<command-args>  staging now  </command-args>"]
        for wrap in shapes:
            with self.subTest(wrap=wrap.splitlines()[:3]):
                self.w.write(exchange() + slash_records(wrap))
                atoms = [a for t in self.w.parse()["turns"] for a in t["atoms"] if a.get("command")]
                self.assertEqual(len(atoms), 1)
                name, disp = sb._command_invocation(wrap)
                self.assertEqual(atoms[0]["command"], name)
                self.assertEqual(atoms[0]["message"]["content"][0]["text"], disp)
                self.assertIn(km.sb.echo_text_key(disp), sb._landed_texts({"type": "user", "message": {"role": "user", "content": wrap}}))

    def test_prose_that_quotes_a_tag_is_not_an_invocation(self):
        self.assertIsNone(sb._command_invocation("the CLI writes a <command-name>/deploy</command-name> record, why?"))


class KernelRetire(unittest.TestCase):
    """The chat's path: build_session's _merge_live_atoms hands prune_live the parsed record texts."""

    def setUp(self):
        self.w = _World()
        self.assertIs(km.Sessions.backend_for(SID), self.w.be)

    def tearDown(self):
        self.w.close()

    def test_the_wrapper_record_retires_the_typed_echo(self):
        for order in ("message-first", "name-first"):
            with self.subTest(order=order):
                self.w.write(exchange() + slash_records(wrapper("/deploy", "staging now", order)))
                key = self.w.echo(TYPED, T0 + 95)
                merged = km._merge_live_atoms(self.w.parse(), SID)
                self.assertNotIn(key, self.w.be._live.get(SID, {}),
                                 "the send's own record landed after the send: the echo retires")
                self.assertEqual(_texts(merged).count("/deploy staging now"), 1, "the command shows once")
                self.assertNotIn(TYPED, _texts(merged), "…and never as a second, pending bubble")

    def test_arguments_with_irregular_whitespace_land_under_the_shared_key(self):
        # the record's parsed text keeps whatever the sender typed INSIDE the arguments, so it can differ from
        # the typed text there as well as between the name and the arguments: both sides key the record's text
        # too, and the same words land whatever the spacing on either side. The scan agrees with the prune.
        typed = "/deploy\nstaging  now"
        self.w.write(exchange() + slash_records(wrapper("/deploy", "staging  now")))
        key = self.w.echo(typed, T0 + 95)
        self.assertIs(self.w.be._text_landed(SID, typed, T0 + 95), True)
        merged = km._merge_live_atoms(self.w.parse(), SID)
        self.assertNotIn(key, self.w.be._live.get(SID, {}), "the record's words are the echo's words")
        self.assertEqual(_texts(merged).count("/deploy staging  now"), 1, "the command shows once, as its record")
        self.assertNotIn(typed, _texts(merged))

    def test_a_wrapper_for_another_command_or_other_args_keeps_it(self):
        for other in (wrapper("/rollback", "staging now"), wrapper("/deploy", "production now"),
                      wrapper("/deploy", "staging"), wrapper("/deploy", "")):
            with self.subTest(other=other.splitlines()[:3]):
                self.w.write(exchange() + slash_records(other))
                key = self.w.echo(TYPED, T0 + 95)
                km._merge_live_atoms(self.w.parse(), SID)
                self.assertIn(key, self.w.be._live.get(SID, {}),
                              "a different command's record is not this send's landing")

    def test_a_wrapper_written_before_the_send_keeps_it(self):
        # the record must be at or after the send: a re-send of an earlier command waits for its own record
        self.w.write(exchange() + slash_records(wrapper("/deploy", "staging now"), t=T0 + 50))
        key = self.w.echo(TYPED, T0 + 95)
        km._merge_live_atoms(self.w.parse(), SID)
        self.assertIn(key, self.w.be._live.get(SID, {}))

    def test_a_plain_send_still_needs_its_exact_text(self):
        # the collapse is for slash sends only: a plain message differing in whitespace is not a landing
        self.w.write(exchange() + [uline(T0 + 100, "deploy  staging now", "u2", "a1"),
                                   aline(T0 + 130, "ok", "a2", "u2")])
        key = self.w.echo("deploy staging now", T0 + 95)
        km._merge_live_atoms(self.w.parse(), SID)
        self.assertIn(key, self.w.be._live.get(SID, {}))


class BackendScanAgrees(unittest.TestCase):
    """The backend's boot/spawn scan reads the RAW records; it must give the kernel's verdict on the same
    ones, or a restart re-delivers a command that already ran."""

    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()

    def test_the_scan_finds_the_send_in_its_wrapper_record(self):
        self.w.write(exchange() + slash_records(wrapper("/deploy", "staging now")))
        self.assertIs(self.w.be._text_landed(SID, TYPED, T0 + 95), True)

    def test_the_scan_agrees_with_the_kernel_on_every_record(self):
        cases = [(wrapper("/deploy", "staging now"), True), (wrapper("/deploy", "staging now", "name-first"), True),
                 (wrapper("/rollback", "staging now"), False), (wrapper("/deploy", "production now"), False),
                 (wrapper("/deploy", ""), False)]
        for wrap, expect in cases:
            with self.subTest(wrap=wrap.splitlines()[:3]):
                self.w.write(exchange() + slash_records(wrap))
                key = self.w.echo(TYPED, T0 + 95)
                scan = self.w.be._text_landed(SID, TYPED, T0 + 95)
                km._merge_live_atoms(self.w.parse(), SID)
                kernel = key not in self.w.be._live.get(SID, {})
                self.assertEqual((scan, kernel), (expect, expect))

    def test_a_restart_does_not_run_the_command_twice(self):
        # the spawn-time marking: the echo is un-pruned (the previous kernel died before a build), the
        # queue is empty, the wrapper is on disk. The scan must find it: the verdict is `_landed`, nothing
        # is re-queued and nothing is flagged lost.
        self.w.write(exchange() + slash_records(wrapper("/deploy", "staging now")))
        key = self.w.echo(TYPED, T0 + 95)
        self.w.be._mark_dropped_echoes(SID, [])
        echo = self.w.be._live[SID][key]
        self.assertTrue(echo.get("_landed"), "the scan read the landing off the wrapper record")
        self.assertFalse(echo.get("dropped"))
        self.assertEqual(self.w.s.pending(), [], "never re-delivered: the command already ran")


class LandingCarriesTheCopysId(unittest.TestCase):
    """The chat's own pending bubble retires by IDENTITY once it has latched the copy's id: the kernel
    stamps a landed user event with the id of the fed copy whose text it carries (qids_for_landing). The
    wrapper's parsed text must pair with the typed copy, or the sender's bubble outlives the command."""

    def setUp(self):
        self.w = _World()

    def tearDown(self):
        self.w.close()

    def _fed(self, text, qid="echo:slash"):
        self.w.s._fed_meta.append({"qid": qid, "qts": None, "text": text, "t": T0 + 95})

    def test_the_wrappers_parsed_text_pairs_with_the_typed_copy(self):
        self._fed(TYPED)
        self.assertEqual(self.w.s.qids_for_landing("u2", ["/deploy staging now"], T0 + 100), ["echo:slash"])
        self.assertEqual(self.w.s._fed_meta, [], "the copy's entry is spent by its landing")

    def test_another_command_does_not_spend_the_copy(self):
        self._fed(TYPED)
        self.assertEqual(self.w.s.qids_for_landing("u2", ["/deploy production now"], T0 + 100), [None])
        self.assertEqual(self.w.s.qids_for_landing("u3", ["/rollback staging now"], T0 + 100), [None])
        self.assertEqual(len(self.w.s._fed_meta), 1, "the typed copy still waits for its own landing")

    def test_a_plain_copy_still_pairs_by_its_exact_text(self):
        self._fed("deploy staging now", qid="echo:plain")
        self.assertEqual(self.w.s.qids_for_landing("u2", ["deploy  staging now"], T0 + 100), [None])
        self.assertEqual(self.w.s.qids_for_landing("u3", ["deploy staging now"], T0 + 100), ["echo:plain"])


class TmuxEchoAgrees(unittest.TestCase):
    """The tmux route's echo prune reads the same kernel-built texts."""

    def tearDown(self):
        km._tmux_echo.pop(TMUX_SID, None)

    def _tx_texts(self, disp):
        # the texts build_session hands the prune, from the command atom the event model reads the wrapper as
        atom = {"type": "user", "uuid": "u2", "session_id": TMUX_SID, "t": T0 + 100, "author": "human",
                "command": "/deploy", "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
        return set(km._atom_user_texts(atom))

    def test_the_parsed_command_atom_retires_the_typed_echo(self):
        km._tmux_echo_add(TMUX_SID, TYPED)
        self.assertEqual(len(km._tmux_echo_atoms(TMUX_SID)), 1)
        km._tmux_echo_prune(TMUX_SID, set(), self._tx_texts("/deploy staging now"))
        self.assertEqual(km._tmux_echo_atoms(TMUX_SID), [], "the command atom's text lands the typed echo")

    def test_another_commands_atom_keeps_it(self):
        km._tmux_echo_add(TMUX_SID, TYPED)
        km._tmux_echo_prune(TMUX_SID, set(), self._tx_texts("/deploy production now"))
        self.assertEqual(len(km._tmux_echo_atoms(TMUX_SID)), 1)


if __name__ == "__main__":
    unittest.main()
