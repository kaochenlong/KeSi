# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import readline  # 讓 input() 支援上下鍵翻歷史，Windows 沒有這個模組

import anthropic

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
        print("（日記已清空，我們重新開始）")
        continue
    if not user:
        continue

    history.append({"role": "user", "content": user})
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=history,
    )
    history.append({"role": "assistant", "content": resp.content})

    text = "".join(b.text for b in resp.content if b.type == "text")
    print("KeSi >", text)
