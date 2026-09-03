/* ============================================================
   TaxNet Graph — Citizens' Portal SPA
   Single-page app over /portal/* API endpoints.

   Auth state: tracked in memory only (no localStorage).
   Session cookie is httpOnly — JS never reads it directly.
   All state-changing calls use JSON bodies (never form POST).
   ============================================================ */
'use strict';

/* ---- utilities ---- */
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

const fmtMoney = n => {
  n = Number(n) || 0;
  if (n === 0) return 'Rs 0';
  const sign = n < 0 ? '-' : '';
  const a = Math.abs(n);
  if (a >= 1e9) return `${sign}Rs ${(a/1e9).toFixed(2)} Arab`;
  if (a >= 1e7) return `${sign}Rs ${(a/1e7).toFixed(2)} cr`;
  if (a >= 1e5) return `${sign}Rs ${(a/1e5).toFixed(1)} lac`;
  return `${sign}Rs ${Math.round(a).toLocaleString('en-PK')}`;
};

function toast(msg, kind) {
  const wrap = document.getElementById('p-toasts');
  if (!wrap) return;
  if (wrap.children.length > 3) wrap.firstChild.remove();
  const t = document.createElement('div');
  t.className = 'toast' + (kind === 'err' ? ' err' : '');
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, 3800);
}

const view = () => document.getElementById('p-view');
const render = html => { view().innerHTML = html; };

/* ---- in-memory auth state (name only — jwt is httpOnly cookie) ---- */
let _authedName = null;   // set after successful login/register
let _currentSection = 'record';  // dashboard tab

/* ---- API layer ---- */
const API = {
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    let data;
    try { data = await r.json(); } catch { data = {}; }
    if (!r.ok) throw Object.assign(new Error(data.detail || r.statusText), { status: r.status, data });
    return data;
  },
  async get(path) {
    const r = await fetch(path, { credentials: 'same-origin' });
    let data;
    try { data = await r.json(); } catch { data = {}; }
    if (!r.ok) throw Object.assign(new Error(data.detail || r.statusText), { status: r.status, data });
    return data;
  },
  async patch(path, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = qs ? `${path}?${qs}` : path;
    const r = await fetch(url, { method: 'PATCH', credentials: 'same-origin' });
    let data;
    try { data = await r.json(); } catch { data = {}; }
    if (!r.ok) throw Object.assign(new Error(data.detail || r.statusText), { status: r.status, data });
    return data;
  },
};

/* ================================================================
   AUTH VIEWS
   ================================================================ */

function showAuth(tab = 'login') {
  render(`
    <div class="p-auth-wrap">
      <div class="p-card" id="auth-card">
        <div class="p-security-banner">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none">
            <path d="M10 2L3 5.5V10c0 4.4 3 7.6 7 8.9 4-1.3 7-4.5 7-8.9V5.5L10 2z" stroke="#a07a0a" stroke-width="1.5" stroke-linejoin="round"/>
            <path d="M7 10l2 2 4-4" stroke="#a07a0a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span>This portal uses government data to show your tax record. Your identity is verified against FBR records. <b>v1 — password-only authentication.</b></span>
        </div>

        <div class="p-tabs">
          <button class="p-tab${tab==='login'?' active':''}" id="tab-login" onclick="showAuth('login')">Sign In</button>
          <button class="p-tab${tab==='register'?' active':''}" id="tab-register" onclick="showAuth('register')">Register</button>
        </div>

        ${tab === 'login' ? renderLogin() : renderRegister()}
      </div>
    </div>
  `);
}

function renderLogin() {
  return `
    <p class="p-card-sub">Sign in with the phone number you registered with.</p>
    <form id="login-form" onsubmit="doLogin(event)">
      <div class="p-field">
        <label class="p-label" for="l-phone">Mobile Phone Number</label>
        <input class="p-input" id="l-phone" name="phone" type="tel"
          placeholder="03001234567" maxlength="13" autocomplete="tel" required>
        <div class="p-err-msg" id="l-phone-err"></div>
      </div>
      <div class="p-field">
        <label class="p-label" for="l-pass">Password</label>
        <input class="p-input" id="l-pass" name="password" type="password"
          placeholder="••••••••" autocomplete="current-password" required>
        <div class="p-err-msg" id="l-pass-err"></div>
      </div>
      <div class="p-err-msg" id="l-form-err" style="margin-bottom:10px"></div>
      <button class="p-btn" type="submit" id="login-btn">Sign In</button>
    </form>
  `;
}

