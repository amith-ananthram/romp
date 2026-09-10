// Six more grammars for fenced code in a viewed file, beside the ten the chat and the viewer register: rust (alias rs),
// go (golang), c (h), java, sql, and toml, which highlight.js ships no module for; its ini grammar is "TOML, also INI"
// and declares the alias, so it is registered under both names. Registered on the bundle's one hljs core by this module,
// which file-view.ts imports, so the feed bundle gains them through the viewer, and the chat bundle, which imports
// file-view.ts, gains them too. The chat's auto-detection of an unlabeled fence keeps to its ten (highlight-cache.ts
// AUTO_LANGUAGES): a chat tail's cost and its guesses do not change, and a fence in a reply labelled `rust` highlights as
// one. Cost per bundle: about 18 KB minified, 6.5 KB gzipped. Nothing here is a viewer-only name: the aliases are the
// grammars' own, spelled so `getLanguage("rs")` and `getLanguage("toml")` answer.
import hljs from "highlight.js/lib/core";
import rust from "highlight.js/lib/languages/rust";
import go from "highlight.js/lib/languages/go";
import c from "highlight.js/lib/languages/c";
import java from "highlight.js/lib/languages/java";
import sql from "highlight.js/lib/languages/sql";
import ini from "highlight.js/lib/languages/ini";

/** The six grammars, by every name a fence may use. */
export const VIEWER_GRAMMARS: Record<string, unknown> = {
  rust, rs: rust, go, golang: go, c, h: c, java, sql, ini, toml: ini,
};

for (const [name, lang] of Object.entries(VIEWER_GRAMMARS)) {
  try { hljs.registerLanguage(name, lang as any); } catch { /* dup alias */ }
}
