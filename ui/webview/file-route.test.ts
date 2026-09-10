// Where a FILE click opens (render.ts openPath): one ladder, pure and DOM-free (file-route.ts), so the
// whole table runs here. The inputs are read at click time by the caller: the gear's "File links open in"
// value as stored (anything), whether a shell frames this document, and whether the shell says the Files
// pane is on screen (render.ts panesOn, the shell's own broadcast). An OPEN Files pane takes the click
// whatever the setting says: the pane being open is the intent, and a file that opened as a modal over the
// chat while the pane sat there empty was the bug. Closed, the setting decides; "here" is the viewer as a
// modal over the pane that was clicked, the default and the only route where no shell exists.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { fileLinkRoute } from "./file-route";

const SETTINGS = ["chat", "pane", undefined, null, "purple", 42];   // the gear's two values, an unset store, foreign values

test("fileLinkRoute: no shell (standalone /chat), everything opens in place", () => {
  for (const setting of SETTINGS) for (const open of [true, false]) {
    assert.equal(fileLinkRoute(setting, false, open), "here", `setting=${String(setting)}, filesOpen=${open}`);
  }
});

test("fileLinkRoute: Files pane OPEN, the click goes there whatever the setting says", () => {
  for (const setting of SETTINGS) assert.equal(fileLinkRoute(setting, true, true), "pane", `setting=${String(setting)}`);
});

test("fileLinkRoute: Files pane CLOSED, the setting decides; only the literal 'pane' opts in", () => {
  assert.equal(fileLinkRoute("pane", true, false), "pane", "the setting names the Files pane: the shell brings it forward");
  assert.equal(fileLinkRoute("chat", true, false), "here", "the default: the viewer over the pane you clicked");
  assert.equal(fileLinkRoute(undefined, true, false), "here", "an unset store reads as the default");
  assert.equal(fileLinkRoute(null, true, false), "here");
  assert.equal(fileLinkRoute("purple", true, false), "here", "a foreign stored value falls to the default");
  assert.equal(fileLinkRoute(42, true, false), "here");
});

test("fileLinkRoute names only the two targets this chat can open", () => {
  const seen = new Set<string>();
  for (const setting of SETTINGS) for (const framed of [true, false]) for (const open of [true, false]) seen.add(fileLinkRoute(setting, framed, open));
  assert.deepEqual([...seen].sort(), ["here", "pane"]);
});
