/* ============================================================
   TaxNet Graph — National Tax Intelligence Console
   Single-page app over the FastAPI pipeline endpoints.
   Every figure rendered here is read from the pipeline output;
   analyst actions (assign / notice / freeze / notes / review
   decisions / preferences) live in the local workspace only.
   ============================================================ */
'use strict';

/* ---------------- utilities ---------------- */
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

const fmtMoney = n => {
  n = Number(n) || 0;
  const sign = n < 0 ? '-' : '';
  const a = Math.abs(n);
  if (a >= 1e9) return `${sign}Rs ${(a / 1e9).toFixed(2)} Arab`;
  if (a >= 1e7) return `${sign}Rs ${(a / 1e7).toFixed(2)} cr`;
  if (a >= 1e5) return `${sign}Rs ${(a / 1e5).toFixed(1)} lac`;
  return `${sign}Rs ${Math.round(a).toLocaleString('en-PK')}`;
};
const fmtN = n => (Number(n) || 0).toLocaleString('en-PK');

const TIER_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'MINIMAL'];
/* cutoffs as applied by pipeline/scoring.py TIERS */
const TIER_CUTS = { CRITICAL: 81, HIGH: 61, MEDIUM: 41, LOW: 21, MINIMAL: 0 };
const tierOf = s => s >= 81 ? 'CRITICAL' : s >= 61 ? 'HIGH' : s >= 41 ? 'MEDIUM' : s >= 21 ? 'LOW' : 'MINIMAL';
const TIER_UR = { CRITICAL: 'انتہائی سنگین', HIGH: 'سنگین', MEDIUM: 'درمیانہ', LOW: 'کم', MINIMAL: 'معمولی' };

const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const animOff = () => document.documentElement.classList.contains('no-anim')
  || window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function countUp(el, target, render) {
  render = render || (v => fmtN(Math.round(v)));
  if (animOff()) { el.textContent = render(target); return; }
  const t0 = performance.now(), dur = 850;
  const tick = now => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = render(target * e);
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* grow bars that were rendered at width 0 (CSS transitions handle motion) */
function growBars(scope) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    scope.querySelectorAll('[data-w]').forEach(b => { b.style.width = b.dataset.w + '%'; });
  }));
}

/* ---------------- local workspace (analyst terminal state) ---------------- */
const WS = (() => {
  let mem = {};
  const get = k => {
    try { return JSON.parse(localStorage.getItem('tn.' + k)); } catch { return mem['tn.' + k] ?? null; }
  };
  const set = (k, v) => {
    try { localStorage.setItem('tn.' + k, JSON.stringify(v)); } catch { mem['tn.' + k] = v; }
  };
  const keys = () => {
    try { return Object.keys(localStorage).filter(k => k.startsWith('tn.')); }
    catch { return Object.keys(mem); }
  };
  return {
    kase: id => get('case.' + id) || { officer: '', notice: false, frozen: false },
    setCase: (id, patch) => set('case.' + id, Object.assign({ officer: '', notice: false, frozen: false }, get('case.' + id) || {}, patch)),
    note: id => get('note.' + id) || { text: '', ts: null },
    setNote: (id, text) => set('note.' + id, { text, ts: Date.now() }),
    reviews: () => get('reviews') || {},
    setReview: (i, v) => { const r = get('reviews') || {}; if (v === null) delete r[i]; else r[i] = { d: v, ts: Date.now() }; set('reviews', r); },
    prefs: () => Object.assign({ urdu: false, anim: true, privacy: false, compact: false }, get('prefs') || {}),
    setPrefs: p => set('prefs', p),
    counts: () => ({
      cases: keys().filter(k => k.includes('case.')).length,
      notes: keys().filter(k => k.includes('note.')).length,
      reviews: Object.keys(get('reviews') || {}).length,
    }),
    clear: () => { keys().forEach(k => { try { localStorage.removeItem(k); } catch {} }); mem = {}; },
  };
})();

function statusOf(id) {
  const c = WS.kase(id);
  if (c.frozen) return ['frozen', 'Freeze flagged'];
  if (c.notice) return ['notice', 'Notice drafted'];
  if (c.officer) return ['assigned', 'Assigned · ' + c.officer];
  return [null, null];
}

function applyPrefs() {
  const p = WS.prefs(), root = document.documentElement;
  root.classList.toggle('no-anim', !p.anim);
  root.classList.toggle('privacy', p.privacy);
  root.classList.toggle('compact', p.compact);
}

/* ---------------- toasts ---------------- */
function toast(msg, kind) {
  const wrap = document.getElementById('toasts');
  if (wrap.children.length > 3) wrap.firstChild.remove();
  const t = document.createElement('div');
  t.className = 'toast' + (kind === 'err' ? ' err' : '');
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, 3400);
}

let lastErrToast = 0;
window.addEventListener('error', () => {
  if (Date.now() - lastErrToast > 4000) { lastErrToast = Date.now(); toast('Unexpected error — view recovered.', 'err'); }
});

/* ---------------- API layer (cached, defensive) ---------------- */
const API = (() => {
  const cache = new Map();        /* caches in-flight promises: dedupes concurrent calls */
  const profCache = new Map();
  async function get(path) {
    if (cache.has(path)) return cache.get(path);
    const pr = (async () => {
      const r = await fetch(path);
      if (r.status === 401 || r.status === 403) {
        window.location.href = '/login';
        throw new Error('Authentication required');
      }
      if (!r.ok) {
        let detail = r.statusText;
        try { detail = (await r.json()).detail || detail; } catch {}
        throw new Error(detail + ' (' + r.status + ')');
      }
      return r.json();
    })();
    cache.set(path, pr);
    try { return await pr; } catch (e) { cache.delete(path); throw e; }
  }
  return {
    summary: () => get('/api/summary'),
    geo: () => get('/api/geo'),
    sources: () => get('/api/sources'),
    reviewPairs: () => get('/api/review-pairs'),
    all: async () => (await get('/api/profiles?limit=6000')).profiles,
    byTier: async t => (await get('/api/profiles?tier=' + encodeURIComponent(t) + '&limit=6000')).profiles,
    profile: async id => {
      if (profCache.has(id)) return profCache.get(id);
      const r = await fetch('/api/profile/' + encodeURIComponent(id));
      if (!r.ok) throw new Error(r.status === 404 ? 'Entity not found in pipeline output' : 'API error ' + r.status);
      const j = await r.json();
      if (profCache.size > 40) profCache.delete(profCache.keys().next().value);
      profCache.set(id, j);
      return j;
    },
  };
})();

/* ---------------- shared fragments ---------------- */
const skeleton = rows => `<div class="skel">${
  Array.from({ length: rows || 5 }, (_, i) => `<div class="sk" style="height:${i ? 58 : 30}px;width:${i ? 100 : 42}%"></div>`).join('')
}</div>`;

const errorPanel = (msg, retryHash) => `
  <div class="error-panel view-enter">
    <div class="t">Data link unavailable</div>
    <div class="d">${esc(msg)}<br>If the pipeline has not been run yet: <b>python pipeline/run_pipeline.py</b></div>
    <button class="btn danger" onclick="location.hash='${esc(retryHash)}';window.dispatchEvent(new HashChangeEvent('hashchange'))">↻ Retry</button>
  </div>`;

const tierChip = t => `<span class="tier-chip t-${esc(t)}">${esc(t)}</span>`;
const filerPill = f => f === 'Unknown'
  ? '<span class="pill bad">NOT IN FBR</span>'
  : `<span class="pill${f === 'Non-Filer' ? ' bad' : ''}">${esc(String(f).toUpperCase())}</span>`;

/* ============================================================
   PAKISTAN MAP — national boundary traced as lon/lat polyline,
   bubbles = real per-city aggregates from /api/geo
   ============================================================ */
const PK_BORDER = [
  [61.65,25.20],[61.80,26.20],[62.35,26.50],[63.20,26.65],[63.25,27.22],[62.78,28.27],
  [62.40,29.41],[60.90,29.85],[61.80,30.80],[62.50,31.10],[63.58,31.00],[64.10,30.55],
  [64.52,30.50],[65.10,30.20],[66.25,29.85],[66.36,30.45],[66.55,30.98],[66.92,31.30],
  [67.79,31.33],[68.16,31.83],[68.86,31.60],[69.28,31.94],[69.50,32.59],[69.87,33.09],
  [70.33,33.36],[69.99,34.05],[71.10,34.56],[71.66,35.20],[71.43,35.97],[72.58,36.82],
  [73.65,36.91],[74.54,37.02],[75.13,36.99],[75.90,36.62],[76.17,35.81],[77.05,35.50],
  [76.87,34.65],[75.74,34.50],[74.30,34.05],[73.95,33.18],[74.62,32.76],[75.33,32.27],
  [74.51,31.71],[74.61,31.10],[73.91,30.39],[73.38,29.93],[72.90,29.03],[71.90,27.96],
  [70.65,27.93],[69.58,27.17],[70.10,26.60],[70.28,25.71],[71.05,24.69],[70.59,24.42],
  [69.73,24.17],[68.74,24.32],[68.59,23.97],[68.18,23.84],[67.45,24.06],[67.02,24.78],
  [66.50,25.19],[65.41,25.29],[64.53,25.24],[63.49,25.21],[62.32,25.08],[61.65,25.20],
];
const CITY_POS = {
  Islamabad:  { lon: 73.06, lat: 33.69, dx: 12,  dy: -6,  anchor: 'start' },
  Rawalpindi: { lon: 73.05, lat: 33.60, dx: -12, dy: 22,  anchor: 'end'   },
  Peshawar:   { lon: 71.52, lat: 34.01, dx: -12, dy: 4,   anchor: 'end'   },
  Lahore:     { lon: 74.35, lat: 31.55, dx: 13,  dy: 4,   anchor: 'start' },
  Faisalabad: { lon: 73.08, lat: 31.42, dx: -13, dy: 13,  anchor: 'end'   },
  Multan:     { lon: 71.47, lat: 30.20, dx: -13, dy: 4,   anchor: 'end'   },
  Karachi:    { lon: 67.01, lat: 24.86, dx: -12, dy: 13,  anchor: 'end'   },
};

