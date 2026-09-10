// The staged stack (the user 2026-08-15): every rule executed.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { StagedStack, quoteReplyBody, stagedRunBody, stagedPosts, isSlashCommand } from "./staged-messages";

test("stage order is release order, and a flush is one-shot", () => {
  const s = new StagedStack();
  s.push("a", { text: "first", cites: [] });
  s.push("a", { text: "second", cites: [{ quote: "ctx" }] });
  s.push("a", { text: "third", cites: [] });
  assert.equal(s.count("a"), 3);
  assert.deepEqual(s.takeAll("a").map((m) => m.text), ["first", "second", "third"]);
  assert.equal(s.count("a"), 0, "released, not re-sendable");
  assert.deepEqual(s.takeAll("a"), []);
});

test("tabs hold separate stacks", () => {
  const s = new StagedStack();
  s.push("a", { text: "for a", cites: [] });
  s.push("b", { text: "for b", cites: [] });
  assert.deepEqual(s.takeAll("a").map((m) => m.text), ["for a"]);
  assert.equal(s.count("b"), 1, "flushing one tab never touches another");
});

test("discard removes exactly the one chip", () => {
  const s = new StagedStack();
  s.push("a", { text: "keep", cites: [] });
  s.push("a", { text: "drop", cites: [] });
  s.push("a", { text: "keep too", cites: [] });
  s.removeAt("a", 1);
  assert.deepEqual(s.list("a").map((m) => m.text), ["keep", "keep too"]);
});

test("the persistence round-trip keeps text, context and order — and drops junk", () => {
  const s = new StagedStack();
  s.push("a", { text: "one", cites: [{ quote: "q", title: "t" }] });
  s.push("a", { text: "two", cites: [] });
  const r = new StagedStack();
  r.restore(JSON.parse(JSON.stringify(s.entries())));
  assert.deepEqual(r.list("a").map((m) => m.text), ["one", "two"]);
  assert.equal((r.list("a")[0].cites[0] as any).quote, "q", "the context survives the reload");
  r.restore({ b: [{ text: "" }, { nope: 1 }, "junk"], c: "junk" });   // a hand-edited/old store
  assert.equal(r.count("b"), 0, "junk hydrates to nothing, never a crash");
  assert.equal(r.count("c"), 0);
});

