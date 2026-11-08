import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_title as ct
import install_hooks as ih

SCRIPT = Path("C:/p/scripts/claude_title.py")
OTHER_STOP = {"hooks": [{"type": "command", "command": "echo hi"}]}
OTHER_SETTINGS = {"permissions": {"allow": ["Bash(ls)"]}, "model": "opus",
                  "hooks": {"Stop": [OTHER_STOP],
                            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]}}


class InstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.settings = self.dir / "settings.json"

    def write(self, value):
        self.settings.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def backups(self):
        return sorted(p.name for p in self.dir.glob("settings.json.bak-*"))

    def test_install_creates_file_with_three_hooks(self):
        result = ih.install(self.settings, SCRIPT, python_cmd="python")
        self.assertEqual((result["changed"], result["backup"]), (True, None))
        hooks = self.read()["hooks"]
        self.assertEqual(hooks["Stop"], [{"hooks": [{
            "type": "command", "command": 'python -X utf8 "C:/p/scripts/claude_title.py" stop',
            "async": True, "timeout": 180}]}])
        self.assertEqual(hooks["UserPromptSubmit"], [{"hooks": [
            {"type": "command", "command": 'python -X utf8 "C:/p/scripts/claude_title.py" prompt', "timeout": 10},
            {"type": "mcp_tool", "server": "ccd_session_mgmt", "tool": "set_session_title",
             "input": {"session_id": "self", "title": "${session_title}"}, "timeout": 20}]}])

    def test_install_is_idempotent(self):
        ih.install(self.settings, SCRIPT, python_cmd="python")
        first = self.settings.read_bytes()
        result = ih.install(self.settings, SCRIPT, python_cmd="python")
        self.assertEqual((result["changed"], result["backup"]), (False, None))
        self.assertEqual(self.settings.read_bytes(), first)
        self.assertEqual(self.backups(), [])

    def test_install_preserves_other_hooks_and_keys(self):
        self.write(OTHER_SETTINGS)
        ih.install(self.settings, SCRIPT, python_cmd="python")
        data = self.read()
        self.assertEqual(data["permissions"], OTHER_SETTINGS["permissions"])
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["PreToolUse"], OTHER_SETTINGS["hooks"]["PreToolUse"])
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        self.assertEqual(data["hooks"]["Stop"][0], OTHER_STOP)

    def test_install_backs_up_existing_file(self):
        self.write(OTHER_SETTINGS)
        original = self.settings.read_text(encoding="utf-8")
        result = ih.install(self.settings, SCRIPT, python_cmd="python")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(Path(result["backup"]).read_text(encoding="utf-8"), original)

    def test_install_dry_run_writes_nothing(self):
        result = ih.install(self.settings, SCRIPT, dry_run=True, python_cmd="python")
        self.assertTrue(result["changed"])
        self.assertIn("Stop", result["settings"]["hooks"])
        self.assertFalse(self.settings.exists())
        self.write(OTHER_SETTINGS)
        before = self.settings.read_bytes()
        ih.install(self.settings, SCRIPT, dry_run=True, python_cmd="python")
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_install_invalid_json_untouched(self):
        for text in ("{not json", "[1, 2]"):
            with self.subTest(text=text):
                self.settings.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    ih.install(self.settings, SCRIPT, python_cmd="python")
                self.assertEqual(self.settings.read_text(encoding="utf-8"), text)
                self.assertEqual(self.backups(), [])

    def test_command_quotes_path_with_spaces_and_cjk(self):
        groups = ih.hook_groups(Path("C:/我的 工具/scripts/claude_title.py"), "python")
        command = groups["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(command, 'python -X utf8 "C:/我的 工具/scripts/claude_title.py" stop')

    def test_python_cmd_default_by_platform(self):
        for platform, expected in (("win32", "python -X utf8"), ("linux", "python3 -X utf8"),
                                   ("darwin", "python3 -X utf8")):
            with self.subTest(platform=platform), mock.patch.object(ih.sys, "platform", platform):
                command = ih.hook_groups(SCRIPT)["Stop"][0]["hooks"][0]["command"]
                self.assertTrue(command.startswith(expected), command)

    def test_uninstall_removes_only_ours(self):
        self.write(OTHER_SETTINGS)
        ih.install(self.settings, SCRIPT, python_cmd="python")
        result = ih.uninstall(self.settings)
        self.assertTrue(result["changed"])
        self.assertEqual(self.read(), OTHER_SETTINGS)

    def test_uninstall_cleans_empty_events_and_keeps_other_keys(self):
        self.write({"model": "opus"})
        ih.install(self.settings, SCRIPT, python_cmd="python")
        ih.uninstall(self.settings)
        self.assertEqual(self.read(), {"model": "opus"})

    def test_uninstall_when_not_installed_is_noop(self):
        self.write(OTHER_SETTINGS)
        result = ih.uninstall(self.settings)
        self.assertEqual((result["changed"], result["backup"]), (False, None))
        self.assertEqual(self.backups(), [])
        self.assertEqual(ih.uninstall(self.dir / "missing.json")["changed"], False)

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows 有 260 字符路径限制")
    def test_install_and_uninstall_work_on_long_settings_path(self):
        import shutil
        bs = chr(92)
        prefix = bs * 2 + "?" + bs
        deep = Path(os.path.abspath(self.dir)) / bs.join(["d" * 50] * 6)
        self.addCleanup(shutil.rmtree, prefix + os.path.abspath(self.dir), True)
        os.makedirs(prefix + str(deep))
        settings = deep / "settings.json"
        self.assertGreater(len(str(settings)), 260)
        with open(prefix + str(settings), "w", encoding="utf-8") as stream:
            stream.write('{"model": "x"}')
        result = ih.install(settings, SCRIPT, python_cmd="python")
        self.assertTrue(result["changed"])
        self.assertTrue(result["backup"])
        with open(prefix + str(settings), encoding="utf-8") as stream:
            data = json.load(stream)
        self.assertEqual(data["model"], "x")
        self.assertIn("Stop", data["hooks"])
        self.assertEqual(ih.is_installed(settings), {"stop": True, "prompt": True, "mcp": True})
        self.assertTrue(ih.uninstall(settings)["changed"])
        with open(prefix + str(settings), encoding="utf-8") as stream:
            self.assertEqual(json.load(stream), {"model": "x"})

    def test_is_installed_reports_each_hook(self):
        self.assertEqual(ih.is_installed(self.settings), {"stop": False, "prompt": False, "mcp": False})
        ih.install(self.settings, SCRIPT, python_cmd="python")
        self.assertEqual(ih.is_installed(self.settings), {"stop": True, "prompt": True, "mcp": True})
        data = self.read()
        data["hooks"]["UserPromptSubmit"][0]["hooks"].pop()  # 去掉 mcp hook
        self.write(data)
        self.assertEqual(ih.is_installed(self.settings), {"stop": True, "prompt": True, "mcp": False})


class MainInstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.settings = self.dir / "settings.json"
        self.root = self.dir / "data"

    def run_main(self, args):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_TITLE_DATA": str(self.root), "CLAUDE_TITLE_WORKER": ""}):
            code = ct.main(args, stdin=io.StringIO(""), stdout=out)
        return code, json.loads(out.getvalue())

    def test_main_install_dry_run_then_install_then_uninstall(self):
        args = ["--settings-file", str(self.settings)]
        code, out = self.run_main(["install", *args, "--dry-run"])
        self.assertEqual((code, out["changed"], out["dry_run"]), (0, True, True))
        self.assertIn("UserPromptSubmit", out["hooks"])
        self.assertFalse(self.settings.exists())
        code, out = self.run_main(["install", *args])
        self.assertEqual((code, out["changed"]), (0, True))
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertIn("claude_title.py", data["hooks"]["Stop"][0]["hooks"][0]["command"])
        code, out = self.run_main(["uninstall", *args])
        self.assertEqual((code, out["changed"]), (0, True))
        self.assertNotIn("hooks", json.loads(self.settings.read_text(encoding="utf-8")))

    def test_main_install_invalid_json_reports_error(self):
        self.settings.write_text("{bad", encoding="utf-8")
        code, out = self.run_main(["install", "--settings-file", str(self.settings)])
        self.assertEqual((code, out["status"]), (1, "error"))
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{bad")

    def test_doctor_reports_environment(self):
        ct.atomic_json(self.root / "config.json", {"claude_bin": sys.executable})
        args = ["--settings-file", str(self.settings)]
        code, report = self.run_main(["doctor", *args])
        self.assertEqual(code, 0)
        self.assertTrue(report["python_ok"])
        self.assertTrue(Path(report["claude"]).samefile(sys.executable))
        self.assertTrue(report["claude_version"])
        self.assertTrue(report["data_dir_writable"])
        self.assertEqual(report["hooks"], {"stop": False, "prompt": False, "mcp": False})
        self.run_main(["install", *args])
        self.assertEqual(self.run_main(["doctor", *args])[1]["hooks"], {"stop": True, "prompt": True, "mcp": True})

    def test_doctor_reports_missing_claude_without_failing(self):
        ct.atomic_json(self.root / "config.json", {"claude_bin": "definitely-not-a-real-command-xyz"})
        code, report = self.run_main(["doctor", "--settings-file", str(self.settings)])
        self.assertEqual(code, 0)
        self.assertIsNone(report["claude"])
        self.assertIn("claude_error", report)

    def test_doctor_live_uses_injected_generator(self):
        calls = []

        def generator(context):
            calls.append(context)
            return {"action": "rename", "title": "🧩 示例｜检查", "reason": "r"}
        report = ct.doctor(self.root, self.dir / "settings.json", live=True, generator=generator)
        self.assertEqual(report["live"]["title"], "🧩 示例｜检查")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
