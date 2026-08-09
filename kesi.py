# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import readline  # 支援上下鍵翻歷史，不過 Windows 沒有這個模組
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

import anthropic

client = anthropic.Anthropic()
BASE_DIR = Path.cwd().resolve()
IGNORE = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    ".DS_Store",
    ".pytest_cache",
}
MAX_LIST_FILES = 200
MAX_SEARCH_LINES = 100
MAX_ERROR_CHARS = 2000
RG_PATH = shutil.which("rg")
RG_EXCLUDES = [
    glob
    for name in sorted(IGNORE)
    for glob in (f"!**/{name}", f"!**/{name}/**")
] + ["!**/.env.*", "!**/.env.*/**"]


def safe_path(path):
    try:
        target = (BASE_DIR / path).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return target if target.is_relative_to(BASE_DIR) else None


def is_ignored(target):
    try:
        parts = target.relative_to(BASE_DIR).parts
    except ValueError:
        return True
    return any(
        part in IGNORE or part.startswith(".env.")
        for part in parts
    )


def read_file(file_path):
    target = safe_path(file_path)
    if target is None:
        return f"錯誤：找不到或不允許存取檔案 {file_path}", True
    if is_ignored(target):
        return "錯誤：這個檔案不開放讀取。", True
    if not target.is_file():
        return f"錯誤：不是可以讀取的文字檔 {file_path}", True

    try:
        return target.read_bytes().decode("utf-8"), False
    except UnicodeDecodeError:
        return f"錯誤：檔案不是 UTF-8 文字檔 {file_path}", True
    except OSError as exc:
        return f"錯誤：無法讀取檔案 {file_path}：{exc}", True


def edit_file(file_path, old_string, new_string):
    if not all(
        isinstance(value, str)
        for value in (file_path, old_string, new_string)
    ):
        return "錯誤：file_path、old_string、new_string 都必須是字串。", True
    if not old_string:
        return "錯誤：old_string 不可為空。", True
    if old_string == new_string:
        return "錯誤：old_string 與 new_string 相同，沒有內容需要修改。", True

    target = safe_path(file_path)
    if target is None:
        return f"錯誤：找不到或不允許存取檔案 {file_path}", True
    if is_ignored(target):
        return "錯誤：這個檔案不開放修改。", True
    if not target.is_file():
        return f"錯誤：不是可以修改的文字檔 {file_path}", True

    try:
        content = target.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return f"錯誤：檔案不是 UTF-8 文字檔 {file_path}", True
    except OSError as exc:
        return f"錯誤：無法讀取檔案 {file_path}：{exc}", True

    first = content.find(old_string)
    if first == -1:
        return (
            "錯誤：在檔案裡找不到要替換的文字。old_string 必須與檔案內容"
            "完全一致（包含空白、縮排與換行），請先用 read_file 確認目前內容。",
            True,
        )
    if content.find(old_string, first + 1) != -1:
        return (
            "錯誤：要替換的文字在檔案裡不只出現一次，無法確定要改哪一處。"
            "請加長 old_string、納入更多前後文，使它在檔案中唯一。",
            True,
        )

    prefix = content[:first]
    line_no = (
        prefix.count("\n")
        + prefix.count("\r")
        - prefix.count("\r\n")
        + 1
    )
    new_content = (
        content[:first] + new_string + content[first + len(old_string):]
    )
    try:
        encoded = new_content.encode("utf-8")
        target.write_bytes(encoded)
    except UnicodeEncodeError:
        return "錯誤：new_string 含有無法寫成 UTF-8 的內容。", True
    except OSError as exc:
        return f"錯誤：無法寫入檔案 {file_path}：{exc}", True
    return f"替換完成（從第 {line_no} 行開始）。", False


