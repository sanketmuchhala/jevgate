'use strict';

// JevGate Reviewer Workbench: a single page over the /v1/cases contract in src/workbench_schemas.py.
// No build step and no dependencies. Document and model text is always inserted as text nodes,
// never as HTML, so a document cannot inject markup into the page.

const API = '/v1';
const DEFAULT_OFFICES = [
  'security_desk', 'records_officer', 'mission_lead', 'privacy_office', 'finance_approver', 'legal_counsel', 'protocol_office',
];
const STATUS_LABELS = {
  OPEN: 'Open',
  CHANGES_REQUESTED: 'Changes requested',
  APPROVED_BY_HUMAN: 'Approved by human',
  BLOCKED_FINAL: 'Blocked (final)',
  CLEARED_BY_MACHINE: 'Cleared by machine',
};
const VERDICT_LABELS = { BLOCKED: 'Blocked', ROUTED: 'Routed', HUMAN_REVIEW: 'Human review', CLEARED: 'Cleared' };
const DECIDED_BY_LABELS = { SYSTEM_ONE: 'System-1', SYSTEM_TWO: 'System-2', PENDING_HUMAN: 'awaiting a person' };
const RESOLVED_BY_LABELS = { SYSTEM_ONE: 'System-1', SYSTEM_TWO: 'System-2', USER_CONTEXT: 'Submitter context' };
const DISPOSITIONS = {
  ACCEPT_FINDING: { label: 'Accept finding', hint: 'The risk is real. Accepting a BLOCK finding blocks the case for good.' },
  FALSE_POSITIVE_OVERRIDE: { label: 'False positive override', hint: 'The rule fired, but the risk is not present in this document.' },
  WAIVE_RISK: { label: 'Waive risk', hint: 'The risk is present and accepted for this release, on your authority.' },
};
const DISPOSITION_LABELS = {
  PENDING: 'Pending', ACCEPT_FINDING: 'Finding accepted', FALSE_POSITIVE_OVERRIDE: 'False positive', WAIVE_RISK: 'Risk waived',
  CARRIED_FORWARD: 'Carried forward',
};
const DISCARD_LABELS = {
  HIGH_STAKES_NO: 'A reviewer "no" cannot clear a high-stakes rule.',
  UNGROUNDED_YES: 'The "yes" did not quote the document verbatim.',
  UNKNOWN_ID: 'No open proposition has this ID.',
  DUPLICATE: 'Only the first verdict per proposition counts.',
};

// Same text as eval-01 to eval-06 in samples/eval_corpus.jsonl, so offline results and the workbench can be compared.
const PRESETS = [
  {
    "id": "eval-01-mission-aide-memoire-route",
    "title": "Country Mission Aide-Mémoire with Delegation Route",
    "document": "To: Office of Protocol, Ministry of Finance and Economic Planning, Republic of Calder\nSubject: Annex 2 to the Aide-Mémoire - mission logistics, 11-15 November\n\nAs requested by your security service, the movements of the mission team are set out below.\nMission leader Dr. H. Onstad and three team members arrive on flight CL 802 at 09:45 on 11 November.\nMotorcade route: airport ring road, east causeway, then Kestrel Avenue to the Grand Meridian Hotel (rooms 712-715).\nClose protection: officer T. Vasko travels in the lead vehicle. The team departs 15 November at 18:20.",
    "context": {
      "intended_audience": "partner_government",
      "release_requested": null,
      "purpose": "Coordinate mission arrival with the host security service",
      "proposed_action": "share_with_ministry",
      "source_unit": "country_mission"
    }
  },
  {
    "id": "eval-02-prior-action-red-lines",
    "title": "Sovereign Financing Prior-Action Negotiating Red Lines",
    "document": "DRAFT - Letter to the Permanent Secretary, Ministry of Finance, Republic of Calder\nRe: Development policy financing, second tranche - prior actions\n\nAhead of Thursday's negotiation, we set out the institution's position on the prior actions.\nOur red line is the fuel-subsidy reform: the tranche cannot be released unless the pricing formula is adopted by Cabinet.\nIf needed, our fallback is to accept a phased adoption over two quarters, but not a reduction below 60 percent cost recovery.\nPlease treat this letter as the basis for Thursday's discussion.",
    "context": {
      "intended_audience": "partner_government",
      "release_requested": null,
      "purpose": "Prepare the ministry for Thursday's prior-action negotiation",
      "proposed_action": "share_with_ministry",
      "source_unit": "macro_fiscal"
    }
  },
  {
    "id": "eval-03-press-release-minister",
    "title": "Public Press Release Naming Host Ministry Official",
    "document": "FOR IMMEDIATE RELEASE\nInstitution and Republic of Calder launch coastal resilience partnership\n\nPORT ELIN, 24 October - A new partnership will provide technical support for flood defences in 40 coastal communities.\n\"Families on the coast will feel the difference within two seasons,\" said Minister of Environment Mira Castellan, who launched the partnership at the Port Elin harbour.\nMedia enquiries: press office, main switchboard.",
    "context": {
      "intended_audience": "public_release",
      "release_requested": true,
      "purpose": "Announce the partnership to the press",
      "proposed_action": "public_disclosure",
      "source_unit": "communications"
    }
  },
  {
    "id": "eval-04-step-payment-minutes",
    "title": "Procurement STEP Payment Release Minutes",
    "document": "Minutes - Bid Evaluation Committee, contract CAL-WS-RFB-014 (recorded in STEP)\n21 October\n\nPresent: P. Venn (chair), L. Bertrand, A. Quist.\nThe committee confirmed Norvale Logistics as the lowest evaluated substantially responsive bidder.\nDecision: the institution will release the advance payment of USD 312,000 to Norvale Logistics on 28 October, against the bank guarantee received on 19 October.\nThe no-objection is recorded in STEP. Next meeting: 4 November.",
    "context": {
      "intended_audience": "internal_team",
      "release_requested": null,
      "purpose": "Record the committee decision and release the advance payment",
      "proposed_action": "release_payment",
      "source_unit": "procurement"
    }
  },
  {
    "id": "eval-05-field-laptop-breach",
    "title": "Field Office Laptop Breach Incident",
    "document": "SECURITY INCIDENT REPORT - Calder field office - filed 24 October, 07:50\n\nAt approximately 23:15 on 23 October a staff laptop was stolen from a locked vehicle outside the guesthouse.\nThe device holds the beneficiary registry extract for the cash-transfer pilot, including names, national ID numbers and household locations.\nBitLocker is enabled, but the device was suspended rather than shut down. VPN certificate not yet revoked.\nPolice case number CPD-90417. Requesting immediate instructions from the security desk.",
    "context": {
      "intended_audience": "internal_team",
      "release_requested": null,
      "purpose": "Report a stolen device to the security desk",
      "proposed_action": "incident_report",
      "source_unit": "field_office_calder"
    }
  },
  {
    "id": "eval-06-declassification-request",
    "title": "Archival Declassification Request for Restricted Record",
    "document": "Archives Access Request AR-2026-118\n\nRequester: Dr. L. Moreau, University of Port Elin (external researcher).\nRecord requested: Security Risk Assessment, Calder Country Office, March 2019 (classification: Strictly Confidential), including the annex on evacuation routes and safe-house locations.\nThe requester asks that the full record, with annexes, be released to her for publication in a peer-reviewed study.\nPlease advise whether the record can be declassified and released.",
    "context": {
      "intended_audience": "institution_wide",
      "release_requested": true,
      "purpose": "Decide an external researcher's access request",
      "proposed_action": "declassify_and_release",
      "source_unit": "archives"
    }
  }
];

