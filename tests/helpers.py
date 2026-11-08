"""测试辅助：按真实 transcript 的字段形状构造条目（仅测试使用）。"""
import json
from pathlib import Path


def write_transcript(path, entries):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
                    encoding="utf-8")


def user(text, uuid, **extra):
    """真实用户消息：message.content 是字符串。"""
    return {"type": "user", "uuid": uuid, "isSidechain": False,
            "message": {"role": "user", "content": text}, **extra}


def assistant(text, uuid, blocks=None, **extra):
    """助手条目：每个内容块单独成行，content 是块列表。"""
    content = blocks if blocks is not None else [{"type": "text", "text": text}]
    return {"type": "assistant", "uuid": uuid, "isSidechain": False,
            "message": {"role": "assistant", "content": content, "stop_reason": "end_turn"}, **extra}


def tool_result_user(uuid):
    """工具结果也以 user 条目承载，content 是 tool_result 块列表。"""
    return {"type": "user", "uuid": uuid, "isSidechain": False,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def title_entry(kind, value):
    if kind == "custom":
        return {"type": "custom-title", "customTitle": value, "sessionId": "s"}
    return {"type": "ai-title", "aiTitle": value, "sessionId": "s"}


def config(**over):
    return {"recent_turns": 5, "max_context_chars": 14000, **over}
