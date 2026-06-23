# Wisp Communication Graph

This document shows how Wisp's main files and subsystems talk to each other.
The current architecture is supervisor-first: product flows are coordinated by
`runtime.supervisor`, while UI, native OS work, audio, and model/brain work are
isolated into worker processes.

Mermaid source for the larger runtime map: [`communication_graph.mmd`](communication_graph.mmd)

## Process Graph

```mermaid
flowchart LR
    Launcher["Start Wisp launcher or python -m runtime.supervisor.app"]
    Supervisor["runtime.supervisor.app / WispSupervisor"]
    Flows["runtime.supervisor.flows.FlowController"]
    UIWorker["wisp-ui\nruntime.workers.ui_host"]
    NativeWorker["wisp-native\nruntime.workers.native_host"]
    AudioWorker["wisp-audio\nruntime.workers.audio_host"]
    BrainWorker["wisp-brain\nruntime.workers.brain_host"]
    Protocol["runtime.protocol\nnewline-delimited JSON"]

    Launcher --> Supervisor
    Supervisor --> Flows
    Supervisor <--> Protocol
    Protocol <--> UIWorker
    Protocol <--> NativeWorker
    Protocol <--> AudioWorker
    Protocol <--> BrainWorker
    Flows <--> UIWorker
    Flows <--> NativeWorker
    Flows <--> AudioWorker
    Flows <--> BrainWorker
```

## Runtime Services

```mermaid
flowchart TB
    Config["config.py\n.env, typed settings, localized intents"]
    Paths["core.system.paths\ncanonical data paths"]
    SecretStore["core.secret_store\nkeychain/env secret access"]
    LLM["core.llm_clients\nmodel routing and streaming"]
    Tools["core.tool_registry / model_tools\nmodel-callable tools"]
    Addons["core.addon_manager\naddon discovery and host IPC"]
    Memory["core.memory_store\nshort and long-term memory"]
    Conversations["core.conversation_store\nchat/project persistence"]
    Agent["core.agent\nscoped background tasks"]
    Context["core.context_fetcher / core.capture\nambient context and screenshots"]
    Audio["core.audio / core.tts / core.stt\nplayback, speech, transcription"]
    UI["ui.*\nQt widgets, settings, status dialogs"]

    Config --> Paths
    Config --> SecretStore
    Config --> LLM
    Config --> Audio
    LLM --> Tools
    LLM --> Addons
    LLM --> Memory
    Addons --> Tools
    Agent --> LLM
    Agent --> Paths
    UI --> Conversations
    UI --> Memory
    Context --> LLM
```

## Hotkey Query Flow

```mermaid
sequenceDiagram
    participant User
    participant Native as wisp-native
    participant Supervisor as FlowController
    participant UI as wisp-ui
    participant Brain as wisp-brain
    participant Audio as wisp-audio

    User->>Native: Press caller hotkey
    Native-->>Supervisor: native.hotkey event
    Supervisor->>Native: Capture selected text/context
    Supervisor->>UI: Show intent picker
    User->>UI: Choose intent
    UI-->>Supervisor: ui.intent.chosen
    Supervisor->>Brain: Start query with context and caller settings
    Brain-->>Supervisor: reply.chunk events
    Supervisor-->>UI: Append bubble/chat text
    Supervisor-->>Audio: Play streamed TTS when enabled
    Brain-->>Supervisor: reply.done
    Supervisor-->>UI: Finish response state
```

## Chat, Memory, Addons, And Agent Flow

```mermaid
flowchart LR
    ChatUI["ui.chat_window"]
    MemoryUI["ui.memory_viewer"]
    PluginUI["ui.addon_manager"]
    AgentUI["ui.agent.task_window"]
    Supervisor["runtime.supervisor.flows"]
    BrainHandlers["runtime.brain.wisp_brain.handlers"]
    MemoryCore["core.memory_store"]
    AddonCore["core.addon_manager / core.addon_host"]
    AgentCore["core.agent.runner"]
    LLMCore["core.llm_clients.client"]

    ChatUI --> Supervisor
    MemoryUI --> Supervisor
    PluginUI --> Supervisor
    AgentUI --> Supervisor
    Supervisor --> BrainHandlers
    BrainHandlers --> MemoryCore
    BrainHandlers --> AddonCore
    BrainHandlers --> AgentCore
    BrainHandlers --> LLMCore
    AddonCore --> LLMCore
    AgentCore --> LLMCore
```

## Settings And Health Flow

```mermaid
sequenceDiagram
    participant Settings as ui.settings_panel.dialog
    participant Supervisor as FlowController
    participant Setup as core.setup_check
    participant UI as wisp-ui

    Settings->>Supervisor: ui.health.requested(source=settings)
    Supervisor->>Setup: run_setup_check()
    Setup-->>Supervisor: static setup rows
    Supervisor-->>UI: ui.health.show(title="Setup check")
```

## Boundary Rules

- UI work belongs in `wisp-ui` and `ui/`.
- Native OS work belongs in `wisp-native` and platform helpers.
- Audio, STT, and TTS imports belong in `wisp-audio`.
- Model calls, memory, addons, tools, chat, and agent execution belong in
  `wisp-brain` and `core/`.
- The supervisor coordinates flow and lifecycle, but should avoid owning heavy
  domain logic.
- Settings setup checks should stay static and fast; live probes belong to the
  general health-status path.
