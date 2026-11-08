import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_title as ct
import title_core as core
from tests.helpers import assistant, title_entry, user, write_transcript

SID = "11111111-2222-3333-4444-555555555555"
TITLE = "🧩 登录表单｜布局优化"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "data"
        self.transcript = Path(self._tmp.name) / "t.jsonl"
        self.calls = []
        env = mock.patch.dict(os.environ, {"CLAUDE_TITLE_WORKER": ""})
        env.start()
        self.addCleanup(env.stop)

    def turns(self, *pairs, extra=()):
        entries = []
        for i, (u, a) in enumerate(pairs):
            entries += [user(u, f"u{i}"), assistant(a, f"a{i}")]
        write_transcript(self.transcript, entries + list(extra))
        return entries

    def event(self, **over):
        return {"session_id": SID, "transcript_path": str(self.transcript), "cwd": "C:/Users/a/Maple",
                "last_assistant_message": "", **over}

    def generator(self, **candidate):
        result = {"action": "rename", "title": TITLE, "reason": "依据", **candidate}

        def generate(context):
            self.calls.append(context)
            return result
        return generate

    def state(self):
        path = ct.state_path(self.root, SID)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def seed_state(self, **values):
        ct.atomic_json(ct.state_path(self.root, SID), values)

    def set_config(self, **values):
        ct.atomic_json(self.root / "config.json", values)


