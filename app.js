/* Santa 2026 merge - page logic. All processing is local (see core.js). */
(function () {
  'use strict';
  const C = window.SantaCore;
  const $ = id => document.getElementById(id);
  const fmt = n => n.toLocaleString('en-GB');

  // ---------- tiny IndexedDB store (falls back to memory if unavailable)
  const mem = {};
  function db() {
    return new Promise((res, rej) => {
      try {
        const r = indexedDB.open('santa-merge', 1);
        r.onupgradeneeded = () => r.result.createObjectStore('kv');
        r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
      } catch (e) { rej(e); }
    });
  }
  async function get(k) {
    try { const d = await db(); return await new Promise((res, rej) => {
      const q = d.transaction('kv').objectStore('kv').get(k); q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error); });
    } catch (e) { return mem[k]; }
  }
  async function set(k, v) {
    mem[k] = v;
    try { const d = await db(); await new Promise((res, rej) => {
      const t = d.transaction('kv', 'readwrite'); v === undefined ? t.objectStore('kv').delete(k) : t.objectStore('kv').put(v, k);
      t.oncomplete = res; t.onerror = () => rej(t.error); });
    } catch (e) { /* memory only */ }
  }

  // ---------- state
  let P = null, bestText = null, bestTotal = null, files = [], lastCSV = null;
  const tick = () => new Promise(r => setTimeout(r, 0));
  const readBytes = f => f.arrayBuffer().then(b => new Uint8Array(b));

  function download(name, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
    const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }

  function refresh() {
    $('puzStatus').textContent = P ? `loaded · ${P.size} puzzles` : 'not loaded';
    $('puzStatus').className = 'status' + (P ? ' ok' : '');
    $('bestStatus').textContent = bestTotal ? `${fmt(bestTotal)} · all ${P ? P.size : ''} programs valid` : (bestText ? 'loaded (needs puzzles.csv to check)' : 'none yet');
    $('bestStatus').className = 'status' + (bestTotal ? ' ok' : '');
    $('bestTop').textContent = bestTotal ? fmt(bestTotal) : '–';
    $('bestDownload').disabled = !bestText; $('bestForget').disabled = !bestText;
    $('needPuz').classList.toggle('hidden', !!P);
    const ul = $('fileList'); ul.innerHTML = '';
    files.forEach((f, i) => {
      const li = document.createElement('li');
      const s = document.createElement('span'); s.textContent = `${f.name}  (${(f.size / 1024 / 1024).toFixed(1)} MB)`;
      const b = document.createElement('button'); b.textContent = 'remove'; b.onclick = () => { files.splice(i, 1); refresh(); };
      li.append(s, b); ul.append(li);
    });
  }

  function computeBestTotal() {
    bestTotal = null;
    if (!P || !bestText) return;
    const b = C.startBest(P, bestText);
    if (b.size === P.size) bestTotal = C.total(b);
  }

  // ---------- inputs
  // puzzles.csv from a csv or from inside a zip (e.g. the competition data zip); returns true if found
  async function tryPuzzles(f, quiet) {
    try {
      let bytes = await readBytes(f);
      if (f.name.toLowerCase().endsWith('.zip')) {
        const z = window.fflate.unzipSync(bytes, { filter: x => /(^|\/)puzzles\.csv$/i.test(x.name) });
        const k = Object.keys(z)[0]; if (!k) { if (!quiet) alert('No puzzles.csv inside this zip.'); return false; }
        bytes = z[k];
      } else if (!/(^|\/)puzzles[^/]*\.csv$/i.test(f.name) && quiet) return false;
      const text = new TextDecoder().decode(bytes);
      P = C.loadPuzzles(text); await set('puzzles', text);
      computeBestTotal(); refresh(); return true;
    } catch (err) { if (!quiet) alert(err.message); return false; }
  }
  $('puzFile').onchange = async e => { const f = e.target.files[0]; if (!f) return; e.target.value = ''; await tryPuzzles(f, false); };
  $('bestFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return; e.target.value = '';
    const text = new TextDecoder().decode(await readBytes(f));
    if (!Object.keys(C.programsFromCSV(text)).length) return alert('No id,program rows in that file.');
    bestText = text; await set('best', text); computeBestTotal(); refresh();
    if (bestTotal) await record(bestTotal, 'loaded ' + f.name);
  };
  $('bestDownload').onclick = () => bestText && download('best.csv', bestText);
  $('bestForget').onclick = async () => { if (!confirm('Forget the best file stored in this browser?')) return;
    bestText = null; bestTotal = null; await set('best', undefined); refresh(); };

  async function addFiles(list) {
    for (const f of list) {
      if (!/\.(zip|csv|ipynb)$/i.test(f.name)) continue;
      if (/(^|\/)puzzles[^/]*\.csv$/i.test(f.name)) { await tryPuzzles(f, false); continue; }
      if (!P && /\.zip$/i.test(f.name) && /santa|data/i.test(f.name) && await tryPuzzles(f, true)) continue;
      if (!files.some(x => x.name === f.name && x.size === f.size)) files.push(f);
    }
    refresh();
  }
  const drop = $('drop');
  drop.onclick = () => $('addFiles').click();
  drop.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') $('addFiles').click(); };
  $('addFiles').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); addFiles(e.dataTransfer.files); };


  // ---------- score progress (history of every new best)
  let history = [], range = 'recent';
  const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const stamp = () => { const d = new Date(); const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
  async function record(total, note) {
    const last = history.length ? history[history.length - 1].total : null;
    if (last !== null && total >= last) return;
    history.push({ time: stamp(), total, note: note || '' });
    await set('history', history); drawHistory();
  }
  function drawHistory() {
    const box = $('chart');
    $('histStatus').textContent = history.length ? `${history.length} points · ${fmt(history[0].total)} → ${fmt(history[history.length - 1].total)}` : 'no history yet';
    $('histStatus').className = 'status' + (history.length ? ' ok' : '');
    $('histDownload').disabled = !history.length;
    if (!history.length) { box.innerHTML = '<div class="empty">Your first merge starts the graph.</div>'; $('histTable').classList.add('hidden'); return; }
    const shown = range === 'recent' ? history.slice(-20) : history;
    $('rngAll').className = range === 'all' ? 'on' : ''; $('rngRecent').className = range === 'recent' ? 'on' : '';
    const W = 380, H = 230, L = 58, R = 10, T = 22, B = 26;
    const pts = shown.map(h => ({ t: Date.parse(h.time.replace(' ', 'T')) || 0, v: h.total }));
    if (pts.length === 1) pts.push({ t: pts[0].t + 60000, v: pts[0].v });
    const t0 = pts[0].t, t1 = Math.max(pts[pts.length - 1].t, t0 + 60000);
    let lo = Math.min(...pts.map(p => p.v)), hi = Math.max(...pts.map(p => p.v));
    const pad = Math.max(3, (hi - lo) * 0.08); lo -= pad; hi += pad;
    const X = t => L + (W - L - R) * (t - t0) / (t1 - t0), Y = v => T + (H - T - B) * (hi - v) / (hi - lo);
    let d = ''; pts.forEach((p, i) => { d += i ? ` L${X(p.t).toFixed(1)},${Y(pts[i - 1].v).toFixed(1)} L${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}` : `M${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`; });
    let g = '';
    for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4, y = Y(v).toFixed(1);
      g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/><text class="axis" x="${L - 6}" y="${(+y + 4).toFixed(1)}" text-anchor="end">${fmt(Math.round(v))}</text>`; }
    const lab = t => new Date(t).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    g += `<text class="axis" x="${L}" y="${H - 8}">${esc(lab(t0))}</text><text class="axis" x="${W - R}" y="${H - 8}" text-anchor="end">${esc(lab(t1))}</text>`;
    const lp = pts[pts.length - 1];
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="total instructions over time, lower is better">${g}<path class="line" d="${d}"/>` +
      shown.map(h => `<circle class="dot" cx="${X(Date.parse(h.time.replace(' ', 'T')) || t0).toFixed(1)}" cy="${Y(h.total).toFixed(1)}" r="3"/>`).join('') +
      `<text class="last" x="${Math.min(X(lp.t), W - R).toFixed(1)}" y="${(Y(lp.v) - 9).toFixed(1)}" text-anchor="end">${fmt(lp.v)}</text></svg>`;
    const tb = $('histRows'); tb.innerHTML = '';
    history.slice(-12).reverse().forEach((h, i, arr) => {
      const prev = history[history.length - 1 - i - 1];
      const tr = document.createElement('tr');
      [h.time.slice(5).replace('-', '/'), fmt(h.total), prev ? '−' + fmt(prev.total - h.total) : '', h.note].forEach((v, j) => {
        const td = document.createElement('td'); td.textContent = v; if (j === 1 || j === 2) td.className = 'n' + (j === 2 && v ? ' gain' : ''); tr.append(td); });
      tb.append(tr);
    });
    $('histTable').classList.remove('hidden');
  }
  $('rngAll').onclick = () => { range = 'all'; drawHistory(); };
  $('rngRecent').onclick = () => { range = 'recent'; drawHistory(); };
  $('histFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return; e.target.value = '';
    const rows = C.parseCSV(new TextDecoder().decode(await readBytes(f)));
    const head = (rows[0] || []).map(x => x.trim());
    const it = head.indexOf('time'), is = head.indexOf('score') >= 0 ? head.indexOf('score') : head.indexOf('total'), inote = head.indexOf('note');
    if (it < 0 || is < 0) return alert('Expected columns time and score (like state/history.csv).');
    const merged = history.concat(rows.slice(1).map(r => ({ time: r[it], total: Number(r[is]), note: (inote >= 0 ? r[inote] : '') || 'imported' })).filter(h => h.time && h.total));
    merged.sort((a, b) => a.time.localeCompare(b.time));
    history = []; for (const h of merged) if (!history.length || h.total < history[history.length - 1].total) history.push(h);
    await set('history', history); drawHistory();
  };
  $('histDownload').onclick = () => download('history.csv', 'time,score,note\r\n' +
    history.map(h => `${h.time},${h.total},"${String(h.note).replace(/"/g, '""')}"`).join('\r\n') + '\r\n');

  // ---------- merge
  $('merge').onclick = async () => {
    if (!P) { const s1 = $('step1'); s1.scrollIntoView({ behavior: 'smooth', block: 'center' });
      s1.classList.remove('flash'); void s1.offsetWidth; s1.classList.add('flash');
      $('log').textContent = 'Load puzzles.csv first (step 1) - one time only.'; return; }
    if (!files.length && !bestText) { $('log').textContent = 'Add some files in step 3 first.'; return; }
    $('merge').disabled = true; $('result').classList.add('hidden');
    const log = t => { $('log').textContent = t; };
    try {
      const best = C.startBest(P, bestText);
      const before = best.size === P.size ? C.total(best) : null;
      const rows = [];
      for (const f of files) {
        log('reading ' + f.name + ' …'); await tick();
        const sources = C.readFile(f.name, await readBytes(f));
        if (!sources.length) rows.push({ name: f.name, ok: 0, bad: 0, gain: 0, fileTotal: null, note: 'nothing to read' });
        for (const s of sources) {
          log('checking ' + s.name + ' …'); await tick();
          const r = C.mergeSource(P, best, s);
          if (!Object.keys(s.progs).length) r.note = r.note || 'no programs found';
          rows.push(r);
        }
      }
      const missing = [...P.keys()].filter(k => !best.has(k));
      if (missing.length) throw new Error(`Only ${P.size - missing.length} of ${P.size} puzzles have a valid program. Add a full submission file (or load your best in step 2).`);
      log('final check …'); await tick();
      for (const [k, prog] of best) if (!C.check(P.get(k), prog)) throw new Error('internal check failed on ' + k);
      const total = C.total(best);
      lastCSV = C.toCSV(P, best);
      const improved = before === null || total < before;
      if (improved) { bestText = lastCSV; bestTotal = total; await set('best', bestText);
        await record(total, files.map(f => f.name).slice(0, 3).join(', ') + (files.length > 3 ? ' …' : '')); }

      $('total').textContent = fmt(total);
      $('delta').textContent = before !== null && total < before ? `−${fmt(before - total)}` : '';
      $('verdict').textContent = before === null ? 'All programs replay correctly. Saved as your best.'
        : improved ? `Better than your best (${fmt(before)}). Saved as your new best.` : `No improvement over your best (${fmt(before)}).`;
      const tb = $('rows'); tb.innerHTML = '';
      for (const r of rows) {
        const tr = document.createElement('tr');
        const td = (t, cls) => { const c = document.createElement('td'); c.textContent = t; if (cls) c.className = cls; tr.append(c); };
        td(r.name + (r.note ? ` - ${r.note}` : ''), 'name');
        td(r.ok + (r.bad ? ` (${r.bad} invalid)` : ''), 'n' + (r.bad ? ' bad' : ''));
        td(r.fileTotal ? fmt(r.fileTotal) : '', 'n');
        td(r.gain ? '−' + fmt(r.gain) : '', 'n' + (r.gain ? ' gain' : ''));
        tb.append(tr);
      }
      $('result').classList.remove('hidden');
      log(''); refresh();
    } catch (err) { log(''); alert(err.message); }
    $('merge').disabled = false;
  };
  $('download').onclick = () => lastCSV && download('submission.csv', lastCSV);

  // ---------- restore saved state
  (async () => {
    const pt = await get('puzzles'); if (pt) { try { P = C.loadPuzzles(pt); } catch (e) { /* ignore */ } }
    bestText = (await get('best')) || null; computeBestTotal(); refresh();
    history = (await get('history')) || []; drawHistory();
  })();
})();
