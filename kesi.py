# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import hashlib
import os
import readline  # 支援上下鍵翻歷史，不過 Windows 沒有這個模組
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PureWindowsPath

import anthropic

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
MAX_OUTPUT_BYTES = 8000
MAX_CAPTURE_BYTES = 1_000_000
COMMAND_TIMEOUT = 120
KILL_GRACE_SECONDS = 3
SHELL_PATH = "/bin/sh"
MAX_TURNS = 20
MAX_TOOL_FAILURES = 3
RG_PATH = shutil.which("rg")
RG_EXCLUDES = [
    glob
    for name in sorted(IGNORE)
    for glob in (f"!**/{name}", f"!**/{name}/**")
] + ["!**/.env.*", "!**/.env.*/**"]
READ_VERSIONS = {}


def safe_path(path):
    try:
        target = (BASE_DIR / path).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return target if target.is_relative_to(BASE_DIR) else None


def safe_write_path(path):
    if not isinstance(path, str) or not path.strip():
        return None

    path_obj = Path(path)
    windows_path = PureWindowsPath(path)
    if (
        path.startswith("~")
        or path_obj.is_absolute()
        or windows_path.drive
        or windows_path.root
    ):
        return None

    try:
        target = (BASE_DIR / path_obj).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if target == BASE_DIR or not target.is_relative_to(BASE_DIR):
        return None
    return target


def is_ignored(target):
    try:
        parts = target.relative_to(BASE_DIR).parts
    except ValueError:
        return True
    return any(
        part in IGNORE or part.startswith(".env.")
        for part in parts
    )


def file_digest(data):
    return hashlib.sha256(data).digest()


def read_file(file_path):
    recorded_target = safe_write_path(file_path)
    if recorded_target is not None:
        READ_VERSIONS.pop(recorded_target, None)

    target = safe_path(file_path)
    if target is None:
        return f"錯誤：找不到或不允許存取檔案 {file_path}", True
    if is_ignored(target):
        return "錯誤：這個檔案不開放讀取。", True
    if not target.is_file():
        return f"錯誤：不是可以讀取的文字檔 {file_path}", True

    try:
        data = target.read_bytes()
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"錯誤：檔案不是 UTF-8 文字檔 {file_path}", True
    except OSError as exc:
        return f"錯誤：無法讀取檔案 {file_path}：{exc}", True
    READ_VERSIONS[target] = file_digest(data)
    return content, False


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

    line_no = content[:first].count("\n") + 1
    new_content = (
        content[:first] + new_string + content[first + len(old_string):]
    )
    try:
        encoded = new_content.encode("utf-8")
        target.write_bytes(encoded)
    except UnicodeEncodeError:
        return "錯誤：new_string 含有無法寫成 UTF-8 的內容。", True
    except OSError as exc:
        READ_VERSIONS.pop(target, None)
        return f"錯誤：無法寫入檔案 {file_path}：{exc}", True
    READ_VERSIONS.pop(target, None)
    return f"替換完成（從第 {line_no} 行開始）。", False


def replace_existing_file(target, data, expected_digest):
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())

        latest = target.read_bytes()
        if file_digest(latest) != expected_digest:
            return "錯誤：檔案在讀取後又有變更，請重新 read_file 後再覆寫。"

        mode = stat.S_IMODE(target.stat().st_mode)
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
        temp_path = None
        return None
    except OSError as exc:
        return f"錯誤：無法覆寫檔案：{exc}"
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def write_file(file_path, content):
    if not isinstance(file_path, str) or not isinstance(content, str):
        return "錯誤：file_path 與 content 都必須是字串。", True

    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError:
        return "錯誤：content 含有無法寫成 UTF-8 的內容。", True

    target = safe_write_path(file_path)
    if target is None:
        return f"錯誤：不允許寫入路徑 {file_path}", True
    if is_ignored(target):
        return "錯誤：這個路徑不開放寫入。", True

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"錯誤：無法建立上層目錄 {file_path}：{exc}", True

    target = safe_write_path(file_path)
    if target is None or is_ignored(target):
        return f"錯誤：不允許寫入路徑 {file_path}", True

    if target.exists():
        if not target.is_file():
            return f"錯誤：不是可以覆寫的文字檔 {file_path}", True

        expected_digest = READ_VERSIONS.get(target)
        if expected_digest is None:
            return (
                "錯誤：覆寫既有檔案前，必須先用 read_file 讀取目前內容。",
                True,
            )

        try:
            current = target.read_bytes()
        except OSError as exc:
            READ_VERSIONS.pop(target, None)
            return f"錯誤：無法確認檔案目前內容 {file_path}：{exc}", True

        if file_digest(current) != expected_digest:
            READ_VERSIONS.pop(target, None)
            return (
                "錯誤：檔案在讀取後又有變更，請重新 read_file 後再覆寫。",
                True,
            )
        if current == data:
            return "檔案內容相同，不需要覆寫。", False

        error = replace_existing_file(target, data, expected_digest)
        READ_VERSIONS.pop(target, None)
        if error is not None:
            return error, True
        return f"已覆寫 {file_path}（{len(data)} bytes）。", False

    created_identity = None
    try:
        with target.open("xb") as file:
            info = os.fstat(file.fileno())
            created_identity = (info.st_dev, info.st_ino)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        return (
            "錯誤：檔案剛被其他程式建立，請先用 read_file 讀取後再決定。",
            True,
        )
    except OSError as exc:
        if created_identity is not None:
            try:
                info = target.stat()
                if (info.st_dev, info.st_ino) == created_identity:
                    target.unlink()
            except OSError:
                pass
        return f"錯誤：無法建立檔案 {file_path}：{exc}", True

    READ_VERSIONS.pop(target, None)
    return f"已建立 {file_path}（{len(data)} bytes）。", False


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


