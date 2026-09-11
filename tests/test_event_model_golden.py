#!/usr/bin/env python3
"""Golden contract tests for the rebuilt bottom-layer parser (bin/romp-event-model).

Each scenario builds a SYNTHETIC transcript (invented prompt text, placeholder
UUIDs, hostname TESTHOST — never real session data, per CLAUDE.md), runs the
REAL parse_session on it with a fixed clock, and compares the full Session ->
Turn -> Atom tree against a checked-in golden JSON file. The unit classes below
pin the subtle invariants that are hard to eyeball in a JSON diff: author
classification, the absorb-vs-queue turn boundary, turn/segment derivation,
`ended` inference, the resume/clear lineage walk, idle-from-the-state-log, and
popAll.

Run:    python3 tests/test_event_model_golden.py
Regen:  python3 tests/test_event_model_golden.py --regen   (then REVIEW the diff)
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "bin")
GOLDEN = Path(HERE) / "fixtures" / "event-model-golden"

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(SCRIPTS, "romp-event-model"))

NOW = 1781100000                      # fixed test clock — goldens depend on it
SID = "11111111-2222-3333-4444-555555555555"      # the session's stable ROMP UUID
PEER = "99999999-8888-7777-6666-000000000000"     # a peer session's ROMP UUID
MID = "1700000000.111_222.TESTHOST"               # a synthetic postal message id
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── synthetic on-disk line builders (mirror the real transcript shapes) ──
def uline(t, text, uuid, parent=None, ps="typed"):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "user", "content": text}}
    if ps is not None:
        r["promptSource"] = ps
    return r


def aline(t, text, uuid, parent=None, tools=(), stop="end_turn", thinking=None):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if text:
        content.append({"type": "text", "text": text})
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n, "input": {}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def trline(t, tool_use_id, uuid, parent=None, content="ok"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result",
                        "tool_use_id": tool_use_id, "content": content}]}}


def qop(t, op, content=None):
    return {"type": "queue-operation", "timestamp": iso(t), "operation": op, "content": content}


def attline(t, prompt, uuid, parent=None):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isSidechain": False, "attachment": {"type": "queued_command", "prompt": prompt}}


def reminder_line(t, uuid, parent):
    # the total_tokens_reminder attachment the CLI writes at request start — the record the
    # api_error-flush forks at in both verified incidents (uuid-chained, never an atom)
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isSidechain": False, "attachment": {"type": "total_tokens_reminder"}}


def api_error_line(t, uuid, parent, attempt=1):
    # shape mirrors the live corpus (2026-09-01): level "error", source request_retry, a
    # retryAttempt counter that resets between bursts — all content synthetic
    return {"type": "system", "subtype": "api_error", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": parent, "isSidechain": False, "level": "error",
            "retryAttempt": attempt, "maxRetries": 10, "retryInMs": 1000,
            "source": "request_retry", "error": {"status": 429, "message": "synthetic rate limit"}}


def stop_hook_line(t, uuid, parent):
    return {"type": "system", "subtype": "stop_hook_summary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": parent, "isSidechain": False, "level": "suggestion",
            "hookCount": 1, "hookErrors": [], "preventedContinuation": False}


def compact_line(t, uuid, logical_parent, trigger="manual", pre=263239, post=6514):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": None, "logicalParentUuid": logical_parent, "isMeta": False,
            "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post}}


def compact_line_broken(t, uuid, dangling_logical, preserved_tail, trigger="auto", pre=99999):
    # a compact_boundary whose logicalParentUuid points at a uuid that exists NOWHERE
    # (as seen in real transcripts); the real in-file pre-compaction leaf is in
    # compactMetadata.preservedSegment.tailUuid
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": None, "logicalParentUuid": dangling_logical, "isMeta": False,
            "compactMetadata": {"trigger": trigger, "preTokens": pre,
                                "preservedSegment": {"headUuid": preserved_tail,
                                                     "anchorUuid": preserved_tail,
                                                     "tailUuid": preserved_tail}}}


def compact_summary_line(t, uuid, parent, text="summary of the conversation so far"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "message": {"role": "user", "content": text}}


def manual_compact_lines(t_issue, t_done, tag, parent, summary_text="summary of the conversation so far",
                         summary_pid=True):
    """The on-disk tail of a LIVE manual /compact, exactly as the CLI writes it (shape verified
    against the live corpus 2026-08-19; all content here synthetic): the boundary + summary are
    appended FIRST, at COMPLETION time, as a DETACHED side branch — the boundary carries
    parentUuid:null + logicalParentUuid:<the pre-compact leaf>, and the summary is its only
    child. THEN come the command-wrapper records (the raw-text twin and the <command-name>
    wrapper, stamped with the earlier ISSUE time), then the stdout at completion time. The
    conversation chains through the wrappers — NOTHING on the active path visits the side
    branch, which is why the walk dropped the boundary and the chat lost its card.
    The summary record carries the invoking /compact's promptId (13/13 manual compacts in the
    live corpus) — the designed link the adoption repair keys on; summary_pid=False models a
    write without it, which the repair must still adopt via the file-order fallback.
    Returns (records, stdout_uuid); chain the next prompt off the stdout, as the CLI does."""
    cb, cs, rt, cw, so = ("cb" + tag, "cs" + tag, "rt" + tag, "cw" + tag, "so" + tag)
    summary = compact_summary_line(t_done, cs, parent=cb, text=summary_text)
    if summary_pid:
        summary["promptId"] = "p" + tag
    recs = [
        compact_line(t_done, cb, logical_parent=parent, trigger="manual"),
        summary,
        {"type": "user", "timestamp": iso(t_issue), "uuid": rt, "parentUuid": parent,
         "isMeta": True, "promptId": "p" + tag,
         "message": {"role": "user", "content": "/compact"}},
        {"type": "user", "timestamp": iso(t_issue), "uuid": cw, "parentUuid": rt,
         "promptId": "p" + tag,
         "message": {"role": "user", "content": "<command-name>/compact</command-name>\n"
                                                "<command-message>compact</command-message>\n"
                                                "<command-args></command-args>"}},
        {"type": "user", "timestamp": iso(t_done), "uuid": so, "parentUuid": cw,
         "promptId": "p" + tag,
         "message": {"role": "user", "content": "<local-command-stdout>Compacted "
                                                "(ctrl+o to see full summary)</local-command-stdout>"}},
    ]
    return recs, so


def tasknote_line(t, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "system",
            "message": {"role": "user", "content": "<task-notification>\nbackground agent finished\n</task-notification>"}}


def postal_line(t, text, uuid, parent, mid=MID, ps=None):
    body = text + "\n<!-- romp-msg-id: %s -->" % mid
    return uline(t, body, uuid, parent, ps=ps)


SENT_LOG = [{"t": T0 + 190, "ev": "sent", "id": MID, "from": "feeddesign",
             "from_id": PEER, "to_id": SID, "body": "ASK: bump the alpha"}]


# ───────────────────────── scenarios ─────────────────────────
def scenario_multi_input_absorbed():
    """A typed opener, then a mid-turn prompt spliced in while the assistant is mid-tool, in the
    shape the CLI writes: the enqueue op (no uuid — never a landing witness), the tool_result the
    splice waited for, the remove op, then the queued_command ATTACHMENT — stamped with the ENQUEUE
    time (where the atom is placed) but written at the splice, so its file-order predecessor, the
    tool_result, is when the session took it (`landedT`). One turn, two inputs, two segments.
    Until 2026-09-06 this scenario had no tool_result and stamped the attachment at the remove
    time — a shape the CLI never writes — so the golden pinned a landedT 40 s BEFORE the send."""
    return [
        uline(T0, "refactor the ledger", "u1", ps="typed"),
        aline(T0 + 20, "Reading romp-ledger.", "a1", "u1", tools=("Read",), stop="tool_use"),
        qop(T0 + 40, "enqueue", "also rename the digest file"),
        trline(T0 + 55, "tu_a1_0", "tr1", "a1"),
        qop(T0 + 55, "remove"),
        attline(T0 + 40, "also rename the digest file", "att1", "tr1"),
        aline(T0 + 90, "Folded the rename in too.", "a2", "att1", stop="end_turn"),
    ]


def scenario_author_kinds():
    """Each turn-opener author (human / sdk / peer) opens a turn; a system
    (task-notification) atom folds into the current turn, never opens one."""
    return [
        uline(T0, "human typed prompt", "u1", ps="typed"),
        aline(T0 + 10, "ack human", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "sdk injected prompt", "u2", "a1", ps="sdk"),
        aline(T0 + 110, "ack sdk", "a2", "u2", stop="end_turn"),
        postal_line(T0 + 200, "ASK: bump the recency alpha", "u3", "a2"),
        aline(T0 + 210, "ack peer", "a3", "u3", stop="end_turn"),
        tasknote_line(T0 + 300, "u4", "a3"),                 # folds into the peer turn
        aline(T0 + 310, "continued after the task note", "a4", "u4", stop="end_turn"),
    ]


def scenario_queued_new_turn():
    """A prompt that arrives AFTER end_turn (a dequeued queued prompt is just a normal
    user line) opens a NEW turn — the position-based boundary, no queue-op needed."""
    return [
        uline(T0, "first ask", "u1", ps="typed"),
        aline(T0 + 20, "first reply", "a1", "u1", stop="end_turn"),
        qop(T0 + 30, "enqueue", "second ask"),
        qop(T0 + 60, "dequeue"),
        uline(T0 + 60, "second ask", "u2", "a1", ps="queued"),
        aline(T0 + 90, "second reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_compaction_atom():
    """A compact_boundary system line becomes one compaction atom (pre_tokens mapped);
    its paired isCompactSummary line is dropped as an atom but kept in the graph, so the
    pre-compaction turn stays on the active path via logicalParentUuid (the stitch)."""
    return [
        uline(T0, "long running refactor", "u1", ps="typed"),
        aline(T0 + 30, "Working through it.", "a1", "u1", tools=("Edit",), stop="end_turn"),
        compact_line(T0 + 500, "c1", logical_parent="a1", trigger="manual", pre=263239),
        compact_summary_line(T0 + 505, "cs1", parent="c1"),
        uline(T0 + 520, "continue post-compaction", "u2", "cs1", ps="sdk"),
        aline(T0 + 530, "Continuing.", "a2", "u2", stop="end_turn"),
    ]


def scenario_compaction_broken_stitch():
    """Real-data case (3/69 compactions): a compact_boundary whose logicalParentUuid points
    at a uuid present in NO transcript line. Followed blindly it orphans ALL pre-compaction
    history; the repair re-points the stitch at compactMetadata.preservedSegment.tailUuid
    (the real in-file pre-compaction leaf), so u1 is retained, not dropped."""
    return [
        uline(T0, "pre-compaction ask", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "pre-compaction reply", "a1", "u1", stop="end_turn"),
        compact_line_broken(T0 + 500, "c1", dangling_logical="ghost-pre-compaction-leaf",
                            preserved_tail="a1", trigger="auto", pre=99999),
        uline(T0 + 520, "post-compaction ask", "u2", parent="c1", ps="sdk"),
        aline(T0 + 530, "post-compaction reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_manual_compact_detached():
    """A LIVE manual /compact (the user 2026-08-19): the boundary + summary land as a DETACHED
    side branch (10/13 manual boundaries in the live corpus; the other 3 are resume re-splices
    that arrive attached) while the conversation chains through the /compact command wrappers.
    The walk never visited the branch, so no compact atom was emitted and the chat showed no
    "Context compacted" card — while auto-compactions, which chain THROUGH their boundary,
    kept theirs. The adoption repair splices the pair back in at its anchor."""
    side, so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
    return [
        uline(T0, "start the long build", "u1", ps="typed"),
        aline(T0 + 30, "Working on it.", "a1", "u1", stop="end_turn"),
    ] + side + [
        uline(T0 + 500, "carry on with the build", "u2", so, ps="typed"),
        aline(T0 + 530, "Continuing.", "a2", "u2", stop="end_turn"),
    ]


def scenario_idle_atom():
    """An idle atom is synthesized from a real idle transition in the state log (NOT a
    silence heuristic) and folds into the turn it follows; the gap colors as not-working."""
    return [
        uline(T0, "investigate the crash", "u1", ps="typed"),
        aline(T0 + 30, "Reproduced it.", "a1", "u1", tools=("Bash",), stop="end_turn"),
        uline(T0 + 3600, "continue please", "u2", "a1", ps="sdk"),     # revived an hour later
        aline(T0 + 3630, "Resumed work.", "a2", "u2", stop="end_turn"),
    ]


IDLE_STATES = [
    {"t": T0 + 30, "state": "working"},
    {"t": T0 + 60, "state": "idle"},          # idle span [T0+60, T0+3600)
    {"t": T0 + 3600, "state": "working"},
]


def scenario_popall():
    """popAll clears the whole queue at once: every still-queued item is spliced into the
    continuation as an absorbed mid-turn atom (the old code missed this op). The CLI's shape, as in
    scenario_multi_input_absorbed: the tool_result the splice waited for precedes the attachments,
    each stamped with its own ENQUEUE time, and both read that boundary as `landedT` (attachments
    are never witnesses for each other)."""
    return [
        uline(T0, "start the big task", "u1", ps="typed"),
        aline(T0 + 20, "Working.", "a1", "u1", tools=("Read",), stop="tool_use"),
        qop(T0 + 30, "enqueue", "first queued note"),
        qop(T0 + 40, "enqueue", "second queued note"),
        trline(T0 + 50, "tu_a1_0", "tr1", "a1"),
        qop(T0 + 50, "popAll"),
        attline(T0 + 30, "first queued note", "att1", "tr1"),
        attline(T0 + 40, "second queued note", "att2", "att1"),
        aline(T0 + 90, "Folded both notes in.", "a2", "att2", stop="end_turn"),
    ]


def scenario_clear_breaks_lineage():
    """`/clear` starts a fresh root (parentUuid:null) with no link to pre-clear history,
    so the leaf->root walk stops at it and pre-clear atoms drop out for free."""
    return [
        uline(T0, "pre-clear ask", "u1", ps="typed"),
        aline(T0 + 30, "pre-clear reply", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "post-clear ask", "u2", parent=None, ps="typed"),   # fresh root
        aline(T0 + 130, "post-clear reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_rewind_off_path():
    """A rewound branch (its chain rejoins the active spine at a1) is intentionally
    dropped; only the surviving attempt remains."""
    return [
        uline(T0, "first attempt", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "did it one way", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "abandoned follow-up", "u2", parent="a1", ps="typed"),   # rewound
        aline(T0 + 130, "going down a dead end", "a2", "u2", stop="end_turn"),
        uline(T0 + 200, "second attempt instead", "u3", parent="a1", ps="typed"),
        aline(T0 + 230, "better approach done", "a3", "u3", stop="end_turn"),
    ]


def scenario_broken_chain_kept():
    """Safety floor (this repo's one fatal error is silently dropping a real ask): a real
    prompt whose parentUuid points at a uuid that exists NOWHERE (corruption / partial
    write) is NOT a proven rewind and NOT a clean null root, so it is KEPT — unlike a
    rewind fork or a /clear branch, which are intentionally dropped."""
    return [
        uline(T0, "main line ask", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "main reply", "a1", "u1", stop="end_turn"),
        uline(T0 + 100, "orphaned but real ask", "ux", parent="ghost-missing-uuid", ps="typed"),
        aline(T0 + 130, "orphan reply", "ax", "ux", stop="end_turn"),
        uline(T0 + 200, "second main ask", "u2", parent="a1", ps="typed"),       # the active leaf line
        aline(T0 + 230, "second main reply", "a2", "u2", stop="end_turn"),
    ]


def scenario_slash_command_turn():
    """A turn opened ONLY by a slash command: the command line is skipped as an atom, but
    the assistant work that follows must still form a turn (trigger=null), never orphan."""
    return [
        {"type": "user", "timestamp": iso(T0), "uuid": "cmd1", "parentUuid": None,
         "message": {"role": "user", "content": "<command-name>/code-review</command-name>"}},
        aline(T0 + 30, "Reviewing the diff.", "a1", "cmd1", tools=("Bash",), stop="end_turn"),
    ]


# resume across a fork is two files; handled specially in run_scenario
def scenario_resume_lineage_fileA():
    return [
        uline(T0, "first ask before resume", "u1", ps="typed"),
        aline(T0 + 30, "reply in the parent transcript", "a1", "u1", stop="end_turn"),
    ]


def scenario_resume_lineage_fileB():
    # first line's parentUuid links into file A's a1 (a resume fork)
    return [
        uline(T0 + 100, "second ask after resume", "u2", parent="a1", ps="typed"),
        aline(T0 + 130, "reply in the resumed transcript", "a2", "u2", stop="end_turn"),
    ]


def sysline(t, subtype, uuid, parent, **extra):
    r = {"type": "system", "subtype": subtype, "timestamp": iso(t), "uuid": uuid,
         "parentUuid": parent}
    r.update(extra)
    return r


def scenario_eclipsed_branch_kept():
    """T209 (the user 2026-09-01): during a rate-limit storm the CLI buffers its api_error
    records and flushes them at the NEXT enqueue — chained off the turn's OPENING prompt —
    then hangs the stop hook and the next prompt on that spur. The turn's real output
    (thinking, tool work, the final reply the user watched render) falls off the
    leaf->root spine with no user gesture, and no orphanReply marker exists because the
    disk DID keep the text. The eclipsed verdict keeps the branch: >=1 api_error record
    sits STRICTLY BETWEEN the fork and the next conversational record, an exact machine
    event no genuine rollback produces (a rollback re-parents the user's next prompt
    directly at the cut)."""
    return [
        uline(T0, "please chart the throughput numbers", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "Charting the throughput numbers now.", "a1", "u1",
              tools=("Bash",), stop=None),
        trline(T0 + 40, "tu_a1_0", "tr1", "a1"),
        aline(T0 + 60, "Throughput rises linearly until the cache saturates.", "a2", "tr1",
              stop="end_turn"),
        # the buffered spur, flushed later in FILE order but parent-chained off u1
        sysline(T0 + 31, "api_error", "e1", "u1", level="error",
                error={"message": "429 rate_limit_error (synthetic)"}),
        sysline(T0 + 45, "api_error", "e2", "e1", level="error",
                error={"message": "429 rate_limit_error (synthetic)"}),
        sysline(T0 + 61, "stop_hook_summary", "sh1", "e2", hookCount=1),
        uline(T0 + 120, "thanks - now label the axes", "u2", parent="sh1", ps="typed"),
        aline(T0 + 150, "Labeled both axes.", "a3", "u2", stop="end_turn"),
    ]


def scenario_retry_superseded():
    """The other side of the eclipse discriminator: when the spine RE-REPLIED after the
    api_error records (an assistant record ends the fork probe before any user prompt),
    the off-path partial is a superseded attempt — kept OFF, exactly as before, so a
    retry that re-replied never doubles (the orphan salvage's own dedup rule)."""
    return [
        uline(T0, "summarize the log file", "u1", parent=None, ps="typed"),
        aline(T0 + 30, "Half an answer that a retry replaced.", "a_old", "u1", stop=None),
        sysline(T0 + 31, "api_error", "e1", "u1", level="error",
                error={"message": "429 rate_limit_error (synthetic)"}),
        aline(T0 + 90, "The full answer after the retry.", "a_new", "e1", stop="end_turn"),
    ]


SINGLE_FILE = {
    "multi_input_absorbed": (scenario_multi_input_absorbed, None),
    "author_kinds": (scenario_author_kinds, SENT_LOG),
    "queued_new_turn": (scenario_queued_new_turn, None),
    "compaction_atom": (scenario_compaction_atom, None),
    "compaction_broken_stitch": (scenario_compaction_broken_stitch, None),
    "manual_compact_detached": (scenario_manual_compact_detached, None),
    "idle_atom": (scenario_idle_atom, IDLE_STATES),
    "popall": (scenario_popall, None),
    "clear_breaks_lineage": (scenario_clear_breaks_lineage, None),
    "rewind_off_path": (scenario_rewind_off_path, None),
    "broken_chain_kept": (scenario_broken_chain_kept, None),
    "slash_command_turn": (scenario_slash_command_turn, None),
    "eclipsed_branch_kept": (scenario_eclipsed_branch_kept, None),
    "retry_superseded": (scenario_retry_superseded, None),
}

# fsid stems for the resume scenario (placeholder UUIDs)
FSID_A = "aaaaaaaa-0000-0000-0000-000000000000"
FSID_B = "bbbbbbbb-0000-0000-0000-000000000000"


def run_single(name):
    records, sent = SINGLE_FILE[name]
    states = IDLE_STATES if name == "idle_atom" else None
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (SID + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        return em.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR",
                                candidate_files=[str(path)], states=states,
                                postal_log=sent or [], now=NOW)


def run_recs(records):
    """Run the event model over a raw record list (for ad-hoc, non-golden scenarios)."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (SID + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return em.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR",
                                candidate_files=[str(path)], states=None, postal_log=[], now=NOW)


def run_resume():
    with tempfile.TemporaryDirectory() as td:
        pa = Path(td) / (FSID_A + ".jsonl")
        pb = Path(td) / (FSID_B + ".jsonl")
        pa.write_text("\n".join(json.dumps(r) for r in scenario_resume_lineage_fileA()) + "\n")
        pb.write_text("\n".join(json.dumps(r) for r in scenario_resume_lineage_fileB()) + "\n")
        return em.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR",
                                candidate_files=[str(pa), str(pb)], states=None,
                                postal_log=[], now=NOW)


def run_scenario(name):
    return run_resume() if name == "resume_lineage" else run_single(name)


ALL_SCENARIOS = list(SINGLE_FILE) + ["resume_lineage"]


# ───────────────────────── golden comparison ─────────────────────────
class GoldenTests(unittest.TestCase):
    maxDiff = None


def _add_case(name):
    def test(self):
        gp = GOLDEN / (name + ".json")
        self.assertTrue(gp.exists(), "missing golden %s — run with --regen and review" % gp)
        expected = json.loads(gp.read_text())
        actual = json.loads(json.dumps(run_scenario(name)))
        self.assertEqual(expected, actual,
                         "tree changed for %r — if intended, --regen and review the diff" % name)
    setattr(GoldenTests, "test_" + name, test)


for _n in ALL_SCENARIOS:
    _add_case(_n)


# ───────────────────────── invariant unit tests ─────────────────────────
def _authors(turns):
    return [t["trigger"] and _trigger_author(t) for t in turns]


def _trigger_author(turn):
    trig = turn["trigger"]
    if not trig:
        return None
    a = next((x for x in turn["atoms"] if x.get("uuid") == trig["uuid"]), None)
    return a.get("author") if a else None


class Authorship(unittest.TestCase):
    def test_opener_authors_human_sdk_peer(self):
        out = run_scenario("author_kinds")
        self.assertEqual([_trigger_author(t) for t in out["turns"]],
                         ["human", "sdk", {"peer": PEER, "mid": MID, "kind": ""}])

    def test_romp_auto_marker_flags_rompAuto_but_a_button_nudge_does_not(self):
        # An AUTO-nudge (kernel _auto_nudge_tick) carries romp-injected AND romp-auto → its trigger atom is
        # flagged rompAuto; a Nudge BUTTON (romp-injected only) is NOT (the user 2026-06-23). The timeline/chat
        # key the romp-logo on rompAuto, so only auto-nudges (+ postal) are marked, never the user's clicks.
        recs = [
            uline(T0, "Status?\n\n<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: g1 -->", "u1"),
            aline(T0 + 10, "ok", "a1", "u1"),
            uline(T0 + 100, "Nudge\n\n<!-- romp-injected --><!-- romp-goal-id: g1 -->", "u2", "a1"),
            aline(T0 + 110, "ok2", "a2", "u2"),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR",
                                   candidate_files=[str(path)], states=None, postal_log=[], now=NOW)
        def trig_atom(turn):
            trig = turn.get("trigger") or {}
            return next((x for x in turn["atoms"] if x.get("uuid") == trig.get("uuid")), None)
        autos = [bool((trig_atom(t) or {}).get("rompAuto")) for t in out["turns"] if t.get("trigger")]
        self.assertEqual(autos, [True, False], "auto-nudge trigger is rompAuto; the button-nudge trigger is not")

    def test_system_task_notification_folds_in(self):
        out = run_scenario("author_kinds")
        self.assertEqual(len(out["turns"]), 3, "system atom must NOT open a turn")
        peer_turn = out["turns"][2]
        sysauthors = [a.get("author") for a in peer_turn["atoms"]
                      if a["type"] == "user" and a.get("author") == "system"]
        self.assertEqual(sysauthors, ["system"], "task-notification folds into the peer turn")

    def test_peer_rompuuid_resolved_from_messages_log(self):
        out = run_scenario("author_kinds")
        self.assertEqual(_trigger_author(out["turns"][2]).get("peer"), PEER)

    def test_peer_null_when_id_absent_from_log(self):
        # same postal marker, but the message id is not in the log -> peer rompUuid null
        with tempfile.TemporaryDirectory() as td:
            recs = [postal_line(T0, "ASK: do a thing", "u1", None),
                    aline(T0 + 20, "done", "a1", "u1", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        self.assertEqual(_trigger_author(out["turns"][0]).get("peer"), None)


class RompInjectedAuthor(unittest.TestCase):
    """author_of('romp') ONLY for a message romp itself injected into a pane (a feed NUDGE / auto-nudge /
    Retry) — the romp-injected marker makes it a SYSTEM message so the chat renders the gray romp bubble
    instead of the blue user bubble. A follow-up the user TYPES carries only romp-goal-id ("which goal",
    for the reopen) and stays 'human' — it's the user's words, not romp's (the user 2026-06-20)."""

    @staticmethod
    def _blocks(text):
        return [{"type": "text", "text": text}]

    def test_goal_id_alone_is_human_a_typed_follow_up(self):
        # a follow-up the USER types: the kernel tags it with romp-goal-id (for the reopen) but NOT
        # romp-injected — it's the user's message, so it must render as the blue human bubble, not romp.
        b = self._blocks("> the goal\n\nWhat did you change?\n\n<!-- romp-goal-id: sid:g1 -->")
        self.assertEqual(em.author_of(b, "typed", {}), "human",
                         "romp-goal-id is 'which goal' metadata, not authorship — a typed follow-up stays human")

    def test_explicit_romp_injected_marker_authors_romp(self):
        b = self._blocks("Picking this back up.\n\n<!-- romp-injected -->")
        self.assertEqual(em.author_of(b, None, {}), "romp")

    def test_nudge_has_both_markers_and_authors_romp(self):
        # a Nudge button / auto-nudge: the kernel adds BOTH romp-injected (gray bubble) and romp-goal-id
        # (reopen). romp-injected wins over promptSource=typed — the nudge is pasted, not typed by you.
        b = self._blocks("> the goal\n\nStatus on the goal above?\n\n<!-- romp-injected --><!-- romp-goal-id: sid:g1 -->")
        self.assertEqual(em.author_of(b, "typed", {}), "romp",
                         "romp-injected authors romp even though promptSource=typed")

    def test_plain_typed_prompt_is_still_human(self):
        self.assertEqual(em.author_of(self._blocks("just a normal message"), "typed", {}), "human")

    def test_postal_marker_still_wins_for_a_peer_message(self):
        b = self._blocks("DELEGATE: do a thing\n\n<!-- romp-msg-id: m1 -->")
        self.assertEqual(em.author_of(b, "typed", {"m1": PEER}).get("peer"), PEER,
                         "a real peer message stays a peer card, not a romp injection")


class TurnBoundaries(unittest.TestCase):
    def test_absorbed_prompt_stays_in_turn(self):
        out = run_scenario("multi_input_absorbed")
        self.assertEqual(len(out["turns"]), 1, "an absorbed mid-turn prompt must not open a turn")
        inputs = [a for a in out["turns"][0]["atoms"]
                  if a["type"] == "user" and a.get("author") == "human"]
        self.assertEqual(len(inputs), 2, "the turn holds two inputs (opener + absorbed)")

    def test_absorbed_atom_anchors_on_attachment(self):
        out = run_scenario("multi_input_absorbed")
        absorbed = [a for a in out["turns"][0]["atoms"]
                    if a.get("uuid") == "att1" and a["type"] == "user"]
        self.assertEqual(len(absorbed), 1, "absorbed atom anchors on the queued_command attachment")

    def test_prompt_after_end_turn_opens_new_turn(self):
        out = run_scenario("queued_new_turn")
        self.assertEqual(len(out["turns"]), 2)
        self.assertEqual([t["trigger"]["uuid"] for t in out["turns"]], ["u1", "u2"])

    def test_romp_nudge_opens_its_own_turn_so_the_judges_process_it(self):
        # the user 2026-06-21: a romp NUDGE / auto-nudge (author 'romp', carrying romp-injected + romp-goal-id)
        # is a fresh prompt to the agent and MUST open its own turn. Before this, it folded into the prior
        # (already-completed) turn — the planner never read the romp-goal-id off a trigger, so the goal never
        # reopened and NO judge ran on the follow-up. Now it opens a turn with the nudge atom as the trigger.
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do the thing", "u1", ps="typed"),
                    aline(T0 + 10, "did it, done", "a1", "u1", stop="end_turn"),
                    uline(T0 + 100, "Status on the goal above?\n\n<!-- romp-injected --><!-- romp-goal-id: %s:g1 -->" % SID,
                          "u2", "a1", ps="typed"),
                    aline(T0 + 110, "everything is done", "a2", "u2", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        self.assertEqual(len(out["turns"]), 2, "the nudge opens a SECOND turn, not folded into the completed one")
        self.assertEqual(_trigger_author(out["turns"][1]), "romp", "the second turn is opened by the romp nudge")
        self.assertEqual(out["turns"][1]["trigger"]["uuid"], "u2",
                         "the nudge atom is the trigger — so _seg_followup reads its romp-goal-id and reopens the goal")


class TurnVsSegment(unittest.TestCase):
    """A turn is end_turn-bounded (may hold several inputs); a segment is the per-input
    span. The absorbed turn is ONE turn but TWO segments."""

    def test_absorbed_turn_is_one_turn_two_segments(self):
        out = run_scenario("multi_input_absorbed")
        turn = out["turns"][0]
        segs = em.segments(turn)
        self.assertEqual(len(segs), 2)
        self.assertEqual([s["trigger"] for s in segs], ["u1", "att1"])

    def test_popall_turn_three_segments(self):
        out = run_scenario("popall")
        self.assertEqual(len(out["turns"]), 1)
        segs = em.segments(out["turns"][0])
        self.assertEqual(len(segs), 3, "opener + two popAll-absorbed inputs = three segments")


class EndedInference(unittest.TestCase):
    def test_ended_true_on_end_turn(self):
        out = run_scenario("queued_new_turn")
        self.assertTrue(all(t["ended"] for t in out["turns"]))

    def test_ended_false_when_last_assistant_is_tool_use(self):
        # a turn whose last assistant line stopped on tool_use (interrupted / still working)
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do the thing", "u1", ps="typed"),
                    aline(T0 + 20, "calling a tool", "a1", "u1", tools=("Bash",), stop="tool_use")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        self.assertFalse(out["turns"][0]["ended"])


class Compaction(unittest.TestCase):
    def test_compaction_atom_shape(self):
        out = run_scenario("compaction_atom")
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0]["compact_metadata"],
                         {"trigger": "manual", "pre_tokens": 263239, "post_tokens": 6514})

    def test_compact_summary_line_not_emitted(self):
        out = run_scenario("compaction_atom")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("cs1", uuids, "the isCompactSummary payload is not an atom")

    def test_compaction_summary_is_attached_to_the_boundary_atom(self):
        # the summary payload is not its own atom, but its TEXT rides the boundary atom so the chat can show
        # it in a collapsible box (the user 2026-07-07).
        out = run_scenario("compaction_atom")
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0].get("summary"), "summary of the conversation so far")

    def test_long_compaction_summary_is_capped(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do task X", "u1", ps="typed"),
                    aline(T0 + 30, "did X", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    {"type": "user", "timestamp": iso(T0 + 505), "uuid": "cs1", "parentUuid": "c1",
                     "isCompactSummary": True, "message": {"role": "user", "content": "x" * 9000}}]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(len(comp), 1)
        s = comp[0]["summary"]
        self.assertTrue(s.endswith("…(summary truncated)"), "an over-cap summary is truncated with a marker")
        self.assertLessEqual(len(s), em.SUMMARY_CAP + 40, "capped near SUMMARY_CAP")

    def test_post_compaction_replay_is_deduped(self):
        # the user 2026-06-22: compaction RESTORES the recent message tail verbatim (new uuids + timestamps).
        # Those replayed user prompts are NOT new work — without dedup the judges re-process them and re-mint
        # already-done (even CLEARED) goals with fresh ids that escape the clear. A post-compaction user
        # message whose text matches an earlier one is dropped; genuinely-new post-compaction work is kept.
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do task X", "u1", ps="typed"),
                    aline(T0 + 30, "did X", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    compact_summary_line(T0 + 505, "cs1", parent="c1"),
                    uline(T0 + 520, "do task X", "u2", "cs1", ps="sdk"),     # REPLAY of u1 (restored tail)
                    aline(T0 + 530, "redid X", "a2", "u2", stop="end_turn"),
                    uline(T0 + 600, "do task Z", "u3", "a2", ps="typed"),    # GENUINE new work
                    aline(T0 + 610, "did Z", "a3", "u3", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        utexts = [em._text_of(em._content(a.get("message"))) for t in out["turns"] for a in t["atoms"]
                  if a["type"] == "user"]
        self.assertEqual(utexts.count("do task X"), 1, "the replayed prompt is deduped — only the original remains")
        self.assertEqual(utexts.count("do task Z"), 1, "genuinely-new post-compaction work is kept")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("u2", uuids, "the replay atom (u2) is dropped, so the planner can't re-mint its goal")
        self.assertIn("u1", uuids, "the pre-compaction original survives")
        self.assertIn("u3", uuids, "genuine new work survives")

    def test_a_repeat_of_an_old_message_is_not_a_replay(self):
        """THE BUG (the user 2026-08-01): a message they sent, answered by the session, absent from the chat.

        The replay guard keyed on TEXT ALONE, so once a session had compacted, the second time anyone said
        a thing they had said before — "Now?", "retry", "[Request interrupted by user]", a romp notice —
        it was read as restored context and dropped, however many days apart. The reply still rendered,
        which is what made it look like the chat had lost a message rather than dropped one on purpose.
        A repeat AFTER work resumed is a message the person actually sent."""
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "Now?", "u1", ps="typed"),
                    aline(T0 + 30, "not yet", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    compact_summary_line(T0 + 505, "cs1", parent="c1"),
                    aline(T0 + 600, "carrying on", "a2", "cs1", stop="end_turn"),
                    uline(T0 + 90000, "Now?", "u2", "a2", ps="sdk"),        # the SAME word, a day later
                    aline(T0 + 90030, "333/358 done", "a3", "u2", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u2", uuids, "the repeat is a real message and must render")
        self.assertIn("u1", uuids, "…and so does the original")
        self.assertIn("a3", uuids, "the reply was never in doubt — which is what made the loss confusing")

    def test_a_verbatim_rewrite_at_the_same_second_is_still_deduped(self):
        # the other measured replay shape: the SAME record written twice (resume/compaction re-splice),
        # identical text at an identical timestamp — deduped wherever it lands, restore burst or not.
        with tempfile.TemporaryDirectory() as td:
            recs = [uline(T0, "do task X", "u1", ps="typed"),
                    aline(T0 + 30, "did X", "a1", "u1", stop="end_turn"),
                    compact_line(T0 + 500, "c1", logical_parent="a1"),
                    compact_summary_line(T0 + 505, "cs1", parent="c1"),
                    aline(T0 + 600, "carrying on", "a2", "cs1", stop="end_turn"),
                    uline(T0, "do task X", "u2", "a2", ps="sdk"),          # same text AND same second as u1
                    aline(T0 + 700, "again", "a3", "u2", stop="end_turn")]
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            out = em.parse_session(str(p), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(p)],
                                   postal_log=[], now=NOW)
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("u2", uuids, "a verbatim re-write is the same record, not a second message")
        self.assertIn("u1", uuids)

    def test_pre_compaction_turn_survives_via_logical_parent(self):
        out = run_scenario("compaction_atom")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u1", uuids, "the stitch (logicalParentUuid) keeps pre-compaction history on path")
        self.assertIn("a1", uuids)

    def test_broken_stitch_repaired_via_preserved_segment(self):
        """When logicalParentUuid dangles, preservedSegment.tailUuid repairs the stitch so
        pre-compaction history is retained instead of orphaned."""
        out = run_scenario("compaction_broken_stitch")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u1", uuids, "pre-compaction history must survive a dangling logicalParentUuid")
        self.assertIn("a1", uuids)
        self.assertIn("u2", uuids)
        comp = [a for t in out["turns"] for a in t["atoms"] if a.get("subtype") == "compact_boundary"]
        self.assertEqual(comp[0]["parentUuid"], "a1", "the compaction atom's parent is the repaired stitch")


class DetachedManualCompaction(unittest.TestCase):
    """A LIVE manual /compact writes its boundary + summary as a DETACHED side branch — the
    boundary has parentUuid:null + logicalParentUuid:<pre-compact leaf>, the summary is its only
    child, and the conversation chains through the /compact command wrappers instead, so the
    leaf->root walk never visits the pair and the compact atom (the chat's "Context compacted"
    card) was silently never emitted (the user 2026-08-19; 10/13 manual boundaries in the live
    corpus). The adoption repair (FileAdapter._adopt_detached_compactions) splices the pair in
    at its anchor — keyed on the SHAPE (boundary off the active path, its anchor on it), never
    on trigger=manual: an attached manual must no-op, a detached auto must be adopted."""

    def _flat(self, out):
        return [a for t in out["turns"] for a in t["atoms"]]

    def _cards(self, out):
        return [a for a in self._flat(out) if a.get("subtype") == "compact_boundary"]

    def test_detached_manual_boundary_is_adopted_and_emits_the_card_atom(self):
        out = run_scenario("manual_compact_detached")
        comp = self._cards(out)
        self.assertEqual(len(comp), 1, "the detached boundary is adopted — the card exists")
        self.assertEqual(comp[0]["uuid"], "cb1")
        self.assertEqual(comp[0]["compact_metadata"]["trigger"], "manual")
        self.assertEqual(comp[0].get("summary"), "summary of the conversation so far",
                         "the side-branch summary rides the adopted atom, same as an attached one")
        self.assertEqual(comp[0]["parentUuid"], "so1",
                         "the adopted atom chains from its episode's stdout — the /compact exchange "
                         "stays intact on the path, with the pre-compact leaf reachable through it")

    def test_adopted_card_sits_where_the_compact_completed(self):
        out = run_scenario("manual_compact_detached")
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertLess(uuids.index("cw1"), uuids.index("cb1"),
                        "the card follows the /compact invocation")
        self.assertLess(uuids.index("so1"), uuids.index("cb1"),
                        "…AFTER the whole command exchange: splicing before the stdout pulled that "
                        "atom into the boundary's fresh turn as assistant work, minting a phantom "
                        "WORK unit per manual compact (2026-08-19 review)")
        self.assertLess(uuids.index("cb1"), uuids.index("u2"),
                        "…and precedes the next prompt — the moment the compaction happened")

    def test_adopted_boundary_opens_a_fresh_turn(self):
        out = run_scenario("manual_compact_detached")
        bturn = next(t for t in out["turns"] if any(a.get("uuid") == "cb1" for a in t["atoms"]))
        self.assertEqual(bturn["atoms"][0].get("uuid"), "cb1",
                         "the boundary anchors its own turn, exactly as an attached one does")
        self.assertIsNone(bturn["trigger"])
        u2turn = next(t for t in out["turns"] if any(a.get("uuid") == "u2" for a in t["atoms"]))
        self.assertEqual(u2turn["trigger"], {"uuid": "u2"},
                         "the genuine post-compact prompt still opens its own turn")

    def test_adoption_splices_in_and_never_reroutes_around(self):
        # everything that was already kept stays kept: the pre-compact history, the /compact
        # command atom, its stdout output, the post-compact conversation
        out = run_scenario("manual_compact_detached")
        uuids = [a.get("uuid") for a in self._flat(out)]
        for u in ("u1", "a1", "cw1", "so1", "u2", "a2"):
            self.assertIn(u, uuids)
        self.assertNotIn("cs1", uuids, "the summary is captured, never its own atom")

    def test_attached_auto_boundary_is_not_double_emitted(self):
        # the no-op leg, keyed on shape: an ATTACHED boundary (an auto-compaction's normal
        # shape — the continuation chains through boundary+summary) emits exactly once
        recs = [uline(T0, "do the long task", "u1", ps="typed"),
                aline(T0 + 30, "on it", "a1", "u1", stop="end_turn"),
                compact_line(T0 + 500, "cb1", logical_parent="a1", trigger="auto"),
                compact_summary_line(T0 + 505, "cs1", parent="cb1"),
                uline(T0 + 520, "carry on please", "u2", "cs1", ps="sdk"),
                aline(T0 + 530, "done", "a2", "u2", stop="end_turn")]
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual(len(comp), 1, "an attached boundary emits exactly once — the repair stands down")

    def test_attached_manual_resume_resplice_is_not_double_emitted(self):
        # the corpus's other manual shape: a RESUME rebuild re-splices the boundary into the
        # chain, arriving attached (the compaction_atom scenario IS that shape). The repair
        # must not fight the resume shape — same no-op leg, still exactly one atom.
        out = run_scenario("compaction_atom")
        self.assertEqual(len(self._cards(out)), 1)

    def test_two_sequential_manual_compacts_each_get_their_card_in_order(self):
        side1, so1 = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1",
                                          summary_text="first compact summary")
        side2, so2 = manual_compact_lines(T0 + 290, T0 + 300, "2", parent="a2",
                                          summary_text="second compact summary")
        recs = ([uline(T0, "kick off phase one", "u1", ps="typed"),
                 aline(T0 + 30, "phase one done", "a1", "u1", stop="end_turn")]
                + side1
                + [uline(T0 + 150, "kick off phase two", "u2", so1, ps="typed"),
                   aline(T0 + 180, "phase two done", "a2", "u2", stop="end_turn")]
                + side2
                + [uline(T0 + 350, "kick off phase three", "u3", so2, ps="typed"),
                   aline(T0 + 380, "phase three done", "a3", "u3", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([a["uuid"] for a in comp], ["cb1", "cb2"],
                         "each compaction adopts at its own anchor — two cards, in order")
        self.assertEqual([a.get("summary") for a in comp],
                         ["first compact summary", "second compact summary"])
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertLess(uuids.index("cb1"), uuids.index("u2"))
        self.assertLess(uuids.index("u2"), uuids.index("cb2"))
        self.assertLess(uuids.index("cb2"), uuids.index("u3"))
        # BOTH stdouts render, identical text and all: an adopted boundary never arms the
        # restore-burst dedup (a live manual compact replays no tail), so nothing after it is
        # "restored context". The first cut pinned so2 as eaten — that pin was the bug's own
        # signature, not a goal (2026-08-19 review).
        self.assertIn("so1", uuids)
        self.assertIn("so2", uuids)

    def test_boundary_whose_anchor_was_rewound_away_stays_hidden(self):
        # a detached boundary whose OWN anchor is off the active path — its pre-compact context
        # was rewound away, so the compaction is not part of visible history. No card. Pinned.
        side, _so = manual_compact_lines(T0 + 190, T0 + 200, "1", parent="ax")
        recs = ([uline(T0, "main line ask", "u1", ps="typed"),
                 aline(T0 + 30, "main reply", "a1", "u1", stop="end_turn"),
                 uline(T0 + 100, "abandoned tangent", "ux", "a1", ps="typed"),
                 aline(T0 + 130, "tangent reply", "ax", "ux", stop="end_turn")]
                + side
                + [uline(T0 + 300, "back on the main line", "u2", "a1", ps="typed"),
                   aline(T0 + 330, "continuing main", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        self.assertEqual(self._cards(out), [],
                         "a compaction whose context was rewound away stays hidden")
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertNotIn("ux", uuids, "…and the rewound branch stays dropped")

    def test_repeated_typed_prompt_after_manual_compact_renders(self):
        # The fatal shape (2026-08-19 review): the user's GENUINE next prompt after a live
        # manual compact repeats an earlier message's text ("continue", "retry", a nudge).
        # Arming the restore-burst dedup on the adopted boundary read it as replayed context
        # and silently dropped a real ask — the one loss class this file exists to prevent.
        side, so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
        recs = ([uline(T0, "run the full test suite", "u1", ps="typed"),
                 aline(T0 + 30, "all green", "a1", "u1", stop="end_turn")]
                + side
                + [uline(T0 + 500, "run the full test suite", "u2", so, ps="typed"),
                   aline(T0 + 530, "running now", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertIn("u2", uuids, "the repeated-text prompt is the user's real ask, not a replay")
        self.assertEqual([c["uuid"] for c in self._cards(out)], ["cb1"])

    def test_rewound_manual_compact_is_not_resurrected(self):
        # The user rewinds PAST the /compact: the next prompt re-parents at the pre-compact
        # leaf, so the episode (wrappers + stdout) is off-path but the ANCHOR is still on it.
        # Gating on the bare anchor resurrected the undone compaction's card (2026-08-19
        # review); the episode gate keeps it hidden with the history it belonged to.
        side, _so = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1")
        recs = ([uline(T0, "kick off the refactor", "u1", ps="typed"),
                 aline(T0 + 30, "refactor staged", "a1", "u1", stop="end_turn")]
                + side
                + [uline(T0 + 200, "different direction instead", "u2", "a1", ps="typed"),
                   aline(T0 + 230, "sure", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertNotIn("cw1", uuids, "the rewound /compact exchange stays dropped")
        self.assertNotIn("so1", uuids)
        self.assertEqual(self._cards(out), [], "an undone compaction gets no card")

    def test_same_anchor_double_compact_only_the_live_one_renders(self):
        # Compact, rewind to the anchor, compact again: two detached boundaries share ONE
        # anchor. Splicing both at the anchor threaded them through each other — boundary #1's
        # parent became boundary #2's summary, and the rewound one rendered (2026-08-19
        # review). Each boundary belongs to its OWN episode; only the live episode is on-path.
        side1, _so1 = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1",
                                           summary_text="first summary")
        side2, so2 = manual_compact_lines(T0 + 290, T0 + 300, "2", parent="a1",
                                          summary_text="second summary")
        recs = ([uline(T0, "start it", "u1", ps="typed"),
                 aline(T0 + 30, "started", "a1", "u1", stop="end_turn")]
                + side1 + side2
                + [uline(T0 + 400, "go on", "u2", so2, ps="typed"),
                   aline(T0 + 430, "going", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb2"],
                         "only the live compaction renders; the rewound one stays hidden")
        self.assertEqual(comp[0]["parentUuid"], "so2",
                         "…chained from its OWN episode's stdout, never through the other pair")
        self.assertEqual(comp[0].get("summary"), "second summary")

    def test_summary_without_promptid_adopts_via_the_file_order_fallback(self):
        # An older write whose summary lacks the promptId link: the episode is still
        # identified — the nearest /compact invoked from the boundary's anchor and appended
        # after it (the CLI writes boundary+summary first, then the episode records).
        side, so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1",
                                        summary_pid=False)
        recs = ([uline(T0, "start the long build", "u1", ps="typed"),
                 aline(T0 + 30, "Working on it.", "a1", "u1", stop="end_turn")]
                + side
                + [uline(T0 + 500, "carry on with the build", "u2", so, ps="typed"),
                   aline(T0 + 530, "Continuing.", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1"])
        self.assertEqual(comp[0].get("summary"), "summary of the conversation so far")

    def test_self_anchored_boundary_is_skipped(self):
        # a corrupt boundary whose logicalParentUuid is its own uuid: no card, no crash —
        # and no 1-cycle handed to any walk (the parent link is dropped at load)
        recs = [uline(T0, "only ask", "u1", ps="typed"),
                aline(T0 + 30, "only answer", "a1", "u1", stop="end_turn"),
                compact_line(T0 + 50, "cbS", logical_parent="cbS", trigger="manual")]
        out = run_recs(recs)
        self.assertEqual(self._cards(out), [])

    def test_stdout_stays_in_the_command_turn_and_the_boundary_turn_holds_no_work(self):
        # Defect 2's event-model contract (2026-08-19 review): the /compact stdout is the
        # command exchange's output — it must never migrate into the boundary's fresh turn,
        # where it reads as assistant work and mints a judge-visible unit.
        out = run_scenario("manual_compact_detached")
        cmd_turn = next(t for t in out["turns"] if any(a.get("uuid") == "cw1" for a in t["atoms"]))
        self.assertIn("so1", [a.get("uuid") for a in cmd_turn["atoms"]])
        bturn = next(t for t in out["turns"] if any(a.get("uuid") == "cb1" for a in t["atoms"]))
        self.assertEqual([a.get("uuid") for a in bturn["atoms"]], ["cb1"],
                         "the boundary's turn holds the boundary alone")

    def test_crash_truncated_compact_never_steals_a_same_anchor_retrys_episode(self):
        # The mid-write window's worst case: boundary+summary landed, the CLI died before the
        # episode records, then the user compacted AGAIN from the same anchor. The stale
        # summary's promptId names nothing on record — the designed link failed, so the stale
        # boundary stays hidden. Degrading to the file-order fallback handed it the RETRY's
        # episode: the stale summary rendered at the live splice, and the already-claimed
        # guard then hid the real compact's card (2026-08-19 second review).
        stale = compact_summary_line(T0 + 100, "cs1", parent="cb1",
                                     text="stale interrupted summary")
        stale["promptId"] = "p1"
        side2, so2 = manual_compact_lines(T0 + 290, T0 + 300, "2", parent="a1",
                                          summary_text="second live summary")
        recs = ([uline(T0, "kick off the sweep", "u1", ps="typed"),
                 aline(T0 + 30, "sweep done", "a1", "u1", stop="end_turn"),
                 compact_line(T0 + 100, "cb1", logical_parent="a1", trigger="manual"),
                 stale]
                + side2
                + [uline(T0 + 400, "keep going", "u2", so2, ps="typed"),
                   aline(T0 + 430, "going", "a2", "u2", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb2"],
                         "only the retry renders — a promptId that names no on-record episode "
                         "keeps its boundary hidden, never falls to adjacency")
        self.assertEqual(comp[0]["parentUuid"], "so2")
        self.assertEqual(comp[0].get("summary"), "second live summary")

    def test_replayed_episode_copy_never_reseats_the_card(self):
        # A later auto compaction's restore burst can replay the manual /compact episode
        # records VERBATIM — new uuids, promptId preserved (the documented replay shape).
        # The card's splice is the ORIGINAL episode, the copy seq-nearest its own
        # boundary+summary pair: last-copy-wins re-seated the card after the auto card, on
        # a parent atom the replay dedup drops (2026-08-19 second review).
        side1, so1 = manual_compact_lines(T0 + 90, T0 + 100, "1", parent="a1",
                                          summary_text="manual compact summary")
        auto_summary = compact_summary_line(T0 + 500, "csA", parent="cbA",
                                            text="auto compact summary")
        auto_summary["promptId"] = "pA"
        replay = [
            {"type": "user", "timestamp": iso(T0 + 501), "uuid": "rt1r", "parentUuid": "csA",
             "isMeta": True, "promptId": "p1",
             "message": {"role": "user", "content": "/compact"}},
            {"type": "user", "timestamp": iso(T0 + 501), "uuid": "cw1r", "parentUuid": "rt1r",
             "promptId": "p1",
             "message": {"role": "user", "content": "<command-name>/compact</command-name>\n"
                                                    "<command-message>compact</command-message>\n"
                                                    "<command-args></command-args>"}},
            {"type": "user", "timestamp": iso(T0 + 501), "uuid": "so1r", "parentUuid": "cw1r",
             "promptId": "p1",
             "message": {"role": "user", "content": "<local-command-stdout>Compacted "
                                                    "(ctrl+o to see full summary)"
                                                    "</local-command-stdout>"}},
            uline(T0 + 501, "step two of the migration", "u2r", "so1r", ps="typed"),
        ]
        recs = ([uline(T0, "start the migration", "u1", ps="typed"),
                 aline(T0 + 30, "migration started", "a1", "u1", stop="end_turn")]
                + side1
                + [uline(T0 + 200, "step two of the migration", "u2", so1, ps="typed"),
                   aline(T0 + 230, "step two done", "a2", "u2", stop="end_turn"),
                   compact_line(T0 + 500, "cbA", logical_parent="a2", trigger="auto"),
                   auto_summary]
                + replay
                + [aline(T0 + 540, "resuming after the auto compact", "a3", "u2r",
                         stop="end_turn"),
                   uline(T0 + 600, "now step three", "u3", "a3", ps="typed"),
                   aline(T0 + 630, "step three done", "a4", "u3", stop="end_turn")])
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1", "cbA"])
        self.assertEqual(comp[0]["parentUuid"], "so1",
                         "the card seats at the ORIGINAL episode's stdout, never a replayed copy")
        self.assertEqual(comp[0].get("summary"), "manual compact summary")
        uuids = [a.get("uuid") for a in self._flat(out)]
        self.assertLess(uuids.index("cb1"), uuids.index("cbA"),
                        "…so it stays where the manual compact happened, before the auto card")
        self.assertLess(uuids.index("so1"), uuids.index("cb1"))
        self.assertLess(uuids.index("cb1"), uuids.index("u2"))

    def test_mid_write_wrapper_leaf_build_adopts_at_the_wrapper(self):
        # The mid-write phase the episode-scan comment describes: the wrapper is the file
        # leaf, the stdout not yet written. The pair is NOT hidden — it adopts at the
        # episode's last landed record for this one build (parent = the wrapper) and
        # re-seats at the stdout next parse (the full-shape tests above ARE that parse).
        side, _so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
        cut = side[:-1]           # boundary, summary, caveat twin, wrapper — no stdout yet
        recs = ([uline(T0, "start the long build", "u1", ps="typed"),
                 aline(T0 + 30, "build started", "a1", "u1", stop="end_turn")]
                + cut)
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1"])
        self.assertEqual(comp[0]["parentUuid"], "cw1",
                         "mid-write, the card seats at the episode's last landed record")
        # ADOPTED is also the dedup's off-switch (the emit loop keys on _adopted membership).
        # Nothing can follow the wrapper in this phase, so no atoms-level probe exists —
        # pin the switch itself on the adapter.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            adapter = em.FileAdapter([str(p)], p)
            self.assertIn("cb1", adapter._adopted,
                          "mid-write adoption keeps the restore dedup unarmed")

    def test_mid_write_summary_leaf_build_is_attached_by_shape(self):
        # One write earlier — boundary+summary are the whole tail. The pair IS the active
        # path: attached by shape, native emit, no adoption needed (and the dedup's armed
        # window is empty — the file ends there).
        side, _so = manual_compact_lines(T0 + 390, T0 + 400, "1", parent="a1")
        recs = ([uline(T0, "start the long build", "u1", ps="typed"),
                 aline(T0 + 30, "build started", "a1", "u1", stop="end_turn")]
                + side[:2])       # boundary + summary only
        out = run_recs(recs)
        comp = self._cards(out)
        self.assertEqual([c["uuid"] for c in comp], ["cb1"])
        self.assertEqual(comp[0]["parentUuid"], "a1",
                         "the leaf-anchored pair emits natively off its pre-compact anchor")


class Lineage(unittest.TestCase):
    def test_resume_keeps_pre_fork_history(self):
        out = run_scenario("resume_lineage")
        self.assertEqual(out["leafFsid"], FSID_B)
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertIn("u1", uuids, "resume links across files, pre-fork history kept")
        self.assertIn("u2", uuids)
        # provenance: each atom is tagged with the physical file it lives in
        fsid_of = {a["uuid"]: a.get("fsid") for t in out["turns"] for a in t["atoms"]}
        self.assertEqual(fsid_of["u1"], FSID_A)
        self.assertEqual(fsid_of["u2"], FSID_B)

    def test_clear_drops_pre_clear_history(self):
        out = run_scenario("clear_breaks_lineage")
        self.assertEqual(len(out["turns"]), 1, "only the post-clear turn survives")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        self.assertNotIn("u1", uuids)
        self.assertIn("u2", uuids)

    def test_rewind_branch_is_dropped(self):
        out = run_scenario("rewind_off_path")
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("first attempt", texts)
        self.assertIn("second attempt instead", texts)
        self.assertNotIn("abandoned follow-up", texts, "a rewound branch is intentionally dropped")


class BrokenChainFloor(unittest.TestCase):
    """This repo's one fatal error is silently dropping a real ask. A dangling parent
    chain (corruption / partial write) is not a proven rewind or a /clear, so it is KEPT
    — even though it is off the leaf->root spine. (0 such cases in the live corpus; this
    is a safety net.)"""

    def test_dangling_parent_prompt_is_kept(self):
        out = run_scenario("broken_chain_kept")
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("orphaned but real ask", texts, "a real ask must never be silently dropped")
        self.assertIn("main line ask", texts)
        self.assertIn("second main ask", texts)


class EclipsedBranch(unittest.TestCase):
    """T209 (the user 2026-09-01): a rendered five-minute turn vanished behind the
    "Recovered after retries" note the moment the user sent their next message — the CLI's
    buffered api_error flush had re-rooted the conversation spine through its error spur,
    abandoning the turn's kept output with no user gesture and no orphanReply marker to
    salvage it (the disk kept the text; only the spine left it). Both directions pinned:
    the machine-abandoned branch is KEPT, while a superseded retry and a genuine rollback
    (test_rewind_branch_is_dropped above) stay dropped."""

    def test_eclipsed_turn_output_is_kept(self):
        out = run_scenario("eclipsed_branch_kept")
        uuids = [a.get("uuid") for t in out["turns"] for a in t["atoms"]]
        for u in ("u1", "a1", "tr1", "a2", "u2", "a3"):
            self.assertIn(u, uuids, "eclipsed turn output must stay visible")
        texts = [_text(a) for t in out["turns"] for a in t["atoms"]]
        self.assertIn("Throughput rises linearly until the cache saturates.", texts,
                      "the only visible copy of the reply must never be eaten")

    def test_superseded_retry_stays_dropped(self):
        out = run_scenario("retry_superseded")
        texts = [_text(a) for t in out["turns"] for a in t["atoms"]]
        self.assertIn("The full answer after the retry.", texts)
        self.assertNotIn("Half an answer that a retry replaced.", texts,
                         "a retry that re-replied must not double")

    def test_chain_membership_reports_eclipsed_kept(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in scenario_eclipsed_branch_kept()) + "\n")
            mem = em.chain_membership(str(path))
        self.assertIn("eclipsed", mem)
        self.assertEqual(mem["eclipsed"], {"a1", "tr1", "a2"})
        self.assertTrue(mem["eclipsed"] <= mem["kept"], "eclipsed chains are kept")
        self.assertEqual(mem["rewind"], set(),
                         "an eclipse is not a rewind — nothing here may be swept")

class EclipsedChainSelection(unittest.TestCase):
    """WHICH uuids an eclipsed fork keeps (2026-09-01, two incidents verified at transcript
    level): the CLI recovers from an API-error storm internally, streams AND persists the
    reply — then flushes its buffered api_error records at the NEXT turn's start,
    parent-chained from the leaf as it stood at ERROR time. The flushed chain (api_error+
    then a stop_hook_summary, then the next user record) hijacks the leaf, so the persisted
    reply branch is bypassed on disk: pre-eclipse the parse called it a rewind and dropped it,
    while the ghost-reply gates rightly refused the orphanReply salvage (the text landed on
    SOME branch). The reply vanished from the chat, the judges, and the model's own next
    reload. The eclipsed fork probe (EclipsedBranch above) detects the machine bypass; this
    class pins the SELECTION layered on it (em._select_eclipsed_chains): the eclipse keeps
    exactly one chain — max-seq, assistant-headed, carrying reply text — so sibling stub
    pairs, error bursts and older attempts drop as their on-spine twins do, and a user-headed
    branch behind a COMPLETED flush stands the fork down (a rollback typed mid-storm forks
    exactly there, and re-showing deleted content is the one direction the eclipse must never
    take). All fixtures here are synthetic; the shapes mirror the two incident transcripts."""

    def _run(self, records, states=None):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            out = em.parse_session(str(path), rompuuid=SID, dir="/TESTDIR",
                                   candidate_files=[str(path)], states=states,
                                   postal_log=[], now=NOW)
            mem = em.chain_membership(str(path))
        return out, mem

    def _atoms(self, out):
        return [a for t in out["turns"] for a in t["atoms"]]

    def _episode(self, t, tag, fork_parent, reply_text, n_errors=2):
        """One flush-orphaned episode, the exact single-episode incident shape: the reminder
        attachment fork, the persisted thinking+reply branch (dead end), then the flushed
        api_error run, the stop hook, and the next turn's opener. Returns (records, uuids)."""
        rem, th, rp = "rem" + tag, "th" + tag, "rp" + tag
        recs = [reminder_line(t, rem, fork_parent),
                aline(t + 60, "", th, rem, stop=None, thinking="working through it"),
                aline(t + 61, reply_text, rp, th, stop="end_turn")]
        prev = rem
        for i in range(n_errors):
            recs.append(api_error_line(t + i, "e%d%s" % (i, tag), prev, attempt=i + 1))
            prev = "e%d%s" % (i, tag)
        recs.append(stop_hook_line(t + 70, "sh" + tag, prev))
        recs.append(uline(t + 100, "next ask %s" % tag, "u" + tag, "sh" + tag))
        return recs, (rem, th, rp)

    def base(self):
        return [uline(T0, "storm-turn ask", "u1", parent=None, ps="typed")]

    def test_flush_orphaned_reply_is_kept(self):
        ep, (rem, th, rp) = self._episode(T0 + 10, "A", "u1", "the reply the user watched stream")
        out, mem = self._run(self.base() + ep + [aline(T0 + 140, "reply on the next turn", "a2", "uA")])
        texts = [_text(a) for a in self._atoms(out) if a["type"] == "assistant"]
        self.assertIn("the reply the user watched stream", texts,
                      "the persisted reply the flush bypassed must render")
        self.assertIn(rp, mem["eclipsed"])
        self.assertIn(rp, mem["kept"])
        self.assertNotIn(rp, mem["rewind"], "a machine-orphaned reply is never a rewind")
        # the reply closes ITS OWN turn: same turn as the ask, ended, before the next opener
        turn0 = out["turns"][0]
        self.assertEqual([a.get("uuid") for a in turn0["atoms"]], ["u1", th, rp])
        self.assertTrue(turn0["ended"], "the eclipsed end_turn reply ends the stormed turn")
        self.assertEqual(out["turns"][1]["trigger"], {"uuid": "uA"})
        # the flushed bookkeeping never becomes atoms
        self.assertFalse({"e0A", "e1A", "shA", rem} & {a.get("uuid") for a in self._atoms(out)})

    def test_consecutive_storm_episodes_each_keep_their_reply(self):
        # case 1's shape: five back-to-back stormed turns; two suffice to pin the chaining —
        # each episode forks where the PREVIOUS episode's flushed spine left the leaf
        epA, (_, _, rpA) = self._episode(T0 + 10, "A", "u1", "first stormed reply")
        epB, (_, _, rpB) = self._episode(T0 + 200, "B", "uA", "second stormed reply", n_errors=1)
        out, mem = self._run(self.base() + epA + epB
                             + [aline(T0 + 400, "clean reply at last", "a9", "uB")])
        texts = [_text(a) for a in self._atoms(out) if a["type"] == "assistant"]
        self.assertIn("first stormed reply", texts)
        self.assertIn("second stormed reply", texts)
        self.assertTrue({rpA, rpB} <= mem["eclipsed"])
        self.assertEqual(mem["rewind"], set())

    def test_mid_turn_storm_with_tool_cycles_keeps_the_whole_turn(self):
        # the mid-turn variant (case 1 episodes C/D): the storm hits INSIDE the turn, the CLI
        # recovers and the turn runs on — tool cycles, a second reminder attachment — before the
        # final text; the flush then buffers SEVERAL bursts (retryAttempt resets between them)
        recs = self.base() + [
            reminder_line(T0 + 10, "remC", "u1"),
            aline(T0 + 20, "", "tuC1", "remC", tools=("Bash",), stop=None),
            trline(T0 + 25, "tu_tuC1_0", "trC1", "tuC1"),
            reminder_line(T0 + 26, "remC2", "trC1"),
            aline(T0 + 30, "", "tuC2", "remC2", tools=("Read",), stop=None),
            trline(T0 + 35, "tu_tuC2_0", "trC2", "tuC2"),
            aline(T0 + 40, "finished after the mid-turn storm", "rpC", "trC2", stop="end_turn"),
            # the flush: two bursts' records in one chain, counters restarting
            api_error_line(T0 + 11, "eC1", "remC", attempt=1),
            api_error_line(T0 + 12, "eC2", "eC1", attempt=2),
            api_error_line(T0 + 28, "eC3", "eC2", attempt=1),
            stop_hook_line(T0 + 41, "shC", "eC3"),
            uline(T0 + 100, "and the next thing", "u2", "shC"),
            aline(T0 + 130, "next thing handled", "a2", "u2"),
        ]
        out, mem = self._run(recs)
        uuids = [a.get("uuid") for a in self._atoms(out)]
        for u in ("tuC1", "trC1", "tuC2", "trC2", "rpC"):
            self.assertIn(u, uuids, "the whole bypassed turn stays kept, tools included (%s)" % u)
        self.assertTrue({"tuC1", "trC1", "remC2", "tuC2", "trC2", "rpC"} <= mem["eclipsed"])
        self.assertEqual(len(out["turns"]), 2, "one stormed turn, one clean turn")
        self.assertTrue(out["turns"][0]["ended"])

    def test_user_gesture_fork_stays_dropped_and_the_ghost_gate_holds(self):
        # the genuine-rollback contrast: the abandoned branch carries a REAL landed reply, but
        # the bypass at the fork is the user's replacement prompt — a gesture, no api_error
        # witness — so it stays dropped, and a stale orphanReply marker for that reply must not
        # resurrect it through the salvage door (the 2026-08-03 ghost-reply gate, fully intact)
        recs = [uline(T0, "first attempt", "u1", parent=None, ps="typed"),
                aline(T0 + 30, "did it one way", "a1", "u1"),
                uline(T0 + 100, "deleted follow-up", "u2", "a1", ps="typed"),
                aline(T0 + 130, "reply the rollback abandoned", "a2", "u2"),
                uline(T0 + 200, "second attempt instead", "u3", "a1", ps="typed"),
                aline(T0 + 230, "better approach done", "a3", "u3")]
        marker = [{"t": T0 + 130, "orphanReply": {"uuid": "a2", "text": "reply the rollback abandoned"}}]
        out, mem = self._run(recs, states=marker)
        atoms = self._atoms(out)
        self.assertNotIn("reply the rollback abandoned",
                         [_text(a) for a in atoms if a["type"] == "assistant"],
                         "a user-gesture rollback's branch stays dropped")
        self.assertFalse(any(a.get("orphaned") for a in atoms), "and its marker stays suppressed")
        self.assertEqual(mem["rewind"], {"u2", "a2"})
        self.assertEqual(mem["eclipsed"], set())

    def test_marker_stands_down_when_the_branch_is_kept(self):
        # a settle-time salvage marker may predate the fix for the same bypassed reply — the
        # eclipse renders the REAL atom, and the marker must not double it
        ep, (_, th, rp) = self._episode(T0 + 10, "A", "u1", "the reply the user watched stream")
        marker = [{"t": T0 + 71, "orphanReply": {"uuid": rp, "text": "the reply the user watched stream"}}]
        out, _ = self._run(self.base() + ep + [aline(T0 + 140, "onward", "a2", "uA")],
                           states=marker)
        atoms = self._atoms(out)
        hits = [a for a in atoms if _text(a) == "the reply the user watched stream"]
        self.assertEqual(len(hits), 1, "exactly one rendering of the reply — the real atom")
        self.assertEqual(hits[0].get("uuid"), rp)
        self.assertFalse(any(a.get("orphaned") for a in atoms))

    def test_parallel_tool_stub_inside_the_winning_branch_rides_the_keep(self):
        # the selection unit is the BRANCH, kept whole: a stub twin inside it stays (one
        # output-less duplicate tool row — deliberate; per-leaf-chain narrowing proved
        # reply-lossy), while stub CHAINS at the fork itself still drop (they are branches
        # of their own and never qualify)
        recs = self.base() + [
            reminder_line(T0 + 10, "remA", "u1"),
            aline(T0 + 20, "", "tuA1", "remA", tools=("Bash",), stop=None),
            # the stub pair: a second tool_use chained BESIDE the result, with its own result
            aline(T0 + 21, "", "stub1", "tuA1", tools=("Read",), stop=None),
            trline(T0 + 24, "tu_stub1_0", "stub2", "stub1"),
            # the real chain: result of tuA1 -> final text
            trline(T0 + 25, "tu_tuA1_0", "trA1", "tuA1"),
            aline(T0 + 30, "reply after the parallel calls", "rpA", "trA1", stop="end_turn"),
            api_error_line(T0 + 11, "eA1", "remA", attempt=1),
            stop_hook_line(T0 + 31, "shA", "eA1"),
            uline(T0 + 100, "next ask", "u2", "shA"),
            aline(T0 + 130, "done", "a2", "u2"),
        ]
        out, mem = self._run(recs)
        uuids = {a.get("uuid") for a in self._atoms(out)}
        self.assertIn("rpA", uuids)
        self.assertIn("trA1", uuids)
        # the stub twin rides the kept branch: the selection unit is the WHOLE branch (a
        # per-leaf-chain read of shared records proved reply-lossy by construction), and the
        # cost is one output-less duplicate tool row — never a lost reply
        self.assertTrue({"tuA1", "trA1", "rpA"} <= mem["eclipsed"])
        self.assertTrue({"stub1", "stub2"} <= mem["eclipsed"],
                        "over-keep is the deliberate trade: a dup tool row beats a possible reply loss")
        self.assertEqual(mem["rewind"], set())

    def test_stop_hook_only_bypass_stays_dropped(self):
        # the conservative scope: a bypass of stop_hook_summary records with NO api_error carries
        # no studied machine witness (15 such forks in the live corpus, forensics pending) — the
        # fork probe requires an api_error between the fork and the next conversational
        # record, so this shape keeps today's behavior
        recs = self.base() + [
            reminder_line(T0 + 10, "remA", "u1"),
            aline(T0 + 20, "reply behind a hook-only bypass", "rpA", "remA"),
            stop_hook_line(T0 + 30, "shA", "remA"),
            stop_hook_line(T0 + 31, "shB", "shA"),
            uline(T0 + 100, "next ask", "u2", "shB"),
            aline(T0 + 130, "done", "a2", "u2"),
        ]
        out, mem = self._run(recs)
        self.assertNotIn("reply behind a hook-only bypass",
                         [_text(a) for a in self._atoms(out) if a["type"] == "assistant"])
        self.assertIn("rpA", mem["rewind"])
        self.assertEqual(mem["eclipsed"], set())

    def test_a_user_headed_branch_never_survives_a_completed_flush(self):
        # the belt on the buckle: even AT an api_error bypass with the next prompt landed (a
        # COMPLETED flush — the "user" terminal), a branch whose head is a user record is not
        # a streamed reply (all 49 corpus matches are assistant-headed) and is exactly what a
        # rollback typed mid-storm leaves behind — the fork stands down whole rather than
        # re-show a prompt its user may have deleted
        recs = self.base() + [
            uline(T0 + 20, "prompt on a dead side branch", "ux", "u1", ps="typed"),
            aline(T0 + 30, "reply on that side branch", "ax", "ux"),
            api_error_line(T0 + 11, "eA1", "u1", attempt=1),
            stop_hook_line(T0 + 31, "shA", "eA1"),
            uline(T0 + 100, "next ask", "u2", "shA"),
            aline(T0 + 130, "done", "a2", "u2"),
        ]
        out, mem = self._run(recs)
        self.assertTrue({"ux", "ax"} <= mem["rewind"])
        self.assertEqual(mem["eclipsed"], set())

    def test_a_user_headed_branch_at_a_tail_spur_stays_kept(self):
        # the other eclipse terminal: the spur is the transcript's TAIL (a mid-flush race, a
        # session dead mid-storm — the "exhausted" terminal), so NO next prompt exists and no
        # user gesture can have abandoned anything — keep-on-unprovable holds and the component
        # stays eclipsed whole (the completed-flush belt above needs the landed next prompt to
        # bite)
        recs = self.base() + [
            uline(T0 + 20, "prompt on a dying branch", "ux", "u1", ps="typed"),
            aline(T0 + 30, "reply on that branch", "ax", "ux"),
            api_error_line(T0 + 40, "eA1", "u1", attempt=1),
        ]
        out, mem = self._run(recs)
        self.assertTrue({"ux", "ax"} <= mem["eclipsed"])
        self.assertEqual(mem["rewind"], set())
        self.assertIn("reply on that branch",
                      [_text(a) for a in self._atoms(out) if a["type"] == "assistant"])

    def test_a_pending_cut_outranks_the_eclipse(self):
        # a bare-rollback cut armed at the fork: the walk's leaf IS the fork, there is no bypass
        # segment to read, and nothing eclipses — the user's pending gesture wins
        ep, (_, _, rp) = self._episode(T0 + 10, "A", "u1", "the reply the user watched stream")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = self.base() + ep + [aline(T0 + 140, "onward", "a2", "uA")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            mem = em.chain_membership(str(path), leaf_override="u1")
        self.assertEqual(mem["eclipsed"], set())
        self.assertIn(rp, mem["rewind"])

    # ── sibling chains at ONE fork: selection reads properties, never the CLI's byte order of
    # writes (no on-disk transcript carries these shapes today — this is the defense against
    # write-order drift, since nothing contracts the order the CLI flushes its buffers in) ──

    def _fork_pieces(self):
        """The shared single-episode skeleton, split so tests can permute WRITE order without
        changing the graph: (head, reply, flush_spine, closer). The graph is always the same —
        fork at remA, reply chain rpA, flushed spine eA1->shA->u2 — only file order varies."""
        head = self.base() + [reminder_line(T0 + 10, "remA", "u1")]
        reply = [aline(T0 + 30, "the reply the user watched stream", "rpA", "remA")]
        flush_spine = [api_error_line(T0 + 11, "eA1", "remA", attempt=1),
                       stop_hook_line(T0 + 31, "shA", "eA1"),
                       uline(T0 + 100, "next ask", "u2", "shA")]
        closer = [aline(T0 + 130, "done", "a2", "u2")]  # the true leaf — always written last
        return head, reply, flush_spine, closer

    def test_late_written_stub_pair_never_steals_the_keep(self):
        # a parallel tool-stub pair parented directly AT the fork but written AFTER the reply
        # records: the stub chain is assistant-headed, so a max-seq-record selection would keep
        # the STUBS and leave the real reply dropped — inverting the fix's own contract purely
        # on write order. The stub chain carries no reply text; the reply chain does.
        head, reply, flush_spine, closer = self._fork_pieces()
        stubs = [aline(T0 + 20, "", "stubA", "remA", tools=("Bash",), stop=None),
                 trline(T0 + 25, "tu_stubA_0", "stubB", "stubA")]
        out, mem = self._run(head + reply + flush_spine + stubs + closer)
        self.assertIn("the reply the user watched stream",
                      [_text(a) for a in self._atoms(out) if a["type"] == "assistant"],
                      "the persisted reply stays kept whatever the stub pair's file position")
        self.assertIn("rpA", mem["eclipsed"])
        self.assertTrue({"stubA", "stubB"} <= mem["rewind"],
                        "the textless stub chain drops exactly as an early-written one does")

    def test_sibling_error_burst_chain_never_stands_the_fork_down(self):
        # a second api_error burst flushed as its OWN sibling chain from the same fork, written
        # last: its tail was the component's max-seq record, the head check saw a system record,
        # and the WHOLE fork stood down — the reply silently kept the pre-fix data-loss behavior.
        # An ineligible sibling chain must never veto the eligible one.
        head, reply, flush_spine, closer = self._fork_pieces()
        burst2 = [api_error_line(T0 + 40, "eB1", "remA", attempt=1),
                  api_error_line(T0 + 41, "eB2", "eB1", attempt=2)]
        out, mem = self._run(head + reply + flush_spine + burst2 + closer)
        self.assertIn("the reply the user watched stream",
                      [_text(a) for a in self._atoms(out) if a["type"] == "assistant"])
        self.assertIn("rpA", mem["eclipsed"])
        self.assertTrue({"eB1", "eB2"} <= mem["rewind"], "the burst chain itself is never kept")
        self.assertFalse({"eB1", "eB2"} & {a.get("uuid") for a in self._atoms(out)})

    def test_stub_pair_write_order_never_changes_the_verdict(self):
        # both chains present, both write orders — stubs before the reply records and after —
        # must yield the SAME membership: order-independence pinned at the membership level
        head, reply, flush_spine, closer = self._fork_pieces()
        stubs = [aline(T0 + 20, "", "stubA", "remA", tools=("Bash",), stop=None),
                 trline(T0 + 25, "tu_stubA_0", "stubB", "stubA")]
        _, early = self._run(head + stubs + reply + flush_spine + closer)
        _, late = self._run(head + reply + flush_spine + stubs + closer)
        for key in ("kept", "rewind", "eclipsed"):
            self.assertEqual(early[key], late[key], "write order changed the %r set" % key)
        self.assertIn("rpA", early["eclipsed"])
        self.assertTrue({"stubA", "stubB"} <= early["rewind"])

    def test_a_grafted_burst_below_the_reply_text_never_steals_the_keep(self):
        # shared-prefix laundering: gates and a ranking key that read leaf->fork CHAINS let a
        # junk tail grafted BELOW a text-carrying record inherit the prefix's qualifications —
        # a second flushed burst at the mid-turn graft point, written last, out-seq'd the real
        # reply chain and the actual reply demoted to rewind. The selection unit is the BRANCH
        # (ranked by its latest reply text), so a textless tail can never move the pick.
        head = self.base() + [reminder_line(T0 + 10, "remA", "u1")]
        reply = [aline(T0 + 20, "mid-turn text before the tool", "tuA1", "remA", tools=("Bash",), stop=None),
                 trline(T0 + 25, "tu_tuA1_0", "trA1", "tuA1"),
                 aline(T0 + 30, "the reply the user watched stream", "rpA", "trA1")]
        flush_spine = [api_error_line(T0 + 11, "eA1", "remA", attempt=1),
                       stop_hook_line(T0 + 31, "shA", "eA1"),
                       uline(T0 + 100, "next ask", "u2", "shA")]
        burst = [api_error_line(T0 + 40, "eB1", "tuA1", attempt=1),
                 api_error_line(T0 + 41, "eB2", "eB1", attempt=2)]
        closer = [aline(T0 + 130, "done", "a2", "u2")]
        for recs in (head + reply + flush_spine + burst + closer,
                     head + reply + burst + flush_spine + closer):
            out, mem = self._run(recs)
            texts = [_text(a) for a in self._atoms(out) if a["type"] == "assistant"]
            self.assertIn("the reply the user watched stream", texts,
                          "a grafted burst tail must never steal the keep from the reply")
            self.assertTrue({"tuA1", "trA1", "rpA"} <= mem["eclipsed"])
            # the grafted burst rides the kept branch (kept whole, see above) at zero display
            # cost: system records never become atoms, kept or not
            self.assertFalse({"eB1", "eB2"} & {a.get("uuid") for a in self._atoms(out)},
                             "burst records never surface as atoms")

    def test_a_late_stub_pair_below_the_reply_text_never_steals_the_keep(self):
        # the same laundering, stub flavor: a parallel stub pair parented one level BELOW a
        # text-carrying record must never steal the pick from the reply records beside it
        # (the at-the-fork variant is a branch of its own and still drops — covered above)
        head = self.base() + [reminder_line(T0 + 10, "remA", "u1")]
        reply = [aline(T0 + 20, "mid-turn text before the tool", "tuA1", "remA", tools=("Bash",), stop=None),
                 trline(T0 + 25, "tu_tuA1_0", "trA1", "tuA1"),
                 aline(T0 + 30, "the reply the user watched stream", "rpA", "trA1")]
        flush_spine = [api_error_line(T0 + 11, "eA1", "remA", attempt=1),
                       stop_hook_line(T0 + 31, "shA", "eA1"),
                       uline(T0 + 100, "next ask", "u2", "shA")]
        stubs = [aline(T0 + 21, "", "stubA", "tuA1", tools=("Read",), stop=None),
                 trline(T0 + 26, "tu_stubA_0", "stubB", "stubA")]
        closer = [aline(T0 + 130, "done", "a2", "u2")]
        out, mem = self._run(head + reply + flush_spine + stubs + closer)
        self.assertIn("the reply the user watched stream",
                      [_text(a) for a in self._atoms(out) if a["type"] == "assistant"],
                      "the reply survives whatever the stub pair's file position")
        self.assertTrue({"rpA", "trA1", "tuA1"} <= mem["eclipsed"])
        self.assertTrue({"stubA", "stubB"} <= mem["eclipsed"],
                        "the in-branch twin rides the keep — the deliberate over-keep trade")

    def test_a_textless_tool_only_turn_survives_a_completed_flush(self):
        # a machine-orphaned turn of PURE tool activity (no reply text) has no qualifying
        # chain, and the whole-component stand-down swept it — reverting the eclipse's keep
        # for work that really ran. A rollback branch's fork-side head is always the user's
        # own record (the head-gate's reasoning, and 42/42 user-gesture rollbacks in the
        # author's corpus), so the stand-down demotes user-headed chains ONLY; an
        # assistant-headed textless chain is provably not rollback residue and stays kept.
        recs = self.base() + [
            reminder_line(T0 + 10, "remA", "u1"),
            aline(T0 + 20, "", "tuA1", "remA", tools=("Bash",), stop=None),
            trline(T0 + 25, "tu_tuA1_0", "trA1", "tuA1"),
            api_error_line(T0 + 11, "eA1", "remA", attempt=1),
            stop_hook_line(T0 + 31, "shA", "eA1"),
            uline(T0 + 100, "next ask", "u2", "shA"),
            aline(T0 + 130, "done", "a2", "u2"),
        ]
        out, mem = self._run(recs)
        self.assertTrue({"tuA1", "trA1"} <= mem["eclipsed"],
                        "tool work that really ran survives the completed flush")
        self.assertTrue({"tuA1", "trA1"} <= mem["kept"])
        self.assertEqual(mem["rewind"], set())

    def test_a_failure_record_chain_never_outranks_the_reply(self):
        # Claude Code writes a FAILED attempt as an assistant record carrying the error as a
        # text block (isApiErrorMessage:true) — atoms() refuses to treat it as a reply, and the
        # eclipse's reply witness must too, or a late-written failed-attempt chain qualifies
        # and steals the keep from the turn's only real text.
        head, reply, flush_spine, closer = self._fork_pieces()
        failed = aline(T0 + 40, "API Error: 529 overloaded", "rpF", "remA", stop="stop_sequence")
        failed["isApiErrorMessage"] = True
        out, mem = self._run(head + reply + flush_spine + [failed] + closer)
        texts = [_text(a) for a in self._atoms(out) if a["type"] == "assistant"]
        self.assertIn("the reply the user watched stream", texts)
        self.assertNotIn("API Error: 529 overloaded", texts)
        self.assertIn("rpA", mem["eclipsed"])
        self.assertIn("rpF", mem["rewind"], "a failure echo is never the eclipse's reply")

    def test_twin_sub_branches_never_disenfranchise_their_own_turn(self):
        # the shape that broke per-leaf-chain selection (adversarial construction, 2026-09-01):
        # the REAL turn's text record has two textless sub-branches below it (the storm-cut
        # tool result + a parallel stub twin), and an OLDER superseded attempt sits beside the
        # branch at the fork. A unique-suffix read stripped the shared text record from both
        # of its own chains' suffixes, both failed the text gate, and the older sibling stole
        # the keep — reply loss. Branch-level selection keys on the branch's latest reply
        # text: the real turn (newer text) must win, whole.
        head = self.base() + [reminder_line(T0 + 10, "remA", "u1")]
        older = [aline(T0 + 15, "first persisted attempt", "rpQ", "remA")]
        turn = [aline(T0 + 20, "the reply the user watched stream", "tuT", "remA",
                      tools=("Bash", "Read"), stop=None),
                trline(T0 + 25, "tu_tuT_0", "trT", "tuT"),
                aline(T0 + 21, "", "stubX", "tuT", tools=("Read",), stop=None),
                trline(T0 + 26, "tu_stubX_0", "trX", "stubX")]
        flush_spine = [api_error_line(T0 + 11, "eA1", "remA", attempt=1),
                       stop_hook_line(T0 + 31, "shA", "eA1"),
                       uline(T0 + 100, "next ask", "u2", "shA")]
        closer = [aline(T0 + 130, "done", "a2", "u2")]
        out, mem = self._run(head + older + turn + flush_spine + closer)
        texts = [_text(a) for a in self._atoms(out) if a["type"] == "assistant"]
        self.assertIn("the reply the user watched stream", texts,
                      "the turn with the LATEST reply text wins, its twin sub-branches notwithstanding")
        self.assertNotIn("first persisted attempt", texts)
        self.assertTrue({"tuT", "trT"} <= mem["eclipsed"])
        self.assertIn("rpQ", mem["rewind"], "the superseded older attempt drops")

    def test_a_stranded_tool_result_at_the_fork_survives_the_stand_down(self):
        # the storm strands a tool_result beside its ON-SPINE tool_use (the CLI buffered the
        # errors while the tool ran, then flushed from the tool_use as the leaf): the branch
        # head is user-TYPED but machine-MADE. The stand-down demotes only user PROMPT heads —
        # a tool_result head is provably not rollback residue and stays kept, where its output
        # reunites with the spine's own tool call.
        recs = self.base() + [
            aline(T0 + 10, "", "tuF", "u1", tools=("Bash",), stop=None),
            trline(T0 + 20, "tu_tuF_0", "trF", "tuF"),
            api_error_line(T0 + 15, "eA1", "tuF", attempt=1),
            stop_hook_line(T0 + 25, "shA", "eA1"),
            uline(T0 + 100, "next ask", "u2", "shA"),
            aline(T0 + 130, "done", "a2", "u2"),
        ]
        out, mem = self._run(recs)
        self.assertIn("trF", mem["eclipsed"], "a machine-made tool_result head survives the stand-down")
        self.assertIn("trF", mem["kept"])
        self.assertEqual(mem["rewind"], set())

    def test_two_reply_chains_prefer_the_latest_written(self):
        # two assistant-headed, text-carrying sibling chains at one fork — NOT observed in the
        # corpus (every incident fork holds exactly one) — pinned deliberately: the latest-written
        # chain wins, as the CLI's final word on that turn, and it is the same chain the pre-fix
        # walk picked when only one existed. Only the selected chain stays eclipsed.
        head, _, flush_spine, closer = self._fork_pieces()
        replies = [aline(T0 + 20, "first persisted attempt", "rpA", "remA"),
                   aline(T0 + 30, "second persisted attempt", "rpB", "remA")]
        out, mem = self._run(head + replies + flush_spine + closer)
        self.assertIn("rpB", mem["eclipsed"])
        self.assertIn("rpA", mem["rewind"], "only the selected chain survives")
        texts = [_text(a) for a in self._atoms(out) if a["type"] == "assistant"]
        self.assertIn("second persisted attempt", texts)
        self.assertNotIn("first persisted attempt", texts)

    # ── the five-way membership on a fork holding every sibling kind at once: the oracle for the
    # narrowed selection. _select_eclipsed_chains builds its child map and its text witness over
    # the eclipse set alone; these literals were recorded from the whole-graph version before that
    # change and must not move. ──

    def _sibling_fork(self, reply=True, completed=True):
        """One fork (remA) with three sibling branches: the bypassed reply branch (assistant-headed,
        a tool cycle then text), a textless stub pair, and a user-headed branch carrying a reply.
        `reply=False` drops the reply branch (no branch qualifies); `completed=False` ends the
        spine at the api_error record (the "exhausted" terminal)."""
        recs = self.base() + [reminder_line(T0 + 10, "remA", "u1")]
        if reply:
            recs += [aline(T0 + 20, "", "tuA1", "remA", tools=("Bash",), stop=None),
                     trline(T0 + 25, "tu_tuA1_0", "trA1", "tuA1"),
                     aline(T0 + 30, "the reply the user watched stream", "rpA", "trA1")]
        recs += [aline(T0 + 21, "", "stubA", "remA", tools=("Read",), stop=None),
                 trline(T0 + 26, "tu_stubA_0", "stubB", "stubA"),
                 uline(T0 + 40, "prompt on a side branch", "ux", "remA", ps="typed"),
                 aline(T0 + 50, "reply on that side branch", "ax", "ux"),
                 api_error_line(T0 + 11, "eA1", "remA", attempt=1)]
        if completed:
            recs += [stop_hook_line(T0 + 31, "shA", "eA1"),
                     uline(T0 + 100, "next ask", "u2", "shA"),
                     aline(T0 + 130, "done", "a2", "u2")]
        return recs

    def _five_way(self, records):
        """chain_membership's dict beside the same five sets derived from a FRESH FileAdapter with
        no help from the exported predicate (PredicateParityGolden's oracle, all five sets)."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            mem = em.chain_membership(str(path))
            ad = em.FileAdapter([str(path)], path)
        active = ad.active_path()
        fresh = {"kept": ad.kept_uuids(active), "rewind": set(), "clear": set(), "broken": set(),
                 "eclipsed": set()}
        for u, v in ad.chain_verdicts(active).items():
            if v != "active":
                fresh[v].add(u)
        return mem, fresh

    def test_sibling_fork_five_way_verdicts_are_pinned_and_match_a_fresh_adapter(self):
        mem, fresh = self._five_way(self._sibling_fork())
        self.assertEqual(mem, fresh)
        self.assertEqual(mem, {
            "kept": {"u1", "remA", "eA1", "shA", "u2", "a2", "tuA1", "trA1", "rpA"},
            "eclipsed": {"tuA1", "trA1", "rpA"},
            "rewind": {"stubA", "stubB", "ux", "ax"},
            "clear": set(), "broken": set()})

    def test_sibling_fork_with_no_qualifying_branch_demotes_only_the_user_headed_one(self):
        mem, fresh = self._five_way(self._sibling_fork(reply=False))
        self.assertEqual(mem, fresh)
        self.assertEqual(mem, {
            "kept": {"u1", "remA", "eA1", "shA", "u2", "a2", "stubA", "stubB"},
            "eclipsed": {"stubA", "stubB"},
            "rewind": {"ux", "ax"},
            "clear": set(), "broken": set()})

    def test_sibling_fork_at_a_tail_spur_still_picks_the_reply(self):
        # the terminal decides only when NO branch qualifies; with the reply present the
        # selection is the same at an exhausted spur as behind a completed flush
        mem, fresh = self._five_way(self._sibling_fork(completed=False))
        self.assertEqual(mem, fresh)
        self.assertEqual(mem, {
            "kept": {"u1", "remA", "eA1", "tuA1", "trA1", "rpA"},
            "eclipsed": {"tuA1", "trA1", "rpA"},
            "rewind": {"stubA", "stubB", "ux", "ax"},
            "clear": set(), "broken": set()})

    def test_sibling_fork_at_a_tail_spur_with_no_qualifying_branch_keeps_every_sibling(self):
        mem, fresh = self._five_way(self._sibling_fork(reply=False, completed=False))
        self.assertEqual(mem, fresh)
        self.assertEqual(mem, {
            "kept": {"u1", "remA", "eA1", "stubA", "stubB", "ux", "ax"},
            "eclipsed": {"stubA", "stubB", "ux", "ax"},
            "rewind": set(), "clear": set(), "broken": set()})


class SlashCommandTurn(unittest.TestCase):
    def test_command_turn_is_tracked_and_flagged(self):
        # the user 2026-06-29: a slash command is no longer dropped — its invocation is a `command`-flagged
        # user atom that OPENS a tracked turn (so it shows in the chat/timeline + counts as working), with the
        # model work absorbed into that turn. The `command` flag is what makes the planner skip it (no goal).
        out = run_scenario("slash_command_turn")
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual(turn["trigger"], {"uuid": "cmd1"}, "the command invocation opens (triggers) the turn")
        uuids = [a.get("uuid") for a in turn["atoms"]]
        self.assertEqual(uuids, ["cmd1", "a1"], "the invocation is an atom; the work absorbs into its turn")
        cmd = turn["atoms"][0]
        self.assertEqual(cmd.get("command"), "/code-review", "the invocation atom carries the command flag (the name)")
        self.assertEqual(_text(cmd), "/code-review", "its display text is the slash command itself")
        self.assertTrue(turn["ended"], "the turn ends (the model work stopped end_turn) — not stuck working")

    def test_local_command_output_becomes_a_synthetic_assistant_reply(self):
        # a LOCAL command (e.g. /usage) writes <command-name> then <local-command-stdout> with the output and
        # NO model turn. The invocation → command user atom; the stdout → a synthetic assistant reply, so the
        # turn has content + ends naturally and the working signal lifts when the output lands.
        recs = [
            {"type": "user", "timestamp": iso(T0), "uuid": "c1", "parentUuid": None,
             "message": {"role": "user", "content": "<command-name>/usage</command-name>"}},
            {"type": "user", "timestamp": iso(T0 + 1), "uuid": "o1", "parentUuid": "c1",
             "message": {"role": "user", "content": "<local-command-stdout>You have 42 credits left.</local-command-stdout>"}},
        ]
        out = run_recs(recs)
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual([a.get("uuid") for a in turn["atoms"]], ["c1", "o1"])
        self.assertEqual(turn["atoms"][0].get("command"), "/usage")
        self.assertEqual(turn["atoms"][1]["type"], "assistant", "the stdout becomes the turn's reply")
        self.assertTrue(turn["atoms"][1].get("command"), "the output atom is flagged as command output")
        self.assertIn("42 credits", _text(turn["atoms"][1]))
        self.assertTrue(turn["ended"], "the turn ends once the output lands")

    def test_bare_command_with_no_output_still_ends(self):
        # the user 2026-06-29 (the JLD /usage case): a command that produced NO output (no stdout, no model
        # work) must NOT leave the turn open forever — that read the session as "working" for hours and left a
        # stuck card. The _finalize_turn backstop ends a bare command turn so the session settles to idle.
        recs = [{"type": "user", "timestamp": iso(T0), "uuid": "c1", "parentUuid": None,
                 "message": {"role": "user", "content": "<command-name>/usage</command-name>"}}]
        out = run_recs(recs)
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual([a.get("uuid") for a in turn["atoms"]], ["c1"])
        self.assertEqual(turn["atoms"][0].get("command"), "/usage")
        self.assertTrue(turn["ended"], "a bare command turn self-ends — never traps the session in 'working'")

    def test_a_skill_invocation_with_message_first_is_still_a_command_atom(self):
        # The CLI does NOT fix the wrapper ORDER: a built-in writes <command-name> first, but a SKILL / custom
        # command writes <command-message> first. Anchored-only matching missed the latter entirely — the
        # record fell through to the harness-noise skip, the invocation never became an atom, and the work it
        # triggered was absorbed into the PRECEDING segment. That is why a JLD session (`/jld <request>`) ran
        # with its ask buried in the previous "/model" command turn and no card of its own (the user
        # 2026-07-22).
        recs = [
            {"type": "user", "timestamp": iso(T0), "uuid": "c1", "parentUuid": None,
             "message": {"role": "user",
                         "content": "<command-message>jld</command-message>\n"
                                    "<command-name>/jld</command-name>\n"
                                    "<command-args>design a curriculum</command-args>"}},
            {"type": "assistant", "timestamp": iso(T0 + 1), "uuid": "a1", "parentUuid": "c1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}],
                         "stop_reason": "end_turn"}},
        ]
        out = run_recs(recs)
        self.assertEqual(len(out["turns"]), 1)
        turn = out["turns"][0]
        self.assertEqual(turn["trigger"], {"uuid": "c1"}, "the skill invocation OPENS its own turn")
        self.assertEqual(turn["atoms"][0].get("command"), "/jld", "recognized despite the message-first order")
        self.assertEqual(_text(turn["atoms"][0]), "/jld design a curriculum",
                         "its display text carries the args, so the ask is visible")

    def test_prose_quoting_the_command_tag_is_not_an_invocation(self):
        # the ordering fix searches for <command-name> ANYWHERE, so it is guarded by CMD_WRAP_RE: the record
        # must already BEGIN with a command wrapper. A real message that merely quotes the tag stays human.
        recs = [{"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
                 "message": {"role": "user",
                             "content": "the transcript shows <command-name>/usage</command-name> mid-line"}}]
        out = run_recs(recs)
        turn = out["turns"][0]
        self.assertIsNone(turn["atoms"][0].get("command"),
                          "prose quoting the tag is a real message, never an invocation")


def _text(atom):
    msg = atom.get("message") or {}
    return " ".join(b.get("text", "") for b in msg.get("content", [])
                    if isinstance(b, dict) and b.get("type") == "text").strip()


class ApiErrorAtom(unittest.TestCase):
    """Claude Code writes a failed turn as an assistant record with top-level isApiErrorMessage:true
    and a text block. em must TAG that atom isApiError so deep-link anchoring (_seg_anchors) can skip
    it — the error carries text but is a FAILURE, not a reply. (the user 2026-06-18.)"""

    def _atoms(self, records):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            out = em.parse_session(str(path), rompuuid=SID, dir="/TESTDIR",
                                   candidate_files=[str(path)], now=NOW)
        return [a for t in out["turns"] for a in t["atoms"]]

    def test_api_error_assistant_atom_is_tagged(self):
        err = aline(T0 + 20, "API Error: 500 server_error", "a1", "u1", stop="stop_sequence")
        err["isApiErrorMessage"] = True
        a1 = next(a for a in self._atoms([uline(T0, "do it", "u1"), err]) if a.get("uuid") == "a1")
        self.assertIs(a1.get("isApiError"), True, "API-error assistant atom must be tagged isApiError")

    def test_normal_assistant_atom_is_not_tagged(self):
        a1 = next(a for a in self._atoms([uline(T0, "do it", "u1"), aline(T0 + 20, "done", "a1", "u1")])
                  if a.get("uuid") == "a1")
        self.assertNotIn("isApiError", a1, "a real reply is never tagged isApiError")


class Idle(unittest.TestCase):
    def test_idle_atom_from_state_log(self):
        out = run_scenario("idle_atom")
        idles = [a for t in out["turns"] for a in t["atoms"] if a["type"] == "idle"]
        self.assertEqual(len(idles), 1)
        self.assertEqual((idles[0]["t"], idles[0]["end"]), (T0 + 60, T0 + 3600))

    def test_idle_folds_into_preceding_turn(self):
        out = run_scenario("idle_atom")
        self.assertTrue(any(a["type"] == "idle" for a in out["turns"][0]["atoms"]))
        self.assertFalse(any(a["type"] == "idle" for a in out["turns"][1]["atoms"]))

    def test_no_idle_atom_without_a_state_transition(self):
        # same one-hour assistant gap, but NO idle state row -> NO idle atom (not a heuristic)
        out = run_single_no_states("idle_atom")
        idles = [a for t in out["turns"] for a in t["atoms"] if a["type"] == "idle"]
        self.assertEqual(idles, [])


def run_single_no_states(name):
    records, sent = SINGLE_FILE[name]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / (SID + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        return em.parse_session(str(path), rompuuid=SID, dir="/TESTDIR", candidate_files=[str(path)],
                                states=None, postal_log=sent or [], now=NOW)


class PopAll(unittest.TestCase):
    def test_popall_produces_one_absorbed_atom_per_queued_item(self):
        out = run_scenario("popall")
        absorbed = [a.get("uuid") for a in out["turns"][0]["atoms"]
                    if a.get("uuid") in ("att1", "att2")]
        self.assertEqual(absorbed, ["att1", "att2"])


class AbsorbedLandingNeverPrecedesTheSend(unittest.TestCase):
    """An absorbed atom's `t` is its LANDING — when the CLI took the mid-turn send, read off the
    attachment's file-order predecessor — and `sentAt` is the send (T252d, 2026-09-08: placed where the
    model read it, below the steps that ran while it waited). The predecessor's stamp can precede the
    attachment's own (the ENQUEUE time) only by clock granularity: the live corpus's worst case is
    -0.2 s, which whole-second stamps can turn into a 1 s inversion. No truthful landing precedes the
    send, so the event model clamps the placement to the send and counts the clamp in parse stats
    (`landedT-clamp`): the atom never sits before its own send. Pinned across every golden, because the
    two goldens that carry absorbed atoms once pinned the inverted value (2026-09-06 review) from a
    scenario shape with no tool_result before the attachment. The ORDER is pinned too: the atom sorts
    after the record the CLI wrote before taking it and before the assistant record that answers it."""

    def _absorbed(self, out):
        return [a for t in out["turns"] for a in t["atoms"] if a.get("absorbed")]

    def test_every_golden_absorbed_atom_lands_at_or_after_its_send(self):
        seen = 0
        for name in ALL_SCENARIOS:
            for a in self._absorbed(run_scenario(name)):
                seen += 1
                self.assertGreaterEqual(a["t"], a["sentAt"], (name, a["uuid"]))
        self.assertGreaterEqual(seen, 3, "multi_input_absorbed and popall carry the absorbed atoms this pins")

    def test_the_realistic_shape_lands_at_the_boundary_after_the_send(self):
        out = run_scenario("multi_input_absorbed")
        a = self._absorbed(out)
        self.assertEqual([(x["sentAt"], x["t"]) for x in a], [(T0 + 40, T0 + 55)],
                         "sent at the enqueue, placed at the tool_result that followed it in file order")
        order = [x["uuid"] for t in out["turns"] for x in t["atoms"]]
        self.assertLess(order.index("tr1"), order.index("att1"), "after the record the CLI wrote before taking it")
        self.assertLess(order.index("att1"), order.index("a2"), "before the assistant record that answers it")
        out = run_scenario("popall")
        a = self._absorbed(out)
        self.assertEqual([(x["uuid"], x["sentAt"], x["t"]) for x in a], [("att1", T0 + 30, T0 + 50), ("att2", T0 + 40, T0 + 50)],
                         "two splices at one boundary both sit at that boundary, in send order")

    def test_an_inverted_witness_clamps_to_the_send_and_is_counted(self):
        # the tool_result stamped before the attachment's enqueue stamp: clock granularity at worst, an
        # impossible shape beyond it — either way the landing reads as the send, never earlier
        recs = [uline(T0, "refactor the ledger", "u1", ps="typed"),
                aline(T0 + 20, "Reading.", "a1", "u1", tools=("Read",), stop="tool_use"),
                trline(T0 + 39, "tu_a1_0", "tr1", "a1"),
                attline(T0 + 40, "also rename the digest file", "att1", "tr1"),
                aline(T0 + 90, "Done.", "a2", "att1", stop="end_turn")]
        before = em._ASM_STATS.get("landedT-clamp", 0)
        a = self._absorbed(run_recs(recs))
        self.assertEqual([(x["sentAt"], x["t"]) for x in a], [(T0 + 40, T0 + 40)])
        self.assertEqual(em._ASM_STATS.get("landedT-clamp", 0), before + 1,
                         "counted in parse stats, beside ts-repair — a run of these is a CLI write-order change")

    def test_a_witness_at_the_send_second_is_not_a_clamp(self):
        recs = [uline(T0, "refactor the ledger", "u1", ps="typed"),
                aline(T0 + 20, "Reading.", "a1", "u1", tools=("Read",), stop="tool_use"),
                trline(T0 + 40, "tu_a1_0", "tr1", "a1"),
                attline(T0 + 40, "also rename the digest file", "att1", "tr1"),
                aline(T0 + 90, "Done.", "a2", "att1", stop="end_turn")]
        before = em._ASM_STATS.get("landedT-clamp", 0)
        a = self._absorbed(run_recs(recs))
        self.assertEqual([(x["sentAt"], x["t"]) for x in a], [(T0 + 40, T0 + 40)])
        self.assertEqual(em._ASM_STATS.get("landedT-clamp", 0), before, "equal stamps are a landing, not an inversion")


class SafeDefault(unittest.TestCase):
    """parse_session must NOT glob the project dir by default (a footgun: it would read
    every unrelated transcript in the dir). The default candidate set is just [leaf];
    cross-file resume requires the caller to pass the explicit session file set."""

    def _two_files(self, td):
        # `other` is a resume PARENT of `leaf` (leaf's first prompt parents into other's x2)
        other = Path(td) / "cccccccc-0000-0000-0000-000000000000.jsonl"
        other.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, "sibling parent ask", "x1", ps="typed"),
            aline(T0 + 20, "sibling reply", "x2", "x1", stop="end_turn")]) + "\n")
        leaf = Path(td) / (SID + ".jsonl")
        leaf.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 + 100, "leaf ask resuming sibling", "u1", parent="x2", ps="typed"),
            aline(T0 + 120, "leaf reply", "a1", "u1", stop="end_turn")]) + "\n")
        return leaf, other

    def test_default_does_not_read_sibling_files(self):
        with tempfile.TemporaryDirectory() as td:
            leaf, other = self._two_files(td)
            out = em.parse_session(str(leaf), rompuuid=SID, dir="/TESTDIR", now=NOW)  # NO candidate_files
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("leaf ask resuming sibling", texts)
        self.assertNotIn("sibling parent ask", texts, "default must not glob/read sibling transcripts")

    def test_explicit_file_set_enables_cross_file_resume(self):
        with tempfile.TemporaryDirectory() as td:
            leaf, other = self._two_files(td)
            out = em.parse_session(str(leaf), rompuuid=SID, dir="/TESTDIR",
                                   candidate_files=[str(leaf), str(other)], now=NOW)
        texts = [_text(a) for t in out["turns"] for a in t["atoms"] if a["type"] == "user"]
        self.assertIn("sibling parent ask", texts, "explicit candidate_files enables cross-file resume")


class WaitingStopClosesTheTurn(unittest.TestCase):
    """The SDK backend writes state:"waiting" when the agent hands the floor back (the removed tmux backend's
    Stop hook did the same); it must terminate the
    turn the SAME as the later idle-prompt's state:"idle". Keying only on "idle" left a finished session
    whose last assistant message wasn't a clean end_turn (e.g. it ended on a tool_use) stuck reading
    "working" from Stop until the idle-prompt eventually landed (the user 2026-06-25, who asked to revert working)."""
    ATOMS = [{"t": 100, "session_id": "s"}, {"t": 200, "end": 200, "session_id": "s"}]

    def test_a_waiting_state_synthesizes_an_idle_atom_like_idle(self):
        out = em.synthesize_idle([{"t": 210, "state": "waiting"}], self.ATOMS, now=300)
        self.assertEqual([(a["type"], a["t"], a["end"]) for a in out], [("idle", 210, 300)])
        # ...exactly as a real idle-prompt "idle" record does
        self.assertEqual(em.synthesize_idle([{"t": 210, "state": "idle"}], self.ATOMS, now=300)[0]["type"], "idle")

    def test_working_never_synthesizes_an_idle_atom(self):
        self.assertEqual(em.synthesize_idle([{"t": 210, "state": "working"}], self.ATOMS, now=300), [])
        self.assertEqual(em._IDLE_STATES, ("idle", "waiting"))


def regen():
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in ALL_SCENARIOS:
        out = run_scenario(name)
        p = GOLDEN / (name + ".json")
        p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        print("wrote %s  (%d turns)" % (p, len(out["turns"])))


if __name__ == "__main__":
    if "--regen" in sys.argv:
        regen()
    else:
        unittest.main(verbosity=2)
