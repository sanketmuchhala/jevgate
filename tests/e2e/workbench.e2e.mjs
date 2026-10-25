// End-to-end test of the reviewer workbench: starts scripts/demo_server.py (no API keys) and drives
// headless Chrome over the DevTools protocol. No npm dependencies; needs Node 22+ and Chrome.
//
//   node tests/e2e/workbench.e2e.mjs
//   node tests/e2e/workbench.e2e.mjs --screenshots outputs/e2e
//
// Environment: PYTHON (default .venv/bin/python), CHROME_PATH. Without Chrome the test is skipped,
// unless CI is set, in which case that is a failure.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const ROOT = resolve(new URL('../..', import.meta.url).pathname);
const shotsArg = process.argv.indexOf('--screenshots');
const SHOTS = shotsArg > -1 ? resolve(process.argv[shotsArg + 1]) : null;
const children = [];
const profile = mkdtempSync(join(tmpdir(), 'jevgate-e2e-'));
const cleanup = () => {
  for (const child of children) child.kill();
  // Chrome may still be writing its profile as it exits; leaving a temp directory must not fail the run.
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); } catch {}
};
process.on('exit', cleanup);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => process.exit(130));

function findChrome() {
  if (process.env.CHROME_PATH) return existsSync(process.env.CHROME_PATH) ? process.env.CHROME_PATH : null;
  const candidates = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'];
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    const found = spawnSync('which', [name], { encoding: 'utf8' }).stdout.trim();
    if (found) candidates.push(found);
  }
  return candidates.find(path => path && existsSync(path));
}

const freePort = () => new Promise(done => {
  const server = createServer().listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => done(port)); });
});

const chromePath = findChrome();
if (!chromePath) {
  console.log(process.env.CI ? 'FAIL: Chrome not found (set CHROME_PATH)' : 'skipped: Chrome not found (set CHROME_PATH)');
  process.exit(process.env.CI ? 1 : 0);
}

// --- Demo server -------------------------------------------------------------

