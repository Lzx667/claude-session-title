"""纯逻辑：transcript 解析、摘录与快照、候选标题校验。无副作用，便于测试。

标题规则与摘录思路移植自 oil-oil/oil-codex-title（MIT），数据来源改为 Claude Code 的 transcript。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

EMOJI = ("🎬", "🧩", "🔎", "📝", "📅", "🎨", "⚙️", "💬")
POLICY_VERSION = 1

# Desktop/CLI 注入的包装块；不是用户真实表达的需求。
_WRAPPER = re.compile(r"<(system-reminder|command-[\w-]+|local-command-[\w-]+)\b[^>]*>[\s\S]*?</\1>")


def clean_text(text: str) -> str:
    return _WRAPPER.sub("", text).strip()


def long_path(path) -> str:
    """Windows 下路径超过 260 字符时加 \\\\?\\ 前缀（用 chr 拼接，避免转义出错）。"""
    if sys.platform != "win32":
        return str(path)
    absolute = os.path.abspath(str(path))
    if len(absolute) < 240 or absolute.startswith(chr(92) * 2):
        return absolute
    return chr(92) * 2 + "?" + chr(92) + absolute


def read_transcript(path: str) -> list[dict]:
    if not path:
        return []
    entries = []
    try:
        with open(long_path(path), encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    entries.append(value)
    except OSError:
        return []
    return entries


def current_title(entries: list[dict]) -> str:
    """CLI 视角的当前标题：最后一个 custom-title，否则最后一个 ai-title。"""
    custom = ai = ""
    for entry in entries:
        if entry.get("type") == "custom-title" and entry.get("customTitle"):
            custom = entry["customTitle"]
        elif entry.get("type") == "ai-title" and entry.get("aiTitle"):
            ai = entry["aiTitle"]
    return custom or ai


def project_hint(cwd: str | None) -> str:
    if not cwd:
        return ""
    path = Path(cwd)
    if path == Path.home() or path.parent.name.lower() in ("users", "home"):
        return ""
    name = path.name
    if name.lower() in ("desktop", "documents", "downloads", "tmp", "project", "projects"):
        return ""
    return name if re.fullmatch(r"[\w .-]{1,64}", name) else ""


def _user_text(content) -> str | None:
    """真实用户消息的文本；工具结果或无文本块返回 None。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        return "\n".join(texts) if texts else None
    return None


def parse_turns(entries: list[dict]) -> list[dict]:
    """按真实用户消息切分轮次；每轮保留用户请求与最后一个助手文本块。"""
    turns: list[dict] = []
    for index, entry in enumerate(entries):
        if entry.get("isSidechain"):
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if entry.get("type") == "user" and not entry.get("isMeta"):
            text = _user_text(message.get("content"))
            cleaned = clean_text(text) if text else ""
            if cleaned:
                turns.append({"id": entry.get("uuid") or str(index), "user": cleaned, "assistant": ""})
        elif entry.get("type") == "assistant" and turns and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    cleaned = clean_text(block.get("text", ""))
                    if cleaned:
                        turns[-1]["assistant"] = cleaned
    return turns


