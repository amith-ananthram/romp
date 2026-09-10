// The comment popover's chat-parity pass (the user 2026-08-25, four asks in one): the thread's
// statusline carries the chat's own working chip WITH the counting timer; the action buttons wear
// the chat's under-bubble button family; a fresh thread boots on the standard romp loader and then
// renders in its final (chat-parity) format ONCE — no intermediate msgs-projection flash; and the
// green→yellow settle repaints LIVE on the push event, no refocus needed (the kernel carries the
// settle count on the frame, since the wire dedup withholds the unchanged frames the client latch
// used to wait for). Source pins — the chat renderer has no jsdom harness (repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const COMMENTS = ui("webview", "comments.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the popover's bottom row IS a statusline: the chat's chip anatomy + counting timer", () => {
  // one vocabulary by construction: the row wears .statusline, the chip wears chip-working +
  // chip-pulse, the timer wears .status-timer — the chat's own classes, not restyled copies
  assert.match(RENDER, /const mrow = el\("div", "statusline cmt-meta-row"\);/);
  assert.match(RENDER, /mrow\.appendChild\(cmtStateChip\(th\)\);/);
  const chip = RENDER.split("function cmtStateChip(")[1].split("\nfunction ")[0];
  assert.ok(chip.includes('el("span", "chip chip-working")'), "the chat's working chip");
  assert.ok(chip.includes('el("span", "chip-pulse")'), "with its pulse label");
  assert.ok(chip.includes('timer.id = "cmt-work-timer"'), "and the counting timer");
  assert.ok(chip.includes("elapsedMs(th.sinceEpoch || null)"), "counting from the kernel's state-start epoch");
  // the 1s tick drives it exactly like the chat's #work-timer
  assert.match(RENDER, /const ct = document\.getElementById\("cmt-work-timer"\);/);
  assert.match(RENDER, /if \(cur && threadBusy\(cur\.th\.state\)\) ct\.textContent = elapsedMs\(cur\.th\.sinceEpoch \|\| null\);/);
  // the meta badges are the CHAT'S OWN controls (the user 2026-08-25 follow-up: sharing classes
  // wasn't enough — the popover copy drifted in dress, font, and element set): syncMetaControls
  // builds the FULL set (mode · model · effort · fast) into .sl-right, sid-scoped at the thread
  assert.match(RENDER, /const metaBox = el\("span", "spinner-meta"\);/);
  assert.match(RENDER, /syncMetaControls\(metaBox, threadMetaStatus\(th\), th\.tid\);/);
  const tms = RENDER.split("function threadMetaStatus(")[1].split("\nfunction ")[0];
  assert.ok(tms.includes("mode: th.mode ||"), "the mode badge's source rides the frame");
  assert.ok(tms.includes("fast: th.fast ||"), "the fast badge's source rides the frame");
  // …and the kernel sends both (the element-set gap: Auto and Fast were missing entirely)
  assert.match(KERNEL, /"mode": str\(meta\.get\("mode"\) or ""\), "fast": str\(meta\.get\("fast"\) or ""\),/);
  // the popover-local dress is GONE — no .cmt-meta chip skin to drift again; and the row restates
  // the page's base font inside the popover's 12px context (the adopted-context trap)
  const CSSs = CSS;
  assert.ok(!/\.cmt-meta \{/.test(CSSs), "no popover-local meta dress");
  assert.match(CSSs, /\.cmt-pop \.statusline \{[^}]*font-family: var\(--vscode-font-family\); font-size: var\(--vscode-font-size, 13px\); \}/);
  // …and the comments frame carries the epoch (milliseconds, the client convention)
  assert.match(KERNEL, /"sinceEpoch": since_ms,/);   // (the push-count field is retired — T102)
  // the in-place refresh keeps chip + timer live per frame (chips carry no listeners — click-safe)
  assert.match(RENDER, /if \(th && cs\) cs\.replaceWith\(cmtStateChip\(th\)\);/);
});

