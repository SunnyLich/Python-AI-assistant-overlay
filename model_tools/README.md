# Model Tools

This folder contains tools that the **LLM can call** during inference (model-callable tools). Each tool is a subprocess script that receives input as JSON and returns a result as JSON.

Drop one folder per tool here:

```text
model_tools/my_tool/
  tool.toml
  tool.py
```

## tool.toml

```toml
name = "my_tool"
label = "My Tool"
description = "Return a short answer from a local script."
enabled = true
timeout_seconds = 8
max_output_chars = 12000

[input_schema]
type = "object"
required = ["query"]

[input_schema.properties.query]
type = "string"
description = "The lookup query."
```

## tool.py

Reads JSON from stdin, writes JSON to stdout:

```python
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
query = payload.get("inputs", {}).get("query", "")
print(json.dumps({"content": f"You asked for: {query}"}))
```

## Notes

- Tools are only invoked when the caller has `use_tools = true` in its config.
- Restart the app (or re-save Settings) after adding tools so the registry refreshes.
- You can also override the legacy tools directory via `.env`: `TOOL_PLUGIN_DIR=path/to/dir`
- For tools that need app hooks, settings, tray actions, or addon-managed state, use an **addon** in `addons/` instead.
