"""Stand-in for a headless agent CLI: prints Claude-style stream-json so runner tests spend no real tokens."""
import json
import sys
import time
from pathlib import Path


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def assistant(n: int, tokens: int) -> dict:
    return {"type": "assistant", "message": {"id": f"msg_{n}", "content": [{"type": "text", "text": f"step {n}"}],
                                             "usage": {"input_tokens": tokens // 2, "output_tokens": tokens // 2,
                                                       "cache_read_input_tokens": 99_999}}}


mode, marker = sys.argv[1], Path(sys.argv[2])
emit({"type": "system", "subtype": "init", "session_id": "fake-session"})
if mode == "ok":
    emit(assistant(1, 150))
    emit(assistant(1, 150))  # same message id again: must not be counted twice
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    emit({"type": "result", "subtype": "success", "result": "Implemented and verified.", "total_cost_usd": 0.01})
elif mode == "burn":
    for n in range(100_000):
        emit(assistant(n, 500))
        time.sleep(0.02)
elif mode == "hang":
    time.sleep(600)
elif mode == "fail":
    sys.exit(3)
