# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 17：看串流實際送回來的事件，以及工具參數送到一半時快照裡有什麼。

用法：uv run --env-file .env examples/day17/events.py

跑三段，每段都是真的呼叫 Messages API（Haiku 4.5，三段加起來不到 2 美分）：

1. 請它讀 note.txt，把收到的原始事件跟時間印出來
2. 請它建立一個新檔案，每收到一段工具參數，就印出 SDK 當下拼出來的 input
3. 同一題再跑一次，參數送到一半就停下來，看快照裡留下什麼

這支腳本只收事件、不執行工具，所以不會真的讀檔或寫檔。
工作目錄建在系統暫存資料夾，跑完就刪掉。
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("請先設好 ANTHROPIC_API_KEY。")

workdir = Path(tempfile.mkdtemp(prefix="kesi-day17-")).resolve()
(workdir / "note.txt").write_text("今天天氣很好\n", encoding="utf-8")

# 必須在載入 kesi 之前 chdir：它的 BASE_DIR 是載入時決定的
os.chdir(workdir)

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402

READ_QUESTION = "幫我讀一下 note.txt 的內容"
WRITE_QUESTION = "直接建立一個 hello.py，內容是一支印出 hello 的 Python 程式，不用先查看目錄"


def open_stream(client, question):
    return client.messages.stream(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=kesi.system_prompt(),
        tools=kesi.TOOLS,
        messages=[{"role": "user", "content": question}],
    )


def describe(event):
    """把原始事件整理成一行，只留跟內容有關的欄位。"""
    if event.type == "content_block_start":
        return f"content_block_start type={event.content_block.type}"
    if event.type == "content_block_delta":
        delta = event.delta
        if delta.type == "text_delta":
            return f"text_delta       {delta.text!r}"
        if delta.type == "input_json_delta":
            return f"input_json_delta {delta.partial_json!r}"
        return f"content_block_delta {delta.type}"
    if event.type == "message_delta":
        return (
            f"message_delta stop_reason={event.delta.stop_reason} "
            f"output_tokens={event.usage.output_tokens}"
        )
    return event.type


def show_trace(client):
    print(f"# 1. 原始事件：{READ_QUESTION}\n")
    counts = Counter()
    started = time.monotonic()
    with open_stream(client, READ_QUESTION) as stream:
        for event in stream:
            counts[event.type] += 1
            # SDK 另外會多送 text、input_json 這類整理過的事件，這裡先只印原始的
            if event.type in ("text", "input_json"):
                continue
            print(f"  [{time.monotonic() - started:5.2f}s] {describe(event)}")
    print("\n  迭代時拿到的事件種類與次數：")
    for name, count in sorted(counts.items()):
        print(f"    {name:<20} {count}")


def show_partial_input(client):
    print(f"\n# 2. 工具參數一段一段到：{WRITE_QUESTION}\n")
    with open_stream(client, WRITE_QUESTION) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                block = stream.current_message_snapshot.content[event.index]
                print(f"  收到 {event.delta.partial_json!r}")
                print(f"      快照裡的 input：{block.input}")
        final = stream.get_final_message()
    for block in final.content:
        if block.type == "tool_use":
            print(f"\n  收完之後：{block.name}({block.input})")


def show_interrupted(client):
    print(f"\n# 3. 參數送到一半就停下來：{WRITE_QUESTION}\n")
    finished = 0
    seen = 0
    stopped = False
    with open_stream(client, WRITE_QUESTION) as stream:
        for event in stream:
            if event.type == "content_block_stop":
                finished = event.index + 1
            if event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                seen += 1
                block = stream.current_message_snapshot.content[event.index]
                # 檔名已經拼出來、內容還沒到的時候停下來
                if "file_path" in block.input and "content" not in block.input:
                    stopped = True
                    break
        snapshot = stream.current_message_snapshot
    if stopped:
        print(f"  收到第 {seen} 段工具參數時停下來，完整的積木有 {finished} 塊")
    else:
        print(f"  這次沒遇到「有檔名、還沒有內容」的時間點，整則收完了（共 {seen} 段參數）")
    for index, block in enumerate(snapshot.content):
        if block.type == "text":
            print(f"    [{index}] text：{block.text!r}")
        elif block.type == "tool_use":
            print(f"    [{index}] tool_use：name={block.name} input={block.input}")
    print(f"    stop_reason={snapshot.stop_reason}")


def main():
    client = anthropic.Anthropic()
    try:
        show_trace(client)
        show_partial_input(client)
        show_interrupted(client)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
