# claude-session-title

让 Claude Desktop（Code 标签页）和 Claude Code 的会话标题跟上你正在做的事。每轮对话结束后，在后台用独立的 Haiku 判断是否需要更新标题，**不往对话里加任何消息**。

复刻自 [oil-oil/oil-codex-title](https://github.com/oil-oil/oil-codex-title)（原项目面向 Codex），命名规则与提示词沿用原版思路，Hook 机制按 Claude Code 重新实现。

## 效果

统一采用 **「类别 emoji + 对象｜目标」**，先找对象，再看正在做什么：

| 原来的标题 | 更容易找回的标题 |
| --- | --- |
| 回应中文问候 | 🧩 邮箱验证码｜过期排查 |
| 继续修改 | 🎨 登录表单｜布局优化 |

类别固定：🎬 内容制作、🧩 工具开发、🔎 对比调研、🎨 页面设计、📝 方法整理、📅 日程安排、⚙️ 环境配置、💬 一般讨论。准确的标题保持不变；“好的”“继续”等纯确认不会触发改名；标题跟随你最近几轮的主要语言。

## 工作原理

1. **`Stop`（异步）**：每轮结束后，读最近几轮对话，调用 `claude -p --model haiku`（无工具、无 hook、不保存会话）生成候选标题，校验后存为待应用。
2. **下一条消息的 `UserPromptSubmit`**：把待应用标题写进 Claude Code 的会话标题。
3. **同一事件里的 `mcp_tool` hook**：调用 Desktop 自带的 `set_session_title`，把 CLI 标题同步到 Desktop 侧边栏。

**有滞后**：第 N 轮结束后算出的标题，CLI 在你发第 N+1 条消息时更新，Desktop 在第 N+2 条消息时显示。原因是 Claude Code 只在 `UserPromptSubmit` / `SessionStart` 接受 hook 返回的标题，`Stop` 事件不行；Desktop 又只采纳它自己的改名工具。

## 前置条件

- Python 3.10+（只用标准库）。
- PATH 上有已登录的 `claude`（Claude Code CLI）。命名模型借用这份登录，不需要另外的 API Key，用量计入你的账号。
- Windows 上若 `claude` 是 npm 的 `.cmd` 入口，会自动解析到包内的原生 `claude.exe`；找不到时在 `config.json` 里设置 `claude_bin`。

## 安装与卸载

```bash
python scripts/claude_title.py doctor              # 检查环境
python scripts/claude_title.py install --dry-run   # 先预览将写入的 hook
python scripts/claude_title.py install             # 写入 ~/.claude/settings.json（写前自动备份）
python scripts/claude_title.py uninstall           # 只移除本工具的 hook
```

`--settings-file <路径>` 可指定别的设置文件（例如某个项目的 `.claude/settings.json`）。安装后在**新的**会话里正常聊天即可，运行中的会话会热加载 hook 配置。`doctor --live` 会真实调用一次命名模型。hook 里写的是脚本的绝对路径，**移动或重命名本目录后需重新运行 `install`**。

## 日常控制

```bash
python scripts/claude_title.py pause      # 暂停自动命名（全局）
python scripts/claude_title.py resume
python scripts/claude_title.py lock       # 固定当前会话标题（缺省取 CLAUDE_CODE_SESSION_ID）
python scripts/claude_title.py unlock
python scripts/claude_title.py status
```

你也可以直接让 Claude 帮你执行上面的命令。在 Desktop 里手动改名后，本工具会发现标题与自己上次写入的不同，自动锁定该会话。**首次写入前**无法区分 Desktop 自动生成的标题和你手动设置的标题，需要永久保留的标题请先 `lock`。

## 已知限制（未验证项）

- 每个新会话的第一条消息，`mcp_tool` hook 因 `session_title` 为空会报一条非阻塞错误；界面是否显示未验证。
- **手动改名（已实测）**：在 Desktop 里手动改名后，Desktop 会把新标题同步给 CLI；`mcp_tool` hook 拿到的就是你的标题，`set_session_title` 对相同标题直接返回“already named”，不会回滚也不会改名；本工具发现标题与自己上次写入的不同，自动锁定该会话。
- **未验证**：在本工具**第一次写入之前**就手动改过名的会话。此时没有基线，pending 标题可能覆盖你的名字，且 `set_session_title` 对用户设置的标题可能弹出确认。需要保留的标题请先 `lock`。
- 终端里的 Claude Code 没有 `ccd_session_mgmt`，`mcp_tool` hook 每条消息会报非阻塞错误。装在用户级设置会同时影响终端会话。
- 依赖的 Claude Code 内部行为来自 2.1.317 的实测，版本升级可能变化。
- 尚未做重名检测、闲置归档与用量统计（原版有）。

## 数据与隐私

配置、每会话状态和简短日志在 `~/.claude/claude-session-title/`（可用 `CLAUDE_SESSION_TITLE_DATA` 覆盖）。日志只记录状态、标题、简短理由，不保存对话原文。对话摘录只发给当前登录所用的 Claude 模型服务。模型子进程带 `CLAUDE_TITLE_WORKER=1` 守卫并禁用 hook，不会递归触发自己。

## 开发

```bash
python -B -X utf8 -m unittest discover -s tests -t . -v
```

测试不会调用真实模型（用假的 `claude`）。`doctor --live` 才会。

## 许可

MIT，见 [LICENSE](LICENSE)。感谢 [oil-oil/oil-codex-title](https://github.com/oil-oil/oil-codex-title) 的原始设计。
