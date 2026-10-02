# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 18 電表的驗收。不呼叫真的 API、不花錢。

用法：uv run examples/day18/check.py

驗的是三件事：
- 電表的加總、`since` 跟價錢算得對
- 中斷時記下的那筆會標成 partial，帳單那一行會註明
- 串流開始之後斷線（丟出來的不是 AnthropicError），KeSi 不會當掉，日記只留完成的文字

斷線用一個假的 stream 物件模擬，在指定的事件序號丟出 ConnectionResetError。
不需要 API 金鑰，所有東西都在一個暫存目錄裡跑完就丟。
全部通過會回結束碼 0，有任何一條沒過回 1。
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

# 必須在載入 kesi 之前 chdir：它的 BASE_DIR 是載入時決定的
workdir = Path(tempfile.mkdtemp(prefix="kesi-day18-")).resolve()
os.chdir(workdir)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-FAKE"

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))


def usage(inp, out, cache_write=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )


print("# 1. 電表的加總跟價錢")
meter = kesi.Meter()
meter.record(None)
check("沒有 usage 不記帳", meter.calls == [])

meter.record(usage(1_000_000, 0))
meter.record(usage(0, 1_000_000))
meter.record(usage(0, 0, cache_write=1_000_000, cache_read=1_000_000))
check(
    "四種 token 各一百萬，照價目表加起來",
    round(meter.cost(), 6) == 1.0 + 5.0 + 1.25 + 0.1,
    f"${meter.cost():.4f}",
)
check(
    "since 只算後面的請求",
    meter.totals(since=1)["input"] == 0 and round(meter.cost(since=1), 6) == 6.35,
    f"${meter.cost(since=1):.4f}",
)

print("\n# 2. 中斷的那筆會被註明")
meter = kesi.Meter()
meter.record(usage(100, 20))
meter.record(usage(200, 5), partial=True)
line = meter.report()
check("帳單那一行有註明", "其中 1 次只算到中斷前收到的部分" in line, line)
check("完整的帳不會被註明", "中斷" not in kesi.Meter().report())

print("\n# 3. 串流途中斷線，KeSi 不會當掉")


class FakeEvent:
    def __init__(self, type_, index=None, delta=None):
        self.type = type_
        self.index = index
        self.delta = delta


class FakeStream:
    """前三個事件正常送出（一段完整的文字），接著就斷線。"""

    events = [
        FakeEvent("message_start"),
        FakeEvent("content_block_delta", 0, SimpleNamespace(type="text_delta", text="我來讀一下")),
        FakeEvent("content_block_stop", 0),
        FakeEvent("content_block_start", 1),
    ]

    def __init__(self):
        self.started = False

    @property
    def current_message_snapshot(self):
        assert self.started, "SDK 還沒收到 message_start"
        return SimpleNamespace(
            content=[{"type": "text", "text": "我來讀一下"}],
            stop_reason=None,
            usage=usage(300, 3),
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for index, event in enumerate(self.events):
            if index == 3:
                # 真的斷線時丟出來的是 httpx2.ReadError，它也不是 AnthropicError
                raise ConnectionResetError("[Errno 54] Connection reset by peer")
            if event.type == "message_start":
                self.started = True
            yield event


class FakeClient:
    messages = property(lambda self: self)

    def stream(self, **kwargs):
        return FakeStream()


kesi.METER.calls.clear()
history = [{"role": "user", "content": "讀檔"}]
try:
    kesi.run_agent(FakeClient(), history)
    crashed = None
except Exception as exc:  # noqa: BLE001
    crashed = exc
check("沒有往外丟錯誤", crashed is None, repr(crashed) if crashed else "")
kept = [
    block
    for item in history
    if item["role"] == "assistant" and isinstance(item["content"], list)
    for block in item["content"]
]
check(
    "日記只留下完成的文字",
    kept and all(kesi.block_type(block) == "text" for block in kept),
    f"留下 {len(kept)} 塊",
)
check(
    "最後一則告訴使用者斷線了",
    history[-1]["role"] == "assistant"
    and "ConnectionResetError" in str(history[-1]["content"]),
    str(history[-1]["content"]),
)
check(
    "斷線前收到的 usage 有記下來，標成 partial",
    len(kesi.METER.calls) == 1 and kesi.METER.calls[0]["partial"],
    f"{len(kesi.METER.calls)} 筆",
)

failed = [name for name, ok in results if not ok]
print(f"\n# {len(results) - len(failed)}/{len(results)} 通過")
if failed:
    print("# 沒過：" + "、".join(failed))
sys.exit(1 if failed else 0)