const port = await freePort();
const BASE = `http://127.0.0.1:${port}`;
const python = process.env.PYTHON || (existsSync(join(ROOT, '.venv/bin/python')) ? join(ROOT, '.venv/bin/python') : 'python3');
const server = spawn(python, ['scripts/demo_server.py', '--port', String(port), '--seed'], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
children.push(server);
let serverOutput = '';
server.stdout.on('data', chunk => { serverOutput += chunk; });
server.stderr.on('data', chunk => { serverOutput += chunk; });
for (let i = 0; !serverOutput.includes('seeded 8 cases'); i++) {
  if (i > 200 || server.exitCode !== null) { console.error(`demo server did not start:\n${serverOutput}`); process.exit(1); }
  await sleep(100);
}
const cases = await (await fetch(`${BASE}/v1/cases`)).json();
const caseId = title => cases.find(c => c.title === title).case_id;

// --- Chrome --------------------------------------------------------------------

const debugPort = await freePort();
const chrome = spawn(chromePath, [
  '--headless=new', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, '--no-first-run',
  '--no-default-browser-check', '--disable-gpu', ...(process.env.CI ? ['--no-sandbox'] : []), 'about:blank',
], { stdio: 'ignore' });
children.push(chrome);
let target;
for (let i = 0; i < 100 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find(t => t.type === 'page'); } catch {}
  if (!target) await sleep(100);
}
if (!target) { console.error('Chrome did not expose a page target'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(done => ws.addEventListener('open', done, { once: true }));
let nextId = 1;
const pending = new Map();
const problems = [];
ws.addEventListener('message', ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
  if (msg.method === 'Runtime.exceptionThrown') problems.push(`exception: ${msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text}`);
  if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(msg.params.type)) problems.push(`console.${msg.params.type}: ${msg.params.args.map(a => a.value ?? a.description).join(' ')}`);
  if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error' && !msg.params.entry.url?.endsWith('favicon.ico')) problems.push(`log: ${msg.params.entry.text}`);
  if (msg.method === 'Page.javascriptDialogOpening') { problems.push(`dialog opened: ${msg.params.message}`); send('Page.handleJavaScriptDialog', { accept: false }); }
});
function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((done, fail) => pending.set(id, msg => (msg.error ? fail(new Error(`${method}: ${msg.error.message}`)) : done(msg.result))));
}
async function js(expression) {
  const r = await send('Runtime.evaluate', { expression: `(async () => { ${expression} })()`, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`page script failed: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
  return r.result.value;
}
async function waitFor(condition, label, timeout = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await js(`return Boolean(${condition});`)) return true;
    await sleep(60);
  }
  throw new Error(`timed out waiting for ${label}`);
}
async function goto(path) {
  await send('Page.navigate', { url: BASE + path });
  await waitFor(`document.readyState === 'complete'`, 'page load');
}
async function shot(name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(SHOTS, `${name}.png`), Buffer.from(data, 'base64'));
}
const setViewport = (width, height, mobile = false) => send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
const setScheme = value => send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value }] });

let passed = 0;
const failures = [];
function check(ok, label, detail = '') {
  if (ok) passed++;
  else failures.push(`${label}${detail ? `: ${detail}` : ''}`);
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${label}${ok || !detail ? '' : ` (${detail})`}`);
}

// WCAG contrast of key text against its nearest opaque background.
const LOW_CONTRAST = `
  const lum = c => { const [r, g, b] = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const bg = el => { while (el) { const c = getComputedStyle(el).backgroundColor; if (c !== 'transparent' && !c.startsWith('rgba(0, 0, 0, 0)')) return c; el = el.parentElement; } return 'rgb(255, 255, 255)'; };
  const ratio = el => { const a = lum(getComputedStyle(el).color), b = lum(bg(el)); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
  const selectors = ['.rule-title', '.rule-id', '.acting-as', '.proposition', '.rule-meta', '.case-sub', '.hint', '.prob-scale span',
    '.condition-id', '.s2-rationale', '.kicker', '.case-item-meta', '.verdict-label'];
  return selectors.map(s => [s, document.querySelector(s)]).filter(([, el]) => el).map(([s, el]) => [s, Math.round(ratio(el) * 10) / 10]).filter(([, r]) => r < 4.5);
`;

const TITLES = {
  cleared: 'Internal mission planning note with tentative travel',
  aideMemoire: 'Country mission Aide-Mémoire annex with delegation route',
  trade: 'Forwarded trade-desk notes with an open audience',
  draft: 'Draft Aide-Mémoire with logistics, circulation unconfirmed',
  arbitration: 'Arbitration note where the reviewer paraphrases',
  ministry: 'Partner ministry letter with a focal point\'s personal mobile',
};

try {
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable');
  await setViewport(1440, 1000);
  await setScheme('light');

  console.log('queue and identity');
  await goto('/app/');
  await waitFor(`document.querySelectorAll('#case-list .case-item').length === 8`, 'queue');
  check((await js(`return document.querySelector('#policy-chip').textContent;`)).startsWith('Policy jevgate-default'), 'policy chip');
  check(await js(`return document.querySelectorAll('#acting-office option').length;`) === 7, 'seven offices from the policy');
  check(await js(`return document.querySelector('#case-list .case-item-title').textContent;`) === 'Field office laptop breach incident', 'expedite case sorts first');
  check(await js(`return [...document.querySelectorAll('#case-list .verdict .kicker')].every(k => k.textContent === 'Machine') && [...document.querySelectorAll('#case-list .status .kicker')].every(k => k.textContent === 'Human');`), 'machine verdict and human status are labelled apart');
  await shot('01-queue');
  await js(`const h = document.querySelector('#reviewer-handle'); h.value = 'a.quist'; h.dispatchEvent(new Event('input')); const o = document.querySelector('#acting-office'); o.value = 'security_desk'; o.dispatchEvent(new Event('change')); return true;`);
  await send('Page.reload');
  await waitFor(`document.querySelectorAll('#case-list .case-item').length === 8`, 'queue after reload');
  check(await js(`return document.querySelector('#reviewer-handle').value + '|' + document.querySelector('#acting-office').value;`) === 'a.quist|security_desk', 'identity persists across reload');

  console.log('evidence highlights');
  for (const c of cases) {
    await goto(`/app/#case=${c.case_id}`);
    await waitFor(`document.querySelector('.case-title')?.textContent === ${JSON.stringify(c.title)}`, c.title);
    const r = await js(`
      const detail = await (await fetch('/v1/cases/${c.case_id}')).json();
      const text = {};
      for (const m of document.querySelectorAll('#document-viewer mark.evidence-highlight')) for (const id of m.dataset.conditionIds.split(' ')) text[id] = (text[id] || '') + m.textContent;
      return { n: detail.active_version.evidence_spans.length, exact: detail.active_version.evidence_spans.every(s => text[s.condition_id] === s.quote),
               whole: document.querySelector('#document-viewer').textContent === detail.active_version.document };`);
    check(r.exact && r.whole, `${c.title}: ${r.n} span(s) highlight exactly the quoted text`);
  }

  console.log('blocked case');
  await goto(`/app/#case=${caseId(TITLES.aideMemoire)}`);
  await waitFor(`document.querySelector('#rule-P1')`, 'P1 card');
  check(await js(`return [...document.querySelector('#case-view').childNodes].every(n => n.nodeType !== 3 || !n.textContent.trim());`), 'no stray text in the case view');
  const lowLight = await js(LOW_CONTRAST);
  check(lowLight.length === 0, 'light theme: key text meets 4.5:1 contrast', JSON.stringify(lowLight));
  check(await js(`return document.querySelector('.case-head .verdict-label').textContent;`) === 'Blocked', 'header shows the machine verdict');
  check(await js(`return !document.querySelector('.cleared-group').open && document.querySelectorAll('.cleared-group .rule-card').length === 7;`), 'cleared rules start collapsed');
  await js(`document.querySelector('#rule-P1 .rule-rationale').click(); return true;`);
  await sleep(120);
  check(await js(`return document.querySelector('#document-viewer mark').classList.contains('pulse');`), 'clicking a rule card pulses its evidence');
  check(await js(`const m = document.querySelector('#document-viewer mark').getBoundingClientRect(); const p = document.querySelector('.doc-pane').getBoundingClientRect(); return m.top >= p.top && m.bottom <= p.bottom;`), 'evidence is scrolled into view');
  await shot('02-blocked');
  await js(`const f = document.querySelector('#rule-P1 .disposition-form'); f.elements.disposition.value = 'ACCEPT_FINDING'; f.elements.rationale.value = 'Route and hotel must not go to the ministry.'; f.requestSubmit(); return true;`);
  await waitFor(`document.querySelector('.case-head .status')?.textContent.includes('Blocked (final)')`, 'BLOCKED_FINAL');
  check((await js(`return document.querySelector('#rule-P1 .disposition-current').textContent;`)).startsWith('Finding accepted by a.quist (security_desk)'), 'accepting a BLOCK finding blocks the case for good');
  check(await waitFor(`[...document.querySelectorAll('#case-list .status')].some(s => s.textContent.includes('Blocked (final)'))`, 'queue refresh'), 'queue reflects the derived status');

  console.log('open uncertainty');
  await goto(`/app/#case=${caseId(TITLES.trade)}`);
  await waitFor(`document.querySelector('#rule-P3')`, 'P3 card');
  check(await js(`return document.querySelector('.alert-banner h3').textContent;`) === 'Escalated to a person', 'escalation banner');
  check(await js(`return document.querySelector('#rule-P3 [data-condition-id="external_audience"] .s2-verdict').textContent;`) === 'cannot determine', 'System-2 cannot_determine is shown');
  check(await js(`return document.querySelector('#rule-P3 [data-condition-id="external_audience"] .resolved').textContent;`) === 'Unresolved', 'open condition is marked unresolved');
  await js(`document.querySelector('.alert-banner .link').click(); return true;`);
  await sleep(120);
  check(await js(`return document.querySelector('.rules-pane .pulse') !== null;`), 'escalation link locates the rule');
  await goto(`/app/#case=${caseId(TITLES.draft)}`);
  await waitFor(`document.querySelector('#rule-P1')`, 'draft P1');
  check(await js(`return document.querySelector('#rule-P1 .discard-badge')?.textContent;`) === 'HIGH_STAKES_NO', 'discarded high-stakes no is explained');
  await goto(`/app/#case=${caseId(TITLES.arbitration)}`);
  await waitFor(`document.querySelector('#rule-P7')`, 'P7');
  check(await js(`return document.querySelector('#rule-P7 .discard-badge')?.textContent;`) === 'UNGROUNDED_YES', 'discarded ungrounded yes is explained');
  await js(`document.querySelector('.callout button').click(); return true;`);
  check(await waitFor(`[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('no verbatim quote'))`, 'toast'), 'extraction without a verbatim quote says so');

  console.log('new case, quotes, revisions');
  await js(`document.querySelector('#new-case-button').click(); return true;`);
  await waitFor(`document.querySelector('#new-case-dialog').open`, 'new-case dialog');
  check(await js(`return document.querySelectorAll('#preset-select optgroup').length;`) === 2, 'presets and server samples are offered');
  await js(`const s = document.querySelector('#preset-select'); s.value = 'eval-04-step-payment-minutes'; s.dispatchEvent(new Event('change')); document.querySelector('#new-case-form').elements.mode.value = 'fast'; return true;`);
  await shot('03-new-case');
  await js(`const f = document.querySelector('#new-case-form'); f.requestSubmit(f.querySelector('[type=submit]')); return true;`);
  await waitFor(`!document.querySelector('#new-case-dialog').open && document.querySelector('.case-title')?.textContent === 'Procurement STEP Payment Release Minutes'`, 'new case');
  check(await js(`return document.querySelectorAll('#case-list .case-item').length;`) === 9, 'the new case joins the queue');
  check(await js(`return document.querySelectorAll('#document-viewer mark').length;`) === 0, 'fast mode stores no quotes');
  await js(`document.querySelector('.callout button').click(); return true;`);
  await waitFor(`document.querySelectorAll('#document-viewer mark').length === 1`, 'extracted quote');
  check(await js(`return document.querySelector('#document-viewer mark').textContent;`) === 'the institution will release the advance payment of USD 312,000 to Norvale Logistics on 28 October', 'extracted quote is highlighted');
  check(await js(`return document.querySelector('.callout') === null;`), 'the extract button goes once every condition is quoted');
  const newCase = await js(`return new URLSearchParams(location.hash.slice(1)).get('case');`);
  await js(`const f = document.querySelector('#rule-P6 .disposition-form'); f.elements.disposition.value = 'WAIVE_RISK'; f.elements.rationale.value = 'Covered by the bank guarantee.'; f.requestSubmit(); return true;`);
  await waitFor(`document.querySelector('#rule-P6 .disposition-current')?.textContent.startsWith('Risk waived')`, 'waiver');
  const revise = async (edit, version) => {
    await js(`[...document.querySelectorAll('.doc-pane button')].find(b => b.textContent.startsWith('Edit & submit revision')).click(); return true;`);
    await waitFor(`document.querySelector('#revision-document')`, 'revision form');
    await js(`const f = document.querySelector('.revision-form'); ${edit} f.requestSubmit(f.querySelector('[type=submit]')); return true;`);
    await waitFor(`document.querySelector('.case-head .version-chip')?.textContent === 'v${version}'`, `v${version}`);
  };
  await revise(`f.elements.change_note.value = 'Purpose clarified.'; f.elements.purpose.value = 'Record the payment decision';`, 2);
  check(await js(`return document.querySelector('#rule-P6 .badge-carry')?.textContent;`) === 'Carried forward from v1', 'unchanged text carries the waiver forward');
  await revise(`f.elements.document.value = f.elements.document.value.replace('USD 312,000', 'USD 412,000'); f.elements.change_note.value = 'Amount corrected.';`, 3);
  check(await js(`return document.querySelector('#rule-P6 .badge-invalid')?.textContent;`) === 'Earlier disposition invalidated', 'changed text invalidates the waiver');
  await shot('04-revision');
  await js(`document.querySelector('.versions .version-button').click(); return true;`);
  await waitFor(`document.querySelector('.doc-pane .notice')`, 'v1 view');
  check(await js(`return document.querySelector('.case-head .version-chip').textContent;`) === 'v1 of 3', 'older versions are labelled');
  check(await js(`return document.querySelectorAll('.disposition-form, .case-controls').length;`) === 0, 'older versions are read-only');
  check(await js(`return location.hash;`) === `#case=${newCase}&v=1`, 'versions are deep-linkable');

  console.log('case controls and filters');
  await goto(`/app/#case=${caseId(TITLES.ministry)}`);
  await waitFor(`document.querySelector('.case-controls')`, 'controls');
  await js(`const c = document.querySelector('.case-controls'); c.querySelector('[name=assigned_office]').value = 'privacy_office'; c.querySelector('[name=assigned_reviewer]').value = 'l.moreau'; [...c.querySelectorAll('button')].find(b => b.textContent === 'Save assignment').click(); return true;`);
  check(await waitFor(`document.querySelector('.case-sub')?.textContent.includes('assigned to privacy_office · l.moreau')`, 'assignment'), 'assignment is saved');
  await js(`[...document.querySelectorAll('.case-controls button')].find(b => b.textContent === 'Request changes').click(); return true;`);
  await waitFor(`document.querySelector('.case-head .status')?.textContent.includes('Changes requested')`, 'changes requested');
  check(await js(`return [...document.querySelectorAll('.case-controls button')].some(b => b.textContent === 'Reopen case');`), 'a case with requested changes can be reopened');
  const filter = (name, value) => js(`const f = document.querySelector('#filters'); const v = ${JSON.stringify(value)}; const el = f.elements[${JSON.stringify(name)}]; if (el.type === 'checkbox') el.checked = v; else el.value = v; f.dispatchEvent(new Event('change')); return document.querySelectorAll('#case-list .case-item').length;`);
  check(await filter('status', 'BLOCKED_FINAL') === 1, 'status filter');
  await filter('status', '');
  check(await filter('expedite', true) === 1, 'expedite filter');
  await filter('expedite', false);
  check(await filter('office', 'privacy_office') === 2, 'office filter');
  await filter('office', '');
  check(await filter('verdict', 'HUMAN_REVIEW') === 3, 'machine verdict filter');
  await filter('verdict', '');
  check(await js(`return document.querySelectorAll('.toast').length;`) <= 3, 'at most three toasts at once');
  await js(`document.querySelector('#policy-chip').click(); return true;`);
  await waitFor(`document.querySelector('#policy-dialog').open`, 'policy dialog');
  check(await js(`return document.querySelectorAll('#policy-content .office-card').length;`) === 7, 'policy dialog lists the seven offices');
  await js(`document.querySelector('#policy-dialog [data-close]').click(); return true;`);

  console.log('hostile text');
  const hostile = await js(`
    const r = await fetch('/v1/cases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      title: '<img src=x onerror=alert(1)> Title', mode: 'hybrid',
      document: 'To: Ministry of Interior. <script>alert(2)</script><img src=x onerror=alert(3)> Motorcade route: north gate.' }) });
    return (await r.json()).case_id;`);
  await goto(`/app/#case=${hostile}`);
  await waitFor(`document.querySelector('.case-title')`, 'hostile case');
  check(await js(`return document.querySelectorAll('#case-view img, #case-view script, #case-list img').length;`) === 0, 'markup in titles and documents renders as text');
  check(await js(`return document.querySelector('#document-viewer').textContent.includes('<script>alert(2)</script>');`), 'document text is preserved literally');

  console.log('phone width and dark theme');
  await setViewport(375, 812, true);
  await goto(`/app/#case=${caseId(TITLES.trade)}`);
  await waitFor(`document.querySelector('#rule-P3')`, 'phone');
  check(await js(`return document.documentElement.scrollWidth <= window.innerWidth;`), 'no horizontal scroll at 375 px');
  await shot('05-phone');
  await setViewport(1440, 1000);
  await setScheme('dark');
  await goto(`/app/#case=${caseId(TITLES.trade)}`);
  await waitFor(`document.querySelector('#rule-P3')`, 'dark');
  const lowDark = await js(LOW_CONTRAST);
  check(lowDark.length === 0, 'dark theme: key text meets 4.5:1 contrast', JSON.stringify(lowDark));
  await shot('06-dark');
} catch (error) {
  failures.push(String(error.message || error));
  console.log(`  FAIL ${error.message || error}`);
}

check(problems.length === 0, 'no console errors, exceptions, or dialogs', problems.join(' | '));
console.log(`\n${passed} passed, ${failures.length} failed`);
ws.close();
process.exit(failures.length ? 1 : 0);