function drawPakMap(stage, geo) {
  const VBW = 760, VBH = 700, PAD = 36;
  const lons = PK_BORDER.map(p => p[0]), lats = PK_BORDER.map(p => p[1]);
  const lo0 = Math.min(...lons), lo1 = Math.max(...lons);
  const la0 = Math.min(...lats), la1 = Math.max(...lats);
  const cosMid = Math.cos(((la0 + la1) / 2) * Math.PI / 180);
  const k = Math.min((VBW - 2 * PAD) / ((lo1 - lo0) * cosMid), (VBH - 2 * PAD) / (la1 - la0));
  const xo = (VBW - (lo1 - lo0) * cosMid * k) / 2, yo = (VBH - (la1 - la0) * k) / 2;
  const X = lon => xo + (lon - lo0) * cosMid * k;
  const Y = lat => yo + (la1 - lat) * k;
  const d = 'M' + PK_BORDER.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join('L') + 'Z';
  const cx = X(70.6), cy = Y(30.0);

  const cities = geo.cities.filter(c => c.entities > 0);
  const maxEnt = Math.max(...cities.map(c => c.entities), 1);
  const share = c => (c.tiers.CRITICAL || 0) / Math.max(c.entities, 1);
  const shares = cities.map(share);
  const s0 = Math.min(...shares), s1 = Math.max(...shares);
  const colorOf = c => {
    const t = s1 > s0 ? (share(c) - s0) / (s1 - s0) : .5;
    return d3.interpolateRgb('#4A7A58', '#B02E20')(t);
  };
  const hottest = cities.slice().sort((a, b) => (b.tiers.CRITICAL || 0) - (a.tiers.CRITICAL || 0))[0];

  /* graticule inside the border */
  let grid = '';
  for (let lon = 62; lon < 77; lon += 2) grid += `<line x1="${X(lon)}" y1="0" x2="${X(lon)}" y2="${VBH}"/>`;
  for (let lat = 24; lat < 37; lat += 2) grid += `<line x1="0" y1="${Y(lat)}" x2="${VBW}" y2="${Y(lat)}"/>`;

  const bubbles = cities.map((c, i) => {
    const p = CITY_POS[c.city]; if (!p) return '';
    const bx = X(p.lon), by = Y(p.lat);
    const r = (5 + 13 * Math.sqrt(c.entities / maxEnt)).toFixed(1);
    return `
    <g class="city-dot" data-city="${esc(c.city)}" style="--i:${i}" transform="translate(${bx.toFixed(1)},${by.toFixed(1)})">
      ${c === hottest ? `<circle class="pulse-ring" r="${r}"></circle>` : ''}
      <circle class="core" r="${r}" fill="${colorOf(c)}" fill-opacity=".82"></circle>
      <text x="${p.dx}" y="${p.dy}" text-anchor="${p.anchor}">${esc(c.city)}</text>
      <text class="cnt" x="${p.dx}" y="${p.dy + 11}" text-anchor="${p.anchor}">${fmtN(c.entities)} files</text>
    </g>`;
  }).join('');

  stage.innerHTML = `
  <svg id="pakmap" viewBox="0 0 ${VBW} ${VBH}" role="img" aria-label="Pakistan map of flagged entities by city">
    <defs>
      <linearGradient id="sweepgrad" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#B98A2F" stop-opacity=".65"/>
        <stop offset="1" stop-color="#B98A2F" stop-opacity="0"/>
      </linearGradient>
      <clipPath id="pkclip"><path d="${d}"/></clipPath>
    </defs>
    <g class="pk-grid" clip-path="url(#pkclip)">${grid}</g>
    <path class="pk-land" d="${d}" fill-opacity=".55"/>
    <g clip-path="url(#pkclip)">
      <g class="pk-sweep" style="transform-origin:${cx.toFixed(0)}px ${cy.toFixed(0)}px">
        <line class="pk-sweepline" x1="${cx}" y1="${cy}" x2="${cx + 560}" y2="${cy}" stroke="url(#sweepgrad)"/>
      </g>
    </g>
    <text x="${X(64.6)}" y="${Y(24.35)}" font-family="IBM Plex Mono,monospace" font-size="10" letter-spacing="3" fill="#8B968C">ARABIAN SEA</text>
    <g>${bubbles}</g>
  </svg>
  <div class="map-tip" id="maptip"></div>`;

  /* tooltip behaviour: hover previews, click pins */
  const tip = stage.querySelector('#maptip');
  let pinned = null;
  const showTip = (c, gEl) => {
    const rows = TIER_ORDER.filter(t => c.tiers[t]).map(t =>
      `<div class="trow"><span>${esc(t)}</span><b>${fmtN(c.tiers[t])}</b></div>`).join('');
    const top = (c.top || []).map(t => `
      <a class="tt-case" href="#/register/${esc(t.entity_id)}">
        <span class="cn">${esc(t.name)}</span><b>${t.score.toFixed(0)}</b>
      </a>`).join('');
    tip.innerHTML = `
      <h4>${esc(c.city)} <span class="ur">${esc(c.city_ur)}</span></h4>
      <div class="trow"><span>Entities geolocated</span><b>${fmtN(c.entities)}</b></div>
      ${rows}
      <div class="trow"><span>Undeclared gap (sum)</span><b>${esc(fmtMoney(c.gap))}/yr</b></div>
      <div class="tt-top"><div class="tt-lbl">Top case files — open in register</div>${top}</div>`;
    const sr = stage.getBoundingClientRect(), br = gEl.getBoundingClientRect();
    let x = br.left - sr.left + br.width / 2 + 14, y = br.top - sr.top - 10;
    tip.classList.add('show');
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    if (x + tw > sr.width - 8) x = Math.max(8, br.left - sr.left - tw - 14);
    y = Math.min(Math.max(8, y), sr.height - th - 8);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  };
  const byName = Object.fromEntries(cities.map(c => [c.city, c]));
  stage.querySelectorAll('.city-dot').forEach(g => {
    const c = byName[g.dataset.city];
    g.addEventListener('mouseenter', () => { if (!pinned) showTip(c, g); });
    g.addEventListener('mouseleave', () => { if (!pinned) tip.classList.remove('show'); });
    g.addEventListener('click', e => { e.stopPropagation(); pinned = c; showTip(c, g); });
  });
  stage.addEventListener('click', e => { if (!tip.contains(e.target)) { pinned = null; tip.classList.remove('show'); } });
}

/* ============================================================
   ROUTER
   ============================================================ */
const VIEWS = {};
let disposeView = null;
let NAV_SEQ = 0;                 /* stale async renders bail out when this moves on */
const navStale = my => my !== NAV_SEQ;

