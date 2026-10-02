# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic", "pytest"]
# ///
"""Day 18：連續問八輪，看每一輪實際花了多少錢。

用法：uv run --env-file .env examples/day18/demo.py

會做三件事：

1. 用免費的 Token Counting API，量幾組同樣意思的中文跟英文各算幾個 token
2. 量 KeSi 每次請求都要帶的固定開銷（工具說明書加 system prompt）
3. 拿第 13 天那個訂單試算專案連問八個問題，記下每一輪的請求次數、token 跟費用

第 1、2 件事不花錢，第 3 件會真的呼叫 Messages API，跑一次大約 0.1 美元（Haiku 4.5），
實際花費最後會印出來。第一輪會照 KeSi 平常的樣子把回答印出來，後面七輪只印帳單。

授權詢問一律自動放行，所以跟第 16 天的 demo 一樣，只能在沙箱開著的 macOS 上跑。
示範專案建在系統暫存資料夾，不會動到這個 repo。
"""

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("請先設好 ANTHROPIC_API_KEY。")

# 授權詢問全部自動放行，沒有沙箱在底下擋著就不能跑
if sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
    sys.exit("這個 demo 會自動放行所有授權詢問，只能在有 sandbox-exec 的 macOS 上跑。")
if os.environ.get("KESI_NO_SANDBOX") == "1":
    sys.exit("這個 demo 會自動放行所有授權詢問，不能在關掉沙箱的情況下跑。")

sys.path.insert(0, str(HERE.parent / "day13"))
from project import build  # noqa: E402

project = build(Path(tempfile.mkdtemp(prefix="kesi-day18-")).resolve() / "demo-project")

# 必須在載入 kesi 之前 chdir：它的 BASE_DIR 是載入時決定的
os.chdir(project)

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402

MODEL = "claude-haiku-4-5"

QUESTIONS = [
    "這個專案在做什麼？",
    "rules.py 裡的折扣規則怎麼算的？",
    "測試有幾條？",
    "pricing.py 跟 rules.py 的關係是什麼？",
    "utils.py 有什麼用？",
    "現在有測試沒過嗎？",
    "如果要加一個滿三千折兩百的規則，要改哪裡？",
    "整理一下你到目前為止看到的東西。",
]

PAIRS = [
    ("Hello, how are you today?", "你好，你今天過得如何？"),
    ("Please read the file and tell me what it does.", "請讀取這個檔案並告訴我它在做什麼。"),
    ("def add(a, b):\n    return a + b", "def 相加(甲, 乙):\n    return 甲 + 乙"),
]


def count(client, text, **extra):
    return client.messages.count_tokens(
        model=MODEL, messages=[{"role": "user", "content": text}], **extra
    ).input_tokens


def measure_tokenizer(client):
    print("# 1. 同樣的意思，中文跟英文各算幾個 token\n")
    # 只送一個字元 x，扣掉 x 本身，剩下的就是每個請求都有的包裝
    baseline = count(client, "x") - 1
    print(f"  每個請求的包裝：{baseline}")
    for english, chinese in PAIRS:
        for label, text in (("英", english), ("中", chinese)):
            shown = text.replace("\n", "\\n")
            print(f"  [{label}] {shown}  →  {count(client, text) - baseline}")
        print()


def measure_fixed(client):
    print("# 2. 每次請求都要帶的固定開銷\n")
    bare = count(client, "x")
    full = count(client, "x", tools=kesi.TOOLS, system=kesi.system_prompt())
    print(f"  工具說明書加 system prompt：{full - bare:,} 個 token\n")
    return full - bare


def run_conversation(client):
    print("# 3. 連問八輪\n")
    history = []
    rows = []
    for index, question in enumerate(QUESTIONS, 1):
        history.append({"role": "user", "content": question})
        before = len(kesi.METER.calls)
        if index == 1:
            # 第一輪照 KeSi 平常的樣子印出來，看看多出來的那一行帳單
            print(f"你 > {question}")
            kesi.run_agent(client, history)
            print(f"  {kesi.METER.report(before)}\n")
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                kesi.run_agent(client, history)
        total = kesi.METER.totals(before)
        rows.append((
            index,
            len(kesi.METER.calls) - before,
            total["input"],
            total["output"],
            kesi.METER.cost(before),
            kesi.METER.cost(),
        ))

    print(" 輪  請求     input   output       本輪       累計")
    for index, requests, inp, out, cost, running in rows:
        print(
            f" {index:>2}  {requests:>4}  {inp:>8,}  {out:>7,}"
            f"  ${cost:>8.4f}  ${running:>8.4f}"
        )
    return history


def show_totals(history, fixed):
    total = kesi.METER.totals()
    calls = kesi.METER.calls
    inp = total["input"] / 1e6 * kesi.PRICE["input"]
    out = total["output"] / 1e6 * kesi.PRICE["output"]
    print("\n# 總計\n")
    print(f"  日記 {len(history)} 則，API 請求 {len(calls)} 次")
    print(f"  input {total['input']:,} token、output {total['output']:,} token")
    print(f"  費用 ${kesi.METER.cost():.4f}")
    print(f"    其中 input ${inp:.4f}（{inp / (inp + out):.0%}）、output ${out:.4f}（{out / (inp + out):.0%}）")
    print(f"  最後一次請求的 input：{calls[-1]['input']:,}")
    fixed_total = fixed * len(calls)
    print(
        f"  固定開銷 {fixed:,} × {len(calls)} 次 = {fixed_total:,}，"
        f"佔全部 input 的 {fixed_total / total['input']:.1%}"
    )


def main():
    kesi.ask_user = lambda detail, reason=None: (True, False)
    client = anthropic.Anthropic()
    measure_tokenizer(client)
    fixed = measure_fixed(client)
    history = run_conversation(client)
    show_totals(history, fixed)


if __name__ == "__main__":
    main()