class StopTest(Base):
    def test_stop_writes_pending_for_rename(self):
        self.turns(("修复登录表单布局", "已处理"))
        result = ct.handle_stop(self.event(), self.root, self.generator())
        self.assertEqual(result["status"], "pending")
        state = self.state()
        self.assertEqual(state["pending_title"], TITLE)
        self.assertEqual(state["last_turn_id"], "u0")
        self.assertEqual(state["policy_version"], core.POLICY_VERSION)
        self.assertTrue(state["last_fingerprint"])
        self.assertEqual(len(self.calls), 1)

    def test_stop_keep_clears_pending(self):
        self.turns(("修复登录表单布局", "已处理"))
        self.seed_state(pending_title="🧩 旧的｜待应用")
        result = ct.handle_stop(self.event(), self.root, self.generator(action="keep", title="", reason="已合适"))
        self.assertEqual(result["status"], "kept")
        self.assertNotIn("pending_title", self.state())

    def test_stop_rename_to_same_title_is_kept(self):
        self.turns(("修复登录表单布局", "已处理"), extra=[title_entry("custom", TITLE)])
        result = ct.handle_stop(self.event(), self.root, self.generator())
        self.assertEqual(result["status"], "kept")
        self.assertNotIn("pending_title", self.state())

    def test_stop_disabled_makes_no_model_call(self):
        self.turns(("修复登录表单布局", "已处理"))
        self.set_config(enabled=False)
        self.assertEqual(ct.handle_stop(self.event(), self.root, self.generator())["status"], "disabled")
        self.assertEqual(self.calls, [])

    def test_stop_worker_env_guard(self):
        self.turns(("修复登录表单布局", "已处理"))
        with mock.patch.dict(os.environ, {"CLAUDE_TITLE_WORKER": "1"}):
            self.assertEqual(ct.handle_stop(self.event(), self.root, self.generator())["status"], "disabled")
        self.assertEqual(self.calls, [])

    def test_stop_locked_makes_no_model_call(self):
        self.turns(("修复登录表单布局", "已处理"))
        self.seed_state(locked=True)
        self.assertEqual(ct.handle_stop(self.event(), self.root, self.generator())["status"], "locked")
        self.assertEqual(self.calls, [])

    def test_stop_unchanged_fingerprint_skips_model(self):
        self.turns(("修复登录表单布局", "已处理"))
        gen = self.generator(action="keep", title="", reason="r")
        ct.handle_stop(self.event(), self.root, gen)
        self.assertEqual(ct.handle_stop(self.event(), self.root, gen)["status"], "unchanged")
        self.assertEqual(len(self.calls), 1)

    def test_stop_greeting_only_skips_model(self):
        self.turns(("你好", "你好！有什么可以帮你"))
        self.assertEqual(ct.handle_stop(self.event(), self.root, self.generator())["status"], "kept")
        self.assertEqual(self.calls, [])

    def test_stop_confirmation_skips_model_and_counts(self):
        self.turns(("修复登录表单布局", "已处理"))
        gen = self.generator(action="keep", title="", reason="r")
        ct.handle_stop(self.event(), self.root, gen)
        self.turns(("修复登录表单布局", "已处理"), ("好的", "收到"))
        result = ct.handle_stop(self.event(), self.root, gen)
        self.assertEqual((result["status"], result["skipped"]), ("kept", 1))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()["confirmation_skips"], 1)

    def test_stop_confirmation_skip_preserves_pending(self):
        self.turns(("修复登录表单布局", "已处理"))
        ct.handle_stop(self.event(), self.root, self.generator(action="keep", title="", reason="r"))
        self.seed_state(**{**self.state(), "pending_title": "🧩 待应用｜标题"})
        self.turns(("修复登录表单布局", "已处理"), ("好的", "收到"))
        ct.handle_stop(self.event(), self.root, self.generator())
        self.assertEqual(self.state()["pending_title"], "🧩 待应用｜标题")

    def test_stop_invalid_candidate_writes_nothing(self):
        self.turns(("修复登录表单布局", "已处理"))
        with self.assertRaises(ValueError):
            ct.handle_stop(self.event(), self.root, self.generator(title="🧩 没有分隔符"))
        self.assertNotIn("pending_title", self.state())
        self.assertNotIn("last_fingerprint", self.state())

    def test_stop_stale_result_when_transcript_changes_during_generation(self):
        self.turns(("修复登录表单布局", "已处理"))

        def generate(context):
            self.turns(("修复登录表单布局", "已处理"), ("顺便加导出功能", "好"))
            return {"action": "rename", "title": TITLE, "reason": "r"}
        self.assertEqual(ct.handle_stop(self.event(), self.root, generate)["status"], "stale_result")
        self.assertNotIn("pending_title", self.state())

    def test_stop_stale_result_when_locked_during_generation(self):
        self.turns(("修复登录表单布局", "已处理"))

        def generate(context):
            self.seed_state(locked=True)
            return {"action": "rename", "title": TITLE, "reason": "r"}
        self.assertEqual(ct.handle_stop(self.event(), self.root, generate)["status"], "locked")
        self.assertNotIn("pending_title", self.state())

    def test_stop_busy_when_lock_held(self):
        self.turns(("修复登录表单布局", "已处理"))
        with ct.thread_lock(self.root, SID, 0) as held:
            self.assertTrue(held)
            result = ct.handle_stop(self.event(), self.root, self.generator(), lock_wait=0.2)
        self.assertEqual(result["status"], "busy")
        self.assertEqual(self.calls, [])

    def test_stop_detects_manual_rename_and_locks(self):
        self.turns(("修复登录表单布局", "已处理"), extra=[title_entry("custom", "我自己起的名字")])
        self.seed_state(last_applied_title=TITLE)
        result = ct.handle_stop(self.event(), self.root, self.generator())
        self.assertEqual(result["status"], "manual_title")
        self.assertTrue(self.state()["locked"])
        self.assertEqual(self.calls, [])

    def test_stop_no_transcript(self):
        self.assertEqual(ct.handle_stop(self.event(), self.root, self.generator())["status"], "no_transcript")

    def test_stop_empty_when_no_user_message(self):
        write_transcript(self.transcript, [assistant("独白", "a0")])
        self.assertEqual(ct.handle_stop(self.event(), self.root, self.generator())["status"], "empty")

    def test_stop_uses_hook_last_assistant_message(self):
        self.turns(("修复登录表单布局", "旧回复"))
        ct.handle_stop(self.event(last_assistant_message="钩子给出的最新回复"), self.root, self.generator())
        last = self.calls[0]["recent_turns"][-1]["messages"][-1]
        self.assertEqual(last, {"role": "assistant", "text": "钩子给出的最新回复"})

    def test_stop_rejects_unsafe_session_id(self):
        self.turns(("修复登录表单布局", "已处理"))
        with self.assertRaises(ValueError):
            ct.handle_stop(self.event(session_id="../../etc"), self.root, self.generator())

    def test_audit_log_has_status_but_no_conversation_text(self):
        self.turns(("私密需求内容XYZ", "私密回复内容XYZ"))
        ct.handle_stop(self.event(), self.root, self.generator())
        text = (self.root / "logs" / (SID + ".jsonl")).read_text(encoding="utf-8")
        self.assertIn('"status": "pending"', text)
        self.assertNotIn("XYZ", text)


