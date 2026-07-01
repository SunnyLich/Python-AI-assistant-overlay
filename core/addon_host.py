"""JSON-line subprocess host for one Wisp addon."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class HostContext:
    """Model host context."""
    signals: Any
    model_tool_registry: Any
    config: Any
    addon_id: str
    data_dir: Path


class AddonHost:
    """Model addon host."""
    def __init__(self, addon_id: str, folder: Path, entry: str) -> None:
        """Initialize the addon host instance."""
        self.addon_id = addon_id
        self.folder = folder
        self.entry = entry
        self.module: Any = None
        self.tool_executors: dict[str, Any] = {}

    def load(self) -> None:
        """Import the addon's entry module into a namespaced package."""
        entry_path = (self.folder / self.entry).resolve()
        if not entry_path.exists():
            raise FileNotFoundError(f"addon entry does not exist: {entry_path}")
        package_name = f"wisp_addons.{self.addon_id.replace('-', '_')}"
        if str(self.folder) not in sys.path:
            sys.path.insert(0, str(self.folder))
        spec = importlib.util.spec_from_file_location(
            package_name,
            entry_path,
            submodule_search_locations=[str(self.folder)] if entry_path.name == "__init__.py" else None,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not create import spec for {entry_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        self.module = module

    def call(self, method: str, params: dict[str, Any]) -> Any:
        """Dispatch an RPC method (ping/hooks/lifecycle/tool) to the loaded addon."""
        if method == "ping":
            return {"ok": True, "pid": os.getpid()}
        if method == "hooks":
            return self._hook_names()
        if method == "on_startup":
            return self._on_startup(params)
        if method == "on_shutdown":
            return self._call_hook("on_shutdown")
        if method == "before_query":
            return self._before_query(params)
        if method == "after_response":
            return self._call_hook("after_response", str(params.get("text") or ""))
        if method == "get_tray_actions":
            return self._tray_labels()
        if method == "run_tray_action":
            return self._run_tray_action(str(params.get("label") or ""))
        if method == "get_intents":
            return self._intents()
        if method == "run_intent":
            return self._run_intent(str(params.get("id") or ""), params.get("payload") or {})
        if method == "get_notifications":
            return self._call_list_hook("get_notifications")
        if method == "get_hotkeys":
            return self._hotkeys()
        if method == "run_hotkey":
            return self._run_hotkey(str(params.get("id") or ""), params.get("payload") or {})
        if method == "on_event":
            return self._on_event(str(params.get("event") or ""), params.get("payload") or {})
        if method == "get_settings":
            return self._call_list_hook("get_settings")
        if method == "get_text_annotations":
            return self._text_annotations(params)
        if method == "get_tools":
            return self._tools()
        if method == "execute_tool":
            return self._execute_tool(str(params.get("name") or ""), params.get("inputs") or {})
        raise ValueError(f"unknown addon host method: {method}")

    def _hook_names(self) -> list[str]:
        """Handle hook names for addon host."""
        hooks = (
            "on_startup",
            "on_shutdown",
            "before_query",
            "after_response",
            "get_tray_actions",
            "get_intents",
            "get_notifications",
            "get_hotkeys",
            "on_event",
            "get_tools",
            "get_settings",
            "get_text_annotations",
        )
        return [name for name in hooks if hasattr(self.module, name)]

    def _on_startup(self, params: dict[str, Any]) -> None:
        """Handle startup events."""
        import config

        data_dir = Path(str(params.get("data_dir") or self.folder / ".data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        ctx = HostContext(
            signals=None,
            model_tool_registry=None,
            config=config,
            addon_id=self.addon_id,
            data_dir=data_dir,
        )
        return self._call_hook("on_startup", ctx)

    def _before_query(self, params: dict[str, Any]) -> dict[str, str]:
        """Handle before query for addon host."""
        prompt = str(params.get("prompt") or "")
        context = str(params.get("context") or "")
        fn = getattr(self.module, "before_query", None)
        if not callable(fn):
            return {"prompt": prompt, "context": context}
        result = fn(prompt, context)
        if isinstance(result, tuple) and len(result) == 2:
            return {"prompt": str(result[0]), "context": str(result[1])}
        if isinstance(result, dict):
            return {
                "prompt": str(result.get("prompt", prompt)),
                "context": str(result.get("context", context)),
            }
        return {"prompt": prompt, "context": context}

    def _tray_labels(self) -> list[str]:
        """Handle tray labels for addon host."""
        actions = self._call_list_hook("get_tray_actions")
        return [
            str(item.get("label") or "Action")
            for item in actions
            if isinstance(item, dict)
        ]

    def _run_tray_action(self, label: str) -> None:
        """Run tray action."""
        if not label:
            raise ValueError("label is required")
        actions = self._call_list_hook("get_tray_actions")
        for item in actions:
            if not isinstance(item, dict) or str(item.get("label") or "") != label:
                continue
            callback = item.get("callback")
            if not callable(callback):
                raise ValueError(f"addon action is not callable: {label}")
            callback()
            return None
        raise ValueError(f"addon action not found: {label}")

    def _intents(self) -> list[dict[str, Any]]:
        """Handle intents for addon host."""
        intents = self._call_list_hook("get_intents")
        out: list[dict[str, Any]] = []
        for item in intents:
            if not isinstance(item, dict):
                continue
            out.append({
                "id": str(item.get("id") or item.get("label") or "intent"),
                "key": str(item.get("key") or ""),
                "label": str(item.get("label") or item.get("id") or "Intent"),
                "hint": str(item.get("hint") or item.get("description") or ""),
                "prompt": str(item.get("prompt") or item.get("template") or ""),
                "caller": str(item.get("caller") or "all"),
                "callback": callable(item.get("callback")),
            })
        return out

    def _run_intent(self, intent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run intent."""
        if not intent_id:
            raise ValueError("intent id is required")
        for item in self._call_list_hook("get_intents"):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("label") or "")
            if item_id != intent_id:
                continue
            callback = item.get("callback")
            if not callable(callback):
                prompt = str(item.get("prompt") or item.get("template") or "")
                return {"prompt": prompt} if prompt else {}
            result = callback(payload or {})
            return result if isinstance(result, dict) else {"result": result}
        raise ValueError(f"addon intent not found: {intent_id}")

    def _on_event(self, event: str, payload: dict[str, Any]) -> Any:
        """Handle event events."""
        fn = getattr(self.module, "on_event", None)
        if callable(fn):
            return fn(event, payload or {})
        return None

    def _hotkeys(self) -> list[dict[str, Any]]:
        """Handle hotkeys for addon host."""
        hotkeys = self._call_list_hook("get_hotkeys")
        out: list[dict[str, Any]] = []
        for item in hotkeys:
            if not isinstance(item, dict):
                continue
            out.append({
                "id": str(item.get("id") or item.get("label") or "hotkey"),
                "label": str(item.get("label") or item.get("id") or "Hotkey"),
                "hotkey": str(item.get("hotkey") or item.get("combo") or ""),
                "prompt": str(item.get("prompt") or ""),
                "intent_id": str(item.get("intent_id") or ""),
                "callback": callable(item.get("callback")),
            })
        return out

    def _run_hotkey(self, hotkey_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run hotkey."""
        if not hotkey_id:
            raise ValueError("hotkey id is required")
        for item in self._call_list_hook("get_hotkeys"):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("label") or "")
            if item_id != hotkey_id:
                continue
            callback = item.get("callback")
            if callable(callback):
                result = callback(payload or {})
                return result if isinstance(result, dict) else {"result": result}
            prompt = str(item.get("prompt") or "")
            return {"prompt": prompt} if prompt else {}
        raise ValueError(f"addon hotkey not found: {hotkey_id}")

    def _tools(self) -> list[dict[str, Any]]:
        """Handle tools for addon host."""
        tools = self._call_list_hook("get_tools")
        out: list[dict[str, Any]] = []
        self.tool_executors.clear()
        for item in tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            executor = item.get("executor")
            if callable(executor):
                self.tool_executors[name] = executor
            out.append({
                "name": name,
                "description": str(item.get("description") or name),
                "input_schema": item.get("input_schema") or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                "has_executor": callable(executor),
            })
        return out

    def _execute_tool(self, name: str, inputs: dict[str, Any]) -> str:
        """Handle execute tool for addon host."""
        executor = self.tool_executors.get(name)
        if executor is None:
            self._tools()
            executor = self.tool_executors.get(name)
        if executor is None:
            raise ValueError(f"addon tool not found or not executable: {name}")
        result = executor(inputs or {})
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    def _text_annotations(self, params: dict[str, Any]) -> list[Any]:
        """Return display-only text annotations from an addon hook."""
        fn = getattr(self.module, "get_text_annotations", None)
        if not callable(fn):
            return []
        result = fn(params or {})
        return result if isinstance(result, list) else []

    def _call_hook(self, hook: str, *args: Any) -> Any:
        """Call hook."""
        fn = getattr(self.module, hook, None)
        if callable(fn):
            return fn(*args)
        return None

    def _call_list_hook(self, hook: str) -> list[Any]:
        """Call list hook."""
        fn = getattr(self.module, hook, None)
        if not callable(fn):
            return []
        result = fn()
        return result if isinstance(result, list) else []


def _respond(req_id: Any, result: Any = None, error: str | None = None) -> None:
    """Handle respond for addon host."""
    payload = {"id": req_id, "result": result}
    if error is not None:
        payload["error"] = error
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    """Handle main for addon host."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--entry", default="__init__.py")
    parser.add_argument("--store", default="")
    args = parser.parse_args()

    if args.store:
        os.environ["WISP_ADDON_STORE"] = args.store
    os.environ["WISP_ADDON_ID"] = args.id

    host = AddonHost(args.id, Path(args.folder), args.entry)
    try:
        host.load()
    except Exception:
        _respond(None, error=traceback.format_exc())
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = host.call(str(request.get("method") or ""), request.get("params") or {})
            _respond(request.get("id"), result=result)
        except Exception:
            _respond(request.get("id") if isinstance(locals().get("request"), dict) else None, error=traceback.format_exc())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
