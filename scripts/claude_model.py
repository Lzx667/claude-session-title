"""独立命名模型：用 `claude -p` 起一个无工具、无 hook、不落盘会话的临时调用。

复用当前机器上 claude 的登录，不复制凭据；对话摘录只发往该登录所用的模型服务。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class ModelError(RuntimeError):
    pass


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["keep", "rename"]},
        "title": {"type": "string"}, "reason": {"type": "string"},
    },
    "required": ["action", "title", "reason"],
}


def find_claude(explicit: str | None = None) -> str:
    found = shutil.which(explicit or "claude")
    if not found:
        raise ModelError("未找到 claude 命令；请安装并登录 Claude Code，或在配置里设置 claude_bin")
    if sys.platform == "win32" and Path(found).suffix.lower() in (".cmd", ".bat"):
        # 经 cmd.exe 会改写 JSON 参数里的引号，改用 npm 包内的原生 exe。
        native = Path(found).parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        if not native.is_file():
            raise ModelError("找到的是 .cmd 入口，请把 claude_bin 设为原生 claude.exe 的路径")
        return str(native)
    return found


def build_argv(claude, config: dict, prompt_file: Path) -> list[str]:
    prefix = [claude] if isinstance(claude, str) else list(claude)
    return prefix + [
        "-p", "--model", config["model"],
        "--system-prompt-file", str(prompt_file),
        "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--disable-slash-commands",
        "--settings", json.dumps({"disableAllHooks": True}),
    ]


def _first_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ModelError("模型输出中没有可解析的 JSON 对象")


def parse_output(stdout: str) -> dict:
    try:
        outer = json.loads(stdout)
    except ValueError as exc:
        raise ModelError("模型输出不是 JSON") from exc
    if not isinstance(outer, dict):
        raise ModelError("模型输出结构无效")
    if outer.get("is_error"):
        raise ModelError("命名模型返回错误；请检查 claude 登录与模型配置")
    structured = outer.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = outer.get("result")
    if not isinstance(result, str):
        raise ModelError("模型输出缺少 result")
    return _first_object(result)


def generate_candidate(config: dict, context: dict, prompt_file: Path, *,
                       runner=subprocess.run, claude=None) -> dict:
    argv = build_argv(claude or find_claude(config.get("claude_bin")), config, prompt_file)
    env = {**os.environ, "CLAUDE_TITLE_WORKER": "1"}  # 子进程守卫：hook 再次触发时直接退出
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    # 空临时目录作 cwd：不加载项目配置与项目 CLAUDE.md。
    with tempfile.TemporaryDirectory(prefix="claude-session-title-") as tmp:
        try:
            proc = runner(argv, input=json.dumps(context, ensure_ascii=False), capture_output=True,
                          encoding="utf-8", errors="replace", env=env, cwd=tmp,
                          timeout=config["model_timeout_seconds"], **options)
        except subprocess.TimeoutExpired as exc:
            raise ModelError("命名模型超时；保留原标题") from exc
        except OSError as exc:
            raise ModelError("无法启动 claude：" + type(exc).__name__) from exc
    if proc.returncode:
        raise ModelError(f"命名模型退出码 {proc.returncode}；请检查登录、模型配置和 doctor")
    return parse_output(proc.stdout or "")
