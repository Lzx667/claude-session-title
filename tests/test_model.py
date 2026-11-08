import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import claude_model as model

FAKE = [sys.executable, "-X", "utf8", str(Path(__file__).resolve().parent / "fake_claude.py")]
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "naming.md"
CANDIDATE = {"action": "rename", "title": "🧩 登录表单｜布局优化", "reason": "依据"}
CFG = {"model": "haiku", "claude_bin": None, "model_timeout_seconds": 20}


class BuildArgvTest(unittest.TestCase):
    def test_build_argv_has_isolation_flags(self):
        argv = model.build_argv("claude", CFG, PROMPT)
        self.assertEqual(argv[0], "claude")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--system-prompt-file") + 1], str(PROMPT))
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), model.SCHEMA)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), {"disableAllHooks": True})

    def test_build_argv_accepts_command_prefix(self):
        self.assertEqual(model.build_argv(["py", "x.py"], CFG, PROMPT)[:2], ["py", "x.py"])


class ParseOutputTest(unittest.TestCase):
    def wrap(self, **fields):
        return json.dumps({"type": "result", **fields}, ensure_ascii=False)

    def test_parse_structured_output(self):
        self.assertEqual(model.parse_output(self.wrap(structured_output=CANDIDATE, result="")), CANDIDATE)

    def test_parse_result_with_code_fence(self):
        text = "```json\n" + json.dumps(CANDIDATE, ensure_ascii=False) + "\n```"
        self.assertEqual(model.parse_output(self.wrap(result=text)), CANDIDATE)

    def test_parse_result_with_surrounding_prose(self):
        text = "好的：" + json.dumps(CANDIDATE, ensure_ascii=False) + " 完毕 {不是json"
        self.assertEqual(model.parse_output(self.wrap(result=text)), CANDIDATE)

    def test_parse_rejects_garbage(self):
        for stdout in ("plain text", "", "[1,2]", self.wrap(result="这里没有 JSON"), self.wrap(result=5),
                       self.wrap(is_error=True, result="Not logged in")):
            with self.subTest(stdout=stdout):
                with self.assertRaises(model.ModelError):
                    model.parse_output(stdout)


class FindClaudeTest(unittest.TestCase):
    def test_find_claude_missing_raises(self):
        with self.assertRaises(model.ModelError):
            model.find_claude("definitely-not-a-real-command-xyz")

    def test_find_claude_returns_existing_path(self):
        self.assertTrue(Path(model.find_claude(sys.executable)).samefile(sys.executable))

    def test_find_claude_resolves_cmd_to_native_exe_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = Path(tmp) / "claude.cmd"
            cmd.write_text("@echo off", encoding="utf-8")
            exe = Path(tmp) / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            with mock.patch.object(model.sys, "platform", "win32"), \
                    mock.patch.object(model.shutil, "which", return_value=str(cmd)):
                self.assertEqual(model.find_claude(), str(exe))
                exe.unlink()
                with self.assertRaises(model.ModelError):
                    model.find_claude()


class GenerateCandidateTest(unittest.TestCase):
    def run_fake(self, mode, config=CFG, context=None):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.json"
            env = {"FAKE_CLAUDE_MODE": mode, "FAKE_CLAUDE_LOG": str(log)}
            with mock.patch.dict(os.environ, env):
                result = model.generate_candidate(config, context or {"recent_turns": []}, PROMPT, claude=FAKE)
            return result, json.loads(log.read_text(encoding="utf-8"))

    def test_generate_candidate_success_sets_worker_env_and_temp_cwd(self):
        context = {"current_title": "", "recent_turns": [{"id": "u1", "messages": [{"role": "user", "text": "你好"}]}]}
        result, seen = self.run_fake("structured", context=context)
        self.assertEqual(result, CANDIDATE)
        self.assertEqual(seen["worker"], "1")
        self.assertNotEqual(Path(seen["cwd"]).resolve(), Path.cwd().resolve())
        self.assertIn("--no-session-persistence", seen["argv"])
        self.assertEqual(json.loads(seen["stdin"]), context)

    def test_generate_candidate_handles_fenced_and_prose(self):
        for mode in ("fenced", "prose"):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_fake(mode)[0], CANDIDATE)

    def test_generate_candidate_nonzero_exit_raises(self):
        with self.assertRaises(model.ModelError):
            self.run_fake("fail")

    def test_generate_candidate_bad_output_raises(self):
        for mode in ("error", "notjson", "garbage"):
            with self.subTest(mode=mode):
                with self.assertRaises(model.ModelError):
                    self.run_fake(mode)

    def test_generate_candidate_timeout_raises_quickly(self):
        started = time.monotonic()
        with self.assertRaises(model.ModelError):
            self.run_fake("slow", config={**CFG, "model_timeout_seconds": 1})
        self.assertLess(time.monotonic() - started, 4)

    def test_generate_candidate_missing_binary_raises(self):
        with self.assertRaises(model.ModelError):
            model.generate_candidate(CFG, {}, PROMPT, claude=["definitely-not-a-real-command-xyz"])


if __name__ == "__main__":
    unittest.main()
