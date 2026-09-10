#!/usr/bin/env python3
"""The bars frame's wire shape (T278b): a bar carries the FIRST line of its prompt, capped, and none of the three
fields that duplicated another (tid = the lane key, uuid = promptId, workUuid = workId).

Why: on the devbox the {type:"bars"} frame reached 20.4 MB (8,562 bars in 25 lanes) and took 22 s to cross the
laptop tunnel on every connect. The prompt text was 8.2 MB of it, though the pane's only reader (reqText, the
prompt-dot tip) shows at most 90 characters of the first non-empty line and the full text is a click away in the
chat; the three duplicate uuid-shaped fields were another 1.2 MB. The encoder lives in the kernel (_wire_prompt and
the bar literal); the view derives the dropped fields. tests/fixtures/timeline-bars-wire.json pins the encoding of
a synthetic payload, and ui/timeline-bars-wire.test.ts feeds the same fixture to the view's helpers and checks the
rendered tip and anchors match what the old shape rendered: the round trip, each half in its own suite.

Synthetic only: invented prompts, placeholder ids."""
import json
import os
import re
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic BEFORE the load: never live state
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("kernel_bars_wire", os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"))

FIXTURE = os.path.join(HERE, "fixtures", "timeline-bars-wire.json")
SID = "11111111-2222-3333-4444-555555555555"
DROPPED = ("tid", "uuid", "workUuid", "pending")
SHORT = {name: short for short, (name, _d) in km._BAR_WIRE.items()}      # long name -> wire key


def wire_bar(old, sid=SID):
    """The wire shape of an old-shape bar (T278c): id, start and end by name, the rest under the short keys of
    _BAR_WIRE with every default omitted, the prompt shaped by _wire_prompt. The test-side twin of the builder's
    literal; _expand_bar is its inverse and the view's expandBars the JavaScript one."""
    b = {"id": old["id"], "start": old["start"], "end": old["end"]}
    for name, (short) in SHORT.items():
        default = km._BAR_WIRE[short][1]
        v = km._wire_prompt(old.get("prompt")) if name == "prompt" else old.get(name)
        if v is None or v == default or (isinstance(default, list) and v == []):
            continue
        b[short] = v
    return b


def long_bar(old):
    """What every reader sees after the view expands a wire bar built from `old`."""
    out = {k: v for k, v in old.items() if k not in DROPPED}
    out["prompt"] = km._wire_prompt(old.get("prompt"))
    out["pending"] = False
    for name, short in SHORT.items():
        default = km._BAR_WIRE[short][1]
        if out.get(name) is None and default is not None:
            out[name] = list(default) if isinstance(default, list) else default
    return out


class WirePrompt(unittest.TestCase):
    def test_the_first_non_empty_line_rides_and_the_rest_does_not(self):
        self.assertEqual(km._wire_prompt("\n\n  Fix the parser  \nsecond line\nthird"), "Fix the parser")

    def test_romp_marks_and_a_leading_label_are_removed_before_the_line_is_picked(self):
        # an injected notice: the marks (or the label alone) would otherwise be the first line, or eat the cap;
        # the view removed both from the WHOLE text before picking its line, so the kernel does the same
        self.assertEqual(km._wire_prompt("<!-- romp-injected --><!-- romp-system -->[romp] The kernel restarted\nmore"),
                         "The kernel restarted")
        self.assertEqual(km._wire_prompt("<!-- romp-note: a\nmulti-line mark -->\nReal ask"), "Real ask")
        self.assertEqual(km._wire_prompt("[romp]\nPlease restart the build"), "Please restart the build",
                         "a label alone on line one must not become the line the tip strips to nothing")
        self.assertEqual(km._wire_prompt("  [ROMP]  \n\nAsk"), "Ask")
        self.assertEqual(km._wire_prompt("Ask mentions [romp] mid-line"), "Ask mentions [romp] mid-line", "only a LEADING label")

    def test_a_long_first_line_is_cut_with_an_ellipsis(self):
        long = " ".join("word%d" % i for i in range(200))      # varied words: a long ask, not a repeat storm
        out = km._wire_prompt(long)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), km._WIRE_PROMPT_MAX + 1)
        self.assertEqual(out[:-1], long[:km._WIRE_PROMPT_MAX].rstrip())
        self.assertEqual(km._wire_prompt("x" * km._WIRE_PROMPT_MAX), "x" * km._WIRE_PROMPT_MAX, "exactly the cap: no marker")

    def test_a_repeat_storm_is_collapsed_over_the_whole_text_before_the_cut(self):
        self.assertEqual(km._wire_prompt(" ".join(["retry"] * 120)), "retry ×120", "the count is the storm's size, never truncated")
        self.assertEqual(km._wire_prompt("retry Retry RETRY"), "retry ×3")
        self.assertEqual(km._wire_prompt("retry now"), "retry now", "any variety is left alone")

    def test_empty_and_none_are_empty(self):
        self.assertEqual(km._wire_prompt(""), "")
        self.assertEqual(km._wire_prompt(None), "")
        self.assertEqual(km._wire_prompt("<!-- romp-only -->"), "")


