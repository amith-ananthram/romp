"""_seg_mids and _fold_tasks read a tool_result's content in place instead of json.dumps-ing every block.

Both functions searched tool_result blocks for text — _seg_mids for romp-msg-id markers on every timeline
build (once per segment), _fold_tasks for the 'Task #N' in a TaskCreate result on every chat build — and
both encoded every list-shaped result to do it. A list-shaped result is mostly image blocks (base64) and
tool_reference blocks that can never carry either string, and the encoding was 2.6% of the pusher
(push-thread cProfile, 2026-09-06). _seg_mids now walks the strings the encoding would write and encodes only a
string that carries the marker literal; _fold_tasks stores the raw content and encodes only the TaskCreate
result it reads. The match semantics are the encoding's, exactly: these tests pin that with the pre-change
_seg_mids kept below as the oracle, over every content shape the SDK passes through (str, list of block
dicts, None) and the shapes it does not (a dict, an int, an object).

Synthetic content throughout: invented text, a placeholder message id shaped like the postal service's
(ASCII letters, digits, dots and underscores), hostname TESTHOST.
"""
import base64
import json
import os
import tempfile
import unittest
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load — the kernel resolves its state root at import time, and only pytest runs
# conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
POSTAL_RE = km.jd.em.POSTAL_RE

MID = "1700000000.1_12345.TESTHOST"          # the emitters' id shape; never a real message id
MID2 = "1700000000.2_12345.TESTHOST"
IMAGE_DATA = base64.b64encode(bytes(range(256)) * 160).decode()   # a 40 KB image (about 55 KB of base64), no marker


def marker(mid):
    return "<!-- romp-msg-id: %s -->" % mid


def _seg_mids_encoding(seg):
    """The pre-change _seg_mids, verbatim: the oracle for what the encoding-based search returned."""
    ids = []
    for a in seg.get("atoms", []):
        msg = a.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            ids += POSTAL_RE.findall(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    ids += POSTAL_RE.findall(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    c = b.get("content")
                    ids += POSTAL_RE.findall(c if isinstance(c, str) else json.dumps(c))
    return ids


def result_seg(*contents):
    """A segment of user atoms, one tool_result block per content value."""
    return {"atoms": [{"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t%d" % i, "content": c}]}} for i, c in enumerate(contents)]}


def text_block(t):
    return {"type": "text", "text": t}


IMAGE_BLOCK = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMAGE_DATA}}
REFERENCE_BLOCKS = [{"type": "tool_reference", "tool_name": "Read", "id": "ref%d" % i} for i in range(3)]


