import json
import logging
import os
import time
import uuid
import random
from typing import Any, Dict, Optional, List

import azure.functions as func
from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient

from .services.storage import (
    get_storage_clients,
    upload_job_blob,
    get_job_blob,
    upload_sidecar_request,
    get_sidecar_request,
)
from .services.tools import resolve_mcp_config
from .services.conversation import (
    create_llm_client,
    select_model_and_effort,
    build_responses_args,
    run_with_optional_stream,
    run_responses_with_tools,
    build_system_message_text,
)
from .services.tools import get_builtin_tools_config
from .services.memory import (
    list_memories as cosmos_list_memories,
    get_memory as cosmos_get_memory,
    upsert_memory as cosmos_upsert_memory,
    get_next_memory_id as cosmos_get_next_memory_id,
    get_conversation_messages as cosmos_get_conversation_messages,
    upsert_conversation_turn as cosmos_upsert_conversation_turn,
)


bp = func.Blueprint()


_SNIPPET_NAME_PROPERTY_NAME = "snippetname"
_SNIPPET_PROPERTY_NAME = "snippet"
_BLOB_PATH = "snippets/{mcptoolargs." + _SNIPPET_NAME_PROPERTY_NAME + "}.json"
QUEUE_NAME = os.getenv("MCP_JOBS_QUEUE", "mcpjobs")


def _get_result_delay_seconds() -> float:
    try:
        min_delay = float(os.getenv("MCP_RESULT_MIN_DELAY_SECONDS", "2"))
        max_delay = float(os.getenv("MCP_RESULT_MAX_DELAY_SECONDS", "5"))
        if max_delay < min_delay:
            min_delay, max_delay = max_delay, min_delay
        return random.uniform(min_delay, max_delay)
    except Exception:
        return 2.0


def _get_recommended_poll_interval_ms() -> int:
    try:
        min_delay = float(os.getenv("MCP_RESULT_MIN_DELAY_SECONDS", "2"))
        return max(1000, int(min_delay * 1000))
    except Exception:
        return 2000


