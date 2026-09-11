import { test } from "node:test";
import * as assert from "node:assert/strict";
import { compactDisplay, summarizeTools, toolCounts, itemAnchor, STANDALONE_TOOLS, type DisplayItem } from "./compact";

test("compactDisplay: thinking is dropped entirely", () => {
  const d = compactDisplay(["user", "thinking", "assistant"]);
  assert.deepEqual(d, [{ kind: "event", index: 0 }, { kind: "event", index: 2 }]);
});

test("compactDisplay: a run of consecutive tools collapses to one toolgroup", () => {
  const d = compactDisplay(["user", "tool", "tool", "tool", "assistant"]);
  assert.deepEqual(d, [
    { kind: "event", index: 0 },
    { kind: "toolgroup", indices: [1, 2, 3] },
    { kind: "event", index: 4 },
  ]);
});

test("compactDisplay: thinking between tools does NOT break the run (it's dropped first)", () => {
  const d = compactDisplay(["tool", "thinking", "tool"]);
  assert.deepEqual(d, [{ kind: "toolgroup", indices: [0, 2] }]);
});

test("compactDisplay: visible content between tools DOES split the run; each lone tool stays inline", () => {
  const d = compactDisplay(["tool", "assistant", "tool"]);
  assert.deepEqual(d, [
    { kind: "event", index: 0 },   // a lone tool renders inline, not a 1-element toolgroup (the user 2026-06-22)
    { kind: "event", index: 1 },
    { kind: "event", index: 2 },
  ]);
});

test("compactDisplay: a LONE tool renders inline; only a run of ≥2 collapses (the user 2026-06-22)", () => {
  assert.deepEqual(compactDisplay(["assistant", "tool", "assistant"]),
    [{ kind: "event", index: 0 }, { kind: "event", index: 1 }, { kind: "event", index: 2 }]);
  assert.deepEqual(compactDisplay(["tool", "tool"]), [{ kind: "toolgroup", indices: [0, 1] }]);
  assert.deepEqual(compactDisplay(["tool"]), [{ kind: "event", index: 0 }]);
});

test("compactDisplay: a trailing tool run is flushed", () => {
  const d = compactDisplay(["assistant", "tool", "tool"]);
  assert.deepEqual(d, [{ kind: "event", index: 0 }, { kind: "toolgroup", indices: [1, 2] }]);
});

test("compactDisplay: AskUserQuestion is standalone — it breaks the run and passes through first-class", () => {
  // [Read, Edit, AskUserQuestion, Bash, Bash] → group(Read,Edit), the ask first-class, group(Bash,Bash).
  // The ask must NOT be swept into a toolgroup (the user 2026-06-17): it's the "you answered" box.
  const kinds = ["tool", "tool", "tool", "tool", "tool"];
  const names = ["Read", "Edit", "AskUserQuestion", "Bash", "Bash"];
  assert.deepEqual(compactDisplay(kinds, names), [
    { kind: "toolgroup", indices: [0, 1] },
    { kind: "event", index: 2 },
    { kind: "toolgroup", indices: [3, 4] },
  ]);
  assert.ok(STANDALONE_TOOLS.has("AskUserQuestion"));
});

test("compactDisplay: a lone AskUserQuestion run is a standalone event, never a 1-tool group", () => {
  assert.deepEqual(compactDisplay(["tool"], ["AskUserQuestion"]), [{ kind: "event", index: 0 }]);
});

test("compactDisplay: without names, every tool still collapses (back-compat)", () => {
  assert.deepEqual(compactDisplay(["tool", "tool", "tool"]), [{ kind: "toolgroup", indices: [0, 1, 2] }]);
});

test("summarizeTools: capitalized tool names, ordered by count desc, pluralized", () => {
  assert.equal(summarizeTools(["Edit", "Edit", "Edit", "Read", "Read", "Bash"]), "3 Edits, 2 Reads, 1 Bash");
});

test("summarizeTools: Edit variants merge as Edits; other tools keep their own name", () => {
  assert.equal(summarizeTools(["Edit", "MultiEdit", "Grep", "Glob"]), "2 Edits, 1 Grep, 1 Glob");
});

test("summarizeTools: a single tool is singular; Bash pluralizes correctly", () => {
  assert.equal(summarizeTools(["Read"]), "1 Read");
  assert.equal(summarizeTools(["Bash", "Bash"]), "2 Bashes");
});

test("toolCounts: returns ordered, pluralized {label,count} for styled rendering", () => {
  assert.deepEqual(toolCounts(["Edit", "Edit", "Read"]), [{ label: "Edits", count: 2 }, { label: "Read", count: 1 }]);
});

