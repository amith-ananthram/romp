// Where a click on a FILE opens: one ladder, pure and DOM-free so the table runs for real in tests
// (file-route.test.ts). The caller (render.ts openPath) reads every input at CLICK time:
//   pane       the gear's "File links open in" (settings.fileLinkPane): "chat" (the default) or "pane";
//              a foreign stored value reads as the default.
//   framed     window.parent !== window: a shell exists to relay to. Standalone /chat has no shell and no
//              other pane, so everything opens in place there.
//   filesOpen  the shell's Files-pane bit (render.ts panesOn.files, cached from the shell's own broadcast):
//              the pane is ON SCREEN, a desktop column toggled on or the tab showing on a phone.
// A verdict names the TARGET: "pane" is the Files pane (the shell brings a closed one forward; the click is
// the gesture), "here" is this document, the viewer as a modal over the pane that was clicked.

export type FileRoute = "pane" | "here";

/** An OPEN Files pane takes the click whatever the setting says: the pane being open IS the intent, and a
 *  file that opened as a modal over the chat while the pane sat there empty was the bug. Closed, the
 *  setting decides; "here" is the default. */
export function fileLinkRoute(pane: unknown, framed: boolean, filesOpen: boolean): FileRoute {
  if (!framed) return "here";
  if (filesOpen) return "pane";
  return pane === "pane" ? "pane" : "here";
}
