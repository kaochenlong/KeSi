# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///
"""Day 14 權限關卡的驗收，不呼叫模型 API。

用法：uv run examples/day14/check.py

驗四件事：
- 分級對不對：唯讀放行、危險攔下、看不懂的要問
- 紅線指令不給 yes 的機會
- session 內的同意記得住，但記住的範圍不會跟著放大
- 被擋下來的操作仍然會回一則 tool_result 給模型（第 12 天那條規矩）

不需要 API 金鑰，也不會碰到你的專案，所有東西都在一個暫存目錄裡跑完就丟。
全部通過會回結束碼 0，有任何一條沒過回 1，方便接在別的檢查後面跑。
"""

import importlib.util
import os
import shlex
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
KESI = HERE.parent.parent / "kesi.py"

# 這幾行必須在載入 kesi 之前：kesi 的 BASE_DIR 是在載入時決定的
workdir = Path(tempfile.mkdtemp(prefix="kesi-day14-")).resolve()
os.chdir(workdir)
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-FAKE")

spec = importlib.util.spec_from_file_location("kesi", KESI)
kesi = importlib.util.module_from_spec(spec)
sys.modules["kesi"] = kesi
spec.loader.exec_module(kesi)

REAL_ASK_USER = kesi.ask_user   # 第 6 題要用真的那支，先留一份

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))


def answers(*replies):
    """把使用者的回答排隊，取代互動輸入。

    要自己把回答轉成 ask_user 的合約 (是否放行, 要不要記住)。
    如果讓假的 ask_user 直接回傳 "n" 這種字串，呼叫端的 truthiness 判斷
    會把非空字串當成同意，測試就會假通過。
    """
    queue = [
        (reply.lower() in ("y", "yes", "a", "all"), reply.lower() in ("a", "all"))
        for reply in replies
    ]
    kesi.ask_user = lambda detail, reason=None: (
        queue.pop(0) if queue else (False, False)
    )


def reset_grants():
    kesi.GRANTED_COMMANDS.clear()
    kesi.GRANTED_WRITES.clear()


