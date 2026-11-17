"""Checks measured-log parsing without requiring namespaces or third-party packages."""

from pathlib import Path
import tempfile
import unittest

from analyze_results import expected_tags, parse_hey, parse_run, summarize


class AnalyzeResultsTests(unittest.TestCase):
    def test_expected_manifest_has_60_unique_trials(self) -> None:
        self.assertEqual(len(expected_tags()), 60)

    def test_hey_status_and_connection_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hey.txt"
            path.write_text("""Requests/sec: 12.50
  50% in 0.0100 secs
  95% in 0.0400 secs
  99% in 0.0800 secs
Status code distribution:
  [200] 8 responses
  [502] 1 responses
Error distribution:
  [1] Get http://example/: connection refused
""")
            result = parse_hey(path)
            self.assertEqual(result["total_requests"], 10)
            self.assertEqual(result["failed_requests"], 2)
            self.assertEqual(result["error_rate_pct"], 20.0)
            self.assertEqual(result["p95_ms"], 40.0)

    def test_trial_uses_backend_logs_and_events(self) -> None:
        tag = "E3_slb_rr_loss0_c50_r1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / f"{tag}_hey.txt").write_text("""Requests/sec: 2.00
50% in 0.010 secs
95% in 0.020 secs
99% in 0.030 secs
[200] 2 responses
""")
            for backend in ("b1", "b2", "b3"):
                recv = 112.0 if backend == "b2" else 99.0
                (root / f"{tag}_{backend}.log").write_text(
                    f"[RECV_EPOCH={recv:.6f}] [BACKEND={backend.upper()}] [TYPE=PROXY] status=200\n")
            (root / f"{tag}_events.txt").write_text("STOP_EPOCH=100.000000\nRESTART_EPOCH=110.000000\n")
            (root / f"{tag}_proxy.log").write_text(
                "[EPOCH=102.500000] [HEALTH_STATE] Backend B2 marked DOWN\n")
            row = parse_run(root, tag)
            self.assertEqual(row["detection_time_s"], 2.5)
            self.assertEqual(row["recovery_time_s"], 2.0)
            self.assertEqual(row["b2_requests"], 1)
            self.assertEqual(summarize([row])[0]["runs"], 1)


if __name__ == "__main__":
    unittest.main()