class BarLiteral(unittest.TestCase):
    def setUp(self):
        self.src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        head, tail = 'bar = {"id": seg["id"], "start": bstart, "end": bend}', "bars.append(bar)"
        self.assertIn(head, self.src, "the bar literal's first line moved or was reflowed: re-anchor this pin")
        i = self.src.index(head)
        self.assertIn(tail, self.src[i:], "the bar literal's end moved: re-anchor this pin")
        self.literal = self.src[i:self.src.index(tail, i) + len(tail)]

    def test_the_bar_is_built_compact_with_every_default_omitted(self):
        self.assertIn('q = _wire_prompt(full_prompt)', self.literal)
        for short in km._BAR_WIRE:
            self.assertIn('bar["%s"] = ' % short, self.literal, "the %s field rides under its short key" % km._BAR_WIRE[short][0])
        for name in ("promptId", "workId", "replyUuid", "prompt", "summary", "msgCaption", "src", "mids",
                     "pending", "nudgeAuto", "romp", "cont", "open", "tid", "uuid", "workUuid"):
            self.assertNotIn('"%s": ' % name, self.literal, name + " is a long name: the wire carries the short key, the view expands it")
        self.assertIn('full_prompts[seg["id"]] = full_prompt = _seg_prompt(seg)', self.src, "the full text is kept beside the wire")
        self.assertIn("_bind_message_execs(messages, turns, full_prompts)", self.src, "...and handed to the message binder")

    def test_the_view_expands_with_the_same_table(self):
        # the view's BAR_WIRE literal is the kernel's _BAR_WIRE: short key -> long name and default, byte for byte
        view = open(os.path.join(os.path.dirname(HERE), "ui", "romp-timeline-view.js")).read()
        m = re.search(r"const BAR_WIRE = \{(.*?)\};", view, re.S)
        self.assertTrue(m, "the view carries BAR_WIRE")
        pairs = {short: (name, js) for short, name, js in re.findall(r"(\w+): \['(\w+)', (\[\]|null|''|'typed'|false)\]", m.group(1))}
        self.assertEqual(set(pairs), set(km._BAR_WIRE))
        for short, (name, default) in km._BAR_WIRE.items():
            self.assertEqual(pairs[short][0], name, short)
            want = "[]" if default == [] else {None: "null", "": "''", "typed": "'typed'", False: "false"}[default]
            self.assertEqual(pairs[short][1], want, "%s default" % name)
        # …and the judging table, the same way
        mj = re.search(r"const JUDGING_WIRE = \{(.*?)\};", view, re.S)
        self.assertTrue(mj, "the view carries JUDGING_WIRE")
        jp = {short: (name, js) for short, name, js in re.findall(r"(\w+): \['(\w+)', (null|'run'|''|0|false)\]", mj.group(1))}
        self.assertEqual(set(jp), set(km._JUDGING_WIRE))
        for short, (name, default) in km._JUDGING_WIRE.items():
            self.assertEqual(jp[short][0], name, short)
            want = "false" if default is False else "0" if default == 0 else "null" if default is None else "'run'" if default == "run" else "''"
            self.assertEqual(jp[short][1], want, "%s default" % name)


class ExpandIsTheInverse(unittest.TestCase):
    def test_expand_bar_undoes_wire_bar(self):
        fx = json.load(open(FIXTURE))
        for old in fx["old"]:
            self.assertEqual(km._expand_bar(wire_bar(old)), long_bar(old), old["id"])

    def test_compact_judging_round_trips(self):
        fx = json.load(open(FIXTURE))
        wire = km._compact_judging(fx["judging_old"])
        self.assertEqual(wire, fx["judging_wire"], "the fixture is the compactor's output")
        back = km._expand_judging(wire)
        self.assertEqual(back, fx["judging_long"], "the expansion is what every reader saw, text capped, defaults filled")
        for lane in wire.values():
            for c in lane:
                self.assertIsInstance(c["k"], str, "the delta key is a string the shim reads as is")
                for gone in ("sid", "judge", "kind", "text", "sent", "recv", "open"):
                    self.assertNotIn(gone, c)
        self.assertEqual(km._expand_judging([]), [])
        self.assertEqual(km._expand_judging({}), [])
        legacy = [{"judge": "closer", "sid": SID, "t": 1}]
        self.assertEqual(km._expand_judging(legacy), legacy, "a legacy list passes through")


