# Santa 2026 · Merge

Merge everything you collect for **Kaggle Santa 2026 – The Elf Calculator** into one verified `submission.csv`:
results zips, CSV files, and notebooks (`.ipynb`) – Pardheev's `GIFT` cells, Asterios's `CHANGE` cells and other
embedded program blobs are read out of the notebook as data (notebook code is never run).

Every program is replayed under the competition rules (positive integers, exact division, value limit, register
range, every target produced, ≤ 4096 instructions, ≤ 128 KiB per cell) and the shortest valid program per puzzle wins.

Two ways to use it:

| | Web app (`index.html`) | Command line (`santa_merge.py`) |
|---|---|---|
| Runs | in the browser, on Vercel or any static host | anywhere with Python 3.8+ |
| Inputs | zips, CSVs, notebooks | zips, CSVs, notebooks, folders, **Kaggle notebook links**, `dataset:owner/slug` |
| Best so far | kept in the browser (IndexedDB) | kept in `best.csv` |
| Submit | download `submission.csv`, upload on Kaggle | `--submit` (only if it beats `best.csv`) |

## Web app

1. Open the site. Load `puzzles.csv` once (competition **Data** page) – it stays in your browser, never uploaded.
2. Optional: load your current best file. Every merge starts from it and it updates itself when a merge is better.
3. Add zips / CSVs / notebooks → **Merge & verify** → **Download submission.csv**.

Kaggle links can't be fetched from a browser (they need your Kaggle API key): open the notebook on Kaggle → *Output* →
download, then add that zip. Or use the command line, which fetches links itself.

### Deploy on Vercel

Static site, no build step.

1. vercel.com → **Add New… → Project** → import this GitHub repository.
2. Framework preset: **Other**. Build command: *(empty)*. Output directory: `.` (repository root).
3. **Deploy.** Every push to `main` redeploys automatically.

(CLI alternative: `npm i -g vercel && vercel --prod` in this folder.)

## Command line

```bash
pip install kaggle                     # only for Kaggle links and --submit (token in ~/.kaggle/kaggle.json)
python santa_merge.py results_88.zip 130-931-best-public-score-santa-2026.ipynb
python santa_merge.py https://www.kaggle.com/code/pardheev/130-934-best-public-score-santa-2026
python santa_merge.py "kaggle kernels output pardheev/130-934-best-public-score-santa-2026 -p /path/to/dest"
python santa_merge.py dataset:goodpjw2008/santa-2026-dag-lns-programs --submit
```

It finds `puzzles.csv` next to it (or downloads it), starts from `best.csv`, writes `submission.csv`, and updates
`best.csv` when the total improves.

## Files

- `index.html`, `app.js` – the page
- `core.js` – rules, readers and merge (shared logic; also runs in Node)
- `vendor/fflate.js` – zip / zlib in the browser (MIT, see `vendor/fflate-LICENSE`)
- `santa_merge.py` – command-line version
- `vercel.json` – static hosting headers

Competition data (`puzzles.csv`) and submission files are not part of this repository (`.gitignore`).
