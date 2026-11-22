/* Santa 2026 merge core - runs in the browser (and in Node for tests).
   Reads puzzles.csv, programs from CSVs / zips / notebooks, replays every program under the
   competition rules with BigInt arithmetic, and keeps the shortest valid program per puzzle.
   Notebook code is never executed: programs are read out of the notebook text as data. */
(function (root) {
  'use strict';
  const fflate = root.fflate || (typeof require !== 'undefined' ? require('fflate') : null);
  const SKIP = new Set(['puzzles.csv', 'practice_puzzles.csv', 'sample_submission.csv']);

  // ---------- CSV (RFC 4180: quoted fields, "" escapes, \n or \r\n)
  function parseCSV(text) {
    const rows = []; let row = [], field = '', i = 0, q = false;
    const n = text.length;
    if (text.charCodeAt(0) === 0xfeff) i = 1;
    for (; i < n; i++) {
      const c = text[i];
      if (q) {
        if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
        else field += c;
      } else if (c === '"') q = true;
      else if (c === ',') { row.push(field); field = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(field); field = ''; rows.push(row); row = [];
      } else field += c;
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    return rows.filter(r => r.length > 1 || (r.length === 1 && r[0] !== ''));
  }
  function csvRecords(text) {
    const rows = parseCSV(text); if (!rows.length) return { head: [], recs: [] };
    const head = rows[0].map(h => h.trim());
    return { head, recs: rows.slice(1).map(r => Object.fromEntries(head.map((h, k) => [h, r[k]]))) };
  }
  const bigs = s => (String(s).match(/\d+/g) || []).map(BigInt);

  // ---------- puzzles + rules
  function loadPuzzles(text) {
    const { head, recs } = csvRecords(text);
    for (const h of ['id', 'starting_values', 'targets', 'value_limit', 'registers'])
      if (!head.includes(h)) throw new Error('this is not puzzles.csv (missing column ' + h + ')');
    const P = new Map();
    for (const r of recs) P.set(r.id, { start: bigs(r.starting_values), targets: bigs(r.targets),
      limit: BigInt(r.value_limit), regs: Number(r.registers) });
    return P;
  }
  function check(p, prog) {          // length of a valid program, or 0
    if (!Array.isArray(prog) || prog.length === 0 || prog.length > 4096) return 0;
    if (JSON.stringify(prog).length > 131072) return 0;
    const reg = new Array(p.regs).fill(null); p.start.forEach((v, k) => { reg[k] = v; });
    const seen = new Set(p.start.map(String));
    for (const ins of prog) {
      if (!Array.isArray(ins) || ins.length !== 4) return 0;
      const [op, a, b, d] = ins;
      for (const x of [a, b, d]) if (!Number.isInteger(x) || x < 0 || x >= p.regs) return 0;
      const x = reg[a], y = reg[b];
      if (x === null || y === null) return 0;
      let v;
      if (op === '+') v = x + y; else if (op === '-') v = x - y; else if (op === '*') v = x * y;
      else if (op === '/') { if (y === 0n || x % y !== 0n) return 0; v = x / y; }
      else return 0;
      if (v <= 0n || v > p.limit) return 0;
      reg[d] = v; seen.add(String(v));
    }
    for (const t of p.targets) if (!seen.has(String(t))) return 0;
    return prog.length;
  }

  // ---------- readers
  function programsFromCSV(text) {
    const { head, recs } = csvRecords(text);
    if (!head.includes('id') || !head.includes('program')) return {};
    const out = {};
    for (const r of recs) { try { out[r.id] = JSON.parse(r.program); } catch (e) { /* skip */ } }
    return out;
  }
  function b64bytes(s) {
    const bin = atobAny(s.replace(/\s+/g, '')); const u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u;
  }
  function atobAny(s) { return typeof atob === 'function' ? atob(s) : Buffer.from(s, 'base64').toString('binary'); }
  const utf8 = u => new TextDecoder().decode(u);
  function programsFromNotebook(text) {
    let nb; try { nb = JSON.parse(text); } catch (e) { return {}; }
    const src = (nb.cells || []).filter(c => c.cell_type === 'code')
      .map(c => Array.isArray(c.source) ? c.source.join('') : (c.source || '')).join('\n');
    const found = {};
    for (const m of src.matchAll(/GIFT\s*=\s*'''([\s\S]*?)'''/g)) {            // Pardheev
      try { Object.assign(found, JSON.parse(utf8(fflate.unzlibSync(b64bytes(m[1]))))); } catch (e) { /* skip */ }
    }
    for (const m of src.matchAll(/^CHANGE\s*=\s*(\{.*\})\s*$/gm)) {             // Asterios
      try { Object.assign(found, JSON.parse(m[1])); } catch (e) { /* skip */ }
    }
    for (const m of src.matchAll(/(?:'''|""")\s*([A-Za-z0-9+/=\s]{400,}?)\s*(?:'''|""")/g)) { // other blobs
      let raw; try { raw = utf8(fflate.unzlibSync(b64bytes(m[1]))); } catch (e) { continue; }
      try { const o = JSON.parse(raw); if (o && typeof o === 'object' && !Array.isArray(o)) { Object.assign(found, o); continue; } } catch (e) { /* not json */ }
      Object.assign(found, programsFromCSV(raw));
    }
    for (const k of Object.keys(found)) if (!Array.isArray(found[k])) delete found[k];
    return found;
  }
  // one uploaded file -> list of {name, progs}
  function readFile(name, bytes) {
    const low = name.toLowerCase(), base = low.split('/').pop();
    if (low.endsWith('.zip')) {
      let files; try { files = fflate.unzipSync(bytes, { filter: f => /\.(csv|ipynb)$/i.test(f.name) }); }
      catch (e) { return [{ name, progs: {}, note: 'not a valid zip' }]; }
      return Object.keys(files).sort().flatMap(n => readFile(name + ':' + n, files[n]));
    }
    if (SKIP.has(base)) return [];
    if (low.endsWith('.ipynb')) return [{ name, progs: programsFromNotebook(utf8(bytes)) }];
    if (low.endsWith('.csv')) return [{ name, progs: programsFromCSV(utf8(bytes)) }];
    return [{ name, progs: {}, note: 'ignored (not .zip / .csv / .ipynb)' }];
  }

  // ---------- merge
  function startBest(P, bestText) {
    const best = new Map(); if (!bestText) return best;
    const progs = programsFromCSV(bestText);
    for (const [k, prog] of Object.entries(progs)) if (P.has(k) && check(P.get(k), prog)) best.set(k, prog);
    return best;
  }
  function mergeSource(P, best, src) {
    let ok = 0, bad = 0, gain = 0, tot = 0;
    for (const [k, prog] of Object.entries(src.progs)) {
      if (!P.has(k)) continue;
      const n = check(P.get(k), prog);
      if (!n) { bad++; continue; }
      ok++; tot += n;
      const cur = best.get(k);
      if (!cur) best.set(k, prog);
      else if (n < cur.length) { gain += cur.length - n; best.set(k, prog); }
    }
    return { name: src.name, ok, bad, gain, fileTotal: ok === P.size && !bad ? tot : null, note: src.note };
  }
  const total = best => { let t = 0; for (const v of best.values()) t += v.length; return t; };
  function toCSV(P, best) {
    const lines = ['id,program'];
    for (const k of [...P.keys()].sort()) lines.push(k + ',"' + JSON.stringify(best.get(k)).replace(/"/g, '""') + '"');
    return lines.join('\r\n') + '\r\n';
  }

  root.SantaCore = { parseCSV, loadPuzzles, check, programsFromCSV, programsFromNotebook, readFile,
    startBest, mergeSource, total, toCSV };
  if (typeof module !== 'undefined') module.exports = root.SantaCore;
})(typeof window !== 'undefined' ? window : globalThis);
