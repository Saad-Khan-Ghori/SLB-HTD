import assert from 'node:assert/strict';
import { Simulation } from './engine.js';

const individual = new Simulation({ requestsPerStep: 3 });
assert.equal(individual.stepRequest().request.backend, 'B1');
assert.equal(individual.stepRequest().request.backend, 'B2');
assert.equal(individual.stepRequest().request.backend, 'B3');
assert.equal(individual.stepRequest().request.backend, 'B1');
assert.equal(individual.snapshot().requests[0].id, 4);

const rr = new Simulation({ requestsPerStep: 3 });
const first = rr.tick();
assert.deepEqual(first.routes, [1, 1, 1]);
assert.equal(rr.snapshot().totals.success, 2); // B3 is still in flight at the first second boundary.
rr.tick();
assert.ok(rr.snapshot().totals.success >= 3);

const lc = new Simulation({ policy: 'lc', requestsPerStep: 48, delays: [0, 50, 150] });
for (let i = 0; i < 8; i++) lc.tick();
const served = lc.snapshot().backends.map(item => item.served);
assert.ok(served[0] > served[1] && served[1] > served[2], `Least connections distribution: ${served}`);

const failure = new Simulation({ requestsPerStep: 3, fall: 3, rise: 2 });
failure.setPhysical('B2', false);
assert.equal(failure.stepRequest().request.backend, 'B1');
const detectionWindow = failure.stepRequest().request;
assert.equal(detectionWindow.backend, 'B2');
assert.equal(detectionWindow.status, '502 BAD GATEWAY');
failure.tick();
assert.equal(failure.backends[1].logicalUp, true);
failure.tick();
assert.equal(failure.backends[1].logicalUp, true);
failure.tick();
assert.equal(failure.backends[1].logicalUp, false);
assert.ok(failure.snapshot().totals.errors > 0);
failure.setPhysical('B2', true);
failure.tick();
assert.equal(failure.backends[1].logicalUp, false);
failure.tick();
assert.equal(failure.backends[1].logicalUp, true);

const hash = new Simulation({ policy: 'iphash', requestsPerStep: 6, clientIPs: ['192.168.10.50'] });
const mapped = hash.tick().routes;
assert.equal(mapped.filter(Boolean).length, 1);
assert.equal(mapped.reduce((sum, n) => sum + n, 0), 6);
const selectedIP = new Simulation({ policy: 'iphash' });
const firstMapping = selectedIP.stepRequest('192.168.10.51').request;
const secondMapping = selectedIP.stepRequest('192.168.10.51').request;
assert.equal(firstMapping.backend, secondMapping.backend);
assert.match(firstMapping.decision, /CRC32/);

const lost = new Simulation({ lossPct: 100 });
assert.equal(lost.stepRequest().request.status, 'LOST');
assert.equal(lost.snapshot().totals.loss, 1);

const inflight = new Simulation({ requestsPerStep: 48 });
inflight.stepRequest(); inflight.stepRequest(); inflight.stepRequest();
assert.equal(inflight.backends[1].active, 1);
inflight.setPhysical('B2', false);
assert.equal(inflight.backends[1].active, 0);
assert.equal(inflight.snapshot().requests.find(item => item.id === 2).status, '502 BAD GATEWAY');

const none = new Simulation();
for (const name of ['B1', 'B2', 'B3']) none.setPhysical(name, false);
assert.equal(none.advanceHealth().probes.length, 3);
none.advanceHealth(); none.advanceHealth();
assert.equal(none.stepRequest().request.status, '503 NO BACKEND');
console.log('Simulation engine checks passed.');
