import os
import time
import logging
import re
import json
from typing import Any, Dict, List, Optional

_cosmos_client = None
_cosmos_db = None


def _quiet_azure_sdk_logs() -> None:
    try:
        sdk_level = "WARNING"
        level = getattr(logging, sdk_level, logging.WARNING)
    except Exception:
        level = logging.WARNING
    # Quiet most Azure SDK loggers
    for name in (
        "azure",
        "azure.cosmos",
        "azure.core.pipeline.policies.http_logging_policy",
    ):
        logging.getLogger(name).setLevel(level)


def _init_cosmos() -> None:
    global _cosmos_client, _cosmos_db
    if _cosmos_client is not None and _cosmos_db is not None:
        return
    endpoint = os.getenv("AZURE_COSMOS_ENDPOINT")
    key = os.getenv("AZURE_COSMOS_KEY")
    db_name = os.getenv("AZURE_COSMOS_DATABASE_NAME")
    if not endpoint or not key or not db_name:
        raise RuntimeError(
            "Missing Cosmos settings: AZURE_COSMOS_ENDPOINT, AZURE_COSMOS_KEY, AZURE_COSMOS_DATABASE_NAME")
    try:
        from azure.cosmos import CosmosClient
    except Exception as e:
        raise RuntimeError(
            "The 'azure-cosmos' package is required. Add it to requirements.txt and deploy.") from e
    _quiet_azure_sdk_logs()
    # For local emulator over HTTPS with self-signed cert, allow disabling TLS verification
    verify = True
    try:
        verify_env = os.getenv("COSMOS_VERIFY_TLS", "true").lower()
        if (endpoint.startswith("https://localhost") or endpoint.startswith("https://127.0.0.1")) and verify_env in ("0", "false", "no", "off"):
            verify = False
    except Exception:
        verify = True
    logging.debug(
        f"Initializing Cosmos client endpoint={endpoint} verify_tls={verify}")
    try:
        _cosmos_client = CosmosClient(endpoint, key, connection_verify=verify)
        _cosmos_db = _cosmos_client.create_database_if_not_exists(db_name)
    except Exception as e:
        msg = str(e)
        # Common case with local emulator: TLS disabled but endpoint is https -> WRONG_VERSION_NUMBER
        if endpoint.startswith("https://localhost") and "WRONG_VERSION_NUMBER" in msg.upper():
            fallback = "http://" + endpoint[len("https://"):]
            logging.debug(
                f"HTTPS handshake failed with WRONG_VERSION_NUMBER. Retrying Cosmos with HTTP endpoint={fallback}")
            _cosmos_client = CosmosClient(fallback, key)
            _cosmos_db = _cosmos_client.create_database_if_not_exists(db_name)
        else:
            raise


def _sanitize_container_name(raw_user_id: str) -> str:
    # Cosmos container name: letters, numbers, dash, underscore only, max 255
    base = "mem_" + (raw_user_id or "unknown")
    safe = ''.join(ch if ch.isalnum() or ch in (
        '-', '_') else '_' for ch in base)
    return safe[:255]


