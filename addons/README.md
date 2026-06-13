# Addons

Addons extend Wisp with query hooks, response observers, tray actions, settings,
and model-callable tools.

Each addon lives in its own folder under `addons/` and declares an `addon.toml`
manifest:

```text
addons/
  my-addon/
    addon.toml
    __init__.py
```

Addons run in a dedicated Python subprocess, one process per addon. That means a
crash, import failure, or slow hook is isolated from the brain worker and from
other addons.

## Manifest

```toml
[addon]
id = "my-addon"
name = "My Addon"
version = "1.0.0"
description = "Adds one small behavior to Wisp."
entry = "__init__.py"
api_version = "1"

[permissions]
query = "modify"
response = "read"
tools = true
ui = ["tray", "settings"]
```

Missing permissions are denied. For example, an addon without `tools = true`
will not register model-callable tools, and an addon without `ui = ["tray"]`
will not expose tray actions.

## Hooks

All hooks are optional:

```python
def on_startup(app_context):
    # app_context.config is the live config module.
    # app_context.data_dir is a per-addon writable directory.
    pass

def on_shutdown():
    pass

def before_query(prompt: str, context: str) -> tuple[str, str]:
    return prompt, context

def after_response(text: str):
    pass

def get_tray_actions() -> list[dict]:
    return [{"label": "Run thing", "callback": run_thing}]

def get_settings() -> list[dict]:
    return [{"key": "prefix", "label": "Prefix", "type": "text", "default": "[my-addon]"}]

def get_tools() -> list[dict]:
    return [{
        "name": "my_tool",
        "description": "Does something useful.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "executor": lambda inputs: "ok",
    }]
```

Read settings with:

```python
from core.plugin_manager import plugin_setting

value = plugin_setting("my-addon", "prefix", "[my-addon]")
```

The `core.plugin_manager` import remains as a compatibility alias while the
runtime migrates to addon naming.
