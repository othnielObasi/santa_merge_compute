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
    $('merge').disabled = !P || (!files.length && !bestText);
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
  $('puzFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return; e.target.value = '';
    try {
      let bytes = await readBytes(f), text;
      if (f.name.toLowerCase().endsWith('.zip')) {
        const z = window.fflate.unzipSync(bytes, { filter: x => /(^|\/)puzzles\.csv$/i.test(x.name) });
        const k = Object.keys(z)[0]; if (!k) throw new Error('no puzzles.csv inside this zip'); bytes = z[k];
      }
      text = new TextDecoder().decode(bytes);
      P = C.loadPuzzles(text); await set('puzzles', text);
      computeBestTotal(); refresh();
    } catch (err) { alert(err.message); }
  };
  $('bestFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return; e.target.value = '';
    const text = new TextDecoder().decode(await readBytes(f));
    if (!Object.keys(C.programsFromCSV(text)).length) return alert('No id,program rows in that file.');
    bestText = text; await set('best', text); computeBestTotal(); refresh();
  };
  $('bestDownload').onclick = () => bestText && download('best.csv', bestText);
  $('bestForget').onclick = async () => { if (!confirm('Forget the best file stored in this browser?')) return;
    bestText = null; bestTotal = null; await set('best', undefined); refresh(); };

  function addFiles(list) {
    for (const f of list) if (/\.(zip|csv|ipynb)$/i.test(f.name) && !files.some(x => x.name === f.name && x.size === f.size)) files.push(f);
    refresh();
  }
  const drop = $('drop');
  drop.onclick = () => $('addFiles').click();
  drop.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') $('addFiles').click(); };
  $('addFiles').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); addFiles(e.dataTransfer.files); };

  // ---------- merge
  $('merge').onclick = async () => {
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
      if (improved) { bestText = lastCSV; bestTotal = total; await set('best', bestText); }

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
  })();
})();