test("the action buttons wear the chat's under-bubble family; Fork stays out by the user's call", () => {
  assert.match(CSS, /\.cmt-act \{\s*\n\s*background: transparent;[^\n]*\n\s*border: 1px solid var\(--card-border\); color: var\(--dim\); border-radius: 5px; font-size: 0\.82em;\s*\n\s*padding: 1px 8px; cursor: pointer;\s*\n\}/);   // T141: the one button rest — dark ground, the feed hairline
  assert.match(CSS, /\.cmt-act:hover \{ border-color: var\(--accent\); color: var\(--accent\); background: var\(--accent-wash\); \}/);   // the feed word-button hover (2026-08-25): text+outline accent, never a fill
  // the popover's verbs stay Break out / Merge / Delete — the chat's Fork button is deliberately absent
  const pop = RENDER.split("function renderCommentPopover(")[1].split("\nfunction ")[0];
  assert.ok(!/data-act="fork"|showForkPrompt/.test(pop), "no fork button in the popover");
});

test("a fresh thread boots on the romp loader, then renders its final format ONCE", () => {
  // the loader holds the list until the thread's REAL render (its events) is ready — the plain
  // msgs projection no longer flashes as an intermediate format. Since T152 the guard requires
  // BOTH projections empty and holds PAST the backstop with a slower label (never blank; a msgs-
  // only old-kernel thread still falls through to the plain projection).
  assert.match(RENDER, /if \(!evs\.length && !th\.msgs\.length && th\.status === "open" && !th\.error\) \{/);
  assert.match(RENDER, /rompLoaderInner\(\s*\n?\s*slow \? "still opening — the thread's session is taking longer than usual…" : "opening the thread…",/);
  // logo-only in the POPOVER (the user 2026-08-25): the spinning swirl + dots stay, the R-o-m-p
  // letters drop — parameterized on the ONE shared builder, so the boot splash and the pane/revive
  // loaders keep the full treatment (their call sites pass no opts)
  assert.match(RENDER, /if \(opts\?\.wordmark === false\) \{\s*\n\s*word\.appendChild\(swirl\);/);
  assert.match(RENDER, /o\.appendChild\(rompLoaderInner\(`reviving “\$\{name\}”…`\)\);/, "the revive loader keeps the wordmark");
  // event-based fade: the frame that carries events refills the list and retires the hold
  assert.match(RENDER, /if \(evs\.length\) cmtBootSince\.delete\(th\.tid\);/);
  // …with the can't-trap backstop: past it, the hold releases and the projection paints after all
  assert.match(RENDER, /const CMT_BOOT_BACKSTOP_MS = 8000;/);
  assert.match(RENDER, /window\.setTimeout\(\(\) => \{ if \(openCommentKey\?\.tid === tid\) refillOpenCommentPop\(\); \}, CMT_BOOT_BACKSTOP_MS \+ 50\);/);
  // the user's own pending sends still acknowledge under the loader — through the CHAT'S queued
  // idiom (T104: the one-off gray pill is gone; cmtPendingQueued IS renderQueued's bare group)
  const gate = RENDER.split('!th.msgs.length && th.status === "open" && !th.error) {')[1].split("return;")[0];
  assert.ok(gate.includes("cmtPendingQueued(pend)"), "pending bubbles ride the boot view, chat-idiom");
});

test("the pulse is exchange-scoped (T102): send-gesture latch, reply-record clear — no push counts", () => {
  // the old kernel-carried confirm counter is retired root and branch: its all-quiet fork-birth
  // frames killed the create-window green, and a stall in its stepping parked green forever. The
  // pulse now latches at the SEND gesture and clears on the agent's reply RECORD in msgs
  // (comments.test.ts carries the full lifecycle pins; this guards the retirement on this surface).
  assert.doesNotMatch(KERNEL, /_comment_settle/);
  assert.doesNotMatch(RENDER, /settleConfirmed|commentBusyLatch/);
  assert.match(RENDER, /const cmtAwaitBase = new Map<string, CmtLatch>\(\);/);   // T237: the latch remembers the click's counts
  // the frame handler already repaints marks AND the open popover per frame — the live wire
  // T237: the open popover's seen-stamp now runs BEFORE the paint, so the paint is followed by the re-render only
  assert.match(RENDER, /applyCommentMarks\(sid\);\s*\n\s*if \(openCommentKey && openCommentKey\.sid === sid\) renderCommentPopover\(\);/);
});
test("a family click sends the kernel's alias default; a version the seed table lacks renders LOUDLY as new", () => {
  // the family row's label is the family's OWN label — never a version-table lookup — so an alias
  // default ("fable") renders the same as a pinned id did; the ✓ matches on the leading word
  assert.match(RENDER, /item\.textContent = c\.label;/);
  assert.match(RENDER, /return \(st\.model \|\| ""\)\.toLowerCase\(\)\.startsWith\(value\);/);
  // a version a running session's CLI reported that no seed table lists (kernel /models `learned`)
  // is offered AND marked, per the fail-loudly rule — a stale menu would hide a live model
  assert.match(RENDER, /learned\?: boolean/);
  assert.match(RENDER, /if \(v\.learned\) \{[\s\S]{0,600}el\("span", "meta-item-sub"\)/,
    "the marker wears the menu vocabulary's sub-line size and opacity");
  assert.match(RENDER, /if \(v\.learned\) \{[\s\S]{0,600}row\.title = /, "and says where the version came from");
});

test("the create dialog's model menu sends the family's remembered default, like the other two pickers", () => {
  // the new-thread dialog sent `c.value` — the family ALIAS — and never `c.default`, so with alias
  // semantics the same click FLOATED a new thread while it PINNED on the chat statusline and the
  // timeline lane. One rule on all three surfaces: the remembered pin, else the alias.
  assert.match(RENDER, /pendingCommentAnchor\[kind\] = kind === "model" \? \(c\.default \|\| c\.value\) : c\.value;/);
});

test("the version submenu opens with a Latest row: the one gesture back to floating once a family is pinned", () => {
  // the family row sends the pin, the version rows pin, and a typed "/model fable" leaves the pick
  // memory alone by design — so a pinned family had no way back. Latest clears the family's
  // remembered pin (the op carries `floating`; kernel _set_model_or_park forgets the pick) and
  // sends the alias, so the family follows the CLI's newest release again. An explicit user
  // gesture, so it may move state.
  assert.match(RENDER, /const pickValue = \(value: string, floating = false\) =>/);
  assert.match(RENDER, /if \(floating\) op\.floating = true;/, "the flag rides the setModel op");
  assert.match(RENDER, /const pinned = !!c\.default && c\.default !== c\.value;/, "pinned = the default is not the alias");
  assert.match(RENDER, /lhead\.textContent = "Latest";/);
  assert.match(RENDER, /pickValue\(c\.value, true\)/, "sends the ALIAS with the flag");
  assert.match(RENDER, /sub\.appendChild\(latest\);[\s\S]{0,300}for \(const v of versions\)/, "heads the submenu, ahead of the versions");
  assert.match(RENDER, /!pinned && isCurrentMeta\(kind, s\.status, c\.value\) \? " current" : ""/,
    "✓ when the family is unpinned and the session runs it");
  assert.match(RENDER, /const lsub = el\("div", "meta-item-sub"\);/, "the consequence line wears the menu vocabulary's sub-line");
});

test("the model meta-menu exposes VERSIONS: submenu, remembered default, keyboard (the user 2026-08-25)", () => {
  // families with >1 live version wear a side submenu (leftward — the menu anchors bottom-right):
  // hover or an arrow key reveals every version, each pickable with the current-✓; clicking the
  // family sends its remembered DEFAULT (the kernel /models `default`), never a bare shorthand.
  // Rides the ONE builder, so the chat statusline and the popover statusline both grow it.
  assert.match(RENDER, /const versions = kind === "model" \? \(c\.versions \|\| \[\]\) : \[\]/);
  assert.match(RENDER, /pickValue\(kind === "model" \? \(c\.default \|\| c\.value\) : c\.value\)/,
    "family click sends the remembered default");
  assert.match(RENDER, /versions\.length > 1 \?/, "single-version families stay flat");
  assert.match(RENDER, /el\("span", "meta-caret"\)/, "the caret affordance marks expandable families");
  assert.match(RENDER, /\(e\.key === "ArrowRight" \|\| e\.key === "ArrowLeft"\) && openSub/,
    "arrow keys expand (the submenu opens leftward, so both arrows work)");
  assert.match(RENDER, /e\.key === "ArrowLeft"[\s\S]{0,90}closeSub\(\); item\.focus\(\)/,
    "ArrowLeft inside the submenu collapses back to the family row");
  assert.match(RENDER, /pickValue\(v\.value\)/, "version rows pick their own full id");
  assert.match(RENDER, /\(s\.status\.model \|\| ""\)\.toLowerCase\(\) === v\.label\.toLowerCase\(\)/,
    "the ✓ marks the session's current version");
  assert.match(RENDER, /querySelectorAll\("\.meta-sub"\)\.forEach/, "closing the menu drops an open submenu");
  assert.match(CSS, /\.meta-caret \{ position: absolute; right: 22px/, "caret sits left of the ✓ slot");
});


test("the badges' COLOR is the chat's too: the rank tints ride the frame (the 2026-08-25 color rider)", () => {
  // the user's sighting was two layers: a stale pre-parity window, AND a real gap underneath — the
  // chat's model/effort labels are tinted by server-computed rank colors (st.modelColor/effortColor,
  // metaColor), and the comments frame never carried them, so the popover's stayed plain gray.
  assert.match(KERNEL, /"modelColor": _model_color\(\(reg\.get\("liveModel"\) or reg\.get\("model"\) or ""\) if reg else "",\s*\n\s*cm\.stops_for\(_colormap\(\)\)\),/);
  assert.match(KERNEL, /"effortColor": _effort_color\(\(reg\.get\("effort"\) or ""\) if reg else "",\s*\n\s*cm\.stops_for\(_colormap\(\)\)\),/);
  assert.match(RENDER, /modelColor: th\.modelColor, effortColor: th\.effortColor,\n\s*modelTone: \(th as any\)\.modelTone, effortTone: \(th as any\)\.effortTone \} as Status;/);
  // the equality bar (asserted headless over the built bundle, per the follow-up): computed
  // font-family/size/weight AND color/opacity equal chat↔popover for chip, timer, and all badges
});

test("the caret faces RIGHT and the submenu side is measured, right-preferred (the user 2026-08-25)", () => {
  assert.match(RENDER, /textContent = "\\u25B8"/, "the caret marks expandable, never the side");
  assert.ok(!RENDER.includes("\\u25C2"), "no left-facing caret anywhere");
  assert.match(RENDER, /if \(rr\.right \+ 4 \+ sw <= window\.innerWidth - 8\) sub\.style\.left = Math\.round\(rr\.right \+ 4\) \+ "px";/,
    "right side taken whenever it fits");
  assert.match(RENDER, /else sub\.style\.right = Math\.max\(8, window\.innerWidth - rr\.left \+ 4\) \+ "px";/,
    "left is the measured fallback, not the default");
});


test("the thread popover opens 70% wide right-aligned, 60% tall — and NEVER grows on content", () => {
  // the user 2026-08-25: "start out pretty big and fill in" — the size is fixed at open, streaming
  // fills within (.cmt-msgs flexes + scrolls; comments.test.ts pins that rule), the box never
  // reflows on a content event. Verified headless: 840×480 at 1200×800, right gap 8, zero rect
  // change across three streaming appends. The CREATE composer keeps its selection-point gesture.
  assert.match(RENDER, /if \(th && !pop\.style\.width\) \{ pop\.style\.width = Math\.round\(window\.innerWidth \* 0\.7\) \+ "px"; \}/);
  assert.match(RENDER, /if \(th && !pop\.style\.height\) \{ pop\.style\.height = Math\.round\(window\.innerHeight \* 0\.6\) \+ "px"; \}/);
  assert.match(RENDER, /const defaultX = th \? \(window\.innerWidth - r\.width - 8\) : \(window\.innerWidth - r\.width\) \/ 2;/);
  // a thread open ignores click coords — only a real drag parks the box elsewhere
  const opener = RENDER.split("function openCommentPopover(")[1].split("\nfunction ")[0];
  assert.ok(!opener.includes("commentPopPos = { x, y }"), "no click-coord seeding on thread open");
  // …and the CSS no longer hard-sizes the box (the inline open geometry owns it)
  assert.doesNotMatch(CSS, /\.cmt-pop \{[^}]*width: 440px/s);
  // the remembered size (2026-09-10, comment-pop-size.test.ts) sits ABOVE these lines and only fills the
  // inline size when a preference exists — so with nothing stored the two lines above still decide, byte
  // for byte, and the create dialog still writes no inline size at all
  const body = RENDER.split("function renderCommentPopover(")[1].split("\nfunction ")[0];
  assert.ok(body.indexOf("const pref = readCmtPopSize();") < body.indexOf("if (th && !pop.style.width)"));
  assert.match(body, /if \(pref\) \{[\s\S]*?pop\.style\.width = sz\.w \+ "px";[\s\S]*?\}\s*\n\s*\/\/ OPEN GEOMETRY/, "the preference branch, then the default");
});

test("the popover resizes from ANY edge or corner, macOS-style; the old grip keeps working", () => {
  // the user 2026-08-25: an 8px band per side is a live handle with the platform cursor, each pull
  // moving its own edge with the opposite one anchored, clamped to the CSS mins; the native
  // bottom-right grip's corner is deliberately deferred to (it keeps working exactly as before)
  const fn = RENDER.split("function wireEdgeResize(")[1].split("\nfunction ")[0];
  assert.ok(fn.includes('if (ev.clientX > r.right - 18 && ev.clientY > r.bottom - 18) return "";'), "the native grip corner defers");
  assert.ok(fn.includes('ne: "nesw-resize", sw: "nesw-resize", nw: "nwse-resize", se: "nwse-resize"'), "corner cursors");
  assert.ok(fn.includes('n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize"'), "edge cursors");
  assert.ok(fn.includes('if (zone.includes("w")) { wpx = r0.width - dx; left = r0.left + dx; }'), "west pulls west, east anchored");
  assert.ok(fn.includes('if (zone.includes("n")) { hpx = r0.height - dy; top = r0.top + dy; }'), "north pulls north, south anchored");
  assert.ok(fn.includes("const MIN_W = 300, MIN_H = 120;"), "min bounds match the CSS");
  assert.ok(fn.includes("pop.setPointerCapture(ev.pointerId);"), "capture keeps fast pulls on the edge");
  assert.ok(fn.includes("commentPopPos = { x: left, y: top };"), "a north/west pull persists position like the drag");
  assert.match(RENDER, /wireEdgeResize\(pop\);/);
  const CSSs = CSS;
  assert.match(CSSs, /resize: both/, "the native grip stays");
});

test("the pickers re-read /models on the kernel's models frame — the pick memory moved", () => {
  // Both pickers read a family's `default` from a /models list fetched ONCE at page load; nothing
  // mutated it after a pick and no push carried it. So after Latest un-pinned a family on the kernel,
  // the same tab's next family click sent the STALE pinned id and silently re-pinned; another
  // dashboard's pick moved the default unseen. Event-keyed: the kernel emits a models frame whenever
  // model-picks.json changes (a pin, a Latest un-pin, a refused pin dropped) or the catalog grows,
  // and the list is re-fetched IN PLACE on it — META_CHOICES keeps its reference, so the next family
  // click reads the fresh default. Never a poll.
  assert.match(RENDER, /function loadModelChoices\(\): void \{/);
  const loader = RENDER.split("function loadModelChoices(): void {")[1].split("\n}\n")[0];
  assert.ok(loader.includes('fetch(kernelUrl("/models"), { cache: "no-store" })'), "the same fetch as page load");
  assert.ok(loader.includes("MODEL_CHOICES.length = 0; MODEL_CHOICES.push(...d.models"), "refilled in place — the shared reference holds");
  assert.match(RENDER, /^loadModelChoices\(\);$/m, "…and page load is just the first call");
  assert.match(RENDER, /else if \(m\.type === "models"\) loadModelChoices\(\);/, "the frame arm");
  // the kernel side: one emitter, fired by every writer of the pick memory, to EVERY app that hosts a
  // picker — chat, timeline, and the feed, where the settings gear's judge-tier pickers live
  assert.match(KERNEL, /frame = \{"type": "models", "rev": _models_rev\[0\]\}/);
  assert.match(KERNEL, /for app in \("chat", "timeline", "feed"\):\s*\n\s*_send_to_app\(app, frame\)/);
  // …and the payload carries the same counter, so a consumer can drop a response older than one applied
  assert.match(KERNEL, /\{"rev": _rev,\s*\n\s*"models": \[/);
  assert.match(KERNEL, /_rev = _models_rev\[0\]\s*\n\s*_learned = _learned_versions\(\)/, "read before the list, never after it");
  const note = KERNEL.split("def _note_model_pick(")[1].split("\ndef ")[0];
  const forget = KERNEL.split("def _forget_model_pick(")[1].split("\ndef ")[0];
  assert.ok(note.includes("_models_changed()"), "a pin emits");
  assert.ok(forget.includes("_models_changed()"), "a forget emits");
});

test("the create dialog offers Latest for a pinned family — a new thread can start on the alias from here too", () => {
  // The dialog's menu is FLAT (no submenu, so no Latest row): once a family carried a pin, the family
  // row launched on the pin and nothing here launched on the alias. A pinned family now names its pin
  // on the family row and grows a "<Family> · Latest" row that sends the bare alias — a per-thread
  // launch pref, never the family's memory (an alias records nothing at the kernel's choke point).
  const dialog = RENDER.split("const buildMetaRow = (): HTMLElement => {")[1].split("\n    };\n")[0];
  assert.ok(dialog.includes("const pinnedTo = kind === \"model\" ? (c.default || \"\") : \"\";"), "the pin, from the /models default");
  assert.ok(dialog.includes("const pinned = !!pinnedTo && pinnedTo !== c.value;"));
  assert.ok(dialog.includes("pinSub.textContent = modelChoiceLabel(pinnedTo).label;"), "the family row names the pin it launches on");
  assert.ok(dialog.includes('lh.textContent = c.label + " · Latest";'), "the extra row");
  assert.ok(dialog.includes("if (pendingCommentAnchor) pendingCommentAnchor[kind] = c.value;"), "…sends the ALIAS");
  assert.ok(dialog.includes('latest = el("div", "meta-item" + (effVal === c.value ? " current" : ""))'), "✓ when the thread is set to float");
  assert.ok(dialog.includes("pinned ? effVal === pinnedTo :"), "the family row's ✓ means the pin, once there is a Latest row beside it");
  assert.ok(!dialog.includes("op.floating"), "no floating flag: the dialog never moves the family's memory");
});

test("the create name input wears no underline at rest (the user 2026-08-25)", () => {
  assert.match(CSS, /\.cmt-name \{[^}]*border-bottom: 1px solid transparent;/s);
  assert.doesNotMatch(CSS, /\.cmt-name \{[^}]*dashed/s);
  assert.match(CSS, /\.cmt-name:focus \{ outline: none; border-bottom-color: var\(--accent\); \}/, "focus still shows the editing affordance");
});