function renderRegister() {
  return `
    <p class="p-card-sub">Register with the details from your CNIC to see your record.
    Your identity will be matched against government records.</p>
    <div class="p-notice">
      <b>No NADRA integration in v1.</b> Identity matching is done against FBR, Excise,
      DISCO and Property Registry records only.
      If your name appears in none of those sources, you will not be matched.
    </div>
    <form id="reg-form" onsubmit="doRegister(event)">
      <div class="p-field">
        <label class="p-label" for="r-cnic">CNIC Number <span style="color:var(--mut);font-weight:400">(13 digits, no dashes)</span></label>
        <input class="p-input" id="r-cnic" name="cnic" type="text"
          placeholder="3520112345678" maxlength="15" inputmode="numeric" required>
        <div class="p-err-msg" id="r-cnic-err"></div>
      </div>
      <div class="p-field">
        <label class="p-label" for="r-name">Full Name <span style="color:var(--mut);font-weight:400">(as on CNIC)</span></label>
        <input class="p-input" id="r-name" name="name" type="text"
          placeholder="Muhammad Ahmed Khan" maxlength="120" autocomplete="name" required>
        <div class="p-err-msg" id="r-name-err"></div>
      </div>
      <div class="p-field">
        <label class="p-label" for="r-addr">Known Address</label>
        <input class="p-input" id="r-addr" name="address" type="text"
          placeholder="House 14, Street 5, G-10, Islamabad" maxlength="300"
          autocomplete="street-address" required>
        <div class="p-err-msg" id="r-addr-err"></div>
      </div>
      <div class="p-field">
        <label class="p-label" for="r-phone">Mobile Phone Number</label>
        <input class="p-input" id="r-phone" name="phone" type="tel"
          placeholder="03001234567" maxlength="13" autocomplete="tel" required>
        <div class="p-err-msg" id="r-phone-err"></div>
      </div>
      <div class="p-field">
        <label class="p-label" for="r-pass">Choose Password</label>
        <input class="p-input" id="r-pass" name="password" type="password"
          placeholder="Minimum 8 characters" autocomplete="new-password" minlength="8" required>
        <div class="p-input-hint">At least 8 characters. You will use this to sign in.</div>
        <div class="p-err-msg" id="r-pass-err"></div>
      </div>
      <div class="p-err-msg" id="r-form-err" style="margin-bottom:10px"></div>
      <button class="p-btn" type="submit" id="reg-btn">Submit Registration</button>
    </form>
  `;
}

