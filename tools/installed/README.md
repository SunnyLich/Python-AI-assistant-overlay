# Installed model tools

Drop one folder per tool here:

```text
tools/installed/my_tool/
  tool.toml
  tool.py
```

`tool.toml`:

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

`tool.py` reads JSON from stdin and writes JSON to stdout:

```python
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
query = payload.get("inputs", {}).get("query", "")
print(json.dumps({"content": f"You asked for: {query}"}))
```

Restart the app or re-save Settings after adding tools so the registry refreshes.