const state = {
  policy: null,
  questions: {},
  offices: DEFAULT_OFFICES,
  cases: [],
  queueError: null,
  current: null,
  editing: false,
  serverSamples: [],
};

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

const $ = (selector, root = document) => root.querySelector(selector);

const storage = {
  get(key, area = 'local') {
    try { return window[`${area}Storage`].getItem(key); } catch { return null; }
  },
  set(key, value, area = 'local') {
    try {
      if (value === null || value === undefined || value === '') window[`${area}Storage`].removeItem(key);
      else window[`${area}Storage`].setItem(key, value);
    } catch { /* storage blocked: the value lasts for this page only */ }
  },
};

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key === 'style') for (const [name, v] of Object.entries(value)) el.style.setProperty(name, String(v));
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === '') continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const clamp01 = value => Math.min(1, Math.max(0, Number(value) || 0));
const fixed = value => clamp01(value).toFixed(2);
const shortId = id => String(id).slice(0, 8);
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

function formatDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso || '') : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

const prefersReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

function pulse(elements) {
  for (const el of elements) {
    el.classList.remove('pulse');
    void el.offsetWidth; // restart the animation
    el.classList.add('pulse');
    setTimeout(() => el.classList.remove('pulse'), 1800);
  }
}

function scrollToElement(el) {
  el.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'center' });
}

function toast(message, kind = 'info') {
  const el = h('div', { class: `toast toast-${kind}`, role: kind === 'error' ? 'alert' : null },
    h('span', { class: 'toast-text' }, message),
    h('button', { type: 'button', class: 'toast-close', 'aria-label': 'Dismiss', onclick: () => el.remove() }, '×'));
  const region = $('#toasts');
  region.append(el);
  while (region.children.length > 3) region.firstElementChild.remove(); // newest three only
  setTimeout(() => el.remove(), kind === 'error' ? 10000 : 5000);
}

async function withBusy(button, busyLabel, task) {
  const label = button?.textContent;
  if (button) { button.disabled = true; button.textContent = busyLabel; }
  try {
    return await task();
  } finally {
    if (button?.isConnected) { button.disabled = false; button.textContent = label; }
  }
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function describeError(status, data) {
  const detail = data && typeof data === 'object' ? data.detail : data;
  if (Array.isArray(detail)) {
    return detail.map(d => `${(d.loc || []).filter(p => p !== 'body').join('.') || 'request'}: ${d.msg}`).join('; ');
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (status === 404) return 'Not found.';
  return `Request failed (HTTP ${status}).`;
}

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const token = storage.get('jevgate.token', 'session');
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'The JevGate server could not be reached.');
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!response.ok) {
    if (response.status === 401) requestToken();
    throw new ApiError(response.status, describeError(response.status, data));
  }
  return data;
}

const casePath = (caseId, suffix = '') => `${API}/cases/${encodeURIComponent(caseId)}${suffix}`;

// ---------------------------------------------------------------------------
// Policy reference data
// ---------------------------------------------------------------------------

const policyRule = ruleId => state.policy?.rules?.find(r => r.rule_id === ruleId) || null;
const bandFor = stakes => state.policy?.bands?.[stakes] || null;

function conditionSignalType(conditionId) {
  const signal = state.policy?.conditions?.[conditionId]?.signal;
  return signal ? state.questions?.[signal]?.type || null : null;
}

// Only yes/no (noul) conditions can be quoted; audience and sensitivity are read from Jev's distributions.
function isQuotable(conditionId) {
  const type = conditionSignalType(conditionId);
  return type === null || type === 'noul';
}

function officesFromPolicy(policy) {
  const offices = [...new Set((policy?.rules || []).map(r => r.office))];
  return offices.length ? offices : DEFAULT_OFFICES;
}

async function loadReference() {
  const [policy, questions] = await Promise.allSettled([api(`${API}/policy`), api(`${API}/questions`)]);
  if (policy.status === 'fulfilled') state.policy = policy.value;
  else toast(`Policy could not be loaded: ${policy.reason.message}`, 'error');
  if (questions.status === 'fulfilled') state.questions = questions.value || {};
  state.offices = officesFromPolicy(state.policy);

  const chip = $('#policy-chip');
  chip.textContent = state.policy ? `Policy ${state.policy.policy_id} · ${state.policy.version}` : 'Policy unavailable';
  chip.disabled = !state.policy;

  const saved = storage.get('jevgate.office');
  fillOfficeSelect($('#acting-office'), { selected: state.offices.includes(saved) ? saved : state.offices[0] });
  fillOfficeSelect($('#filters').elements.office, { all: 'All offices' });
}

function fillOfficeSelect(select, { selected = '', all = null, none = null } = {}) {
  const options = [];
  if (all) options.push(h('option', { value: '' }, all));
  if (none) options.push(h('option', { value: '' }, none));
  for (const office of state.offices) options.push(h('option', { value: office }, office));
  select.replaceChildren(...options);
  select.value = selected || '';
}

// ---------------------------------------------------------------------------
// Reviewer identity
// ---------------------------------------------------------------------------

function setupIdentity() {
  const handle = $('#reviewer-handle');
  handle.value = storage.get('jevgate.reviewer') || '';
  handle.addEventListener('input', () => storage.set('jevgate.reviewer', handle.value.trim()));
  $('#acting-office').addEventListener('change', event => {
    storage.set('jevgate.office', event.target.value);
    if (state.current && !state.editing) renderCase();
  });
  $('#identity-form').addEventListener('submit', event => event.preventDefault());
}

function identity() {
  return { reviewer: $('#reviewer-handle').value.trim(), office: $('#acting-office').value };
}

