# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Day 12 用的故障 mock server，專門製造 agent loop 會遇到的各種狀況。

用 ANTHROPIC_BASE_URL 把 KeSi 導過來，就能在不花 API 錢、也不等真的限流的情況下，
觀察 SDK 的重試行為與 KeSi 自己的圈數上限。

模式由 MOCK_MODE 環境變數決定：

- loop        永遠回同一張許願單，用來測 MAX_TURNS
- fail        永遠回一張會失敗的許願單，用來測 MAX_TOOL_FAILURES
- 429         永遠回 429（RateLimitError）
- 429-after   回 429 並附上 retry-after，看 SDK 照不照做
- 529         永遠回 529（OverloadedError）
- 500-then-ok 前 MOCK_FAILURES 次回 500，之後正常回應
- 400         回 400（不該被重試）
- 400-retry   回 400 加 x-should-retry: true（應照標頭重試）

每次收到請求都會印一行，帶上距離上一次請求的秒數與 SDK 自報的
x-stainless-retry-count，重試的節奏直接看得出來。

用法：MOCK_MODE=429 uv run examples/day12/mock.py

examples/day12/check.py 會自己把這支叫起來，只有想單獨試某一種狀況時才需要自己跑。
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ.get("MOCK_MODE", "loop")
PORT = int(os.environ.get("MOCK_PORT", "8799"))
FAILURES = int(os.environ.get("MOCK_FAILURES", "2"))
RETRY_AFTER = os.environ.get("MOCK_RETRY_AFTER", "1")

state = {"count": 0, "last": None}


def message(content, stop_reason):
    return {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "model": "mock",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def tool_use(name, tool_input, text):
    return message(
        [
            {"type": "text", "text": text},
            {
                "type": "tool_use",
                "id": f"toolu_mock{state['count']}",
                "name": name,
                "input": tool_input,
            },
        ],
        "tool_use",
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        """探活用，不列入請求計數。"""
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        state["count"] += 1
        now = time.monotonic()
        gap = "" if state["last"] is None else f"，距離上次 {now - state['last']:.2f} 秒"
        state["last"] = now
        retry_count = self.headers.get("x-stainless-retry-count", "?")
        length = int(self.headers.get("Content-Length", 0))
        print(
            f"第 {state['count']} 次請求（retry-count={retry_count}"
            f"，body {length} bytes{gap}）",
            flush=True,
        )
        raw = self.rfile.read(length)
        try:
            self.wants_stream = json.loads(raw or b"{}").get("stream", False)
        except ValueError:
            self.wants_stream = False

        if MODE == "429":
            self.fail(429, "rate_limit_error", "Number of requests has exceeded your rate limit.")
        elif MODE == "429-after":
            self.fail(
                429,
                "rate_limit_error",
                "Number of requests has exceeded your rate limit.",
                extra_headers={"retry-after": RETRY_AFTER},
            )
        elif MODE == "529":
            self.fail(529, "overloaded_error", "Overloaded")
        elif MODE == "400":
            self.fail(400, "invalid_request_error", "prompt is too long")
        elif MODE == "400-retry":
            self.fail(
                400,
                "invalid_request_error",
                "prompt is too long",
                extra_headers={"x-should-retry": "true"},
            )
        elif MODE == "500-then-ok" and state["count"] <= FAILURES:
            self.fail(500, "api_error", "Internal server error")
        elif MODE == "fail":
            self.ok(tool_use("read_file", {"file_path": "不存在的檔案.txt"}, "我看一下這個檔案。"))
        elif MODE == "loop":
            self.ok(tool_use("list_files", {}, "我再看一次目錄。"))
        else:
            self.ok(message([{"type": "text", "text": "（mock）好了。"}], "end_turn"))

    def ok(self, payload):
        """SDK 用 stream() 呼叫時要收 SSE，不能回一整包 JSON。"""
        if getattr(self, "wants_stream", False):
            self.stream(payload)
        else:
            self.respond(200, payload)

    def stream(self, payload):
        """把一則完整訊息拆成事件序列送出，格式照 Messages API 的 SSE。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("request-id", "req_mock")
        self.end_headers()

        skeleton = {**payload, "content": [], "stop_reason": None}
        self.event("message_start", {"type": "message_start", "message": skeleton})

        for index, block in enumerate(payload["content"]):
            if block["type"] == "text":
                self.event("content_block_start", {
                    "type": "content_block_start", "index": index,
                    "content_block": {"type": "text", "text": ""},
                })
                # 切成幾段送，讓收端看得出來這是逐塊到的
                text = block["text"]
                for piece in [text[i:i + 6] for i in range(0, len(text), 6)]:
                    self.event("content_block_delta", {
                        "type": "content_block_delta", "index": index,
                        "delta": {"type": "text_delta", "text": piece},
                    })
                    time.sleep(0.02)
            else:
                self.event("content_block_start", {
                    "type": "content_block_start", "index": index,
                    "content_block": {
                        "type": "tool_use", "id": block["id"],
                        "name": block["name"], "input": {},
                    },
                })
                self.event("content_block_delta", {
                    "type": "content_block_delta", "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block["input"]),
                    },
                })
            self.event("content_block_stop",
                       {"type": "content_block_stop", "index": index})

        self.event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": payload["stop_reason"], "stop_sequence": None},
            "usage": {"output_tokens": payload["usage"]["output_tokens"]},
        })
        self.event("message_stop", {"type": "message_stop"})

    def event(self, name, data):
        body = f"event: {name}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(body.encode())
        self.wfile.flush()

    def fail(self, status, error_type, text, extra_headers=None):
        payload = {"type": "error", "error": {"type": error_type, "message": text}}
        self.respond(status, payload, extra_headers)

    def respond(self, status, payload, extra_headers=None):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", "req_mock")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"mock Anthropic API on 127.0.0.1:{PORT}，模式 {MODE}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
