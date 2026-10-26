#!/usr/bin/env python3
"""Exercise the real Docker/CLI lifecycle with a fake reviewer; never calls a model."""
import json
import os
import re
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
from reviewlib.records import digest, load, validate_output


class RuntimeTests(GitCase):
    images = {}

    def setUp(self):
        super().setUp()
        self.campaign_spec()
        self.croot = self.root / "campaign"
        onboarding.freeze(self.root / "spec.json", self.croot)
        self.config = self.root / "host.toml"
        self.config.write_text('model = "synthetic-exact-model"\nmodel_reasoning_effort = "high"\n')
        self.auth = self.root / "auth.json"
        self.auth.write_text('{"synthetic": true, "mode": "finished"}')
        self.auth.chmod(0o600)
        self.trial = self.croot / "attempts/train0--focused--0"
        self.containers = set()

    def tearDown(self):
        try:
            # Clean only this test's named containers, after outcome assertions.
            for trial in (self.croot / "attempts").glob("*"):
                self.containers.add("crc-" + digest(str(trial))[:20])
            for name in sorted(self.containers):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, check=True)
        finally:
            super().tearDown()

    def command(self, image="finished", seconds=10):
        return [sys.executable, str(SKILL / "scripts/review.py"), "onboard", "run",
                "--campaign", str(self.croot), "--image", self.images[image],
                "--host-config", str(self.config), "--auth", str(self.auth),
                "--max-calls", "1", "--max-wall-seconds", "60", "--trial-seconds", str(seconds)]

    def container_absent(self, name):
        # A daemon error must not masquerade as successful container removal.
        names = subprocess.check_output(["docker", "ps", "-a", "--format", "{{.Names}}"],
                                        text=True, timeout=15).splitlines()
        self.assertNotIn(name, names, "Container survived production cleanup")

    def assert_trial_removed(self, trial=None):
        self.container_absent("crc-" + digest(str(trial or self.trial))[:20])

    def run_mode(self, mode, seconds=10):
        self.auth.write_text(json.dumps({"synthetic": True, "mode": mode}))
        result = subprocess.run(self.command(seconds=seconds), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["launched"], 1)
        receipt = load(self.trial / "receipt.json", "trial-receipt")
        self.assertEqual(receipt["status"], mode)
        self.assertTrue(receipt["launched"])
        self.assert_trial_removed()
        return receipt

    def test_success_captures_stdin_output_usage_and_removes_container(self):
        receipt = self.run_mode("finished")
        self.assertEqual(receipt["tokens"], 120)
        self.assertEqual(receipt["trace_hash"], digest((self.trial / "trace.jsonl").read_bytes()))
        self.assertEqual(receipt["result_hash"], digest((self.trial / "result.json").read_bytes()))
        result = load(self.trial / "result.json", "calibration-result")
        validate_output(result, "calibration-result")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["inspected_hunks"])

    def test_process_failure_is_retained_on_resume(self):
        self.run_mode("failed")
        before = (self.trial / "receipt.json").read_bytes()
        result = subprocess.run(self.command(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["launched"], 1)
        self.assertEqual((self.trial / "receipt.json").read_bytes(), before)
        second = self.croot / "attempts/train0--focused--1"
        self.assertEqual(load(second / "receipt.json", "trial-receipt")["status"], "failed")
        self.assert_trial_removed(second)

    def test_timeout_retains_incomplete_evidence_and_removes_container(self):
        receipt = self.run_mode("timeout", seconds=3)
        self.assertIsNone(receipt["result_hash"])
        self.assertIsNone(receipt["tokens"])
        self.assertTrue(receipt["trace_hash"])

    def interrupted_run(self, image, ready):
        stderr_path = self.root / "runner-stderr.txt"
        with (self.root / "runner-stdout.txt").open("wb") as stdout, stderr_path.open("wb") as stderr:
            with subprocess.Popen(self.command(image, seconds=30), stdout=stdout, stderr=stderr) as process:
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if ready(stderr_path):
                            break
                        if process.poll() is not None:
                            self.fail("Runner exited before fixture started: " + stderr_path.read_text())
                        time.sleep(0.05)
                    else:
                        self.fail("Fixture did not start: " + stderr_path.read_text())
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=25)
                    self.assertNotEqual(process.returncode, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
        return stderr_path.read_text()

    def test_interrupt_retains_attempt_and_removes_container(self):
        self.auth.write_text('{"synthetic": true, "mode": "interrupted"}')
        trace = self.trial / "trace.jsonl"
        self.interrupted_run("finished", lambda _: trace.exists() and "runtime.fake.started" in trace.read_text())
        receipt = load(self.trial / "receipt.json", "trial-receipt")
        self.assertEqual(receipt["status"], "interrupted")
        self.assertTrue(receipt["launched"])
        self.assertGreater(receipt["elapsed_seconds"], 0)
        self.assertIsNone(receipt["result_hash"])
        self.assert_trial_removed()

    def probe_name(self, stderr):
        match = re.search(r"Version probe container: (crc-version-[0-9a-f]{32})", stderr)
        self.assertIsNotNone(match, stderr)
        name = match.group(1)
        self.containers.add(name)
        return name

    def test_version_probe_timeout_removes_container_without_starting_trial(self):
        result = subprocess.run(self.command("timeout"), capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        name = self.probe_name(result.stderr)
        self.assertIn("timed out", result.stderr)
        self.assertFalse((self.croot / "attempts").exists())
        self.container_absent(name)

    def test_version_probe_interrupt_removes_container_without_starting_trial(self):
        def running(path):
            stderr = path.read_text()
            if "Version probe container:" not in stderr:
                return False
            name = self.probe_name(stderr)
            result = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                                    capture_output=True, text=True, timeout=15)
            return result.returncode == 0 and result.stdout.strip() == "true"

        stderr = self.interrupted_run("interrupted", running)
        self.assertFalse((self.croot / "attempts").exists())
        self.container_absent(self.probe_name(stderr))


def main():
    if os.name != "posix":
        raise SystemExit("Docker lifecycle tests require a POSIX host")
    engine = subprocess.check_output(["docker", "info", "--format", "{{.OSType}}"], text=True, timeout=30).strip()
    if engine != "linux":
        raise SystemExit("Docker lifecycle tests require Linux containers")
    with tempfile.TemporaryDirectory(prefix="crc-runtime-build-") as temp:
        for mode in ("finished", "timeout", "interrupted"):
            iid = Path(temp) / mode
            subprocess.run(["docker", "build", "--iidfile", str(iid), "--build-arg", "PROBE_MODE=" + mode,
                            str(ROOT / "tests/runtime")], check=True, timeout=300)
            image = iid.read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise ValueError("Docker did not return an immutable image ID")
            RuntimeTests.images[mode] = image
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeTests)
        return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


if __name__ == "__main__":
    sys.exit(main())
