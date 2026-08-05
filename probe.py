# /// script
# requires-python = ">=3.14"
# dependencies = ["anthropic"]
# ///

import anthropic

client = anthropic.Anthropic()

tools = [
    {
        "name": "get_time",
        "description": "取得現在時間。當使用者問現在幾點時呼叫。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }
]

resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "現在幾點？"}],
)

print("stop_reason:", resp.stop_reason)
for block in resp.content:
    if block.type == "text":
        print("模型說：", block.text)
    elif block.type == "tool_use":
        print(f"模型想呼叫工具：{block.name}，參數：{block.input}")