class SegMidsShapes(unittest.TestCase):
    """Each shape the SDK's ToolResultBlock.content can take (str | list[dict] | None), plus the ones it
    cannot, gives the ids the encoding gave, as a list (order and duplicates included)."""

    def assertSameAsEncoding(self, seg, expected):
        self.assertEqual(km._seg_mids(seg), expected)
        self.assertEqual(_seg_mids_encoding(seg), expected, "the oracle agrees with the expectation")

    def test_plain_string_result(self):
        # how check_inbox results are stored: the marker in a string, no encoding either way
        self.assertSameAsEncoding(result_seg("inbox:\n" + marker(MID) + "\nbody"), [MID])

    def test_list_of_text_blocks_beside_image_and_tool_reference_blocks(self):
        seg = result_seg([text_block("before " + marker(MID)), IMAGE_BLOCK, *REFERENCE_BLOCKS,
                          text_block("after " + marker(MID2))])
        self.assertSameAsEncoding(seg, [MID, MID2])

    def test_bare_string_items_in_a_list(self):
        self.assertSameAsEncoding(result_seg([marker(MID), "no marker here", marker(MID2)]), [MID, MID2])

    def test_nested_dicts_are_walked(self):
        # a marker in a nested field was found by the encoding, so it is found by the walk too — and
        # in a dict KEY, which the encoding also wrote out
        seg = result_seg([{"type": "other", "meta": {"note": marker(MID), "list": [{"deep": marker(MID2)}]}}],
                         {marker("1700000000.3_1.TESTHOST"): 1})
        self.assertSameAsEncoding(seg, [MID, MID2, "1700000000.3_1.TESTHOST"])

    def test_none_content_yields_nothing(self):
        # sdk_backend passes ToolResultBlock.content through unchanged, so a live-tail atom can carry None
        self.assertSameAsEncoding(result_seg(None), [])

    def test_unexpected_types_do_not_raise(self):
        for c in (7, 2.5, True, b"bytes", object(), {"k": object()}, [object(), 3, None]):
            with self.subTest(content=type(c).__name__):
                self.assertEqual(km._seg_mids(result_seg(c)), [])

    def test_text_block_with_null_text_is_skipped(self):
        # a text block whose `text` is null raised a TypeError in findall before
        seg = {"atoms": [{"type": "assistant", "message": {"content": [{"type": "text", "text": None},
                                                                        text_block(marker(MID))]}}]}
        self.assertEqual(km._seg_mids(seg), [MID])

    def test_order_and_duplicates_are_kept(self):
        seg = {"atoms": [
            {"type": "user", "message": {"content": [text_block("a " + marker(MID))]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                     "content": [text_block(marker(MID2)), text_block(marker(MID))]}]}},
            {"type": "user", "message": {"content": "plain " + marker(MID2)}}]}
        self.assertSameAsEncoding(seg, [MID, MID2, MID, MID2])

    def test_agrees_with_the_encoding_on_every_marker_shape(self):
        # The exactness claim, including the shapes no emitter writes (zero instances in observed
        # transcripts): a newline before the close, a tab after the open, a quote or a non-ASCII character
        # inside the id. The encoding turns those into escape sequences before matching, so its answer
        # differs from a raw match; the walk returns the encoding's answer, whatever it is, so nothing a
        # timeline connector binds to can move.
        shapes = [
            [text_block(marker(MID))],
            [text_block("<!-- romp-msg-id: abc\n-->")],
            [text_block(marker("abc").replace("<!-- ", "<!--\t"))],   # a tab after the open, not a space
            [text_block('<!-- romp-msg-id: a"b -->')],
            [text_block("<!-- romp-msg-id: a\\b -->")],
            [text_block("<!-- romp-msg-id: café -->")],
            [text_block("<!-- romp-msg-id: \n -->")],
            [text_block("<!-- romp-msg-id: abc"), text_block("-->")],
            [text_block("<!-- romp-msg-id: abc"), "-->"],
            [{"type": "image", "source": {"data": marker(MID)}}],
            [{"type": "tool_reference", "tool_name": marker(MID)}, IMAGE_BLOCK],
            ["<!-- romp-msg-id: x -->  <!-- romp-msg-id: y -->"],
            [{"nested": [[[marker(MID)]]]}],
            [],
            [{}],
            [[]],
            {"type": "text", "text": marker(MID)},
        ]
        for c in shapes:
            with self.subTest(content=json.dumps(c)[:60]):
                seg = result_seg(c)
                self.assertEqual(km._seg_mids(seg), _seg_mids_encoding(seg))

    def test_the_blocks_are_not_encoded(self):
        # A value json.dumps refuses beside a marker: encoding the whole result raised, the walk reads the
        # marker and passes the value by. This is the pin that the image and reference blocks are no longer
        # serialized on every build (the saving), not just that the answer is unchanged.
        seg = result_seg([text_block(marker(MID)), {"type": "image", "source": {"data": object()}}])
        with self.assertRaises(TypeError):
            _seg_mids_encoding(seg)
        self.assertEqual(km._seg_mids(seg), [MID])

    def test_only_strings_carrying_the_literal_are_encoded(self):
        # the saving itself: json.dumps runs on the one string that holds the marker literal — never on the image data
        # string, the block dicts or the marker-free text (test_the_blocks_are_not_encoded pins the container; this pins
        # the prefilter, which is what the micro-benchmark's saving comes from)
        seen = []; real = json.dumps

        def spy(obj, *a, **k):
            seen.append(obj)
            return real(obj, *a, **k)
        seg = result_seg([text_block("before " + marker(MID)), IMAGE_BLOCK, *REFERENCE_BLOCKS, text_block("after")])
        with mock.patch.object(json, "dumps", spy):
            ids = km._seg_mids(seg)
        self.assertEqual(ids, [MID])
        self.assertEqual(seen, ["before " + marker(MID)], "one encode, of the one string that carries the literal")

    def test_string_result_content_is_matched_raw_not_encoded(self):
        # a str tool_result content is matched as it is, never encoded — for the marker shapes the encoding would
        # escape too, where an encoded match would come back changed (a\"b, a\\\\b, caf\\u00e9); the plain-string case
        # above only exercises an id that encodes to itself
        for c in ('<!-- romp-msg-id: a"b -->', "<!-- romp-msg-id: a\\b -->", "<!-- romp-msg-id: café -->"):
            with self.subTest(content=c):
                seg = result_seg(c)
                self.assertEqual(km._seg_mids(seg), POSTAL_RE.findall(c), "the raw match")
                self.assertEqual(km._seg_mids(seg), _seg_mids_encoding(seg), "…which is what the oracle did for a str")

    def test_encoded_mids_covers_keys_values_and_nesting_in_document_order(self):
        # every string the encoding writes: keys, values, inside lists, tuples and dicts, in the order the
        # encoding writes them; a non-string key and the scalars contribute nothing
        m = ["1700000000.%d_1.TESTHOST" % i for i in range(6)]
        obj = {"a": marker(m[0]), marker(m[1]): [1, marker(m[2]), {"c": marker(m[3]), 4: marker(m[4])}, None, True],
               "d": (marker(m[5]),), "e": {}}
        self.assertEqual(km.em._encoded_mids(obj), m)
        self.assertEqual(km.em._encoded_mids(obj), POSTAL_RE.findall(json.dumps(obj)))
        sink = ["seed"]
        self.assertIs(km.em._encoded_mids([marker(m[0])], sink), sink, "appends to the list it is given")
        self.assertEqual(sink, ["seed", m[0]])
        self.assertEqual(km.em._encoded_mids(None), [])
        self.assertEqual(km.em._encoded_mids(object()), [])