test("every non-tool event owns exactly one display unit — the scroll-mark translation's ground truth", () => {
  // eventUnitIndex (render.ts) inverts this stream to map mark anchors (events) onto the frame's
  // unit space; that inversion is sound only if a user/assistant event is never folded into a
  // group and never dropped (the user 2026-08-18, whose reply notches vanished in compact mode)
  const kinds = ["user", "tool", "tool", "assistant", "user", "tool", "user"];
  const items = compactDisplay(kinds, kinds.map((k) => (k === "tool" ? "Bash" : undefined)));
  const seen = new Map<number, number>();
  items.forEach((it, u) => {
    if (it.kind === "toolgroup" || it.kind === "noticegroup") for (const i of it.indices) seen.set(i, u);   // noticegroup since 2026-09-08
    else seen.set(it.index, u);
  });
  for (let i = 0; i < kinds.length; i++) {
    assert.ok(seen.has(i), "event " + i + " (" + kinds[i] + ") maps to a unit");
    if (kinds[i] !== "tool") {
      const u = seen.get(i)!;
      const it = items[u];
      assert.equal(it.kind, "event", "a non-tool event is its OWN unit, never inside a fold");
    }
  }
});

test("consecutive retry-recovery notes fold like a tool run — a lone one stays first-class (T131)", () => {
  // the user 2026-08-27: seventeen consecutive 'Recovered after N retries' rows are a flood; the
  // collapsed-run idiom fits consecutive same-shape machine notices exactly. 2026-09-08: the fold is the
  // generic NOTICEGROUP (a bare "retried" run still folds when the caller passes no foldability array).
  const kinds = ["user", "retried", "retried", "retried", "assistant", "retried", "user"];
  const items = compactDisplay(kinds);
  assert.deepEqual(items, [
    { kind: "event", index: 0 },
    { kind: "noticegroup", indices: [1, 2, 3] },
    { kind: "event", index: 4 },
    { kind: "event", index: 5 },   // a lone recovery renders first-class, like a lone tool
    { kind: "event", index: 6 },
  ]);
});

test("the foldable set is the caller's (isFoldableNotice): effort/model-swap/reload/interrupt pairs fold; peers and boundaries never do (2026-09-08)", () => {
  const kinds = ["user", "effortApplied", "modelFallback", "user", "assistant", "postal-service", "compact", "retried", "retried"];
  const notices = [false, true, true, true /* an interrupt marker */, true /* its settle */, false, false, true, true];
  assert.deepEqual(compactDisplay(kinds, undefined, notices), [
    { kind: "event", index: 0 },
    { kind: "noticegroup", indices: [1, 2, 3, 4] },
    { kind: "event", index: 5 },   // a peer's mail stands alone — a reply may be owed
    { kind: "event", index: 6 },   // a compaction boundary is a boundary
    { kind: "noticegroup", indices: [7, 8] },
  ]);
  // with the array present, a "retried" NOT marked foldable is a plain event (the array is authoritative)
  assert.deepEqual(compactDisplay(["retried", "retried"], undefined, [false, false]),
    [{ kind: "event", index: 0 }, { kind: "event", index: 1 }]);
});

test("retry runs and tool runs break each other — two folds, never one mixed group", () => {
  const kinds = ["retried", "retried", "tool", "tool", "retried", "retried"];
  const items = compactDisplay(kinds, kinds.map((k) => (k === "tool" ? "Bash" : undefined)));
  assert.deepEqual(items, [
    { kind: "noticegroup", indices: [0, 1] },   // noticegroup since 2026-09-08
    { kind: "toolgroup", indices: [2, 3] },
    { kind: "noticegroup", indices: [4, 5] },
  ]);
});

test("thinking hides without breaking a retry run, same as a tool run", () => {
  const kinds = ["retried", "thinking", "retried"];
  assert.deepEqual(compactDisplay(kinds), [{ kind: "noticegroup", indices: [0, 2] }]);   // noticegroup since 2026-09-08
});

// T339 (the user 2026-09-11): a collapsed run is placed and timed by its LATEST member, never its first. The user's
// transcript held a notice run whose first member carried yesterday's clock among today's rows.
test("itemAnchor: a lone event is its own anchor; a run anchors on its latest member, ties on the later one", () => {
  const epochs = [1000, 2000, 500, 2000, null, 3000];
  const at = (i: number) => epochs[i] ?? null;
  assert.equal(itemAnchor({ kind: "event", index: 2 }, at), 2);
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [2, 5] }, at), 5, "yesterday then today: today");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [5, 2] }, at), 5, "the latest, wherever it sits in the run");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [1, 3] }, at), 3, "a tie: the later member");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [0, 4, 1] }, at), 1, "a member with no epoch never anchors");
  assert.equal(itemAnchor({ kind: "toolgroup", indices: [2, 5] }, at), 2, "a tool run keeps its first member, as before");
  assert.equal(itemAnchor({ kind: "noticegroup", indices: [4, 4] } as DisplayItem, at), 4, "no timed member: the first");
});