function requireIdentity() {
  const id = identity();
  if (!id.reviewer) {
    toast('Set your reviewer handle in the top bar first.', 'error');
    $('#reviewer-handle').focus();
    return null;
  }
  return id;
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

function setupFilters() {
  const form = $('#filters');
  form.addEventListener('change', renderQueue);
  form.addEventListener('submit', event => event.preventDefault());
  $('#refresh-button').addEventListener('click', event => withBusy(event.currentTarget, 'Refreshing…', refreshQueue));
}

async function refreshQueue() {
  try {
    const data = await api(`${API}/cases`);
    state.cases = Array.isArray(data) ? data : data?.cases || data?.items || [];
    state.queueError = null;
  } catch (error) {
    state.queueError = error;
  }
  renderQueue();
}

function filteredCases() {
  const f = $('#filters').elements;
  return state.cases.filter(c =>
    (!f.status.value || c.status === f.status.value)
    && (!f.verdict.value || c.machine_verdict === f.verdict.value)
    && (!f.office.value || [c.assigned_office, ...(c.offices || []), ...(c.review_offices || [])].includes(f.office.value))
    && (!f.expedite.checked || c.priority === 'EXPEDITE'));
}

function renderQueue() {
  const list = $('#case-list');
  const count = $('#queue-count');
  if (state.queueError) {
    count.textContent = '';
    list.replaceChildren(h('li', { class: 'queue-message error' }, `Queue unavailable: ${state.queueError.message}`));
    return;
  }
  const cases = filteredCases();
  count.textContent = cases.length === state.cases.length
    ? plural(state.cases.length, 'case')
    : `${cases.length} of ${plural(state.cases.length, 'case')}`;
  if (!cases.length) {
    list.replaceChildren(h('li', { class: 'queue-message' },
      state.cases.length ? 'No cases match these filters.' : 'No cases yet. Submit a document to start the queue.'));
    return;
  }
  list.replaceChildren(...cases.map(caseItem));
}

function caseItem(c) {
  const selected = state.current?.case_id === c.case_id;
  const offices = c.offices?.length ? c.offices.join(', ') : 'no office';
  const review = c.review_offices?.length ? ` · review: ${c.review_offices.join(', ')}` : '';
  return h('li', {},
    h('button', {
      type: 'button', class: 'case-item', 'aria-current': selected ? 'true' : null,
      onclick: () => navigate(c.case_id),
    },
    h('span', { class: 'case-item-top' },
      h('span', { class: 'case-item-title' }, c.title),
      h('span', { class: 'version-chip' }, `v${c.current_version}`)),
    h('span', { class: 'badge-row' },
      verdictBadge(c.machine_verdict, c.decided_by),
      statusBadge(c.status),
      c.priority === 'EXPEDITE' && expediteFlag()),
    riskMeter(c.risk_index),
    h('span', { class: 'case-item-meta' }, `${offices}${review}`),
    c.assigned_reviewer || c.assigned_office
      ? h('span', { class: 'case-item-meta' }, `Assigned: ${[c.assigned_office, c.assigned_reviewer].filter(Boolean).join(' · ')}`)
      : null));
}

// ---------------------------------------------------------------------------
// Badges
// ---------------------------------------------------------------------------

function verdictBadge(verdict, decidedBy) {
  return h('span', { class: `verdict verdict-${verdict}`, title: 'Machine verdict' },
    h('span', { class: 'kicker' }, 'Machine'),
    h('span', { class: 'verdict-label' }, VERDICT_LABELS[verdict] || verdict),
    decidedBy && h('span', { class: 'decided-by' }, DECIDED_BY_LABELS[decidedBy] || decidedBy));
}

function statusBadge(status) {
  return h('span', { class: `status status-${status}`, title: 'Human disposition status' },
    h('span', { class: 'kicker' }, 'Human'),
    STATUS_LABELS[status] || status);
}

const expediteFlag = () => h('span', { class: 'flag-expedite', title: 'P(urgency ≥ 2) is at least the policy threshold' }, 'Expedite');

function truthBadge(truth) {
  return h('span', { class: `truth truth-${truth}` }, truth === 'UNCERTAIN' ? 'Uncertain' : truth === 'TRUE' ? 'True' : 'False');
}

function riskMeter(risk) {
  const value = clamp01(risk);
  return h('span', { class: 'risk', title: 'Risk index orders the queue; it never changes a verdict' },
    h('span', { class: 'risk-label' }, 'Risk'),
    h('span', {
      class: 'risk-track', role: 'meter', 'aria-label': 'Risk index', 'aria-valuemin': '0', 'aria-valuemax': '1',
      'aria-valuenow': value.toFixed(2),
    }, h('span', { class: 'risk-fill', style: { '--risk': value } })),
    h('span', { class: 'risk-value' }, value.toFixed(2)));
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function hashFor(caseId, version) {
  return `#case=${encodeURIComponent(caseId)}${version ? `&v=${version}` : ''}`;
}

function navigate(caseId, version = null) {
  const hash = hashFor(caseId, version);
  if (location.hash === hash) openFromHash();
  else location.hash = hash;
}

function syncHash(detail) {
  const version = detail.active_version.version_number;
  const hash = hashFor(detail.case_id, version === detail.current_version ? null : version);
  if (location.hash !== hash) history.replaceState(null, '', hash);
}

function openFromHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const caseId = params.get('case');
  if (!caseId) {
    state.current = null;
    renderEmpty();
    renderQueue();
    return;
  }
  loadCase(caseId, Number(params.get('v')) || null);
}

async function loadCase(caseId, version) {
  const view = $('#case-view');
  view.setAttribute('aria-busy', 'true');
  try {
    const detail = await api(casePath(caseId, version ? `?version=${version}` : ''));
    showCase(detail);
  } catch (error) {
    state.current = null;
    view.replaceChildren(h('div', { class: 'empty-state' }, h('h2', {}, 'Case unavailable'), h('p', {}, error.message)));
    renderQueue();
  } finally {
    view.removeAttribute('aria-busy');
  }
}

function showCase(detail) {
  const changedCase = state.current?.case_id !== detail.case_id;
  state.current = detail;
  state.editing = false;
  syncHash(detail);
  renderCase();
  renderQueue();
  if (changedCase) $('#case-view').scrollTop = 0;
}

function renderEmpty() {
  $('#case-view').replaceChildren(h('div', { class: 'empty-state' },
    h('h2', {}, 'No case selected'),
    h('p', {}, 'Pick a case from the queue, or submit a document for triage.'),
    h('button', { type: 'button', class: 'btn primary', onclick: openNewCase }, 'New case')));
}

// ---------------------------------------------------------------------------
// Case view
// ---------------------------------------------------------------------------

function renderCase() {
  const c = state.current;
  const v = c.active_version;
  const isCurrent = v.version_number === c.current_version;
  const view = $('#case-view');
  view.replaceChildren(); // replaceChildren(null) would insert the text "null"
  append(view, [
    caseHeader(c, v, isCurrent),
    alertBanner(v),
    h('div', { class: 'split' }, documentPane(c, v, isCurrent), rulesPane(c, v, isCurrent)),
  ]);
}

function caseHeader(c, v, isCurrent) {
  const d = v.decision;
  const assigned = [c.assigned_office, c.assigned_reviewer].filter(Boolean).join(' · ');
  return h('header', { class: 'case-head' },
    h('div', { class: 'case-head-main' },
      h('h2', { class: 'case-title', id: 'case-title' }, c.title),
      h('p', { class: 'case-sub' },
        `Case ${shortId(c.case_id)} · created ${formatDate(c.created_at)} · updated ${formatDate(c.updated_at)}`,
        assigned && ` · assigned to ${assigned}`)),
    h('div', { class: 'badge-row case-head-badges' },
      verdictBadge(d.verdict, d.decided_by),
      statusBadge(c.status),
      d.priority === 'EXPEDITE' && expediteFlag(),
      riskMeter(d.risk_index),
      h('span', { class: 'version-chip' }, isCurrent ? `v${v.version_number}` : `v${v.version_number} of ${c.current_version}`)),
    h('dl', { class: 'route-summary' },
      h('div', {}, h('dt', {}, 'Offices'), h('dd', {}, d.offices?.length ? d.offices.join(', ') : 'none')),
      h('div', {}, h('dt', {}, 'Open for review'), h('dd', {}, d.review_offices?.length ? d.review_offices.join(', ') : 'none')),
      h('div', {}, h('dt', {}, 'Models'), h('dd', {}, [d.model || 'System-1 unavailable', d.system_two?.model].filter(Boolean).join(' · ')))));
}

function alertBanner(v) {
  const warnings = v.context_warnings || [];
  const escalations = v.decision.escalations || [];
  if (!warnings.length && !escalations.length) return null;
  const title = warnings.length && escalations.length ? 'Context mismatch and escalations'
    : warnings.length ? 'Context mismatch' : 'Escalated to a person';
  return h('section', { class: 'alert-banner', role: 'alert', 'aria-labelledby': 'alert-title' },
    h('h3', { id: 'alert-title' }, title),
    warnings.length > 0 && h('ul', { class: 'alert-list' }, warnings.map(w => h('li', { class: 'warning' }, w))),
    escalations.length > 0 && h('ul', { class: 'alert-list' }, escalations.map(e => h('li', {},
      h('code', {}, e.code),
      e.rule_id && [' ', h('button', { type: 'button', class: 'link', onclick: () => focusRuleCard(e.rule_id) }, e.rule_id)],
      e.condition_id && [' · ', h('code', {}, e.condition_id)],
      `: ${e.detail}`))));
}

// --- Document pane ---------------------------------------------------------

function documentPane(c, v, isCurrent) {
  const versions = c.version_history?.length
    ? c.version_history
    : Array.from({ length: c.current_version }, (_, i) => ({ version_number: i + 1 }));
  const characters = Array.from(v.document).length;
  return h('section', { class: 'pane doc-pane', 'aria-labelledby': 'doc-heading' },
    h('div', { class: 'pane-head' },
      h('h3', { id: 'doc-heading' }, 'Document'),
      h('div', { class: 'versions', role: 'group', 'aria-label': 'Version history' },
        versions.map(item => h('button', {
          type: 'button', class: 'version-button', 'aria-pressed': String(item.version_number === v.version_number),
          title: [item.created_at && formatDate(item.created_at), item.change_note].filter(Boolean).join(' · ') || null,
          onclick: () => navigate(c.case_id, item.version_number === c.current_version ? null : item.version_number),
        }, `v${item.version_number}`))),
      isCurrent && !state.editing && h('button', {
        type: 'button', class: 'btn small', onclick: () => { state.editing = true; renderCase(); $('#revision-document')?.focus(); },
      }, `Edit & submit revision (v${c.current_version + 1})`)),
    !isCurrent && h('p', { class: 'notice' },
      `You are viewing v${v.version_number}. Dispositions and revisions are made on the current version, v${c.current_version}. `,
      h('button', { type: 'button', class: 'link', onclick: () => navigate(c.case_id) }, 'Go to current version')),
    v.change_note && h('p', { class: 'change-note' }, h('strong', {}, `v${v.version_number} change note: `), v.change_note),
    contextSummary(v.context),
    state.editing && isCurrent ? revisionForm(c, v) : documentViewer(v),
    h('p', { class: 'doc-foot' },
      `SHA-256 ${String(v.document_sha256).slice(0, 12)}… · ${characters.toLocaleString()} characters · `,
      plural((v.evidence_spans || []).length, 'evidence span')));
}

function contextSummary(context = {}) {
  const release = context.release_requested === true ? 'Yes' : context.release_requested === false ? 'No' : 'Infer';
  const items = [
    ['Audience', context.intended_audience === 'infer' || !context.intended_audience ? 'Infer' : context.intended_audience],
    ['Release requested', release],
    ['Proposed action', context.proposed_action],
    ['Source unit', context.source_unit],
    ['Purpose', context.purpose],
  ].filter(([, value]) => value);
  return h('dl', { class: 'context-summary', 'aria-label': 'Submission context' },
    items.map(([label, value]) => h('div', {}, h('dt', {}, label), h('dd', {}, value))));
}

/**
 * Split `text` at every span boundary. Each segment lists the spans covering it, so
 * overlapping spans become nested-free runs. Offsets come from Python and count code
 * points, so the text is indexed with Array.from rather than UTF-16 string indices.
 */
function segmentDocument(text, spans) {
  const chars = Array.from(text);
  const n = chars.length;
  const valid = (spans || []).filter(s => Number.isInteger(s.start_char) && Number.isInteger(s.end_char)
    && s.start_char >= 0 && s.start_char < s.end_char && s.end_char <= n);
  const cuts = [...new Set([0, n, ...valid.flatMap(s => [s.start_char, s.end_char])])].sort((a, b) => a - b);
  const segments = [];
  for (let i = 0; i < cuts.length - 1; i += 1) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    segments.push({
      start, end, text: chars.slice(start, end).join(''),
      spans: valid.filter(s => s.start_char <= start && s.end_char >= end),
    });
  }
  return segments;
}

