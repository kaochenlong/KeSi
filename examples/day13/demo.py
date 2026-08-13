# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic", "pytest"]
# ///
"""Day 13 實戰：把一個有 failing test 的專案丟給 KeSi，全程記錄。

用法：ANTHROPIC_API_KEY=... uv run examples/day13/demo.py

會印出四段：起點的測試狀態、agent 的完整過程、終點的測試狀態、磁碟上的 diff，
最後是一張自己攔下來的帳單（每一圈的 token 與秒數）。

驗收要看的是 diff，不是只看 pytest 最後一行。測試變綠有很多種作弊方式，
把測試改掉也算一種，所以每次跑完都要看它到底動了什麼。

加上 --no-shell 會把 run_command 從工具清單拿掉，用來對照「沒辦法跑測試的
agent 會怎麼收尾」：

    ANTHROPIC_API_KEY=... uv run examples/day13/demo.py --no-shell

會真的呼叫 API（用 claude-haiku-4-5，一次大約 0.04 美元）。模型每次挑的路線
不見得一樣，跟文章裡的節錄不同是正常的。示範專案建在暫存目錄，agent 下的指令
也在那裡執行，不會動到這個 repo。帳單是外掛的，包一層 client 記 usage，
沒有改 kesi.py，正式的計價功能第 18 天才裝進本體。
"""

import difflib
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("請先設好 ANTHROPIC_API_KEY。")

sys.path.insert(0, str(HERE))
from project import FILES, build  # noqa: E402

NO_SHELL = "--no-shell" in sys.argv
workdir = Path(tempfile.mkdtemp(prefix="kesi-day13-")).resolve()
project = build(workdir / "demo-project")

# 必須在載入 kesi 之前 chdir：它的 BASE_DIR 是載入時決定的
os.chdir(project)

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402  載入 kesi 之後才 import，讓 cwd 先固定下來

# Haiku 4.5 的費率（2026 年 8 月，美元 / 百萬 token），會變，用前先查官方定價頁
PRICE_IN = 1.0
PRICE_OUT = 5.0

QUESTION = "跑一下測試，有一條過不了，幫我找出原因並修好"


class Metered:
    """包一層記帳，不動 kesi.py 本體。run_agent 只用到 client.messages.create。"""

    def __init__(self, client):
        self._client = client
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        started = time.monotonic()
        resp = self._client.messages.create(**kwargs)
        self.calls.append({
            "input": resp.usage.input_tokens,
            "output": resp.usage.output_tokens,
            "elapsed": time.monotonic() - started,
            "stop": resp.stop_reason,
        })
        return resp

    @property
    def cost(self):
        return sum(
            call["input"] / 1e6 * PRICE_IN + call["output"] / 1e6 * PRICE_OUT
            for call in self.calls
        )


def pytest_summary():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        capture_output=True,
        text=True,
        cwd=project,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, lines[-1] if lines else "(沒有輸出)"


def show_diff():
    changed = False
    for name, original in FILES.items():
        now = (project / name).read_text(encoding="utf-8")
        if now == original:
            continue
        changed = True
        print("\n".join(difflib.unified_diff(
            original.splitlines(), now.splitlines(),
            fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="",
        )))
    # .pytest_cache 跟 __pycache__ 是我們自己跑 pytest 留下的，不是 agent 新增的
    noise = {".pytest_cache", "__pycache__"}
    extra = sorted(
        p.name for p in project.iterdir()
        if p.name not in FILES and p.name not in noise
    )
    if extra:
        print(f"（多出來的檔案：{extra}）")
    if not changed:
        print("（沒有任何原始檔案被改動）")


def show_bill(client, elapsed, history):
    total_in = sum(call["input"] for call in client.calls)
    total_out = sum(call["output"] for call in client.calls)
    print(f"模型往返 {len(client.calls)} 次，牆上時鐘 {elapsed:.1f} 秒")
    print(f"input {total_in:,} token、output {total_out:,} token")
    print(f"約 {client.cost:.4f} 美元")
    print("\n每一圈：")
    for index, call in enumerate(client.calls, 1):
        print(
            f"  第 {index:>2} 圈  input {call['input']:>6,}  "
            f"output {call['output']:>4,}  {call['elapsed']:>4.1f} 秒  "
            f"stop={call['stop']}"
        )
    print(f"\n日記長度 {len(history)} 則")


def main():
    if NO_SHELL:
        kesi.TOOLS = [t for t in kesi.TOOLS if t["name"] != "run_command"]
        kesi.TOOL_FUNCS.pop("run_command", None)
        print("（這次把 run_command 拿掉了，它沒辦法跑測試）\n")

    print(f"# 工作目錄 {project}\n")
    code, tail = pytest_summary()
    print(f"## 起點\npytest 結束碼 {code}：{tail}\n")

    client = Metered(anthropic.Anthropic())
    history = [{"role": "user", "content": QUESTION}]
    print(f"## 過程\n你 > {QUESTION}")

    started = time.monotonic()
    answer = kesi.run_agent(client, history)
    elapsed = time.monotonic() - started
    print(f"KeSi > {answer}\n")

    code, tail = pytest_summary()
    print(f"## 終點\npytest 結束碼 {code}：{tail}\n")

    print("## 磁碟上的變化")
    show_diff()

    print("\n## 帳單")
    show_bill(client, elapsed, history)
    print(f"\n# 專案留在 {project}")


if __name__ == "__main__":
    main()
