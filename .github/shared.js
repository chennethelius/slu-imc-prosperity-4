// Shared helpers for dashboard.html and run.html
const pc = v => v >= 0 ? 'pos' : 'neg';
const fp = v => (v >= 0 ? '+' : '') + v.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1});

async function fetchText(u) {
  try { const r = await fetch(u); return r.ok ? await r.text() : null; }
  catch { return null; }
}

async function fetchCsv(u) {
  try {
    const r = await fetch(u);
    if (!r.ok) return [];
    const t = await r.text();
    const l = t.trim().split('\n');
    if (l.length < 2) return [];
    const h = l[0].split(';');
    return l.slice(1).map(x => {
      const v = x.split(';');
      const o = {};
      h.forEach((k, i) => o[k] = v[i] || '');
      return o;
    });
  } catch { return []; }
}
