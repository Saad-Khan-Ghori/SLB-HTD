import { Simulation, DEFAULT_CONFIG } from './engine.js';

const form = document.querySelector('#config-form');
const rate = document.querySelector('#rate');
const loss = document.querySelector('#loss');
const clock = document.querySelector('#sim-clock');
const playButton = document.querySelector('#play-button');
const backendStrip = document.querySelector('#backend-strip');
const metrics = document.querySelector('#metrics');
const chart = document.querySelector('#history-chart');
const eventsList = document.querySelector('#events-list');
const requestsList = document.querySelector('#requests-list');
const packetLayer = document.querySelector('#packet-layer');
const clientSource = document.querySelector('#client-source');
const decisionExplanation = document.querySelector('#decision-explanation');
const requestTitle = document.querySelector('#current-request-title');
const requestDetail = document.querySelector('#current-request-detail');
const svg = document.querySelector('#topology');
const backendCards = new Map();
let simulation = new Simulation();
let timer = null;
let animationGeneration = 0;

function settings() {
  const values = new FormData(form);
  return {
    policy: values.get('policy'), requestsPerStep: Number(values.get('rate')),
    lossPct: Number(values.get('loss')), fall: Number(values.get('fall')),
    rise: Number(values.get('rise')),
    delays: ['delayB1', 'delayB2', 'delayB3'].map(key => Number(values.get(key))),
    clientIPs: DEFAULT_CONFIG.clientIPs, hashSeed: DEFAULT_CONFIG.hashSeed, seed: DEFAULT_CONFIG.seed,
  };
}

function stop() {
  if (timer !== null) clearInterval(timer);
  timer = null;
  playButton.textContent = 'Play';
  playButton.setAttribute('aria-pressed', 'false');
}

function clearAnimations() {
  animationGeneration += 1;
  packetLayer.replaceChildren();
  document.querySelectorAll('.network-lines path').forEach(path => path.classList.remove('route-active', 'route-loss'));
}

function highlightRoute(paths, failed = false) {
  document.querySelectorAll('.network-lines path').forEach(path => path.classList.remove('route-active', 'route-loss'));
  for (const id of paths) document.getElementById(id).classList.add(failed ? 'route-loss' : 'route-active');
}

function pointOn(path, progress) {
  return path.getPointAtLength(path.getTotalLength() * progress);
}

function packet(paths, { color = '#96e2d3', id = '', duration = 900, fraction = 1, reverse = false } = {}) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return Promise.resolve();
  const generation = animationGeneration;
  const segments = paths.map(name => document.getElementById(name));
  const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  dot.setAttribute('r', '6'); dot.setAttribute('fill', color); dot.setAttribute('class', 'packet');
  label.setAttribute('fill', color); label.setAttribute('class', 'packet-label'); label.textContent = id;
  group.append(dot, label); packetLayer.append(group);
  return new Promise(resolve => {
    const started = performance.now();
    function frame(now) {
      if (generation !== animationGeneration) { group.remove(); resolve(); return; }
      const ratio = Math.min(1, (now - started) / duration);
      const scaled = Math.min(segments.length - 0.00001, ratio * segments.length * fraction);
      const index = Math.floor(scaled);
      const local = scaled - index;
      const path = segments[reverse ? segments.length - 1 - index : index];
      const progress = reverse ? 1 - local : local;
      const position = pointOn(path, progress);
      dot.setAttribute('cx', position.x); dot.setAttribute('cy', position.y);
      label.setAttribute('x', position.x + 10); label.setAttribute('y', position.y - 10);
      if (ratio < 1) requestAnimationFrame(frame);
      else { group.remove(); resolve(position); }
    }
    requestAnimationFrame(frame);
  });
}