def asst(bid, name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": bid, "name": name, "input": inp}]}}


def user_result(tid, content):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}}


def session_of(*atoms):
    return {"turns": [{"atoms": list(atoms)}]}


class FoldTasksResultRead(unittest.TestCase):
    """_fold_tasks encodes only the TaskCreate result it reads for 'Task #N'; every other result is stored
    as it came and never serialized."""

    def test_other_results_are_never_encoded(self):
        # a Bash result carrying a value json.dumps refuses: encoding every result up front raised here
        session = session_of(
            asst("b1", "Bash", {"command": "ls"}), user_result("b1", [{"type": "image", "source": {"data": object()}}]),
            asst("tc1", "TaskCreate", {"subject": "Wire the picker", "activeForm": "Wiring the picker"}),
            user_result("tc1", "Task #4 created successfully: Wire the picker"),
            asst("tu1", "TaskUpdate", {"taskId": "4", "status": "in_progress"}))
        self.assertEqual(km._fold_tasks(session),
                         [{"id": "4", "subject": "Wire the picker", "activeForm": "Wiring the picker", "status": "in_progress"}])

    def test_task_create_result_as_text_blocks_is_encoded_at_the_read(self):
        # a list-shaped TaskCreate result still yields the id the encoding found in it
        session = session_of(
            asst("tc1", "TaskCreate", {"subject": "Run the suite"}),
            user_result("tc1", [{"type": "text", "text": "Task #7 created successfully: Run the suite"}]))
        self.assertEqual([t["id"] for t in km._fold_tasks(session)], ["7"])

    def test_task_create_result_none_or_missing_falls_back_to_creation_order(self):
        session = session_of(
            asst("tc1", "TaskCreate", {"subject": "first"}), user_result("tc1", None),
            asst("tc2", "TaskCreate", {"subject": "second"}))
        self.assertEqual([t["id"] for t in km._fold_tasks(session)], ["c0", "c1"])

    def test_no_task_calls_gives_none(self):
        session = session_of(asst("b1", "Bash", {"command": "ls"}), user_result("b1", [{"type": "text", "text": "ok"}]))
        self.assertIsNone(km._fold_tasks(session))


if __name__ == "__main__":
    unittest.main()
