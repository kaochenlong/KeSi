# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import anthropic

client = anthropic.Anthropic()

resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "用一句話介紹誰是高見龍"}],
)

for block in resp.content:
    if block.type == "text":
        print(block.text)