class PromptTest(Base):
    def prompt(self, **over):
        return ct.handle_prompt({"session_id": SID, **over}, self.root)

    def test_prompt_applies_pending_once(self):
        self.seed_state(pending_title=TITLE)
        output, result = self.prompt(session_title="旧标题")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(output, {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                         "sessionTitle": TITLE}})
        state = self.state()
        self.assertEqual(state["last_applied_title"], TITLE)
        self.assertNotIn("pending_title", state)
        self.assertEqual(self.prompt(session_title=TITLE), (None, {"status": "idle"}))

    def test_prompt_first_turn_without_session_title_still_applies(self):
        self.seed_state(pending_title=TITLE)
        output, result = self.prompt()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(output["hookSpecificOutput"]["sessionTitle"], TITLE)

    def test_prompt_manual_rename_locks_and_outputs_nothing(self):
        self.seed_state(pending_title="🧩 新的｜待应用", last_applied_title=TITLE)
        output, result = self.prompt(session_title="我自己起的名字")
        self.assertIsNone(output)
        self.assertEqual(result["status"], "manual_title")
        self.assertTrue(self.state()["locked"])

    def test_prompt_expected_title_does_not_lock(self):
        self.seed_state(pending_title="🧩 新的｜待应用", last_applied_title=TITLE)
        output, result = self.prompt(session_title=TITLE)
        self.assertEqual(result["status"], "applied")
        self.assertNotIn("locked", self.state())

    def test_prompt_paused_outputs_nothing(self):
        self.seed_state(pending_title=TITLE)
        self.set_config(enabled=False)
        self.assertEqual(self.prompt(), (None, {"status": "disabled"}))

    def test_prompt_locked_outputs_nothing(self):
        self.seed_state(pending_title=TITLE, locked=True)
        self.assertEqual(self.prompt(), (None, {"status": "locked"}))

    def test_prompt_same_title_clears_pending_without_output(self):
        self.seed_state(pending_title=TITLE)
        output, result = self.prompt(session_title=TITLE)
        self.assertEqual((output, result["status"]), (None, "cleared"))
        self.assertEqual(self.state()["last_applied_title"], TITLE)
        self.assertNotIn("pending_title", self.state())

    def test_prompt_invalid_pending_is_dropped(self):
        self.seed_state(pending_title="没有分隔符的标题")
        output, result = self.prompt()
        self.assertEqual((output, result["status"]), (None, "cleared"))
        self.assertNotIn("pending_title", self.state())

    def test_prompt_busy_when_lock_held(self):
        self.seed_state(pending_title=TITLE)
        with ct.thread_lock(self.root, SID, 0) as held:
            self.assertTrue(held)
            output, result = ct.handle_prompt({"session_id": SID}, self.root, lock_wait=0.1)
        self.assertEqual((output, result["status"]), (None, "busy"))

    def test_prompt_output_is_ascii_json(self):
        text = ct.emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": TITLE}})
        self.assertTrue(text.isascii())
        self.assertEqual(json.loads(text)["hookSpecificOutput"]["sessionTitle"], TITLE)


class MainTest(Base):
    def run_main(self, args, stdin_text=""):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_TITLE_DATA": str(self.root)}):
            code = ct.main(args, stdin=io.StringIO(stdin_text), stdout=out)
        return code, out.getvalue()

    def test_main_hooks_never_fail(self):
        bad_inputs = ["", "not json", "[]", '{"session_id": 5}', '{"session_id": "../../etc"}',
                      json.dumps({"session_id": SID, "transcript_path": "/nope/never.jsonl"})]
        for command in ("stop", "prompt"):
            for raw in bad_inputs:
                with self.subTest(command=command, raw=raw):
                    self.assertEqual(self.run_main([command], raw), (0, ""))

    def test_main_prompt_prints_ascii_json_for_pending(self):
        self.seed_state(pending_title=TITLE)
        code, out = self.run_main(["prompt"], json.dumps({"session_id": SID}))
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii())
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["sessionTitle"], TITLE)

    def test_pause_resume_lock_unlock_status_roundtrip(self):
        self.assertEqual(self.run_main(["pause"])[0], 0)
        self.assertFalse(json.loads((self.root / "config.json").read_text(encoding="utf-8"))["enabled"])
        self.assertFalse(json.loads(self.run_main(["status"])[1])["config"]["enabled"])
        self.run_main(["resume"])
        self.assertTrue(json.loads(self.run_main(["status"])[1])["config"]["enabled"])
        self.seed_state(last_applied_title=TITLE, pending_title="x", last_fingerprint="f")
        self.run_main(["lock", SID])
        self.assertTrue(self.state()["locked"])
        self.run_main(["unlock", SID])
        state = self.state()
        self.assertFalse(state["locked"])
        for key in ("last_applied_title", "pending_title", "last_fingerprint"):
            self.assertNotIn(key, state)
        self.assertEqual(json.loads(self.run_main(["status"])[1])["tracked_sessions"], 1)

    def test_lock_defaults_to_env_session_id(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": SID}):
            self.assertEqual(self.run_main(["lock"])[0], 0)
        self.assertTrue(self.state()["locked"])

    def test_invalid_config_is_reported_by_status(self):
        self.set_config(recent_turns=99)
        code, out = self.run_main(["status"])
        self.assertEqual(code, 1)
        self.assertIn("recent_turns", out)


if __name__ == "__main__":
    unittest.main()
