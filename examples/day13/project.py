# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Day 13 的示範專案：一個會員訂單試算，測試有一條過不了。

用法：uv run examples/day13/project.py /tmp/kesi-demo

examples/day13/demo.py 會自己把這支叫起來，只有想單獨看一眼題目長什麼樣，
或是想自己手動玩玩看的時候才需要跑它。不給路徑的話會建在 demo-project。

出題的三個原則：

- bug 不在測試檔指到的那個檔案裡（test_pricing → pricing → rules，要追兩跳）
- 錯誤訊息看得到「少折 100 元」，但看不出來是折扣、運費還是折扣表算錯
- 放兩個無關的檔案（utils.py、test_utils.py）當干擾，看 agent 會不會亂讀

bug 在 rules.tier_discount()：`amount > threshold` 應該是 `>=`，
所以買 1001 元有折扣、剛好 1000 元反而沒有。改對之後 7 條測試全綠。

只用標準庫加 pytest，任何機器都跑得起來。
"""

import shutil
import sys
from pathlib import Path

FILES = {
    "README.md": """# 訂單試算

會員結帳時的金額計算。

- `pricing.py`：對外的試算入口
- `rules.py`：折扣與運費規則
- `utils.py`：一些雜項工具

執行測試：`python3 -m pytest -q`
""",
    "pricing.py": '''"""訂單試算的入口。"""
from rules import shipping_fee, tier_discount


def subtotal(items):
    """items 是 [(單價, 數量), ...]。"""
    return sum(price * qty for price, qty in items)


def checkout(items, member_level="normal"):
    """回傳這張訂單要付多少錢。"""
    goods = subtotal(items)
    discount = tier_discount(goods, member_level)
    payable = goods - discount
    return payable + shipping_fee(payable)
''',
    "rules.py": '''"""折扣與運費規則。

滿千折百，兩千以上再多折 50。
運費 60 元，滿 800 免運。
"""
FREE_SHIPPING_THRESHOLD = 800
SHIPPING_FEE = 60

DISCOUNT_TABLE = [
    (2000, 150),
    (1000, 100),
]

LEVEL_BONUS = {
    "normal": 0,
    "gold": 50,
}


def tier_discount(amount, member_level="normal"):
    """依消費金額給折扣，再加上會員等級的額外折抵。"""
    discount = 0
    for threshold, value in DISCOUNT_TABLE:
        if amount > threshold:
            discount = value
            break
    return discount + LEVEL_BONUS.get(member_level, 0)


def shipping_fee(amount):
    if amount >= FREE_SHIPPING_THRESHOLD:
        return 0
    return SHIPPING_FEE
''',
    "utils.py": '''"""跟這次的 bug 無關的雜項工具。"""


def format_money(amount):
    return f"NT$ {amount:,}"


def parse_items(raw):
    """把 "100x2,50x1" 這種字串解析成 [(100, 2), (50, 1)]。"""
    items = []
    for chunk in raw.split(","):
        price, _, qty = chunk.partition("x")
        items.append((int(price), int(qty or 1)))
    return items
''',
    "test_pricing.py": '''from pricing import checkout, subtotal


def test_subtotal():
    assert subtotal([(100, 2), (50, 3)]) == 350


def test_small_order_pays_shipping():
    assert checkout([(100, 1)]) == 160


def test_free_shipping_at_800():
    assert checkout([(800, 1)]) == 800


def test_discount_at_exactly_1000():
    # 滿千折百：1000 - 100 = 900，900 大於 800 所以免運
    assert checkout([(1000, 1)]) == 900


def test_gold_member_bonus():
    # 1200 - (100 + 50) = 1050
    assert checkout([(1200, 1)], member_level="gold") == 1050
''',
    "test_utils.py": '''from utils import format_money, parse_items


def test_format_money():
    assert format_money(1200) == "NT$ 1,200"


def test_parse_items():
    assert parse_items("100x2,50x1") == [(100, 2), (50, 1)]
''',
}


def build(target):
    """把專案建到 target，已存在就整個重來，確保每次的起點都一樣。"""
    target = Path(target).resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name, content in FILES.items():
        (target / name).write_text(content, encoding="utf-8")
    return target


if __name__ == "__main__":
    where = build(sys.argv[1] if len(sys.argv) > 1 else "demo-project")
    print(f"建好了：{where}")