function burst(position, color, text) {
  if (!position) return;
  const generation = animationGeneration;
  const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  circle.setAttribute('cx', position.x); circle.setAttribute('cy', position.y);
  circle.setAttribute('r', '7'); circle.setAttribute('fill', 'none'); circle.setAttribute('stroke', color);
  circle.setAttribute('stroke-width', '3');
  const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  label.setAttribute('x', position.x + 13); label.setAttribute('y', position.y - 12);
  label.setAttribute('fill', color); label.setAttribute('class', 'packet-label'); label.textContent = text;
  packetLayer.append(circle, label);
  circle.animate([{ r: 7, opacity: 1 }, { r: 31, opacity: 0 }], { duration: 570, easing: 'ease-out' });
  setTimeout(() => { if (generation === animationGeneration) { circle.remove(); label.remove(); } }, 590);
}

async function animateRequest(request) {
  const id = `#${request.id}`;
  if (request.status === 'LOST') {
    highlightRoute(['path-client', 'path-r1'], true);
    const position = await packet(['path-client', 'path-r1'], { color: '#e89c94', id, fraction: .78, duration: 730 });
    burst(position, '#e89c94', 'LOST');
    return;
  }
  if (!request.backend) {
    highlightRoute(['path-client', 'path-r1', 'path-r2'], true);
    const position = await packet(['path-client', 'path-r1', 'path-r2'], { color: '#e89c94', id, duration: 780 });
    burst(position, '#e89c94', '503');
    await packet(['path-client', 'path-r1', 'path-r2'], { color: '#e89c94', id: '503', reverse: true, duration: 640 });
    return;
  }
  const paths = ['path-client', 'path-r1', 'path-r2', `route-${request.backend.toLowerCase()}`];
  highlightRoute(paths, request.status.startsWith('502'));
  const position = await packet(paths, { id, color: request.status.startsWith('502') ? '#e89c94' : '#96e2d3', duration: 960 });
  if (request.status.startsWith('502')) {
    burst(position, '#e89c94', '502');
    await packet(paths, { color: '#e89c94', id: '502', reverse: true, duration: 690 });
    return;
  }
}

function animateCompletions(completions) {
  for (const item of completions) {
    packet(['path-client', 'path-r1', 'path-r2', `route-${item.backend.toLowerCase()}`],
      { id: `#${item.id} 200`, color: '#96e2d3', reverse: true, duration: 740 });
  }
}

function animateProbes(probes) {
  if (!probes.length) return;
  for (const probe of probes) {
    const path = document.getElementById(`route-${probe.backend.toLowerCase()}`);
    const position = pointOn(path, 1);
    packet([`route-${probe.backend.toLowerCase()}`], { color: probe.ok ? '#e7bd79' : '#e89c94', id: '/health', duration: 550 }).then(end => {
      if (!probe.ok && end) burst(end, '#e89c94', 'FAIL');
      else if (probe.transition === 'UP' && end) burst(end, '#a8dfcf', 'UP');
    });
  }
}

function initializeBackendCards() {
  backendStrip.replaceChildren(); backendCards.clear();
  for (const name of ['B1', 'B2', 'B3']) {
    const card = document.createElement('div'); card.className = 'backend-control';
    card.innerHTML = `<div class="backend-top"><div><span class="backend-name">${name}</span><span class="backend-detail" data-delay></span></div><span class="backend-badge" data-status></span></div><div class="backend-work"><strong data-active>0</strong><span>handling now</span><span class="served-count" data-served>0 served</span></div><div class="activity-list" data-activity></div>`;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'backend-toggle';
    button.addEventListener('click', () => {
      const backend = simulation.backends.find(item => item.name === name);
      const interrupted = backend.physicalUp ? simulation.pending.filter(item => item.backend.name === name).map(item => item.trace.id) : [];
      simulation.setPhysical(name, !backend.physicalUp);
      render();
      for (const id of interrupted) packet(['path-client', 'path-r1', 'path-r2', `route-${name.toLowerCase()}`],
        { id: `#${id} 502`, color: '#e89c94', reverse: true, duration: 740 });
    });
    card.append(button); backendStrip.append(card);
    backendCards.set(name, { card, button, rows: new Map() });
  }
}

