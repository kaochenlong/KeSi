# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 17 串流的驗收：分段輸出不能把日記弄壞。不呼叫真的 API、不花錢。

用法：uv run examples/day17/check.py

驗的是五件事：
- 串流跑完的日記，結構跟不串流的時候一樣
- 文字真的是一段一段印出來的，不是最後一次吐出來
- 第一個事件前、或工具參數組到一半時中斷，都不會把日記弄壞
- 串流開始之後才出錯，只留下已經完成的文字
- 補上工具結果之後，日記真的能再送出一次請求

前兩件跟最後一件打的是 examples/day12/mock.py，它看到 "stream": true 就會改回 SSE 事件。
不過 mock 會把整份工具參數放在同一段送出，也只會在串流開始之前出錯，
所以「參數組到一半」跟「串流途中出錯」這兩種情況，改用一個假的 stream 物件，
在指定的事件序號丟出 KeyboardInterrupt 或 API 錯誤。

不需要 API 金鑰，也不會碰到你的專案，所有東西都在一個暫存目錄裡跑完就丟。
mock server 由腳本自己叫起來，用 MOCK_PORT 可以換埠號（預設 8803）。
全部通過會回結束碼 0，有任何一條沒過回 1。
"""

import http.client
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"
MOCK = HERE.parent / "day12" / "mock.py"
PORT = int(os.environ.get("MOCK_PORT", "8803"))

# 這幾行必須在載入 kesi 之前：kesi 的 BASE_DIR 是在載入時決定的，
# 而且我們要讓它打本機的 mock server，不是真的 API。
workdir = Path(tempfile.mkdtemp(prefix="kesi-day17-")).resolve()
os.chdir(workdir)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-FAKE"
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{PORT}"

import anthropic  # noqa: E402

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))


class Mock:
    """把 examples/day12/mock.py 叫起來，離開時收掉。"""

    def __init__(self, mode):
        self.env = {"MOCK_MODE": mode, "MOCK_PORT": str(PORT)}

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(MOCK)],
            env={**os.environ, **self.env},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=0.2)
                conn.request("GET", "/ping")
                conn.getresponse().read()
                conn.close()
                break
            except OSError:
                time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.proc.stdout.close()
        return False


def dangling(history):
    """日記裡有沒有沒被回覆的許願單。"""
    pending = []
    for index, item in enumerate(history):
        if item["role"] != "assistant" or not isinstance(item["content"], list):
            continue
        for block in item["content"]:
            if kesi.block_type(block) != "tool_use":
                continue
            following = history[index + 1] if index + 1 < len(history) else None
            answered = (
                following is not None
                and following["role"] == "user"
                and isinstance(following["content"], list)
                and any(
                    result.get("tool_use_id") == kesi.block_id(block)
                    for result in following["content"]
                )
            )
            if not answered:
                pending.append(kesi.block_id(block))
    return pending


def kept_blocks(history):
    return [
        block
        for item in history
        if item["role"] == "assistant" and isinstance(item["content"], list)
        for block in item["content"]
    ]


(workdir / "note.txt").write_text("hello\n", encoding="utf-8")

print("# 1. 串流跑完的日記結構正確")
with Mock("fail"):
    client = anthropic.Anthropic(max_retries=0)
    history = [{"role": "user", "content": "讀一個不存在的檔案"}]
    kesi.run_agent(client, history)
roles = [item["role"] for item in history]
check(
    "日記是 user、assistant 交錯",
    roles == ["user", "assistant", "user", "assistant", "user",
              "assistant", "user", "assistant"],
    f"共 {len(roles)} 則",
)
check("沒有懸空的許願單", not dangling(history))

print("\n# 2. 文字是一段一段印出來的")
chunks = []
real_print = print


def spy(*args, **kwargs):
    if args and kwargs.get("end") == "":
        chunks.append(args[0])
    return real_print(*args, **kwargs)


with Mock("plain"):
    client = anthropic.Anthropic(max_retries=0)
    kesi.print = spy
    try:
        kesi.run_agent(client, [{"role": "user", "content": "隨便講一句"}])
    finally:
        del kesi.print
text_chunks = [chunk for chunk in chunks if chunk and not chunk.startswith("KeSi")]
check(
    "文字分成多次印出",
    len(text_chunks) >= 2,
    f"文字分 {len(text_chunks)} 次印出",
)

print("\n# 3. 半截的許願單不會進日記")


class FakeEvent:
    def __init__(self, type_, index=None, delta=None):
        self.type = type_
        self.index = index
        self.delta = delta


class FakeDelta:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class FakeSnapshot:
    """照 SDK 1.9.0 的實際行為：參數只到一半，input 卻已經有一個看起來完整的 file_path。"""

    def __init__(self):
        self.content = [
            {"type": "text", "text": "我來讀一下"},
            {
                "type": "tool_use",
                "id": "toolu_half",
                "name": "read_file",
                "input": {"file_path": "note.txt"},
            },
        ]
        self.stop_reason = None


class FakeStreamError(anthropic.AnthropicError):
    pass


class FakeStream:
    """在指定的事件序號中斷或出錯，也模擬 SDK 什麼時候才有快照。"""

    def __init__(self, events, interrupt_at=None, error_at=None):
        self.events = events
        self.interrupt_at = interrupt_at
        self.error_at = error_at
        self.snapshot_ready = False

    @property
    def current_message_snapshot(self):
        assert self.snapshot_ready, "SDK 還沒收到 message_start"
        return FakeSnapshot()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for index, event in enumerate(self.events):
            if index == self.interrupt_at:
                raise KeyboardInterrupt
            if index == self.error_at:
                raise FakeStreamError("mock stream error")
            if event.type == "message_start":
                self.snapshot_ready = True
            yield event


EVENTS = [
    FakeEvent("message_start"),
    FakeEvent("content_block_delta", 0, FakeDelta("text_delta", "我來讀一下")),
    FakeEvent("content_block_stop", 0),
    FakeEvent("content_block_start", 1),
    FakeEvent("content_block_delta", 1, FakeDelta("input_json_delta")),
    FakeEvent("content_block_delta", 1, FakeDelta("input_json_delta")),
]


class FakeClient:
    def __init__(self, interrupt_at=None, error_at=None):
        self.interrupt_at = interrupt_at
        self.error_at = error_at

    @property
    def messages(self):
        return self

    def stream(self, **kwargs):
        return FakeStream(EVENTS, self.interrupt_at, self.error_at)


# 工具參數組到一半時中斷：文字那塊留下，看起來完整的半截工具單丟掉
history = [{"role": "user", "content": "讀檔"}]
kesi.run_agent(FakeClient(interrupt_at=5), history)
blocks = kept_blocks(history)
tools = [block for block in blocks if kesi.block_type(block) == "tool_use"]
check(
    "半截的工具單沒有進日記",
    not tools and any(kesi.block_type(block) == "text" for block in blocks),
    f"留下 {len(blocks)} 塊，其中工具單 {len(tools)} 張",
)
check("中斷後沒有懸空的許願單", not dangling(history))

# 連第一個事件都還沒收到就中斷：沒有快照可讀，整輪都不留
history = [{"role": "user", "content": "讀檔"}]
kesi.run_agent(FakeClient(interrupt_at=0), history)
check(
    "message_start 前中斷，不會去讀不存在的快照",
    not kept_blocks(history),
    f"日記 {len(history)} 則",
)

print("\n# 4. 串流途中出錯，日記仍然完整")
history = [{"role": "user", "content": "讀檔"}]
kesi.run_agent(FakeClient(error_at=3), history)
blocks = kept_blocks(history)
check(
    "串流途中出錯只留下完成的文字",
    blocks
    and all(kesi.block_type(block) == "text" for block in blocks)
    and history[-1]["role"] == "assistant"
    and "FakeStreamError" in history[-1]["content"],
    f"日記 {len(history)} 則",
)

print("\n# 5. 補完之後的日記能再送出一次請求")
history = [
    {"role": "user", "content": "讀檔"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "我來讀一下"},
            {
                "type": "tool_use",
                "id": "toolu_complete",
                "name": "read_file",
                "input": {"file_path": "note.txt"},
            },
        ],
    },
]
sealed = kesi.seal_dangling_tool_use(history)
with Mock("plain"):
    client = anthropic.Anthropic(max_retries=0)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        tools=kesi.TOOLS,
        messages=history,
    )
check(
    "補完之後的日記送到 mock 也收到回應",
    sealed and not dangling(history) and response.stop_reason == "end_turn",
    f"補完之後 {len(history)} 則，stop_reason={response.stop_reason}",
)

failed = [name for name, ok in results if not ok]
print(f"\n# {len(results) - len(failed)}/{len(results)} 通過")
if failed:
    print("# 沒過：" + "、".join(failed))
sys.exit(1 if failed else 0)
