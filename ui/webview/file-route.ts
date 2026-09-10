// Where a click on a FILE or a FOLDER opens: one ladder, two callers (render.ts openPath for a file link,
// openBrowse for a folder), pure and DOM-free so the table runs for real in tests (file-route.test.ts,
// browse-route.test.ts). The caller reads every input at CLICK time:
//   pane       the gear's "File links open in" (settings.fileLinkPane): "chat" (the default) or "pane";
//              a foreign stored value reads as the default.
//   framed     window.parent !== window: a shell exists to relay to. Standalone /chat has no shell and no
//              other pane, so everything opens in place there.
//   filesOpen  the shell's Files-pane bit (render.ts panesOn.files, cached from the shell's own broadcast):
//              the pane is ON SCREEN, a desktop column toggled on or the tab showing on a phone.
// A verdict names the TARGET: "pane" is the Files pane (the shell brings a closed one forward; the click is
// the gesture), "here" is this document, the viewer or the file browser as a modal over the pane that was
// clicked, and "editor" (a folder in VS Code) is the host editor's own opener.

export type FileRoute = "pane" | "here";
export type BrowseRoute = FileRoute | "editor";

/** A FILE link. An OPEN Files pane takes the click whatever the setting says: the pane being open IS the
 *  intent, and a file that opened as a modal over the chat while the pane sat there empty was the bug.
 *  Closed, the setting decides; "here" is the default. */
export function fileLinkRoute(pane: unknown, framed: boolean, filesOpen: boolean): FileRoute {
  if (!framed) return "here";
  if (filesOpen) return "pane";
  return pane === "pane" ? "pane" : "here";
}

/** A FOLDER click (the folder shown under the chat, the system context card's Directory row, a tab menu's
 *  Browse files, a chat-hosted viewer's directory link; render.ts openBrowse) walks the SAME ladder as a
 *  file link: an open Files pane takes the listing, a closed one only when the setting names it, and
 *  otherwise the browser opens over the chat as it always has. Web only: in VS Code the folder link keeps
 *  the editor's own opener (asFolderLink's openFolder act), so the ladder is never asked. */
export function browseRoute(web: boolean, pane: unknown, framed: boolean, filesOpen: boolean): BrowseRoute {
  if (!web) return "editor";
  return fileLinkRoute(pane, framed, filesOpen);
}
