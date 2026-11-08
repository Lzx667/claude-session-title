# claude-session-title Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. 项目不是 Git 仓库，且用户未授权提交，因此没有 commit 步骤。

**Goal:** 在 Claude Desktop / Claude Code 中，每轮对话后用独立模型维护「类别 emoji + 对象｜目标」会话标题，且不向对话追加消息。

**Architecture:** `Stop`（异步命令）调 `claude -p` 生成候选存为 pending；下一条消息的 `UserPromptSubmit`（命令）用 `sessionTitle` 写 CLI 标题；同事件的 `mcp_tool` hook 调 `set_session_title(self, "${session_title}")` 转给 Desktop。纯逻辑与副作用分离，模型调用可注入。

**Tech Stack:** Python 3.10+ 标准库；`unittest`；Claude Code 2.1.317 的 hook 协议。

**Spec:** `docs/设计.md`

## Global Constraints

- 只用 Python 标准库；支持 Python 3.10+；测试用 `unittest`，运行：`python -B -X utf8 -m unittest discover -s tests -t . -v`（在项目根目录）。
- 测试中禁止调用真实 `claude` 模型；模型调用一律注入假实现或假可执行文件。
- 标题格式：`<类别emoji> <对象>｜<目标>`；允许 emoji 为 `🎬 🧩 🔎 📝 📅 🎨 ⚙️ 💬`；全角「｜」恰好一个；整体 ≤48 字符；无控制字符、`http(s)://`、`@`、反引号、Windows 绝对路径。
- Hook 命令永远退出码 0，不向宿主输出非 JSON 内容；`Stop` 不输出，`UserPromptSubmit` 只在需要改标题时输出 JSON。
- 不写入 Desktop 私有文件（`local_*.json`）或 transcript；不保存对话原文，日志只记状态、标题、简短理由。
- 数据目录：`$CLAUDE_SESSION_TITLE_DATA`，否则 `<CLAUDE_CONFIG_DIR 或 ~/.claude>/claude-session-title`。
- 文档与注释用中文。保留原版 MIT 署名（`LICENSE` 已就位）。

## Review Focus

1. 工具密集的轮次：内容为 `tool_result` 列表的 `user` 条目、`isSidechain` 条目不得被当作用户请求或开启新轮次。→ Task 2
2. Hook 输入异常（空 stdin、非 JSON、缺 `session_id`、transcript 不可读）必须静默退出 0、不崩。→ Task 5
3. 中日韩与 emoji 标题经 stdout 输出后，在非 UTF-8 控制台代码页下仍能还原：stdout 用 ASCII 转义 JSON。→ Task 5
4. 模型输出被代码围栏或多余文字包裹、或缺少 `structured_output`：仍能解析，否则明确报错而不是写坏标题。→ Task 4
5. Windows 下 transcript 路径超过 260 字符（实测会触发，临时 glob 即因此失配）。→ Task 2

---

### Task 1: 骨架、文件锁与命名提示词

**Files:**
- Create: `tests/__init__.py`（把 `scripts/` 加入 `sys.path`）、`scripts/file_lock.py`（取自原版 `scripts/file_lock.py`，异常文案改为「会话正在命名」）、`prompts/naming.md`、`tests/test_basics.py`

**Interfaces:**
- Produces: `file_lock.acquire(stream)`（占用时抛 `BlockingIOError`）、`file_lock.release(stream)`；`prompts/naming.md` 文本。

- [ ] **Step 1: 写失败测试** `tests/test_basics.py`：`test_file_lock_is_exclusive`（同一文件两个句柄，第二次 `acquire` 抛 `BlockingIOError`，释放后可获取）；`test_prompt_has_no_codex_residue`（`prompts/naming.md` 存在且不含 `Codex`、`codex`、`conflicting_titles`，且含 `｜`、`keep`、`rename`）。
- [ ] **Step 2: 运行确认失败**：`python -B -X utf8 -m unittest tests.test_basics -v`，期望 FAIL（文件缺失）。
- [ ] **Step 3: 实现**：复制原版文件锁；`naming.md` 由原版 `prompts/naming.md` 改写：标题改为「Claude 会话标题编辑器」，删除全部 `conflicting_titles` 相关句子，其余规则（格式、语言、类别、稳定性、项目名省略、输出 action/title/reason）原样保留。
- [ ] **Step 4: 运行确认通过**，同上命令，期望 PASS。

### Task 2: transcript 解析与摘录（`scripts/title_core.py` 上半）