function documentViewer(v) {
  const viewer = h('div', { class: 'document', id: 'document-viewer', tabindex: '0', 'aria-label': 'Document text' });
  for (const segment of segmentDocument(v.document, v.evidence_spans)) {
    if (!segment.spans.length) {
      viewer.append(segment.text);
      continue;
    }
    const ids = [...new Set(segment.spans.map(s => s.condition_id))];
    const mark = h('mark', {
      class: `evidence-highlight${ids.length > 1 ? ' overlap' : ''}`,
      dataset: { conditionId: ids[0], conditionIds: ids.join(' ') },
      title: segment.spans.map(s => `${s.condition_id} · ${s.source === 'LOCATOR' ? 'located' : 'System-2'}${s.rationale ? `: ${s.rationale}` : ''}`).join('\n'),
      tabindex: '0',
      onclick: () => focusConditionCard(ids[0]),
      onkeydown: event => { if (event.key === 'Enter') focusConditionCard(ids[0]); },
    }, segment.text);
    viewer.append(mark);
  }
  return viewer;
}

const marksFor = conditionIds => [...document.querySelectorAll('#document-viewer mark.evidence-highlight')]
  .filter(mark => mark.dataset.conditionIds.split(' ').some(id => conditionIds.includes(id)));

function focusEvidence(conditionIds) {
  const marks = marksFor(conditionIds);
  if (!marks.length) {
    toast(`No quoted evidence in this version for ${conditionIds.join(', ')}.`);
    return;
  }
  scrollToElement(marks[0]);
  pulse(marks);
}

