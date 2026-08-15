# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 15 沙箱的驗收：驗的是作業系統擋不擋，不是我們的字串檢查擋不擋。

用法：uv run examples/day15/check.py

所以每一條都直接下 shell 指令，不經過 run_command 的權限判斷，
故意繞過應用層那道門，看底下那道還在不在。

工作目錄刻意建在家目錄底下，讓 profile 同時碰到「拒絕父目錄、
放行工作子目錄」兩條規則。單純把工作目錄搬到 /tmp，測不到這個交疊。
想換一個假的父目錄來重現同一組規則交疊，用 KESI_CHECK_HOME 指定。

不需要 API 金鑰，所有東西都在一個暫存目錄裡跑完就刪。全部通過會回結束碼 0，
有任何一條沒過回 1。macOS 以外的平台沒有 sandbox-exec，這支腳本會直接說明並跳過。
"""

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

if sys.platform != "darwin":
    sys.exit(f"這一版的沙箱只在 macOS 上實作，目前平台是 {sys.platform}。")
if shutil.which("sandbox-exec") is None:
    sys.exit("找不到 sandbox-exec，無法驗收。")

# 預設模擬真實專案；需要隔離驗收時，也能指定一個假的家目錄
check_home_override = os.environ.get("KESI_CHECK_HOME")
check_home = Path(check_home_override or Path.home()).resolve()
if not check_home.is_dir():
    sys.exit(f"KESI_CHECK_HOME 不存在或不是目錄：{check_home}")
os.environ["HOME"] = str(check_home)
python_command = "/usr/bin/python3" if check_home_override else "python3"
if shutil.which(python_command) is None:
    sys.exit(f"找不到驗收需要的 Python：{python_command}")

playground = Path(tempfile.mkdtemp(prefix=".kesi-day15-", dir=check_home)).resolve()
workdir = playground / 'work"quoted'
outside = playground / "outside"
workdir.mkdir()
outside.mkdir()
(workdir / "config").mkdir()
(workdir / "config" / ".git").mkdir()
(workdir / "note.txt").write_text("工作目錄內的檔案\n", encoding="utf-8")
(workdir / "config" / ".env").write_text(
    "ANTHROPIC_API_KEY=sk-ant-假金鑰\n",
    encoding="utf-8",
)
(workdir / ".env.local").write_text(
    "ANTHROPIC_API_KEY=sk-ant-另一把假金鑰\n",
    encoding="utf-8",
)
(workdir / "config" / ".git" / "config").write_text(
    "[remote \"origin\"]\nurl = git@example.invalid:secret/repo.git\n",
    encoding="utf-8",
)
(outside / "secret.txt").write_text("隔壁專案的秘密\n", encoding="utf-8")
(workdir / "linked-secret.txt").symlink_to(outside / "secret.txt")
os.link(outside / "secret.txt", workdir / "hardlinked-secret.txt")

os.chdir(workdir)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-FAKE"
os.environ["GITHUB_TOKEN"] = "ghp-FAKE"
os.environ["KESI_VISIBLE_TEST"] = "visible"

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))


def known_limitation(name, observed, detail=""):
    results.append((name, observed))
    label = "KNOWN" if observed else "CHANGED"
    print(f"{label} {name}" + (f"  {detail}" if detail else ""))


def sh(command, sandboxed=True):
    """直接跑指令，繞過權限判斷，只看沙箱擋不擋。"""
    argv = kesi.shell_argv(command) if sandboxed else [kesi.SHELL_PATH, "-c", command]
    proc = subprocess.run(
        argv, cwd=workdir, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


print(f"# 工作目錄：{workdir}")
print(f"# 沙箱狀態：{kesi.sandbox_status()}\n")

print("# 1. 工作目錄內照常運作")
code, out = sh("cat note.txt")
check(
    "含引號的工作目錄仍讀得到檔案",
    code == 0 and "工作目錄內" in out,
    repr(out),
)
code, out = sh("echo 新內容 > new.txt && cat new.txt")
check("寫得進工作目錄", code == 0 and "新內容" in out, repr(out))
code, out = sh(f"{python_command} -c 'print(1+1)'")
check("工具鏈還跑得動", code == 0 and out == "2", repr(out))

print("\n# 2. 專案外面碰不到")
code, out = sh(f"cat {outside / 'secret.txt'}")
check("讀不到隔壁專案", code != 0 and "秘密" not in out, repr(out[:60]))
code, out = sh(f"echo x > {outside / 'hacked.txt'}")
check(
    "寫不進專案外",
    code != 0 and not (outside / "hacked.txt").exists(),
    repr(out[:60]),
)
code, out = sh("cat linked-secret.txt")
check("符號連結不能繞出去", code != 0 and "秘密" not in out, repr(out[:60]))

print("\n# 3. 工作目錄裡的禁區，連 shell 也讀不到")
code, out = sh("cat config/.env")
check(
    "shell 讀不到巢狀 .env",
    "假金鑰" not in out,
    repr(out[:60]),
)
code, out = sh("cat .env.local")
check(
    "shell 讀不到 .env.*",
    "另一把假金鑰" not in out,
    repr(out[:60]),
)
code, out = sh("cat config/.git/config")
check(
    "shell 讀不到巢狀的一般禁區",
    "secret/repo" not in out,
    repr(out[:60]),
)

print("\n# 4. 常見憑證不會交給子行程")
out, is_error = kesi.run_command(
    'test -z "$ANTHROPIC_API_KEY" '
    '&& test -z "$GITHUB_TOKEN" '
    '&& test "$KESI_VISIBLE_TEST" = visible'
)
check("移除常見憑證並保留一般環境變數", not is_error, repr(out[:80]))

print("\n# 5. 網路斷了")
listener = socket.socket()
try:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    code, out = sh(
        f"{python_command} -c 'import socket; "
        f'socket.create_connection(("127.0.0.1", {port}), 2)\''
    )
finally:
    listener.close()
check("連不到本機測試 socket", code != 0, repr(out[:60]))

print("\n# 6. 對照組：同一條外部讀取，關掉沙箱就會成功")
code, out = sh(f"cat {outside / 'secret.txt'}", sandboxed=False)
check(
    "關掉沙箱就讀得到隔壁專案",
    code == 0 and "秘密" in out,
    "這正是第 11 天留下的洞",
)

print("\n# 7. 應用層那道門還在")
blocked, is_error = kesi.read_file(str(outside / "secret.txt"))
check("read_file 仍然擋工作目錄外", is_error, repr(blocked[:40]))
verdict, reason = kesi.classify_command("cat .env")
check("cat .env 仍然要問", verdict == "ask", f"判成 {verdict}")

print("\n# 8. 在家目錄啟動時，工作目錄邊界會失效")
real_base = kesi.BASE_DIR
try:
    kesi.BASE_DIR = kesi.HOME_DIR
    useless = kesi.sandbox_would_be_useless()
    status = kesi.sandbox_status()
finally:
    kesi.BASE_DIR = real_base
check(
    "工作目錄是家目錄時會明講",
    useless and "工作目錄邊界形同虛設" in status,
    status,
)
check("正常目錄不會誤報", not kesi.sandbox_would_be_useless(), kesi.sandbox_status())

print("\n# 9. 已知限制：既有 hard link 仍可穿過路徑邊界")
read_code, read_out = sh("cat hardlinked-secret.txt")
write_code, _ = sh("printf '從工作目錄改寫\\n' > hardlinked-secret.txt")
outside_content = (outside / "secret.txt").read_text(encoding="utf-8")
known_limitation(
    "hard link 可讀取並改寫工作目錄外的同一個 inode",
    read_code == 0
    and "隔壁專案的秘密" in read_out
    and write_code == 0
    and outside_content == "從工作目錄改寫\n",
    "Seatbelt 的這份規則是路徑邊界，不是檔案來源追蹤",
)

shutil.rmtree(playground, ignore_errors=True)
failed = [name for name, ok in results if not ok]
print(f"\n# {len(results) - len(failed)}/{len(results)} 通過")
if failed:
    print("# 沒過：" + "、".join(failed))
sys.exit(1 if failed else 0)