function route() {
  const hash = location.hash || '#/';
  const seg = hash.replace(/^#\//, '').split('/');
  const name = seg[0] || 'home';
  const view = VIEWS[name] ? name : 'home';
  document.querySelectorAll('.navtab').forEach(a =>
    a.classList.toggle('on', a.dataset.route === view));
  if (disposeView) { try { disposeView(); } catch {} disposeView = null; }
  const my = ++NAV_SEQ;
  const el = document.getElementById('view');
  el.scrollTop = 0; window.scrollTo(0, 0);
  Promise.resolve(VIEWS[view](el, seg[1] ? decodeURIComponent(seg[1]) : null, my))
    .catch(err => { if (!navStale(my)) el.innerHTML = `<div class="wrap">${errorPanel(err.message, hash)}</div>`; });
}

/* ============================================================
   VIEW · HOME
   ============================================================ */
VIEWS.home = async (el, _param, my) => {
  el.innerHTML = `<div class="wrap view-enter">${skeleton(6)}</div>`;
  const [s, geo, all] = await Promise.all([API.summary(), API.geo(), API.all()]);
  if (navStale(my)) return;
  const totalGap = all.reduce((a, p) => a + Math.max(0, p.lifestyle_income - p.declared_income), 0);
  const top = all.slice(0, 8);
  const m = s.er_metrics || {};

  el.innerHTML = `
  <div class="wrap view-enter">
    <div class="pagehead">
      <h2>National Overview</h2><span class="ur">قومی جائزہ</span>
      <div class="desc">Cross-referencing ${fmtN(s.er_stats.n_records)} records from ${4 + (s.plugins || []).length} government registries into
      ${fmtN(s.er_stats.n_entities)} resolved entities — scored, tiered and fully traceable to source rows.</div>
    </div>

    <div class="statgrid stagger">
      <div class="stat-card" style="--i:0"><div class="v"><span data-ct="records"></span><small> → </small><span data-ct="entities"></span></div>
        <div class="k">Records → Entities</div><div class="sub">entity resolution · ${esc(s.er_stats.reduction)} pairs pruned</div></div>
      <div class="stat-card redtop" style="--i:1"><div class="v" data-ct="critical"></div>
        <div class="k">Critical case files</div><div class="sub">score ≥ ${TIER_CUTS.CRITICAL} of 100</div></div>
      <div class="stat-card goldtop" style="--i:2"><div class="v" data-ct="gap"></div>
        <div class="k">Undeclared income gap</div><div class="sub">Σ lifestyle-implied − declared, per year</div></div>
      <div class="stat-card" style="--i:3"><div class="v" data-ct="households"></div>
        <div class="k">Households inferred</div><div class="sub">benami screening across family units</div></div>
      <div class="stat-card" style="--i:4"><div class="v">${(m.precision * 100).toFixed(1)}<small>%</small> / ${(m.recall * 100).toFixed(1)}<small>%</small></div>
        <div class="k">ER precision / recall</div><div class="sub">F1 ${(m.f1 * 100).toFixed(1)}% vs ground truth</div></div>
      <div class="stat-card" style="--i:5"><div class="v"><span data-ct="nodes"></span><small> n</small> · <span data-ct="edges"></span><small> e</small></div>
        <div class="k">Knowledge graph</div><div class="sub">pipeline ${s.pipeline_seconds}s · GraphSAGE anomaly</div></div>
    </div>

    <div class="home-grid">
      <div class="card map-card">
        <div class="eyebrow"><span class="en">National Risk Map</span><span class="ur">قومی رسک نقشہ</span>
          <span class="end note">bubble = geolocated entities · colour = critical share</span></div>
        <div class="map-stage"><div class="map-loading">projecting boundary…</div></div>
        <div class="map-cap">
          <span>${fmtN(geo.located)} of ${fmtN(geo.total)} entities geolocated via property / asset records</span>
          <span class="map-legend"><span><i style="background:#4A7A58"></i>lower</span><span><i style="background:#B02E20"></i>higher critical share</span></span>
        </div>
      </div>
      <div class="card feed-card">
        <div class="eyebrow" style="margin:18px 20px 0"><span class="en">Highest-Risk Case Files</span><span class="ur">اہم ترین مقدمات</span></div>
        <div class="feed stagger">
          ${top.map((p, i) => `
          <a class="feed-row" style="--i:${i}" href="#/register/${esc(p.entity_id)}">
            <span class="chip t-${esc(p.tier)}">${p.score.toFixed(0)}</span>
            <span><span class="nm">${esc(p.name)}</span>
              <div class="sub">${esc(p.entity_id)} · ${p.filer === 'Unknown' ? 'NOT IN FBR' : esc(String(p.filer).toUpperCase())} · ${p.n_sources} sources</div></span>
            <span class="gap">▲ ${esc(fmtMoney(Math.max(0, p.lifestyle_income - p.declared_income)))}<small>gap / yr</small></span>
          </a>`).join('')}
        </div>
        <div class="map-cap"><span>pipeline run ${esc(s.generated_at)}</span><a class="btn sm" href="#/register">Open full register →</a></div>
      </div>
    </div>

    <div class="quick-grid stagger">
      <a class="qa-card" style="--i:0" href="#/dashboard"><span class="ic">▦</span>
        <span><span class="t">Triage Dashboard</span><div class="d">${fmtN(s.er_stats.n_entities)} entities · sort & filter</div></span><span class="arr">→</span></a>
      <a class="qa-card" style="--i:1" href="#/register"><span class="ic">⌗</span>
        <span><span class="t">Risk Register</span><div class="d">${fmtN(s.tiers.CRITICAL || 0)} critical case files</div></span><span class="arr">→</span></a>
      <a class="qa-card" style="--i:2" href="#/integrations"><span class="ic">⇄</span>
        <span><span class="t">Identity Review</span><div class="d">${fmtN(s.er_stats.review_queue || 0)} borderline matches queued</div></span><span class="arr">→</span></a>
      <a class="qa-card" style="--i:3" href="/api/export/audit-report?top=20"><span class="ic">⬇</span>
        <span><span class="t">Audit Report</span><div class="d">top 20 cases · evidence cited</div></span><span class="arr">→</span></a>
    </div>
  </div>`;

  const ct = (key, val, render) => { const n = el.querySelector(`[data-ct="${key}"]`); if (n) countUp(n, val, render); };
  ct('records', s.er_stats.n_records); ct('entities', s.er_stats.n_entities);
  ct('critical', s.tiers.CRITICAL || 0);
  ct('gap', totalGap, v => fmtMoney(v));
  ct('households', s.n_households);
  ct('nodes', s.graph_stats.nodes); ct('edges', s.graph_stats.edges);
  drawPakMap(el.querySelector('.map-stage'), geo);
};

/* ============================================================
   VIEW · DASHBOARD (triage table)
   ============================================================ */
const dashState = { tier: 'ALL', q: '', sort: 'score', dir: -1, page: 0 };

VIEWS.dashboard = async (el, _param, my) => {
  el.innerHTML = `<div class="wrap view-enter">${skeleton(7)}</div>`;
  const [s, all] = await Promise.all([API.summary(), API.all()]);
  if (navStale(my)) return;
  const PAGE = 50;

  el.innerHTML = `
  <div class="wrap view-enter">
    <div class="pagehead">
      <h2>Triage Dashboard</h2><span class="ur">ابتدائی جانچ</span>
      <div class="desc">Filter, sort and route entities into the Risk Register. Every row links to a fully evidenced case file.</div>
    </div>
    <div class="tabs" id="dtabs">
      ${['ALL', ...TIER_ORDER].map(t => `
        <button class="tab${dashState.tier === t ? ' on' : ''}" data-t="${t}">
          ${t === 'ALL' ? 'All cases' : t.charAt(0) + t.slice(1).toLowerCase()}
          <span class="n">${t === 'ALL' ? fmtN(all.length) : fmtN(s.tiers[t] || 0)}${t !== 'ALL' ? ' · ≥' + TIER_CUTS[t] : ''}</span>
        </button>`).join('')}
    </div>
    <div class="toolrow">
      <input class="search" id="dq" type="search" placeholder="Search name or entity ID…" value="${esc(dashState.q)}" aria-label="Search entities">
      <span class="note" id="dcount"></span>
      <span class="spacer" style="margin-left:auto"></span>
      <a class="btn sm" id="dcsv" href="/api/export/csv">⬇ CSV — current tier</a>
    </div>
    <div class="tablewrap">
      <table class="data">
        <thead><tr>
          <th class="num">#</th><th>Entity</th><th>Filer</th>
          <th class="num sortable" data-s="declared_income">Declared /yr</th>
          <th class="num sortable" data-s="lifestyle_income">Lifestyle /yr</th>
          <th class="num sortable" data-s="gap">Gap /yr</th>
          <th>Assets</th><th class="num">Src</th>
          <th class="num sortable" data-s="score">Score</th>
          <th>Tier</th><th>Workspace</th>
        </tr></thead>
        <tbody id="dbody"></tbody>
      </table>
    </div>
    <div class="pager">
      <button class="btn sm" id="dprev">← Prev</button>
      <button class="btn sm" id="dnext">Next →</button>
      <span class="pinfo" id="dpinfo"></span>
    </div>
  </div>`;

  const gapOf = p => Math.max(0, p.lifestyle_income - p.declared_income);
  const filtered = () => {
    let rows = dashState.tier === 'ALL' ? all : all.filter(p => p.tier === dashState.tier);
    const q = dashState.q.trim().toLowerCase();
    if (q) rows = rows.filter(p => p.name.toLowerCase().includes(q) || p.entity_id.toLowerCase().includes(q));
    const key = dashState.sort;
    rows = rows.slice().sort((a, b) => {
      const va = key === 'gap' ? gapOf(a) : a[key], vb = key === 'gap' ? gapOf(b) : b[key];
      return (va < vb ? -1 : va > vb ? 1 : 0) * dashState.dir;
    });
    return rows;
  };

  function paint() {
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    dashState.page = Math.min(dashState.page, pages - 1);
    const slice = rows.slice(dashState.page * PAGE, dashState.page * PAGE + PAGE);
    el.querySelector('#dbody').innerHTML = slice.map((p, i) => {
      const [stCls, stTxt] = statusOf(p.entity_id);
      return `
      <tr tabindex="0" data-id="${esc(p.entity_id)}">
        <td class="num">${dashState.page * PAGE + i + 1}</td>
        <td><span class="nm">${esc(p.name)}</span><div class="sub">${esc(p.entity_id)}${p.household_members > 1 ? ' · HH×' + p.household_members : ''}</div></td>
        <td>${filerPill(p.filer)}</td>
        <td class="num">${esc(fmtMoney(p.declared_income))}</td>
        <td class="num">${esc(fmtMoney(p.lifestyle_income))}</td>
        <td class="num"><span class="gapv">${gapOf(p) ? esc(fmtMoney(gapOf(p))) : '—'}</span></td>
        <td class="num" style="text-align:left;font-family:var(--mono);font-size:10.5px">${p.n_properties}P · ${p.n_vehicles}V${p.n_accounts ? ' · ' + p.n_accounts + 'A' : ''}${p.n_intl_trips ? ' · ' + p.n_intl_trips + 'T' : ''}</td>
        <td class="num">${p.n_sources}</td>
        <td class="num"><span class="chip t-${esc(p.tier)}" style="min-width:44px;padding:3px 0;font-size:12px">${p.score.toFixed(0)}</span></td>
        <td>${tierChip(p.tier)}</td>
        <td>${stCls ? `<span class="st-chip ${stCls}">${esc(stTxt)}</span>` : '<span class="st-chip">—</span>'}</td>
      </tr>`;
    }).join('') || `<tr><td colspan="11"><div class="empty"><div><div class="t">No entities match</div><div class="ur">کوئی ریکارڈ نہیں ملا</div></div></div></td></tr>`;

    el.querySelector('#dcount').textContent = `${fmtN(rows.length)} of ${fmtN(all.length)} entities`;
    el.querySelector('#dpinfo').textContent = `page ${dashState.page + 1} / ${pages}`;
    el.querySelector('#dprev').disabled = dashState.page === 0;
    el.querySelector('#dnext').disabled = dashState.page >= pages - 1;
    el.querySelector('#dcsv').href = dashState.tier === 'ALL' ? '/api/export/csv' : '/api/export/csv?tier=' + dashState.tier;
    el.querySelectorAll('th.sortable').forEach(th => {
      const on = th.dataset.s === dashState.sort;
      th.innerHTML = th.textContent.replace(/[▲▼]\s*$/, '').trim() + (on ? `<span class="dir">${dashState.dir < 0 ? '▼' : '▲'}</span>` : '');
    });
    el.querySelectorAll('#dbody tr[data-id]').forEach(tr => {
      const open = () => { location.hash = '#/register/' + tr.dataset.id; };
      tr.addEventListener('click', open);
      tr.addEventListener('keydown', e => { if (e.key === 'Enter') open(); });
    });
  }

  el.querySelector('#dtabs').addEventListener('click', e => {
    const b = e.target.closest('.tab'); if (!b) return;
    dashState.tier = b.dataset.t; dashState.page = 0;
    el.querySelectorAll('#dtabs .tab').forEach(x => x.classList.toggle('on', x === b));
    paint();
  });
  el.querySelector('#dq').addEventListener('input', debounce(e => { dashState.q = e.target.value; dashState.page = 0; paint(); }, 160));
  el.querySelector('#dprev').addEventListener('click', () => { dashState.page--; paint(); });
  el.querySelector('#dnext').addEventListener('click', () => { dashState.page++; paint(); });
  el.querySelectorAll('th.sortable').forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.s;
    if (dashState.sort === k) dashState.dir *= -1; else { dashState.sort = k; dashState.dir = -1; }
    dashState.page = 0; paint();
  }));
  paint();
};

/* ============================================================
   VIEW · RISK REGISTER (list + dossier)
   ============================================================ */
const regState = { tier: 'CRITICAL', q: '', sel: null, shown: 300 };
let currentSim = null;

