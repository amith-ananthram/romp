// Publish the API-health merge and reading rules (./api-health-merge) to the kernel's landing SHELL, whose
// inline script (_LANDING_APIH_JS) paints the rail's dot and its popup but cannot import a module. Built as its
// own tiny dist entry (esbuild.js) and included by _landing() before that script, the way age-color-global.ts
// publishes the recency colour; the consumer feature-tests `window.__rompApiHealthMerge` and falls back to the
// local frame alone, so a stale dist cannot break the rail (T301).
import { mergeFrames, readHistory, mergeHistories, documentSeries, documentLedger, rebin, frameDot, machineText, machineLine, countsParts, agoWords, windowWords } from "./api-health-merge";

(window as unknown as { __rompApiHealthMerge: unknown }).__rompApiHealthMerge = {
  mergeFrames, readHistory, mergeHistories, documentSeries, documentLedger, rebin, frameDot, machineText, machineLine, countsParts, agoWords, windowWords,
};