function renderBackends(snapshot) {
  for (const backend of snapshot.backends) {
    const ui = backendCards.get(backend.name);
    const detecting = !backend.physicalUp && backend.logicalUp;
    const recovering = backend.physicalUp && !backend.logicalUp;
    const status = backend.logicalUp ? (detecting ? `Detecting ${backend.failCount}/${snapshot.config.fall}` : 'Routing UP')
      : recovering ? `Recovering ${backend.riseCount}/${snapshot.config.rise}` : 'Routing DOWN';
    const tone = detecting || recovering ? 'detecting' : backend.logicalUp ? '' : 'down';
    ui.card.querySelector('[data-delay]').textContent = `${backend.delay} ms service delay`;
    ui.card.querySelector('[data-active]').textContent = backend.active;
    ui.card.querySelector('[data-served]').textContent = `${backend.served} served`;
    const badge = ui.card.querySelector('[data-status]'); badge.textContent = status; badge.className = `backend-badge ${tone}`;
    ui.button.textContent = backend.physicalUp ? 'Stop process' : 'Restart process';
    ui.button.setAttribute('aria-label', `${backend.physicalUp ? 'Stop' : 'Restart'} ${backend.name}`);
    const activeIds = new Set(snapshot.pending.filter(item => item.backend === backend.name).map(item => item.id));
    for (const [id, row] of ui.rows) if (!activeIds.has(id)) { row.remove(); ui.rows.delete(id); }
    for (const work of snapshot.pending.filter(item => item.backend === backend.name)) {
      if (ui.rows.has(work.id)) continue;
      const row = document.createElement('div'); row.className = 'activity-row';
      const label = document.createElement('span'); label.textContent = `#${work.id} processing`;
      const track = document.createElement('div'); track.className = 'service-track';
      const fill = document.createElement('div'); fill.className = 'service-fill'; track.append(fill);
      row.append(label, track); ui.card.querySelector('[data-activity]').append(row);
      ui.rows.set(work.id, row);
      fill.animate([{ width: `${Math.round(work.progress * 100)}%` }, { width: '100%' }],
        { duration: Math.min(3200, 500 + (work.finishedAt - work.startedAt) * 9), fill: 'forwards', easing: 'linear' });
    }
    const svgNode = document.querySelector(`#svg-${backend.name.toLowerCase()}`);
    svgNode.classList.toggle('is-down', !backend.logicalUp);
    svgNode.classList.toggle('is-detecting', detecting || recovering);
    document.querySelector(`#svg-${backend.name.toLowerCase()}-active`).textContent = `${backend.active} active`;
  }
}

function renderMetrics(snapshot) {
  const values = [
    ['Sent', snapshot.totals.attempted], ['Completed', snapshot.totals.success],
    ['Failed', snapshot.totals.errors], ['p95 service', `${Math.round(snapshot.totals.p95)}ms`],
  ];
  metrics.replaceChildren();
  for (const [label, value] of values) {
    const item = document.createElement('div'); item.className = 'metric';
    const strong = document.createElement('strong'); strong.textContent = value;
    const caption = document.createElement('span'); caption.textContent = label;
    item.append(strong, caption); metrics.append(item);
  }
  chart.replaceChildren();
  if (!snapshot.requests.length) {
    const empty = document.createElement('div'); empty.className = 'empty';
    empty.textContent = 'No requests yet. Send one to see the first route.';
    chart.append(empty); return;
  }
  for (const request of [...snapshot.requests].reverse().slice(-48)) {
    const bar = document.createElement('div');
    bar.className = `request-mark ${request.status === '200 OK' ? 'served' : request.status === 'processing' ? 'processing' : 'failed'}`;
    bar.title = `#${request.id} ${request.clientIP} → ${request.backend ?? 'none'}: ${request.status}`;
    chart.append(bar);
  }
}