def _sanitize_text_for_cosmos(raw: str) -> str:
    r"""Sanitize text to avoid Cosmos JSON parser errors like 'unsupported Unicode escape sequence'.

    - Remove any surrogate code points (U+D800..U+DFFF)
    - Neutralize common escape-like sequences (``\u``, ``\U``, ``\x``)
    - Escape any stray backslashes not followed by a valid JSON escape char
    - Ensure the string is valid UTF-8 by replacing undecodable bytes
    """
    try:
        text = str(raw or "")
    except Exception:
        return ""
    try:
        # Remove surrogate code points
        text = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))
    except Exception:
        pass
    try:
        # Retrait inconditionnel du symbole degré (demande utilisateur) sans variable d'env supplémentaire
        if "°" in text:
            text = text.replace("°", "")
    except Exception:
        pass
    try:
        # Cosmos rejects strings with unrecognized escape sequences, returning
        # "Unsupported Unicode escape sequence". Double the backslash for
        # potential escape patterns so the literal text is stored.
        text = (
            text.replace("\\u", "\\\\u")
                .replace("\\U", "\\\\U")
                .replace("\\x", "\\\\x")
        )
        # Escape any remaining backslash not followed by a valid JSON escape char
        text = re.sub(r"\\(?![\"\\/bfnrtuU])", r"\\\\", text)
    except Exception:
        pass
    try:
        # Force to valid UTF-8; replace invalid sequences
        text = text.encode(
            "utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        pass
    return text


def _sanitize_json_for_cosmos(value: Any) -> Any:
    """Recursively sanitize all strings in a JSON-serializable structure for Cosmos."""
    try:
        if isinstance(value, str):
            return _sanitize_text_for_cosmos(value)
        if isinstance(value, list):
            return [_sanitize_json_for_cosmos(v) for v in value]
        if isinstance(value, dict):
            return {k: _sanitize_json_for_cosmos(v) for k, v in value.items()}
        return value
    except Exception:
        return value


def _scan_invalid_escape_sequences(value: Any, path: str = "$") -> List[str]:
    r"""Return list of JSON-path-like strings where a raw single backslash escape appears.

    We flag patterns like: \uXXXX with <4 hex digits, bare \u not followed by hex, or stray \x / \U.
    Only single backslash (not doubled) is considered unsafe because doubled will survive as literal.
    """
    issues: List[str] = []
    try:
        if isinstance(value, str):
            patterns = [
                r"(?<!\\)\\u(?![0-9a-fA-F]{4})",
                r"(?<!\\)\\x(?![0-9a-fA-F]{2})",
                r"(?<!\\)\\U(?![0-9a-fA-F]{8})",
            ]
            for pat in patterns:
                if re.search(pat, value):
                    snippet = value
                    if len(snippet) > 80:
                        snippet = snippet[:77] + "…"
                    issues.append(f"{path}: {pat} -> '{snippet}'")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                issues.extend(
                    _scan_invalid_escape_sequences(v, f"{path}[{i}]"))
        elif isinstance(value, dict):
            for k, v in value.items():
                key = str(k)
                issues.extend(
                    _scan_invalid_escape_sequences(v, f"{path}.{key}"))
    except Exception:
        pass
    return issues


def _final_cosmos_scrub(doc: Dict[str, Any]) -> Dict[str, Any]:
    r"""Final defensive scrub before sending to Cosmos.

    Steps:
    1. Recursive sanitization of all strings
    2. Serialize to JSON and neutralize any remaining raw ``\u`` sequences
       that aren't followed by 4 hex digits (source of Cosmos 'unsupported Unicode escape sequence').
    3. Re-load into a dict to hand a clean structure to the SDK.
    """
    try:
        def _neutralize_invalid_unicode_escapes_string(s: str) -> str:
            r"""Replace any invalid / short / malformed "\\u" style escape with a literal sequence.

                        Patterns handled (single backslash forms only):
                            - \u (nothing after)
                            - \u<1-3 hex>
                            - \u<non-hex>
                        We convert them to a double-backslash form so Cosmos JSON parser treats them as plain text.
                        We are careful to NOT touch already double escaped patterns (\\uXXXX) so we do a negative lookbehind.
                        """
            try:
                # Replace \u not followed by 4 hex digits
                s = re.sub(r"(?<!\\)\\u(?![0-9a-fA-F]{4})", r"\\\\u", s)
                # Also catch partial hex sequences (1-3 hex digits then non-hex boundary)
                s = re.sub(
                    r"(?<!\\)\\u([0-9a-fA-F]{1,3})(?=\b|[^0-9a-fA-F])", lambda m: "\\\\u" + m.group(1), s)
            except Exception:
                pass
            return s

        def _neutralize_walk(value: Any) -> Any:
            if isinstance(value, str):
                return _neutralize_invalid_unicode_escapes_string(value)
            if isinstance(value, list):
                return [_neutralize_walk(v) for v in value]
            if isinstance(value, dict):
                return {k: _neutralize_walk(v) for k, v in value.items()}
            return value

        # First general sanitization
        sanitized = _sanitize_json_for_cosmos(doc)
        # Targeted neutralization pass
        sanitized = _neutralize_walk(sanitized)

        try:
            raw_json = json.dumps(sanitized, ensure_ascii=False)
        except Exception:
            raw_json = json.dumps(sanitized, ensure_ascii=True)

        raw_json = re.sub(
                r"(?<!\\)(\\u[0-9a-fA-F]{4})", r"\\\\\\1", raw_json)

        try:
            return json.loads(raw_json)
        except Exception:
            # Last resort: escape every single backslash then parse
            try:
                aggressive = raw_json.replace("\\", "\\\\")
                return json.loads(aggressive)
            except Exception:
                return sanitized
    except Exception:
        return doc


def _get_user_container(user_id: str):
    _init_cosmos()
    try:
        from azure.cosmos import PartitionKey
    except Exception:
        # Should not happen if _init_cosmos succeeded
        raise
    container_name = _sanitize_container_name(user_id)
    logging.debug(
        f"Ensuring Cosmos container id={container_name} for user_id={user_id}")
    container = _cosmos_db.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/id"),
        default_ttl=int(os.getenv("COSMOS_DEFAULT_TTL_SECONDS",
                        str(60 * 24 * 60 * 60)))  # 60 days
    )
    return container


