# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic", "pytest"]
# ///
"""Day 11 換模型上場的三題。

用法：ANTHROPIC_API_KEY=... uv run examples/day11/demo.py

會真的呼叫 API（用 claude-haiku-4-5，三題的花費很小）。模型每次挑的路線
不見得一樣，跟文章裡的節錄不同是正常的。測試專案建在暫存目錄裡，跑完就丟。
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

KESI = Path(__file__).resolve().parent.parent.parent / "kesi.py"

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("請先設好 ANTHROPIC_API_KEY。")

# 有 bug 的 add、正確的 mul，外加一個跟這題完全無關的 helper.py
work = Path(tempfile.mkdtemp(prefix="kesi-day11-demo-"))
(work / "calc.py").write_text(
    "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
)
(work / "test_calc.py").write_text(
    "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
)
(work / "helper.py").write_text(
    'def slugify(text):\n    return text.strip().lower().replace(" ", "-")\n'
)
os.chdir(work)

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402  載入 kesi 之後才 import，讓 cwd 先固定下來

client = anthropic.Anthropic()
history = []


def ask(question):
    print(f"\n你 > {question}")
    history.append({"role": "user", "content": question})
    print("KeSi >", kesi.run_agent(client, history))


print(f"測試專案：{work}")

# 第一題：它會先讀檔還是先跑測試？
ask("這個測試過不了，你看一下")
print("\n--- 磁碟上的 calc.py ---")
print((work / "calc.py").read_text())

# 第二題：數 Python 檔，它會選哪個工具？
ask("看一下這個目錄有幾個 Python 檔")

# 第三題：說明書寫死 120 秒，但實際行為改成 5 秒，看模型怎麼解釋
print("\n--- 把上限暫時改成 5 秒（說明書裡的 120 是模組載入時就燒進去的）---")
kesi.COMMAND_TIMEOUT = 5
ask("幫我跑 sleep 300 這個指令")
