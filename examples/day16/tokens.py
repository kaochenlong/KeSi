# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""量一下 system prompt 讓每一次請求多了多少 input token。

用法：在 repo 根目錄執行 uv run --env-file .env examples/day16/tokens.py

只呼叫 Token Counting API，不會讓模型產生回答，不過還是需要 API 金鑰。
這支腳本會把完整的工具定義、system prompt，以及包含工作目錄的環境資訊
送到 Anthropic 去計算，確認這些內容可以送出再執行。

環境那一塊有日期、工作目錄跟作業系統，所以換一個目錄或換一天，
量出來的數字可能會差一點點。
"""

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("請先設好 ANTHROPIC_API_KEY。")

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402

MODEL = "claude-haiku-4-5"
MESSAGES = [{"role": "user", "content": "幫我跑測試"}]


def count(client, **extra):
    return client.messages.count_tokens(
        model=MODEL, messages=MESSAGES, **extra
    ).input_tokens


def main():
    client = anthropic.Anthropic()
    rules = [{"type": "text", "text": kesi.SYSTEM_RULES}]

    bare = count(client)
    tools = count(client, tools=kesi.TOOLS)
    with_rules = count(client, tools=kesi.TOOLS, system=rules)
    full = count(client, tools=kesi.TOOLS, system=kesi.system_prompt())

    print(f"工作目錄：{kesi.BASE_DIR}")
    print(f"不帶 tools 也不帶 system：{bare:,}")
    print(f"只帶 tools：{tools:,}")
    print(f"tools 加上整份 system：{full:,}（比只帶 tools 多 {full - tools:,}）")
    print(f"  其中守則：{with_rules - tools:,}")
    print(f"  其中環境：{full - with_rules:,}")
    print(
        f"守則 {len(kesi.SYSTEM_RULES):,} 個字元，"
        f"環境 {len(kesi.environment_block()):,} 個字元"
    )


if __name__ == "__main__":
    main()