function focusRuleCard(ruleId) {
  const card = document.getElementById(`rule-${ruleId}`);
  if (!card) return;
  card.closest('details')?.setAttribute('open', '');
  scrollToElement(card);
  pulse([card]);
}

function focusConditionCard(conditionId) {
  const items = [...document.querySelectorAll('.rules-pane .condition')].filter(el => el.dataset.conditionId === conditionId);
  const target = items.find(el => !el.closest('details')) || items[0];
  if (!target) return;
  target.closest('details')?.setAttribute('open', '');
  scrollToElement(target);
  pulse([target]);
}

// --- Revision ----------------------------------------------------------------

function contextFields(context = {}) {
  const release = context.release_requested === true ? 'true' : context.release_requested === false ? 'false' : '';
  const select = (name, options, selected) => h('select', { name },
    options.map(([value, label]) => h('option', { value, selected: value === selected }, label)));
  return h('fieldset', { class: 'context-fields' },
    h('legend', {}, 'Submission context'),
    h('div', { class: 'grid-2' },
      h('label', { class: 'field' }, h('span', {}, 'Intended audience'), select('intended_audience', [
        ['infer', 'Infer from the document'], ['internal_team', 'Internal team'], ['institution_wide', 'Institution-wide'],
        ['partner_government', 'Partner government'], ['public_release', 'Public release'],
      ], context.intended_audience || 'infer')),
      h('label', { class: 'field' }, h('span', {}, 'Release requested'),
        select('release_requested', [['', 'Infer from the document'], ['true', 'Yes'], ['false', 'No']], release)),
      h('label', { class: 'field' }, h('span', {}, 'Proposed action'),
        h('input', { name: 'proposed_action', maxlength: '200', value: context.proposed_action || '' })),
      h('label', { class: 'field' }, h('span', {}, 'Source unit'),
        h('input', { name: 'source_unit', maxlength: '120', value: context.source_unit || '' }))),
    h('label', { class: 'field' }, h('span', {}, 'Purpose'),
      h('input', { name: 'purpose', maxlength: '500', value: context.purpose || '' })));
}

function readContext(form) {
  const f = form.elements;
  const release = f.release_requested.value;
  return {
    intended_audience: f.intended_audience.value || 'infer',
    release_requested: release === 'true' ? true : release === 'false' ? false : null,
    purpose: f.purpose.value.trim(),
    proposed_action: f.proposed_action.value.trim(),
    source_unit: f.source_unit.value.trim(),
  };
}

function modeFields(name) {
  return h('fieldset', { class: 'mode-fields' },
    h('legend', {}, 'Pipeline'),
    h('label', { class: 'radio' }, h('input', { type: 'radio', name, value: 'hybrid', checked: true }),
      h('span', {}, h('strong', {}, 'Hybrid'), ': System-2 reviews open conditions')),
    h('label', { class: 'radio' }, h('input', { type: 'radio', name, value: 'fast' }),
      h('span', {}, h('strong', {}, 'Fast'), ': System-1 only; open conditions go to a person')));
}

function revisionForm(c, v) {
  const next = c.current_version + 1;
  const textarea = h('textarea', {
    name: 'document', id: 'revision-document', rows: '16', required: true, maxlength: '100000', spellcheck: 'false',
  });
  textarea.value = v.document;
  const form = h('form', { class: 'revision-form', 'aria-label': `Revision v${next}` },
    h('label', { class: 'field' }, h('span', {}, 'Document'), textarea),
    contextFields(v.context),
    h('label', { class: 'field' }, h('span', {}, 'Change note'),
      h('input', { name: 'change_note', maxlength: '500', placeholder: 'What changed, and why' })),
    modeFields('mode'),
    h('div', { class: 'form-actions' },
      h('button', { type: 'button', class: 'btn ghost', onclick: () => { state.editing = false; renderCase(); } }, 'Cancel'),
      h('button', { type: 'submit', class: 'btn primary' }, `Submit revision v${next}`)));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const body = {
      document: form.elements.document.value,
      context: readContext(form),
      mode: form.elements.mode.value,
      change_note: form.elements.change_note.value.trim(),
    };
    try {
      const detail = await withBusy(event.submitter, 'Triaging…', () => api(casePath(c.case_id, '/versions'), { method: 'POST', body }));
      showCase(detail);
      refreshQueue();
      toast(`v${detail.active_version.version_number} triaged: ${VERDICT_LABELS[detail.active_version.decision.verdict]}.`, 'success');
    } catch (error) {
      toast(error.message, 'error');
      if (error.status === 409) loadCase(c.case_id, null);
    }
  });
  return form;
}

// --- Rules pane ----------------------------------------------------------------

function conditionsLackingEvidence(v) {
  const quoted = new Set((v.evidence_spans || []).map(s => s.condition_id));
  const missing = new Set();
  for (const rule of v.decision.rules || []) {
    if (rule.truth === 'FALSE') continue;
    for (const cond of rule.conditions) {
      if (cond.truth !== 'FALSE' && !quoted.has(cond.condition_id) && isQuotable(cond.condition_id)) missing.add(cond.condition_id);
    }
  }
  return [...missing];
}

function rulesPane(c, v, isCurrent) {
  const rules = v.decision.rules || [];
  const active = rules.filter(r => r.truth !== 'FALSE');
  const cleared = rules.filter(r => r.truth === 'FALSE');
  const missing = conditionsLackingEvidence(v);
  const pane = h('section', { class: 'pane rules-pane', 'aria-labelledby': 'rules-heading' },
    h('div', { class: 'pane-head' }, h('h3', { id: 'rules-heading' }, 'Rules and findings')),
    isCurrent && missing.length > 0 && h('div', { class: 'callout' },
      h('p', {}, `${plural(missing.length, 'active condition')} without a quote: `, missing.map((id, i) => [i ? ', ' : '', h('code', {}, id)])),
      h('button', { type: 'button', class: 'btn', onclick: event => extractQuotes(event.currentTarget, c, v) }, 'Extract quotes (System-2)')),
    !rules.length && h('p', { class: 'notice' }, 'No rules were evaluated. System-1 did not answer, so the document failed closed to a person.'),
    active.length > 0 && h('h4', { class: 'group-title' }, `Active rules (${active.length})`),
    active.map(rule => ruleCard(c, v, rule, isCurrent)),
    cleared.length > 0 && h('details', { class: 'cleared-group' },
      h('summary', {}, `Cleared rules (${cleared.length})`),
      h('p', { class: 'hint' }, 'One confident FALSE settles a rule, so uncertainty elsewhere in it cannot change the outcome.'),
      cleared.map(rule => ruleCard(c, v, rule, false))),
    caseControls(c, v, isCurrent),
    signalsTable(v.decision));
  pane.addEventListener('click', event => {
    if (event.target.closest('button, a, input, select, textarea, label, summary, form, mark')) return;
    const condition = event.target.closest('.condition');
    const card = event.target.closest('.rule-card');
    if (condition) focusEvidence([condition.dataset.conditionId]);
    else if (card) focusEvidence(card.dataset.conditionIds.split(' '));
  });
  return pane;
}

