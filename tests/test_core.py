import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import title_core as core
from tests.helpers import (assistant, config, title_entry, tool_result_user, user,
                           write_transcript)


class CleanTextTest(unittest.TestCase):
    def test_clean_text_strips_reminders(self):
        self.assertEqual(core.clean_text("<system-reminder>\nx\n</system-reminder>\n你好 "), "你好")

    def test_clean_text_strips_command_wrappers(self):
        self.assertEqual(core.clean_text("<command-name>/x</command-name>真实问题"), "真实问题")
        self.assertEqual(
            core.clean_text("<local-command-stdout>out</local-command-stdout>真实问题"), "真实问题")


class ReadTranscriptTest(unittest.TestCase):
    def test_read_transcript_skips_bad_lines_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text('{"type":"user"}\nnot json\n[1,2]\n{"type":"assistant"}\n', encoding="utf-8")
            self.assertEqual([e["type"] for e in core.read_transcript(str(path))], ["user", "assistant"])
            self.assertEqual(core.read_transcript(str(Path(tmp) / "none.jsonl")), [])
            self.assertEqual(core.read_transcript(""), [])

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows 有 260 字符路径限制")
    def test_read_transcript_long_path(self):
        bs = chr(92)
        base = tempfile.mkdtemp()
        prefix = bs * 2 + "?" + bs
        self.addCleanup(shutil.rmtree, prefix + os.path.abspath(base), True)
        deep = base + bs + (bs.join(["d" * 50] * 6))
        os.makedirs(prefix + os.path.abspath(deep))
        target = deep + bs + "t.jsonl"
        self.assertGreater(len(os.path.abspath(target)), 260)
        with open(prefix + os.path.abspath(target), "w", encoding="utf-8") as stream:
            stream.write('{"type":"user"}\n')
        self.assertEqual(core.read_transcript(target), [{"type": "user"}])


class TitleAndHintTest(unittest.TestCase):
    def test_current_title_prefers_custom_over_ai_and_last_wins(self):
        self.assertEqual(core.current_title([]), "")
        self.assertEqual(core.current_title([title_entry("ai", "AI 标题")]), "AI 标题")
        entries = [title_entry("custom", "旧"), title_entry("ai", "AI 标题"), title_entry("custom", "新")]
        self.assertEqual(core.current_title(entries), "新")
        self.assertEqual(core.current_title([title_entry("custom", "手动"), title_entry("ai", "后来的 AI")]),
                         "手动")

    def test_project_hint_rules(self):
        self.assertEqual(core.project_hint("C:/Users/alice/Maple"), "Maple")
        self.assertEqual(core.project_hint("C:/Users/alice/邮箱 项目"), "邮箱 项目")
        self.assertEqual(core.project_hint("C:/Users/alice"), "")
        self.assertEqual(core.project_hint("C:/Users/alice/Desktop"), "")
        self.assertEqual(core.project_hint("C:/Users/alice/bad(name)"), "")
        self.assertEqual(core.project_hint(None), "")
        self.assertEqual(core.project_hint(""), "")


class ParseTurnsTest(unittest.TestCase):
    def test_tool_result_and_sidechain_ignored(self):
        entries = [
            user("修复登录表单布局", "u1"),
            assistant("先看一下", "a1", blocks=[{"type": "text", "text": "先看一下"},
                                              {"type": "tool_use", "name": "Read", "input": {}}]),
            tool_result_user("u1r"),
            assistant("已经修复", "a2"),
            assistant("侧链回答", "sa", isSidechain=True),
            user("子代理内部提示", "s1", isSidechain=True),
            user("元消息", "m1", isMeta=True),
            user("<system-reminder>只有提醒</system-reminder>", "r1"),
            user("继续补测试", "u2"),
            assistant("补好了", "a3"),
        ]
        self.assertEqual(core.parse_turns(entries), [
            {"id": "u1", "user": "修复登录表单布局", "assistant": "已经修复"},
            {"id": "u2", "user": "继续补测试", "assistant": "补好了"},
        ])

    def test_user_text_blocks_count_as_user_message(self):
        entry = user("", "u1")
        entry["message"]["content"] = [{"type": "text", "text": "块形式的请求"}]
        self.assertEqual(core.parse_turns([entry]), [{"id": "u1", "user": "块形式的请求", "assistant": ""}])

    def test_assistant_before_any_user_is_ignored(self):
        self.assertEqual(core.parse_turns([assistant("无人提问", "a0")]), [])


