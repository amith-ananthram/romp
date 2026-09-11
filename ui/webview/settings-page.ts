// The settings page (kernel /settings, _settings_page): the ⛭ gear on a page of its own (the user
// 2026-09-10). The gear rode the feed bundle since 2026-07-13, which made the Feed pane structurally
// required in the dashboard: the rail gear, the phone's settings action and the palette all posted
// openSettings into #f-feed. Now the shell embeds this page as the hidden #f-settings iframe (not a
// pane: no rail button, no tab, no gutter), posts the open request here, and lifts the iframe
// full-window while the modal is open ({romp:'settings', on} from gear.js, body.settings-open in the
// shell), so the Feed pane can be hidden, or later left unloaded, with the gear untouched.
//
// This page is the gear and nothing else: no pushed view (the shim runs with the stale opt-out, the
// Files pane's pattern), so the kernel builds nothing for app=settings; the gear's kernel ops
// (setAutoNudge, setJudgeModel, browseDir, …) ride the shim's socket through the same
// acquireVsCodeApi() channel feed.ts hands it, and their replies (browseResult, settingRefused,
// the models frame) come back on that socket as window messages, which gear.js already listens for.
// `ownPage`: with nothing under the modal there is no pane rect to pin the body to, so the gear skips
// its rs-lifted measurement and the page stays transparent under the dim (the panels rule in
// ui/CLAUDE.md: the dashboard behind the backdrop is dimmed, never hidden).
import { installSettingsSync, loadSettings, onExternalSettingsChange } from "./settings";
import { applyTheme } from "./theme";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { initGear } = require("./gear.js");

const api =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;
initGear((m: Record<string, unknown>) => api?.postMessage(m), { ownPage: true });
installSettingsSync();   // a gear save relayed by a host (VS Code's fan-out shape) lands here too
applyTheme(document, loadSettings());
onExternalSettingsChange((s) => applyTheme(document, s));
// the handshake every pane sends: this socket carries keepalives and op replies only, and the kernel's
// caps frame answering it is what the shim reads as "this page was served whole" (readyAcked)
api?.postMessage({ type: "ready" });