test("a staged line stays inside the pane: shrinkable strips, ellipsis, click-to-expand tail", () => {
  // the user 2026-08-16 (screenshot): one long staged message refused to shrink — the composer's
  // strips are wrapped flex ITEMS, and without flex-basis 100% + min-width 0 their min-content
  // width is the nowrap label's full intrinsic width, so the line blew past the pane and dragged a
  // horizontal scroll with it. The pane must NEVER scroll sideways.
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(CSS, /#composer-files, #composer-staged, #composer-chips \{ flex: 1 1 100%; min-width: 0; max-width: 100%; \}/);
  assert.match(CSS, /body \{ display: flex; flex-direction: column; overflow-x: hidden; \}/,
    "the hard guarantee: a wide child is a layout bug, never a sideways scroll");
  assert.match(CSS, /\.composer-chip-label \{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap;/,
    "the one-line ellipsis that the shrinkable strip finally lets engage");
  assert.match(RENDER, /hint\.textContent = open \? "\(collapse\)" : "\(click to expand\)";/,
    "the tail names the gesture");
});

// The one-message release: a plain send posts the staged run and the typed message as ONE body, and the
// body is this module's return value, so these tests run the composition instead of reading render.ts.

const Q1 = { quote: "const a = 1;", src: "src/a.ts:12-12", title: "t" };
const Q2 = { quote: "first line\nsecond line", title: "t" };
// a goal citation names the card the words follow up on; a synthetic session id, private to this file
const G = { itemId: "7a1c3e5f-0b2d-4c6e-8f90-a1b2c3d4e5f6:g1", title: "t" };
const J = (v: unknown) => JSON.parse(JSON.stringify(v));   // drops undefined-valued keys, so shapes compare by content

test("quoteReplyBody: one section per quote in strip order, then the text; a file highlight names its source; no quote is the text alone", () => {
  assert.equal(quoteReplyBody([Q1], "rename it"),
    "Replying to this highlighted code (src/a.ts:12-12):\n> const a = 1;\n\nrename it");
  assert.equal(quoteReplyBody([Q2], "why two?"),
    "Replying to this part of the conversation:\n> first line\n> second line\n\nwhy two?");
  assert.equal(quoteReplyBody([Q1, Q2], "both"),
    quoteReplyBody([Q1], "") + "\n\n" + quoteReplyBody([Q2], "") + "\n\nboth", "stacked quotes, strip order, text last");
  assert.equal(quoteReplyBody([Q1], ""), "Replying to this highlighted code (src/a.ts:12-12):\n> const a = 1;",
    "context only: no dangling blank tail");
  assert.equal(quoteReplyBody([], "bare"), "bare", "no quote at all is the text alone, no leading blank lines");
  assert.equal(quoteReplyBody([], ""), "");
});

test("stagedRunBody: N items become ONE body in stage order, each as its quote block(s) then its comment, blank-line separated", () => {
  const items = [
    { text: "rename it", cites: [Q1] },
    { text: "why two?", cites: [Q2] },
    { text: "and a bare note", cites: [] },
  ];
  const body = stagedRunBody(items);
  assert.equal(body, [
    "Replying to this highlighted code (src/a.ts:12-12):\n> const a = 1;\n\nrename it",
    "Replying to this part of the conversation:\n> first line\n> second line\n\nwhy two?",
    "and a bare note",
  ].join("\n\n"));
  // each section is byte for byte what the item sent on its own before the release was one message
  for (const it of items) assert.ok(body.includes(quoteReplyBody(it.cites as any, it.text)), "the item's own body is a section");
  assert.ok(body.indexOf("rename it") < body.indexOf("why two?") && body.indexOf("why two?") < body.indexOf("and a bare note"), "stage order");
});

test("stagedRunBody: the typed message closes the body; alone, one item composes exactly what it sent before", () => {
  const items = [{ text: "rename it", cites: [Q1] }];
  assert.equal(stagedRunBody(items, { text: "that is all", cites: [] }),
    quoteReplyBody([Q1], "rename it") + "\n\nthat is all");
  // a typed message with its own quote chips keeps its quote block ahead of its words, after the staged run
  assert.equal(stagedRunBody(items, { text: "and this", cites: [Q2] }),
    quoteReplyBody([Q1], "rename it") + "\n\n" + quoteReplyBody([Q2], "and this"));
  // Send now: nothing typed, one item, the body it always sent
  assert.equal(stagedRunBody(items), quoteReplyBody([Q1], "rename it"));
  // a goal citation is not a quote and adds no section: the kernel wraps a goal follow-up itself
  assert.equal(stagedRunBody(items, { text: "follow-up words", cites: [G] }),
    quoteReplyBody([Q1], "rename it") + "\n\nfollow-up words");
});

test("stagedRunBody: an empty item adds nothing, no stray separators, and nothing at all is the empty string", () => {
  assert.equal(stagedRunBody([]), "");
  assert.equal(stagedRunBody([], { text: "just typed", cites: [] }), "just typed");
  assert.equal(stagedRunBody([{ text: "", cites: [] }], { text: "typed", cites: [] }), "typed", "an item with nothing to say is skipped");
  assert.equal(stagedRunBody([{ text: "", cites: [Q1] }]), quoteReplyBody([Q1], ""), "context only stages as its quote block");
  assert.equal(stagedRunBody([{ text: "t", cites: [{ quote: "" }] }]), "t", "an empty quote is not a quote: no lead-in, no empty block");
  assert.doesNotMatch(stagedRunBody([{ text: "a", cites: [] }, { text: "b", cites: [] }]), /^\n|\n$|\n\n\n/, "no leading, trailing or tripled blank lines");
});

// The release's post list: a goal follow-up and a slash command each go on their own, at their place in
// stage order, and the items between them share one body. render.ts routes exactly these posts, one
// routeUserMessage call each.

test("isSlashCommand mirrors the kernel's shape test: a slash, a name, then whitespace or the end, at the head of the trimmed text", () => {
  // the name's first character is ASCII on both sides; its tail is Unicode letters and digits, as the
  // kernel's word class is, so "/résumé" is a command and "/é" is not
  for (const t of ["/clear", "/clear ", " /compact", "/model opus", "/fast on", "/effort high\nmore lines", "/mcp:x", "/9lives", "/re-run now", "/clear\nfile.png", "/résumé", "/café now", "/a٣"])
    assert.ok(isSlashCommand(t), JSON.stringify(t));
  for (const t of ["", "a /clear", "/", "/ clear", "//", "/Users/x", "/a/b", "/-x", "/clear,now", "not a command", "see /clear", "/é", "/é x"])
    assert.ok(!isSlashCommand(t), JSON.stringify(t));
});

test("stagedPosts: the plain shapes are one post (Send now) or one post with the typed message last; nothing staged posts the typed message as itself", () => {
  const items = [{ text: "rename it", cites: [Q1] }, { text: "and a bare note", cites: [] }];
  assert.deepEqual(J(stagedPosts(items)), [{ text: stagedRunBody(items) }]);
  const typed = { text: "that is all", cites: [Q2], imgPaths: ["a.png"] };
  assert.deepEqual(J(stagedPosts(items, typed)), [{ text: stagedRunBody(items, typed), imgPaths: ["a.png"] }], "the typed images ride the run they close");
  // a typed goal citation wraps the run: the one post carries exactly that citation and the goal wraps the lot
  assert.deepEqual(J(stagedPosts(items, { text: "follow-up words", cites: [G] })), [{ text: stagedRunBody(items) + "\n\nfollow-up words", cites: [G] }]);
  // nothing staged: the typed message, citations and images intact, exactly as a send with no stack
  assert.deepEqual(J(stagedPosts([], typed)), [typed]);
  assert.deepEqual(J(stagedPosts([], { text: "/clear" })), [{ text: "/clear" }], "a typed command with nothing staged is the typed message");
  assert.deepEqual(stagedPosts([]), []);
});

test("stagedPosts: a typed slash command goes last on its own, never as the tail of the shared body (there, a /clear was prose the agent read)", () => {
  const items = [{ text: "rename it", cites: [Q1] }, { text: "why two?", cites: [Q2] }];
  for (const cmd of ["/clear", "/model opus", "/compact", "/fast on", "/clear\nshot.png"]) {
    const posts = stagedPosts(items, { text: cmd, cites: [], imgPaths: ["shot.png"] });
    assert.equal(posts.length, 2, cmd);
    assert.equal(posts[0].text, stagedRunBody(items), "the run goes without the command, and without the typed images");
    assert.equal(posts[0].imgPaths, undefined);
    assert.deepEqual(J(posts[1]), { text: cmd, cites: [], imgPaths: ["shot.png"] }, "the command is the whole text of its own post, its images with it");
  }
  // a typed command with a goal chip still goes alone, the chip on it
  assert.deepEqual(J(stagedPosts(items, { text: "/clear", cites: [G] })), [{ text: stagedRunBody(items) }, { text: "/clear", cites: [G] }]);
});

test("stagedPosts: a staged slash command goes alone at its place; a leading one never heads a shared body (the comments after it were its argument)", () => {
  const A = { text: "rename it", cites: [Q1] }, B = { text: "why two?", cites: [Q2] }, C = { text: "/compact", cites: [] };
  assert.deepEqual(stagedPosts([C, A, B], { text: "done", cites: [] }).map((p) => p.text),
    ["/compact", stagedRunBody([A, B], { text: "done" })]);
  assert.deepEqual(stagedPosts([A, C, B]).map((p) => p.text),
    [stagedRunBody([A]), "/compact", stagedRunBody([B])]);
  assert.deepEqual(stagedPosts([A, B, C], { text: "done", cites: [] }).map((p) => p.text),
    [stagedRunBody([A, B]), "/compact", "done"], "a run closed by a command: the typed message follows on its own");
  // the command goes exactly as it went before: its own citations with it. A quote chip on a command means
  // routeUserMessage wraps it as any quoted message (staged-list-cap.test.ts executes that post) and the
  // CLI reads it as prose, as it did when every item was its own message; only a bare command fires
  const Cq = { text: "/compact", cites: [Q1] };
  assert.deepEqual(J(stagedPosts([Cq, A])), [{ text: "/compact", cites: [Q1] }, { text: stagedRunBody([A]) }]);
});

test("stagedPosts: a goal follow-up goes alone at its place in stage order and the runs around it share a body each", () => {
  const A = { text: "rename it", cites: [Q1] }, Gm = { text: "on the card", cites: [G] }, B = { text: "why two?", cites: [Q2] };
  const typed = { text: "done", cites: [Q2], imgPaths: ["a.png"] };
  assert.deepEqual(J(stagedPosts([A, Gm, B], typed)), [
    { text: stagedRunBody([A]) },
    { text: "on the card", cites: [G] },
    { text: stagedRunBody([B], typed), imgPaths: ["a.png"] },
  ]);
  // a goal item last: the typed message goes on its own after it, its own citations with it
  assert.deepEqual(J(stagedPosts([A, Gm], { text: "done", cites: [G] })),
    [{ text: stagedRunBody([A]) }, { text: "on the card", cites: [G] }, { text: "done", cites: [G] }]);
  // a goal-only stage with nothing typed: the follow-up alone, no empty post
  assert.deepEqual(J(stagedPosts([Gm])), [{ text: "on the card", cites: [G] }]);
});

test("stagedPosts invariants over every shape: stage order across the posts, each item once, and no shared body carries a command section", () => {
  const cmd = { text: "/clear", cites: [] }, note = { text: "a note", cites: [Q1] }, bare = { text: "bare words", cites: [] }, goal = { text: "on the card", cites: [G] };
  const stages = [[cmd], [note, cmd], [cmd, note], [note, cmd, bare], [goal, cmd], [cmd, goal, note], [note, goal, bare, cmd, note], [note, bare]];
  const typeds = [undefined, { text: "/model x", cites: [] }, { text: "typed words", cites: [] }, { text: "typed words", cites: [G] }];
  for (const items of stages) for (const typed of typeds) {
    const posts = stagedPosts(items, typed);
    const all = posts.map((p) => p.text).join("\n\n");
    const seq = typed ? [...items, typed] : items;
    let at = -1;
    for (const it of seq) {
      const i = all.indexOf(it.text, at + 1);
      assert.ok(i > at, "in stage order: " + it.text + " in " + JSON.stringify(all));
      at = i;
    }
    // and each item's words exactly as many times as they were staged (the order walk above skips past a
    // doubled section without noticing it)
    for (const text of new Set(seq.map((it) => it.text)))
      assert.equal(all.split(text).length - 1, seq.filter((it) => it.text === text).length, "once per staging: " + text + " in " + JSON.stringify(all));
    for (const p of posts) {
      if (isSlashCommand(p.text)) { assert.equal(p.text.split("\n\n").length, 1, "a command is the whole post"); continue; }
      for (const s of p.text.split("\n\n")) assert.ok(!isSlashCommand(s), "no command inside a shared body: " + JSON.stringify(p.text));
    }
    // a goal citation rides at most one post, and only a post whose text ends with the words it was typed with
    for (const p of posts) if ((p.cites || []).some((c: any) => c && c.itemId)) assert.ok(p.text.endsWith("on the card") || p.text.endsWith("typed words"), JSON.stringify(p));
  }
});