class SnapshotTest(unittest.TestCase):
    def entries(self, n=1, assistant_text="回答"):
        out = []
        for i in range(n):
            out += [user(f"请求{i}", f"u{i}"), assistant(f"{assistant_text}{i}", f"a{i}")]
        return out

    def test_snapshot_last_assistant_message_overrides(self):
        snap = core.build_snapshot(self.entries(1, "旧"), config(), last_assistant_message="新回答")
        messages = snap["context"]["recent_turns"][-1]["messages"]
        self.assertEqual(messages, [{"role": "user", "text": "请求0"}, {"role": "assistant", "text": "新回答"}])

    def test_snapshot_limits_recent_turns_and_truncates(self):
        entries = self.entries(8)
        entries[0] = user("目" * 3000, "u0")
        snap = core.build_snapshot(entries, config(recent_turns=3), cwd="C:/Users/a/Maple")
        ctx = snap["context"]
        self.assertEqual([t["id"] for t in ctx["recent_turns"]], ["u5", "u6", "u7"])
        self.assertEqual(len(ctx["original_goal"]), 800)
        self.assertEqual(ctx["project_hint"], "Maple")
        self.assertEqual(snap["turn_ids"], [f"u{i}" for i in range(8)])
        long_entries = [user("长" * 5000, "x1"), assistant("答" * 5000, "x2")]
        texts = core.build_snapshot(long_entries, config())["context"]["recent_turns"][0]["messages"]
        self.assertLessEqual(len(texts[0]["text"]), 1200)
        self.assertLessEqual(len(texts[1]["text"]), 500)

    def test_snapshot_fingerprint_changes_with_content_and_is_stable_otherwise(self):
        a = core.build_snapshot(self.entries(2), config())
        b = core.build_snapshot(self.entries(2), config())
        c = core.build_snapshot(self.entries(2, "不同"), config())
        d = core.build_snapshot(self.entries(3), config())
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertNotEqual(a["fingerprint"], c["fingerprint"])
        self.assertNotEqual(a["fingerprint"], d["fingerprint"])

    def test_snapshot_without_user_message_has_no_messages(self):
        snap = core.build_snapshot([assistant("独白", "a0"), title_entry("ai", "T")], config())
        self.assertFalse(snap["has_messages"])
        self.assertEqual(snap["title"], "T")

    def test_snapshot_exposes_current_title_in_context(self):
        snap = core.build_snapshot(self.entries(1) + [title_entry("custom", "🧩 登录｜修复")], config())
        self.assertEqual(snap["title"], "🧩 登录｜修复")
        self.assertEqual(snap["context"]["current_title"], "🧩 登录｜修复")


