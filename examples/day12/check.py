# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 12 安全網的驗收；API 錯誤走 examples/day12/mock.py，不呼叫真的 API、不花錢。

用法：uv run examples/day12/check.py

驗的是六件事：
- 圈數上限（MAX_TURNS）攔不攔得住無限開單
- 連續失敗（MAX_TOOL_FAILURES）會不會比圈數上限更早停
- SDK 的重試行為，以及重試用盡之後 KeSi 有沒有好好收場
- 兩種截斷 stop reason 有沒有分開回報
- 中斷之後，日記裡還有沒有懸空的許願單
- run_command 中斷之後，子行程群組有沒有真的停止

不需要 API 金鑰，也不會碰到你的專案，所有東西都在一個暫存目錄裡跑完就丟。
mock server 由腳本自己叫起來，用 MOCK_PORT 可以換埠號（預設 8801）。
全部通過會回結束碼 0，有任何一條沒過回 1，方便接在別的檢查後面跑。
"""

import http.client
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"
PORT = int(os.environ.get("MOCK_PORT", "8801"))

# 這幾行必須在載入 kesi 之前：kesi 的 BASE_DIR 是在載入時決定的，
# 而且我們要讓它打本機的 mock server，不是真的 API。
workdir = Path(tempfile.mkdtemp(prefix="kesi-day12-")).resolve()
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
    """把 examples/day12/mock.py 叫起來，離開時收掉並取回它印的請求紀錄。"""

    def __init__(self, mode, **env):
        self.env = {"MOCK_MODE": mode, "MOCK_PORT": str(PORT), **env}
        self.log = ""

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(HERE / "mock.py")],
            env={**os.environ, **self.env},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # 用 GET 探活，mock 只在 do_POST 計數，所以不會污染請求數
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
        self.log = self.proc.stdout.read()
        return False

    @property
    def requests(self):
        return self.log.count("次請求")


def check_max_turns():
    print("# 1. 圈數上限")
    with Mock("loop") as mock:
        client = anthropic.Anthropic(max_retries=0)
        history = [{"role": "user", "content": "一直逛目錄"}]
        started = time.monotonic()
        answer = kesi.run_agent(client, history)
        elapsed = time.monotonic() - started
    check(
        "無限開單會被 MAX_TURNS 攔住",
        f"{kesi.MAX_TURNS} 次模型往返" in answer and mock.requests == kesi.MAX_TURNS,
        f"{elapsed:.1f} 秒、mock 收到 {mock.requests} 次請求",
    )
    check(
        "用滿上限後日記結尾是 assistant",
        history[-1]["role"] == "assistant" and "先停下來" in history[-1]["content"],
        f"日記 {len(history)} 則",
    )


def check_tool_failures():
    print("\n# 2. 工具連續失敗")
    with Mock("fail") as mock:
        client = anthropic.Anthropic(max_retries=0)
        answer = kesi.run_agent(
            client, [{"role": "user", "content": "讀一個不存在的檔案"}]
        )
    check(
        "連續失敗會提早停",
        f"連續 {kesi.MAX_TOOL_FAILURES} 輪全部失敗" in answer
        and mock.requests == kesi.MAX_TOOL_FAILURES,
        f"mock 收到 {mock.requests} 次請求，比上限 {kesi.MAX_TURNS} 圈早停",
    )


def check_retry():
    print("\n# 3. SDK 的重試（429）")
    with Mock("429") as mock:
        client = anthropic.Anthropic()  # 用預設 max_retries
        started = time.monotonic()
        kesi.run_agent(client, [{"role": "user", "content": "hi"}])
        elapsed = time.monotonic() - started
    check(
        "429 會被 SDK 自動重試",
        mock.requests == 3,
        f"共送出 {mock.requests} 次請求，耗時 {elapsed:.1f} 秒",
    )
    print("   mock 端看到的節奏：")
    for line in mock.log.splitlines()[1:]:
        print(f"     {line}")

    print("\n# 4. 重試用盡不會炸掉 agent")
    with Mock("429"):
        client = anthropic.Anthropic()
        history = [{"role": "user", "content": "hi"}]
        answer = kesi.run_agent(client, history)
    check(
        "重試用盡回錯誤訊息而不是 traceback",
        answer.startswith("錯誤：重試後仍被限流"),
        repr(answer),
    )
    check(
        "API 失敗不會在日記留下半截訊息",
        len(history) == 1 and history[0]["role"] == "user",
        f"日記 {len(history)} 則",
    )

    print("\n# 5. retry-after 會被遵守")
    with Mock("429-after", MOCK_RETRY_AFTER="2"):
        client = anthropic.Anthropic(max_retries=1)
        started = time.monotonic()
        kesi.run_agent(client, [{"role": "user", "content": "hi"}])
        elapsed = time.monotonic() - started
    check(
        "retry-after 指定的秒數會被照做",
        1.9 <= elapsed <= 3.5,
        f"重試間隔 {elapsed:.1f} 秒（伺服器要求 2 秒）",
    )


def check_error_kinds():
    print("\n# 6. 529、400 與 x-should-retry 的差別")
    with Mock("529") as mock:
        client = anthropic.Anthropic(max_retries=1)
        answer = kesi.run_agent(client, [{"role": "user", "content": "hi"}])
    check(
        "529 會重試，訊息說得清楚",
        answer.startswith("錯誤：伺服器忙碌中") and mock.requests == 2,
        f"{mock.requests} 次請求；{answer[:24]}...",
    )

    with Mock("400") as mock:
        client = anthropic.Anthropic(max_retries=2)
        answer = kesi.run_agent(client, [{"role": "user", "content": "hi"}])
    check(
        "400 不重試，直接回報",
        mock.requests == 1 and "請求被拒絕（400）" in answer,
        f"{mock.requests} 次請求；提示了 /reset：{'/reset' in answer}",
    )

    with Mock("400-retry") as mock:
        client = anthropic.Anthropic(max_retries=1)
        answer = kesi.run_agent(client, [{"role": "user", "content": "hi"}])
    check(
        "400 有 x-should-retry: true 仍會重試",
        mock.requests == 2 and "請求被拒絕（400）" in answer,
        f"{mock.requests} 次請求",
    )

    print("\n# 7. 暫時性錯誤會自己好起來")
    with Mock("500-then-ok", MOCK_FAILURES="2") as mock:
        client = anthropic.Anthropic()
        answer = kesi.run_agent(client, [{"role": "user", "content": "hi"}])
    check(
        "500 重試兩次後成功，使用者無感",
        mock.requests == 3 and "好了" in answer,
        f"{mock.requests} 次請求後拿到正常回應",
    )


def check_stop_reasons():
    print("\n# 8. 截斷原因要分開回報")

    class FixedMessages:
        def __init__(self, stop_reason):
            self.stop_reason = stop_reason

        def create(self, **kwargs):
            return SimpleNamespace(stop_reason=self.stop_reason, content=[])

    max_history = [{"role": "user", "content": "hi"}]
    max_answer = kesi.run_agent(
        SimpleNamespace(messages=FixedMessages("max_tokens")),
        max_history,
    )
    check(
        "max_tokens 會回報輸出上限",
        "輸出達到 max_tokens" in max_answer,
        repr(max_answer),
    )

    context_history = [{"role": "user", "content": "hi"}]
    context_answer = kesi.run_agent(
        SimpleNamespace(messages=FixedMessages("model_context_window_exceeded")),
        context_history,
    )
    check(
        "context window 會回報 context 上限",
        "回應填滿 context window" in context_answer,
        repr(context_answer),
    )


def dangling_tool_use(history):
    """找出日記裡沒有結果緊跟在後面的許願單。"""
    pending = []
    for index, item in enumerate(history):
        if item["role"] != "assistant" or not isinstance(item["content"], list):
            continue
        for blk in item["content"]:
            if kesi.block_type(blk) != "tool_use":
                continue
            nxt = history[index + 1] if index + 1 < len(history) else None
            answered = (
                nxt
                and nxt["role"] == "user"
                and any(
                    r.get("tool_use_id") == kesi.block_id(blk)
                    for r in nxt["content"]
                )
            )
            if not answered:
                pending.append(kesi.block_id(blk))
    return pending


def check_interrupt():
    print("\n# 9. 中斷之後日記還送得出去")

    def interrupting(*args, **kwargs):
        raise KeyboardInterrupt

    original = kesi.TOOL_FUNCS["list_files"]
    kesi.TOOL_FUNCS["list_files"] = interrupting
    try:
        with Mock("loop"):
            client = anthropic.Anthropic(max_retries=0)
            history = [{"role": "user", "content": "逛目錄"}]
            answer = kesi.run_agent(client, history)
    finally:
        kesi.TOOL_FUNCS["list_files"] = original

    pending = dangling_tool_use(history)
    check(
        "工具被中斷後每張許願單都有結果",
        not pending and "中斷" in answer,
        f"懸空的許願單：{pending or '無'}",
    )

    # 模擬 assistant 許願單已寫入、tool_result 還沒寫回去時按 Ctrl-C。
    history = [
        {"role": "user", "content": "逛目錄"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_x",
                    "name": "list_files",
                    "input": {},
                }
            ],
        },
    ]
    sealed = kesi.seal_dangling_tool_use(history)
    check(
        "懸空的許願單會被補上結果",
        sealed
        and history[-1]["role"] == "user"
        and history[-1]["content"][0]["tool_use_id"] == "toolu_x"
        and "執行狀態不明" in history[-1]["content"][0]["content"],
        f"補完後日記 {len(history)} 則",
    )
    # 反面測試：正常的日記不能被亂補
    check(
        "沒有懸空時不會亂補",
        kesi.seal_dangling_tool_use([{"role": "user", "content": "hi"}]) is False,
    )


def check_command_interrupt():
    print("\n# 10. run_command 中斷後會清掉子行程群組")

    original_popen = kesi.subprocess.Popen
    captured = {}

    def capture_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        captured["proc"] = proc
        return proc

    kesi.subprocess.Popen = capture_popen
    timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    interrupted = False
    stopped = False
    proc = None
    try:
        try:
            kesi.run_command("sleep 30")
        except KeyboardInterrupt:
            interrupted = True
        proc = captured.get("proc")
        stopped = proc is not None and proc.poll() is not None
    finally:
        timer.cancel()
        kesi.subprocess.Popen = original_popen
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)

    check(
        "run_command 被中斷後子行程群組會停止",
        interrupted and stopped,
        f"收到 KeyboardInterrupt：{interrupted}；子行程已停止：{stopped}",
    )


def main():
    (workdir / "note.txt").write_text("hello\n", encoding="utf-8")
    print(f"# 工作目錄：{workdir}\n")

    check_max_turns()
    check_tool_failures()
    check_retry()
    check_error_kinds()
    check_stop_reasons()
    check_interrupt()
    check_command_interrupt()

    failed = [name for name, ok in results if not ok]
    print(f"\n# {len(results) - len(failed)}/{len(results)} 通過")
    if failed:
        print("# 沒過：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