async function doLogin(e) {
  e.preventDefault();
  const btn = document.getElementById('login-btn');
  const formErr = document.getElementById('l-form-err');
  formErr.classList.remove('show');

  const phone = document.getElementById('l-phone').value.replace(/\D/g,'');
  const password = document.getElementById('l-pass').value;

  if (!/^0[0-9]{10}$/.test(phone)) {
    showFieldErr('l-phone', 'Enter a valid 11-digit Pakistani phone number');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Signing in…';
  try {
    await API.post('/portal/login', { phone, password });
    // Fetch profile to get name
    const profile = await API.get('/portal/me');
    _authedName = profile.name || '';
    showDashboard(profile);
  } catch (err) {
    btn.disabled = false; btn.textContent = 'Sign In';
    if (err.status === 429) {
      formErr.textContent = 'Too many login attempts. Please wait and try again.';
    } else {
      formErr.textContent = 'Invalid phone number or password.';
    }
    formErr.classList.add('show');
  }
}

async function doRegister(e) {
  e.preventDefault();
  const btn = document.getElementById('reg-btn');
  const formErr = document.getElementById('r-form-err');
  formErr.classList.remove('show');

  const cnic    = document.getElementById('r-cnic').value.replace(/\D/g,'');
  const name    = document.getElementById('r-name').value.trim();
  const address = document.getElementById('r-addr').value.trim();
  const phone   = document.getElementById('r-phone').value.replace(/\D/g,'');
  const password= document.getElementById('r-pass').value;

  let valid = true;
  if (cnic.length !== 13)             { showFieldErr('r-cnic', 'CNIC must be exactly 13 digits'); valid = false; }
  if (name.length < 2)                { showFieldErr('r-name', 'Please enter your full name'); valid = false; }
  if (address.length < 5)             { showFieldErr('r-addr', 'Please enter a full address'); valid = false; }
  if (!/^0[0-9]{10}$/.test(phone))   { showFieldErr('r-phone', 'Enter a valid 11-digit number starting with 0'); valid = false; }
  if (password.length < 8)            { showFieldErr('r-pass', 'Password must be at least 8 characters'); valid = false; }
  if (!valid) return;

  btn.disabled = true;
  btn.textContent = 'Verifying identity…';
  try {
    const result = await API.post('/portal/register', { cnic, name, address, phone, password });
    if (result.status === 'success') {
      toast('Account created. Loading your record…');
      const profile = await API.get('/portal/me');
      _authedName = profile.name || '';
      showDashboard(profile);
    } else {
      // pending or no-match — show holding message
      showPending(result.message);
    }
  } catch (err) {
    btn.disabled = false; btn.textContent = 'Submit Registration';
    if (err.status === 429) {
      formErr.textContent = 'Too many registration attempts. Please try again in an hour.';
    } else if (err.status === 503) {
      formErr.textContent = 'Service temporarily unavailable. Please try again later.';
    } else {
      formErr.textContent = 'Something went wrong. Please check your details and try again.';
    }
    formErr.classList.add('show');
  }
}

function showFieldErr(id, msg) {
  const el = document.getElementById(id + '-err');
  if (el) { el.textContent = msg; el.classList.add('show'); }
  const inp = document.getElementById(id);
  if (inp) inp.classList.add('err');
}

function showPending(msg) {
  render(`
    <div class="p-auth-wrap">
      <div class="p-card">
        <div style="text-align:center;margin-bottom:18px">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
            <circle cx="24" cy="24" r="22" stroke="var(--gold)" stroke-width="2.5"/>
            <path d="M24 14v12l7 4" stroke="var(--gold)" stroke-width="2.5" stroke-linecap="round"/>
          </svg>
        </div>
        <div class="p-card-title" style="text-align:center">Registration Received</div>
        <p style="margin:12px 0 20px;font-size:13.5px;color:var(--mut);line-height:1.6;text-align:center">
          ${esc(msg)}
        </p>
        <button class="p-btn secondary" onclick="showAuth('login')">← Back to Sign In</button>
      </div>
    </div>
  `);
}

/* ================================================================
   DASHBOARD
   ================================================================ */

function showDashboard(profile) {
  const tier = tierFromLabel(profile.tier_label);
  render(`
    <div class="p-topbar">
      <div class="p-welcome">Welcome, <b>${esc(profile.name || 'Citizen')}</b></div>
      <button class="p-btn secondary" style="width:auto;padding:6px 16px;font-size:12.5px"
        onclick="doLogout()">Sign Out</button>
    </div>

    <div class="p-nav">
      <button class="p-nav-tab${_currentSection==='record'?' active':''}"
        onclick="switchSection('record')">My Record</button>
      <button class="p-nav-tab${_currentSection==='disputes'?' active':''}"
        onclick="switchSection('disputes')">My Disputes</button>
    </div>

    <div id="p-section-record" style="display:${_currentSection==='record'?'block':'none'}">
      ${renderRecord(profile, tier)}
    </div>
    <div id="p-section-disputes" style="display:${_currentSection==='disputes'?'block':'none'}">
      <div id="disputes-container">${renderDisputesLoading()}</div>
    </div>
  `);

  // Load disputes in background
  loadDisputes();
  // Animate stat bars
  setTimeout(() => {
    document.querySelectorAll('.p-section').forEach(s => s.classList.remove('collapsed'));
  }, 50);
}

function switchSection(name) {
  _currentSection = name;
  ['record','disputes'].forEach(s => {
    const el = document.getElementById('p-section-' + s);
    if (el) el.style.display = s === name ? 'block' : 'none';
  });
  document.querySelectorAll('.p-nav-tab').forEach((t, i) => {
    t.classList.toggle('active', ['record','disputes'][i] === name);
  });
}

function tierFromLabel(label) {
  const map = {
    'referred for review': 'CRITICAL',
    'flagged for review': 'HIGH',
    'may need clarification': 'MEDIUM',
    'minor discrepancies': 'LOW',
    'appears consistent': 'MINIMAL',
  };
  for (const [k, v] of Object.entries(map)) {
    if (label && label.toLowerCase().includes(k)) return v;
  }
  return 'MINIMAL';
}

function renderRecord(p, tier) {
  const hasBank = (p.n_accounts || 0) > 0;
  const hasTravel = (p.n_intl_trips || 0) > 0;
  return `
    <div class="p-dash-header">
      <div>
        <div class="p-dash-name">${esc(p.name)}</div>
        <div class="p-dash-status ${tier}" title="Compliance status">${esc(p.tier_label)}</div>
      </div>
    </div>

    <div class="p-sections">

      <!-- Summary -->
      <div class="p-section" id="sec-summary">
        <div class="p-section-head" onclick="toggleSection('sec-summary')">
          <span class="p-section-title">Summary</span>
          <span class="p-section-toggle">▾</span>
        </div>
        <div class="p-section-body">
          <p style="font-size:14px;line-height:1.65;color:var(--ink)">${esc(p.summary)}</p>
        </div>
      </div>

      <!-- Financial Overview -->
      <div class="p-section" id="sec-finance">
        <div class="p-section-head" onclick="toggleSection('sec-finance')">
          <span class="p-section-title">Financial Overview</span>
          <span class="p-section-toggle">▾</span>
        </div>
        <div class="p-section-body">
          <div class="p-stat-grid">
            <div class="p-stat">
              <div class="p-stat-label">FBR Filer Status</div>
              <div class="p-stat-value" style="font-size:13px">${esc(p.filer || '—')}</div>
            </div>
            <div class="p-stat">
              <div class="p-stat-label">Declared Income</div>
              <div class="p-stat-value">${fmtMoney(p.declared_income)}</div>
            </div>
            <div class="p-stat">
              <div class="p-stat-label">Lifestyle-Implied</div>
              <div class="p-stat-value">${fmtMoney(p.lifestyle_income)}</div>
            </div>
            <div class="p-stat">
              <div class="p-stat-label">Vehicles</div>
              <div class="p-stat-value">${p.n_vehicles || 0}</div>
            </div>
            <div class="p-stat">
              <div class="p-stat-label">Properties</div>
              <div class="p-stat-value">${p.n_properties || 0}</div>
            </div>
            <div class="p-stat">
              <div class="p-stat-label">Avg Electricity Bill</div>
              <div class="p-stat-value">${p.avg_bill ? fmtMoney(p.avg_bill) + '/mo' : '—'}</div>
            </div>
            ${hasBank ? `
            <div class="p-stat">
              <div class="p-stat-label">Bank Accounts</div>
              <div class="p-stat-value">${p.n_accounts}</div>
            </div>` : ''}
            ${hasTravel ? `
            <div class="p-stat">
              <div class="p-stat-label">Intl Trips</div>
              <div class="p-stat-value">${p.n_intl_trips}</div>
            </div>` : ''}
            <div class="p-stat">
              <div class="p-stat-label">Data Sources</div>
              <div class="p-stat-value">${p.n_sources}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Evidence Records -->
      <div class="p-section" id="sec-evidence">
        <div class="p-section-head" onclick="toggleSection('sec-evidence')">
          <span class="p-section-title">Government Records Linked to You (${(p.evidence||[]).length})</span>
          <span class="p-section-toggle">▾</span>
        </div>
        <div class="p-section-body">
          ${renderEvidence(p.evidence || [])}
        </div>
      </div>

      <!-- Timeline -->
      ${p.timeline && p.timeline.length ? `
      <div class="p-section" id="sec-timeline">
        <div class="p-section-head" onclick="toggleSection('sec-timeline')">
          <span class="p-section-title">Asset History Timeline</span>
          <span class="p-section-toggle">▾</span>
        </div>
        <div class="p-section-body">
          ${renderTimeline(p.timeline)}
        </div>
      </div>` : ''}

    </div>
  `;
}

function renderEvidence(evidence) {
  if (!evidence.length) return '<p style="color:var(--mut);font-size:13px">No linked records found.</p>';
  return `<div class="p-evidence-list">
    ${evidence.map((e, i) => `
      <div class="p-evidence-item">
        <div class="p-evidence-source">${esc(e.source)}</div>
        <div class="p-evidence-finding">${esc(e.finding)}</div>
        <div class="p-evidence-meta">Source: ${esc(e.source_file)} · Row ${esc(String(e.row_number))} · ID: ${esc(e.record_id)}</div>
        <button class="p-dispute-btn" onclick="openDisputeModal(${i},${JSON.stringify(JSON.stringify(e))})"
          id="dispute-btn-${i}" title="Dispute this record">Dispute ↗</button>
      </div>
    `).join('')}
  </div>`;
}

function renderTimeline(events) {
  const sorted = [...events].sort((a, b) => (a.year||'').localeCompare(b.year||''));
  return `<div class="p-timeline">
    ${sorted.map(ev => `
      <div class="p-tl-item">
        <div class="p-tl-dot ${esc(ev.kind||'')}"></div>
        <div class="p-tl-year">${esc(ev.year)}</div>
        <div class="p-tl-label">${esc(ev.label)}</div>
      </div>
    `).join('')}
  </div>`;
}

function toggleSection(id) {
  document.getElementById(id)?.classList.toggle('collapsed');
}

/* ================================================================
   DISPUTE MODAL
   ================================================================ */

let _disputeEvidence = null;

function openDisputeModal(idx, evJson) {
  _disputeEvidence = JSON.parse(evJson);
  const e = _disputeEvidence;

  const overlay = document.createElement('div');
  overlay.className = 'p-modal-overlay';
  overlay.id = 'dispute-modal';
  overlay.innerHTML = `
    <div class="p-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="p-modal-head">
        <div class="p-modal-title" id="modal-title">Dispute a Record</div>
        <button class="p-modal-close" onclick="closeDisputeModal()" aria-label="Close">✕</button>
      </div>
      <div class="p-modal-body">
        <div class="p-notice" style="margin-bottom:16px">
          <b>Record being disputed:</b><br>
          <span style="font-family:var(--mono);font-size:12px">[${esc(e.source)}]</span>
          ${esc(e.finding)}
        </div>

        <div class="p-field">
          <label class="p-label">Reason for dispute</label>
          <div class="p-radio-group" id="dispute-cats">
            ${[
              ['not_my_record',     'This record does not belong to me'],
              ['data_incorrect',    'The information in this record is incorrect'],
              ['already_corrected', 'I have already corrected this with the relevant department'],
            ].map(([val, label]) => `
              <label class="p-radio-label" onclick="selectCat(this)">
                <input type="radio" name="disp-cat" value="${val}"> ${esc(label)}
              </label>
            `).join('')}
          </div>
          <div class="p-err-msg" id="modal-cat-err"></div>
        </div>

        <div class="p-field">
          <label class="p-label" for="disp-explain">Explanation <span style="color:var(--mut);font-weight:400">(required, 10–1000 characters)</span></label>
          <textarea class="p-input" id="disp-explain" rows="4" maxlength="1000"
            placeholder="Please explain why you believe this record is incorrect or does not belong to you."></textarea>
          <div class="p-err-msg" id="modal-exp-err"></div>
        </div>

        <div class="p-notice warn" style="font-size:12.5px">
          Submitting a dispute does <b>not</b> change your record immediately.
          An FBR officer will review your claim and respond within 15 working days.
        </div>
      </div>
      <div class="p-modal-foot">
        <button class="p-btn" id="dispute-submit-btn" onclick="submitDispute(${idx})">Submit Dispute</button>
        <button class="p-btn secondary" onclick="closeDisputeModal()">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', ev => { if (ev.target === overlay) closeDisputeModal(); });
  document.getElementById('disp-explain')?.focus();
}

function selectCat(label) {
  document.querySelectorAll('.p-radio-label').forEach(l => l.classList.remove('selected'));
  label.classList.add('selected');
}

function closeDisputeModal() {
  document.getElementById('dispute-modal')?.remove();
  _disputeEvidence = null;
}

async function submitDispute(idx) {
  const e = _disputeEvidence;
  if (!e) return;

  const catEl = document.querySelector('input[name="disp-cat"]:checked');
  const explain = document.getElementById('disp-explain')?.value.trim() || '';

  const catErr = document.getElementById('modal-cat-err');
  const expErr = document.getElementById('modal-exp-err');
  catErr.classList.remove('show'); expErr.classList.remove('show');

  let valid = true;
  if (!catEl) { catErr.textContent = 'Please select a reason'; catErr.classList.add('show'); valid = false; }
  if (explain.length < 10) { expErr.textContent = 'Please provide at least 10 characters'; expErr.classList.add('show'); valid = false; }
  if (!valid) return;

  const btn = document.getElementById('dispute-submit-btn');
  btn.disabled = true; btn.textContent = 'Submitting…';

  try {
    const result = await API.post('/portal/dispute', {
      source: e.source,
      record_id: e.record_id,
      finding: e.finding,
      category: catEl.value,
      explanation: explain,
    });
    closeDisputeModal();
    toast(result.message || 'Dispute submitted successfully.');
    // Disable the dispute button for this item
    const dispBtn = document.getElementById(`dispute-btn-${idx}`);
    if (dispBtn) { dispBtn.textContent = '✓ Disputed'; dispBtn.disabled = true; }
    // Refresh dispute tab in background
    loadDisputes();
  } catch (err) {
    btn.disabled = false; btn.textContent = 'Submit Dispute';
    if (err.status === 429) {
      toast('Daily dispute limit reached. Please try again tomorrow.', 'err');
    } else if (err.status === 400) {
      toast('This record cannot be disputed — it may not belong to your profile.', 'err');
    } else {
      toast('Failed to submit dispute. Please try again.', 'err');
    }
  }
}

/* ================================================================
   MY DISPUTES TAB
   ================================================================ */

function renderDisputesLoading() {
  return `<div class="p-spinner"><div class="p-spin"></div>Loading your disputes…</div>`;
}

async function loadDisputes() {
  const container = document.getElementById('disputes-container');
  if (!container) return;
  try {
    const data = await API.get('/portal/me/disputes');
    container.innerHTML = renderDisputeList(data.disputes || []);
  } catch (err) {
    if (err.status === 401) { boot(); return; }
    if (container) container.innerHTML = '<p style="color:var(--mut);font-size:13px">Could not load disputes.</p>';
  }
}

function renderDisputeList(disputes) {
  if (!disputes.length) return `
    <div class="p-notice info">
      You have not submitted any disputes yet. You can dispute individual records
      on the <b>My Record</b> tab.
    </div>
  `;
  return `<div class="p-dispute-list">
    ${disputes.map(d => `
      <div class="p-dispute-row">
        <div class="p-dispute-row-head">
          <span class="p-disp-status ${esc(d.status)}">${esc(d.status.replace('_',' '))}</span>
          <span style="font-size:12px;color:var(--mut)">#${d.id} · ${esc(d.created_at?.slice(0,10)||'')}</span>
        </div>
        <div style="font-size:13.5px;color:var(--ink);margin-bottom:4px">${esc(d.finding)}</div>
        <div style="font-size:12px;color:var(--mut)">
          Source: ${esc(d.source)} · Category: ${esc(d.category?.replace(/_/g,' ')||'')}
        </div>
        ${d.auditor_note ? `<div class="p-notice" style="margin-top:10px;margin-bottom:0;font-size:12.5px">
          <b>FBR response:</b> ${esc(d.auditor_note)}
        </div>` : ''}
      </div>
    `).join('')}
  </div>`;
}

/* ================================================================
   LOGOUT
   ================================================================ */

async function doLogout() {
  try { await API.post('/portal/logout', {}); } catch {}
  _authedName = null;
  toast('You have been signed out.');
  showAuth('login');
}

/* ================================================================
   BOOT — check if already authed
   ================================================================ */

async function boot() {
  render(`<div class="p-spinner"><div class="p-spin"></div>Loading…</div>`);
  try {
    const profile = await API.get('/portal/me');
    _authedName = profile.name || '';
    showDashboard(profile);
  } catch (err) {
    if (err.status === 401 || err.status === 403) {
      showAuth('login');
    } else if (err.status === 503) {
      render(`<div class="p-auth-wrap"><div class="p-card">
        <div class="p-card-title">Service Unavailable</div>
        <p class="p-card-sub">The pipeline output is not available. Please run
          <code>python pipeline/run_pipeline.py</code> first.</p>
        <button class="p-btn secondary" onclick="boot()">Retry</button>
      </div></div>`);
    } else {
      showAuth('login');
    }
  }
}

boot();
