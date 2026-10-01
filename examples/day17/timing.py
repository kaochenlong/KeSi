# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 17：同樣的請求，串流跟不串流各要等多久才看得到東西。

用法：uv run --env-file .env examples/day17/timing.py [--trials 3]

兩個題目，一個短（會用工具）、一個長（純文字），每一題串流跟不串流交錯著跑，
預設各 3 次。每一次都印一行：第一段文字什麼時候到、整則什麼時候收完、
output token 數。不串流的那一次，第一段文字跟收完是同一個時間。

output token 在不串流時讀 response.usage，串流時讀收完之後的最終訊息，
兩邊是同一個欄位。

會真的呼叫 Messages API（Haiku 4.5），預設 12 次請求，大約幾美分。
只收回應、不執行工具。時間會受網路與服務負載影響，每次跑都不一樣。
"""

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("請先設好 ANTHROPIC_API_KEY。")

TRIALS = 3
if "--trials" in sys.argv:
    TRIALS = int(sys.argv[sys.argv.index("--trials") + 1])

workdir = Path(tempfile.mkdtemp(prefix="kesi-day17-")).resolve()
(workdir / "note.txt").write_text("今天天氣很好\n", encoding="utf-8")
os.chdir(workdir)

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402

QUESTIONS = [
    ("短", "幫我讀一下 note.txt 的內容"),
    ("長", "不用查任何檔案，直接用大約 600 個中文字介紹 Python 的 list 跟 tuple 差在哪"),
]


def request(question):
    return {
        "model": "claude-haiku-4-5",
        "max_tokens": 1024,
        "system": kesi.system_prompt(),
        "tools": kesi.TOOLS,
        "messages": [{"role": "user", "content": question}],
    }


def streamed(client, question):
    started = time.monotonic()
    first = None
    with client.messages.stream(**request(question)) as stream:
        for event in stream:
            if (
                first is None
                and event.type == "content_block_delta"
                and event.delta.type == "text_delta"
            ):
                first = time.monotonic() - started
        final = stream.get_final_message()
    return first, time.monotonic() - started, final.usage.output_tokens


def unstreamed(client, question):
    started = time.monotonic()
    resp = client.messages.create(**request(question))
    elapsed = time.monotonic() - started
    has_text = any(block.type == "text" for block in resp.content)
    return (elapsed if has_text else None), elapsed, resp.usage.output_tokens


def main():
    client = anthropic.Anthropic()
    for label, question in QUESTIONS:
        print(f"# {label}：{question}")
        for trial in range(1, TRIALS + 1):
            for mode, run in (("串流", streamed), ("不串流", unstreamed)):
                first, total, tokens = run(client, question)
                first_text = f"{first:5.2f}s" if first is not None else "  （無）"
                print(
                    f"  第 {trial} 次  {mode:<3}  第一段文字 {first_text}"
                    f"  收完 {total:5.2f}s  output {tokens}"
                )
        print()


if __name__ == "__main__":
    main()