def stop_process_group(proc):
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        proc.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except OSError:
            return
        time.sleep(0.05)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def drain_output(proc, captured, state):
    try:
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            remaining = MAX_CAPTURE_BYTES - len(captured)
            if len(chunk) > remaining:
                captured.extend(chunk[:remaining])
                state["overflow"] = True
                stop_process_group(proc)
                break
            captured.extend(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass


def clip_output(data):
    if len(data) <= MAX_OUTPUT_BYTES:
        return data.decode("utf-8", errors="replace")

    half = MAX_OUTPUT_BYTES // 2
    head = data[:half].decode("utf-8", errors="replace")
    tail = data[-half:].decode("utf-8", errors="replace")
    dropped = len(data) - half * 2
    return f"{head}\n（中間省略 {dropped} bytes）\n{tail}"


def run_command(command):
    if not isinstance(command, str) or not command.strip():
        return "錯誤：command 不可為空。", True

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    try:
        proc = subprocess.Popen(
            [SHELL_PATH, "-c", command],
            cwd=BASE_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return f"錯誤：無法執行指令：{exc}", True

    captured = bytearray()
    state = {"overflow": False}
    reader = threading.Thread(
        target=drain_output,
        args=(proc, captured, state),
        daemon=True,
    )
    reader.start()

    timed_out = False
    try:
        try:
            proc.wait(timeout=COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        stop_process_group(proc)
        proc.wait()
        reader.join(timeout=KILL_GRACE_SECONDS)

    output = clip_output(captured)
    notes = []
    if timed_out:
        notes.append(f"指令超過 {COMMAND_TIMEOUT} 秒未結束，已終止")
    if state["overflow"]:
        notes.append(f"輸出超過 {MAX_CAPTURE_BYTES} bytes，已終止指令")

    code = proc.returncode
    if code is not None and code < 0:
        notes.append(f"被訊號 {-code} 終止")
    notes.append(f"結束碼 {code}")

    summary = "".join(f"（{note}）" for note in notes)
    is_error = bool(timed_out or state["overflow"] or code)
    if not output:
        return f"（指令沒有輸出）{summary}", is_error
    separator = "" if output.endswith("\n") else "\n"
    return f"{output}{separator}{summary}", is_error


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
    {
        "name": "write_file",
        "description": "建立新的 UTF-8 文字檔，或用完整內容覆寫既有檔案。"
        "修改既有檔案的一小部分時優先使用 edit_file。"
        "若確定要覆寫既有檔案，必須先用 read_file 讀取目前版本；"
        "檔案在讀取後若又有變更，write_file 會拒絕覆寫。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "相對於工作目錄的檔案路徑",
                },
                "content": {
                    "type": "string",
                    "description": "要寫入檔案的完整 UTF-8 文字內容",
                },
            },
            "required": ["file_path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_command",
        "description": "在工作目錄執行一行 shell 指令，回傳合併後的輸出與結束碼。"
        "用來跑測試、linter、建置或查 git 紀錄。"
        f"指令最多執行 {COMMAND_TIMEOUT} 秒，超過會被終止；輸出過長會截斷。"
        "沒有互動輸入可用，不要下需要等待輸入的指令。"
        "每次都是全新的行程，cd 或環境變數不會留到下一次呼叫，"
        "需要換目錄時請在同一行用 cd x && ...。"
        "讀寫檔案請優先使用 read_file、edit_file 與 write_file。",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要執行的完整 shell 指令，例如 pytest -q",
                }
            },
            "required": ["command"],
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
    "write_file": write_file,
    "run_command": run_command,
}