VIEWS.register = async (el, id, my) => {
  el.innerHTML = `<div class="wrap view-enter">${skeleton(6)}</div>`;
  const s = await API.summary();

  let preload = null;
  if (id) {
    try { preload = await API.profile(id); regState.sel = id; regState.tier = preload.tier; }
    catch { toast('Case ' + id + ' not found in pipeline output.', 'err'); }
  }
  if (navStale(my)) return;

  el.innerHTML = `
  <div class="wrap view-enter">
    <div class="pagehead">
      <h2>Risk Register</h2><span class="ur">رسک رجسٹر</span>
      <span class="spacer"></span>
      <a class="btn gold" href="/api/export/audit-report?top=20">⬇ Audit report — top 20</a>
    </div>
    <div class="reg-grid">
      <aside class="card reg-list">
        <div class="reg-head">
          <div class="bands" id="bands"></div>
          <div style="margin-top:10px"><input class="search" id="rq" type="search" placeholder="Search name or entity ID…" value="${esc(regState.q)}" aria-label="Search register"></div>
          <div style="display:flex;gap:6px;margin-top:9px">
            <a class="btn sm" id="rcsv" href="/api/export/csv?tier=CRITICAL">⬇ CSV — tier</a>
          </div>
        </div>
        <div class="reg-rows" id="rrows" role="list"></div>
      </aside>
      <main class="card dossier" id="dossier"></main>
    </div>
  </div>`;

  let rows = [];
  const bandsEl = el.querySelector('#bands');
  bandsEl.innerHTML = TIER_ORDER.map(t =>
    `<button class="band${t === regState.tier ? ' on' : ''}" data-b="${t}" title="${esc(TIER_UR[t])}"><b>${t}</b><span class="n">${fmtN(s.tiers[t] || 0)}</span></button>`).join('');

  async function loadRows() {
    rows = await API.byTier(regState.tier);
    if (navStale(my)) return;
    paintRows();
  }

  function paintRows() {
    const q = regState.q.trim().toLowerCase();
    const list = rows.filter(p => !q || p.name.toLowerCase().includes(q) || p.entity_id.toLowerCase().includes(q));
    const slice = list.slice(0, regState.shown);
    el.querySelector('#rrows').innerHTML = slice.map(p => {
      const [stCls] = statusOf(p.entity_id);
      const noted = WS.note(p.entity_id).text;
      return `
      <button class="row${p.entity_id === regState.sel ? ' sel' : ''}" data-id="${esc(p.entity_id)}" role="listitem">
        <span class="chip t-${esc(p.tier)}">${p.score.toFixed(0)}</span>
        <span><span class="nm">${esc(p.name)}${stCls || noted ? '<span class="wsdot" title="workspace activity"></span>' : ''}</span>
          <div class="sub">${esc(p.entity_id)} · <span class="${p.filer === 'Non-Filer' || p.filer === 'Unknown' ? 'nonfiler' : ''}">${p.filer === 'Unknown' ? 'NOT IN FBR' : esc(String(p.filer).toUpperCase())}</span> · ${p.n_sources} src${p.household_members > 1 ? ' · HH×' + p.household_members : ''}</div></span>
        <span class="right">decl ${esc(fmtMoney(p.declared_income))}<br>est ${esc(fmtMoney(p.lifestyle_income))}</span>
      </button>`;
    }).join('') + (list.length > regState.shown
      ? `<button class="btn loadmore" id="rmore">Show ${Math.min(300, list.length - regState.shown)} more (${fmtN(list.length - regState.shown)} remaining)</button>`
      : '') || `<div class="empty"><div><div class="t">No matches in this band</div><div class="ur">کوئی ریکارڈ نہیں</div></div></div>`;

    el.querySelectorAll('#rrows .row').forEach(r => r.addEventListener('click', () => select(r.dataset.id)));
    const more = el.querySelector('#rmore');
    if (more) more.addEventListener('click', () => { regState.shown += 300; paintRows(); });
    el.querySelector('#rcsv').href = '/api/export/csv?tier=' + regState.tier;
    const selBtn = el.querySelector('#rrows .row.sel');
    if (selBtn) selBtn.scrollIntoView({ block: 'nearest' });
  }

  bandsEl.addEventListener('click', e => {
    const b = e.target.closest('.band'); if (!b) return;
    regState.tier = b.dataset.b; regState.shown = 300;
    bandsEl.querySelectorAll('.band').forEach(x => x.classList.toggle('on', x === b));
    loadRows().catch(err => toast(err.message, 'err'));
  });
  el.querySelector('#rq').addEventListener('input', debounce(e => { regState.q = e.target.value; regState.shown = 300; paintRows(); }, 160));

  async function select(pid, push) {
    regState.sel = pid;
    if (push !== false) history.replaceState(null, '', '#/register/' + pid);
    paintRows();
    const box = el.querySelector('#dossier');
    box.innerHTML = skeleton(5);
    try {
      const p = await API.profile(pid);
      renderDossier(box, p);
    } catch (err) {
      box.innerHTML = errorPanel(err.message, '#/register');
    }
  }

  await loadRows();
  if (navStale(my)) return;
  if (preload) { const box = el.querySelector('#dossier'); renderDossier(box, preload); paintRows(); }
  else el.querySelector('#dossier').innerHTML = `
    <div class="empty" style="height:70vh"><div>
      <div class="t">Select a case file from the register</div>
      <div class="ur">رجسٹر سے کوئی مقدمہ منتخب کریں</div>
    </div></div>`;

  const onWs = () => paintRows();                 /* workspace actions refresh list dots */
  window.addEventListener('tn-ws', onWs);
  disposeView = () => {
    window.removeEventListener('tn-ws', onWs);
    if (currentSim) { currentSim.stop(); currentSim = null; }
  };
};

