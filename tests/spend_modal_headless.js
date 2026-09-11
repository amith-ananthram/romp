// Drives the served landing page (tests/test_spend_modal_headless.py serves it) with playwright:
// click the usage readout → the spend modal opens over the dimmed dashboard, renders the per-session
// table + the stacked histogram with its unattributed stack, the toggles re-render, a segment hover
// shows the tooltip, Escape closes it. Prints one JSON line of observations; screenshots when asked.
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[2], shots = process.argv[3] || '';
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1280, height: 820 } });
  const errs = [];
  pg.on('pageerror', (e) => errs.push(String(e)));
  await pg.goto(url);
  await pg.waitForSelector('#rail-usage', { timeout: 20000 });
  await pg.evaluate(() => { const bt = document.getElementById('romp-boot'); if (bt) bt.remove(); });
  await pg.waitForFunction(() => document.getElementById('rail-usage').children.length > 0, null, { timeout: 20000 });
  // T247d: the desktop hover ends in the affordance line, and a screenshot of the tip
  await pg.hover('#rail-usage');
  await pg.waitForFunction(() => { const t = document.getElementById('ru-tip'); return t && t.style.display === 'block'; }, null, { timeout: 5000 });
  const hoverHint = await pg.evaluate(() => { const t = document.getElementById('ru-tip'); const last = t.lastElementChild; return { text: last ? last.textContent : '', cls: last ? last.className : '', font: last ? getComputedStyle(last).fontSize : '', opacity: last ? getComputedStyle(last).opacity : '' }; });
  if (shots) { const tipEl = await pg.$('#ru-tip'); if (tipEl) await tipEl.screenshot({ path: shots + '-hover.png' }); }
  await pg.mouse.move(5, 400);
  const hiddenBefore = await pg.evaluate(() => document.getElementById('rsp-back').hidden);
  await pg.evaluate(() => document.getElementById('rail-usage').click());
  await pg.waitForFunction(() => !document.getElementById('rsp-back').hidden, null, { timeout: 5000 });
  const loaderSeen = await pg.evaluate(() => !!document.querySelector('#rsp-panel .rsp-load, #rsp-panel .rsp-tbl'));
  await pg.waitForSelector('#rsp-panel .rsp-tbl', { timeout: 15000 });
  await pg.waitForSelector('#rsp-chart .rsp-svg', { timeout: 15000 });
  const out = await pg.evaluate(() => ({
    head: document.querySelector('#rsp-panel .rsp-top span').textContent,
    legendNodes: document.querySelectorAll('#rsp-chart .rsp-leg, #rsp-chart .rsp-chip').length,
    chartFirst: (() => { const secs = Array.from(document.querySelectorAll('#rsp-panel .rsp-sec')); const ci = secs.findIndex((s) => s.querySelector('#rsp-chart')); const ti = secs.findIndex((s) => s.querySelector('#rsp-table')); return ci >= 0 && ti > ci; })(),
    swatches: document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr .rsp-sw:not(.rsp-hatch)').length,
    title: (() => { const t = document.querySelector('#rsp-panel .rsp-tbl tbody tr .tab-label'); const cs = t ? getComputedStyle(t) : null; const hp = t ? t.querySelector('.host-prefix') : null; return { color: cs ? cs.color : null, weight: cs ? cs.fontWeight : null, prefix: hp ? hp.textContent : null }; })(),
    pane: (() => { const p = document.getElementById('rsp-table'); const th = document.querySelector('#rsp-panel .rsp-tbl thead th'); const panel = document.getElementById('rsp-panel'); return { scrolls: !!p && p.scrollHeight > p.clientHeight + 4, sticky: th ? getComputedStyle(th).position : null, h: p ? p.clientHeight : null, thOpacity: th ? getComputedStyle(th).opacity : null, panelScrolls: panel.scrollHeight > panel.clientHeight + 2 }; })(),
    rows: Array.from(document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr')).map((tr) => tr.textContent),
    deadRows: document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr.rsp-dead').length,
    segs: document.querySelectorAll('#rsp-chart .rsp-seg').length,
    hatched: document.querySelectorAll('#rsp-chart .rsp-seg[fill="url(#rsp-hatch)"]').length,
    backdrop: getComputedStyle(document.getElementById('rsp-back')).backgroundColor,
    windows: Array.from(document.querySelectorAll('#rsp-panel .ru-tip-fleetspend .ru-tip-row .ru-tip-k')).map((e) => e.textContent),
    notes: Array.from(document.querySelectorAll('#rsp-panel .rsp-note')).map((e) => e.textContent),
    xlabels: Array.from(document.querySelectorAll('#rsp-chart .ru-tip-gx span')).map((e) => e.textContent),
    ylabels: Array.from(document.querySelectorAll('#rsp-chart .ru-tip-gy')).map((e) => e.textContent),
    railOpacity: getComputedStyle(document.getElementById('rail-usage')).opacity,
  }));
  if (shots) { await pg.screenshot({ path: shots + '-dark.png' }); const ch = await pg.$('#rsp-chart'); if (ch) await ch.screenshot({ path: shots + '-chart.png' }); }
  // T293: the crosshair. Hover the chart clear of every bar (its top 2px: the tallest bar stops at the 6px pad) at a
  // fraction of its width and read the hairline, the stamp and the tooltip's rows; the marks must take no pointer
  // events and move nothing under the pointer
  const hBefore = await pg.evaluate(() => document.getElementById('rsp-chart').getBoundingClientRect().height);
  const week = await pg.evaluate(() => ({ label: document.querySelector('#rsp-panel [data-act="range:hours"]').textContent }));
  const xhProbe = async (fx, fy, scroll) => {
    const r = await pg.evaluate((scroll) => { const s = document.querySelector('#rsp-chart svg'); if (scroll === 'top') document.getElementById('rsp-panel').scrollTop = 0; else s.scrollIntoView({ block: 'center' }); const b = s.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; }, scroll || 'center');
    await pg.mouse.move(r.x + r.w * fx, r.y + r.h * fy);
    await pg.waitForTimeout(60);
    return pg.evaluate(() => {
      const line = document.querySelector('#rsp-chart .rsp-xh'), st = document.querySelector('#rsp-chart .rsp-xh-stamp'), t = document.getElementById('rsp-tip');
      const rows = t ? Array.from(t.querySelectorAll('.rsp-tip-row')).map((r) => ({ name: r.querySelector('span').textContent, v: r.querySelector('b').textContent, dot: r.querySelector('i').style.backgroundColor, on: r.classList.contains('on') })) : [];
      const more = t && t.querySelector('.rsp-tip-more'); const head = t && t.querySelector('.rsp-tip-h');
      return { lineShown: !!line && line.style.display !== 'none', x1: line ? line.getAttribute('x1') : null, stamp: st ? st.textContent : null, stampShown: !!st && st.style.display !== 'none',
        flip: !!st && st.classList.contains('flip'), lineInert: line ? getComputedStyle(line).pointerEvents : null, stampInert: st ? getComputedStyle(st).pointerEvents : null,
        stampFont: st ? getComputedStyle(st).fontSize : null, tipShown: !!t && t.style.display === 'block', tipInert: t ? getComputedStyle(t).pointerEvents : null,
        head: head ? head.textContent : null, rows, more: more ? more.textContent : null, chartH: document.getElementById('rsp-chart').getBoundingClientRect().height,
        stampLeft: st ? st.getBoundingClientRect().left : null, gyRight: (() => { const g = document.querySelector('#rsp-chart .ru-tip-gy'); return g ? g.getBoundingClientRect().right : null; })(),
        tipRect: t ? (() => { const b = t.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right }; })() : null,
        stampRect: st ? (() => { const b = st.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right }; })() : null };
    });
  };
  const xh = await xhProbe(0.69, 0.01);   // a recorded bucket: the fixture's ledger holds the last 60 hours of the 168
  if (shots) { await pg.screenshot({ path: shots + '-xh-7day-dark.png' }); await pg.evaluate(() => document.body.classList.add('theme-light')); await pg.waitForTimeout(120); await pg.screenshot({ path: shots + '-xh-7day-light.png' }); await pg.evaluate(() => document.body.classList.remove('theme-light')); }
  const xhRight = await xhProbe(0.9, 0.01);   // another bucket: the line follows; near the right edge the stamp flips to its left
  xh.movedX1 = xhRight.x1; xh.flip = xhRight.flip;
  const xhLeft = await xhProbe(0.005, 0.01);   // the first bucket: unflipped, and the stamp starts past the ceiling label
  xh.flipLeft = xhLeft.flip; xh.stampLeft = xhLeft.stampLeft; xh.gyRight = xhLeft.gyRight;
  await pg.mouse.move(5, 400); await pg.waitForTimeout(60);
  const xhAfter = await pg.evaluate(() => { const line = document.querySelector('#rsp-chart .rsp-xh'), st = document.querySelector('#rsp-chart .rsp-xh-stamp'), t = document.getElementById('rsp-tip'); return { line: line ? line.style.display : null, stamp: st ? st.style.display : null, tip: t ? t.style.display : null }; });
  // the 1-day view: an hourly stamp too; then back to the 7-day view the rest of the run expects
  await pg.click('#rsp-panel [data-act="range:day"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="range:day"]').classList.contains('on'), null, { timeout: 5000 });
  const xhDay = await xhProbe(0.5, 0.01);
  if (shots) { await pg.screenshot({ path: shots + '-xh-1day-dark.png' }); await pg.evaluate(() => document.body.classList.add('theme-light')); await pg.waitForTimeout(120); await pg.screenshot({ path: shots + '-xh-1day-light.png' }); await pg.evaluate(() => document.body.classList.remove('theme-light')); }
  await pg.setViewportSize({ width: 1200, height: 820 }); await pg.waitForTimeout(250);   // a rebuild under a still pointer takes the tooltip down with the svg
  const afterResize = await pg.evaluate(() => { const t = document.getElementById('rsp-tip'), line = document.querySelector('#rsp-chart .rsp-xh'), st = document.querySelector('#rsp-chart .rsp-xh-stamp'); return { tip: t ? t.style.display : null, line: line ? line.style.display : null, stamp: st ? st.style.display : null }; });
  await pg.setViewportSize({ width: 1280, height: 820 }); await pg.waitForTimeout(250);
  await pg.mouse.move(5, 400);
  await pg.click('#rsp-panel [data-act="range:hours"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="range:hours"]').classList.contains('on'), null, { timeout: 5000 });
  // T247f: "your order"
  await pg.click('#rsp-panel [data-act="order:yours"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="order:yours"]').classList.contains('on'), null, { timeout: 5000 });
  const yourOrder = await pg.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr')).map((tr) => tr.textContent.trim());
    // the bottom stack of the first bucket that has one: the path whose top edge is lowest among those sharing the first x
    const segs = Array.from(document.querySelectorAll('#rsp-chart .rsp-seg'));
    const byX = new Map(); segs.forEach((p) => { const x = p.getAttribute('d').split(/[ ,]/)[0].slice(1); if (!byX.has(x)) byX.set(x, []); byX.get(x).push(p); });
    let bottomFill = null; for (const [, ps] of byX) { const withY = ps.map((p) => ({ p, y1: parseFloat(p.getAttribute('d').split(/[ ,]/)[1]) })); withY.sort((a, b) => b.y1 - a.y1); if (withY.length > 1) { bottomFill = withY[0].p.getAttribute('fill'); break; } }
    const prefs = JSON.parse(localStorage.getItem('romp:spendModal') || '{}');
    return { rows, bottomFill, prefs, pressed: document.querySelector('#rsp-panel [data-act="order:yours"]').classList.contains('on') };
  });
  if (shots) await pg.screenshot({ path: shots + '-yourorder.png' });
  // a viewer arrangement (the strip's key) puts web first
  await pg.evaluate(() => { const rows = Array.from(document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr')); const web = rows.find((tr) => tr.textContent.includes('TESTHOST:web')); const sid = web && web.getAttribute('data-sid'); localStorage.setItem('romp:vieworder', JSON.stringify(sid ? [sid] : [])); });
  await pg.click('#rsp-panel [data-act="order:spend"]'); await pg.click('#rsp-panel [data-act="order:yours"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="order:yours"]').classList.contains('on'), null, { timeout: 5000 });
  yourOrder.viewRows = await pg.evaluate(() => Array.from(document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr')).map((tr) => tr.textContent.trim()));
  await pg.evaluate(() => { localStorage.removeItem('romp:vieworder'); });
  // leave "your order" pressed: the phone reload below must read it back (the chip pressed on open, the rows ordered)
  await pg.click('#rsp-panel [data-act="measure:tok"]');
  await pg.click('#rsp-panel [data-act="range:days"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="range:days"]').classList.contains('on')
    && document.querySelector('#rsp-panel [data-act="measure:tok"]').classList.contains('on'), null, { timeout: 5000 });
  const days = await pg.evaluate(() => ({
    // T353: the session list follows the chart's range, so the FULL list (every session with spend in 90 days) is
    // read here, with its pane's scroll: the seven days shown at open list only the sessions with hourly spend
    rows: Array.from(document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr')).map((tr) => tr.textContent),
    deadRows: document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr.rsp-dead').length,
    pane: (() => { const p = document.getElementById('rsp-table'); const panel = document.getElementById('rsp-panel'); return { scrolls: !!p && p.scrollHeight > p.clientHeight + 4, h: p ? p.clientHeight : null, panelScrolls: panel.scrollHeight > panel.clientHeight + 2 }; })(),
    segs: document.querySelectorAll('#rsp-chart .rsp-seg').length,
    ylabels: Array.from(document.querySelectorAll('#rsp-chart .ru-tip-gy')).map((e) => e.textContent),
    xlabels: Array.from(document.querySelectorAll('#rsp-chart .ru-tip-gx span')).map((e) => e.textContent),
  }));
  const xhDays = await xhProbe(0.9, 0.01);   // T293: the daily view's stamp is a date; dozens of sessions fold into one line
  await pg.mouse.move(5, 400); await pg.waitForTimeout(60);
  // a short window with the card unscrolled: no room below the pointer, so the box goes above the CHART, clear of the stamp (review find)
  await pg.setViewportSize({ width: 1280, height: 600 }); await pg.waitForTimeout(250);
  const xhShort = await xhProbe(0.9, 0.85, 'top');
  await pg.mouse.move(5, 5); await pg.waitForTimeout(60);
  await pg.setViewportSize({ width: 1280, height: 820 }); await pg.waitForTimeout(250);
  // hover the TALLEST segment (a sliver has no reliable hit box); measured in the page, scrolled into view
  const bb = await pg.evaluate(() => {
    let best = null;
    document.querySelectorAll('#rsp-chart .rsp-seg').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (!best || r.height > best.height) best = { x: r.x, y: r.y, width: r.width, height: r.height, d: el.getAttribute('d') };
    });
    if (best) { const svg = document.querySelector('#rsp-chart svg'); svg.scrollIntoView({ block: 'center' }); const r2 = document.querySelector('#rsp-chart .rsp-seg'); }
    return best;
  });
  if (!bb || !(bb.height > 0)) throw new Error('no measurable segment: ' + JSON.stringify(bb));
  const bb2 = await pg.evaluate((d) => { const el = Array.from(document.querySelectorAll('#rsp-chart .rsp-seg')).find((e) => e.getAttribute('d') === d); const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; }, bb.d);
  await pg.mouse.move(bb2.x + bb2.width / 2, bb2.y + bb2.height / 2);
  await pg.waitForFunction(() => { const t = document.getElementById('rsp-tip'); return t && t.style.display === 'block'; }, null, { timeout: 5000 });
  const tip = await pg.evaluate(() => document.getElementById('rsp-tip').textContent);
  const tipOn = await pg.evaluate(() => { const on = document.querySelector('#rsp-tip .rsp-tip-row.on span'); const line = document.querySelector('#rsp-chart .rsp-xh'); return { name: on ? on.textContent : null, lineShown: !!line && line.style.display !== 'none' }; });
  if (shots) await pg.screenshot({ path: shots + '-days-tokens.png' });
  await pg.mouse.move(5, 5);
  if (shots) {
    await pg.click('#rsp-panel [data-act="measure:usd"]');
    await pg.click('#rsp-panel [data-act="range:hours"]');
    await pg.evaluate(() => document.body.classList.add('theme-light'));
    await pg.waitForTimeout(150);
    await pg.screenshot({ path: shots + '-light.png' });
    await pg.evaluate(() => document.body.classList.remove('theme-light'));
  }
  await pg.keyboard.press('Escape');
  const hiddenAfter = await pg.evaluate(() => document.getElementById('rsp-back').hidden);
  // reopen by click, close by backdrop tap
  await pg.evaluate(() => document.getElementById('rail-usage').click());
  await pg.waitForFunction(() => !document.getElementById('rsp-back').hidden, null, { timeout: 5000 });
  await pg.mouse.click(8, 8);
  const hiddenAfterTap = await pg.evaluate(() => document.getElementById('rsp-back').hidden);
  // a drag-select from a table cell that ends over the backdrop is not a tap
  await pg.evaluate(() => document.getElementById('rail-usage').click());
  await pg.waitForFunction(() => !document.getElementById('rsp-back').hidden && document.querySelector('#rsp-panel .rsp-tbl td.n'), null, { timeout: 5000 });
  const cell = await (await pg.$('#rsp-panel .rsp-tbl td.n')).boundingBox();
  await pg.mouse.move(cell.x + 2, cell.y + cell.height / 2); await pg.mouse.down(); await pg.mouse.move(5, 5, { steps: 4 }); await pg.mouse.up();
  const hiddenAfterDrag = await pg.evaluate(() => document.getElementById('rsp-back').hidden);
  // the light theme's PRESSED toggle: accent chip, --accent-fg text, no hairline (T247b)
  const lightBtn = await pg.evaluate(() => {
    document.body.classList.add('theme-light');
    const b = document.querySelector('#rsp-panel .rsp-btn.on'); const cs = getComputedStyle(b);
    const out = { color: cs.color, border: cs.borderTopColor, bg: cs.backgroundColor };
    document.body.classList.remove('theme-light'); return out;
  });
  // dim once: a dead row's annotation sits at the row's level, and the fold row's button is a control
  const dim = await pg.evaluate(() => {
    const dead = document.querySelector('#rsp-panel tr.rsp-dead td.rsp-name');
    const ann = dead && dead.querySelector('.ru-tip-reset');
    return { row: dead ? getComputedStyle(dead).opacity : null, ann: ann ? getComputedStyle(ann).opacity : null };
  });
  // the loader's backstop: a fetch that never answers ends on the error + retry path (T247b)
  const timeout = await pg.evaluate(async () => {
    const f = window.fetch; window.__rompSpendTimeoutMs = 1000;
    window.fetch = (u, o) => new Promise((res, rej) => { if (o && o.signal) o.signal.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError'))); });
    try {
      // a re-open while the first fetch is pending: the superseded fetch's abort must NOT paint an error
      window.__rompOpenSpend(); window.__rompOpenSpend(); await new Promise((r) => setTimeout(r, 150));
      const early = { err: !!document.querySelector('#rsp-panel .rsp-err'), loader: !!document.querySelector('#rsp-panel .rsp-load') };
      await new Promise((r) => setTimeout(r, 1400));
      const err = document.querySelector('#rsp-panel .rsp-err'); return { early, err: err ? err.textContent : null, retry: !!document.querySelector('#rsp-panel [data-act="retry"]') }; }
    finally { window.fetch = f; delete window.__rompSpendTimeoutMs; }
  });
  await pg.keyboard.press('Escape');
  await pg.evaluate(() => document.getElementById('rail-usage').click());
  await pg.waitForSelector('#rsp-panel .rsp-tbl', { timeout: 10000 });
  // T247g: merge by tag, and the 1-day range — under "by spend", so the merged rows sort by dollars
  await pg.click('#rsp-panel [data-act="order:spend"]');
  await pg.click('#rsp-panel [data-act="merge:toggle"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="merge:toggle"]').classList.contains('on'), null, { timeout: 5000 });
  const merge = await pg.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('#rsp-panel .rsp-tbl tbody tr')).map((tr) => tr.textContent.trim());
    const tagRow = document.querySelector('#rsp-panel .rsp-tbl tbody tr[data-tag] .rsp-tag-chip');
    return { rows, tagColor: tagRow ? getComputedStyle(tagRow).color : null, tagWeight: tagRow ? getComputedStyle(tagRow).fontWeight : null,
      tagBorder: tagRow ? getComputedStyle(tagRow).borderTopColor : null,
      notes: Array.from(document.querySelectorAll('#rsp-panel .rsp-note')).map((e) => e.textContent),
      stackNames: (() => { const names = new Set(); document.querySelectorAll('#rsp-chart .rsp-seg').forEach((p) => { names.add(p.getAttribute('data-s')); }); return Array.from(names).map((i) => (window.__rompSpendStackNames || [])[+i] || ''); })(),
      prefs: JSON.parse(localStorage.getItem('romp:spendModal') || '{}') };
  });
  // stack names ride the tooltip: hover the tallest segment of a tag stack is fiddly, so read them off the tooltip text per distinct stack index
  merge.stackNames = await pg.evaluate(async () => {
    const segs = Array.from(document.querAllSafe ? [] : document.querySelectorAll('#rsp-chart .rsp-seg'));
    const byIdx = new Map(); segs.forEach((p) => { const i = p.getAttribute('data-s'); if (!byIdx.has(i)) byIdx.set(i, p); });
    const names = [];
    for (const [, p] of byIdx) { const r = p.getBoundingClientRect(); if (r.height < 1) continue;
      p.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }));
      const t = document.getElementById('rsp-tip'); if (t && t.style.display === 'block') { const on = t.querySelector('.rsp-tip-row.on span'); names.push(on ? on.textContent.trim() : ''); } }
    const t = document.getElementById('rsp-tip'); if (t) t.style.display = 'none';
    return names;
  });
  if (shots) { await pg.screenshot({ path: shots + '-merged.png' }); await pg.evaluate(() => document.body.classList.add('theme-light')); await pg.waitForTimeout(120); await pg.screenshot({ path: shots + '-merged-light.png' }); await pg.evaluate(() => document.body.classList.remove('theme-light')); }
  await pg.click('#rsp-panel [data-act="range:day"]');
  await pg.waitForFunction(() => document.querySelector('#rsp-panel [data-act="range:day"]').classList.contains('on'), null, { timeout: 5000 });
  const day = await pg.evaluate(() => { const xs = new Set(); document.querySelectorAll('#rsp-chart .rsp-seg').forEach((p) => xs.add(p.getAttribute('d').split(/[ ,]/)[0])); return { buckets: xs.size, labels: Array.from(document.querySelectorAll('#rsp-chart .ru-tip-gx span')).map((e) => e.textContent) }; });
  merge.dayBuckets = day.buckets; merge.dayLabels = day.labels;
  await pg.click('#rsp-panel [data-act="range:hours"]'); await pg.click('#rsp-panel [data-act="merge:toggle"]');
  await pg.waitForFunction(() => !document.querySelector('#rsp-panel [data-act="merge:toggle"]').classList.contains('on'), null, { timeout: 5000 });
  await pg.click('#rsp-panel [data-act="order:yours"]');   // back to the persisted "your order" the phone reload reads
  // the light theme's error line, over a failed fetch
  const lightErr = await pg.evaluate(async () => {
    document.body.classList.add('theme-light');
    const f = window.fetch; window.fetch = () => Promise.reject(new Error('boom'));
    try { window.__rompOpenSpend(); await new Promise((r) => setTimeout(r, 200)); return getComputedStyle(document.querySelector('#rsp-panel .rsp-err')).color; }
    finally { window.fetch = f; document.body.classList.remove('theme-light'); }
  });
  await pg.keyboard.press('Escape');
  // the phone: no rail; the Usage panel's button is the door
  await pg.setViewportSize({ width: 390, height: 844 });
  await pg.reload(); await pg.waitForSelector('#mtabs [data-act="usage"]', { timeout: 20000 });
  await pg.evaluate(() => { const bt = document.getElementById('romp-boot'); if (bt) bt.remove(); });
  const railHidden = await pg.evaluate(() => getComputedStyle(document.querySelector('.pane-rail')).display === 'none');
  await pg.evaluate(() => window.__rompUsagePanel());
  await pg.waitForFunction(() => !!document.getElementById('ru-bysession') && document.getElementById('ru-back').classList.contains('on'), null, { timeout: 10000 });
  const btn = await pg.evaluate(() => { const bs = document.getElementById('ru-bysession');
    return { font: getComputedStyle(bs).fontSize, opacity: getComputedStyle(bs.parentNode).opacity, inAge: !!bs.closest('.ru-tip-age') }; });
  if (shots) await pg.screenshot({ path: shots + '-phone-panel.png' });
  await pg.click('#ru-bysession');
  await pg.waitForFunction(() => !document.getElementById('rsp-back').hidden && !document.getElementById('ru-back').classList.contains('on'), null, { timeout: 5000 });
  await pg.waitForSelector('#rsp-panel .rsp-tbl', { timeout: 10000 });
  if (shots) await pg.screenshot({ path: shots + '-phone.png' });
  const panelHint = await pg.evaluate(() => document.getElementById('ru-tip').textContent.includes('Click for the full breakdown'));
  const persisted = await pg.evaluate(() => ({ pressed: document.querySelector('#rsp-panel [data-act="order:yours"]').classList.contains('on'),
    first: (document.querySelector('#rsp-panel .rsp-tbl tbody tr') || {}).textContent || '' }));
  await pg.click('#rsp-panel [data-act="order:spend"]');   // restore the default for whoever runs next
  const mobile = { railHidden, panelOpened: true, modalOpened: true, btn, panelHint , persisted };
  console.log(JSON.stringify({ merge, yourOrder, hoverHint, hiddenBefore, loaderSeen, out, days, tip, tipOn, xh, xhAfter, xhDay, xhDays, xhShort, afterResize, week, hBefore, hiddenAfter, hiddenAfterTap, hiddenAfterDrag, lightErr, lightBtn, dim, timeout, mobile, errs }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
