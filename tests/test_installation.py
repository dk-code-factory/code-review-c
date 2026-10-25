"""Exercise artifact boundaries through both supported installation layouts."""
import json
import shutil
import subprocess
import sys

from support import GitCase, ROOT, SKILL


class InstallationTests(GitCase):
    def install(self, plugin=False):
        home = self.root / "home"
        package = home / "plugin" if plugin else home / ".codex" / "skills" / "code-review-c"
        skill = package / "codex" / "skills" / "code-review-c" if plugin else package
        shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
        if plugin:
            (package / ".codex-plugin").mkdir()
            shutil.copyfile(ROOT / ".codex-plugin" / "plugin.json", package / ".codex-plugin" / "plugin.json")
        return home, package, skill

    def cli(self, skill, *args, success=True):
        result = subprocess.run([sys.executable, str(skill / "scripts" / "review.py"), *map(str, args)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
        return json.loads(result.stdout if success else result.stderr)

    def test_home_artifacts_work_in_both_installations(self):
        for plugin in (False, True):
            with self.subTest(plugin=plugin):
                home, package, skill = self.install(plugin)
                output = home / ("plugin-artifacts" if plugin else "copied-artifacts")
                prepared = self.cli(skill, "onboard", "prepare", "--out", output / "preparation")
                self.assertEqual(prepared["kind"], "campaign-spec")
                inv = self.cli(skill, "inventory", "--repo", self.repo, "--base", self.base,
                               "--head", self.head, "--out", output / "bundle")
                self.assertEqual(inv["head"], self.head)
                plan = self.cli(skill, "plan", "--bundle", output / "bundle", "--out", output / "plan.json")
                self.assertTrue(plan["tasks"])
                self.assertFalse(plan["gaps"])

    def test_package_and_reviewed_repository_stay_forbidden(self):
        for plugin in (False, True):
            with self.subTest(plugin=plugin):
                home, package, skill = self.install(plugin)
                blocked = package / "artifacts"
                result = self.cli(skill, "onboard", "prepare", "--out", blocked, success=False)
                self.assertIn("outside repository/package", result["error"])
                self.assertFalse(blocked.exists())
                self.cli(skill, "inventory", "--repo", self.repo, "--base", self.base,
                         "--head", self.head, "--out", self.repo / "artifacts", success=False)
                self.assertFalse((self.repo / "artifacts").exists())

    def test_symlink_into_package_stays_forbidden(self):
        home, package, skill = self.install()
        link = home / "linked-package"
        link.symlink_to(package, target_is_directory=True)
        result = self.cli(skill, "onboard", "prepare", "--out", link / "artifacts", success=False)
        self.assertIn("outside repository/package", result["error"])
