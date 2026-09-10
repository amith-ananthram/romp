// @-mention autocomplete in the composer: "@ro" lists the live sessions whose names match, and a pick
// inserts the plain "@name " an agent's mail tools take. The rules live in composer-mention.ts, a pure
// module, and run for real here: the trigger (an "@" opening a word, never inside an email address or a
// path), the matcher (a prefix before a substring, case-insensitive, the session being written to left
// out, twelve rows at most with a count of the rest, remote names by host or by name), the token
// (relative to the RECIPIENT's kernel: the display name when writing to a local session, the bare name
// when writing to a remote one), the insertion (refused when the caret no longer ends the token), the
// keyboard model (Enter picks while the card is open and is not consumed otherwise, so it keeps sending)
// and the transcript segmenter (the trigger's word rule). The DOM half runs in headless Chromium in
// composer-mention-pane.test.ts. Synthetic names only (web, api, tests, notes-api, TESTHOST; placeholder ids).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { MENTION_MAX_ROWS, insertMention, matchMentions, mentionBareName, mentionKeyAction, mentionMoreNote,
         mentionQuery, mentionSegments, mentionToken, rankMentions } from "./composer-mention";
import type { MentionCandidate } from "./composer-mention";

const SELF = "11111111-2222-4333-8444-555555555555";
const c = (id: string, name: string, extra: Partial<MentionCandidate> = {}): MentionCandidate => ({ id, name, ...extra });
const ROSTER: MentionCandidate[] = [
  c(SELF, "web", { color: { bg: "#3a7bd5", fg: "#ffffff" } }),
  c("22222222-3333-4444-8555-666666666666", "api", { color: { bg: "#d5643a", fg: "#ffffff" } }),
  c("33333333-4444-4555-8666-777777777777", "tests"),
  c("44444444-5555-4666-8777-888888888888", "notes-api"),
  c("TESTHOST:55555555-6666-4777-8888-999999999999", "TESTHOST:web"),
  c("TESTHOST:66666666-7777-4888-8999-aaaaaaaaaaaa", "TESTHOST:apidocs"),
];

// ── the trigger ──

test("an @ opening a word, plus one or more characters, with the caret ending the word, is the query", () => {
  assert.deepEqual(mentionQuery("@ro", 3), { start: 0, query: "ro" }, "at the start of the composer");
  assert.deepEqual(mentionQuery("ask @ro", 7), { start: 4, query: "ro" }, "after a space");
  assert.deepEqual(mentionQuery("first line\n@a", 13), { start: 11, query: "a" }, "after a newline");
  assert.deepEqual(mentionQuery("ask @ro to look", 7), { start: 4, query: "ro" }, "with text after the word, the caret at its end");
  assert.deepEqual(mentionQuery("@TESTHOST:we", 12), { start: 0, query: "TESTHOST:we" }, "a host-prefixed query keeps its colon");
});

test("no query: a bare @, an @ inside an email or a path, a space after the word, a caret inside the word", () => {
  assert.equal(mentionQuery("@", 1), null, "the bare @ opens nothing: one character at least");
  assert.equal(mentionQuery("mail a@b.example", 16), null, "an email address");
  assert.equal(mentionQuery("see /tmp/@x", 11), null, "a path");
  assert.equal(mentionQuery("ask @ro ", 8), null, "a space closes the token");
  assert.equal(mentionQuery("ask @romp", 7), null, "the caret inside the word (@ro|mp)");
  assert.equal(mentionQuery("ask @ro@mp", 10), null, "a second @ is not part of a name");
  assert.equal(mentionQuery("", 0), null);
  assert.equal(mentionQuery("@ro", 9), null, "a caret past the end is no caret");
  assert.equal(mentionQuery("@ro", -1), null);
});

// ── the matcher ──

test("a prefix of the name ranks before a substring, case-insensitively, alphabetical within a rank", () => {
  const names = (q: string) => matchMentions(q, ROSTER, null).map((x) => x.name);
  assert.deepEqual(names("api"), ["api", "TESTHOST:apidocs", "notes-api"], "prefixes first, then the substring hit");
  assert.deepEqual(names("API"), ["api", "TESTHOST:apidocs", "notes-api"], "case does not matter");
  assert.deepEqual(names("s"), ["TESTHOST:apidocs", "notes-api", "tests", "TESTHOST:web"],
    "substring hits (a remote's host counts) stay alphabetical by bare name");
  assert.deepEqual(names("zzz"), [], "a query nothing matches is empty: the card closes");
  assert.deepEqual(names(""), [], "an empty query matches nothing");
});

test("the session being written to is left out; the others stay", () => {
  assert.deepEqual(matchMentions("we", ROSTER, SELF).map((x) => x.name), ["TESTHOST:web"], "the local web is the writer's own session");
  assert.deepEqual(matchMentions("we", ROSTER, null).map((x) => x.name), ["web", "TESTHOST:web"],
    "with no self both webs are listed: same rank, same bare name, the local one first");
});