def list_files(path="."):
    target = safe_path(path)
    if target is None:
        return f"錯誤：找不到或不允許存取目錄 {path}", True
    if is_ignored(target):
        return "錯誤：這個目錄不開放讀取。", True
    if not target.is_dir():
        return f"錯誤：{path} 不是一個目錄", True

    try:
        entries = []
        for entry in target.iterdir():
            resolved = safe_path(entry)
            if resolved is None or is_ignored(resolved):
                continue
            entries.append((entry.name, resolved.is_dir()))
    except OSError as exc:
        return f"錯誤：無法列出目錄 {path}：{exc}", True

    entries.sort(key=lambda item: item[0])
    lines = [name + "/" if is_dir else name for name, is_dir in entries]
    if not lines:
        return "（這個目錄沒有可列出的項目）", False
    if len(lines) > MAX_LIST_FILES:
        head = "\n".join(lines[:MAX_LIST_FILES])
        return (
            f"{head}\n（共有 {len(lines)} 筆，只顯示前 {MAX_LIST_FILES} 筆）",
            False,
        )
    return "\n".join(lines), False


def glob_files(pattern):
    if not isinstance(pattern, str) or not pattern.strip():
        return "錯誤：pattern 不可為空。", True

    pattern_path = Path(pattern)
    windows_pattern = PureWindowsPath(pattern)
    if pattern_path == Path("."):
        return "錯誤：pattern 必須指定要找的檔名樣式。", True
    if (
        pattern.startswith("~")
        or pattern_path.is_absolute()
        or windows_pattern.drive
        or windows_pattern.root
        or ".." in pattern_path.parts
        or ".." in windows_pattern.parts
    ):
        return "錯誤：pattern 只能用工作目錄內的相對樣式。", True

    try:
        candidates = sorted(
            BASE_DIR.glob(pattern, recurse_symlinks=False),
            key=lambda candidate: candidate.as_posix(),
        )
    except (OSError, ValueError, NotImplementedError) as exc:
        return f"錯誤：無法解析 glob 樣式：{exc}", True

    matches = []
    for candidate in candidates:
        if candidate == BASE_DIR:
            continue
        target = safe_path(candidate)
        if target is None or is_ignored(target):
            continue
        relative = candidate.relative_to(BASE_DIR).as_posix()
        matches.append(relative + "/" if target.is_dir() else relative)

    if not matches:
        return f"找不到可存取且符合 {pattern} 的檔案。", False
    if len(matches) > MAX_LIST_FILES:
        head = "\n".join(matches[:MAX_LIST_FILES])
        return (
            f"{head}\n（共有 {len(matches)} 筆，只顯示前 {MAX_LIST_FILES} 筆）",
            False,
        )
    return "\n".join(matches), False