def snapshot_from_turns(turns: list[dict], title: str, config: dict, *, cwd=None) -> dict:
    hint = project_hint(cwd)
    selected = turns[-config["recent_turns"]:]
    total = sum(1 + bool(t["assistant"]) for t in selected)
    # 为每轮的用户目标保留空间；助手长回答不能挤掉其他轮。
    per_message = max(200, (config["max_context_chars"] - 2000) // max(1, total))
    recent = []
    for turn in selected:
        messages = [{"role": "user", "text": turn["user"][:min(per_message, 1200)]}]
        if turn["assistant"]:
            messages.append({"role": "assistant", "text": turn["assistant"][:min(per_message, 500)]})
        recent.append({"id": turn["id"], "messages": messages})
    signature = json.dumps({"policy_version": POLICY_VERSION, "project_hint": hint, "recent": recent},
                           ensure_ascii=False, sort_keys=True)
    return {
        "title": title,
        "has_messages": bool(turns),
        "fingerprint": hashlib.sha256(signature.encode("utf-8")).hexdigest(),
        "turn_ids": [t["id"] for t in turns],
        "context": {"current_title": title, "project_hint": hint,
                    "original_goal": turns[0]["user"][:800] if turns else "",
                    "recent_turns": recent},
    }


def build_snapshot(entries: list[dict], config: dict, *, cwd=None, last_assistant_message=None) -> dict:
    turns = parse_turns(entries)
    if turns and last_assistant_message:
        cleaned = clean_text(last_assistant_message)
        if cleaned:  # Stop 事件自带最新回复，不依赖 transcript 是否已落盘
            turns[-1] = {**turns[-1], "assistant": cleaned}
    return snapshot_from_turns(turns, current_title(entries), config, cwd=cwd)


def validate_candidate(candidate, current_title: str) -> dict:
    """校验模型输出；keep 时把 title 还原为当前标题。非法输入抛 ValueError。"""
    if not isinstance(candidate, dict) or set(candidate) != {"action", "title", "reason"}:
        raise ValueError("模型输出字段无效")
    if candidate["action"] not in ("rename", "keep") or not all(
        isinstance(candidate[k], str) for k in ("title", "reason")
    ):
        raise ValueError("模型输出类型无效")
    if candidate["action"] == "keep":
        candidate = {**candidate, "title": current_title}
    else:
        title = candidate["title"]
        if title != title.strip() or not 4 <= len(title) <= 48:
            raise ValueError("标题长度或空白无效")
        if not any(title.startswith(e + " ") for e in EMOJI):
            raise ValueError("标题缺少允许的类别 emoji")
        body = title.split(" ", 1)[1]
        if body.count("｜") != 1 or "|" in body:
            raise ValueError("标题必须采用对象｜目标结构")
        if any(not part or part != part.strip() for part in body.split("｜")):
            raise ValueError("标题对象与目标不能为空或带边缘空格")
        if any(e in body for e in EMOJI) or any(
            0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF for c in body
        ):
            raise ValueError("标题正文包含多个类别 emoji")
        if any(unicodedata.category(c).startswith("C") for c in title):
            raise ValueError("标题含控制字符")
        if re.search(r"[A-Za-z]:[\\/]|\\\\", title):
            raise ValueError("标题含 Windows 绝对路径")
        if any(x in title for x in ("`", "https://", "http://", "@", "/Users/", "sk-")):
            raise ValueError("标题含不允许的格式或私人信息")
    return {**candidate, "reason": candidate["reason"][:300]}


# 只有问候或确认时没有命名依据；确定性保留，避免模型凭空生成“普通讨论”。
_TRIVIAL = {"", "你好", "您好", "hi", "hello", "嗨", "谢谢", "好的", "好", "ok", "收到", "继续", "嗯"}
_CONFIRMATIONS = {"好", "好的", "可以", "收到", "谢谢", "继续", "ok", "okay", "thanks", "thank you", "continue"}


def is_trivial(context: dict) -> bool:
    texts = [context.get("original_goal", "")] + [
        m.get("text", "") for turn in context.get("recent_turns", [])
        for m in turn.get("messages", []) if m.get("role") == "user"
    ]
    return all(re.sub(r"[\W_]+", "", text).casefold() in _TRIVIAL for text in texts)


def skippable_confirmations(entries: list[dict], state: dict, config: dict, *, cwd=None) -> int:
    """基线仍一致时，可免模型跳过的纯确认轮数（0～2）；附件、额外需求、规则升级均重新判断。"""
    if state.get("policy_version") != POLICY_VERSION or not state.get("last_fingerprint"):
        return 0
    turns = parse_turns(entries)
    index = next((i for i, t in enumerate(turns) if t["id"] == state.get("last_turn_id")), None)
    if index is None:
        return 0
    pending = turns[index + 1:]
    if not pending or len(pending) + state.get("confirmation_skips", 0) > 2:
        return 0
    if snapshot_from_turns(turns[:index + 1], "", config, cwd=cwd)["fingerprint"] != state["last_fingerprint"]:
        return 0
    if any(t["user"].strip(" \t\r\n.!。！").casefold() not in _CONFIRMATIONS for t in pending):
        return 0
    return len(pending)
