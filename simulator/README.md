# Visual network simulator

Last reviewed: 2026-11-17. Review after major project changes.

Run from the repository root:

```bash
python3 simulator/serve.py
```

Open `http://127.0.0.1:8765/` in a browser. No root access, packages, or live topology are required. Click **Send request** to watch one numbered request travel from a selected client IP through the routers and proxy to its backend. Click repeatedly to see round robin choose B1, B2, B3, then B1 again. **Play** sends requests automatically; **Probe +1s** advances to the next health check without adding traffic.

Stop B2, then use **Probe +1s** three times to see the fall threshold mark it DOWN. Requests sent before that transition can receive 502; later requests skip B2. Restart it and advance two more probes to see it re-admitted. Change the policy to least connections to see the current active counts used in each decision, or select one fixed client IP with IP hash to see affinity. Apply a different traffic rate, backend delay, loss chance, or health threshold to reset the run.

The topology animation shows each client request, response, health probe, and illustrative transit loss. The route ledger lists the client, backend, and result for every request. Backend panels show current active work, service progress, served totals, and health state. The selection explanation shows why each policy chose its route. The event stream and recent-request chart retain a short history. Animations are deliberately slower than the modelled service times so the path can be seen.

**Scope:** This is an illustrative, browser-only model. Its loss control applies a simple per-request drop chance. It does not run `slb.py`, HAProxy, `hey`, Linux namespaces, or TCP retransmission; its numbers are not benchmark measurements. Use `run_poc_demo.bash` and `run_experiments.bash` for live evidence.

Check the scheduling and health-state model with `node simulator/test_engine.mjs`.
