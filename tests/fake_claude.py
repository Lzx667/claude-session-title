"""假的 claude 命令行（仅测试使用）：按 FAKE_CLAUDE_MODE 输出不同形态，并把调用现场写入 FAKE_CLAUDE_LOG。"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

CANDIDATE = {"action": "rename", "title": "🧩 登录表单｜布局优化", "reason": "依据"}
stdin_text = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "w", encoding="utf-8") as stream:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(),
                   "worker": os.environ.get("CLAUDE_TITLE_WORKER"), "stdin": stdin_text},
                  stream, ensure_ascii=False)

mode = os.environ.get("FAKE_CLAUDE_MODE", "structured")
candidate_text = json.dumps(CANDIDATE, ensure_ascii=False)
if mode == "fail":
    sys.stderr.write("boom")
    sys.exit(3)
if mode == "slow":
    time.sleep(5)
if mode == "structured":
    out = {"type": "result", "result": "", "structured_output": CANDIDATE}
elif mode == "fenced":
    out = {"type": "result", "result": "```json\n" + candidate_text + "\n```"}
elif mode == "prose":
    out = {"type": "result", "result": "好的，结果如下：" + candidate_text + " 。完毕"}
elif mode == "error":
    out = {"type": "result", "is_error": True, "result": "Not logged in"}
elif mode == "notjson":
    sys.stdout.write("plain text")
    sys.exit(0)
else:
    out = {"type": "result", "result": "这里没有 JSON"}
sys.stdout.write(json.dumps(out, ensure_ascii=False))
