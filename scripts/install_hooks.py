"""把本工具的 hook 写入 Claude Code 设置文件，或从中移除。

写前备份、原子写入；设置文件不是合法 JSON 对象时拒绝改动。幂等：重复安装结果不变。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from title_core import long_path

MARKER = "claude_title.py"
SERVER, TOOL, TITLE_INPUT = "ccd_session_mgmt", "set_session_title", "${session_title}"


def default_settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")).expanduser() / "settings.json"


def python_command() -> str:
    return "python" if sys.platform == "win32" else "python3"


def hook_groups(script: Path, python_cmd: str | None = None) -> dict:
    base = f'{python_cmd or python_command()} -X utf8 "{Path(script).as_posix()}"'
    return {
        "Stop": [{"hooks": [{"type": "command", "command": f"{base} stop", "async": True, "timeout": 180}]}],
        "UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": f"{base} prompt", "timeout": 10},
            {"type": "mcp_tool", "server": SERVER, "tool": TOOL,
             "input": {"session_id": "self", "title": TITLE_INPUT}, "timeout": 20},
        ]}],
    }


def _is_ours(hook) -> bool:
    if not isinstance(hook, dict):
        return False
    if hook.get("type") == "command":
        return MARKER in str(hook.get("command", ""))
    if hook.get("type") == "mcp_tool":
        return (hook.get("server") == SERVER and hook.get("tool") == TOOL
                and (hook.get("input") or {}).get("title") == TITLE_INPUT)
    return False


def _strip_ours(hooks: dict) -> dict:
    """去掉本工具的 hook；清掉因此变空的组与事件，其它内容原样保留。"""
    out = {}
    for event, groups in hooks.items():
        kept_groups = []
        for group in groups if isinstance(groups, list) else []:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept = [h for h in inner if not _is_ours(h)]
            if kept or not inner:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups or not isinstance(groups, list):
            out[event] = kept_groups if isinstance(groups, list) else groups
    return out


def _load(path: Path) -> dict:
    real = long_path(path)
    if not os.path.exists(real):
        return {}
    try:
        with open(real, encoding="utf-8") as stream:
            value = json.load(stream)
    except ValueError as exc:
        raise ValueError("设置文件不是合法 JSON，未做任何改动") from exc
    if not isinstance(value, dict):
        raise ValueError("设置文件必须是 JSON 对象，未做任何改动")
    return value


def _write(path: Path, value: dict) -> None:
    real = long_path(path)
    parent = os.path.dirname(real)
    os.makedirs(parent, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".write-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(filename, real)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def _commit(path: Path, current: dict, new: dict, dry_run: bool) -> dict:
    changed = new != current
    backup = None
    if changed and not dry_run:
        if os.path.exists(long_path(path)):
            backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(long_path(path), long_path(backup))
        _write(path, new)
    return {"changed": changed, "backup": str(backup) if backup else None, "settings": new}


def install(settings_path, script: Path, *, dry_run=False, python_cmd=None) -> dict:
    path = Path(settings_path)
    current = _load(path)
    new = copy.deepcopy(current)
    hooks = new.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("设置文件里的 hooks 必须是对象，未做任何改动")
    hooks = _strip_ours(hooks)
    for event, groups in hook_groups(script, python_cmd).items():
        hooks.setdefault(event, []).extend(groups)
    new["hooks"] = hooks
    return _commit(path, current, new, dry_run)


def uninstall(settings_path, *, dry_run=False) -> dict:
    path = Path(settings_path)
    current = _load(path)
    new = copy.deepcopy(current)
    hooks = current.get("hooks")
    if isinstance(hooks, dict):
        stripped = _strip_ours(hooks)
        if stripped != hooks:
            if stripped:
                new["hooks"] = stripped
            else:
                new.pop("hooks")
    return _commit(path, current, new, dry_run)


def is_installed(settings_path) -> dict:
    hooks = _load(Path(settings_path)).get("hooks", {})
    hooks = hooks if isinstance(hooks, dict) else {}

    def has(event, kind):
        return any(_is_ours(h) and h.get("type") == kind
                   for g in hooks.get(event, []) if isinstance(g, dict)
                   for h in g.get("hooks", []) if isinstance(h, dict))

    return {"stop": has("Stop", "command"), "prompt": has("UserPromptSubmit", "command"),
            "mcp": has("UserPromptSubmit", "mcp_tool")}
