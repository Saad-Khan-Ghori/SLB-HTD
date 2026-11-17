# Proxy and benchmark improvement priorities

Last reviewed: 2026-11-17. Review after major project changes.

The measured results in [`MEASURED_RESULTS.md`](../MEASURED_RESULTS.md) are useful, but they do not yet isolate the proxy's capacity. Fix the test setup first, then address protocol behavior.

## 1. Remove the client-side test bottleneck

Several `hey` trials reported `dial tcp ... cannot assign requested address` from the client namespace: 6,473 such errors across the run. This is consistent with ephemeral source-port exhaustion during rapid one-request-per-connection traffic. It is a **load-generator limitation**, not evidence that the proxy refused those connections. Preserve the current logs, then repeat E1 and E4 with a measured client-side mitigation, such as spreading requests across multiple configured source IPs or reducing the connection churn. Record the exact change and apply it to every compared policy and proxy. Also record client socket/TIME_WAIT counts and host CPU use during trials.

## 2. Fix HTTP error handling

`slb.py` reads request headers and forwards them to a backend but does not forward a request body. It should explicitly accept the documented GET-only scope and reject unsupported methods or body-bearing requests with a clear HTTP response. If backend I/O fails after some response bytes have reached the client, close the connection and log a truncated response; appending a new 502 status line would produce an invalid HTTP stream.

## 3. Make health timing precise

The health checker applies a timeout separately to connect, write, and status read. Use one total deadline for each probe if `--health-timeout 0.5` is meant to cap the entire check at 500 ms. Measure HAProxy DOWN transitions with timestamped logs in a future run; the current HAProxy warning lines lack timestamps, so its detection time is unavailable.

## 4. Separate a teaching proxy from production features

The one-request-per-TCP design and active-only failover match the course demonstration. For a production-oriented extension, add keep-alive/backend pooling, guarded retries for idempotent requests during the detection window, and consistent hashing to reduce IP-hash remapping when the healthy pool changes. Test these as separate modes so comparisons with the original design remain clear.
