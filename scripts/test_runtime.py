#!/usr/bin/env python3
"""Run real Docker lifecycle checks with synthetic auth and a fake Codex executable."""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from support import GitCase, SKILL
from reviewlib import onboarding
from reviewlib.records import digest, load


class RuntimeTests(GitCase):
    image = None

    def setUp(self):
        super().setUp()
        self.campaign_spec()
        self.croot = self.root / "campaign"
        onboarding.freeze(self.root / "spec.json", self.croot)
        self.config = self.root / "host.toml"
        self.config.write_text('model = "synthetic-exact-model"\nmodel_reasoning_effort = "high"\n')
        self.auth = self.root / "auth.json"
        self.trial = self.croot / "attempts/train0--focused--0"

    def tearDown(self):
        try:
            for trial in (self.croot / "attempts").glob("*"):
                with self.subTest(cleanup=trial.name):
                    subprocess.run(["docker", "rm", "-f", "crc-" + digest(str(trial))[:20]],
                                   capture_output=True, timeout=15, check=True)
        finally:
            super().tearDown()

    def credentials(self, mode):
        self.auth.write_text(json.dumps({"synthetic": True, "mode": mode}))
        self.auth.chmod(0o600)

    def container_absent(self, trial=None):
        name = "crc-" + digest(str(trial or self.trial))[:20]
        result = subprocess.run(["docker", "container", "inspect", name],
                                capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0, "Trial container survived cleanup")

    def run_mode(self, mode, seconds=5):
        self.credentials(mode)
        result = onboarding.run(self.croot, self.image, self.config, self.auth, 1, 30, seconds)
        self.assertEqual(result["launched"], 1)
        receipt = load(self.trial / "receipt.json", "trial-receipt")
        self.assertEqual(receipt["status"], mode)
        self.assertTrue(receipt["launched"])
        self.container_absent()
        return receipt

    def test_success_captures_output_usage_and_removes_container(self):
        receipt = self.run_mode("finished")
        self.assertEqual(receipt["tokens"], 120)
        self.assertTrue(receipt["trace_hash"])
        self.assertTrue(receipt["result_hash"])
        result = load(self.trial / "result.json", "calibration-result")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["inspected_hunks"])

    def test_process_failure_is_retained_without_retry(self):
        self.run_mode("failed")
        before = (self.trial / "receipt.json").read_bytes()
        result = onboarding.run(self.croot, self.image, self.config, self.auth, 1, 30, 5)
        self.assertEqual(result["launched"], 1)
        self.assertEqual((self.trial / "receipt.json").read_bytes(), before)
        second = self.croot / "attempts/train0--focused--1"
        self.assertEqual(load(second / "receipt.json", "trial-receipt")["status"], "failed")
        self.container_absent()
        self.container_absent(second)

    def test_timeout_removes_container_and_keeps_incomplete_evidence(self):
        receipt = self.run_mode("timeout", seconds=2)
        self.assertIsNone(receipt["result_hash"])
        self.assertIsNone(receipt["tokens"])

    def test_interrupt_removes_container_and_preserves_started_call(self):
        self.credentials("interrupted")
        command = [sys.executable, str(SKILL / "scripts/review.py"), "onboard", "run",
                   "--campaign", str(self.croot), "--image", self.image,
                   "--host-config", str(self.config), "--auth", str(self.auth),
                   "--max-calls", "1", "--max-wall-seconds", "30", "--trial-seconds", "20"]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                deadline = time.monotonic() + 15
                trace = self.trial / "trace.jsonl"
                while time.monotonic() < deadline:
                    if trace.exists() and "runtime.fake.started" in trace.read_text():
                        break
                    if process.poll() is not None:
                        self.fail("Runner exited before fake reviewer started")
                    time.sleep(0.05)
                else:
                    self.fail("Fake reviewer never started")
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0, stdout + stderr)
                self.container_absent()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
        receipt = load(self.trial / "receipt.json", "trial-receipt")
        self.assertEqual(receipt["status"], "interrupted")
        self.assertTrue(receipt["launched"])
        self.assertGreater(receipt["elapsed_seconds"], 0)
        self.assertIsNone(receipt["result_hash"])
        self.container_absent()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Existing fake-reviewer image, pinned by digest or local sha256 ID")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("Docker runtime integration requires a POSIX host")
    with tempfile.TemporaryDirectory(prefix="crc-runtime-build-") as temp:
        if args.image:
            RuntimeTests.image = args.image
        else:
            iid = Path(temp) / "image-id"
            subprocess.run(["docker", "build", "--iidfile", str(iid), str(ROOT / "tests/runtime")],
                           check=True, timeout=300)
            RuntimeTests.image = iid.read_text().strip()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeTests)
        return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


if __name__ == "__main__":
    sys.exit(main())