**Files:**
- Create: `scripts/title_core.py`、`tests/helpers.py`（`write_transcript(path, entries)`、`user(text, uuid, **kw)`、`assistant(text, uuid)`、`tool_result_user(uuid)`、`title_entry(kind, value)` 构造器，字段形状与真实 transcript 一致：`user.message.content` 为字符串，`assistant.message.content` 为块列表，标题条目为 `{"type":"custom-title","customTitle":...}` / `{"type":"ai-title","aiTitle":...}`）、`tests/test_core.py`

**Interfaces:**
- Produces:
  - `EMOJI: tuple[str, ...]`；`POLICY_VERSION: int = 1`
  - `clean_text(text: str) -> str`：删除 `<system-reminder>…</system-reminder>`、`<command-…>`、`<local-command-…>` 块，去首尾空白。
  - `read_transcript(path: str) -> list[dict]`：逐行解析，坏行跳过，文件不存在返回 `[]`；Windows 下路径超长时自动用 `\\?\` 前缀（用 `chr(92)` 拼接，避免转义问题）。
  - `current_title(entries: list[dict]) -> str`：最后一个 `custom-title`，否则最后一个 `ai-title`，否则 `""`。
  - `project_hint(cwd: str | None) -> str`：目录末级名；主目录、`desktop/documents/downloads/tmp/project/projects` 及不匹配 `[\w .-]{1,64}` 的返回 `""`。
  - `parse_turns(entries: list[dict]) -> list[dict]`：每轮 `{"id": str, "user": str, "assistant": str}`；真实用户消息 = `type=="user"`、非 `isMeta`、非 `isSidechain`、`content` 为字符串（或仅含 text 块）且清洗后非空；`assistant` 取该轮最后一个非空 text 块。
  - `build_snapshot(entries, config: dict, *, cwd=None, last_assistant_message=None) -> dict`：返回 `{"title","has_messages","fingerprint","turn_ids","context"}`；`context = {"current_title","project_hint","original_goal","recent_turns":[{"id","messages":[{"role","text"}]}]}`；传入 `last_assistant_message` 时覆盖最后一轮的 `assistant`；取最近 `config["recent_turns"]` 轮，单条上限用户 1200 / 助手 500 字符，预算 `max(200, (max_context_chars-2000)//消息数)`；`original_goal` 为首轮用户文本前 800 字符；`fingerprint` 为 `{POLICY_VERSION, project_hint, 最近轮摘录}` 的 sha256 十六进制。

- [ ] **Step 1: 写失败测试** `tests/test_core.py`：
  - `test_clean_text_strips_reminders`：含 `<system-reminder>x</system-reminder>你好` → `"你好"`。
  - `test_read_transcript_skips_bad_lines_and_missing_file`：坏行跳过；不存在路径 → `[]`。
  - `test_read_transcript_long_path`（`sys.platform != "win32"` 时 `skipTest`）：创建总长 >260 的目录嵌套，写入并读回成功。
  - `test_current_title_prefers_custom_over_ai_and_last_wins`。
  - `test_tool_result_and_sidechain_ignored`：夹杂 `tool_result` 用户条目与 `isSidechain` 用户条目，`parse_turns` 只返回真实用户轮，`assistant` 为该轮最后一个 text 块。
  - `test_snapshot_last_assistant_message_overrides`、`test_snapshot_limits_recent_turns_and_truncates`、`test_snapshot_fingerprint_changes_with_content_and_is_stable_otherwise`、`test_snapshot_without_user_message_has_no_messages`、`test_project_hint_rules`。
- [ ] **Step 2: 运行** `python -B -X utf8 -m unittest tests.test_core -v`，期望 FAIL（模块不存在）。
- [ ] **Step 3: 实现**上述接口。
- [ ] **Step 4: 运行**同上，期望全部 PASS。

### Task 3: 校验、问候与确认过滤（`title_core.py` 下半）

**Files:**
- Modify: `scripts/title_core.py`；Test: `tests/test_core.py`（追加类）

**Interfaces:**
- Consumes: Task 2 的 `EMOJI`、`POLICY_VERSION`、`parse_turns`、`build_snapshot`。
- Produces:
  - `validate_candidate(candidate: dict, current_title: str) -> dict`：键集必须恰为 `{"action","title","reason"}`；`keep` 时把 `title` 置为 `current_title`；`rename` 时按 Global Constraints 校验，失败抛 `ValueError`；返回 `reason` 截断到 300 字符的新字典。
  - `is_trivial(context: dict) -> bool`：原始目标与近期用户消息全部是问候或确认（`你好 您好 hi hello 嗨 谢谢 好的 好 ok 收到 继续 嗯`，去标点、忽略大小写）。
  - `skippable_confirmations(entries, state: dict, config: dict, *, cwd=None) -> int`：返回 0～2。仅当 `state` 有 `policy_version==POLICY_VERSION` 与 `last_fingerprint`、`last_turn_id` 能在轮次中找到、其后新增轮次数加 `state.get("confirmation_skips",0)` ≤2、截断到 `last_turn_id` 的快照指纹等于 `last_fingerprint`、且新增轮次的用户文本（去 `.!。！` 与空白后忽略大小写）都在 `{好, 好的, 可以, 收到, 谢谢, 继续, ok, okay, thanks, thank you, continue}` 中时返回新增轮次数。

- [ ] **Step 1: 写失败测试**：`test_validate_accepts_good_title`；拒绝用例（各一个）：无「｜」、半角 `|`、两个「｜」、对象或目标为空、缺 emoji、两个 emoji、长度 49、含 `\n`、含 `https://`、含 `C:\x`、含 `@`；`test_validate_keep_restores_current_title`、`test_validate_rejects_extra_or_missing_keys`；`test_is_trivial_greetings_only` 与 `test_is_trivial_false_with_real_request`；`test_confirmation_skip_counts_pending_confirmations`、`test_confirmation_not_skipped_when_baseline_changed`、`test_confirmation_not_skipped_after_two_skips`、`test_confirmation_not_skipped_for_real_request`。
- [ ] **Step 2: 运行**：`python -B -X utf8 -m unittest tests.test_core -v`，期望新增用例 FAIL。
- [ ] **Step 3: 实现**上述三个函数（校验规则逐条移植自原版 `validate_candidate`）。
- [ ] **Step 4: 运行**同上，期望 PASS。

### Task 4: 命名模型调用（`scripts/claude_model.py`）

**Files:**
- Create: `scripts/claude_model.py`、`tests/test_model.py`、`tests/fake_claude.py`（可执行的假 `claude`：按环境变量 `FAKE_CLAUDE_MODE` 输出 `structured`、`fenced`、`prose`、`bad`、`fail`、`slow`，并把收到的 argv、cwd、`CLAUDE_TITLE_WORKER` 写入 `FAKE_CLAUDE_LOG` 指向的文件）

**Interfaces:**
- Produces:
  - `class ModelError(RuntimeError)`
  - `SCHEMA: dict`（`action` 枚举 keep/rename，`title`、`reason` 字符串，三者必填，`additionalProperties: False`）
  - `find_claude(explicit: str | None = None) -> str`：`shutil.which`；Windows 下遇到 `.cmd/.bat` 时尝试同级 `node_modules/@anthropic-ai/claude-code/bin/claude.exe`，找不到抛 `ModelError`；未找到抛 `ModelError`。
  - `build_argv(claude: str, config: dict, prompt_file: Path) -> list[str]`：至少包含 `-p`、`--model <config.model>`、`--system-prompt-file`、`--json-schema <SCHEMA json>`、`--output-format json`、`--no-session-persistence`、`--tools ""`、`--disable-slash-commands`、`--settings {"disableAllHooks":true}`。
  - `parse_output(stdout: str) -> dict`：优先 `structured_output`；否则解析 `result` 文本，容忍 ```` ``` ```` 围栏和前后多余文字（取第一个平衡的 `{…}`）；失败抛 `ModelError`。
  - `generate_candidate(config: dict, context: dict, prompt_file: Path, *, runner=subprocess.run) -> dict`：在空临时目录运行，`input=json.dumps(context, ensure_ascii=False)`，环境加 `CLAUDE_TITLE_WORKER=1`，超时用 `config["model_timeout_seconds"]`；非零退出、超时、解析失败均抛 `ModelError`；Windows 加 `CREATE_NO_WINDOW`。

- [ ] **Step 1: 写失败测试** `tests/test_model.py`：`test_build_argv_has_isolation_flags`；`test_parse_structured_output`、`test_parse_result_with_code_fence`、`test_parse_result_with_surrounding_prose`、`test_parse_rejects_garbage`；用假 `claude`（`config["claude_bin"]` 指向 Python 包装）：`test_generate_candidate_success_sets_worker_env_and_temp_cwd`（断言日志中 `CLAUDE_TITLE_WORKER=="1"`、cwd 不是项目目录、argv 含 `--no-session-persistence`）、`test_generate_candidate_nonzero_exit_raises`、`test_generate_candidate_timeout_raises`（`model_timeout_seconds` 设小）。
- [ ] **Step 2: 运行** `python -B -X utf8 -m unittest tests.test_model -v`，期望 FAIL。
- [ ] **Step 3: 实现**；若 `--system-prompt-file`、`--tools ""` 等参数被本机 `claude` 拒绝，以 Task 7 的真机结果为准调整，并保持隔离三要素（禁 hook、无工具、不落盘会话）。
- [ ] **Step 4: 运行**同上，期望 PASS。

### Task 5: Hook 逻辑与管理命令（`scripts/claude_title.py`）

**Files:**
- Create: `scripts/claude_title.py`、`tests/test_hooks.py`

**Interfaces:**
- Consumes: Task 2/3 的 `read_transcript`、`build_snapshot`、`current_title`、`validate_candidate`、`is_trivial`、`skippable_confirmations`；Task 4 的 `generate_candidate`；Task 1 的 `file_lock`。
- Produces:
  - `DEFAULTS = {"enabled": True, "model": "haiku", "claude_bin": None, "recent_turns": 5, "max_context_chars": 14000, "model_timeout_seconds": 100}`；`load_config(root: Path) -> dict`（校验范围：recent_turns 3–5，max_context_chars 3000–20000，model_timeout_seconds 10–110，非法抛 `ValueError`）
  - `data_dir() -> Path`
  - `handle_stop(event: dict, root: Path, generator=None) -> dict`：返回 `{"status": ...}`，status ∈ `disabled busy no_transcript empty locked manual_title unchanged stale_result pending kept`；`generator(context)->dict` 缺省为调用 `generate_candidate`。流程：配置/守卫 → 会话锁（等待 `model_timeout_seconds*2+20` 秒）→ 读 transcript 与 `last_assistant_message` 建快照 → 锁定/手动改名检查 → 指纹未变跳过 → 问候或确认则免模型 keep → 否则调用 generator 并 `validate_candidate` → 写入前重读快照，指纹或锁定状态变化则 `stale_result`/`locked` → `rename` 且与当前标题不同则写 `pending_title`，否则清除 pending → 更新状态（`last_fingerprint`、`last_turn_id`、`policy_version`、`confirmation_skips`、`updated_at`）并写审计日志。
  - `handle_prompt(event: dict, root: Path) -> tuple[dict | None, dict]`：返回 `(hook 输出或 None, {"status": ...})`，status ∈ `disabled locked manual_title applied cleared idle busy`。规则：暂停/锁定不输出；已有 `last_applied_title` 且 `session_title` 非空且不同 → 锁定为 `manual_title`；有合法 pending 且不同于 `session_title` → 输出 `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","sessionTitle":<title>}}` 并记录 `last_applied_title`、清除 pending。
  - `emit(output: dict) -> str`：`json.dumps(output, ensure_ascii=True)`。
  - 状态文件 `<root>/threads/<session_id>.json`；锁文件 `<root>/locks/<session_id>.lock`；审计日志 `<root>/logs/<session_id>.jsonl`（>256KB 轮转）；`session_id` 必须匹配 `^[0-9a-fA-F-]{8,64}$`。
  - `main(argv=None) -> int`：子命令 `stop`、`prompt`（读 stdin，最多 1MB，任何异常记审计后返回 0）、`status`、`pause`、`resume`、`lock [id]`、`unlock [id]`（缺省取 `CLAUDE_CODE_SESSION_ID`）、`doctor [--live]`、`install`、`uninstall`（见 Task 6）。

- [ ] **Step 1: 写失败测试** `tests/test_hooks.py`（临时数据目录 + `tests/helpers.py` 构造 transcript）：
  - Stop：`test_stop_writes_pending_for_rename`、`test_stop_keep_clears_pending`、`test_stop_disabled_makes_no_model_call`、`test_stop_locked_makes_no_model_call`、`test_stop_unchanged_fingerprint_skips_model`、`test_stop_greeting_only_skips_model`、`test_stop_confirmation_skips_model_and_counts`、`test_stop_invalid_candidate_writes_nothing`（生成器返回缺 `｜` 的标题 → 无 pending）、`test_stop_stale_result_when_transcript_changes_during_generation`（生成器内追加用户轮）、`test_stop_busy_when_lock_held`、`test_stop_detects_manual_rename_and_locks`、`test_stop_no_transcript`。
  - Prompt：`test_prompt_applies_pending_once`、`test_prompt_first_turn_without_session_title_still_applies`、`test_prompt_manual_rename_locks_and_outputs_nothing`、`test_prompt_paused_outputs_nothing`、`test_prompt_same_title_clears_pending_without_output`、`test_prompt_output_is_ascii_json`（`emit` 结果全 ASCII，`json.loads` 后标题与原文相等）。
  - 入口：`test_main_hooks_never_fail`（空 stdin、非 JSON、缺 `session_id`、不存在的 transcript → 返回 0，stdout 无输出）；`test_pause_resume_lock_unlock_status_roundtrip`。
- [ ] **Step 2: 运行** `python -B -X utf8 -m unittest tests.test_hooks -v`，期望 FAIL。
- [ ] **Step 3: 实现**；`doctor` 检查 Python 版本、`find_claude` 与 `claude --version`、数据目录可写、设置文件中是否已安装本工具 hook；`--live` 才用合成上下文真实调用一次模型。
- [ ] **Step 4: 运行**同上，期望 PASS；再跑全量 `python -B -X utf8 -m unittest discover -s tests -t . -v`，期望全部 PASS。

### Task 6: 安装器（`scripts/install_hooks.py`）

**Files:**
- Create: `scripts/install_hooks.py`、`tests/test_install.py`；Modify: `scripts/claude_title.py`（`install`/`uninstall` 子命令，参数 `--settings-file`（默认 `<CLAUDE_CONFIG_DIR 或 ~/.claude>/settings.json`）、`--dry-run`）

**Interfaces:**
- Produces:
  - `hook_groups(script: Path, python_cmd: str | None = None) -> dict[str, list[dict]]`：`Stop` 一组含异步命令 hook（`async: true`、`timeout: 180`）；`UserPromptSubmit` 一组含同步命令 hook（`timeout: 10`）与 `mcp_tool` hook（`server: "ccd_session_mgmt"`、`tool: "set_session_title"`、`input: {"session_id":"self","title":"${session_title}"}`、`timeout: 20`）；命令形如 `python -X utf8 "<script posix 路径>" stop`，Windows 默认 `python`，其余 `python3`。
  - `install(settings_path: Path, script: Path, *, dry_run=False, python_cmd=None) -> dict`：返回 `{"changed": bool, "backup": str | None}`；先移除本工具旧条目再加入（幂等）；保留其它键与其它 hook；文件存在且有变更时先备份为 `<name>.bak-<YYYYMMDD-HHMMSS>`；原子写；设置文件不是合法 JSON 时抛 `ValueError` 且不改动。
  - `uninstall(settings_path: Path, *, dry_run=False) -> dict`：只移除本工具条目（命令含 `claude_title.py`，或 `mcp_tool` 的 server/tool/`input.title` 完全匹配），清掉因此变空的组与事件。

- [ ] **Step 1: 写失败测试**：`test_install_creates_file_with_three_hooks`；`test_install_is_idempotent`（连装两次内容相同）；`test_install_preserves_other_hooks_and_keys`；`test_install_backs_up_existing_file`；`test_install_dry_run_writes_nothing`；`test_install_invalid_json_untouched`；`test_command_quotes_path_with_spaces_and_cjk`；`test_python_cmd_default_by_platform`（猴补 `sys.platform`）；`test_uninstall_removes_only_ours`；`test_uninstall_cleans_empty_events`。
- [ ] **Step 2: 运行** `python -B -X utf8 -m unittest tests.test_install -v`，期望 FAIL。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行**全量测试，期望全部 PASS。

### Task 7: 文档与真机端到端验证

**Files:**
- Create: `README.md`（中文：作用、效果、前置条件、安装/卸载、控制命令、滞后与已知限制、数据与隐私、致谢与许可）

- [ ] **Step 1: 写 README**，限制章节逐条照抄 `docs/设计.md` 的「已知限制」。
- [ ] **Step 2: 干跑安装**：`python scripts/claude_title.py install --settings-file <临时文件> --dry-run`，期望输出将写入的 JSON，文件未创建。
- [ ] **Step 3: 把 hook 装到测试目录 `spike2/.claude/settings.json`（不动 `~/.claude`）**，在其中新开测试会话，发 3 条消息，期望：第 1 条后 `threads/<id>.json` 出现 `pending_title`；第 2 条后 transcript 出现对应 `custom-title`；第 3 条后 Desktop 标题（`local_*.json` 的 `title`）等于该标题。模型调用为真实调用。
- [ ] **Step 4: 记录真机结果**（`--system-prompt-file` 等参数是否被接受、首轮报错表现、耗时）并回填 Task 4/5 的调整。
- [ ] **Step 5: 请用户手动改名一个测试会话**，再发消息，期望标题保持用户所改且该会话被锁定，且无确认弹窗；通过后才允许安装到用户级设置。
