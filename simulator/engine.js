export const DEFAULT_CONFIG = Object.freeze({
  policy: 'rr', requestsPerStep: 3, lossPct: 0, fall: 3, rise: 2,
  delays: [0, 50, 150], clientIPs: ['192.168.10.50', '192.168.10.51', '192.168.10.52'],
  hashSeed: 3, seed: 20261103,
});

function crc32(value, seed = 0) {
  let crc = (seed ^ 0xffffffff) >>> 0;
  for (const byte of new TextEncoder().encode(value)) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc & 1) ? ((crc >>> 1) ^ 0xedb88320) >>> 0 : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)];
}

export class Simulation {
  constructor(options = {}) { this.reset(options); }

  reset(options = {}) {
    this.config = { ...DEFAULT_CONFIG, ...options, delays: [...(options.delays ?? DEFAULT_CONFIG.delays)] };
    const c = this.config;
    if (!['rr', 'lc', 'iphash'].includes(c.policy)) throw new Error('Unknown policy');
    if (!Number.isInteger(c.requestsPerStep) || c.requestsPerStep < 1 || c.requestsPerStep > 48) throw new Error('Requests per second must be 1–48');
    if (c.lossPct < 0 || c.lossPct > 100) throw new Error('Loss must be 0–100%');
    if (c.fall < 1 || c.rise < 1) throw new Error('Health thresholds must be positive');
    if (c.delays.length !== 3 || c.delays.some(delay => delay < 0 || delay > 1000)) throw new Error('Backend delays must be 0–1000 ms');
    if (!c.clientIPs.length) throw new Error('At least one client IP is required');
    this.now = 0;
    this.nextProbe = 1000;
    this.nextRequestId = 1;
    this.rrCursor = 0;
    this.randomState = c.seed >>> 0;
    this.pending = [];
    this.history = [];
    this.requests = [];
    this.events = [{ time: 0, type: 'info', message: 'Ready. Send one request to watch the route.' }];
    this.backends = c.delays.map((delay, index) => ({
      name: `B${index + 1}`, delay, physicalUp: true, logicalUp: true,
      active: 0, failCount: 0, riseCount: 0, served: 0,
    }));
    this.totals = { attempted: 0, success: 0, errors: 0, latencies: [], loss: 0, backendErrors: 0 };
    return this;
  }

  random() {
    this.randomState = (Math.imul(this.randomState, 1664525) + 1013904223) >>> 0;
    return this.randomState / 4294967296;
  }

  record(type, message, time = this.now) {
    this.events.unshift({ time, type, message });
    this.events.length = Math.min(this.events.length, 120);
  }

  setPhysical(name, up) {
    const backend = this.backends.find(item => item.name === name);
    if (!backend) throw new Error('Unknown backend');
    if (backend.physicalUp === up) return;
    backend.physicalUp = up;
    this.record(up ? 'recover' : 'failure', `${name} ${up ? 'process restarted' : 'process stopped'}; routing changes after health probes.`);
    if (!up) {
      const remaining = [];
      for (const request of this.pending) {
        if (request.backend !== backend) { remaining.push(request); continue; }
        backend.active -= 1;
        request.trace.status = '502 BAD GATEWAY';
        request.trace.finishedAt = this.now;
        this.totals.errors += 1;
        this.totals.backendErrors += 1;
        this.record('error', `#${request.trace.id} in flight on ${name} failed when the process stopped.`);
      }
      this.pending = remaining;
    }
  }

  choose(clientIP) {
    const healthy = this.backends.filter(backend => backend.logicalUp);
    if (!healthy.length) return null;
    if (this.config.policy === 'rr') {
      const chosen = healthy[this.rrCursor % healthy.length];
      this.rrCursor = (this.rrCursor + 1) % healthy.length;
      return chosen;
    }
    if (this.config.policy === 'lc') return healthy.reduce((best, item) => item.active < best.active ? item : best);
    return healthy[crc32(clientIP, this.config.hashSeed) % healthy.length];
  }

  finishDue(time, result) {
    const stillPending = [];
    for (const request of this.pending) {
      if (request.finishedAt > time) { stillPending.push(request); continue; }
      request.backend.active -= 1;
      request.backend.served += 1;
      request.trace.status = '200 OK';
      request.trace.finishedAt = request.finishedAt;
      this.totals.success += 1;
      this.totals.latencies.push(request.finishedAt - request.startedAt);
      result.completions.push({ id: request.trace.id, backend: request.backend.name, time: request.finishedAt });
      this.record('complete', `#${request.trace.id} ← ${request.backend.name} 200 OK in ${Math.round(request.finishedAt - request.startedAt)} ms`, request.finishedAt);
    }
    this.pending = stillPending;
  }

  probe(time, result) {
    for (const backend of this.backends) {
      const probe = { backend: backend.name, ok: backend.physicalUp, time, transition: null };
      if (backend.physicalUp) {
        backend.failCount = 0;
        if (!backend.logicalUp) {
          backend.riseCount += 1;
          this.record('probe', `${backend.name} /health 200 (${backend.riseCount}/${this.config.rise} to UP)`, time);
          if (backend.riseCount >= this.config.rise) {
            backend.logicalUp = true; backend.riseCount = 0; probe.transition = 'UP';
            this.record('recover', `${backend.name} marked UP and re-admitted`, time);
          }
        }
      } else {
        backend.riseCount = 0;
        if (backend.logicalUp) {
          backend.failCount += 1;
          this.record('probe', `${backend.name} /health failed (${backend.failCount}/${this.config.fall} to DOWN)`, time);
          if (backend.failCount >= this.config.fall) {
            backend.logicalUp = false; backend.failCount = 0; probe.transition = 'DOWN';
            this.record('failure', `${backend.name} marked DOWN and removed from rotation`, time);
          }
        }
      }
      result.probes.push(probe);
    }
  }