def _parse_allowed_origins() -> List[str]:
    raw = os.getenv("ALLOWED_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def _preflight_response(req: func.HttpRequest) -> func.HttpResponse:
    origin = req.headers.get("Origin") or req.headers.get("origin")
    allowed = _parse_allowed_origins()
    headers: Dict[str, str] = {
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "content-type,x-functions-key,authorization",
        "Vary": "Origin",
    }
    if origin and (origin in allowed):
        headers["Access-Control-Allow-Origin"] = origin
    return func.HttpResponse(status_code=204, headers=headers)


def _apply_cors(resp: func.HttpResponse, req: func.HttpRequest) -> func.HttpResponse:
    origin = req.headers.get("Origin") or req.headers.get("origin")
    allowed = _parse_allowed_origins()
    if origin and (origin in allowed):
        resp.headers["Access-Control-Allow-Origin"] = origin
        prev_vary = resp.headers.get("Vary")
        resp.headers["Vary"] = f"{prev_vary}, Origin" if prev_vary else "Origin"
    return resp


@bp.route(route="mcp-run", methods=[func.HttpMethod.POST], auth_level=func.AuthLevel.FUNCTION)
def mcp_run(req: func.HttpRequest) -> func.HttpResponse:
    start = time.perf_counter()
    try:
        try:
            body: Dict[str, Any] = req.get_json()
        except Exception:
            body = {}

        qp = getattr(req, 'params', {}) or {}
        prompt = (body.get("prompt") or qp.get("prompt") or "") if isinstance(body, dict) else (qp.get("prompt") or "")
        if not prompt:
            return func.HttpResponse(
                json.dumps({"error": "Missing 'prompt' in request body"}),
                status_code=400,
                mimetype="application/json",
            )

        merged: Dict[str, Any] = dict(body) if isinstance(body, dict) else {}
        if qp.get("model"):
            merged["model"] = qp.get("model")
        if qp.get("mcp_url"):
            merged["mcp_url"] = qp.get("mcp_url")
        if qp.get("require_approval"):
            merged["require_approval"] = qp.get("require_approval")
        if qp.get("allowed_tools") is not None:
            merged["allowed_tools"] = qp.get("allowed_tools")
        if qp.get("stream") is not None:
            merged["stream"] = str(qp.get("stream")).lower() in ("1", "true", "yes", "on")
        if qp.get("debug") is not None:
            merged["debug"] = str(qp.get("debug")).lower() in ("1", "true", "yes", "on")

        model = merged.get("model") or os.getenv("AZURE_OPENAI_MODEL") or "gpt-5-mini"
        mcp_tool_cfg = resolve_mcp_config(merged)

        client = create_llm_client()

        reasoning_effort = (merged.get("reasoning_effort") or os.getenv("DEFAULT_REASONING_EFFORT") or "low").lower()
        # Conversation logic only if user_id provided
        user_id = (merged.get("user_id") or qp.get("user_id") or "").strip()
        conversation_id = (merged.get("conversation_id") or qp.get("conversation_id"))
        conversation_id = str(conversation_id).strip() if conversation_id else None
        new_conversation = False
        input_messages: Optional[List[dict]] = None
        if user_id:
            if not conversation_id:
                try:
                    mem_id = cosmos_get_next_memory_id(user_id)
                except Exception:
                    mem_id = 1
                conversation_id = f"{user_id}_{mem_id}"
                new_conversation = True
            try:
                prior = cosmos_get_conversation_messages(user_id, conversation_id, limit=6)
                if prior:
                    msgs: List[dict] = []
                    for m in prior[-3:]:
                        role = (m.get("role") or "user").strip()
                        content = (m.get("content") or "").strip()
                        if not content:
                            continue
                        if role == "assistant":
                            msgs.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
                        else:
                            msgs.append({"role": "user", "content": [{"type": "input_text", "text": content}]})
                    input_messages = msgs if msgs else None
            except Exception:
                input_messages = None

        responses_args: Dict[str, Any] = build_responses_args(model, prompt, mcp_tool_cfg, reasoning_effort)
        if input_messages is not None:
            system_msg = build_system_message_text()
            responses_args["input"] = (
                [{"role": "system", "content": [{"type": "input_text", "text": system_msg}]}]
                + input_messages
                + [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
            )
        run_id = str(uuid.uuid4())
        server_url_log = (mcp_tool_cfg.get('server_url') if mcp_tool_cfg else None)
        allowed_tools_log = (mcp_tool_cfg.get('allowed_tools') if mcp_tool_cfg else [])
        require_approval_log = (mcp_tool_cfg.get('require_approval') if mcp_tool_cfg else None)
        logging.info(
            f"[mcp-run] start run_id={run_id} model={model} server_url={server_url_log} "
            f"tools={allowed_tools_log} require_approval={require_approval_log} "
            f"prompt_len={len(prompt)}"
        )

        output_text: Optional[str] = None
        # If any classic tools are present, handle tool loop synchronously
        has_classic_tools = bool(responses_args.get("tools")) and (len(get_builtin_tools_config()) > 0)
        if has_classic_tools:
            # Force explicit auto selection to nudge tool usage (string form)
            responses_args["tool_choice"] = "auto"
            # Use a tools-capable model if not explicitly set
            try:
                if not responses_args.get("model"):
                    responses_args["model"] = os.getenv("ORCHESTRATOR_MODEL_TOOLS", "gpt-4.1")
            except Exception:
                pass
        if has_classic_tools:
            output_text, response = run_responses_with_tools(client, responses_args)
            if not output_text:
                try:
                    no_tools_args = dict(responses_args)
                    no_tools_args.pop("tools", None)
                    no_tools_args.pop("tool_choice", None)
                    response = client.responses.create(**no_tools_args)
                    output_text = getattr(response, "output_text", None)
                except Exception:
                    pass
        else:
            if merged.get("stream"):
                try:
                    output_text, response = run_with_optional_stream(client, responses_args, stream=True)
                except Exception:
                    logging.exception(f"[mcp-run] {run_id} streaming error; falling back to non-streaming")
                    output_text, response = run_with_optional_stream(client, responses_args, stream=False)
            else:
                output_text, response = run_with_optional_stream(client, responses_args, stream=False)
        elapsed = time.perf_counter() - start

        payload = {
            "output_text": output_text,
            "model": model,
            "duration_ms": int(elapsed * 1000),
            "run_id": run_id,
        }
        if user_id:
            payload["conversation_id"] = conversation_id
            payload["new_conversation"] = new_conversation
        # Persist brief memory record when Cosmos is configured
        try:
            if user_id and conversation_id:
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
        except Exception:
            pass
        if merged.get("debug"):
            try:
                if mcp_tool_cfg:
                    sanitized = dict(mcp_tool_cfg)
                    if isinstance(sanitized.get("headers"), dict):
                        redacted_headers = {k: "***" for k in sanitized["headers"].keys()}
                        sanitized["headers"] = redacted_headers
                    payload["mcp_tool"] = sanitized
            except Exception:
                pass

        resp = func.HttpResponse(json.dumps(payload, ensure_ascii=False), mimetype="application/json")
        if user_id and conversation_id:
            resp.headers["X-Conversation-Id"] = conversation_id
        return resp
    except Exception as e:
        logging.exception("Error in /api/mcp-run")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


def _get_storage_clients() -> Dict[str, Any]:
    conn_str = os.getenv("AzureWebJobsStorage")
    if conn_str and conn_str.strip().lower().startswith("usedevelopmentstorage=true"):
        conn_str = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
            "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
        )
    if not conn_str:
        raise RuntimeError("Missing AzureWebJobsStorage connection string.")
    return {
        "queue": QueueClient.from_connection_string(conn_str, queue_name=QUEUE_NAME),
        "blob": BlobServiceClient.from_connection_string(conn_str),
        "container": os.getenv("MCP_JOBS_CONTAINER", "jobs"),
    }


@bp.route(route="mcp-enqueue", methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def mcp_enqueue(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        try:
            body: Dict[str, Any] = req.get_json()
        except Exception:
            body = {}

        prompt = (body.get("prompt") or "") if isinstance(body, dict) else ""
        if not prompt:
            return func.HttpResponse(json.dumps({"error": "Missing 'prompt'"}), status_code=400, mimetype="application/json")

        job_id = str(uuid.uuid4())

        storage = _get_storage_clients()
        try:
            storage["queue"].create_queue()
        except Exception:
            pass
        try:
            storage["blob"].create_container(storage["container"])
        except Exception:
            pass

        req_blob = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.req.json")
        req_blob.upload_blob(json.dumps(body, ensure_ascii=False), overwrite=True)

        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        initial_payload = {
            "status": "queued",
            "progress": 0,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        blob_client.upload_blob(json.dumps(initial_payload), overwrite=True)

        # Enrich job body with canonical conversation id if user_id provided and no conversation_id
        try:
            user_id = str((body.get("user_id") or "").strip()) if isinstance(body, dict) else ""
            if user_id and not str((body.get("conversation_id") or "").strip()):
                try:
                    mem_id = cosmos_get_next_memory_id(user_id)
                except Exception:
                    mem_id = 1
                body["conversation_id"] = f"{user_id}_{mem_id}"
        except Exception:
            pass
        message = json.dumps({"job_id": job_id, "body": body})
        ttl_sec = int(os.getenv("MCP_JOBS_TTL_SECONDS", "3600"))
        try:
            storage["queue"].send_message(message, time_to_live=ttl_sec)
        except TypeError:
            storage["queue"].send_message(message)

        resp = func.HttpResponse(json.dumps({"job_id": job_id, "status": "queued"}), mimetype="application/json")
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/mcp-enqueue")
        resp = func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)


@bp.route(route="mcp-result", methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def mcp_result(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        job_id = req.params.get("job_id")
        if not job_id:
            resp = func.HttpResponse(json.dumps({"error": "Missing 'job_id'"}), status_code=400, mimetype="application/json")
            return _apply_cors(resp, req)

        storage = _get_storage_clients()
        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        if not blob_client.exists():
            resp = func.HttpResponse(json.dumps({"job_id": job_id, "status": "unknown"}), mimetype="application/json", status_code=404)
            return _apply_cors(resp, req)

        content = json.loads(blob_client.download_blob().readall().decode("utf-8"))

        status = str(content.get("status", "")).lower()
        if status in ("queued", "running"):
            delay = _get_result_delay_seconds()
            content.setdefault("recommended_poll_interval_ms", _get_recommended_poll_interval_ms())
            time.sleep(delay)

        qp = getattr(req, 'params', {}) or {}
        return_partial = str(qp.get("return_partial_output", "")).lower() in ("1", "true", "yes", "on")
        if status == "running":
            if return_partial:
                if content.get("partial_output") and not content.get("output_text"):
                    content["output_text"] = content["partial_output"]
            else:
                if "partial_output" in content:
                    content.pop("partial_output", None)

        if status == "running" and not content.get("output_text"):
            content.setdefault("output_text", "⏳ Thought process ...")

        response_payload = {"job_id": job_id, **content}
        resp = func.HttpResponse(json.dumps(response_payload), mimetype="application/json")
        if "recommended_poll_interval_ms" in content:
            resp.headers["Retry-After"] = str(max(1, int(content["recommended_poll_interval_ms"] / 1000)))
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/mcp-result")
        resp = func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)


@bp.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection="AzureWebJobsStorage")
def queue_trigger(msg: func.QueueMessage) -> None:
    storage = _get_storage_clients()
    try:
        payload = json.loads(msg.get_body().decode("utf-8"))
        job_id = payload.get("job_id")
        body = payload.get("body") or {}
        if not job_id:
            return

        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        created_at: Optional[str] = None
        try:
            existing = json.loads(blob_client.download_blob().readall().decode("utf-8"))
            created_at = existing.get("createdAt")
        except Exception:
            pass
        running_payload = {"status": "running", "progress": 1}
        if created_at:
            running_payload["createdAt"] = created_at
        blob_client.upload_blob(json.dumps(running_payload), overwrite=True)

        prompt = (body.get("prompt") or "") if isinstance(body, dict) else ""
        model = body.get("model") or os.getenv("AZURE_OPENAI_MODEL") or "gpt-5-mini"
        reasoning_effort = (body.get("reasoning_effort") or os.getenv("DEFAULT_REASONING_EFFORT") or "low").lower()
        mcp_tool_cfg = resolve_mcp_config(body)
        client = create_llm_client()
        conversation_id = str((body.get("conversation_id") or "").strip()) or None
        responses_args: Dict[str, Any] = build_responses_args(
            model, prompt, mcp_tool_cfg, reasoning_effort
        )
        # Inject prior turns when available (user_id + conversation_id)
        try:
            user_id_ctx = str((body.get("user_id") or "").strip())
            if user_id_ctx and conversation_id:
                prior = cosmos_get_conversation_messages(user_id_ctx, conversation_id, limit=6)
                if prior:
                    msgs: List[dict] = []
                    for m in prior[-3:]:
                        role = (m.get("role") or "user").strip()
                        content = (m.get("content") or "").strip()
                        if not content:
                            continue
                        if role == "assistant":
                            msgs.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
                        else:
                            msgs.append({"role": "user", "content": [{"type": "input_text", "text": content}]})
                    system_msg = build_system_message_text()
                    responses_args["input"] = (
                        [{"role": "system", "content": [{"type": "input_text", "text": system_msg}]}]
                        + msgs
                        + [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
                    )
        except Exception:
            pass
        partial_chunks: List[str] = []
        progress_value: int = 1
        try:
            # If classic tools exist, avoid streaming and run tool loop
            if len(get_builtin_tools_config()) > 0:
                output_text, _ = run_responses_with_tools(client, responses_args)
                if not output_text:
                    try:
                        no_tools_args = dict(responses_args)
                        no_tools_args.pop("tools", None)
                        no_tools_args.pop("tool_choice", None)
                        response = client.responses.create(**no_tools_args)
                        output_text = getattr(response, "output_text", None)
                    except Exception:
                        pass
            else:
                with client.responses.stream(**responses_args) as stream:
                    for event in stream:
                        if getattr(event, "type", None) == "response.output_text.delta":
                            delta = getattr(event, "delta", "")
                            if delta:
                                partial_chunks.append(delta)
                                progress_value = min(95, progress_value + 2)
                                running_update = {
                                    "status": "running",
                                    "partial_output": "".join(partial_chunks),
                                    "progress": progress_value,
                                }
                                if created_at:
                                    running_update["createdAt"] = created_at
                                blob_client.upload_blob(json.dumps(running_update, ensure_ascii=False), overwrite=True)
                final_response = stream.get_final_response()
                output_text = getattr(final_response, "output_text", None) or "".join(partial_chunks)
        except Exception:
            logging.exception("streaming or tool loop failed; falling back to non-stream create")
            response = client.responses.create(**responses_args)
            output_text = getattr(response, "output_text", None)
        result = {
            "status": "done",
            "output_text": output_text,
            "progress": 100,
            "model": model,
        }
        if created_at:
            result["createdAt"] = created_at
        blob_client.upload_blob(json.dumps(result, ensure_ascii=False), overwrite=True)
        # Save full turn memory (optional) when user_id provided
        try:
            user_id = str((body.get("user_id") or "").strip())
            conversation_id = str((body.get("conversation_id") or "").strip()) or None
            if user_id and conversation_id:
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
        except Exception:
            pass
    except Exception as e:
        logging.exception("queue_trigger failure")
        try:
            job_id = job_id if 'job_id' in locals() else 'unknown'
            blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
            blob_client.upload_blob(json.dumps({"status": "error", "error": str(e)}), overwrite=True)
        except Exception:
            pass


@bp.route(route="mcp-process", methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def mcp_process(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        try:
            body: Dict[str, Any] = req.get_json()
        except Exception:
            body = {}
        job_id = (body.get("job_id") or req.params.get("job_id") or "").strip()
        if not job_id:
            resp = func.HttpResponse(json.dumps({"error": "Missing 'job_id'"}), status_code=400, mimetype="application/json")
            return _apply_cors(resp, req)

        storage = _get_storage_clients()
        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        if not blob_client.exists():
            resp = func.HttpResponse(json.dumps({"job_id": job_id, "status": "unknown"}), status_code=404, mimetype="application/json")
            return _apply_cors(resp, req)

        req_blob = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.req.json")
        if not req_blob.exists():
            return func.HttpResponse(json.dumps({"error": "Missing request payload for job"}), status_code=500, mimetype="application/json")
        job_body = json.loads(req_blob.download_blob().readall().decode("utf-8"))

        # Mark as running to signal start, then enqueue the job for the queue_trigger to process
        created_at: Optional[str] = None
        try:
            existing = json.loads(blob_client.download_blob().readall().decode("utf-8"))
            created_at = existing.get("createdAt")
        except Exception:
            pass
        running_payload = {"status": "running", "progress": 1}
        if created_at:
            running_payload["createdAt"] = created_at
        blob_client.upload_blob(json.dumps(running_payload), overwrite=True)

        # Enqueue for background processing
        message = json.dumps({"job_id": job_id, "body": job_body})
        ttl_sec = int(os.getenv("MCP_JOBS_TTL_SECONDS", "3600"))
        try:
            storage["queue"].send_message(message, time_to_live=ttl_sec)
        except TypeError:
            storage["queue"].send_message(message)

        response_payload = {
            "job_id": job_id,
            "status": "running",
            "recommended_poll_interval_ms": _get_recommended_poll_interval_ms(),
        }
        resp = func.HttpResponse(json.dumps(response_payload), mimetype="application/json")
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/mcp-process")
        resp = func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)


@bp.route(route="mcp-memories", methods=[func.HttpMethod.GET], auth_level=func.AuthLevel.FUNCTION)
def mcp_list_memories(req: func.HttpRequest) -> func.HttpResponse:
    try:
        qp = getattr(req, 'params', {}) or {}
        user_id = (qp.get("user_id") or "").strip()
        if not user_id:
            return func.HttpResponse(json.dumps({"error": "Missing 'user_id'"}), status_code=400, mimetype="application/json")
        limit = int(qp.get("limit") or 50)
        items = cosmos_list_memories(user_id, limit=limit)
        return func.HttpResponse(json.dumps({"user_id": user_id, "items": items}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        logging.exception("Error in /api/mcp-memories")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


@bp.route(route="mcp-memory", methods=[func.HttpMethod.GET], auth_level=func.AuthLevel.FUNCTION)
def mcp_get_memory(req: func.HttpRequest) -> func.HttpResponse:
    try:
        qp = getattr(req, 'params', {}) or {}
        user_id = (qp.get("user_id") or "").strip()
        memory_id = (qp.get("memory_id") or "").strip()
        if not user_id or not memory_id:
            return func.HttpResponse(json.dumps({"error": "Missing 'user_id' or 'memory_id'"}), status_code=400, mimetype="application/json")
        doc = cosmos_get_memory(user_id, memory_id)
        if not doc:
            return func.HttpResponse(json.dumps({"error": "Not found"}), status_code=404, mimetype="application/json")
        return func.HttpResponse(json.dumps(doc, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        logging.exception("Error in /api/mcp-memory")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