def call_tool(block):
    func = TOOL_FUNCS.get(block.name)
    if func is None:
        return f"錯誤：沒有 {block.name} 這個工具。", True
    try:
        return func(**block.input)
    except TypeError as exc:
        return f"錯誤：{block.name} 的參數不正確：{exc}", True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        return f"錯誤：{block.name} 執行失敗：{type(exc).__name__}", True


def tool_result(block_id, output, is_error):
    return {
        "type": "tool_result",
        "tool_use_id": block_id,
        "content": output,
        "is_error": is_error,
    }


def run_tools(blocks):
    results = []
    interrupted = False
    for block in blocks:
        if block.type != "tool_use":
            continue
        if interrupted:
            results.append(
                tool_result(block.id, "錯誤：這一輪已中斷，工具沒有執行。", True)
            )
            continue

        print(f"  [執行工具] {block.name}({block.input})")
        try:
            output, is_error = call_tool(block)
        except KeyboardInterrupt:
            interrupted = True
            output, is_error = "錯誤：使用者中斷了這個工具。", True
        results.append(tool_result(block.id, output, is_error))
    return results, interrupted


def block_type(block):
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def block_id(block):
    if isinstance(block, dict):
        return block.get("id")
    return getattr(block, "id", None)


def seal_dangling_tool_use(history):
    """補上懸空許願單的結果，否則這本日記再也送不出去。"""
    if not history or history[-1].get("role") != "assistant":
        return False

    content = history[-1].get("content")
    if not isinstance(content, list):
        return False

    pending = [b for b in content if block_type(b) == "tool_use"]
    if not pending:
        return False

    history.append({
        "role": "user",
        "content": [
            tool_result(
                block_id(b),
                "錯誤：使用者中斷，這個工具的執行狀態不明；"
                "重試前請先檢查現況。",
                True,
            )
            for b in pending
        ],
    })
    return True


def describe_api_error(exc):
    """這裡的讀者是人，不是模型；模型根本沒收到這個請求。"""
    if isinstance(exc, anthropic.RateLimitError):
        return "錯誤：重試後仍被限流（429），等一下再試。"
    if isinstance(exc, anthropic.OverloadedError):
        return "錯誤：伺服器忙碌中（529），重試後仍未成功，等一下再試。"
    if isinstance(exc, anthropic.BadRequestError):
        return (
            f"錯誤：請求被拒絕（400）：{exc}。"
            "如果是對話太長，可以用 /reset 清空後重來。"
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return "錯誤：等待模型回應逾時。"
    if isinstance(exc, anthropic.APIConnectionError):
        return f"錯誤：連不上 API：{exc}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"錯誤：API 回應 {exc.status_code}：{exc}"
    return f"錯誤：{type(exc).__name__}：{exc}"


def run_agent(client, history):
    failures = 0
    for turn in range(1, MAX_TURNS + 1):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                tools=TOOLS,
                messages=history,
            )
        except anthropic.AnthropicError as exc:
            return describe_api_error(exc)

        if resp.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            reason = (
                "輸出達到 max_tokens"
                if resp.stop_reason == "max_tokens"
                else "回應填滿 context window"
            )
            message = f"錯誤：模型{reason}，本輪沒有執行工具。"
            history.append({"role": "assistant", "content": message})
            return message

        history.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")

        results, interrupted = run_tools(resp.content)
        history.append({"role": "user", "content": results})

        if interrupted:
            message = "（這一輪被中斷了，還沒完成的工作沒有繼續）"
            history.append({"role": "assistant", "content": message})
            return message

        if all(result["is_error"] for result in results):
            failures += 1
            if failures >= MAX_TOOL_FAILURES:
                message = (
                    f"錯誤：工具連續 {MAX_TOOL_FAILURES} 輪全部失敗，"
                    f"在第 {turn} 圈停下來，請換個方式再試。"
                )
                history.append({"role": "assistant", "content": message})
                return message
        else:
            failures = 0

    message = (
        f"錯誤：這一輪已經用掉 {MAX_TURNS} 次模型往返還沒有結論，先停下來。"
        "可以換個問法、把任務拆小，或用 /reset 重來。"
    )
    history.append({"role": "assistant", "content": message})
    return message


def main():
    client = anthropic.Anthropic()
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
            READ_VERSIONS.clear()
            print("（日記與檔案版本紀錄已清空，我們重新開始）")
            continue
        if not user:
            continue

        history.append({"role": "user", "content": user})
        try:
            print("KeSi >", run_agent(client, history))
        except KeyboardInterrupt:
            seal_dangling_tool_use(history)
            print("\n（已中斷，可以接著問下一句）")


if __name__ == "__main__":
    main()
