# ui/: the front-end source (all five panes)

The web UI the kernel serves: **chat**, **feed**, the outline (**fleet**), **files**, and **timeline**.
One set of sources renders everywhere — a browser tab on the kernel's port and
the VS Code/Cursor webviews (`vscode-extension/`) run the same bundles, so the
two hosts cannot drift.

## Layout

- **`webview/`** — the pane sources: `render.ts` (chat), `feed.ts` + `feed.css`
  (feed), `fleet.ts` + `fleet-pane.css` (fleet), `files.ts` + `files-pane.css`
  (files: the file viewer as its own column), `timeline-main.ts` +
  `timeline-pane.css` (timeline boot glue), plus shared pieces (`styles.css`,
  `actions.ts` click-safety helpers, `federation.ts` multi-kernel merge,
  `settings.ts`, `compact.ts`, …). Node tests sit beside their sources
  (`*.test.ts`) — many pin lines of `kernel/kernel.py` as strings, so run this
  suite whenever the kernel changes.
- **`romp-timeline-view.js`** — the timeline itself: deliberately ONE file
  shared by the Obsidian plugin and the web/VS Code timeline.
- `quote.ts`, `ask-types.ts` — shared helpers/types; top-level `*.test.ts`
  cover the timeline.

## Build story

- The **kernel** serves these sources to the browser and self-rebuilds stale
  bundles at startup (`_ensure_bundles`); the pane CSS files are read live.
- The **extension** bundles the same files into its VSIX with
  `vscode-extension/esbuild.js`.

A dist-only change needs no kernel restart — rebuild + browser refresh; the
VS Code extension needs `vscode-extension/install.sh` (VSIX reinstall). Run
tests with `npm test` from `vscode-extension/` (it owns `node_modules`), and
`npm run typecheck` before merging: tests read these files as strings and
esbuild only transpiles, so type errors can ship green.
