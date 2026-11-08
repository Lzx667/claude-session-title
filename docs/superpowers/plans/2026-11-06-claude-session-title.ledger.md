# SDD ledger — plan: docs/superpowers/plans/2026-11-06-claude-session-title.md

Setup rulings
- Ruling: 不建 worktree、不提交 — 项目是非 Git 的临时文件夹，且用户全局规则要求提交需明确授权 — 代价：没有 git 历史作回滚点，用本账本和测试作记录。
- Ruling: 不使用 `sdd-workspace` / `task-start` / `task-done` 脚本 — 它们依赖 git；以每任务末尾直接运行该任务测试命令并把结果写入本账本代替 — 代价：无自动区间记录。
- Ruling: 最终评审由我自审 — 会话规则禁止未经用户要求启动子代理 — 代价：作者自审弱于独立评审，需在最终报告里说明。

Pre-flight: Task2→3（parse_turns/build_snapshot 供 validate/skippable 使用）、Task2/3/4→5（handle_stop 消费全部纯函数与 generate_candidate）、Task5↔6（install/uninstall 子命令由 claude_title.main 调用 install_hooks）。签名核对：计划各 Interfaces 块一致；无冲突。

Task 1: complete (tests: python -B -X utf8 -m unittest tests.test_basics → 2/2 pass)
Task 2: complete (tests: tests.test_core → 14/14 pass，含 Windows 长路径用例)
Task 2: Ruling: 新增公开函数 snapshot_from_turns(turns, title, config, *, cwd=None) — Task 3 的 skippable_confirmations 需要对截断轮次重算指纹；build_snapshot 内部复用 — 代价：多一个公开函数，无行为差异。
Task 3: complete (tests: tests.test_core + tests.test_basics → 28/28 pass)
Task 3: note: 首次运行有 2 个失败，原因是测试里的字符数算错（"🧩 " 占 2、"｜x" 占 2），已修正测试边界为 48 通过/49 拒绝，实现未改动。
Task 4: Ruling: generate_candidate/ build_argv 增加 claude 参数（str 或命令前缀 list）作测试接缝 — Windows 无法直接执行 .py 假 claude；计划里的 config["claude_bin"] 仍用于 find_claude — 代价：多一个可选参数。
Task 4: complete (tests: tests.test_model → 15/15 pass)
Task 5: complete (tests: tests.test_hooks → 35/35 pass；全量 discover → 78/78 pass)
Task 5: Ruling: install / uninstall / doctor 子命令移到 Task 6 实现 — doctor 需要安装器的“是否已安装”检测，提前实现会悬空 — 代价：无。
Task 5: Ruling: 确认类/问候类跳过时不清除尚未应用的 pending_title，只有模型给出的 keep 才清除 — 跳过没有新证据，不应作废已有候选；已加测试 test_stop_confirmation_skip_preserves_pending — 代价：极端情况下旧候选保留到下次模型判断。
Task 6: complete (tests: tests.test_install → 17/17 pass；全量 discover → 95/95 pass)
Task 7 (partial): 真机 `doctor --live` 通过 — find_claude 把 npm 的 .cmd 解析为原生 claude.exe（2.1.318）；--system-prompt-file/--json-schema/--tools ""/--settings/--no-session-persistence 等参数均被接受；structured_output 解析得到合规标题「🧩 邮箱注册｜验证码过期修复」。无需调整 Task 4 的参数。
Task 7: found-by-e2e: 安装器在 >260 字符的设置文件路径上失败（备份/写入 FileNotFoundError）— 修复：title_core.long_path 公开并用于 install_hooks 的读/写/备份；先加测试 test_install_and_uninstall_work_on_long_settings_path RED（WinError 206）→ GREEN，全量 96/96。
Task 7: complete — 真机端到端通过：新开 Desktop 会话（spike2，装真实 hook），3 条消息后 Desktop 标题（titleSource=tool）= 「🧩 个人博客｜深色模式切换」；第 1 轮 Stop 生成 pending（Haiku 真实调用，审计无对话原文）→ 第 2 条消息写 CLI 标题 → 第 3 条消息同步 Desktop；第 2、3 轮模型判定 kept。全量 discover → 96/96 pass。
Final review: self-review (no subagent tool) — 通读 claude_title.py / install_hooks.py / title_core.py 的并发、锁、状态流与错误路径；Review Focus 5 项均有测试覆盖。
Final: fixed README 漏写“手动改名可能被 mcp hook 回滚/弹确认”风险 — 文档更正（非代码），无测试。
Final: minor (deferred): 首轮消息 mcp_tool 因 session_title 为空报一条非阻塞错误（hook 协议限制，无法条件跳过）。
Final: minor (deferred): 终端里的 Claude Code 没有 ccd_session_mgmt，装在用户级设置会让每条消息多一条非阻塞错误。
Final: minor (deferred): 用户在本工具 hook 之后还有同事件 hook 时，重复 install 会把本工具条目重排到最后，视为 changed 并产生备份。
Final: minor (deferred): Stop 持锁（模型调用约 7–13 秒）期间，lock/unlock 命令最多等 5 秒，可能需重试；UserPromptSubmit 在此期间对该会话“busy”，不应用标题。
Final: minor (deferred): 模型常给出超过 40 汉字的 reason（提示词要求 ≤40），仅影响审计日志，已截断到 300 字符。
Final: minor (deferred): 每条消息多两次进程启动（同步命令 hook），耗时未测量。
Verify (manual rename, 2026-11-06): 用户在 Desktop 把 e2e 会话手动改名为「个人博客测试」（titleSource=user）后发第 4 条消息 — Desktop 标题保持；CLI transcript 第 89 行出现对应 custom-title（Desktop 把手动改名同步给 CLI）；mcp_tool 返回 `already named`（相同标题不改名）；插件状态 locked=True（检测到手动改名），审计 manual_title；本条无 hook 报错。未验证：第一次写入前就手动改名的会话（见 README）。
Install (2026-11-06): 用户批准后 `install` 写入 ~/.claude/settings.json，自动备份 settings.json.bak-20261106-145054；原有 Stop×1、SessionEnd×1 保留；doctor 显示 stop/prompt/mcp 均已安装。
Verify (real session, user-level): 本会话（真实 Desktop 会话）两条消息后 Desktop 标题 = 「🧩 Claude 会话标题｜插件复刻」（titleSource=tool）；审计 pending→applied→kept；mcp_tool 第 2 条返回 Renamed … (was "Claude Desktop 插件复刻")；Desktop 日志有对应 Updated session。