async function extractQuotes(button, c, v) {
  const before = (v.evidence_spans || []).length;
  try {
    const detail = await withBusy(button, 'Asking System-2…',
      () => api(casePath(c.case_id, `/versions/${v.version_number}/ground`), { method: 'POST' }));
    showCase(detail);
    const added = (detail.active_version.evidence_spans || []).length - before;
    toast(added > 0 ? `${plural(added, 'quote')} located in the document.` : 'System-2 returned no verbatim quote for the open conditions.',
      added > 0 ? 'success' : 'info');
  } catch (error) {
    toast(error.status === 503 ? 'Evidence grounding is unavailable: System-2 is not configured or did not answer.' : error.message, 'error');
  }
}

function ruleCard(c, v, rule, canDispose) {
  const definition = policyRule(rule.rule_id);
  const band = bandFor(rule.stakes);
  const record = v.rule_dispositions?.[rule.rule_id];
  return h('article', {
    class: `rule-card truth-${rule.truth} action-${rule.action}`, id: `rule-${rule.rule_id}`,
    dataset: { ruleId: rule.rule_id, conditionIds: rule.conditions.map(cond => cond.condition_id).join(' ') },
    'aria-labelledby': `rule-title-${rule.rule_id}`,
  },
  h('header', { class: 'rule-head' },
    h('button', {
      type: 'button', class: 'rule-locate', title: 'Show this rule\'s evidence in the document',
      onclick: () => focusEvidence(rule.conditions.map(cond => cond.condition_id)),
    },
    h('span', { class: 'rule-id' }, rule.rule_id),
    h('span', { class: `action action-${rule.action}` }, rule.action),
    h('span', { class: 'rule-title', id: `rule-title-${rule.rule_id}` }, rule.title)),
    truthBadge(rule.truth)),
  h('p', { class: 'rule-meta' },
    'Office ', h('strong', {}, rule.office), ' · ',
    h('span', { class: `stakes stakes-${rule.stakes}` }, `${rule.stakes} stakes`),
    band && ` · FALSE at P ≤ ${band.no.toFixed(2)}, TRUE at P ≥ ${band.yes.toFixed(2)}`),
  definition?.rationale && h('p', { class: 'rule-rationale' }, definition.rationale),
  h('ul', { class: 'conditions' }, rule.conditions.map(cond => conditionItem(v, rule, cond, band))),
  record && dispositionBlock(c, v, rule, record, canDispose));
}

function conditionItem(v, rule, cond, band) {
  const s2 = v.decision.system_two || {};
  const spans = (v.evidence_spans || []).filter(s => s.condition_id === cond.condition_id);
  const sent = (s2.propositions || []).some(p => p.id === cond.condition_id);
  const verdict = (s2.verdicts || []).find(x => x.id === cond.condition_id);
  const discarded = (s2.discarded || []).filter(d => d.id === cond.condition_id);
  return h('li', { class: `condition truth-${cond.truth}`, dataset: { conditionId: cond.condition_id } },
    h('div', { class: 'condition-head' },
      h('code', { class: 'condition-id' }, cond.condition_id),
      truthBadge(cond.truth),
      cond.resolved_by
        ? h('span', { class: `resolved resolved-${cond.resolved_by}` }, RESOLVED_BY_LABELS[cond.resolved_by] || cond.resolved_by)
        : h('span', { class: 'resolved resolved-none' }, 'Unresolved')),
    h('p', { class: 'proposition' }, cond.proposition),
    probabilityBar(cond.probability, band),
    (sent || verdict || discarded.length > 0) && systemTwoNote(verdict, discarded, s2),
    spans.map(evidenceQuote),
    !spans.length && cond.truth !== 'FALSE' && !isQuotable(cond.condition_id)
      && h('p', { class: 'hint' }, 'Read from Jev\'s distribution, so there is no quote to locate.'));
}

function probabilityBar(probability, band) {
  const no = band?.no ?? 0.3;
  const yes = band?.yes ?? 0.7;
  const p = clamp01(probability);
  return h('div', { class: 'prob', style: { '--no': no, '--yes': yes, '--p': p } },
    h('span', { class: 'prob-value' }, `P = ${fixed(p)}`),
    h('div', {
      class: 'prob-track', role: 'img',
      'aria-label': `Probability ${fixed(p)}. FALSE at or below ${fixed(no)}, TRUE at or above ${fixed(yes)}.`,
    },
    h('span', { class: 'zone zone-false' }), h('span', { class: 'zone zone-uncertain' }), h('span', { class: 'zone zone-true' }),
    h('span', { class: 'tick tick-no' }), h('span', { class: 'tick tick-yes' }), h('span', { class: 'marker' })),
    h('div', { class: 'prob-scale', 'aria-hidden': 'true' },
      h('span', { class: 'scale-no' }, `no ≤ ${fixed(no)}`),
      h('span', { class: 'scale-yes' }, `yes ≥ ${fixed(yes)}`)));
}

function systemTwoNote(verdict, discarded, s2) {
  const label = verdict ? verdict.verdict.replace('_', ' ') : s2.error ? 'unavailable' : s2.invoked ? 'no verdict' : 'not called';
  return h('div', { class: 'system-two' },
    h('div', { class: 'system-two-head' },
      h('span', { class: 'kicker' }, 'System-2'),
      h('span', { class: `s2-verdict s2-${verdict?.verdict || 'none'}` }, label)),
    verdict?.evidence && h('blockquote', { class: 's2-quote' }, verdict.evidence),
    verdict?.rationale && h('p', { class: 's2-rationale' }, verdict.rationale),
    discarded.map(d => h('p', { class: 'discarded' },
      h('span', { class: 'discard-badge' }, d.reason), ' ', DISCARD_LABELS[d.reason] || d.detail)));
}

function evidenceQuote(span) {
  return h('div', { class: 'evidence' },
    h('span', { class: 'kicker' }, span.source === 'LOCATOR' ? 'Located quote' : 'Quoted by System-2'),
    h('blockquote', {}, span.quote),
    h('button', { type: 'button', class: 'link', onclick: () => focusEvidence([span.condition_id]) }, 'Show in document'));
}

function dispositionBlock(c, v, rule, record, canDispose) {
  return h('div', { class: 'disposition' },
    h('p', { class: 'disposition-current' },
      h('span', { class: `disp disp-${record.disposition}` }, DISPOSITION_LABELS[record.disposition] || record.disposition),
      record.reviewer && ` by ${record.reviewer}`,
      record.reviewer && record.office && ` (${record.office})`,
      record.updated_at && ` · ${formatDate(record.updated_at)}`),
    record.rationale && h('p', { class: 'disposition-rationale' }, record.rationale),
    record.carried_from_version && h('p', { class: 'carry' },
      h('span', { class: 'badge-carry' }, `Carried forward from v${record.carried_from_version}`),
      ' The rule\'s inputs did not change materially, so the earlier decision still applies.'),
    record.invalidation_note && h('p', { class: 'invalidated', role: 'note' },
      h('span', { class: 'badge-invalid' }, 'Earlier disposition invalidated'), ' ', record.invalidation_note),
    canDispose && dispositionForm(c, v, rule, record));
}