class ValidateCandidateTest(unittest.TestCase):
    def good(self, title="🧩 邮箱验证码｜过期修复", action="rename"):
        return {"action": action, "title": title, "reason": "依据"}

    def test_validate_accepts_good_titles(self):
        self.assertEqual(core.validate_candidate(self.good(), "")["title"], "🧩 邮箱验证码｜过期修复")
        self.assertEqual(core.validate_candidate(self.good("⚙️ 环境｜配置"), "")["title"], "⚙️ 环境｜配置")
        exact = "🧩 " + "长" * 44 + "｜x"
        self.assertEqual(len(exact), 48)
        self.assertEqual(core.validate_candidate(self.good(exact), "")["title"], exact)

    def test_validate_rejects_bad_titles(self):
        bad = {
            "缺少分隔符": "🧩 邮箱验证码过期修复",
            "半角竖线": "🧩 邮箱验证码|过期修复",
            "两个分隔符": "🧩 邮箱｜验证码｜修复",
            "对象为空": "🧩 ｜目标",
            "目标为空": "🧩 对象｜",
            "分隔符旁有空格": "🧩 对象 ｜目标",
            "缺少 emoji": "邮箱验证码｜过期修复",
            "不允许的 emoji": "📌 对象｜目标",
            "正文含 emoji": "🧩 🎨 邮箱｜修复",
            "超过 48 字符": "🧩 " + "长" * 45 + "｜x",
            "含换行": "🧩 对象｜目\n标",
            "含链接": "🧩 对象｜见 https://a.b",
            "含 Windows 路径": "🧩 对象｜C:\\x",
            "含邮箱符号": "🧩 对象｜a@b.c",
            "首尾空白": " 🧩 对象｜目标",
            "太短": "🧩 ",
        }
        for name, title in bad.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    core.validate_candidate(self.good(title), "")

    def test_validate_keep_restores_current_title(self):
        out = core.validate_candidate({"action": "keep", "title": "随便", "reason": "r"}, "🧩 现有｜标题")
        self.assertEqual(out["title"], "🧩 现有｜标题")

    def test_validate_rejects_extra_or_missing_keys_and_bad_types(self):
        for value in ({"action": "keep", "title": "x"},
                      {"action": "keep", "title": "x", "reason": "r", "extra": 1},
                      {"action": "delete", "title": "x", "reason": "r"},
                      {"action": "keep", "title": 1, "reason": "r"}, "字符串", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    core.validate_candidate(value, "")

    def test_validate_truncates_reason(self):
        out = core.validate_candidate({**self.good(), "reason": "因" * 500}, "")
        self.assertEqual(len(out["reason"]), 300)


class TrivialTest(unittest.TestCase):
    @staticmethod
    def ctx(goal, *user_texts):
        return {"original_goal": goal, "recent_turns": [
            {"id": "t", "messages": [{"role": "user", "text": t}, {"role": "assistant", "text": "你好！有什么可以帮你"}]}
            for t in user_texts]}

    def test_is_trivial_greetings_only(self):
        self.assertTrue(core.is_trivial(self.ctx("你好", "你好", "Hi!", "好的。")))
        self.assertTrue(core.is_trivial(self.ctx("", )))

    def test_is_trivial_false_with_real_request(self):
        self.assertFalse(core.is_trivial(self.ctx("你好", "你好", "帮我修复登录页的布局")))
        self.assertFalse(core.is_trivial(self.ctx("修复登录页", "好的")))


class ConfirmationTest(unittest.TestCase):
    def base(self):
        entries = [user("修复登录表单", "u0"), assistant("好的，已处理", "a0"),
                   user("再调整间距", "u1"), assistant("已调整", "a1")]
        snap = core.build_snapshot(entries, config())
        state = {"policy_version": core.POLICY_VERSION, "last_fingerprint": snap["fingerprint"],
                 "last_turn_id": "u1", "confirmation_skips": 0}
        return entries, state

    def extend(self, entries, *texts):
        out = list(entries)
        for i, text in enumerate(texts):
            out += [user(text, f"c{i}"), assistant("收到", f"ca{i}")]
        return out

    def test_confirmation_skip_counts_pending_confirmations(self):
        entries, state = self.base()
        self.assertEqual(core.skippable_confirmations(self.extend(entries, "好的。"), state, config()), 1)
        self.assertEqual(core.skippable_confirmations(self.extend(entries, "OK!", "继续"), state, config()), 2)

    def test_confirmation_not_skipped_when_baseline_changed(self):
        entries, state = self.base()
        entries[1] = assistant("完全不同的回答", "a0")
        self.assertEqual(core.skippable_confirmations(self.extend(entries, "好的"), state, config()), 0)

    def test_confirmation_not_skipped_after_two_skips(self):
        entries, state = self.base()
        self.assertEqual(core.skippable_confirmations(
            self.extend(entries, "好的", "继续"), {**state, "confirmation_skips": 1}, config()), 0)
        self.assertEqual(core.skippable_confirmations(
            self.extend(entries, "好的"), {**state, "confirmation_skips": 2}, config()), 0)

    def test_confirmation_not_skipped_for_real_request(self):
        entries, state = self.base()
        self.assertEqual(core.skippable_confirmations(self.extend(entries, "顺便加个导出功能"), state, config()), 0)
        self.assertEqual(core.skippable_confirmations(self.extend(entries, "好的", "推送一下"), state, config()), 0)

    def test_confirmation_not_skipped_without_valid_baseline(self):
        entries, state = self.base()
        extended = self.extend(entries, "好的")
        for broken in ({**state, "policy_version": 0}, {**state, "last_fingerprint": ""},
                       {**state, "last_turn_id": "没有这一轮"}, {}):
            with self.subTest(broken=broken):
                self.assertEqual(core.skippable_confirmations(extended, broken, config()), 0)
        self.assertEqual(core.skippable_confirmations(entries, state, config()), 0)  # 没有新增轮次


if __name__ == "__main__":
    unittest.main()