def block(name, tool_input, block_id="toolu_x"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


print("# 1. 指令分級")
cases = [
    ("ls", "allow"),
    ("ls -la", "allow"),
    ("git status", "allow"),
    ("cat README.md", "allow"),
    ("ls; pwd", "allow"),
    ("pytest -q", "ask"),
    ("git push", "ask"),
    ("rm -rf build", "ask"),
    ("ls *.py", "ask"),
    ("cat $(ls)", "ask"),
    ("echo hi > file.txt", "ask"),
    ("sudo ls", "deny"),
    ("rm -rf /", "deny"),
    ("rm -rf ~", "deny"),
    ("ls; sudo rm -rf /", "deny"),
    ("rm -rf $HOME", "deny"),
]
wrong = []
for command, expected in cases:
    verdict, reason = kesi.classify_command(command)
    if verdict != expected:
        wrong.append(f"{command!r} 判成 {verdict}，預期 {expected}")
check("每一條指令都分到正確的級別", not wrong, "；".join(wrong) or f"{len(cases)} 條全對")

print("\n# 1b. 唯讀白名單不能偷寫檔，也不能沿 symlink 跳出去")
verdict, _ = kesi.classify_command("git diff --output=report.txt")
check(
    "git diff --output 不會當成唯讀指令放行",
    verdict == "ask",
    f"判成 {verdict}",
)

outside_file = workdir.parent / f"{workdir.name}-outside.txt"
outside_file.write_text("secret\n", encoding="utf-8")
(workdir / "outside-link").symlink_to(outside_file)
verdict, _ = kesi.classify_command("cat outside-link")
check(
    "唯讀指令沿 symlink 跳出工作目錄時要問",
    verdict == "ask",
    f"判成 {verdict}",
)

print("\n# 2. 紅線不給 yes 的機會")
reset_grants()
answers("y", "y", "y")  # 就算使用者一路點頭
allowed, refusal = kesi.check_permission("run_command", {"command": "rm -rf /"})
check(
    "rm -rf / 直接拒絕，不會問",
    allowed is False and "安全規則" in refusal,
    repr(refusal),
)
allowed, refusal = kesi.check_permission("run_command", {"command": "sudo apt install x"})
check("sudo 直接拒絕", allowed is False and "提權" in refusal, repr(refusal))

dangerous_commands = [
    f"rm -rf {shlex.quote(str(Path.home()))}",
    "ls\nrm -rf /",
    "env rm -rf /",
    "sh -c 'rm -rf /'",
]
wrong = [
    f"{command!r} 判成 {kesi.classify_command(command)[0]}"
    for command in dangerous_commands
    if kesi.classify_command(command)[0] != "deny"
]
check(
    "家目錄實際路徑、換行與常見 wrapper 都繞不過紅線",
    not wrong,
    "；".join(wrong) or f"{len(dangerous_commands)} 條全擋下",
)

print("\n# 3. 唯讀工具不問，唯讀指令不問")
reset_grants()
answers()  # 一個回答都沒排，被問到就等於拒絕
for name, tool_input in [
    ("read_file", {"file_path": "a.txt"}),
    ("list_files", {}),
    ("glob", {"pattern": "*.py"}),
    ("grep", {"pattern": "x"}),
    ("run_command", {"command": "git log --oneline"}),
]:
    allowed, _ = kesi.check_permission(name, tool_input)
    if not allowed:
        check(f"{name} 應該不必問", False)
        break
else:
    check("唯讀工具與唯讀指令都自動放行", True, "沒有觸發任何一次詢問")

print("\n# 4. session 內記得住同意")
reset_grants()
answers("a")  # 只準備一個「這個 session 都好」
allowed, _ = kesi.check_permission("run_command", {"command": "pytest -v"})
first = allowed
allowed_again, _ = kesi.check_permission("run_command", {"command": "pytest -v"})
allowed_other, _ = kesi.check_permission("run_command", {"command": "pytest -q tests/"})
check(
    "同一條完整指令不再問",
    first and allowed_again,
    "第二次沒有用掉任何回答",
)
check(
    "參數不同就重新問",
    allowed_other is False,
    "pytest -v 的授權不會放大成所有 pytest",
)

allowed, refusal = kesi.check_permission(
    "run_command", {"command": "pytest && rm -rf build"}
)
check(
    "組合了沒同意過的指令仍然要問",
    allowed is False,
    "pytest 放行不等於 rm 放行",
)

reset_grants()
answers("a")
kesi.check_permission(
    "run_command", {"command": "cd build && rm -rf ."}
)
reordered, _ = kesi.check_permission(
    "run_command", {"command": "rm -rf . && cd build"}
)
check(
    "複合指令換順序後要重新問",
    reordered is False,
    "同一批子指令換順序，效果可能不同",
)

# 記完整指令，不把 python 的其中一種用法放大成任意程式碼
reset_grants()
answers("a")
kesi.check_permission("run_command", {"command": "python -m pytest -v"})
same, _ = kesi.check_permission("run_command", {"command": "python -m pytest -v"})
variant, _ = kesi.check_permission("run_command", {"command": "python -m pytest -q"})
script, _ = kesi.check_permission("run_command", {"command": "python cleanup.py"})
server, _ = kesi.check_permission("run_command", {"command": "python -m http.server"})
check(
    "指令授權記住完整參數",
    same and not variant and not script and not server,
    f"記住的是 {sorted(kesi.GRANTED_COMMANDS)}",
)

reset_grants()
answers("a")
kesi.check_permission("run_command", {"command": "git push origin main"})
git_clean, _ = kesi.check_permission("run_command", {"command": "git clean -fdx"})
reset_grants()
answers("a")
kesi.check_permission("run_command", {"command": "rm -rf build"})
rm_other, _ = kesi.check_permission("run_command", {"command": "rm -rf important"})
check(
    "git 與 rm 的授權不會放大成整個指令家族",
    not git_clean and not rm_other,
    "git push 不等於 git clean；rm build 不等於 rm important",
)

print("\n# 4b. 唯讀指令也不能繞過禁區名單")
reset_grants()
answers()
for command in ["cat .env", "cat .env.local", "head -5 .git/config", "ls .venv"]:
    verdict, _ = kesi.classify_command(command)
    if verdict != "ask":
        check(f"{command} 應該要問", False, f"判成 {verdict}")
        break
else:
    check("cat .env 這類指令不會自動放行", True, "四條都降級成要問")
verdict, _ = kesi.classify_command("cat README.md")
check("一般檔案仍然自動放行", verdict == "allow", f"判成 {verdict}")

print("\n# 5. 檔案授權是一個檔案一個檔案給的")
reset_grants()
(workdir / "rules.py").write_text("x = 1\n", encoding="utf-8")
(workdir / "test_rules.py").write_text("assert True\n", encoding="utf-8")
answers("a")
allowed, _ = kesi.check_permission("edit_file", {"file_path": "rules.py"})
first = allowed
allowed_again, _ = kesi.check_permission("write_file", {"file_path": "rules.py"})
allowed_other, _ = kesi.check_permission("edit_file", {"file_path": "test_rules.py"})
check(
    "同一個檔案只問一次",
    first and allowed_again,
    "edit 同意後 write 同一個檔案也放行",
)
check(
    "換一個檔案要重新問",
    allowed_other is False,
    "同意改 rules.py 不等於同意改 test_rules.py",
)

print("\n# 6. 沒有人可以問的時候一律拒絕")
reset_grants()
kesi.ask_user = REAL_ASK_USER   # 這一題要驗真的那支，不能用排隊的假答案

original_stdin = sys.stdin
sys.stdin = SimpleNamespace(isatty=lambda: False)
try:
    allowed, refusal = kesi.check_permission(
        "run_command", {"command": "pytest -q"}
    )
finally:
    sys.stdin = original_stdin
check(
    "非互動模式 fail closed",
    allowed is False and refusal is not None,
    repr(refusal),
)

print("\n# 7. 被擋下來的操作仍然有 tool_result")
reset_grants()
answers("n")
blocks = [
    block("run_command", {"command": "rm -rf /"}, "toolu_1"),
    block("run_command", {"command": "pytest"}, "toolu_2"),
    block("read_file", {"file_path": "rules.py"}, "toolu_3"),
]
tool_results, interrupted = kesi.run_tools(blocks)
ids = [r["tool_use_id"] for r in tool_results]
check(
    "三張許願單都有結果",
    ids == ["toolu_1", "toolu_2", "toolu_3"] and not interrupted,
    f"回了 {len(tool_results)} 則",
)
check(
    "被拒絕的兩張標成錯誤，唯讀那張正常執行",
    tool_results[0]["is_error"] and tool_results[1]["is_error"]
    and not tool_results[2]["is_error"],
    f"{tool_results[0]['content'][:20]}... / {tool_results[1]['content'][:20]}...",
)

print("\n# 8. 授權看得到也收得回")
reset_grants()
answers("a", "a")
kesi.check_permission("run_command", {"command": "pytest"})
kesi.check_permission("edit_file", {"file_path": "rules.py"})
before = (len(kesi.GRANTED_COMMANDS), len(kesi.GRANTED_WRITES))
kesi.GRANTED_COMMANDS.clear()
kesi.GRANTED_WRITES.clear()
after = (len(kesi.GRANTED_COMMANDS), len(kesi.GRANTED_WRITES))
check("授權有記下來，也清得掉", before == (1, 1) and after == (0, 0), f"{before} → {after}")

failed = [name for name, ok in results if not ok]
print(f"\n# {len(results) - len(failed)}/{len(results)} 通過")
if failed:
    print("# 沒過：" + "、".join(failed))
sys.exit(1 if failed else 0)
