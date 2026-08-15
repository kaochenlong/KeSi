# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""印出 KeSi 這次會用的 Seatbelt profile，也可以直接拿它跑一條指令試試。

用法：uv run examples/day15/profile.py
      uv run examples/day15/profile.py --try 'cat ~/.ssh/config'

repo 裡不放靜態的 .sb 檔，因為 profile 的內容會跟著三件事變：你的家目錄在哪、
現在的工作目錄是哪個、機器上實際裝了哪幾套工具鏈。寫死一份進 repo，跟真正送給
sandbox-exec 的那份一定對不起來。

不帶參數的時候，profile 印到 stdout、說明印到 stderr，所以可以直接存檔：

    uv run examples/day15/profile.py > /tmp/kesi.sb

存下來的檔案裡路徑仍然是 (param "NAME")，這是刻意的，KeSi 走的就是 -p 加 -D，
路徑不進 Scheme 字串。要自己餵給 sandbox-exec 的話，那些 -D 得跟著一起給，所以
想試的話用 --try 比較省事，它走的是 kesi.shell_argv()，跟 run_command 送出去的
完全是同一組參數。
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

if kesi.SANDBOX_EXEC is None:
    sys.exit(f"這一版的沙箱只在 macOS 上實作，目前平台是 {sys.platform}。")

if len(sys.argv) == 3 and sys.argv[1] == "--try":
    command = sys.argv[2]
    print(f"# 工作目錄：{kesi.BASE_DIR}")
    print(f"# 指令：{command}\n")
    proc = subprocess.run(
        kesi.shell_argv(command),
        cwd=kesi.BASE_DIR,
        env=kesi.sandbox_environment(),
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    print(f"\n（結束碼 {proc.returncode}）")
    sys.exit(proc.returncode)

profile, parameters = kesi.sandbox_spec()
print(profile)

toolchains = len(parameters) - 3
print(f"# 工作目錄：{kesi.BASE_DIR}", file=sys.stderr)
print(f"# 沙箱狀態：{kesi.sandbox_status()}", file=sys.stderr)
print(f"# 參數 {len(parameters)} 個，其中 {toolchains} 個是家目錄裡的工具鏈例外", file=sys.stderr)
print("#", file=sys.stderr)
print("# 想直接試一條指令：", file=sys.stderr)
print("#   uv run examples/day15/profile.py --try 'cat ~/.ssh/config'", file=sys.stderr)
