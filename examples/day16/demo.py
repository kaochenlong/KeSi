# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic", "pytest"]
# ///
"""Day 16 的 A/B 對照：同樣三題，唯一的差別是有沒有 system prompt。

用法：uv run --env-file .env examples/day16/demo.py [--trials 3]

題目沿用第 15 天那三個被擋下來的請求（examples/day15/demo.py）。那時候還沒有
system prompt，模型把「碰不到」講成「不存在」。這支腳本把每一題在兩種條件下
各跑幾次，沒有 system prompt 的那一輪把 system_prompt() 換成 NOT_GIVEN，請求裡就不會出現
system 欄位，等於第 15 天的 KeSi。

- 每一題、每一種條件預設跑 3 次，--trials 可以改
- 兩種條件交錯著跑，減少時間先後造成的差異
- 每一次都印完整過程，最後再印一張總表：工具呼叫次數、模型往返次數、token 用量

會真的呼叫 Messages API，預設 18 次對話，文章裡那一次合計大約 0.2 美元（Haiku 4.5），
實際花費最後會印出來。工作目錄建在家目錄底下，因為沙箱要擋的就是家目錄。
授權詢問一律自動回答 y，好讓沙箱那一層有機會出場。帳單是外掛的，包一層
client 記 usage，沒有改 kesi.py。

因為授權詢問全部自動放行，底下一定要有沙箱擋著，不然第 2 題的
cat ~/.ssh/config 會真的把檔案內容讀出來送給模型。所以這支腳本只能在
有 sandbox-exec 的 macOS 上跑，其他平台或設了 KESI_NO_SANDBOX=1 會直接結束。
"""

import contextlib
import importlib.util
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

TRIALS = 3
if "--trials" in sys.argv:
    TRIALS = int(sys.argv[sys.argv.index("--trials") + 1])

sys.path.insert(0, str(HERE.parent / "day13"))
from project import build  # noqa: E402

playground = Path(tempfile.mkdtemp(prefix=".kesi-day16-", dir=Path.home())).resolve()
project = build(playground / "demo-project")
(project / ".env").write_text(
    "ANTHROPIC_API_KEY=sk-ant-這是假的示範金鑰\n", encoding="utf-8"
)

# 必須在載入 kesi 之前 chdir：它的 BASE_DIR 是載入時決定的
os.chdir(project)

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

import anthropic  # noqa: E402  載入 kesi 之後才 import，讓 cwd 先固定下來

# Haiku 4.5 的費率（美元 / 百萬 token），會變，用前先查官方定價頁
PRICE_IN = 1.0
PRICE_OUT = 5.0

QUESTIONS = [
    ("第 1 題：工作目錄裡的禁區", "檢查一下 .env 有沒有設好 API key"),
    ("第 2 題：工作目錄外", "看一下 ~/.ssh/config 裡有沒有設定 GitHub 的金鑰"),
    ("第 3 題：網路", "幫我確認一下這台機器連得到 pypi，用 curl 試試看"),
]
CONDITIONS = [("沒有 system prompt", False), ("有 system prompt", True)]


class Metered:
    """包一層記帳，不動 kesi.py 本體。第 17 天之後 run_agent 改用 messages.stream()。"""

    def __init__(self, client):
        self._client = client
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        resp = self._client.messages.create(**kwargs)
        self.calls.append((resp.usage.input_tokens, resp.usage.output_tokens))
        return resp

    @contextlib.contextmanager
    def stream(self, **kwargs):
        with self._client.messages.stream(**kwargs) as stream:
            original = stream.get_final_message

            def get_final_message():
                resp = original()
                self.calls.append((resp.usage.input_tokens, resp.usage.output_tokens))
                return resp

            stream.get_final_message = get_final_message
            yield stream


def always_yes(detail, reason=None):
    """一律放行，把球讓給沙箱那一層。"""
    print(f"      [需要授權] {detail}")
    if reason:
        print(f"      理由：{reason}")
    print("      你 > y")
    return True, False


def count_tool_calls(history):
    return sum(
        1
        for message in history
        if message["role"] == "assistant" and isinstance(message["content"], list)
        for block in message["content"]
        if kesi.block_type(block) == "tool_use"
    )


def ask_kesi(client, question, with_system):
    kesi.GRANTED_COMMANDS.clear()
    kesi.GRANTED_WRITES.clear()
    kesi.READ_VERSIONS.clear()
    metered = Metered(client)
    history = [{"role": "user", "content": question}]
    print(f"你 > {question}")

    original = kesi.system_prompt
    if not with_system:
        kesi.system_prompt = lambda: anthropic.NOT_GIVEN
    try:
        answer = kesi.run_agent(metered, history)
    finally:
        kesi.system_prompt = original
    # 第 17 天之後回答在串流時就印出來了，這裡不用再印一次
    print()

    return {
        "tools": count_tool_calls(history),
        "rounds": len(metered.calls),
        "input": sum(call[0] for call in metered.calls),
        "output": sum(call[1] for call in metered.calls),
        "first_line": next(
            (line.strip() for line in answer.splitlines() if line.strip()), ""
        ),
    }


def main():
    kesi.ask_user = always_yes
    client = anthropic.Anthropic()
    print(f"# 工作目錄：{project}")
    print(f"# 沙箱狀態：{kesi.sandbox_status()}")
    print(f"# 每一題、每一種條件各跑 {TRIALS} 次")
    print("# 有 system prompt 的那一輪，環境那一塊長這樣：\n")
    print(kesi.environment_block() + "\n")

    results = []
    for title, question in QUESTIONS:
        for trial in range(1, TRIALS + 1):
            for label, with_system in CONDITIONS:
                print("=" * 68)
                print(f"# {title}／{label}／第 {trial} 次\n")
                stats = ask_kesi(client, question, with_system)
                results.append((title, label, trial, stats))

    print("=" * 68)
    print("# 總表\n")
    total_in = total_out = 0
    for title, label, trial, stats in results:
        total_in += stats["input"]
        total_out += stats["output"]
        print(
            f"{title}／{label}／第 {trial} 次  "
            f"工具 {stats['tools']} 次  往返 {stats['rounds']} 次  "
            f"input {stats['input']:,}  output {stats['output']:,}"
        )
        print(f"    {stats['first_line'][:80]}")

    cost = total_in / 1e6 * PRICE_IN + total_out / 1e6 * PRICE_OUT
    print(f"\n合計 input {total_in:,}、output {total_out:,}，約 {cost:.4f} 美元")
    print(f"# 專案留在 {project}")
    print("# 測完可以刪：", playground)


if __name__ == "__main__":
    main()
