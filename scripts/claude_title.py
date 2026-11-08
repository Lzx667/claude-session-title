#!/usr/bin/env python3
"""Claude 会话标题自动命名：Hook 入口与管理命令。

Stop（异步）生成候选存为 pending；下一条消息的 UserPromptSubmit 把 pending 写进 CLI 标题，
同事件里的 mcp_tool hook 再把 CLI 标题转给 Desktop。Hook 永远退出 0，不向宿主输出非 JSON。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import file_lock
import install_hooks
from claude_model import ModelError, find_claude, generate_candidate
from title_core import (POLICY_VERSION, build_snapshot, is_trivial, read_transcript,
                        skippable_confirmations, validate_candidate)

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "naming.md"
DEFAULTS = {
    "enabled": True,
    "model": "haiku",
    "claude_bin": None,
    "recent_turns": 5,
    "max_context_chars": 14000,
    "model_timeout_seconds": 100,
}
_SESSION_ID = re.compile(r"[0-9a-fA-F-]{8,64}")


def data_dir() -> Path:
    override = os.environ.get("CLAUDE_SESSION_TITLE_DATA")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    return Path(base).expanduser() / "claude-session-title"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON 文件必须是对象")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def load_config(root: Path) -> dict:
    config = DEFAULTS | read_json(root / "config.json")
    if not isinstance(config["enabled"], bool):
        raise ValueError("enabled 必须是布尔值")
    for key, lower, upper in (("recent_turns", 3, 5), ("max_context_chars", 3000, 20000),
                              ("model_timeout_seconds", 10, 110)):
        if type(config[key]) is not int or not lower <= config[key] <= upper:
            raise ValueError(f"{key} 必须在 {lower}～{upper} 之间")
    if not isinstance(config["model"], str) or not config["model"].strip():
        raise ValueError("model 不能为空")
    return config


def valid_session_id(value) -> str:
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise ValueError("session_id 无效")
    return value


def state_path(root: Path, session_id: str) -> Path:
    return root / "threads" / (session_id + ".json")


@contextmanager
def thread_lock(root: Path, session_id: str, wait_seconds: float = 0):
    # 内核锁在进程结束后释放，避免崩溃留下永久锁或两个 Worker 覆盖结果。
    path = root / "locks" / (session_id + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                file_lock.acquire(stream)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        try:
            yield True
        finally:
            file_lock.release(stream)


def audit(root: Path, session_id: str, entry: dict) -> None:
    # 只记录状态、标题与简短理由；不保存对话原文、提示词或模型推理。
    path = root / "logs" / (session_id + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 256 * 1024:
        os.replace(path, path.with_suffix(".previous.jsonl"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": int(time.time()), **entry}, ensure_ascii=False) + "\n")


def emit(output: dict) -> str:
    # ASCII 转义：不依赖 Windows 控制台代码页，宿主解析 JSON 后还原中文与 emoji。
    return json.dumps(output, ensure_ascii=True)


def _manual_rename(state: dict, title: str) -> bool:
    """已有本工具写入的基线，而当前标题不同，说明用户或宿主改过名。"""
    return bool(state.get("last_applied_title")) and bool(title) and title != state["last_applied_title"]


def _lock_session(root: Path, session_id: str, state: dict, reason: str) -> None:
    state.update(locked=True, lock_reason=reason)
    atomic_json(state_path(root, session_id), state)
    audit(root, session_id, {"status": "manual_title" if "手动" in reason else "locked", "reason": reason})


def handle_stop(event: dict, root: Path, generator=None, *, lock_wait=None) -> dict:
    config = load_config(root)
    if os.environ.get("CLAUDE_TITLE_WORKER") == "1" or not config["enabled"]:
        return {"status": "disabled"}
    session_id = valid_session_id(event.get("session_id"))
    transcript = event.get("transcript_path") or ""
    cwd, last_message = event.get("cwd"), event.get("last_assistant_message")
    if not read_transcript(transcript):
        return {"status": "no_transcript"}
    wait = config["model_timeout_seconds"] * 2 + 20 if lock_wait is None else lock_wait
    with thread_lock(root, session_id, wait) as acquired:
        if not acquired:
            return {"status": "busy"}
        path = state_path(root, session_id)
        state = read_json(path)
        if state.get("locked"):
            return {"status": "locked"}
        entries = read_transcript(transcript)
        snap = build_snapshot(entries, config, cwd=cwd, last_assistant_message=last_message)
        if not snap["has_messages"]:
            return {"status": "empty"}
        title = snap["title"]
        if _manual_rename(state, title):
            _lock_session(root, session_id, state, "检测到手动改名")
            return {"status": "manual_title", "title": title}
        if state.get("last_fingerprint") == snap["fingerprint"]:
            return {"status": "unchanged"}
        skipped = skippable_confirmations(entries, state, config, cwd=cwd)
        candidate = None
        if not skipped and not is_trivial(snap["context"]):
            generator = generator or (lambda context: generate_candidate(config, context, PROMPT))
            candidate = validate_candidate(generator(snap["context"]), title)
        # 模型运行期间用户可能继续对话、暂停或锁定：写入前重新核对。
        if not load_config(root)["enabled"]:
            return {"status": "disabled"}
        if read_json(path).get("locked"):
            return {"status": "locked"}
        after = build_snapshot(read_transcript(transcript), config, cwd=cwd, last_assistant_message=last_message)
        if after["fingerprint"] != snap["fingerprint"] or after["title"] != title:
            audit(root, session_id, {"status": "stale_result"})
            return {"status": "stale_result"}
        status, result_title = "kept", title
        if candidate is not None:
            if candidate["action"] == "rename" and candidate["title"] != title:
                state["pending_title"] = result_title = candidate["title"]
                status = "pending"
            else:
                state.pop("pending_title", None)  # 新证据表明当前标题合适，作废旧候选
        state.update(last_fingerprint=snap["fingerprint"], last_turn_id=snap["turn_ids"][-1],
                     policy_version=POLICY_VERSION, updated_at=int(time.time()),
                     confirmation_skips=state.get("confirmation_skips", 0) + skipped if skipped else 0)
        atomic_json(path, state)
        reason = candidate["reason"] if candidate else "新增内容仅为问候或确认，保留标题"
        audit(root, session_id, {"status": status, "title": result_title, "reason": reason, "skipped": skipped})
        return {"status": status, "title": result_title, "skipped": skipped}


def handle_prompt(event: dict, root: Path, *, lock_wait: float = 2):
    config = load_config(root)
    if os.environ.get("CLAUDE_TITLE_WORKER") == "1" or not config["enabled"]:
        return None, {"status": "disabled"}
    session_id = valid_session_id(event.get("session_id"))
    session_title = event.get("session_title") or ""
    with thread_lock(root, session_id, lock_wait) as acquired:
        if not acquired:
            return None, {"status": "busy"}
        path = state_path(root, session_id)
        state = read_json(path)
        if state.get("locked"):
            return None, {"status": "locked"}
        if _manual_rename(state, session_title):
            _lock_session(root, session_id, state, "检测到手动改名")
            return None, {"status": "manual_title"}
        pending = state.pop("pending_title", None)
        if not pending:
            return None, {"status": "idle"}
        try:
            validate_candidate({"action": "rename", "title": pending, "reason": ""}, session_title)
        except ValueError:
            atomic_json(path, state)
            return None, {"status": "cleared"}
        state["last_applied_title"] = pending
        atomic_json(path, state)
        if pending == session_title:
            return None, {"status": "cleared"}
        audit(root, session_id, {"status": "applied", "title": pending})
        output = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": pending}}
        return output, {"status": "applied", "title": pending}


def _run_hook(command: str, stdin, stdout, root: Path) -> None:
    session_id = "hook"
    try:
        raw = stdin.read(1024 * 1024)
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            return
        sid = event.get("session_id")
        if isinstance(sid, str) and _SESSION_ID.fullmatch(sid):
            session_id = sid
        if command == "stop":
            handle_stop(event, root)
        else:
            output, _ = handle_prompt(event, root)
            if output:
                stdout.write(emit(output))
    except Exception as exc:  # noqa: BLE001 - Hook 不能影响宿主，错误只写审计日志
        try:
            audit(root, session_id, {"status": "error", "error_type": type(exc).__name__,
                                     "message": str(exc)[:200]})
        except Exception:  # noqa: BLE001
            pass


def _set_lock(root: Path, session_id: str, locked: bool) -> dict:
    with thread_lock(root, session_id, 5) as acquired:
        if not acquired:
            raise ValueError("该会话正在命名，稍后再试")
        path = state_path(root, session_id)
        state = read_json(path)
        if locked:
            state.update(locked=True, lock_reason="用户设置")
            state.pop("pending_title", None)
        else:
            state["locked"] = False
            for key in ("last_applied_title", "pending_title", "last_fingerprint"):
                state.pop(key, None)  # 重置基线，避免立刻把此前的手动标题再次当成外部改名
        atomic_json(path, state)
        return {"status": "locked" if locked else "unlocked", "session_id": session_id}


_DOCTOR_CONTEXT = {
    "current_title": "", "project_hint": "", "original_goal": "修复邮箱注册的验证码过期问题",
    "recent_turns": [{"id": "doctor", "messages": [
        {"role": "user", "text": "修复邮箱注册的验证码过期问题"}, {"role": "assistant", "text": "已定位并修复"}]}],
}


def doctor(root: Path, settings_path, *, live=False, generator=None, runner=subprocess.run) -> dict:
    """环境自检；只有 live=True 才真实调用一次模型。"""
    config = load_config(root)
    report = {"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 10),
              "data_dir": str(root), "claude": None}
    try:
        report["claude"] = find_claude(config["claude_bin"])
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        proc = runner([report["claude"], "--version"], capture_output=True, encoding="utf-8",
                      errors="replace", timeout=15, **options)
        report["claude_version"] = (proc.stdout or proc.stderr or "").strip()
    except (ModelError, OSError, subprocess.SubprocessError) as exc:
        report["claude_error"] = str(exc)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root):
            report["data_dir_writable"] = True
    except OSError:
        report["data_dir_writable"] = False
    try:
        report["hooks"] = install_hooks.is_installed(settings_path)
    except ValueError as exc:
        report["hooks"], report["hooks_error"] = None, str(exc)
    if live:
        generator = generator or (lambda context: generate_candidate(config, context, PROMPT))
        try:
            report["live"] = generator(_DOCTOR_CONTEXT)
        except (ModelError, ValueError) as exc:
            report["live_error"] = str(exc)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude 会话标题自动命名")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stop", help="Stop Hook（读取 stdin）")
    sub.add_parser("prompt", help="UserPromptSubmit Hook（读取 stdin）")
    sub.add_parser("status", help="显示配置与记录数量")
    sub.add_parser("pause", help="暂停自动命名")
    sub.add_parser("resume", help="恢复自动命名")
    for name in ("lock", "unlock"):
        p = sub.add_parser(name, help="固定/解除固定会话标题；缺省使用 CLAUDE_CODE_SESSION_ID")
        p.add_argument("session_id", nargs="?")
    for name, text in (("install", "把 hook 写入 Claude Code 设置文件（写前备份）"),
                       ("uninstall", "从设置文件移除本工具的 hook"),
                       ("doctor", "检查 Python、claude、数据目录与 hook 安装状态")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--settings-file", help="默认 ~/.claude/settings.json")
        if name != "doctor":
            p.add_argument("--dry-run", action="store_true", help="只显示将要写入的内容")
        else:
            p.add_argument("--live", action="store_true", help="真实调用一次命名模型")
    return parser


def main(argv=None, stdin=None, stdout=None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    args = build_parser().parse_args(argv)
    root = data_dir()
    if args.command in ("stop", "prompt"):
        _run_hook(args.command, stdin, stdout, root)
        return 0
    try:
        if args.command in ("pause", "resume"):
            raw = read_json(root / "config.json")
            raw["enabled"] = args.command == "resume"
            atomic_json(root / "config.json", raw)
            result = load_config(root)
        elif args.command == "status":
            threads = root / "threads"
            result = {"config": load_config(root), "data_dir": str(root),
                      "tracked_sessions": len(list(threads.glob("*.json"))) if threads.exists() else 0}
        elif args.command in ("install", "uninstall", "doctor"):
            settings = Path(args.settings_file) if args.settings_file else install_hooks.default_settings_path()
            if args.command == "doctor":
                result = doctor(root, settings, live=args.live)
            else:
                if args.command == "install":
                    done = install_hooks.install(settings, Path(__file__).resolve(), dry_run=args.dry_run)
                else:
                    done = install_hooks.uninstall(settings, dry_run=args.dry_run)
                result = {"status": "ok", "changed": done["changed"], "backup": done["backup"],
                          "settings_file": str(settings), "dry_run": args.dry_run,
                          "hooks": done["settings"].get("hooks") if args.dry_run else None}
        else:
            session_id = valid_session_id(args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID"))
            result = _set_lock(root, session_id, args.command == "lock")
        stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        message = str(exc) if isinstance(exc, ValueError) else "操作失败；检查输入及本地配置"
        stdout.write(json.dumps({"status": "error", "message": message}, ensure_ascii=False) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
