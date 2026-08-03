# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import anthropic

client = anthropic.Anthropic()

resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "用一句話評論 Python 這個程式語言"},
        {"role": "assistant", "content": "我對 Python 的評價是：這語言最大的特色在於"},
    ],
)

for block in resp.content:
    if block.type == "text":
        print(block.text)