/* ---------------- dossier ---------------- */
function renderDossier(box, p) {
  const cls = p.score >= 61 ? '' : p.score >= 41 ? 'mid' : 'low';
  const max = Math.max(p.lifestyle_income, p.declared_income, 1);
  const gap = Math.max(0, p.lifestyle_income - p.declared_income);
  const ws = WS.kase(p.entity_id);
  const note = WS.note(p.entity_id);
  const prefs = WS.prefs();
  const compKeys = Object.keys(p.components || {}).sort();

  box.innerHTML = `
  <div class="dwrap view-enter">
    <div class="case-top">
      <div>
        <div class="case-id">CASE FILE · ${esc(p.entity_id)} · HOUSEHOLD ${esc(p.household_id || '—')}</div>
        <div class="case-name">${esc(p.name)}</div>
        <div class="case-tags">
          <span class="tag ${p.filer === 'Non-Filer' || p.filer === 'Unknown' ? 'bad' : ''}">${p.filer === 'Unknown' ? 'No FBR record linked' : esc(p.filer)}</span>
          <span class="tag">${p.n_vehicles} vehicle(s)</span>
          <span class="tag">${p.n_properties} property record(s)</span>
          ${p.household_members > 1 ? `<span class="tag bad">household of ${p.household_members}</span>` : ''}
          ${p.n_accounts ? `<span class="tag">${p.n_accounts} bank account(s)</span>` : ''}
          ${p.n_intl_trips ? `<span class="tag">${p.n_intl_trips} intl trip(s)</span>` : ''}
          <span class="tag">${p.n_sources} datasets linked</span>
        </div>
      </div>
      <div class="stamp ${cls}">
        <div class="big">${p.score.toFixed(0)}</div>
        <div class="lbl">${esc(p.tier)}</div>
        <div class="ur">${esc(p.tier_ur || TIER_UR[p.tier] || '')}</div>
      </div>
    </div>

    ${ws.frozen ? `<div class="freeze-banner" id="fbanner">⚠ Asset-freeze recommendation flagged on this terminal — pending supervisory approval</div>` : ''}

    <div class="actionbar">
      <span class="assign-wrap">
        <input id="aofficer" type="text" placeholder="Officer name…" value="${esc(ws.officer)}" aria-label="Assign officer">
        <button class="btn sm" id="abtn">Assign</button>
      </span>
      <span class="sep"></span>
      <button class="btn sm gold" id="nbtn">⎙ Generate notice</button>
      <button class="btn sm ${ws.frozen ? 'danger' : ''}" id="fbtn">${ws.frozen ? '✕ Withdraw freeze flag' : '❄ Flag asset freeze'}</button>
      <span class="sep"></span>
      <button class="btn sm" onclick="window.print()">⎙ Print case file</button>
      <span class="note local" style="margin-left:auto">actions saved to this terminal's workspace</span>
    </div>

    <section class="blk">
      <div class="eyebrow"><span class="en">Declared vs Lifestyle</span><span class="ur">ظاہر کردہ بمقابلہ طرزِ زندگی</span></div>
      <div class="cmp">
        <div class="line declared"><span class="l">Declared income (FBR)</span>
          <span class="bar"><i data-w="${(100 * p.declared_income / max).toFixed(1)}"></i></span>
          <span class="v">${esc(fmtMoney(p.declared_income))}/yr</span></div>
        <div class="line lifestyle"><span class="l">Lifestyle-implied income</span>
          <span class="bar"><i data-w="${(100 * p.lifestyle_income / max).toFixed(1)}"></i></span>
          <span class="v">${esc(fmtMoney(p.lifestyle_income))}/yr</span></div>
        ${p.household_members > 1 ? `
        <div class="line declared"><span class="l">Household declared (×${p.household_members})</span>
          <span class="bar"><i data-w="${Math.min(100, 100 * p.household_declared / max).toFixed(1)}"></i></span>
          <span class="v">${esc(fmtMoney(p.household_declared))}/yr</span></div>` : ''}
      </div>
      ${gap > 0 ? `<div class="gapnote">▲ Undeclared gap: ${esc(fmtMoney(gap))} per year</div>` : ''}
    </section>

    <section class="blk">
      <div class="eyebrow"><span class="en">Score Composition · ${compKeys.length} components</span><span class="ur">اسکور کی ترکیب</span>
        <span class="end note">weights renormalised over present components</span></div>
      <div class="compgrid">
        ${compKeys.map(k => `
        <div class="ccard"><div class="h">${esc(k)} · ${esc((p.comp_names[k] || [k])[0])}</div>
          <div class="n">${p.components[k].toFixed(1)}</div>
          <div class="wbar"><i data-w="${(p.weights[k] * 100).toFixed(0)}"></i></div>
          <div class="d">weight ${(p.weights[k] * 100).toFixed(0)}% — ${esc((p.comp_names[k] || ['', ''])[1])}</div></div>`).join('')}
      </div>
    </section>

    ${(p.timeline || []).length ? `
    <section class="blk">
      <div class="eyebrow"><span class="en">Asset Acquisition Timeline</span><span class="ur">اثاثہ جات کی ٹائم لائن</span></div>
      <div class="tl"><div class="tl-items">
        ${p.timeline.map(e => `<div class="tl-it ${esc(e.kind)}"><div class="y">${esc(e.year)}</div><div class="k">${esc(e.kind)}</div><div class="l">${esc(e.label)}</div></div>`).join('')}
      </div></div>
    </section>` : ''}

    <section class="blk">
      <div class="eyebrow"><span class="en">Linked Evidence · file + row traceable</span><span class="ur">منسلک شواہد</span></div>
      <table class="ev"><thead><tr><th>Dept</th><th>Record</th><th>Finding</th><th>Trace</th><th>→</th></tr></thead><tbody>
        ${(p.evidence || []).map(e => `<tr>
          <td class="src ${esc(e.source)}">${esc(e.source)}</td>
          <td class="rid">${esc(e.record_id)}</td><td>${esc(e.finding)}</td>
          <td class="rid">${esc(e.source_file)}:${esc(e.row_number)}</td>
          <td class="rid">${esc(e.contributes_to)}</td></tr>`).join('')}
      </tbody></table>
      ${(p.match_provenance || []).length ? `<div class="prov">identity links via adjudication: ${p.match_provenance.map(m => `${esc(m.linked_to)} (${esc(m.method)}: ${esc(m.reason)})`).join(' · ')}</div>` : ''}
    </section>

    <section class="blk">
      <div class="eyebrow"><span class="en">Why this citizen was flagged</span><span class="ur">نشاندہی کی وجہ</span>
        ${p.audit_urdu ? `<span class="end"><button class="btn sm" id="urbtn">EN / اردو</button></span>` : ''}</div>
      <div class="audit${prefs.urdu && p.audit_urdu ? ' show-ur' : ''}" id="aud">
        <div class="en-block">${esc(p.audit)}</div>
        ${p.audit_urdu ? `<div class="urdu-block">${esc(p.audit_urdu)}</div>` : ''}
        <div class="defens">${esc(p.defensibility)}</div>
      </div>
    </section>

    <section class="blk">
      <div class="eyebrow"><span class="en">Financial Footprint Graph</span><span class="ur">مالیاتی نقشہ</span>
        <span class="end note">drag nodes to inspect</span></div>
      <svg id="netgraph" role="img" aria-label="Knowledge graph of this citizen's linked records"></svg>
      <div class="legend">
        <span><i style="background:#0C3B2A"></i>person</span><span><i style="background:#7A5A18"></i>vehicle</span>
        <span><i style="background:#6E3A7A"></i>property</span><span><i style="background:#1F5E73"></i>meter</span>
        <span><i style="background:#2F6B4F"></i>tax return</span><span><i style="background:#8A6FA8"></i>account</span>
        <span><i style="background:#4A7A8C"></i>trip</span>
      </div>
    </section>

    <section class="blk notes-card">
      <div class="eyebrow"><span class="en">Analyst Notes</span><span class="ur">تجزیہ کار کے نوٹس</span>
        <span class="end note local">autosaves locally</span></div>
      <textarea class="notes-ta" id="nta" placeholder="Observations, follow-ups, field verification requests…">${esc(note.text)}</textarea>
      <div class="notes-meta"><span id="nmeta">${note.ts ? 'saved ' + new Date(note.ts).toLocaleString() : 'no notes yet'}</span><span id="nchars">${note.text.length} chars</span></div>
    </section>
  </div>`;

  growBars(box);

  /* --- wire actions --- */
  const refreshList = () => { const ev = new Event('tn-ws'); window.dispatchEvent(ev); };
  box.querySelector('#abtn').addEventListener('click', () => {
    const name = box.querySelector('#aofficer').value.trim();
    WS.setCase(p.entity_id, { officer: name });
    toast(name ? `Case ${p.entity_id} assigned to ${name} (local workspace).` : 'Assignment cleared.');
    refreshList();
  });
  box.querySelector('#nbtn').addEventListener('click', () => {
    if (openNotice(p, gap)) { WS.setCase(p.entity_id, { notice: true }); toast('Notice drafted from case evidence — review in new window.'); refreshList(); }
  });
  box.querySelector('#fbtn').addEventListener('click', () => {
    const now = !WS.kase(p.entity_id).frozen;
    WS.setCase(p.entity_id, { frozen: now });
    toast(now ? 'Asset-freeze recommendation flagged (local workspace).' : 'Freeze flag withdrawn.');
    renderDossier(box, p); refreshList();
  });
  const urbtn = box.querySelector('#urbtn');
  if (urbtn) urbtn.addEventListener('click', () => box.querySelector('#aud').classList.toggle('show-ur'));
  const nta = box.querySelector('#nta');
  const saveNote = debounce(() => {
    WS.setNote(p.entity_id, nta.value);
    box.querySelector('#nmeta').textContent = 'saved ' + new Date().toLocaleTimeString();
    refreshList();
  }, 600);
  nta.addEventListener('input', () => { box.querySelector('#nchars').textContent = nta.value.length + ' chars'; saveNote(); });

  if (p.graph && window.d3) drawForceGraph(p.graph);
}