def _derive_short_title_from_text(text: str, max_length: int = 60, max_words: int = 8) -> str:
    """Derive a very short, user-friendly title from the first user message.

    Keeps it compact by limiting both characters and words, strips newlines and
    excessive whitespace. Falls back to a generic timestamped title when empty.
    """
    try:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("empty text")
        # Normalize whitespace and remove line breaks
        normalized = " ".join(raw.replace(
            "\n", " ").replace("\r", " ").split())
        # Trim by words first
        parts = normalized.split(" ")
        if len(parts) > max_words:
            normalized = " ".join(parts[:max_words])
        # Then trim by length
        if len(normalized) > max_length:
            normalized = normalized[: max(0, max_length - 1)].rstrip() + "…"
        # Capitalize first letter if not already
        if normalized and normalized[0].islower():
            normalized = normalized[0].upper() + normalized[1:]
        return normalized or ""
    except Exception:
        pass
    # Fallback: timestamped generic title
    try:
        now_iso = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
        return f"Conversation {now_iso}"
    except Exception:
        return "Conversation"


def upsert_memory(user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    if not user_id:
        raise ValueError("user_id is required")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc.setdefault("updatedAt", now_iso)
    container = _get_user_container(user_id)
    return container.upsert_item(doc)


def list_memories(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    if not user_id:
        raise ValueError("user_id is required")
    container = _get_user_container(user_id)
    query = "SELECT c.id, c.type, c.title, c.createdAt, c.updatedAt FROM c ORDER BY c._ts DESC"
    items = list(container.query_items(
        query=query, enable_cross_partition_query=True))
    return items[: max(1, min(limit, 200))]


def get_memory(user_id: str, memory_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        raise ValueError("user_id is required")
    if not memory_id:
        raise ValueError("memory_id is required")
    container = _get_user_container(user_id)
    try:
        return container.read_item(item=memory_id, partition_key=memory_id)
    except Exception:
        return None


def list_conversation_docs(user_id: str, conversation_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    container = _get_user_container(user_id)
    query = (
        "SELECT c.id, c.prompt, c.output_text, c.createdAt, c._ts "
        "FROM c WHERE c.conversation_id = @cid ORDER BY c._ts ASC"
    )
    params = [{"name": "@cid", "value": conversation_id}]
    items = list(container.query_items(
        query=query, parameters=params, enable_cross_partition_query=True))
    return items[: max(1, min(limit, 100))]


def get_conversation_messages(user_id: str, conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return messages array from a single conversation document identified by its id.

    Falls back to empty list if the document does not exist or contains no messages.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    doc = get_memory(user_id, conversation_id)
    if not doc:
        return []
    messages = doc.get("messages") or []
    # Return the last N messages
    return messages[-max(1, min(limit, 200)):]


def upsert_conversation_turn(user_id: str, conversation_id: str, user_text: str, assistant_text: str) -> Dict[str, Any]:
    """Append a new turn (user then assistant) into a single conversation doc with id == conversation_id.

    Creates the document if missing, using the provided conversation_id as the document id
    and storing it explicitly under the "conversation_id" field for querying.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    perf_enabled = os.getenv("MEMORY_PERF_LOG", "0").lower() in (
        "1", "true", "yes", "on")
    t0 = time.perf_counter()
    container = _get_user_container(user_id)
    t_after_container = time.perf_counter()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Try to read existing doc by provided conversation_id
    doc = get_memory(user_id, conversation_id)
    t_after_read = time.perf_counter()
    if not doc:
        # Create new conversation document with id == conversation_id
        # Try to derive a numeric memory_id from the trailing segment of conversation_id when possible
        derived_memory_id: Optional[int] = None
        try:
            if "_" in conversation_id:
                tail = conversation_id.split("_")[-1]
                derived_memory_id = int(tail)
        except Exception:
            derived_memory_id = None
        if derived_memory_id is None:
            try:
                derived_memory_id = get_next_memory_id(user_id)
            except Exception:
                derived_memory_id = 1
        doc = {
            "id": conversation_id,
            "conversation_id": conversation_id,
            "type": "conversation",
            "user_id": user_id,
            "memory_id": derived_memory_id,
            "created_at": now_iso,
            "updated_at": now_iso,
            "createdAt": now_iso,
            "updatedAt": now_iso,
            "messages": [],
            "ttl": int(os.getenv("COSMOS_DEFAULT_TTL_SECONDS", str(60 * 24 * 60 * 60)))
        }
        # Seed title from the very first user input when creating the conversation
        try:
            if user_text:
                doc["title"] = _sanitize_text_for_cosmos(
                    _derive_short_title_from_text(user_text))
        except Exception:
            pass
    else:
        # Backfill title for legacy documents missing it
        if not doc.get("title"):
            try:
                source_text: Optional[str] = None
                # Prefer the first user message already present
                for msg in (doc.get("messages") or []):
                    if (msg.get("role") or "").strip() == "user":
                        src = (msg.get("content") or "").strip()
                        if src:
                            source_text = src
                            break
                # If still none, use current user_text
                if not source_text and user_text:
                    source_text = user_text
                if source_text:
                    doc["title"] = _sanitize_text_for_cosmos(
                        _derive_short_title_from_text(source_text))
            except Exception:
                pass
    # Append user and assistant messages
    if user_text:
        doc.setdefault("messages", []).append({
            "role": "user",
            "content": _sanitize_text_for_cosmos(user_text),
            "timestamp": now_iso,
        })
    if assistant_text:
        doc.setdefault("messages", []).append({
            "role": "assistant",
            "content": _sanitize_text_for_cosmos(assistant_text),
            "timestamp": now_iso,
        })
    doc["updated_at"] = now_iso
    doc["updatedAt"] = now_iso
    # Ensure conversation_id property is present for query-based retrieval
    doc.setdefault("conversation_id", conversation_id)
    # Sanitize the entire document to avoid Cosmos JSON parser errors
    t_before_scrub = time.perf_counter()
    doc = _final_cosmos_scrub(doc)
    t_after_scrub = time.perf_counter()
    logging.debug(
        f"Upserting conversation turn: user_id={user_id} doc_id={doc.get('id')} msgs={len(doc.get('messages') or [])}"
    )
    # Diagnostic scan for residual unsafe escape sequences
    invalid_paths = _scan_invalid_escape_sequences(doc)
    if invalid_paths:
        logging.warning(
            "Detected potential unsafe escape sequences before Cosmos upsert: %s", invalid_paths)
        # Aggressive fallback: JSON round-trip with full backslash doubling for problematic strings
        try:
            def _aggressive(value: Any) -> Any:
                if isinstance(value, str):
                    return re.sub(r"(?<!\\)\\([uUxX])", r"\\\\\\1", value)
                if isinstance(value, list):
                    return [_aggressive(v) for v in value]
                if isinstance(value, dict):
                    return {k: _aggressive(v) for k, v in value.items()}
                return value
            doc = _aggressive(doc)
        except Exception:
            pass
    try:
        saved = container.upsert_item(doc)
        t_after_upsert = time.perf_counter()
        if perf_enabled:
            try:
                logging.info(
                    "MEMORY_PERF user=%s conv=%s timings ms: container=%.1f read=%.1f build=%.1f scrub=%.1f upsert=%.1f total=%.1f size_chars=%d",  # noqa: E501
                    user_id,
                    conversation_id,
                    (t_after_container - t0) * 1000.0,
                    (t_after_read - t_after_container) * 1000.0,
                    (t_before_scrub - t_after_read) * 1000.0,
                    (t_after_scrub - t_before_scrub) * 1000.0,
                    (t_after_upsert - t_after_scrub) * 1000.0,
                    (t_after_upsert - t0) * 1000.0,
                    len(json.dumps(doc, ensure_ascii=False)
                        ) if isinstance(doc, dict) else -1,
                )
            except Exception:
                pass
    except Exception as e:
        msg = str(e)
        if "unsupported Unicode escape sequence" in msg.lower():
            # Fallback: rebuild doc with every string ASCII-escaped via json.dumps ensure_ascii=True
            logging.warning(
                "Cosmos upsert unicode error detected; attempting ASCII escape fallback")

            def _ascii_escape(value: Any) -> Any:
                if isinstance(value, str):
                    try:
                        # json.dumps returns quoted string; strip quotes
                        return json.dumps(value, ensure_ascii=True)[1:-1]
                    except Exception:
                        return value.encode('utf-8', 'backslashreplace').decode('ascii', 'ignore')
                if isinstance(value, list):
                    return [_ascii_escape(v) for v in value]
                if isinstance(value, dict):
                    return {k: _ascii_escape(v) for k, v in value.items()}
                return value
            ascii_doc = _ascii_escape(doc)
            # Mark doc so we can identify fallback docs later
            if isinstance(ascii_doc, dict):
                ascii_doc.setdefault("_encoding", "ascii_escaped")
            try:
                saved = container.upsert_item(ascii_doc)
                logging.info(
                    "Cosmos upsert succeeded after ASCII escape fallback")
                return saved
            except Exception:
                logging.exception(
                    "Fallback upsert after ASCII escape also failed")
        snippet = ""
        try:
            snippet = json.dumps(doc, ensure_ascii=False)[:200]
        except Exception:
            try:
                snippet = str(doc)[:200]
            except Exception:
                snippet = "<unavailable>"
        logging.exception(
            "Failed to upsert conversation turn: user_id=%s conversation_id=%s doc_snippet=%s",
            user_id,
            conversation_id,
            snippet,
        )
        raise
    return saved


def get_next_memory_id(user_id: str) -> int:
    if not user_id:
        raise ValueError("user_id is required")
    container = _get_user_container(user_id)
    # Get highest numeric memory_id across docs (primary strategy)
    query_max_memid = "SELECT VALUE MAX(c.memory_id) FROM c WHERE IS_NUMBER(c.memory_id)"
    items = list(container.query_items(
        query=query_max_memid, enable_cross_partition_query=True))
    try:
        max_by_field = int(items[0]) if items and items[0] is not None else 0
    except Exception:
        max_by_field = 0

    # Fallback: also derive from id suffix if pattern *_<number> exists (handles legacy docs)
    max_by_id_suffix = 0
    try:
        id_query = "SELECT TOP 200 c.id FROM c ORDER BY c._ts DESC"
        id_items = list(container.query_items(
            query=id_query, enable_cross_partition_query=True))
        for it in id_items:
            doc_id = it.get("id") if isinstance(it, dict) else None
            if not isinstance(doc_id, str):
                continue
            # Extract trailing number if any
            if "_" in doc_id:
                tail = doc_id.rsplit("_", 1)[-1]
                try:
                    n = int(tail)
                    if n > max_by_id_suffix:
                        max_by_id_suffix = n
                except Exception:
                    continue
    except Exception:
        max_by_id_suffix = 0

    max_id = max(max_by_field, max_by_id_suffix)
    next_id = max_id + 1
    logging.debug(
        f"Computed next memory_id for user_id={user_id}: next={next_id} (max_field={max_by_field}, max_id_suffix={max_by_id_suffix})"
    )
    return next_id