  advanceTo(target) {
    if (target < this.now) throw new Error('Simulation time cannot go backward');
    const result = { time: target, completions: [], probes: [] };
    while (true) {
      const nextFinish = this.pending.length ? Math.min(...this.pending.map(item => item.finishedAt)) : Infinity;
      const next = Math.min(nextFinish, this.nextProbe);
      if (next > target) break;
      this.now = next;
      this.finishDue(next, result);
      if (this.nextProbe === next) { this.probe(next, result); this.nextProbe += 1000; }
    }
    this.now = target;
    return result;
  }

  advanceHealth() {
    const target = Math.ceil((this.now + 1) / 1000) * 1000;
    return this.advanceTo(target);
  }

  stepRequest(clientIPOverride = null) {
    const at = this.now + 1000 / this.config.requestsPerStep;
    const progress = this.advanceTo(at);
    const id = this.nextRequestId++;
    const clientIP = clientIPOverride ?? this.config.clientIPs[(id - 1) % this.config.clientIPs.length];
    if (!this.config.clientIPs.includes(clientIP)) throw new Error('Unknown client IP');
    const trace = { id, time: at, clientIP, backend: null, status: 'processing', latency: null, finishedAt: null, decision: '' };
    this.requests.unshift(trace);
    this.requests.length = Math.min(this.requests.length, 120);
    this.totals.attempted += 1;
    if (this.random() < this.config.lossPct / 100) {
      trace.status = 'LOST';
      trace.decision = 'The request was dropped on the illustrative transit link before reaching the proxy.';
      this.totals.errors += 1; this.totals.loss += 1;
      this.record('error', `#${id} ${clientIP} dropped on the illustrative transit link`, at);
    } else {
      const healthy = this.backends.filter(item => item.logicalUp);
      if (this.config.policy === 'rr') trace.decision = `Round robin cursor ${this.rrCursor + 1} of ${healthy.length} healthy backends.`;
      if (this.config.policy === 'lc') trace.decision = `Least active connections: ${healthy.map(item => `${item.name}=${item.active}`).join(', ')}.`;
      if (this.config.policy === 'iphash') trace.decision = `CRC32(${clientIP}, seed ${this.config.hashSeed}) mod ${healthy.length} = ${healthy.length ? crc32(clientIP, this.config.hashSeed) % healthy.length : 'undefined'}.`;
      const backend = this.choose(clientIP);
      trace.backend = backend?.name ?? null;
      if (!backend) {
        trace.status = '503 NO BACKEND';
        trace.decision = 'Every backend is marked DOWN; the proxy has no destination.';
        this.totals.errors += 1; this.totals.backendErrors += 1;
        this.record('error', `#${id} ${clientIP} → 503: no healthy backend`, at);
      } else if (!backend.physicalUp) {
        trace.status = '502 BAD GATEWAY';
        this.totals.errors += 1; this.totals.backendErrors += 1;
        this.record('error', `#${id} ${clientIP} → ${backend.name} 502 during detection window`, at);
      } else {
        const latency = 36 + backend.delay + backend.active * 7;
        trace.latency = latency;
        backend.active += 1;
        this.pending.push({ backend, trace, startedAt: at, finishedAt: at + latency });
        this.record('traffic', `#${id} ${clientIP} → ${backend.name} by ${this.config.policy.toUpperCase()} (${backend.active} active)`, at);
      }
    }
    this.history.push({ id, time: at, status: trace.status, backend: trace.backend });
    if (this.history.length > 60) this.history.shift();
    return { ...progress, request: { ...trace } };
  }

  tick() {
    const started = this.now;
    const result = { time: started + 1000, attempted: 0, success: 0, errors: 0, routes: [0, 0, 0], lossErrors: 0, backendErrors: 0, requests: [], probes: [] };
    const beforeSuccess = this.totals.success;
    for (let i = 0; i < this.config.requestsPerStep; i++) {
      const step = this.stepRequest();
      const request = step.request;
      result.attempted += 1;
      result.requests.push(request);
      result.probes.push(...step.probes);
      if (request.backend && request.status === 'processing') result.routes[Number(request.backend[1]) - 1] += 1;
      if (request.status === 'LOST') result.lossErrors += 1;
      if (request.status.startsWith('502') || request.status.startsWith('503')) result.backendErrors += 1;
    }
    result.success = this.totals.success - beforeSuccess;
    result.errors = result.lossErrors + result.backendErrors;
    return result;
  }

  snapshot() {
    return {
      time: this.now, config: this.config,
      backends: this.backends.map(backend => ({ ...backend })),
      pending: this.pending.map(item => ({ id: item.trace.id, backend: item.backend.name,
        startedAt: item.startedAt, finishedAt: item.finishedAt,
        progress: Math.max(0, Math.min(1, (this.now - item.startedAt) / (item.finishedAt - item.startedAt))) })),
      totals: { attempted: this.totals.attempted, success: this.totals.success,
        errors: this.totals.errors, loss: this.totals.loss, backendErrors: this.totals.backendErrors,
        p95: percentile(this.totals.latencies, 95) },
      history: this.history.map(item => ({ ...item })),
      requests: this.requests.map(item => ({ ...item })),
      events: this.events.map(item => ({ ...item })),
    };
  }
}
