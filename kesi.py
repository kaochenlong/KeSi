# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import readline  # 支援上下鍵翻歷史，不過 Windows 沒有這個模組
from pathlib import Path

import anthropic

client = anthropic.Anthropic()
BASE_DIR = Path.cwd().resolve()


def read_file(file_path):
    try:
        target = (BASE_DIR / file_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return f"錯誤：找不到或無法解析檔案 {file_path}", True

    if not target.is_relative_to(BASE_DIR):
        return "錯誤：不允許讀取工作目錄以外的檔案。", True
    if not target.is_file():
        return f"錯誤：不是可以讀取的文字檔 {file_path}", True

    try:
        return target.read_text(encoding="utf-8"), False
    except UnicodeDecodeError:
        return f"錯誤：檔案不是 UTF-8 文字檔 {file_path}", True
    except OSError as exc:
        return f"錯誤：無法讀取檔案 {file_path}：{exc}", True


TOOLS = [
    {
        "name": "read_file",
        "description": "讀取工作目錄底下的文字檔，回傳完整內容。"
        "當使用者問到某個檔案裡有什麼、或需要檔案內容才能回答時呼叫。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "相對於工作目錄的檔案路徑",
                }
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    }
]


def run_agent(history):
    while True:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            tools=TOOLS,
            messages=history,
        )
        history.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")

        results = []
        for block in resp.content:
            if block.type == "tool_use":
                print(f"  [執行工具] {block.name}({block.input})")
                if block.name == "read_file":
                    try:
                        output, is_error = read_file(**block.input)
                    except TypeError as exc:
                        output = f"錯誤：read_file 的參數不正確：{exc}"
                        is_error = True
                else:
                    output = f"錯誤：沒有 {block.name} 這個工具。"
                    is_error = True
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": is_error,
                    }
                )
        history.append({"role": "user", "content": results})


history = []
print("KeSi。輸入 /exit 離開、/reset 清空對話。")
while True:
    try:
        user = input("你 > ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if user in ("/exit", "/quit"):
        break
    if user == "/reset":
        history = []
        print("（日記已清空，我們重新開始）")
        continue
    if not user:
        continue

    history.append({"role": "user", "content": user})
    print("KeSi >", run_agent(history))

