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
question = [{"role": "user", "content": "現在幾點？"}]

bare = client.messages.count_tokens(
    model="claude-haiku-4-5",
    messages=question,
)
armed = client.messages.count_tokens(
    model="claude-haiku-4-5",
    tools=tools,
    messages=question,
)
print(bare.input_tokens, armed.input_tokens, armed.input_tokens - bare.input_tokens)

for choice in (
    {"type": "auto"},
    {"type": "any"},
    {"type": "tool", "name": "get_time"},
):
    n = client.messages.count_tokens(
        model="claude-haiku-4-5",
        tools=tools,
        tool_choice=choice,
        messages=question,
    ).input_tokens
    print(f"tool_choice={choice['type']:<5} -> {n}  (比不帶工具多 {n - bare.input_tokens})")