function dispositionForm(c, v, rule, record) {
  const acting = identity().office;
  const hint = h('p', { class: 'hint disposition-hint' }, DISPOSITIONS.ACCEPT_FINDING.hint);
  const select = h('select', { name: 'disposition', required: true },
    Object.entries(DISPOSITIONS).map(([value, { label }]) => h('option', { value }, label)));
  select.addEventListener('change', () => { hint.textContent = DISPOSITIONS[select.value].hint; });
  const form = h('form', { class: 'disposition-form', 'aria-label': `Disposition for ${rule.rule_id}` },
    h('div', { class: 'grid-2' },
      h('label', { class: 'field' }, h('span', {}, record.disposition === 'PENDING' ? 'Disposition' : 'Change disposition'), select),
      h('div', { class: 'field acting' }, h('span', {}, 'Recorded as'), h('span', { class: 'acting-as' }, `${identity().reviewer || 'set your handle'} · ${acting}`))),
    hint,
    acting && acting !== rule.office && h('p', { class: 'hint office-mismatch' },
      `You are acting as ${acting}; this rule belongs to ${rule.office}.`),
    h('label', { class: 'field' }, h('span', {}, 'Rationale'),
      h('textarea', {
        name: 'rationale', rows: '2', minlength: '3', maxlength: '2000', required: true,
        placeholder: 'Why, in a sentence a later reviewer can audit',
      })),
    h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn small primary' }, 'Record disposition')));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const id = requireIdentity();
    if (!id || !form.reportValidity()) return;
    const body = { disposition: select.value, reviewer: id.reviewer, office: id.office, rationale: form.elements.rationale.value.trim() };
    if (body.rationale.length < 3) {
      toast('A rationale needs at least three characters.', 'error');
      return;
    }
    try {
      const detail = await withBusy(event.submitter, 'Saving…', () => api(
        casePath(c.case_id, `/versions/${v.version_number}/rules/${encodeURIComponent(rule.rule_id)}`), { method: 'PATCH', body }));
      showCase(detail);
      refreshQueue();
      toast(`${rule.rule_id}: ${DISPOSITIONS[body.disposition].label.toLowerCase()} recorded.`, 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  return form;
}

// --- Case-level controls --------------------------------------------------------

function caseControls(c, v, isCurrent) {
  if (!isCurrent) return null;
  const records = Object.values(v.rule_dispositions || {});
  const decided = records.filter(r => r.disposition !== 'PENDING').length;
  const office = h('select', { name: 'assigned_office' });
  fillOfficeSelect(office, { none: 'Unassigned', selected: c.assigned_office || '' });
  const reviewer = h('input', { name: 'assigned_reviewer', maxlength: '120', value: c.assigned_reviewer || '', placeholder: 'Reviewer handle' });
  const note = h('input', { name: 'note', maxlength: '1000', placeholder: 'Optional note for the audit log' });

  const patch = async (button, body, done) => {
    const id = requireIdentity();
    if (!id) return;
    try {
      const detail = await withBusy(button, 'Saving…', () => api(casePath(c.case_id), {
        method: 'PATCH', body: { ...body, reviewer: id.reviewer, note: note.value.trim() },
      }));
      showCase(detail);
      refreshQueue();
      toast(done, 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  };

  const statusActions = [];
  if (c.status === 'OPEN') {
    statusActions.push(h('button', {
      type: 'button', class: 'btn small warn',
      onclick: event => patch(event.currentTarget, { status: 'CHANGES_REQUESTED' }, 'Changes requested from the submitter.'),
    }, 'Request changes'));
  }
  if (c.status === 'CHANGES_REQUESTED') {
    statusActions.push(h('button', {
      type: 'button', class: 'btn small',
      onclick: event => patch(event.currentTarget, { status: 'OPEN' }, 'Case reopened.'),
    }, 'Reopen case'));
  }

  return h('section', { class: 'case-controls', 'aria-labelledby': 'controls-heading' },
    h('h4', { id: 'controls-heading' }, 'Case controls'),
    h('div', { class: 'grid-2' },
      h('label', { class: 'field' }, h('span', {}, 'Assigned office'), office),
      h('label', { class: 'field' }, h('span', {}, 'Assigned reviewer'), reviewer)),
    h('label', { class: 'field' }, h('span', {}, 'Note'), note),
    h('div', { class: 'form-actions' },
      h('button', {
        type: 'button', class: 'btn small',
        onclick: event => patch(event.currentTarget, {
          assigned_office: office.value || null, assigned_reviewer: reviewer.value.trim() || null,
        }, 'Assignment saved.'),
      }, 'Save assignment'),
      statusActions),
    h('p', { class: 'status-line' }, 'Status: ', statusBadge(c.status), ` · ${decided} of ${plural(records.length, 'active rule')} dispositioned`),
    h('p', { class: 'hint' },
      'Approved and Blocked (final) are not set by hand; they follow from the rule dispositions. Accepting a BLOCK finding ',
      'blocks the case for good. Once every active rule is overridden, waived, carried forward, or an accepted ROUTE finding, ',
      'the case is approved.'));
}

function signalsTable(decision) {
  const signals = Object.entries(decision.signals || {});
  if (!signals.length) return null;
  const value = signal => {
    if (signal.type === 'choice') {
      const ranked = Object.entries(signal.probabilities).sort((a, b) => b[1] - a[1]);
      return ranked.slice(0, 3).map(([option, p]) => `${option} ${fixed(p)}`).join(' · ');
    }
    if (signal.type === 'score') {
      const levels = Object.entries(signal.probabilities).map(([level, p]) => `${level}: ${fixed(p)}`).join(' · ');
      return `expected ${Number(signal.score).toFixed(2)} (${levels})`;
    }
    return `P(yes) ${fixed(signal.noul)}`;
  };
  return h('details', { class: 'signals' },
    h('summary', {}, `System-1 signals (${signals.length})`),
    h('table', {},
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Question'), h('th', { scope: 'col' }, 'Answer'), h('th', { scope: 'col' }, 'Confidence'))),
      h('tbody', {}, signals.map(([qid, signal]) => h('tr', {},
        h('th', { scope: 'row' }, h('code', {}, qid), h('span', { class: 'signal-type' }, signal.type)),
        h('td', {}, value(signal)),
        h('td', {}, fixed(signal.confidence)))))));
}

// ---------------------------------------------------------------------------
// New case
// ---------------------------------------------------------------------------

function normalizeSamples(data) {
  const items = Array.isArray(data) ? data : data?.samples || [];
  return items.filter(s => s && typeof s.document === 'string' && s.document.trim()).map((s, i) => ({
    id: `server:${s.id || i}`,
    title: s.title || String(s.id || `Sample ${i + 1}`),
    document: s.document,
    context: {
      intended_audience: s.context?.intended_audience || 'infer',
      release_requested: s.context?.release_requested ?? null,
      purpose: s.context?.purpose || s.metadata?.purpose || '',
      proposed_action: s.context?.proposed_action || '',
      source_unit: s.context?.source_unit || s.metadata?.source_unit || '',
    },
  }));
}

async function loadServerSamples() {
  try {
    state.serverSamples = normalizeSamples(await api(`${API}/samples`));
  } catch {
    state.serverSamples = []; // 404 or no server samples: the built-in presets are enough
  }
  const select = $('#preset-select');
  const groups = [h('option', { value: '' }, 'Blank document'),
    h('optgroup', { label: 'Institutional presets' }, PRESETS.map(p => h('option', { value: p.id }, p.title)))];
  if (state.serverSamples.length) {
    groups.push(h('optgroup', { label: 'Server samples' }, state.serverSamples.map(p => h('option', { value: p.id }, p.title))));
  }
  select.replaceChildren(...groups);
}

function applyPreset(id) {
  const preset = [...PRESETS, ...state.serverSamples].find(p => p.id === id);
  const f = $('#new-case-form').elements;
  if (!preset) return;
  f.title.value = preset.title;
  f.document.value = preset.document;
  const context = preset.context || {};
  f.intended_audience.value = context.intended_audience || 'infer';
  f.release_requested.value = context.release_requested === true ? 'true' : context.release_requested === false ? 'false' : '';
  f.purpose.value = context.purpose || '';
  f.proposed_action.value = context.proposed_action || '';
  f.source_unit.value = context.source_unit || '';
}

function openNewCase() {
  const dialog = $('#new-case-dialog');
  if (!dialog.open) dialog.showModal();
  $('#new-case-form').elements.title.focus();
}

function setupNewCase() {
  const dialog = $('#new-case-dialog');
  const form = $('#new-case-form');
  $('#new-case-button').addEventListener('click', openNewCase);
  $('#preset-select').addEventListener('change', event => applyPreset(event.target.value));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const body = {
      title: form.elements.title.value.trim(),
      document: form.elements.document.value,
      context: readContext(form),
      mode: form.elements.mode.value,
    };
    try {
      const detail = await withBusy(event.submitter, 'Triaging…', () => api(`${API}/cases`, { method: 'POST', body }));
      dialog.close();
      form.reset();
      showCase(detail);
      refreshQueue();
      toast(`Case created: ${VERDICT_LABELS[detail.machine_verdict] || detail.machine_verdict}.`, 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
}

// ---------------------------------------------------------------------------
// Dialogs: policy and token
// ---------------------------------------------------------------------------

function setupDialogs() {
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.addEventListener('click', event => {
      if (event.target.closest('[data-close]')) dialog.close();
      else if (event.target === dialog) dialog.close(); // backdrop
    });
  }
  $('#policy-chip').addEventListener('click', () => {
    renderPolicy();
    $('#policy-dialog').showModal();
  });
  $('#token-form').addEventListener('submit', event => {
    event.preventDefault();
    storage.set('jevgate.token', event.target.elements.token.value.trim(), 'session');
    $('#token-dialog').close();
    event.target.reset();
    refreshQueue();
    loadServerSamples();
    if (state.current) loadCase(state.current.case_id, null);
  });
}

function requestToken() {
  const dialog = $('#token-dialog');
  if (!dialog.open) dialog.showModal();
}

function renderPolicy() {
  const policy = state.policy;
  const content = $('#policy-content');
  if (!policy) {
    content.replaceChildren(h('p', {}, 'The policy could not be loaded.'));
    return;
  }
  const byOffice = new Map(state.offices.map(office => [office, []]));
  for (const rule of policy.rules) byOffice.get(rule.office)?.push(rule);
  content.replaceChildren(
    h('p', { class: 'policy-id' }, h('code', {}, policy.policy_id), ` · version ${policy.version}`),
    h('h3', {}, 'Bands'),
    h('table', {},
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Stakes'), h('th', { scope: 'col' }, 'FALSE at P ≤'),
        h('th', { scope: 'col' }, 'TRUE at P ≥'), h('th', { scope: 'col' }, 'Uncertain'))),
      h('tbody', {}, Object.entries(policy.bands).map(([stakes, band]) => h('tr', {},
        h('th', { scope: 'row' }, stakes), h('td', {}, fixed(band.no)), h('td', {}, fixed(band.yes)),
        h('td', {}, `${fixed(band.no)} < P < ${fixed(band.yes)}`))))),
    h('p', { class: 'hint' },
      `Expedite when P(${policy.expedite.signal} ≥ ${policy.expedite.at_least}) ≥ ${fixed(policy.expedite.probability)}. `,
      'A reviewer "no" never settles a high-stakes condition; a "yes" counts only with a verbatim quote.'),
    h('h3', {}, 'Offices and the rules they own'),
    h('div', { class: 'office-grid' }, [...byOffice].map(([office, rules]) => h('section', { class: 'office-card' },
      h('h4', {}, office),
      rules.length ? h('ul', {}, rules.map(rule => h('li', {},
        h('span', { class: 'rule-id' }, rule.rule_id), ' ', h('span', { class: `action action-${rule.action}` }, rule.action), ' ',
        rule.title, h('span', { class: `stakes stakes-${rule.stakes}` }, ` ${rule.stakes}`),
        h('p', { class: 'hint' }, `When ${rule.when.join(' AND ')}. ${rule.rationale}`))))
        : h('p', { class: 'hint' }, 'No rules route here.')))),
    h('h3', {}, 'Conditions'),
    h('table', {},
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Condition'), h('th', { scope: 'col' }, 'Read as'),
        h('th', { scope: 'col' }, 'Proposition'))),
      h('tbody', {}, Object.entries(policy.conditions).map(([id, cond]) => h('tr', {},
        h('th', { scope: 'row' }, h('code', {}, id)),
        h('td', {}, cond.test === 'yes' ? `P(${cond.signal} = yes)`
          : cond.test === 'at_least' ? `P(${cond.signal} ≥ ${cond.value})` : `P(${cond.signal} ∈ {${cond.value.join(', ')}})`),
        h('td', {}, cond.proposition))))),
    h('details', { class: 'questions' },
      h('summary', {}, `System-1 question battery (${Object.keys(state.questions).length})`),
      h('ul', {}, Object.entries(state.questions).map(([qid, q]) => h('li', {},
        h('code', {}, qid), h('span', { class: 'signal-type' }, q.type), ' ', q.instructions)))));
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function init() {
  setupIdentity();
  setupFilters();
  setupDialogs();
  setupNewCase();
  await loadReference();
  await Promise.all([refreshQueue(), loadServerSamples()]);
  window.addEventListener('hashchange', openFromHash);
  openFromHash();
}

if (typeof document !== 'undefined' && typeof window !== 'undefined' && !window.__JEVGATE_NO_INIT__) init();
if (typeof module === 'object' && module.exports) module.exports = { segmentDocument, describeError, normalizeSamples, PRESETS };