def grep(pattern, path="."):
    if not isinstance(pattern, str) or not pattern:
        return "錯誤：pattern 不可為空。", True

    target = safe_path(path)
    if target is None:
        return f"錯誤：找不到或不允許搜尋路徑 {path}", True
    if is_ignored(target):
        return "錯誤：這個路徑不開放搜尋。", True
    if not (target.is_file() or target.is_dir()):
        return f"錯誤：{path} 不是可以搜尋的檔案或目錄", True
    if RG_PATH is None:
        return "錯誤：找不到 rg 指令，請先安裝 ripgrep。", True

    relative_path = target.relative_to(BASE_DIR).as_posix() or "."
    args = [
        RG_PATH,
        "--no-config",
        "--no-follow",
        "--line-number",
        "--with-filename",
        "--no-heading",
        "--color",
        "never",
        "--max-columns",
        "200",
        "--max-columns-preview",
    ]
    for excluded in RG_EXCLUDES:
        args.extend(["--glob", excluded])
    args.extend(["--", pattern, relative_path])

    try:
        proc = subprocess.run(
            args,
            cwd=BASE_DIR,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except FileNotFoundError:
        return "錯誤：找不到 rg 指令，請先安裝 ripgrep。", True
    except subprocess.TimeoutExpired:
        return "錯誤：搜尋超過 10 秒被中止，請縮小範圍後再試。", True
    except OSError as exc:
        return f"錯誤：無法執行搜尋：{exc}", True

    if proc.returncode == 1:
        return f"找不到含有 {pattern} 的內容。", False
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "rg 沒有提供錯誤訊息"
        return f"搜尋失敗：{detail[:MAX_ERROR_CHARS]}", True

    lines = []
    for line in proc.stdout.splitlines():
        if line.startswith(("./", ".\\")):
            line = line[2:]
        lines.append(line)
    if len(lines) > MAX_SEARCH_LINES:
        head = "\n".join(lines[:MAX_SEARCH_LINES])
        return (
            f"{head}\n（共有 {len(lines)} 行，只顯示前 {MAX_SEARCH_LINES} 行）",
            False,
        )
    return "\n".join(lines), False


TOOLS = [
    {
        "name": "read_file",
        "description": "讀取工作目錄底下的文字檔，回傳完整內容。"
        "當使用者問到某個檔案裡有什麼、或需要檔案內容才能回答時呼叫。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "相對於工作目錄的檔案路徑",
                }
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_files",
        "description": "列出指定目錄底下的檔案與子目錄，目錄名稱結尾會帶斜線。"
        "想知道專案裡有什麼、或不確定檔案位置時，先用這個環顧四周。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相對於工作目錄的路徑，省略時代表工作目錄本身",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "glob",
        "description": "用萬用字元樣式尋找檔案，例如 *.py 找當層的 Python 檔、"
        "**/*.py 連子目錄一起找。知道要找哪一類檔案、但不知道位置時用這個。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "glob 樣式，支援 * 與 **",
                }
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "grep",
        "description": "在工作目錄的檔案內容中搜尋文字，支援正規表達式，"
        "回傳「檔名:行號:該行內容」格式。想知道某個函式、變數或字串"
        "出現在哪些檔案時用這個，不要一個一個檔案讀。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要搜尋的文字或正規表達式",
                },
                "path": {
                    "type": "string",
                    "description": "限定搜尋的檔案或子目錄，省略時搜整個工作目錄",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "edit_file",
        "description": "修改既有 UTF-8 文字檔：把 old_string 替換成 new_string。"
        "old_string 必須與檔案現有內容完全一致（含空白、縮排與換行），"
        "而且在檔案中只出現一次；不唯一時請加長前後文。"
        "修改前應先用 read_file 讀取目前內容。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "相對於工作目錄的既有檔案路徑",
                },
                "old_string": {
                    "type": "string",
                    "description": "要被替換的原文，需完全一致、非空且唯一",
                },
                "new_string": {
                    "type": "string",
                    "description": "替換後的新內容，可為空字串以刪除原文",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    },
]

TOOL_FUNCS = {
    "read_file": read_file,
    "list_files": list_files,
    "glob": glob_files,
    "grep": grep,
    "edit_file": edit_file,
}


def run_agent(history):
    while True:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            tools=TOOLS,
            messages=history,
        )
        history.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")

        results = []
        for block in resp.content:
            if block.type == "tool_use":
                print(f"  [執行工具] {block.name}({block.input})")
                func = TOOL_FUNCS.get(block.name)
                if func is None:
                    output = f"錯誤：沒有 {block.name} 這個工具。"
                    is_error = True
                else:
                    try:
                        output, is_error = func(**block.input)
                    except TypeError as exc:
                        output = f"錯誤：{block.name} 的參數不正確：{exc}"
                        is_error = True
                    except Exception as exc:
                        output = f"錯誤：{block.name} 執行失敗：{type(exc).__name__}"
                        is_error = True
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                })
        history.append({"role": "user", "content": results})


history = []
print("KeSi。輸入 /exit 離開、/reset 清空對話。")
while True:
    try:
        user = input("你 > ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if user in ("/exit", "/quit"):
        break
    if user == "/reset":
        history = []
        print("（日記已清空，我們重新開始）")
        continue
    if not user:
        continue

    history.append({"role": "user", "content": user})
    print("KeSi >", run_agent(history))