test("a remote session matches by its bare name (ranked with the locals) and by its host prefix", () => {
  const remote = ROSTER[4];
  assert.equal(mentionBareName(remote), "web");
  assert.equal(mentionBareName(ROSTER[1]), "api", "a local name is its own bare name");
  // by host: "@te" finds every session on that host, after the local whose NAME starts with it and
  // before the substring hit (notes-api)
  assert.deepEqual(matchMentions("te", ROSTER, null).map((x) => x.name), ["tests", "TESTHOST:apidocs", "TESTHOST:web", "notes-api"]);
});

test("the list stops at twelve rows, best matches first, and the count of the rest is a note, never a silent drop", () => {
  const many = Array.from({ length: 20 }, (_, i) => c("id-" + i, "romp-" + String(i).padStart(2, "0")));
  const out = matchMentions("ro", many, null);
  assert.equal(out.length, MENTION_MAX_ROWS);
  assert.equal(MENTION_MAX_ROWS, 12);
  assert.deepEqual(out.map((x) => x.name), many.slice(0, 12).map((x) => x.name), "alphabetical within the prefix rank");
  const all = rankMentions("ro", many, null);
  assert.equal(all.length, 20, "the ranking itself is uncapped");
  assert.deepEqual(all.slice(0, MENTION_MAX_ROWS), out, "the card's rows are the ranking's head");
  assert.equal(mentionMoreNote(all.length), "8 more, keep typing");
  assert.equal(mentionMoreNote(13), "1 more, keep typing");
  assert.equal(mentionMoreNote(12), null, "every match shown: no note");
  assert.equal(mentionMoreNote(0), null);
  assert.equal(rankMentions("zzz", many, null).length, 0);
  assert.deepEqual(rankMentions("api", ROSTER, SELF), matchMentions("api", ROSTER, SELF), "under the cap the two agree, self excluded in both");
});

test("a roster entry with no name, or a missing entry, is skipped rather than thrown on", () => {
  const odd = [c("a", ""), null as unknown as MentionCandidate, c("b", "web")];
  assert.deepEqual(rankMentions("w", odd, null).map((x) => x.id), ["b"]);
});

// ── the token and the insertion ──

test("the token is @ plus the name the RECEIVING agent's mail tools take, then one space", () => {
  const local = ROSTER[1];                  // api, on this kernel
  const remote = ROSTER[4];                 // TESTHOST:web, on the kernel this one labels TESTHOST
  const remoteSib = ROSTER[5];              // TESTHOST:apidocs, same kernel as remote
  const third = c("OTHERHOST:77777777-8888-4999-8aaa-bbbbbbbbbbbb", "OTHERHOST:api");   // a third kernel
  // writing to a session on THIS kernel: the display name, which is what this kernel's postal resolves
  // (a peer is registered under the label the frame prefixes)
  for (const to of [undefined, null, "", SELF]) {
    assert.equal(mentionToken(local, to), "@api ", "local recipient, local session");
    assert.equal(mentionToken(remote, to), "@TESTHOST:web ", "local recipient, remote session: this kernel's label for its peer");
    assert.equal(mentionToken(third, to), "@OTHERHOST:api ");
  }
  assert.equal(mentionToken(local), "@api ", "the recipient may be omitted: the local rule");
  // writing to a session on ANOTHER kernel: that kernel's postal resolves the token, and the frame
  // knows none of its labels, so every name goes in bare
  assert.equal(mentionToken(remoteSib, remote.id), "@apidocs ", "a sibling on the recipient's own kernel is local there");
  assert.equal(mentionToken(local, remote.id), "@api ", "a session on this kernel: its label over there is unknown, bare");
  assert.equal(mentionToken(third, remote.id), "@api ", "a session on a third kernel: likewise bare");
  assert.equal(mentionToken(remote, third.id), "@web ", "and the other way round");
});

test("inserting replaces the typed @query and lands the caret after the space; the tail is kept", () => {
  const at = mentionQuery("ask @ap to look", 7)!;
  const out = insertMention("ask @ap to look", at, 7, mentionToken(ROSTER[1]));
  assert.equal(out.text, "ask @api  to look");
  assert.equal(out.caret, "ask @api ".length);
  const end = insertMention("@te", { start: 0, query: "te" }, 3, "@tests ");
  assert.deepEqual(end, { text: "@tests ", caret: 7 });
});

test("an insert is refused, text and caret unchanged, when the caret no longer ends the token (a caret move the card never saw)", () => {
  const at = { start: 4, query: "ro" };
  // Ctrl+A put the caret at 0 with the card still open; a splice here would repeat the draft
  assert.deepEqual(insertMention("ask @ro", at, 0, "@romp "), { text: "ask @ro", caret: 0 }, "never 'ask @romp ask @ro'");
  assert.deepEqual(insertMention("ask @ro", at, 2, "@romp "), { text: "ask @ro", caret: 2 }, "a caret before the @");
  assert.deepEqual(insertMention("ask @ro", at, 6, "@romp "), { text: "ask @ro", caret: 6 }, "a caret inside the token");
  assert.deepEqual(insertMention("ask @ro now", at, 11, "@romp "), { text: "ask @ro now", caret: 11 }, "a caret past the token's end");
  assert.deepEqual(insertMention("ask @ro", at, 8, "@romp "), { text: "ask @ro", caret: 8 }, "a caret past the text");
  assert.deepEqual(insertMention("ask @xy", at, 7, "@romp "), { text: "ask @xy", caret: 7 }, "a stale token: the text under it changed");
  assert.deepEqual(insertMention("line one\nask @ro", at, 0, "@romp "), { text: "line one\nask @ro", caret: 0 }, "PageUp on a two-line draft");
  // and the same token with the caret where it belongs still inserts
  assert.deepEqual(insertMention("ask @ro", at, 7, "@romp "), { text: "ask @romp ", caret: 10 });
  assert.deepEqual(insertMention("ask @ro now", at, 7, "@romp "), { text: "ask @romp  now", caret: 10 });
});

