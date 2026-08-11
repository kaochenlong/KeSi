# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 11 run_command 的工具層驗收。

用法：uv run examples/day11/check.py

不需要 API 金鑰，也不會碰到你的專案，所有東西都在一個暫存目錄裡跑完就丟。
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KESI = Path(__file__).resolve().parent.parent.parent / "kesi.py"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))


# kesi.py 的 BASE_DIR 是在 import 的時候用 cwd 決定的，所以要先換過去再載入
workdir = Path(tempfile.mkdtemp(prefix="kesi-day11-"))
os.chdir(workdir)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-這是假的測試金鑰"

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)
run_command = kesi.run_command
print(f"工作目錄 {kesi.BASE_DIR}\n")

out, err = run_command("echo hello")
check("echo 與結束碼 0", out == "hello\n（結束碼 0）" and err is False, repr(out))

out, err = run_command("exit 3")
check("非 0 結束碼", "（結束碼 3）" in out and err is True, repr(out))

out, _ = run_command("echo 一; echo 二 >&2; echo 三")
check("stdout 與 stderr 依同一根 pipe 的實收順序合併", out.startswith("一\n二\n三\n"), repr(out))

out, _ = run_command("true")
check("沒有輸出也講清楚", out == "（指令沒有輸出）（結束碼 0）", repr(out))

out, _ = run_command("printf '   '")
check("前後空白與純空白都不被吃掉", out.startswith("   ") and "沒有輸出" not in out, repr(out))

out, err = run_command("   ")
check("空指令被拒絕", err is True and "不可為空" in out, repr(out))

out, _ = run_command("pwd")
check("cwd 固定在工作目錄", out.startswith(str(kesi.BASE_DIR)), repr(out))

run_command("mkdir -p sub && cd sub")
out, _ = run_command("pwd")
check("cd 不會留到下一次", out.startswith(str(kesi.BASE_DIR) + "\n"), repr(out))

out, _ = run_command("echo [$ANTHROPIC_API_KEY]")
check("子行程看不到 API 金鑰", out.startswith("[]"), repr(out))

# 逾時：暫時把上限調成 2 秒，指令會把一組工作丟到背景再自己卡住
kesi.COMMAND_TIMEOUT = 2
started = time.monotonic()
out, err = run_command("(sleep 20; echo alive > orphan.txt) & echo 背景丟出去了; sleep 30")
elapsed = time.monotonic() - started
check("逾時會被終止", err is True and "已終止" in out and elapsed < 12, f"{elapsed:.1f}s {out!r}")

time.sleep(3)
left = subprocess.run(["pgrep", "-f", "sleep 20"], capture_output=True, text=True)
check("sh 生出來的背景行程也被收掉", left.returncode != 0, f"pgrep={left.stdout.strip()!r}")
check("逾時後沒有留下 orphan.txt", not (workdir / "orphan.txt").exists())

kesi.COMMAND_TIMEOUT = 120
out, err = run_command("sleep 25 & echo 背景啟動")
check("正常結束的回報", "背景啟動" in out and "（結束碼 0）" in out, repr(out))
time.sleep(1)
left = subprocess.run(["pgrep", "-f", "sleep 25"], capture_output=True, text=True)
check("正常結束也不留背景行程", left.returncode != 0, f"pgrep={left.stdout.strip()!r}")

# 無限輸出
started = time.monotonic()
out, err = run_command("yes kesi")
elapsed = time.monotonic() - started
check(
    f"無限輸出最多保留 {kesi.MAX_CAPTURE_BYTES} bytes 並終止",
    err is True and "輸出超過" in out and elapsed < 10,
    f"{elapsed:.1f}s tail={out[-80:]!r}",
)
check("截斷後頭尾都留得住", "（中間省略" in out and out.startswith("kesi"), repr(out[:40]))

# 前十天的柵欄對 shell 全部無效，這兩條是刻意寫下來的失敗
(workdir / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-這是假的測試金鑰\n")
blocked, blocked_err = kesi.read_file(".env")
shelled, _ = run_command("cat .env")
check(
    "read_file 擋得住，run_command 擋不住",
    blocked_err is True and "sk-ant-" in shelled,
    f"{blocked!r} / {shelled!r}",
)

outside = Path(tempfile.mkdtemp(prefix="kesi-outside-")) / "secret.txt"
outside.write_text("工作目錄外的內容\n")
blocked, blocked_err = kesi.read_file(str(outside))
shelled, _ = run_command(f"cat {outside}")
check(
    "工作目錄柵欄對 shell 無效",
    blocked_err is True and "工作目錄外的內容" in shelled,
    f"{blocked!r} / {shelled!r}",
)

failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 通過")
if failed:
    print("失敗：" + "、".join(failed))
sys.exit(1 if failed else 0)
