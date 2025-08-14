import os
import time
import logging
from typing import Any, Dict, List, Optional

_cosmos_client = None
_cosmos_db = None


def _quiet_azure_sdk_logs() -> None:
    try:
        sdk_level = os.getenv("AZURE_SDK_LOG_LEVEL", "WARNING").upper()
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
        raise RuntimeError("Missing Cosmos settings: AZURE_COSMOS_ENDPOINT, AZURE_COSMOS_KEY, AZURE_COSMOS_DATABASE_NAME")
    try:
        from azure.cosmos import CosmosClient
    except Exception as e:
        raise RuntimeError("The 'azure-cosmos' package is required. Add it to requirements.txt and deploy.") from e
    _quiet_azure_sdk_logs()
    _cosmos_client = CosmosClient(endpoint, key)
    _cosmos_db = _cosmos_client.create_database_if_not_exists(db_name)


def _sanitize_container_name(raw_user_id: str) -> str:
    # Cosmos container name: letters, numbers, dash, underscore only, max 255
    base = "mem_" + (raw_user_id or "unknown")
    safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in base)
    return safe[:255]


def _get_user_container(user_id: str):
    _init_cosmos()
    try:
        from azure.cosmos import PartitionKey
    except Exception:
        # Should not happen if _init_cosmos succeeded
        raise
    container_name = _sanitize_container_name(user_id)
    container = _cosmos_db.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/id"),
        default_ttl=int(os.getenv("COSMOS_DEFAULT_TTL_SECONDS", str(60 * 24 * 60 * 60)))  # 60 days
    )
    return container


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
    query = "SELECT c.id, c.type, c.model, c.createdAt, c.updatedAt FROM c ORDER BY c._ts DESC"
    items = list(container.query_items(query=query, enable_cross_partition_query=True))
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
    items = list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
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
    return messages[-max(1, min(limit, 200)) :]


def upsert_conversation_turn(user_id: str, conversation_id: str, user_text: str, assistant_text: str) -> Dict[str, Any]:
    """Append a new turn (user then assistant) into a single conversation doc with id == conversation_id.

    Creates the document if missing, preserving consistent id usage across the conversation.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    container = _get_user_container(user_id)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Try to read existing doc
    doc = get_memory(user_id, conversation_id)
    if not doc:
        # Create new conversation document
        # Infer memory_id and canonical id like <user_id>_<memory_id>
        memory_id = get_next_memory_id(user_id)
        canonical_id = f"{user_id}_{memory_id}"
        doc = {
            "id": canonical_id,
            "type": "conversation",
            "user_id": user_id,
            "memory_id": memory_id,
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [],
            "ttl": int(os.getenv("COSMOS_DEFAULT_TTL_SECONDS", str(60 * 24 * 60 * 60)))
        }
    # Append user and assistant messages
    if user_text:
        doc["messages"].append({
            "role": "user",
            "content": user_text,
            "timestamp": now_iso,
        })
    if assistant_text:
        doc["messages"].append({
            "role": "assistant",
            "content": assistant_text,
            "timestamp": now_iso,
        })
    doc["updated_at"] = now_iso
    saved = container.upsert_item(doc)
    return saved


def get_next_memory_id(user_id: str) -> int:
    if not user_id:
        raise ValueError("user_id is required")
    container = _get_user_container(user_id)
    # Get highest memory_id among conversation docs
    query = (
        "SELECT TOP 1 VALUE c.memory_id FROM c "
        "WHERE c.type = 'conversation' AND IS_NUMBER(c.memory_id) "
        "ORDER BY c.memory_id DESC"
    )
    items = list(container.query_items(query=query, enable_cross_partition_query=True))
    try:
        max_id = int(items[0]) if items else 0
    except Exception:
        max_id = 0
    return max_id + 1

