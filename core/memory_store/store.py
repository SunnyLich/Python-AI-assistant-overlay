"""
core/memory.py -” Long-term and short-term memory for the AI assistant.

Short-term memory (STM):
  - In-memory log of turns from the current session; reset on every app start.
  - Mid-session compression: when accumulated turns exceed MEMORY_STM_TOKEN_BUDGET
    (rough token estimate) the oldest half is summarised by the memory LLM and
    replaced by a single compressed block so the context window stays bounded.
  - Consolidated into LTM every MEMORY_CONSOLIDATION_INTERVAL minutes via a
    background timer.

Long-term memory (LTM):
  - Atomic facts stored in a chromadb vector collection (local, file-backed).
  - Categories: project_context | general.
  - Retrieval: top-k semantic search; result injected into every LLM call.
  - Writes: explicit ("remember that -¦") or via the periodic summariser.
  - Conflict: if a new fact is semantically similar (cosine distance < 0.15,
    i.e. similarity > 0.85) to an existing one the old fact is archived
    (not deleted) and the new one wins. Timestamps are preserved for audit.

Storage: memory/ folder at the project root (gitignored).

Graceful degradation: if chromadb / sentence-transformers are not installed,
the module falls back to a plain JSON store with no semantic search (all active
facts are injected, capped at 10).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import config
from core.system.paths import MEMORY_DIR

try:
    from core.context_router import ContextChunk, ContextRouter
    _HAS_ROUTER = True
except Exception:
    _HAS_ROUTER = False

_MEMORY_DIR = str(MEMORY_DIR)
_CHROMA_DIR = os.path.join(_MEMORY_DIR, "chroma")
_FALLBACK_PATH = os.path.join(_MEMORY_DIR, "facts_fallback.json")

_CATEGORIES = ("project_context", "general")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SUMMARIZER_PROMPT = """\
You are extracting facts for a personal memory system.

Extract ONLY facts that fit these categories:
- project_context: things the user is working on, projects, goals, tasks, deadlines
- general: everything else -” preferences, personal facts, background, open questions

Rules:
- Each fact must be a single atomic sentence, under 20 words.
- Extract ONLY durable facts the USER stated about themselves, their preferences,
  their projects, or their stable situation.
- Do NOT extract transient queries ("what time is it in Tokyo").
- Do NOT extract ordinary task requests, troubleshooting steps, greetings, thanks,
  one-off opinions, UI labels, code, logs, URLs, file paths, or copied document text.
- Do NOT extract credentials, passwords, or secrets.
- Do NOT extract information the assistant provided; only user-originated facts.
- Prefer returning [] unless the fact will clearly still be useful next week.
- If nothing qualifies, return an empty array.

Conversation turns:
{turns}

Return ONLY a JSON array, no other text:
[{{"text": "...", "category": "project_context|general"}}, ...]"""

_COMPRESSION_PROMPT = """\
Compress the following conversation turns into a concise summary (2-“3 sentences maximum) \
that preserves key decisions, topics discussed, and any context the user provided.
Be factual and brief.

Turns:
{turns}

