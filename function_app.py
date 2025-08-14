import json
import logging
import os
import time
import uuid
from typing import Optional, List
import azure.functions as func
from openai import AzureOpenAI
from app.services.memory import upsert_memory as cosmos_upsert_memory
from app.services.memory import list_conversation_docs as cosmos_list_conversation_docs
from app.services.memory import list_memories as cosmos_list_memories
from app.services.memory import get_conversation_messages as cosmos_get_conversation_messages
from app.services.memory import upsert_conversation_turn as cosmos_upsert_conversation_turn
from app.services.memory import get_next_memory_id as cosmos_get_next_memory_id
from app.services.tools import resolve_mcp_config
from app.services.conversation import build_responses_args, run_responses_with_tools, build_system_message_text
from app.services.tools import get_builtin_tools_config

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# Register MCP endpoints via Blueprint
try:
    from app.blueprint import bp as mcp_bp
    app.register_functions(mcp_bp)
except Exception as e:
    logging.warning(f"MCP blueprint not registered: {e}")


def _get_aoai_client() -> AzureOpenAI:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    if not endpoint:
        raise RuntimeError("Missing AZURE_OPENAI_ENDPOINT")
    if not api_key:
        raise RuntimeError("Missing AZURE_OPENAI_KEY for local SDK calls")
    return AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)


# Health route per template rules
@app.function_name("ping")
@app.route(route="ping", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def ping(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok"}), mimetype="application/json")


# Simple ask http POST function that returns the completion based on prompt
@app.function_name("ask")
@app.route(route="ask", methods=["POST"])
def ask(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}
    prompt = (body.get("prompt") or "") if isinstance(body, dict) else ""
    if not prompt:
        return func.HttpResponse(json.dumps({"error": "Missing 'prompt' in request body"}), status_code=400, mimetype="application/json")

    try:
        started = time.perf_counter()
        client = _get_aoai_client()
        # Allow model override via body and query param
        qp = getattr(req, 'params', {}) or {}
        body_model = body.get("model") if isinstance(body, dict) else None
        model = (body_model or qp.get("model") or os.getenv("CHAT_MODEL_DEPLOYMENT_NAME") or "gpt-4o")
        # If classic tools are available and caller did not force a model, prefer the tools-capable model
        try:
            if not body_model and not qp.get("model") and len(get_builtin_tools_config()) > 0:
                model = os.getenv("ORCHESTRATOR_MODEL_TOOLS", model)
        except Exception:
            pass
        # Conversation: only when user_id provided
        user_id = (body.get("user_id") if isinstance(body, dict) else None) or (qp.get("user_id") or "")
        user_id = str(user_id).strip()
        conversation_id = (body.get("conversation_id") if isinstance(body, dict) else None) or qp.get("conversation_id")
        conversation_id = str(conversation_id).strip() if conversation_id else None
        new_conversation = False
        if user_id:
            if not conversation_id:
                try:
                    mem_id = cosmos_get_next_memory_id(user_id)
                except Exception:
                    mem_id = 1
                conversation_id = f"{user_id}_{mem_id}"
                new_conversation = True
            # Load last turns as structured messages
            input_messages: Optional[List[dict]] = None
            try:
                prior_messages = cosmos_get_conversation_messages(user_id, conversation_id, limit=6)
                if prior_messages:
                    msgs: List[dict] = []
                    for m in prior_messages[-3:]:
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

            responses_args = {
                "model": model,
                "text": {"format": {"type": "text"}, "verbosity": "medium"},
                "store": False,
            }
            # Enrich input with system + prior + current turn
            system_msg = build_system_message_text()
            enriched_messages = (
                ([{"role": "system", "content": [{"type": "input_text", "text": system_msg}]}] if input_messages else [])
                + (input_messages or [])
                + [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
            )
            responses_args["input"] = enriched_messages
            # Include classic tools if available
            try:
                tools = get_builtin_tools_config()
                if tools:
                    responses_args["tools"] = tools
                    # Force auto tool usage (string form expected by Responses API)
                    responses_args["tool_choice"] = "auto"
            except Exception:
                pass
            if len(get_builtin_tools_config()) > 0:
                output_text, response = run_responses_with_tools(client, responses_args)
                if not output_text:
                    # Fallback: retry without tools to ensure a textual answer
                    try:
                        no_tools_args = dict(responses_args)
                        no_tools_args.pop("tools", None)
                        no_tools_args.pop("tool_choice", None)
                        response = client.responses.create(**no_tools_args)
                        output_text = getattr(response, "output_text", None) or ""
                    except Exception:
                        pass
            else:
                response = client.responses.create(**responses_args)
                output_text = getattr(response, "output_text", None) or ""
        else:
            responses_args = {
                "model": model,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "text": {"format": {"type": "text"}, "verbosity": "medium"},
                "store": False,
            }
            try:
                tools = get_builtin_tools_config()
                if tools:
                    responses_args["tools"] = tools
                    responses_args["tool_choice"] = "auto"
            except Exception:
                pass
            if len(get_builtin_tools_config()) > 0:
                output_text, response = run_responses_with_tools(client, responses_args)
                if not output_text:
                    try:
                        no_tools_args = dict(responses_args)
                        no_tools_args.pop("tools", None)
                        no_tools_args.pop("tool_choice", None)
                        response = client.responses.create(**no_tools_args)
                        output_text = getattr(response, "output_text", None) or ""
                    except Exception:
                        pass
            else:
                response = client.responses.create(**responses_args)
                output_text = getattr(response, "output_text", None) or ""
        run_id = str(uuid.uuid4())
        duration_ms = int((time.perf_counter() - started) * 1000)
        # Optional memory persist if user_id provided
        try:
            if user_id:
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
        except Exception:
            pass
        payload = {"output_text": output_text, "model": model, "duration_ms": duration_ms, "run_id": run_id}
        if user_id:
            payload["conversation_id"] = conversation_id
            payload["new_conversation"] = new_conversation
        resp = func.HttpResponse(json.dumps(payload, ensure_ascii=False), status_code=200, mimetype="application/json")
        resp.headers["X-Model-Used"] = model
        if user_id and conversation_id:
            resp.headers["X-Conversation-Id"] = conversation_id
        return resp
    except Exception as e:
        logging.exception("ask failed")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


def _orchestrator_models() -> dict:
    return {
        "trivial": os.getenv("ORCHESTRATOR_MODEL_TRIVIAL", "gpt-5-chat"),
        "standard": os.getenv("ORCHESTRATOR_MODEL_STANDARD", "gpt-5-chat"),
        "tools": os.getenv("ORCHESTRATOR_MODEL_TOOLS", "gpt-4.1"),
        "deep": os.getenv("ORCHESTRATOR_MODEL_REASONING", "gpt-5-mini"),
    }


def _route_mode(prompt: str, has_tools: bool, constraints: dict, allowed_tools: Optional[list] = None) -> str:
    # Only select tools mode if caller explicitly allows tools
    if has_tools and allowed_tools:
        return "tools"
    prefer_reasoning = str(constraints.get("preferReasoning", "")).lower() in ("1", "true", "yes", "on")
    try:
        max_latency_ms = int(constraints.get("maxLatencyMs")) if constraints.get("maxLatencyMs") is not None else None
    except Exception:
        max_latency_ms = None
    text = (prompt or "").lower()
    deep_markers = ("plan", "multi-step", "derive", "prove", "why", "strategy", "chain of thought")
    if prefer_reasoning or any(m in text for m in deep_markers) or len(prompt) > 800:
        # If explicit latency budget is tight, downshift to standard
        if max_latency_ms is not None and max_latency_ms < 1500:
            return "standard"
        return "deep"
    # length-based quick rule
    if len(prompt) < 160:
        return "trivial"
    return "standard"


# Orchestrate endpoint: choose model/reasoning, optionally execute
@app.function_name("orchestrate")
@app.route(route="orchestrate", methods=["POST"])
def orchestrate(req: func.HttpRequest) -> func.HttpResponse:
    try:
        try:
            body = req.get_json()
        except Exception:
            body = {}
        qp = getattr(req, 'params', {}) or {}
        prompt = (body.get("prompt") or qp.get("prompt") or "") if isinstance(body, dict) else (qp.get("prompt") or "")
        if not prompt:
            return func.HttpResponse(json.dumps({"error": "Missing 'prompt'"}), status_code=400, mimetype="application/json")

        execute = str((body.get("execute") if isinstance(body, dict) else qp.get("execute")) or "true").lower() in ("1", "true", "yes", "on")
        constraints = body.get("constraints") if isinstance(body, dict) and isinstance(body.get("constraints"), dict) else {}
        allowed_tools = body.get("allowed_tools") if isinstance(body, dict) else None
        # Normalize user_id early so we can persist memory even on first exchange
        user_id = ((body.get("user_id") if isinstance(body, dict) else None) or (qp.get("user_id") or ""))
        user_id = str(user_id).strip()
        mcp_tool_cfg = None
        try:
            merged = dict(body) if isinstance(body, dict) else {}
            # allow query override of MCP URL
            if qp.get("mcp_url"):
                merged["mcp_url"] = qp.get("mcp_url")
            if allowed_tools is not None:
                merged["allowed_tools"] = allowed_tools
            mcp_tool_cfg = resolve_mcp_config(merged)
        except Exception:
            mcp_tool_cfg = None

        # Normalize allowed_tools to a list if present
        normalized_tools = None
        if isinstance(allowed_tools, list):
            normalized_tools = allowed_tools
        elif isinstance(allowed_tools, str) and allowed_tools.strip():
            normalized_tools = [t.strip() for t in allowed_tools.split(',') if t.strip()]
        mode = _route_mode(prompt, has_tools=(mcp_tool_cfg is not None), constraints=constraints, allowed_tools=normalized_tools)
        models = _orchestrator_models()
        selected_model = models["deep" if mode == "deep" else ("tools" if mode == "tools" else mode)]
        reasoning_effort = (body.get("reasoning_effort") if isinstance(body, dict) else None) or os.getenv("DEFAULT_REASONING_EFFORT", "low")
        decision_id = str(uuid.uuid4())

        decision_payload = {
            # decision_id intentionally omitted from response to simplify API
            "mode": mode,
            "selected_model": selected_model,
            "use_reasoning": (mode == "deep"),
            "reasoning_effort": reasoning_effort if mode == "deep" else None,
        }
        # No memory persistence for orchestrator decisions (by design)

        if not execute:
            return func.HttpResponse(json.dumps(decision_payload, ensure_ascii=False), mimetype="application/json")

        # Execute using AOAI directly or with tools via Responses API
        client = _get_aoai_client()
        started = time.perf_counter()
        # Prepare identifiers
        run_id = str(uuid.uuid4())
        # Normalize conversation_id
        orig_missing_conversation_id = False
        conversation_id = (body.get("conversation_id") if isinstance(body, dict) else None) or qp.get("conversation_id")
        conversation_id = str(conversation_id).strip() if conversation_id else None
        if not conversation_id and user_id:
            # Derive canonical conversation_id as <user_id>_<memory_id>
            try:
                mem_id = cosmos_get_next_memory_id(user_id)
            except Exception:
                mem_id = 1
            conversation_id = f"{user_id}_{mem_id}"
            orig_missing_conversation_id = True

        # Build input messages with prior turns if available (prefer single conversation doc with messages[])
        input_messages = None
        if conversation_id and user_id:
            try:
                prior_messages = cosmos_get_conversation_messages(user_id, conversation_id, limit=6)
                if prior_messages:
                    msgs = []
                    for m in prior_messages[-3:]:
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
                pass

        output_text = ""
        response = None
        # Prefer Chat Completions when we have prior turns and no tools/reasoning to ensure proper dialogue continuity
        used_chat_completions = False
        has_classic_tools = len(get_builtin_tools_config()) > 0
        if input_messages is not None and not mcp_tool_cfg and not has_classic_tools and mode != "deep":
            try:
                # Convert responses-style messages to chat.completions format
                chat_messages: List[dict] = []
                # Add a concise system instruction to bias ambiguity resolution towards prior context
                system_msg = build_system_message_text()
                chat_messages.append({"role": "system", "content": system_msg})
                for m in input_messages:
                    role = m.get("role", "user")
                    parts = m.get("content") or []
                    text = " ".join([p.get("text", "") for p in parts if isinstance(p, dict)])
                    if text:
                        chat_messages.append({"role": role, "content": text})
                chat_messages.append({"role": "user", "content": prompt})
                cc = client.chat.completions.create(model=selected_model, messages=chat_messages)
                # Extract first choice
                if getattr(cc, "choices", None):
                    output_text = getattr(cc.choices[0].message, "content", None) or ""
                    response = cc
                    used_chat_completions = True
            except Exception:
                logging.exception("chat.completions fall back to responses API")
                used_chat_completions = False

        if not used_chat_completions:
            responses_args = build_responses_args(selected_model, prompt, mcp_tool_cfg, reasoning_effort)
            if input_messages is not None:
                # Append current user message after prior turns
                # Prepend a system instruction
                system_msg = build_system_message_text()
                enriched_messages = (
                    [{"role": "system", "content": [{"type": "input_text", "text": system_msg}]}]
                    + input_messages
                    + [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
                )
                responses_args["input"] = enriched_messages
            # If classic tools exist, use tool loop to allow auto tools in any mode
            if has_classic_tools:
                output_text, response = run_responses_with_tools(client, responses_args)
            else:
                response = client.responses.create(**responses_args)
                output_text = getattr(response, "output_text", None) or ""
        duration_ms = int((time.perf_counter() - started) * 1000)
        # run_id already created above

        # Memory: persist turn in a single conversation document (id == conversation_id)
        try:
            if user_id and conversation_id:
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
        except Exception:
            pass

        payload = {
            **decision_payload,
            "output_text": output_text,
            "duration_ms": duration_ms,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "new_conversation": orig_missing_conversation_id,
        }
        resp = func.HttpResponse(json.dumps(payload, ensure_ascii=False), mimetype="application/json")
        try:
            resp.headers["X-Conversation-Id"] = conversation_id
        except Exception:
            pass
        return resp
    except Exception as e:
        logging.exception("orchestrate failed")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")

## Whois endpoint removed as requested


CHAT_STORAGE_CONNECTION = "AzureWebJobsStorage"
COLLECTION_NAME = "ChatState"


if str(os.getenv("ENABLE_ASSISTANT_BINDINGS", "false")).lower() in ("1", "true", "yes", "on"):
    # http PUT function to start ChatBot conversation based on a chatID
    @app.function_name("CreateChatBot")
    @app.route(route="chats/{chatId}", methods=["PUT"])
    @app.assistant_create_output(arg_name="requests")
    def create_chat_bot(req: func.HttpRequest,
                        requests: func.Out[str]) -> func.HttpResponse:
        chatId = req.route_params.get("chatId")
        input_json = req.get_json()
        logging.info(
            f"Creating chat ${chatId} from input parameters " +
            "${json.dumps(input_json)}")
        create_request = {
            "id": chatId,
            "instructions": input_json.get("instructions"),
            "chatStorageConnectionSetting": CHAT_STORAGE_CONNECTION,
            "collectionName": COLLECTION_NAME
        }
        requests.set(json.dumps(create_request))
        response_json = {"chatId": chatId}
        return func.HttpResponse(json.dumps(response_json), status_code=202,
                                 mimetype="application/json")


    # http GET function to get ChatBot conversation with chatID & timestamp
    @app.function_name("GetChatState")
    @app.route(route="chats/{chatId}", methods=["GET"])
    @app.assistant_query_input(
        arg_name="state",
        id="{chatId}",
        timestamp_utc="{Query.timestampUTC}",
        chat_storage_connection_setting=CHAT_STORAGE_CONNECTION,
        collection_name=COLLECTION_NAME
    )
    def get_chat_state(req: func.HttpRequest, state: str) -> func.HttpResponse:
        return func.HttpResponse(state, status_code=200,
                                 mimetype="application/json")


    # http POST function for user to send a message to ChatBot with chatID
    @app.function_name("PostUserResponse")
    @app.route(route="chats/{chatId}", methods=["POST"])
    @app.assistant_post_input(
        arg_name="state", id="{chatId}",
        user_message="{message}",
        model="%CHAT_MODEL_DEPLOYMENT_NAME%",
        chat_storage_connection_setting=CHAT_STORAGE_CONNECTION,
        collection_name=COLLECTION_NAME
        )
    def post_user_response(req: func.HttpRequest, state: str) -> func.HttpResponse:
        data = json.loads(state)
        recent_message_content = data['recentMessages'][0]['content']
        return func.HttpResponse(recent_message_content, status_code=200,
                                 mimetype="text/plain")