function renderRequests(snapshot) {
  requestsList.replaceChildren();
  if (!snapshot.requests.length) {
    const empty = document.createElement('li'); empty.className = 'empty';
    empty.textContent = 'Each request will appear here with its client, route, and result.';
    requestsList.append(empty); return;
  }
  for (const request of snapshot.requests.slice(0, 12)) {
    const item = document.createElement('li');
    const identity = document.createElement('span');
    identity.innerHTML = `<strong>#${request.id}</strong><small>${request.clientIP}</small>`;
    const route = document.createElement('span'); route.textContent = request.backend ?? '—';
    const status = document.createElement('span'); status.className = `request-status ${request.status === '200 OK' ? 'ok' : request.status === 'processing' ? 'active' : 'bad'}`;
    status.textContent = request.status;
    item.append(identity, route, status); requestsList.append(item);
  }
}

function renderEvents(snapshot) {
  eventsList.replaceChildren();
  for (const event of snapshot.events.slice(0, 11)) {
    const item = document.createElement('li'); item.className = event.type;
    const stamp = document.createElement('span'); stamp.className = 'event-time';
    stamp.textContent = `${(event.time / 1000).toFixed(1)}s`;
    const message = document.createElement('span'); message.textContent = event.message;
    item.append(stamp, message); eventsList.append(item);
  }
}

function render() {
  const snapshot = simulation.snapshot();
  const seconds = snapshot.time / 1000;
  clock.textContent = `T + ${String(Math.floor(seconds / 60)).padStart(2, '0')}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
  renderBackends(snapshot); renderMetrics(snapshot); renderRequests(snapshot); renderEvents(snapshot);
}

function sendRequest() {
  const result = simulation.stepRequest(clientSource.value === 'auto' ? null : clientSource.value);
  const request = result.request;
  requestTitle.textContent = `Request #${request.id} · ${request.status}`;
  requestDetail.textContent = `${request.clientIP} → ${request.backend ?? 'transit link'} · ${simulation.config.policy.toUpperCase()}${request.latency ? ` · ${Math.round(request.latency)} ms modeled service` : ''}`;
  decisionExplanation.textContent = request.decision;
  render();
  animateRequest(request);
  animateCompletions(result.completions);
  animateProbes(result.probes);
}

function probeHealth() {
  const result = simulation.advanceHealth();
  requestTitle.textContent = `Health cycle at T + ${(result.time / 1000).toFixed(0)}s`;
  requestDetail.textContent = 'The load balancer probed B1, B2, and B3. Watch the counters and route status.';
  render(); animateCompletions(result.completions); animateProbes(result.probes);
}

function reset() {
  stop(); clearAnimations(); simulation.reset(simulation.config);
  requestTitle.textContent = 'Ready for request #1';
  requestDetail.textContent = 'Send a request to trace its route. Stop B2 to see detection-window 502s.';
  decisionExplanation.textContent = 'Select a policy and send a request to see the selection rule.';
  initializeBackendCards(); render();
}

rate.addEventListener('input', () => { document.querySelector('#rate-value').textContent = rate.value; });
loss.addEventListener('input', () => { document.querySelector('#loss-value').textContent = `${loss.value}%`; });
form.addEventListener('submit', event => {
  event.preventDefault(); if (!form.reportValidity()) return;
  stop(); clearAnimations();
  try { simulation = new Simulation(settings()); reset(); }
  catch (error) { window.alert(error.message); }
});
document.querySelector('#step-button').addEventListener('click', sendRequest);
document.querySelector('#probe-button').addEventListener('click', probeHealth);
document.querySelector('#reset-button').addEventListener('click', reset);
playButton.addEventListener('click', () => {
  if (timer !== null) { stop(); return; }
  timer = setInterval(sendRequest, 1150);
  playButton.textContent = 'Pause'; playButton.setAttribute('aria-pressed', 'true');
});
window.addEventListener('keydown', event => {
  if (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(document.activeElement?.tagName)) return;
  if (event.code === 'Space') { event.preventDefault(); playButton.click(); }
  if (event.code === 'ArrowRight') { event.preventDefault(); sendRequest(); }
});
initializeBackendCards(); render();