Summary:"""

# ---------------------------------------------------------------------------
# Category inference for explicit writes (avoids an extra LLM round-trip)
# ---------------------------------------------------------------------------

_CAT_KEYWORDS: dict[str, list[str]] = {
    "project_context": [
        "working on", "project", "deadline", "goal", "building",
        "developing", "task", "sprint", "release",
    ],
}

_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|password|secret)\b", re.IGNORECASE),
)
_JUNK_PREFIXES = (
    "what is", "what are", "how do", "how can", "can you", "could you",
    "please ", "fix ", "rewrite ", "summarize ", "explain ", "create ",
    "make ", "add ", "remove ", "update ", "open ", "search ", "find ",
)
_DURABLE_CUES = (
    "i am ", "i'm ", "i like ", "i prefer ", "i use ", "i work ",
    "my ", "our project", "the project", "this project", "working on",
    "building", "developing", "deadline", "goal",
)


def _infer_category(text: str) -> str:
    t = text.lower()
    for cat, kws in _CAT_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return cat
    return "general"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_tokens(text: str) -> int:
    """Rough estimate: 1 token â‰ˆ 4 chars."""
    return max(1, len(text) // 4)


def _normalize_fact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip(" \t\r\n-•")).strip()


def _is_memory_worthy_fact(text: str, *, source: str) -> bool:
    """Reject noisy/non-durable memory candidates before they hit storage."""
    fact = _normalize_fact_text(text)
    lower = fact.lower()
    words = re.findall(r"[A-Za-z0-9']+", fact)
    if not fact or len(words) < 3 or len(words) > 24:
        return False
    if fact.endswith("?") or any(p.search(fact) for p in _SECRET_PATTERNS):
        return False
    if "http://" in lower or "https://" in lower or "\\" in fact or "/" in fact:
        return False
    if "```" in fact or "traceback" in lower or "error:" in lower:
        return False
    if any(lower.startswith(prefix) for prefix in _JUNK_PREFIXES):
        return source == "explicit"
    if source == "summarizer" and not any(cue in lower for cue in _DURABLE_CUES):
        return False
    return True


def _lexical_overlap(query: str, fact: str) -> int:
    stop = {
        "the", "and", "for", "with", "that", "this", "you", "your", "are",
        "was", "were", "have", "has", "had", "what", "how", "can", "please",
    }
    q_words = {w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) > 2 and w not in stop}
    f_words = {w for w in re.findall(r"[a-z0-9']+", fact.lower()) if len(w) > 2 and w not in stop}
    return len(q_words & f_words)


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------

class MemoryManager:
    """
    Manages short-term (in-session) and long-term (persisted) memory.

    Thread-safety: all STM mutations are guarded by _stm_lock.
    chromadb calls are inherently thread-safe in embedded mode.
    """

    def __init__(self) -> None:
        os.makedirs(_CHROMA_DIR, exist_ok=True)
        self._stm_lock = threading.Lock()
        self._stm: list[dict] = []          # turns + compressed blocks
        self._compressing = False           # guard against concurrent compression
        self._collection = None             # chromadb collection (None = unavailable)
        self._chroma_ok = False
        self._consolidation_timer: threading.Timer | None = None
        self._ctx_router = None
        self._ctx_router_lock = threading.Lock()
        self._ctx_router_fact_count = -1

        self._init_chromadb()
        self._sync_consolidation_timer()

    # ------------------------------------------------------------------
    # chromadb initialisation
    # ------------------------------------------------------------------

    def _init_chromadb(self) -> None:
        try:
            import chromadb
            from chromadb.utils import embedding_functions

            ef = embedding_functions.DefaultEmbeddingFunction()
            client = chromadb.PersistentClient(path=_CHROMA_DIR)
            self._collection = client.get_or_create_collection(
                name="ltm_facts",
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            self._chroma_ok = True
            print(
                f"[memory] chromadb ready. "
                f"{self._collection.count()} fact(s) in long-term memory."
            )
        except Exception as exc:
            print(
                f"[memory] chromadb unavailable -” using plain JSON fallback: {exc}"
            )
            self._chroma_ok = False
            self._collection = None

    # ------------------------------------------------------------------
    # Short-term memory
    # ------------------------------------------------------------------

    def record_turn(
        self,
        user_text: str,
        assistant_text: str,
        context: str = "",
    ) -> None:
        """Append a completed turn to the in-session STM."""
        self._sync_consolidation_timer()
        with self._stm_lock:
            self._stm.append({
                "type": "turn",
                "_id": str(uuid.uuid4()),
                "role_user": user_text,
                "role_assistant": assistant_text,
                "context": context,
                "timestamp": _now_iso(),
            })
            # Check the budget inside the lock so we only spawn a thread when
            # compression is actually needed, avoiding a pointless thread per turn.
            raw = [e for e in self._stm if e.get("type") == "turn"]
            total = sum(
                _estimate_tokens(t.get("role_user", "") + " " + t.get("role_assistant", ""))
                for t in raw
            )
            over_budget = total > config.MEMORY_STM_TOKEN_BUDGET
            launch_compression = not self._compressing and over_budget
            if launch_compression:
                self._compressing = True

        if launch_compression:
            threading.Thread(
                target=self._maybe_compress_stm,
                daemon=True,
            ).start()

    def get_stm_context(self) -> str:
        """
        Return a compact text block representing current session history for
        injection into the system prompt.

        Format:
            [Earlier in this session]
            <compressed summary>

            User: ...
            Assistant: ...
        """
        with self._stm_lock:
            parts: list[str] = []

            for entry in self._stm:
                if entry.get("type") == "compressed_block":
                    parts.append(f"[Earlier in this session]\n{entry['summary']}")

            raw = [e for e in self._stm if e.get("type") == "turn"]
            for t in raw[-10:]:                     # last 10 turns only
                if t.get("role_user"):
                    parts.append(f"User: {t['role_user']}")
                if t.get("role_assistant"):
                    parts.append(f"Assistant: {t['role_assistant']}")

            return "\n\n".join(parts)

    def _maybe_compress_stm(self) -> None:
        """
        If the raw-turn token estimate exceeds the budget, compress the oldest
        half of turns into a summary block via the memory LLM.
        Runs in a background thread; _compressing is set by the caller.
        """
        try:
            with self._stm_lock:
                raw = [e for e in self._stm if e.get("type") == "turn"]
                total = sum(
                    _estimate_tokens(
                        t.get("role_user", "") + " " + t.get("role_assistant", "")
                    )
                    for t in raw
                )
                if total <= config.MEMORY_STM_TOKEN_BUDGET:
                    return
                half = len(raw) // 2
                if half < 2:
                    return
                to_compress = raw[:half]
                compress_ids = {t["_id"] for t in to_compress}

            turns_text = "\n".join(
                f"User: {t['role_user']}\nAssistant: {t['role_assistant']}"
                for t in to_compress
            )
            summary = self._call_memory_llm(
                _COMPRESSION_PROMPT.format(turns=turns_text),
                max_tokens=200,
            ).strip()

            with self._stm_lock:
                self._stm = [e for e in self._stm if e.get("_id") not in compress_ids]
                self._stm.insert(0, {
                    "type": "compressed_block",
                    "_id": str(uuid.uuid4()),
                    "summary": summary,
                    "timestamp": _now_iso(),
                })
                print(f"[memory] STM compressed {half} turn(s) into a summary block.")
        except Exception as exc:
            print(f"[memory] STM compression error: {exc}")
        finally:
            self._compressing = False

    # ------------------------------------------------------------------
    # Long-term memory -” explicit writes
    # ------------------------------------------------------------------

    def add_explicit_fact(self, text: str) -> None:
        """
        Immediately commit a user-stated fact to LTM.
        Called when the user's message starts with "remember that -¦".
        """
        category = _infer_category(text)
        self._upsert_fact(text.strip(), category, source="explicit")
        print(f"[memory] Explicit fact stored ({category}): {text!r}")


    # ------------------------------------------------------------------
    # Context router
    # ------------------------------------------------------------------

    def _get_router(self):
        # Return the cached ContextRouter, rebuilding when fact count changes.
        if not _HAS_ROUTER or not self._chroma_ok or self._collection is None:
            return None
        try:
            count = self._collection.count()
            if count == 0:
                return None
            with self._ctx_router_lock:
                if self._ctx_router is None or count != self._ctx_router_fact_count:
                    self._rebuild_router(count)
                return self._ctx_router
        except Exception:
            return None

    def _rebuild_router(self, fact_count):
        # Build a ContextRouter from LTM facts (call while holding _ctx_router_lock).
        try:
            results = self._collection.get(
                where={'archived': {'$eq': False}},
                include=['documents', 'metadatas'],
            )
            chunks = []
            for doc, meta, fid in zip(
                results.get('documents', []),
                results.get('metadatas', []),
                results.get('ids', []),
            ):
                source = meta.get('source', 'memory')
                chunks.append(ContextChunk.from_text(fid, doc, source))
            if chunks:
                self._ctx_router = ContextRouter(chunks)
                self._ctx_router_fact_count = fact_count
                print('[memory] Context router rebuilt with %d chunk(s).' % len(chunks))
        except Exception as exc:
            print('[memory] Context router build failed: %s' % exc)
            self._ctx_router = None

    def _invalidate_router(self):
        # Signal that the router needs a rebuild on next retrieve_relevant call.
        with self._ctx_router_lock:
            self._ctx_router_fact_count = -1

    # ------------------------------------------------------------------
    # Long-term memory -- retrieval
    # ------------------------------------------------------------------

    def retrieve_relevant(self, query, top_k=None):
        k = top_k if top_k is not None else config.MEMORY_TOP_K

        if not self._chroma_ok or self._collection is None:
            return self._fallback_all_facts(query)

        router = self._get_router()
        if router is not None:
            try:
                result = router.route(query)
                level = result.context_level

                if level == 'none':
                    return ''

                if level == 'tiny':
                    k = min(1, k)

                elif level in ('selected', 'full') and result.selected_chunk_ids:
                    fetched = self._collection.get(
                        ids=result.selected_chunk_ids,
                        include=['documents'],
                    )
                    docs = fetched.get('documents', [])
                    if docs:
                        return '[Memory]\n' + '\n'.join('- ' + d for d in docs)

            except Exception as exc:
                print('[memory] Router error, falling back to chromadb: %s' % exc)

        try:
            count = self._collection.count()
            if count == 0:
                return ''
            results = self._collection.query(
                query_texts=[query],
                n_results=min(k, count),
                where={'archived': {'$eq': False}},
                include=['documents', 'metadatas', 'distances'],
            )
            docs = results.get('documents', [[]])[0]
            dists = results.get('distances', [[]])[0]
            if not docs:
                return ''
            max_dist = config.MEMORY_RELEVANCE_MAX_DISTANCE
            filtered = [
                doc for doc, dist in zip(docs, dists)
                if dist <= max_dist or _lexical_overlap(query, doc) > 0
            ]
            if not filtered:
                return ''
            lines = ['- ' + doc for doc in filtered[:k]]
            return '[Memory]\n' + '\n'.join(lines)
        except Exception as exc:
            print('[memory] Retrieval error: %s' % exc)
            return ''

    def _fallback_all_facts(self, query: str = "") -> str:
        """Return all active facts as a plain list when chromadb is unavailable."""
        if not os.path.exists(_FALLBACK_PATH):
            return ""
        try:
            with open(_FALLBACK_PATH, encoding="utf-8") as f:
                facts: list[dict] = json.load(f)
            active = [
                fa for fa in facts
                if not fa.get("archived") and _lexical_overlap(query, fa.get("text", "")) > 0
            ]
            if not active:
                active = [fa for fa in facts if not fa.get("archived")][:max(1, config.MEMORY_TOP_K)]
            if not active:
                return ""
            return "[Memory]\n" + "\n".join(f"- {fa['text']}" for fa in active)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Long-term memory -” periodic consolidation
    # ------------------------------------------------------------------

    def _schedule_consolidation(self) -> None:
        if not config.MEMORY_AUTO_CONSOLIDATE:
            return
        interval_s = config.MEMORY_CONSOLIDATION_INTERVAL * 60
        self._consolidation_timer = threading.Timer(
            interval_s, self._consolidation_tick
        )
        self._consolidation_timer.daemon = True
        self._consolidation_timer.start()

    def _sync_consolidation_timer(self) -> None:
        if config.MEMORY_AUTO_CONSOLIDATE:
            if self._consolidation_timer is None:
                self._schedule_consolidation()
            return
        if self._consolidation_timer:
            self._consolidation_timer.cancel()
            self._consolidation_timer = None

    def _consolidation_tick(self) -> None:
        try:
            self._consolidate()
        except Exception as exc:
            print(f"[memory] Consolidation error: {exc}")
        finally:
            self._consolidation_timer = None
            self._sync_consolidation_timer()

    def _consolidate(self) -> None:
        """
        Extract atomic facts from the current STM turns via the memory LLM
        and upsert them into the LTM store.
        """
        import re

        with self._stm_lock:
            raw = [e for e in self._stm if e.get("type") == "turn"]

        if len(raw) < 2:
            return

        turns_text = "\n".join(
            f"User: {t['role_user']}\nAssistant: {t['role_assistant']}"
            for t in raw
        )
        prompt = _SUMMARIZER_PROMPT.format(turns=turns_text)
        response = self._call_memory_llm(prompt, max_tokens=600).strip()

        # Parse JSON -” tolerant of extra prose around the array
        facts: list[dict] = []
        try:
            facts = json.loads(response)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", response, re.DOTALL)
            if m:
                try:
                    facts = json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            if not facts:
                print("[memory] Consolidation: could not parse LLM output.")
                return

        if not isinstance(facts, list):
            return

        count = 0
        for item in facts:
            if isinstance(item, dict) and item.get("text"):
                cat = item.get("category", "personal")
                if cat not in _CATEGORIES:
                    cat = "general"
                if self._upsert_fact(item["text"], cat, source="summarizer"):
                    count += 1

        print(f"[memory] Consolidation complete -” {count} fact(s) extracted.")

    # ------------------------------------------------------------------
    # Long-term memory -” upsert with conflict resolution
    # ------------------------------------------------------------------

    def _upsert_fact(
        self, text: str, category: str, source: str = "summarizer"
    ) -> bool:
        """
        Insert a fact into LTM.  If a semantically similar fact already exists
        (cosine similarity â‰¥ 0.85, i.e. distance < 0.15) it is archived and
        the new fact replaces it.
        """
        text = _normalize_fact_text(text)
        if not _is_memory_worthy_fact(text, source=source):
            print(f"[memory] Ignored low-value fact candidate: {text!r}")
            return False

        if not self._chroma_ok or self._collection is None:
            self._fallback_upsert(text, category, source)
            return True

        now = _now_iso()

        # Conflict check -” only if the collection is non-empty
        try:
            count = self._collection.count()
            if count > 0:
                results = self._collection.query(
                    query_texts=[text],
                    n_results=min(3, count),
                    where={"archived": {"$eq": False}},
                    include=["documents", "metadatas", "distances"],
                )
                ids: list[str] = results.get("ids", [[]])[0]
                dists: list[float] = results.get("distances", [[]])[0]
                docs_list: list[str] = results.get("documents", [[]])[0]
                metas: list[dict] = results.get("metadatas", [[]])[0]

                for i, (fact_id, dist) in enumerate(zip(ids, dists)):
                    if dist < 0.08:
                        archived_meta = dict(metas[i])
                        archived_meta["last_seen"] = now
                        self._collection.update(
                            ids=[fact_id],
                            metadatas=[archived_meta],
                        )
                        return False
                    if dist < 0.15:          # similarity > 0.85
                        archived_meta = dict(metas[i])
                        archived_meta["archived"] = True
                        archived_meta["archived_superseded_by"] = text
                        self._collection.update(
                            ids=[fact_id],
                            metadatas=[archived_meta],
                        )
                        print(
                            f"[memory] Archived similar fact "
                            f"(dist={dist:.3f}): {docs_list[i]!r}"
                        )
        except Exception as exc:
            print(f"[memory] Conflict-check error: {exc}")

        fact_id = str(uuid.uuid4())
        try:
            self._collection.add(
                documents=[text],
                ids=[fact_id],
                metadatas=[{
                    "id": fact_id,
                    "category": category,
                    "source": source,
                    "created_at": now,
                    "last_seen": now,
                    "archived": False,
                }],
            )
            self._invalidate_router()
            return True
        except Exception as exc:
            print(f"[memory] Upsert error: {exc}")
            return False

    def _fallback_upsert(
        self, text: str, category: str, source: str
    ) -> None:
        text = _normalize_fact_text(text)
        facts: list[dict] = []
        if os.path.exists(_FALLBACK_PATH):
            try:
                with open(_FALLBACK_PATH, encoding="utf-8") as f:
                    facts = json.load(f)
            except Exception:
                facts = []

        now = _now_iso()
        for fact in facts:
            if fact.get("archived"):
                continue
            existing = fact.get("text", "")
            if existing.lower() == text.lower() or _lexical_overlap(existing, text) >= max(3, min(6, len(text.split()) // 2)):
                fact["last_seen"] = now
                fact["category"] = category
                fact["source"] = source
                break
        else:
            facts.append({
            "id": str(uuid.uuid4()),
            "text": text,
            "category": category,
            "source": source,
            "created_at": now,
            "last_seen": now,
            "archived": False,
            })
        with open(_FALLBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(facts, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Viewer API -” read
    # ------------------------------------------------------------------

    def get_all_facts(self) -> list[dict]:
        """Return all non-archived facts for the memory viewer."""
        if not self._chroma_ok or self._collection is None:
            return self._fallback_get_all()
        try:
            results = self._collection.get(
                where={"archived": {"$eq": False}},
                include=["documents", "metadatas"],
            )
            facts: list[dict] = []
            for doc, meta, fid in zip(
                results.get("documents", []),
                results.get("metadatas", []),
                results.get("ids", []),
            ):
                facts.append({
                    "id": fid,
                    "text": doc,
                    "category": meta.get("category", "personal"),
                    "source": meta.get("source", "summarizer"),
                    "created_at": meta.get("created_at", ""),
                    "last_seen": meta.get("last_seen", ""),
                })
            return facts
        except Exception as exc:
            print(f"[memory] get_all_facts error: {exc}")
            return []

    def _fallback_get_all(self) -> list[dict]:
        if not os.path.exists(_FALLBACK_PATH):
            return []
        try:
            with open(_FALLBACK_PATH, encoding="utf-8") as f:
                facts = json.load(f)
            return [fa for fa in facts if not fa.get("archived")]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Viewer API -” write
    # ------------------------------------------------------------------

    def delete_fact(self, fact_id: str) -> None:
        """Hard-delete a fact (viewer action -” user explicitly removed it)."""
        if not self._chroma_ok or self._collection is None:
            self._fallback_delete(fact_id)
            return
        try:
            self._collection.delete(ids=[fact_id])
            self._invalidate_router()
        except Exception as exc:
            print(f"[memory] delete_fact error: {exc}")

    def update_fact(
        self,
        fact_id: str,
        new_text: str,
        new_category: Optional[str] = None,
    ) -> None:
        """Update text and/or category of a fact (viewer action)."""
        if not self._chroma_ok or self._collection is None:
            self._fallback_update(fact_id, new_text, new_category)
            return
        try:
            existing = self._collection.get(
                ids=[fact_id],
                include=["documents", "metadatas"],
            )
            if not existing.get("ids"):
                return
            meta = dict(existing["metadatas"][0])
            meta["last_seen"] = _now_iso()
            if new_category and new_category in _CATEGORIES:
                meta["category"] = new_category
            self._collection.update(
                ids=[fact_id],
                documents=[new_text],
                metadatas=[meta],
            )
            self._invalidate_router()
        except Exception as exc:
            print(f"[memory] update_fact error: {exc}")

    def add_fact_manual(self, text: str, category: str) -> None:
        """Add a fact directly from the viewer (manual entry)."""
        if category not in _CATEGORIES:
            category = "general"
        self._upsert_fact(text.strip(), category, source="manual")

    def prune_low_value_facts(self) -> int:
        """
        Archive stored facts that fail the current memory-quality filter.
        This is intentionally conservative for manual/explicit facts.
        """
        if not self._chroma_ok or self._collection is None:
            return self._fallback_prune_low_value()
        try:
            results = self._collection.get(
                where={"archived": {"$eq": False}},
                include=["documents", "metadatas"],
            )
            ids: list[str] = results.get("ids", [])
            docs: list[str] = results.get("documents", [])
            metas: list[dict] = results.get("metadatas", [])
            archived = 0
            for fact_id, doc, meta in zip(ids, docs, metas):
                source = str(meta.get("source", "summarizer"))
                if _is_memory_worthy_fact(doc, source=source):
                    continue
                new_meta = dict(meta)
                new_meta["archived"] = True
                new_meta["archived_reason"] = "low_value_cleanup"
                self._collection.update(ids=[fact_id], metadatas=[new_meta])
                archived += 1
            return archived
        except Exception as exc:
            print(f"[memory] prune_low_value_facts error: {exc}")
            return 0

    def _fallback_prune_low_value(self) -> int:
        if not os.path.exists(_FALLBACK_PATH):
            return 0
        try:
            with open(_FALLBACK_PATH, encoding="utf-8") as f:
                facts = json.load(f)
            archived = 0
            for fact in facts:
                if fact.get("archived"):
                    continue
                source = str(fact.get("source", "summarizer"))
                if _is_memory_worthy_fact(fact.get("text", ""), source=source):
                    continue
                fact["archived"] = True
                fact["archived_reason"] = "low_value_cleanup"
                archived += 1
            if archived:
                with open(_FALLBACK_PATH, "w", encoding="utf-8") as f:
                    json.dump(facts, f, indent=2, ensure_ascii=False)
            return archived
        except Exception:
            return 0

    def _fallback_delete(self, fact_id: str) -> None:
        if not os.path.exists(_FALLBACK_PATH):
            return
        try:
            with open(_FALLBACK_PATH, encoding="utf-8") as f:
                facts = json.load(f)
            facts = [fa for fa in facts if fa.get("id") != fact_id]
            with open(_FALLBACK_PATH, "w", encoding="utf-8") as f:
                json.dump(facts, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _fallback_update(
        self, fact_id: str, new_text: str, new_category: Optional[str]
    ) -> None:
        if not os.path.exists(_FALLBACK_PATH):
            return
        try:
            with open(_FALLBACK_PATH, encoding="utf-8") as f:
                facts = json.load(f)
            for fa in facts:
                if fa.get("id") == fact_id:
                    fa["text"] = new_text
                    if new_category:
                        fa["category"] = new_category
                    fa["last_seen"] = _now_iso()
                    break
            with open(_FALLBACK_PATH, "w", encoding="utf-8") as f:
                json.dump(facts, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Blocking LLM call for memory operations
    # ------------------------------------------------------------------

    def _call_memory_llm(self, prompt: str, max_tokens: int = 600) -> str:
        """
        Synchronous LLM call using MEMORY_LLM_PROVIDER / MEMORY_LLM_MODEL.
        Used for consolidation and mid-session compression.
        Runs on background threads -” never call from Qt main thread.
        """
        provider = config.MEMORY_LLM_PROVIDER.lower()
        model    = config.MEMORY_LLM_MODEL

        try:
            if provider in ("groq", "openai", "google"):
                from openai import OpenAI
                if provider == "groq":
                    client = OpenAI(
                        api_key=config.GROQ_API_KEY,
                        base_url="https://api.groq.com/openai/v1",
                    )
                elif provider == "google":
                    client = OpenAI(
                        api_key=config.GOOGLE_API_KEY,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    )
                else:
                    client = OpenAI(api_key=config.OPENAI_API_KEY)

                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                return resp.choices[0].message.content or ""

            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
                resp = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text if resp.content else ""

            print(f"[memory] Unknown MEMORY_LLM_PROVIDER: {provider!r}")
            return ""
        except Exception as exc:
            print(f"[memory] LLM call failed: {exc}")
            return ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Cancel background timers on app exit.  Safe to call multiple times."""
        if self._consolidation_timer:
            self._consolidation_timer.cancel()
            self._consolidation_timer = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[MemoryManager] = None


def get_manager() -> MemoryManager:
    """Return the process-wide MemoryManager, creating it on first call."""
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager

