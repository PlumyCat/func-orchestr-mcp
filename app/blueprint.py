import json
import logging
import os
import time
import uuid
import random
from typing import Any, Dict, Optional, List

import azure.functions as func

from .services.storage import get_storage_clients
from .services.tools import resolve_mcp_config, normalize_allowed_tools

from .services.conversation import (
    create_llm_client,
    build_responses_args,
    run_responses_with_tools,
    build_system_message_text,
)
from .services.memory import (
    list_memories as cosmos_list_memories,
    get_memory as cosmos_get_memory,
    get_next_memory_id as cosmos_get_next_memory_id,
    get_conversation_messages as cosmos_get_conversation_messages,
    upsert_conversation_turn as cosmos_upsert_conversation_turn,
)


bp = func.Blueprint()


_SNIPPET_NAME_PROPERTY_NAME = "snippetname"
_SNIPPET_PROPERTY_NAME = "snippet"
_BLOB_PATH = "snippets/{mcptoolargs." + _SNIPPET_NAME_PROPERTY_NAME + "}.json"
QUEUE_NAME = "mcpjobs"
COPILOT_QUEUE_NAME = "mcpjobs-copilot"


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


def _orchestrator_models() -> dict:
    return {
        "trivial": os.getenv("ORCHESTRATOR_MODEL_TRIVIAL"),
        "standard": os.getenv("ORCHESTRATOR_MODEL_STANDARD"),
        "tools": os.getenv("ORCHESTRATOR_MODEL_TOOLS"),
        "deep": os.getenv("ORCHESTRATOR_MODEL_REASONING"),
    }


def _route_mode(prompt: str, has_tools: bool, constraints: dict, allowed_tools: Optional[List[str]] = None) -> str:
    # Only select tools mode if caller explicitly allows tools
    if has_tools and allowed_tools:
        return "tools"
    # Accept both camelCase and snake_case flags and flat boolean values
    prefer_reasoning = False
    try:
        prefer_reasoning = str(constraints.get("preferReasoning", "")).lower() in ("1", "true", "yes", "on") or \
                           str(constraints.get("prefer_reasoning", "")).lower() in ("1", "true", "yes", "on")
    except Exception:
        prefer_reasoning = False
    try:
        max_latency_ms = int(constraints.get("maxLatencyMs")) if constraints.get("maxLatencyMs") is not None else None
    except Exception:
        max_latency_ms = None
    text = (prompt or "").lower()
    # Include French markers so FR prompts can trigger reasoning automatically
    deep_markers = (
        "plan", "multi-step", "derive", "prove", "why", "strategy", "chain of thought",
        "plan d'action", "multi-etapes", "multi étapes", "démontrer", "demontrer", "prouve", "pourquoi",
        "stratégie", "strategie", "raisonnement", "chaine de raisonnement", "chaîne de raisonnement",
        "réfléchis", "reflechis", "pas à pas", "pas a pas", "analyse détaillée", "explication détaillée"
    )
    if prefer_reasoning or any(m in text for m in deep_markers) or len(prompt) > 800:
        # If explicit latency budget is tight, downshift to standard
        if max_latency_ms is not None and max_latency_ms < 1500:
            return "standard"
        return "deep"
    # length-based quick rule
    if len(prompt) < 160:
        return "trivial"
    return "standard"


def _apply_cors(resp: func.HttpResponse, req: func.HttpRequest) -> func.HttpResponse:
    origin = req.headers.get("Origin") or req.headers.get("origin")
    allowed = _parse_allowed_origins()
    if origin and (origin in allowed):
        resp.headers["Access-Control-Allow-Origin"] = origin
        prev_vary = resp.headers.get("Vary")
        resp.headers["Vary"] = f"{prev_vary}, Origin" if prev_vary else "Origin"
    return resp


@bp.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection="AzureWebJobsStorage")
def queue_trigger(msg: func.QueueMessage) -> None:
    try:
        payload = json.loads(msg.get_body().decode("utf-8"))
        job_id = payload.get("job_id")
        body = payload.get("body") or {}
        if not job_id:
            return
        logging.info(f"[mcp-queue] start job_id={job_id}")

        storage = get_storage_clients()

        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        created_at: Optional[str] = None
        try:
            existing = json.loads(blob_client.download_blob().readall().decode("utf-8"))
            created_at = existing.get("createdAt")
        except Exception:
            pass
        running_payload = {
            "status": "running", 
            "progress": 1,
            "message": "Réflexion en cours…",
            "tool": "",
            "partial_text": "",
            "final_text": "",
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        if created_at:
            running_payload["createdAt"] = created_at
        # Mark start time to compute duration later
        try:
            running_payload["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        except Exception:
            pass
        blob_client.upload_blob(json.dumps(running_payload), overwrite=True)
        logging.info(f"[mcp-queue] job {job_id} marked running")

        prompt = (body.get("prompt") or "") if isinstance(body, dict) else ""
        model = body.get("model") or os.getenv("AZURE_OPENAI_MODEL")
        reasoning_effort = (body.get("reasoning_effort") or "low").lower()
        mcp_tool_cfg = resolve_mcp_config(body)
        client = create_llm_client()
        conversation_id_raw = str((body.get("conversation_id") or "").strip())
        conversation_id = conversation_id_raw or None
        if conversation_id and conversation_id.lower() == "init":
            conversation_id = None
        responses_args: Dict[str, Any] = build_responses_args(
            model, prompt, mcp_tool_cfg, reasoning_effort
        )
        logging.info(f"[mcp-queue] job {job_id} args built; tools={len(responses_args.get('tools', []))}")
        # Prefer streaming by default for background jobs to provide progressive output
        request_stream = True
        try:
            if isinstance(body, dict) and (body.get("stream") is not None):
                request_stream = str(body.get("stream")).lower() in ("1", "true", "yes", "on")
        except Exception:
            request_stream = True
        if request_stream and responses_args.get("tools"):
            try:
                responses_args["tools"] = [t for t in responses_args["tools"] if t.get("type") != "function"]
                logging.info(f"[mcp-queue] job {job_id} tools filtered for streaming; tools={len(responses_args.get('tools', []))}")
            except Exception:
                pass
        # Prefer streaming in background by default; drop classic function tools when streaming
        try:
            request_stream = str((body.get("stream") if isinstance(body, dict) else "true") or "true").lower() in ("1", "true", "yes", "on")
        except Exception:
            request_stream = True
        try:
            # Respect allowed_tools restrictions for classic tools
            raw_allowed = body.get("allowed_tools") if isinstance(body, dict) else None
            normalized_allowed = normalize_allowed_tools(raw_allowed)
        except Exception:
            normalized_allowed = None
        try:
            if responses_args.get("tools"):
                filtered_tools: List[Dict[str, Any]] = []
                for t in responses_args["tools"]:
                    ttype = t.get("type")
                    name = t.get("name") or t.get("function", {}).get("name")
                    if request_stream and ttype == "function":
                        # Drop classic function tools to enable streaming
                        continue
                    if (normalized_allowed is not None) and (ttype == "function") and (name == "search_web") and ("search_web" not in normalized_allowed):
                        continue
                    filtered_tools.append(t)
                responses_args["tools"] = filtered_tools
        except Exception:
            pass
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
            # If any tools exist, avoid streaming and run tool loop; otherwise, stream
            if responses_args.get("tools"):
                tool_context = {"user_id": user_id_ctx} if user_id_ctx else None
                output_text, _ = run_responses_with_tools(client, responses_args, tool_context=tool_context)
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
                                    "progress": progress_value,
                                    "message": "Génération en cours…",
                                    "tool": "",
                                    "partial_text": "".join(partial_chunks),
                                    "final_text": "",
                                }
                                if created_at:
                                    running_update["createdAt"] = created_at
                                # Track timing
                                running_update["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                                try:
                                    if running_payload.get("startedAt"):
                                        # compute elapsed
                                        from datetime import datetime
                                        started_dt = datetime.fromisoformat(running_payload["startedAt"].replace("Z", "+00:00"))
                                        now_dt = datetime.utcnow()
                                        running_update["duration_ms"] = int((now_dt - started_dt).total_seconds() * 1000)
                                except Exception:
                                    pass
                                blob_client.upload_blob(json.dumps(running_update, ensure_ascii=False), overwrite=True)
                    logging.info(f"[mcp-queue] job {job_id} streaming finished; chunks={len(partial_chunks)}")
                    final_response = stream.get_final_response()
                    output_text = getattr(final_response, "output_text", None) or "".join(partial_chunks)
        except Exception:
            logging.exception("streaming or tool loop failed; falling back to non-stream create")
            try:
                response = client.responses.create(**responses_args)
                output_text = getattr(response, "output_text", None)
            except Exception as e_inner:
                logging.exception("non-stream create failed")
                output_text = f"error: {e_inner}"
        result = {
            "status": "completed",
            "progress": 100,
            "message": "Terminé",
            "tool": "",
            "partial_text": "",
            "final_text": output_text or "",
            "model": model,
        }
        # Add duration_ms
        try:
            from datetime import datetime
            started = running_payload.get("startedAt") or created_at
            if started:
                started_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                now_dt = datetime.utcnow()
                result["duration_ms"] = int((now_dt - started_dt).total_seconds() * 1000)
        except Exception:
            pass
        if created_at:
            result["createdAt"] = created_at
        result["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        blob_client.upload_blob(json.dumps(result, ensure_ascii=False), overwrite=True)
        logging.info(f"[mcp-queue] job {job_id} done")
        # Save full turn memory (optional) when user_id provided
        try:
            user_id = str((body.get("user_id") or "").strip())
            conversation_id_raw = str((body.get("conversation_id") or "").strip())
            conversation_id = conversation_id_raw or None
            if conversation_id and conversation_id.lower() == "init":
                conversation_id = None
            if user_id and conversation_id:
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
        except Exception:
            pass
    except Exception as e:
        logging.exception("queue_trigger failure")
        try:
            job = job_id if 'job_id' in locals() else 'unknown'
            try:
                storage = get_storage_clients()
                blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job}.json")
                blob_client.upload_blob(json.dumps({"status": "error", "error": str(e)}), overwrite=True)
            except Exception:
                pass
        except Exception:
            pass


@bp.route(route="mcp-memories", methods=[func.HttpMethod.GET], auth_level=func.AuthLevel.FUNCTION)
def mcp_list_memories(req: func.HttpRequest) -> func.HttpResponse:
    try:
        qp = getattr(req, 'params', {}) or {}
        user_id = (qp.get("user_id") or "").strip()
        if not user_id:
            return func.HttpResponse(json.dumps({"error": "Missing 'user_id'"}, ensure_ascii=False), status_code=400, mimetype="application/json")
        limit = int(qp.get("limit") or 50)
        items = cosmos_list_memories(user_id, limit=limit)
        return func.HttpResponse(json.dumps({"user_id": user_id, "items": items}, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        logging.exception("Error in /api/mcp-memories")
        return func.HttpResponse(json.dumps({"error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")


@bp.route(route="mcp-memory", methods=[func.HttpMethod.GET], auth_level=func.AuthLevel.FUNCTION)
def mcp_get_memory(req: func.HttpRequest) -> func.HttpResponse:
    try:
        qp = getattr(req, 'params', {}) or {}
        user_id = (qp.get("user_id") or "").strip()
        memory_id = (qp.get("memory_id") or "").strip()
        if not user_id or not memory_id:
            return func.HttpResponse(json.dumps({"error": "Missing 'user_id' or 'memory_id'"}, ensure_ascii=False), status_code=400, mimetype="application/json")
        doc = cosmos_get_memory(user_id, memory_id)
        if not doc:
            return func.HttpResponse(json.dumps({"error": "Not found"}, ensure_ascii=False), status_code=404, mimetype="application/json")
        return func.HttpResponse(json.dumps(doc, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        logging.exception("Error in /api/mcp-memory")
        return func.HttpResponse(json.dumps({"error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")


@bp.route(route="orchestrate/start", methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def orchestrate_start(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        
        try:
            body: Dict[str, Any] = req.get_json()
        except Exception:
            body = {}

        qp = getattr(req, 'params', {}) or {}
        prompt = (body.get("prompt") or qp.get("prompt") or "") if isinstance(body, dict) else (qp.get("prompt") or "")
        if not prompt:
            resp = func.HttpResponse(json.dumps({"ok": False, "error": "Missing 'prompt'"}, ensure_ascii=False), status_code=400, mimetype="application/json")
            return _apply_cors(resp, req)

        # Orchestration logic: determine mode and model
        constraints = body.get("constraints") if isinstance(body, dict) and isinstance(body.get("constraints"), dict) else {}
        allowed_tools = body.get("allowed_tools") if isinstance(body, dict) else None
        
        # Merge top-level flags into constraints for backward compatibility
        if isinstance(body, dict) and isinstance(constraints, dict):
            top_level_flags = {
                "prefer_reasoning": body.get("prefer_reasoning"),
                "preferReasoning": body.get("preferReasoning"),
                "maxLatencyMs": body.get("maxLatencyMs"),
                "maxLatencyMs_from_snake": body.get("max_latency_ms"),
            }
            for key, value in top_level_flags.items():
                if value is None:
                    continue
                if key == "maxLatencyMs_from_snake":
                    constraints.setdefault("maxLatencyMs", value)
                else:
                    constraints.setdefault(key, value)

        # Normalize allowed_tools to a list if present
        normalized_tools = None
        if isinstance(allowed_tools, list):
            normalized_tools = allowed_tools
        elif isinstance(allowed_tools, str) and allowed_tools.strip():
            normalized_tools = [t.strip() for t in allowed_tools.split(',') if t.strip()]

        # Determine MCP tools configuration
        mcp_tool_cfg = None
        try:
            merged = dict(body) if isinstance(body, dict) else {}
            if qp.get("mcp_url"):
                merged["mcp_url"] = qp.get("mcp_url")
            if allowed_tools is not None:
                merged["allowed_tools"] = allowed_tools
            mcp_tool_cfg = resolve_mcp_config(merged)
        except Exception:
            mcp_tool_cfg = None

        # Route mode using orchestration logic
        mode = _route_mode(prompt, has_tools=(mcp_tool_cfg is not None), constraints=constraints, allowed_tools=normalized_tools)
        models = _orchestrator_models()
        selected_model = models["deep" if mode == "deep" else ("tools" if mode == "tools" else mode)]
        reasoning_effort = (body.get("reasoning_effort") if isinstance(body, dict) else (qp.get("reasoning_effort") if qp else None)) or "low"

        # Enrich body with orchestration decision
        orchestration_body = dict(body) if isinstance(body, dict) else {}
        orchestration_body.update({
            "selected_model": selected_model,
            "mode": mode,
            "use_reasoning": (mode == "deep"),
            "reasoning_effort": reasoning_effort if mode == "deep" else "low",
            "mcp_tool_cfg": mcp_tool_cfg,
        })

        job_id = str(uuid.uuid4())

        storage = get_storage_clients(COPILOT_QUEUE_NAME)
        try:
            storage["queue"].create_queue()
        except Exception:
            pass
        try:
            storage["blob"].create_container(storage["container"])
        except Exception:
            pass

        req_blob = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.req.json")
        req_blob.upload_blob(json.dumps(orchestration_body, ensure_ascii=False), overwrite=True)

        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        initial_message = "Analyse et sélection du modèle optimal…" if mode == "deep" else "Préparation de la réponse…"
        initial_payload = {
            "status": "queued",
            "progress": 0,
            "message": initial_message,
            "tool": "",
            "partial_text": "",
            "final_text": "",
            "mode": mode,
            "selected_model": selected_model,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        blob_client.upload_blob(json.dumps(initial_payload, ensure_ascii=False), overwrite=True)

        try:
            user_id = str((orchestration_body.get("user_id") or "").strip())
            conv_raw = str((orchestration_body.get("conversation_id") or "").strip())
            if user_id and (not conv_raw or conv_raw.lower() == "init"):
                try:
                    mem_id = cosmos_get_next_memory_id(user_id)
                except Exception:
                    mem_id = int(time.time())
                orchestration_body["conversation_id"] = f"{user_id}_{mem_id}"

                # Update the request blob with the generated conversation_id
                req_blob.upload_blob(json.dumps(orchestration_body, ensure_ascii=False), overwrite=True)
        except Exception:
            pass

        message = json.dumps({"job_id": job_id, "body": orchestration_body, "type": "orchestrate"})
        ttl_sec = int(os.getenv("MCP_JOBS_TTL_SECONDS", "3600"))
        try:
            storage["queue"].send_message(message, time_to_live=ttl_sec)
        except TypeError:
            storage["queue"].send_message(message)

        response_payload = {
            "ok": True,
            "job_id": job_id,
            "status": "queued",
            "message": initial_message,
            "progress": 0,
            "tool": "",
            "mode": mode,
            "selected_model": selected_model,
            "conversation_id": orchestration_body.get("conversation_id", ""),
            "retry_after_sec": 3
        }
        resp = func.HttpResponse(json.dumps(response_payload, ensure_ascii=False), mimetype="application/json")
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/orchestrate/start")
        resp = func.HttpResponse(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)


@bp.route(route="orchestrate/status", methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def orchestrate_status(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        
        job_id = req.params.get("job_id")
        if not job_id:
            resp = func.HttpResponse(json.dumps({"ok": False, "error": "Missing 'job_id'"}, ensure_ascii=False), status_code=400, mimetype="application/json")
            return _apply_cors(resp, req)

        storage = get_storage_clients(COPILOT_QUEUE_NAME)
        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        if not blob_client.exists():
            response_payload = {
                "ok": False,
                "job_id": job_id,
                "status": "unknown",
                "message": "Job not found",
                "progress": 0,
                "tool": "",
                "final_text": ""
            }
            resp = func.HttpResponse(json.dumps(response_payload, ensure_ascii=False), mimetype="application/json", status_code=404)
            return _apply_cors(resp, req)

        content = json.loads(blob_client.download_blob().readall().decode("utf-8"))

        status = str(content.get("status", "")).lower()
        progress = int(content.get("progress", 0))
        
        # Debug logging for unexpected status
        logging.info(f"[orchestrate/status] job_id={job_id}, status='{status}', content={json.dumps(content, ensure_ascii=False)}")
        
        # Map internal status to Copilot-compatible status
        if status == "queued":
            mapped_status = "queued"
            message = content.get("message", "Réflexion en cours…")
            retry_after = 3
        elif status == "running":
            mapped_status = "running" 
            message = content.get("message", "Réflexion en cours…")
            retry_after = 2
        elif status in ("done", "completed"):
            mapped_status = "completed"
            message = "Completed"
            retry_after = None
        elif status == "error":
            mapped_status = "failed"
            message = f"Error: {content.get('error', 'Unknown error')}"
            retry_after = None
        else:
            mapped_status = "unknown"
            message = content.get("message", "Status unknown")
            retry_after = 5

        # Handle tool usage status with more specific messages
        tool_name = content.get("tool", "")
        if tool_name and mapped_status == "running":
            mapped_status = "tool"
            # Use existing message from worker if available, otherwise generate generic one
            current_message = content.get("message", "")
            if current_message and ("cours" in current_message or "Utilisation" in current_message):
                message = current_message
            else:
                # Provide specific tool messages for orchestrate mode
                if tool_name.lower() == "websearch":
                    message = "Web search in progress..."
                elif tool_name in ["list_templates_http", "list_images", "convert_word_to_pdf"]:
                    message = f"Using tool: {tool_name}"
                else:
                    message = f"Using tool: {tool_name}"

        # Add delay for status polling to limit API calls
        if mapped_status in ("queued", "running", "tool"):
            time.sleep(5)

        # Try to get conversation_id from request blob
        conversation_id = ""
        try:
            req_blob = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.req.json")
            if req_blob.exists():
                req_content = json.loads(req_blob.download_blob().readall().decode("utf-8"))
                conversation_id = req_content.get("conversation_id", "")
        except Exception:
            pass

        response_payload = {
            "ok": True,
            "job_id": job_id,
            "status": mapped_status,
            "message": message,
            "progress": progress,
            "tool": tool_name,
            "mode": content.get("mode", "orchestrate"),
            "selected_model": content.get("selected_model", ""),
            "conversation_id": conversation_id,
            "final_text": content.get("final_text", content.get("output_text", ""))
        }

        resp = func.HttpResponse(json.dumps(response_payload, ensure_ascii=False), mimetype="application/json")
        if retry_after:
            resp.headers["Retry-After"] = str(retry_after)
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/orchestrate/status")
        resp = func.HttpResponse(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)


@bp.route(route="ask/start", methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def ask_start(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        
        try:
            body: Dict[str, Any] = req.get_json()
        except Exception:
            body = {}

        qp = getattr(req, 'params', {}) or {}
        prompt = (body.get("prompt") or qp.get("prompt") or "") if isinstance(body, dict) else (qp.get("prompt") or "")
        if not prompt:
            resp = func.HttpResponse(json.dumps({"ok": False, "error": "Missing 'prompt'"}, ensure_ascii=False), status_code=400, mimetype="application/json")
            return _apply_cors(resp, req)

        # ASK logic: manual model selection
        merged: Dict[str, Any] = dict(body) if isinstance(body, dict) else {}
        if qp.get("model"):
            merged["model"] = qp.get("model")
        if qp.get("reasoning_effort"):
            merged["reasoning_effort"] = qp.get("reasoning_effort")

        # Model selection (same logic as api/ask)
        body_model = merged.get("model")
        base_default = os.getenv("AZURE_OPENAI_MODEL")
        model = body_model or base_default
        reasoning_effort = merged.get("reasoning_effort") or "low"

        # Enrich body with ask decision
        ask_body = dict(merged)
        ask_body.update({
            "selected_model": model,
            "mode": "ask",
            "reasoning_effort": reasoning_effort,
        })

        job_id = str(uuid.uuid4())

        storage = get_storage_clients(COPILOT_QUEUE_NAME)
        try:
            storage["queue"].create_queue()
        except Exception:
            pass
        try:
            storage["blob"].create_container(storage["container"])
        except Exception:
            pass

        req_blob = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.req.json")
        req_blob.upload_blob(json.dumps(ask_body, ensure_ascii=False), overwrite=True)

        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        initial_payload = {
            "status": "queued",
            "progress": 0,
            "message": "Préparation de la réponse…",
            "tool": "",
            "partial_text": "",
            "final_text": "",
            "mode": "ask",
            "selected_model": model,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        blob_client.upload_blob(json.dumps(initial_payload, ensure_ascii=False), overwrite=True)

        try:
            user_id = str((ask_body.get("user_id") or "").strip())
            conv_raw = str((ask_body.get("conversation_id") or "").strip())
            if user_id and (not conv_raw or conv_raw.lower() == "init"):
                try:
                    mem_id = cosmos_get_next_memory_id(user_id)
                except Exception:
                    mem_id = int(time.time())
                ask_body["conversation_id"] = f"{user_id}_{mem_id}"

                # Update the request blob with the generated conversation_id
                req_blob.upload_blob(json.dumps(ask_body, ensure_ascii=False), overwrite=True)
        except Exception:
            pass

        message = json.dumps({"job_id": job_id, "body": ask_body, "type": "ask"})
        ttl_sec = int(os.getenv("MCP_JOBS_TTL_SECONDS", "3600"))
        try:
            storage["queue"].send_message(message, time_to_live=ttl_sec)
        except TypeError:
            storage["queue"].send_message(message)

        response_payload = {
            "ok": True,
            "job_id": job_id,
            "status": "queued",
            "message": "Préparation de la réponse…",
            "progress": 0,
            "tool": "",
            "mode": "ask",
            "selected_model": model,
            "conversation_id": ask_body.get("conversation_id", ""),
            "retry_after_sec": 3
        }
        resp = func.HttpResponse(json.dumps(response_payload, ensure_ascii=False), mimetype="application/json")
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/ask/start")
        resp = func.HttpResponse(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)


@bp.route(route="ask/status", methods=[func.HttpMethod.GET, func.HttpMethod.OPTIONS], auth_level=func.AuthLevel.FUNCTION)
def ask_status(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "OPTIONS":
            return _preflight_response(req)
        
        job_id = req.params.get("job_id")
        if not job_id:
            resp = func.HttpResponse(json.dumps({"ok": False, "error": "Missing 'job_id'"}, ensure_ascii=False), status_code=400, mimetype="application/json")
            return _apply_cors(resp, req)

        storage = get_storage_clients(COPILOT_QUEUE_NAME)
        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        if not blob_client.exists():
            response_payload = {
                "ok": False,
                "job_id": job_id,
                "status": "unknown",
                "message": "Job not found",
                "progress": 0,
                "tool": "",
                "final_text": ""
            }
            resp = func.HttpResponse(json.dumps(response_payload, ensure_ascii=False), mimetype="application/json", status_code=404)
            return _apply_cors(resp, req)

        content = json.loads(blob_client.download_blob().readall().decode("utf-8"))

        status = str(content.get("status", "")).lower()
        progress = int(content.get("progress", 0))
        
        # Debug logging for unexpected status
        logging.info(f"[ask/status] job_id={job_id}, status='{status}', content={json.dumps(content, ensure_ascii=False)}")
        
        # Map internal status to response format (same logic as orchestrate/status)
        if status == "queued":
            mapped_status = "queued"
            message = content.get("message", "Préparation de la réponse…")
            retry_after = 3
        elif status == "running":
            mapped_status = "running" 
            message = content.get("message", "Génération en cours…")
            retry_after = 2
        elif status in ("done", "completed"):
            mapped_status = "completed"
            message = "Completed"
            retry_after = None
        elif status == "error":
            mapped_status = "failed"
            message = f"Error: {content.get('error', 'Unknown error')}"
            retry_after = None
        else:
            mapped_status = "unknown"
            message = content.get("message", "Status unknown")
            retry_after = 5

        # Handle tool usage status
        tool_name = content.get("tool", "")
        if tool_name and mapped_status == "running":
            mapped_status = "tool"
            current_message = content.get("message", "")
            if current_message and ("cours" in current_message or "Utilisation" in current_message):
                message = current_message
            else:
                # Provide specific tool messages for ask mode
                if tool_name.lower() == "websearch":
                    message = "Web search in progress..."
                elif tool_name in ["list_templates_http", "list_images", "convert_word_to_pdf"]:
                    message = f"Using tool: {tool_name}"
                else:
                    message = f"Using tool: {tool_name}"
        elif mapped_status == "running" and not tool_name:
            # Improve default running message for ask mode
            if content.get("mode") == "ask":
                message = content.get("message", "Réflexion en cours…")
        
        # Clients may poll rapidly; rely on Retry-After header instead of server-side sleeps
        # to advise on backoff intervals.

        # Try to get conversation_id from request blob
        conversation_id = ""
        try:
            req_blob = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.req.json")
            if req_blob.exists():
                req_content = json.loads(req_blob.download_blob().readall().decode("utf-8"))
                conversation_id = req_content.get("conversation_id", "")
        except Exception:
            pass

        response_payload = {
            "ok": True,
            "job_id": job_id,
            "status": mapped_status,
            "message": message,
            "progress": progress,
            "tool": tool_name,
            "mode": content.get("mode", "ask"),
            "selected_model": content.get("selected_model", ""),
            "conversation_id": conversation_id,
            "final_text": content.get("final_text", content.get("output_text", ""))
        }

        resp = func.HttpResponse(json.dumps(response_payload, ensure_ascii=False), mimetype="application/json")
        if retry_after:
            resp.headers["Retry-After"] = str(retry_after)
        return _apply_cors(resp, req)
    except Exception as e:
        logging.exception("Error in /api/ask/status")
        resp = func.HttpResponse(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")
        return _apply_cors(resp, req)