/* printable notice generated entirely from real case data */
function openNotice(p, gap) {
  const w = window.open('', '_blank', 'width=820,height=900');
  if (!w) { toast('Pop-up blocked — allow pop-ups to draft the notice.', 'err'); return false; }
  const ev = (p.evidence || []).slice(0, 8).map(e =>
    `<tr><td>${esc(e.source)}</td><td>${esc(e.finding)}</td><td>${esc(e.source_file)} row ${esc(e.row_number)}</td></tr>`).join('');
  w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Notice · ${esc(p.entity_id)}</title>
  <style>
    body{font-family:Georgia,serif;color:#16221B;max-width:720px;margin:34px auto;padding:0 24px;line-height:1.65}
    .hd{text-align:center;border-bottom:3px double #0C3B2A;padding-bottom:14px;margin-bottom:20px}
    .hd h1{font-size:19px;letter-spacing:.12em;text-transform:uppercase;color:#0C3B2A;margin:0}
    .hd .u{font-size:13px;color:#B98A2F;margin-top:4px}
    .meta{font-family:ui-monospace,monospace;font-size:11.5px;display:flex;justify-content:space-between;margin-bottom:18px}
    .wm{position:fixed;top:40%;left:8%;font-size:64px;color:rgba(176,46,32,.08);transform:rotate(-24deg);letter-spacing:.2em;pointer-events:none}
    table{width:100%;border-collapse:collapse;font-size:12px;margin:12px 0}
    th,td{border:1px solid #C9C2AB;padding:6px 9px;text-align:left}
    th{background:#F5F3EB;font-size:10px;text-transform:uppercase;letter-spacing:.1em}
    .sig{margin-top:46px;display:flex;justify-content:space-between;font-size:12.5px}
    .sig div{border-top:1px solid #16221B;padding-top:6px;width:220px;text-align:center}
    .note{font-size:10.5px;color:#5C6A60;border-top:1px dashed #C9C2AB;margin-top:26px;padding-top:10px}
    @media print{.noprint{display:none}}
  </style></head><body>
  <div class="wm">SYNTHETIC · DEMO</div>
  <div class="hd"><h1>TaxNet Graph — Notice of Income / Expenditure Discrepancy</h1>
    <div class="u">آمدن اور اخراجات میں تضاد کا نوٹس — ڈیمو دستاویز</div></div>
  <div class="meta"><span>Ref: TN/${esc(p.entity_id)}/${new Date().getFullYear()}</span><span>Date: ${new Date().toLocaleDateString('en-GB')}</span></div>
  <p>To: <b>${esc(p.name)}</b> (entity ${esc(p.entity_id)}, household ${esc(p.household_id || '—')})</p>
  <p>Cross-referenced government records indicate a deviation score of <b>${p.score.toFixed(0)} / 100 (${esc(p.tier)})</b>.
  Declared annual income of <b>${esc(fmtMoney(p.declared_income))}</b> is inconsistent with a lifestyle-implied income of
  <b>${esc(fmtMoney(p.lifestyle_income))}</b>${gap > 0 ? `, an unexplained gap of <b>${esc(fmtMoney(gap))} per year</b>` : ''}.</p>
  <table><thead><tr><th>Department</th><th>Finding</th><th>Source trace</th></tr></thead><tbody>${ev}</tbody></table>
  <p>You are invited to submit a declaration of sources, or supporting documentation, within <b>30 days</b> of this notice.</p>
  <div class="sig"><div>Analyst — TaxNet Console</div><div>Supervisory Officer</div></div>
  <div class="note">${esc(p.defensibility || '')}</div>
  <p class="noprint" style="text-align:center;margin-top:26px"><button onclick="window.print()" style="padding:9px 22px;font-size:13px;cursor:pointer">⎙ Print notice</button></p>
  </body></html>`);
  w.document.close();
  return true;
}

const KIND_COLOR = { person: '#0C3B2A', vehicle: '#7A5A18', property: '#6E3A7A', meter: '#1F5E73', tax_return: '#2F6B4F', account: '#8A6FA8', trip: '#4A7A8C' };
function drawForceGraph(g) {
  if (currentSim) { currentSim.stop(); currentSim = null; }
  const svg = d3.select('#netgraph');
  if (svg.empty()) return;
  svg.selectAll('*').remove();
  const W = svg.node().clientWidth || 900, H = 380;
  svg.attr('viewBox', [0, 0, W, H]);
  const nodes = g.nodes.map(d => ({ ...d })), links = g.links.map(d => ({ ...d }));
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(110))
    .force('charge', d3.forceManyBody().strength(-320))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(34));
  currentSim = sim;
  const link = svg.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#B6B0A0').attr('stroke-width', 1.2);
  const elbl = svg.append('g').selectAll('text').data(links).join('text')
    .text(d => d.rel).attr('font-family', 'IBM Plex Mono,monospace').attr('font-size', 8.5)
    .attr('fill', '#8B968C').attr('text-anchor', 'middle');
  const node = svg.append('g').selectAll('g').data(nodes).join('g').attr('cursor', 'grab');
  node.append('circle')
    .attr('r', d => d.kind === 'person' ? 20 : 12)
    .attr('fill', d => d.kind === 'person' ? '#F5F3EB' : (KIND_COLOR[d.kind] || '#5C6A60'))
    .attr('stroke', d => KIND_COLOR[d.kind] || '#5C6A60').attr('stroke-width', d => d.kind === 'person' ? 3 : 1.5);
  node.append('text')
    .text(d => d.kind === 'person' ? d.label : String(d.label).slice(0, 22))
    .attr('font-family', 'Archivo,sans-serif').attr('font-size', d => d.kind === 'person' ? 11 : 9)
    .attr('font-weight', d => d.kind === 'person' ? 800 : 500)
    .attr('text-anchor', 'middle').attr('dy', d => d.kind === 'person' ? 36 : 26).attr('fill', '#16221B');
  sim.on('tick', () => {
    nodes.forEach(d => { d.x = Math.max(30, Math.min(W - 30, d.x)); d.y = Math.max(26, Math.min(H - 30, d.y)); });
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    elbl.attr('x', d => (d.source.x + d.target.x) / 2).attr('y', d => (d.source.y + d.target.y) / 2 - 4);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
  node.call(d3.drag()
    .on('start', (e, d) => { if (!e.active) sim.alphaTarget(.25).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
}

/* ============================================================
   VIEW · INTEGRATIONS (sources, pipeline, ER quality, review queue)
   ============================================================ */
const rqState = { page: 0 };

VIEWS.integrations = async (el, _param, my) => {
  el.innerHTML = `<div class="wrap view-enter">${skeleton(6)}</div>`;
  const [src, rp] = await Promise.all([API.sources(), API.reviewPairs()]);
  if (navStale(my)) return;
  const t = src.timings;
  const stages = [
    ['entity_resolution', 'Entity resolution'], ['er_evaluation', 'ER evaluation'],
    ['graph_build', 'Graph build'], ['gnn_training', 'GNN training'], ['scoring_audit', 'Scoring + audit'],
  ].filter(x => t[x[0]] !== undefined);
  const stageMax = Math.max(...stages.map(x => t[x[0]]), 0.001);
  const m = src.er_metrics, e = src.er_stats;

  el.innerHTML = `
  <div class="wrap view-enter">
    <div class="pagehead">
      <h2>Integrations</h2><span class="ur">ڈیٹا روابط</span>
      <div class="desc">Status of every registry feeding the graph — exactly as ingested in the last pipeline run (${esc(src.generated_at)}). Nothing simulated.</div>
    </div>

    <div class="statgrid stagger" style="margin-bottom:16px">
      <div class="stat-card" style="--i:0"><div class="v">${fmtN(e.n_records)}</div><div class="k">Rows ingested</div><div class="sub">across linked sources</div></div>
      <div class="stat-card" style="--i:1"><div class="v">${fmtN(e.n_entities)}</div><div class="k">Entities resolved</div><div class="sub">after de-duplication</div></div>
      <div class="stat-card goldtop" style="--i:2"><div class="v">${esc(e.reduction)}</div><div class="k">Pairs pruned by blocking</div><div class="sub">${fmtN(e.comparisons)} of ${fmtN(e.comparisons_naive)} compared</div></div>
      <div class="stat-card ${src.llm_active ? '' : 'redtop'}" style="--i:3"><div class="v">${src.llm_active ? 'ON' : 'OFF'}</div><div class="k">LLM adjudicator</div><div class="sub">${src.llm_active ? fmtN(e.llm_adjudicated_links) + ' links adjudicated' : 'rules fallback · ' + fmtN(e.review_queue) + ' pairs to human review'}</div></div>
    </div>

    <div class="src-grid stagger">
      ${src.sources.map((x, i) => `
      <div class="src-card${!x.on_disk ? ' missing' : x.loaded ? '' : ' plugin-off'}" style="--i:${i}">
        <div class="sc-top"><span class="sc-name">${esc(x.name)}</span>
          <span class="sc-status"><i></i>${!x.on_disk ? 'File missing' : x.loaded ? 'Linked · last run' : 'On disk · not ingested'}</span></div>
        <div class="sc-file">data/${esc(x.file)}</div>
        <div class="sc-desc">${esc(x.desc)}</div>
        <div class="sc-stats">
          <div><div class="v">${fmtN(x.records)}</div><div class="k">rows on disk</div></div>
          <div><div class="v">${x.size_kb >= 1024 ? (x.size_kb / 1024).toFixed(1) + ' MB' : x.size_kb + ' KB'}</div><div class="k">file size</div></div>
          <div><div class="v">${esc(x.component)}</div><div class="k">feeds component</div></div>
        </div>
        ${!x.loaded && x.on_disk ? `<div class="note">present but not ingested in the last run — re-run <span style="font-family:var(--mono)">pipeline/run_pipeline.py</span> to include it</div>` : ''}
      </div>`).join('')}
    </div>

    <div class="ctl-grid" style="margin-top:16px">
      <div class="card card-pad timing">
        <div class="eyebrow"><span class="en">Pipeline Stage Timings</span><span class="ur">پائپ لائن مراحل</span>
          <span class="end note">total ${t.total}s</span></div>
        ${stages.map(([k, label]) => `
        <div class="t-row"><span class="t-name">${esc(label)}</span>
          <span class="t-bar"><i data-w="${(100 * t[k] / stageMax).toFixed(1)}"></i></span>
          <span class="t-val">${t[k].toFixed(2)}s</span></div>`).join('')}
      </div>
      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Entity-Resolution Quality</span><span class="ur">شناختی معیار</span>
          <span class="end note">vs ground truth</span></div>
        <div class="metric green"><div class="m-top"><span>Precision</span><b>${(m.precision * 100).toFixed(1)}%</b></div><div class="m-bar"><i data-w="${(m.precision * 100).toFixed(1)}"></i></div></div>
        <div class="metric green"><div class="m-top"><span>Recall</span><b>${(m.recall * 100).toFixed(1)}%</b></div><div class="m-bar"><i data-w="${(m.recall * 100).toFixed(1)}"></i></div></div>
        <div class="metric"><div class="m-top"><span>F1 score</span><b>${(m.f1 * 100).toFixed(1)}%</b></div><div class="m-bar"><i data-w="${(m.f1 * 100).toFixed(1)}"></i></div></div>
        <div class="kv"><span class="k">Predicted identity pairs</span><span class="v">${fmtN(m.predicted_pairs)}</span></div>
        <div class="kv"><span class="k">True pairs (ground truth)</span><span class="v">${fmtN(m.true_pairs)}</span></div>
        <div class="kv"><span class="k">True positives</span><span class="v">${fmtN(m.true_positives)}</span></div>
        <div class="kv"><span class="k">Gray-zone pairs</span><span class="v">${fmtN(e.gray_zone_pairs)}</span></div>
      </div>
    </div>

    <section style="margin-top:24px">
      <div class="eyebrow"><span class="en">Identity Review Queue · ${fmtN(rp.total)} borderline matches</span><span class="ur">شناختی نظرثانی</span>
        <span class="end"><button class="btn sm gold" id="rqexp">⬇ Export decisions CSV</button></span></div>
      <div class="note local" style="margin-bottom:8px">Pairs below the τ=0.75 auto-link threshold are never auto-merged — they queue here for human adjudication. Decisions are stored on this terminal and exportable.</div>
      <div class="rq-progress"><i id="rqbar"></i></div>
      <div id="rqlist"></div>
      <div class="pager"><button class="btn sm" id="rqprev">← Prev</button><button class="btn sm" id="rqnext">Next →</button><span class="pinfo" id="rqinfo"></span></div>
    </section>
  </div>`;

  growBars(el);

  const PAGE = 8;
  function paintQueue() {
    const dec = WS.reviews();
    const pages = Math.max(1, Math.ceil(rp.pairs.length / PAGE));
    rqState.page = Math.min(rqState.page, pages - 1);
    const slice = rp.pairs.slice(rqState.page * PAGE, rqState.page * PAGE + PAGE);
    el.querySelector('#rqlist').innerHTML = slice.map((pr, j) => {
      const idx = rqState.page * PAGE + j;
      const d = dec[idx] && dec[idx].d;
      const side = r => `
        <div class="rq-side">
          <div class="s-src">${esc(r.source)} · ${esc(r.record_id)}</div>
          <div class="s-name">${esc(r.name)}</div>
          <div class="s-addr">${esc(r.address)}</div>
          <div class="s-file">${esc(r.file)} · row ${esc(r.row)}</div>
        </div>`;
      return `
      <div class="rq-card${d ? ' decided' : ''}">
        <div class="rq-head">
          <span class="rq-score">τ ${pr.score.toFixed(3)}</span>
          <span class="rq-reason">${esc(pr.reason)}</span>
          ${d ? `<span class="rq-decided-chip ${d}">${d === 'approve' ? '✓ same person' : '✕ different'}</span>` : ''}
        </div>
        <div class="rq-body">${side(pr.a)}<div class="rq-vs">VS</div>${side(pr.b)}</div>
        <div class="rq-actions">
          <button class="btn sm" data-rq="approve" data-i="${idx}">✓ Same person — link</button>
          <button class="btn sm danger" data-rq="reject" data-i="${idx}">✕ Different — keep split</button>
          ${d ? `<button class="btn sm" data-rq="undo" data-i="${idx}">↺ Undo</button>` : ''}
        </div>
      </div>`;
    }).join('');
    const done = Object.keys(dec).length;
    el.querySelector('#rqbar').style.width = (100 * done / Math.max(rp.pairs.length, 1)) + '%';
    el.querySelector('#rqinfo').textContent = `page ${rqState.page + 1} / ${pages} · ${done} of ${rp.pairs.length} adjudicated`;
    el.querySelector('#rqprev').disabled = rqState.page === 0;
    el.querySelector('#rqnext').disabled = rqState.page >= pages - 1;
    setRqBadge(rp.pairs.length - done);
    el.querySelectorAll('[data-rq]').forEach(b => b.addEventListener('click', () => {
      const i = Number(b.dataset.i);
      WS.setReview(i, b.dataset.rq === 'undo' ? null : b.dataset.rq);
      paintQueue();
    }));
  }
  el.querySelector('#rqprev').addEventListener('click', () => { rqState.page--; paintQueue(); });
  el.querySelector('#rqnext').addEventListener('click', () => { rqState.page++; paintQueue(); });
  el.querySelector('#rqexp').addEventListener('click', () => {
    const dec = WS.reviews();
    const lines = [['pair_index', 'similarity', 'record_a', 'name_a', 'record_b', 'name_b', 'decision', 'decided_at']];
    rp.pairs.forEach((pr, i) => {
      const d = dec[i];
      lines.push([i, pr.score, pr.a.record_id, pr.a.name, pr.b.record_id, pr.b.name,
        d ? d.d : 'pending', d ? new Date(d.ts).toISOString() : '']);
    });
    const csv = lines.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = 'taxnet_review_decisions.csv';
    a.click(); URL.revokeObjectURL(a.href);
    toast('Review decisions exported.');
  });
  paintQueue();
};

/* ============================================================
   VIEW · CONTROL PANEL
   ============================================================ */
VIEWS.control = async (el, _param, my) => {
  el.innerHTML = `<div class="wrap view-enter">${skeleton(6)}</div>`;
  const [s, all] = await Promise.all([API.summary(), API.all()]);
  const ref = await API.profile(all[0].entity_id);  /* exposes run weights + comp names + defensibility */
  if (navStale(my)) return;
  const prefs = WS.prefs();
  const wsCounts = WS.counts();

  const bandStats = TIER_ORDER.map(t => {
    const scores = all.filter(p => p.tier === t).map(p => p.score);
    return { t, n: scores.length, lo: scores.length ? Math.min(...scores) : null, hi: scores.length ? Math.max(...scores) : null };
  });
  const simDefault = { CRITICAL: TIER_CUTS.CRITICAL, HIGH: TIER_CUTS.HIGH, MEDIUM: TIER_CUTS.MEDIUM, LOW: TIER_CUTS.LOW };
  const sim = { ...simDefault };
  const compKeys = Object.keys(ref.weights || {}).sort();

  el.innerHTML = `
  <div class="wrap view-enter">
    <div class="pagehead">
      <h2>Control Panel</h2><span class="ur">کنٹرول پینل</span>
      <div class="desc">Scoring configuration as applied in the last pipeline run, model status, and this terminal's workspace preferences.</div>
    </div>
    <div class="ctl-grid">

      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Scoring Weights · current run</span><span class="ur">اسکورنگ وزن</span></div>
        ${compKeys.map(k => `
        <div class="w-row">
          <span class="w-key">${esc(k)}</span>
          <span><span class="w-name">${esc((ref.comp_names[k] || [k])[0])}</span>
            <div class="w-desc">${esc((ref.comp_names[k] || ['', ''])[1])}</div>
            <div class="w-bar"><i data-w="${(ref.weights[k] * 100).toFixed(0)}"></i></div></span>
          <span class="w-val">${(ref.weights[k] * 100).toFixed(1)}%</span>
        </div>`).join('')}
        <div class="note" style="margin-top:10px">weights renormalised over components present in this run (plug-ins add D/E)</div>
      </div>

      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Risk Tier Bands · observed</span><span class="ur">رسک درجے</span></div>
        ${bandStats.map(b => `
        <div class="thr-row">
          ${tierChip(b.t)}
          <span class="note">score ≥ ${TIER_CUTS[b.t]}${b.n ? ` · observed ${b.lo.toFixed(1)} – ${b.hi.toFixed(1)}` : ''}</span>
          <span class="thr-range">${fmtN(b.n)} files</span>
        </div>`).join('')}
        <div class="eyebrow" style="margin-top:22px"><span class="en">What-if Threshold Simulator</span></div>
        <div class="note local" style="margin:2px 0 8px">re-bands the ${fmtN(all.length)} real scores locally — pipeline output is never modified</div>
        ${['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(t => `
        <div class="sim-row">
          <span class="sim-lbl">${esc(t)} ≥</span>
          <input type="range" min="1" max="100" step="1" value="${sim[t]}" data-sim="${t}" aria-label="${esc(t)} cutoff">
          <span class="sim-val" id="sv-${t}">${sim[t]}</span>
        </div>`).join('')}
        <div class="sim-out" id="simout"></div>
      </div>

      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Model & Run Status</span><span class="ur">ماڈل کی حالت</span></div>
        <div class="kv"><span class="k">Pipeline run</span><span class="v">${esc(s.generated_at)}</span></div>
        <div class="kv"><span class="k">Total runtime</span><span class="v">${s.pipeline_seconds}s</span></div>
        <div class="kv"><span class="k">Anomaly model</span><span class="v">GraphSAGE · trained ${(s.timings.gnn_training || 0).toFixed(2)}s</span></div>
        <div class="kv"><span class="k">Knowledge graph</span><span class="v">${fmtN(s.graph_stats.nodes)} nodes · ${fmtN(s.graph_stats.edges)} edges</span></div>
        <div class="kv"><span class="k">ER auto-link threshold</span><span class="v">τ = 0.75</span></div>
        <div class="kv"><span class="k">ER precision / recall</span><span class="v">${(s.er_metrics.precision * 100).toFixed(1)}% / ${(s.er_metrics.recall * 100).toFixed(1)}%</span></div>
        <div class="kv"><span class="k">LLM adjudicator</span><span class="v ${s.llm_active ? 'ok' : 'off'}">${s.llm_active ? 'ACTIVE' : 'OFF — rules fallback'}</span></div>
        <div class="kv"><span class="k">Plug-ins in run</span><span class="v">${(s.plugins || []).length ? esc(s.plugins.join(', ')) : 'none'}</span></div>
        <div class="kv"><span class="k">Households inferred</span><span class="v">${fmtN(s.n_households)}</span></div>
      </div>

      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Terminal Preferences</span><span class="ur">ترجیحات</span></div>
        ${[
          ['urdu', 'Urdu narratives by default', 'open case audits in اردو where available'],
          ['anim', 'Interface animations', 'count-ups, transitions, radar sweep'],
          ['privacy', 'Privacy mask', 'blur citizen names for screen-sharing'],
          ['compact', 'Compact density', 'tighter rows in registers and tables'],
        ].map(([k, t2, d2]) => `
        <label class="switchrow">
          <span><span class="st">${t2}</span><div class="sd">${d2}</div></span>
          <span class="switch"><input type="checkbox" data-pref="${k}" ${prefs[k] ? 'checked' : ''}><span class="track"></span></span>
        </label>`).join('')}
      </div>

      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Local Workspace</span><span class="ur">ورک سپیس</span></div>
        <div class="kv"><span class="k">Case files touched</span><span class="v">${fmtN(wsCounts.cases)}</span></div>
        <div class="kv"><span class="k">Analyst notes</span><span class="v">${fmtN(wsCounts.notes)}</span></div>
        <div class="kv"><span class="k">Review decisions</span><span class="v">${fmtN(wsCounts.reviews)}</span></div>
        <div class="note local" style="margin:10px 0">workspace lives in this browser only — clearing it cannot touch pipeline data</div>
        <button class="btn danger sm" id="wsclear">✕ Clear local workspace</button>
      </div>

      <div class="card card-pad">
        <div class="eyebrow"><span class="en">Methodology Statement</span><span class="ur">طریقہ کار</span></div>
        <div class="note" style="font-size:11.5px;line-height:1.8">${esc(ref.defensibility || '')}</div>
      </div>

    </div>
  </div>`;

  growBars(el);

  /* simulator */
  const scores = all.map(p => p.score);
  const actual = {}; TIER_ORDER.forEach(t2 => actual[t2] = all.filter(p => p.tier === t2).length);
  function paintSim() {
    const counts = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, MINIMAL: 0 };
    for (const sc of scores) {
      if (sc >= sim.CRITICAL) counts.CRITICAL++;
      else if (sc >= sim.HIGH) counts.HIGH++;
      else if (sc >= sim.MEDIUM) counts.MEDIUM++;
      else if (sc >= sim.LOW) counts.LOW++;
      else counts.MINIMAL++;
    }
    el.querySelector('#simout').innerHTML = TIER_ORDER.map(t2 => {
      const delta = counts[t2] - actual[t2];
      return `<div class="sim-cell"><div class="sv" style="color:var(--${t2 === 'CRITICAL' ? 'crit' : t2 === 'HIGH' ? 'high' : t2 === 'MEDIUM' ? 'med' : t2 === 'LOW' ? 'low' : 'min'})">${fmtN(counts[t2])}</div>
        <div class="sd ${delta > 0 ? 'up' : delta < 0 ? 'down' : ''}">${delta > 0 ? '▲ +' + fmtN(delta) : delta < 0 ? '▼ ' + fmtN(delta) : '· no change'}</div>
        <div class="sk2">${esc(t2)}</div></div>`;
    }).join('');
  }
  el.querySelectorAll('[data-sim]').forEach(r => r.addEventListener('input', () => {
    sim[r.dataset.sim] = Number(r.value);
    /* keep cutoffs strictly ordered */
    if (sim.HIGH >= sim.CRITICAL) sim.HIGH = sim.CRITICAL - 1;
    if (sim.MEDIUM >= sim.HIGH) sim.MEDIUM = sim.HIGH - 1;
    if (sim.LOW >= sim.MEDIUM) sim.LOW = sim.MEDIUM - 1;
    ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].forEach(t2 => {
      el.querySelector(`[data-sim="${t2}"]`).value = sim[t2];
      el.querySelector('#sv-' + t2).textContent = sim[t2];
    });
    paintSim();
  }));
  paintSim();

  /* preferences */
  el.querySelectorAll('[data-pref]').forEach(cb => cb.addEventListener('change', () => {
    const p = WS.prefs(); p[cb.dataset.pref] = cb.checked; WS.setPrefs(p); applyPrefs();
    toast('Preference saved.');
  }));

  /* clear workspace: double-click confirm */
  const wsBtn = el.querySelector('#wsclear');
  let armed = false, armTimer = null;
  wsBtn.addEventListener('click', () => {
    if (!armed) {
      armed = true; wsBtn.textContent = '⚠ Click again to confirm';
      armTimer = setTimeout(() => { armed = false; wsBtn.textContent = '✕ Clear local workspace'; }, 3000);
      return;
    }
    clearTimeout(armTimer); WS.clear(); applyPrefs();
    toast('Local workspace cleared.');
    VIEWS.control(el, null, NAV_SEQ).catch(() => {});
  });
};

/* ============================================================
   BOOT
   ============================================================ */
function setRqBadge(n) {
  const b = document.getElementById('nav-rq');
  if (!b) return;
  if (n > 0) { b.textContent = fmtN(n) + ' queued'; b.hidden = false; } else b.hidden = true;
}

function startClock() {
  const elc = document.getElementById('clock');
  const fmt = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Karachi', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const tick = () => { elc.textContent = fmt.format(new Date()); };
  tick(); setInterval(tick, 1000);
}

async function boot() {
  applyPrefs();
  startClock();
  window.addEventListener('hashchange', route);
  route();
  try {
    const s = await API.summary();
    document.getElementById('runinfo').innerHTML =
      `run <b>${esc(s.generated_at)}</b> · ${fmtN(s.er_stats.n_entities)} entities<br>pipeline ${s.pipeline_seconds}s${s.plugins && s.plugins.length ? ' · +' + esc(s.plugins.join('+')) : ''} · LLM ${s.llm_active ? 'on' : 'off'}`;
    document.getElementById('foot').textContent =
      `generated ${s.generated_at} · ${fmtN(s.er_stats.n_records)} records → ${fmtN(s.er_stats.n_entities)} entities · synthetic demonstration data`;
    const rp = await API.reviewPairs();
    setRqBadge(rp.total - Object.keys(WS.reviews()).length);
  } catch (e2) {
    document.getElementById('runinfo').innerHTML = '<span style="color:#E8A0A0">pipeline output missing</span>';
  }
}
boot();

/* ============================================================
   VIEW · CITIZEN DISPUTES (auditor-side queue)
   Routed via #/disputes in the auditor dashboard.
   Uses the portal JWT cookie with role='auditor'.
   For v1 demo: no auditor login is implemented; this view
   calls the endpoint and shows the data if accessible.
   ============================================================ */
VIEWS.disputes = async (el, _param, my) => {
  el.innerHTML = `<div class="wrap view-enter">${skeleton(4)}</div>`;
  let data;
  try {
    const r = await fetch('/portal/admin/disputes?status_filter=all', { credentials: 'same-origin' });
    if (r.status === 401 || r.status === 403) {
      if (navStale(my)) return;
      el.innerHTML = `<div class="wrap view-enter">
        <div class="pagehead"><h2>Citizen Disputes</h2><span class="ur">شہری اعتراضات</span></div>
        <div class="card card-pad" style="text-align:center;padding:40px">
          <div style="font-size:15px;color:var(--mut);margin-bottom:8px">Auditor authentication required</div>
          <div style="font-size:13px;color:var(--faint)">This panel requires an auditor session cookie (role=auditor).<br>
          For demo purposes, set <code>PORTAL_JWT_SECRET</code> and issue an auditor token manually.</div>
        </div>
      </div>`;
      return;
    }
    data = await r.json();
  } catch(err) {
    if (navStale(my)) return;
    el.innerHTML = `<div class="wrap">${errorPanel(err.message, '#/disputes')}</div>`;
    return;
  }
  if (navStale(my)) return;

  const disputes = data.disputes || [];
  const pending  = data.pending_registrations || [];

  const statusBadge = s => {
    const map = { pending:'med', accepted:'ok', rejected:'bad', info_requested:'warn' };
    return `<span class="pill ${map[s]||''}">${esc(s.replace('_',' '))}</span>`;
  };

  el.innerHTML = `<div class="wrap view-enter">
    <div class="pagehead">
      <h2>Citizen Disputes</h2><span class="ur">شہری اعتراضات</span>
      <div class="desc">
        Dispute tickets submitted by citizens through the People's Portal, plus
        registration attempts that could not be auto-matched.
        Disputes do <b>not</b> alter scores directly — an accepted dispute creates a
        manual override record for the next pipeline run.
      </div>
    </div>

    <div class="card card-pad" style="margin-bottom:16px">
      <div class="eyebrow"><span class="en">Pending Registrations (${pending.length})</span></div>
      <div class="note" style="margin-bottom:12px">
        Identity claims that matched ambiguously during registration.
        Approve to provision a portal account; reject to decline.
        <br><i>Requires Administrator role.</i>
      </div>
      ${pending.length === 0 ? '<div class="note">No pending registrations.</div>' :
        `<table class="ev-tbl" style="width:100%">
          <thead><tr><th>ID</th><th>Claimed name</th><th>Reason</th><th>Candidates</th><th>Date</th><th>Actions</th></tr></thead>
          <tbody>
          ${pending.map(p => {
            const cands = JSON.parse(p.candidates || '[]');
            return `<tr>
              <td style="font-family:var(--mono);font-size:11px">#${p.id}</td>
              <td>${esc(p.claimed_name)}</td>
              <td><span class="pill">${esc(p.reason?.replace(/_/g,' ')||'')}</span></td>
              <td style="font-size:12px;font-family:var(--mono)">${cands.map(c=>`${c.entity_id} (${c.score})`).join('<br>')}</td>
              <td style="font-size:12px">${esc((p.created_at||'').slice(0,10))}</td>
              <td>
                <button class="btn" style="font-size:11.5px;padding:4px 10px;margin-right:4px"
                  onclick="resolveReg(${p.id},'approve',this, '${esc(JSON.stringify(cands))}')">Approve</button>
                <button class="btn danger" style="font-size:11.5px;padding:4px 10px"
                  onclick="resolveReg(${p.id},'reject',this)">Reject</button>
              </td>
            </tr>`;
          }).join('')}
          </tbody>
        </table>`
      }
    </div>

    <div class="card card-pad">
      <div class="eyebrow"><span class="en">Dispute Tickets (${disputes.length})</span></div>
      ${disputes.length === 0 ? '<div class="note">No dispute tickets yet.</div>' :
        `<table class="ev-tbl" style="width:100%">
          <thead><tr><th>ID</th><th>Source · Record</th><th>Finding</th><th>Category</th><th>Status</th><th>Date</th><th>Actions</th></tr></thead>
          <tbody>
          ${disputes.map(d => `<tr>
            <td style="font-family:var(--mono);font-size:11px">#${d.id}</td>
            <td style="font-family:var(--mono);font-size:11px">${esc(d.source)}<br>${esc(d.record_id)}</td>
            <td style="font-size:12.5px;max-width:200px">${esc(d.finding)}</td>
            <td style="font-size:12px">${esc((d.category||'').replace(/_/g,' '))}</td>
            <td>${statusBadge(d.status)}</td>
            <td style="font-size:12px">${esc((d.created_at||'').slice(0,10))}</td>
            <td style="white-space:nowrap">
              ${d.status === 'pending' ? `
                <button class="btn" style="font-size:11px;padding:3px 8px;margin:2px"
                  onclick="resolveDisp(${d.id},'accept',this)">Accept</button>
                <button class="btn danger" style="font-size:11px;padding:3px 8px;margin:2px"
                  onclick="resolveDisp(${d.id},'reject',this)">Reject</button>
                <button class="btn" style="font-size:11px;padding:3px 8px;margin:2px;background:var(--high)"
                  onclick="resolveDisp(${d.id},'info_requested',this)">Ask info</button>
              ` : '<span class="note">Resolved</span>'}
            </td>
          </tr>`).join('')}
          </tbody>
        </table>`
      }
    </div>
  </div>`;
};

async function resolveDisp(id, action, btn) {
  btn.disabled = true; btn.textContent = '…';
  try {
    await fetch(`/portal/admin/disputes/${id}?action=${encodeURIComponent(action)}`, {
      method: 'PATCH', credentials: 'same-origin'
    });
    toast(`Dispute #${id} ${action.replace('_',' ')}.`);
    VIEWS.disputes(document.getElementById('view'), null, NAV_SEQ);
  } catch(e) {
    btn.disabled = false; btn.textContent = action;
    toast('Action failed: ' + e.message, 'err');
  }
}

async function resolveReg(id, action, btn, candsJsonStr) {
  let body = {};
  if (action === 'approve') {
    const cands = JSON.parse(candsJsonStr || '[]');
    if (!cands.length) return toast('No candidates to approve.', 'err');
    
    // For demo simplicity, prompt the user for the entity_id
    const candStr = cands.map(c => c.entity_id).join(', ');
    const eid = prompt(`Enter entity_id to approve this registration against (Candidates: ${candStr}):`, cands[0].entity_id);
    if (!eid) return;
    body = { entity_id: eid };
  } else {
    const reason = prompt('Enter rejection reason (e.g. No matching candidate, Insufficient confidence, Suspected fraudulent claim):');
    if (!reason) return;
    body = { reason: reason };
  }

  btn.disabled = true; btn.textContent = '…';
  try {
    const r = await fetch(`/portal/admin/pending-registrations/${id}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);

    toast(`Registration #${id} ${action}d.`);
    VIEWS.disputes(document.getElementById('view'), null, NAV_SEQ);
  } catch(e) {
    btn.disabled = false; btn.textContent = action;
    toast('Action failed: ' + e.message, 'err');
    if (e.message.includes('403')) {
      toast('Admin role required for this action.', 'err');
    }
  }
}

// Fetch and display logged-in official user info
document.addEventListener('DOMContentLoaded', async () => {
  const userInfoEl = document.getElementById('official-user-info');
  const logoutBtn = document.getElementById('official-logout-btn');
  
  if (userInfoEl && logoutBtn) {
    try {
      const r = await fetch('/portal/admin/me');
      if (r.ok) {
        const data = await r.json();
        userInfoEl.textContent = `${data.username} (${data.role})`;
      } else {
        window.location.href = '/login';
      }
    } catch (e) {
      // Ignore
    }

    logoutBtn.addEventListener('click', async () => {
      try {
        await fetch('/portal/admin/logout', { method: 'POST' });
      } catch (e) {
        // Ignore
      }
      window.location.href = '/login';
    });
  }
});