def _old_bars(n_lanes=25, per_lane=320):
    """A synthetic payload of the devbox's size class in the shape this change STARTS from (after T278b): 25 lanes,
    8,000 bars, every long key present with its default, the prompt already the first line, no duplicates."""
    turns = {}
    for li in range(n_lanes):
        sid = "%08d-2222-3333-4444-555555555555" % li
        bars = []
        for bi in range(per_lane):
            seg = "%08d-%04d-4000-8000-000000000000" % (li, bi)
            work = "%08d-%04d-4000-8000-000000000001" % (li, bi)
            reply = "%08d-%04d-4000-8000-000000000002" % (li, bi)
            bars.append({"id": seg, "promptId": seg, "workId": work, "start": 1000 + 10 * bi, "end": 1005 + 10 * bi,
                         "open": False, "cont": False, "prompt": "Please look into item %d of lane %d and tell me what changed." % (bi, li),
                         "summary": "invented work caption %d" % bi, "msgCaption": "gist %d" % bi, "src": "typed", "mids": [],
                         "pending": False, "nudgeAuto": False, "romp": False, "replyUuid": reply})
        turns[sid] = bars
    return turns


def _old_judging(n_lanes=25, per_lane=560):
    """14,000 entries of the builder's flat shape: run spans with tokens, glossed by a caption, some in flight."""
    out = []
    for li in range(n_lanes):
        sid = "%08d-2222-3333-4444-555555555555" % li
        for i in range(per_lane):
            t = 1700000000.0 + 7 * i + li * 0.25
            out.append({"judge": ("captioner", "closer", "planner", "archiver")[i % 4], "sid": sid, "t": t, "t1": t + 3.5,
                        "kind": ("run", "turn", "segment", "close")[i % 4], "text": ("invented gloss %d" % i) if i % 3 else "",
                        "ms": 4000 + i, "in": 12000 + i, "out": 1600 + i, "sent": t, "recv": t + 3.5})
    return out


class FrameSize(unittest.TestCase):
    def test_the_wire_frame_is_well_under_two_thirds_of_the_old_on_a_devbox_sized_payload(self):
        # the old frame is the one this change starts from (after T278b): long-named bars with every default present,
        # a flat judging list repeating the lane sid, and the delta key list a keyed full carried (one key per bar and
        # per judging entry); the new: compact bars, per-lane compact judging, no key list. The live devbox frame
        # (8,577 bars, 14,051 entries) was 12.18 MB before; the after figure is measured on the deploy and reported
        old_t, old_j = _old_bars(), _old_judging()
        keys = {"turns": [sid + "\x1f" + b["id"] for sid, bars in old_t.items() for b in bars],
                "judging": ["\x1f".join(str(e[f]) for f in ("sid", "t", "judge", "t1")) for e in old_j]}
        old = {"type": "bars", "turns": old_t, "judging": old_j, "messages": [], "now": 1, "_keys": keys}
        new = {"type": "bars", "turns": {sid: [wire_bar(b, sid) for b in bars] for sid, bars in old_t.items()},
               "judging": km._compact_judging(old_j), "messages": [], "now": 1}
        n_old, n_new = len(json.dumps(old)), len(json.dumps(new))
        self.assertEqual(sum(len(v) for v in new["turns"].values()), 8000)
        self.assertEqual(sum(len(v) for v in new["judging"].values()), 14000)
        self.assertLess(n_new / n_old, 0.65, "old %d B, new %d B: ratio %.2f" % (n_old, n_new, n_new / n_old))
        self.assertGreater(n_new / n_old, 0.5, "the fixture is not a strawman: the ids, times and captions still ride")


class Fixture(unittest.TestCase):
    """The committed fixture is the round trip's shared half: this pins encode(old) == wire; the node test pins
    that the view renders `wire` as it rendered `old`."""

    def test_the_fixture_is_the_encoders_output(self):
        fx = json.load(open(FIXTURE))
        self.assertEqual(fx["sid"], SID)
        self.assertEqual(len(fx["old"]), len(fx["wire"]))
        for old, wire in zip(fx["old"], fx["wire"]):
            self.assertEqual(wire_bar(old, fx["sid"]), wire, old["id"])
        self.assertTrue(any(len(o["prompt"]) > km._WIRE_PROMPT_MAX for o in fx["old"]), "the fixture covers a cut")
        self.assertTrue(any(o["prompt"].startswith("<!-- romp-") for o in fx["old"]), "…and an injected notice")
        self.assertTrue(any(o["prompt"].startswith("[romp]\n") for o in fx["old"]), "…and a label alone on line one")
        self.assertEqual(fx["judging_wire"], km._compact_judging(fx["judging_old"]))