// ── the keyboard model ──

test("with the card CLOSED no key is consumed, so Enter keeps sending and Escape keeps leaving the composer", () => {
  for (const k of ["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown"]) assert.equal(mentionKeyAction(k, false, 0, 3), null, k);
  assert.equal(mentionKeyAction("Enter", true, 0, 0), null, "an open flag with no rows is not a card");
});

test("with the card OPEN: arrows move and wrap, Enter and Tab pick the highlighted row, Escape closes, a modifier passes through", () => {
  assert.deepEqual(mentionKeyAction("ArrowDown", true, 0, 3), { kind: "move", sel: 1 });
  assert.deepEqual(mentionKeyAction("ArrowDown", true, 2, 3), { kind: "move", sel: 0 }, "wraps to the top");
  assert.deepEqual(mentionKeyAction("ArrowUp", true, 0, 3), { kind: "move", sel: 2 }, "wraps to the bottom");
  assert.deepEqual(mentionKeyAction("Enter", true, 1, 3), { kind: "pick", sel: 1 });
  assert.deepEqual(mentionKeyAction("Tab", true, 1, 3), { kind: "pick", sel: 1 });
  assert.deepEqual(mentionKeyAction("Enter", true, 7, 3), { kind: "pick", sel: 2 }, "a highlight past the rows picks the last one");
  assert.deepEqual(mentionKeyAction("Escape", true, 1, 3), { kind: "close" });
  assert.equal(mentionKeyAction("a", true, 1, 3), null, "typing narrows through the input handler, not here");
  assert.equal(mentionKeyAction("Enter", true, 1, 3, true), null, "Shift+Enter stays a newline, Cmd+Enter stays stage");
});

// ── the transcript segmenter ──

test("a typed @name that names a live session is a segment with the hit; other words, emails and paths stay text", () => {
  const live = new Map([["web", "S-web"], ["TESTHOST:api", "S-rapi"]]);
  const lookup = (w: string) => live.get(w) ?? null;
  assert.deepEqual(mentionSegments("ask @web and @nobody, cc a@b.example /tmp/@x", lookup),
    [{ text: "ask " }, { text: "@web", hit: "S-web" }, { text: " and @nobody, cc a@b.example /tmp/@x" }]);
  assert.deepEqual(mentionSegments("@web.", lookup), [{ text: "@web", hit: "S-web" }, { text: "." }], "trailing punctuation stays outside");
  assert.deepEqual(mentionSegments("(@TESTHOST:api) please", lookup), [{ text: "(@TESTHOST:api) please" }], "an @ after a bracket is not a word start, the trigger's rule");
  assert.deepEqual(mentionSegments("@web @TESTHOST:api", lookup),
    [{ text: "@web", hit: "S-web" }, { text: " " }, { text: "@TESTHOST:api", hit: "S-rapi" }], "two in a row");
  assert.deepEqual(mentionSegments("plain text", lookup), [{ text: "plain text" }]);
  assert.deepEqual(mentionSegments("", lookup), []);
  for (const s of ["ask @web and @nobody.", "@web, @web!", "x @web", "cc @web@mastodon.example"]) {
    assert.equal(mentionSegments(s, lookup).map((g) => g.text).join(""), s, "the segments concatenate back to the input");
  }
});

test("a word with a second @ in it is not a mention, the trigger's rule: @name@handle stays text, whole", () => {
  const live = new Map([["web", "S-web"]]);
  const lookup = (w: string) => live.get(w) ?? null;
  assert.deepEqual(mentionSegments("cc @web@mastodon.example", lookup), [{ text: "cc @web@mastodon.example" }], "a fediverse-style handle");
  assert.deepEqual(mentionSegments("@web@web", lookup), [{ text: "@web@web" }], "no chip on the head");
  assert.deepEqual(mentionSegments("@web@", lookup), [{ text: "@web@" }], "a trailing @");
  assert.deepEqual(mentionSegments("ask @web,@web", lookup), [{ text: "ask @web,@web" }], "punctuation then @ is still one word");
  assert.deepEqual(mentionSegments("@web@x and @web", lookup), [{ text: "@web@x and " }, { text: "@web", hit: "S-web" }], "the clean one after it still chips");
  assert.equal(mentionQuery("cc @web@mastodon.example", 7), null, "and the trigger opened no card for it either");
});
