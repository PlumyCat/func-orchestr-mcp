import json
import logging
import os
import time
import uuid
import requests
from typing import Optional, List
import azure.functions as func
from app.services.memory import upsert_memory as cosmos_upsert_memory
from app.services.memory import list_conversation_docs as cosmos_list_conversation_docs
from app.services.memory import list_memories as cosmos_list_memories
from app.services.memory import get_conversation_messages as cosmos_get_conversation_messages
from app.services.memory import upsert_conversation_turn as cosmos_upsert_conversation_turn
from app.services.memory import get_next_memory_id as cosmos_get_next_memory_id
from app.services.tools import resolve_mcp_config
from app.services.routing import _orchestrator_models, _route_mode
from app.services.conversation import (
    build_responses_args,
    run_responses_with_tools,
    build_system_message_text,
    create_llm_client,
)
from app.services.tools import get_builtin_tools_config, execute_tool_call

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# Register MCP endpoints via Blueprint
try:
    from app.blueprint import bp as mcp_bp
    app.register_functions(mcp_bp)
except Exception as e:
    logging.warning(f"MCP blueprint not registered: {e}")

# Register MCP Worker (Copilot async) via Blueprint
try:
    from app.mcp_worker import bp as mcp_worker_bp
    app.register_functions(mcp_worker_bp)
except Exception as e:
    logging.warning(f"MCP worker blueprint not registered: {e}")


# Health route per template rules
@app.function_name("ping")
@app.route(route="ping", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def ping(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok"}, ensure_ascii=False), mimetype="application/json")


# List available deployments (subscribed models) from Azure OpenAI
@app.function_name("models")
@app.route(route="models", methods=["GET"])
def list_models(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # Management plane call (ARM) to list deployed model names
        # Requires Managed Identity or Service Principal with Reader on the OpenAI resource
        qp = getattr(req, 'params', {}) or {}
        # Prefer env vars; allow fallback to query params and also accept plain names in env for convenience
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID") 
        rg_name = os.getenv("AZURE_RESOURCE_GROUP") 
        account_name = os.getenv("AZURE_OPENAI_RESOURCE_NAME") 
        if not sub_id or not rg_name or not account_name:
            return func.HttpResponse(
                json.dumps({
                    "error": "Missing AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, or AZURE_OPENAI_RESOURCE_NAME.",
                    "hint": "Provide them as env vars or query params subscriptionId, resourceGroup, accountName"
                }, ensure_ascii=False),
                status_code=400,
                mimetype="application/json",
            )
        try:
            # Lazy imports
            from azure.identity import DefaultAzureCredential  # type: ignore
            import requests  # type: ignore
        except Exception as e:
            return func.HttpResponse(json.dumps({"error": f"Missing dependencies: {e}"}, ensure_ascii=False), status_code=500, mimetype="application/json")
        # Acquire ARM token
        try:
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            token = credential.get_token("https://management.azure.com/.default").token
        except Exception as e:
            return func.HttpResponse(json.dumps({"error": f"Failed to acquire AAD token: {e}"}, ensure_ascii=False), status_code=500, mimetype="application/json")
        api_version_mgmt = os.getenv("AZURE_OPENAI_MGMT_API_VERSION", "2023-05-01")
        mgmt_url = (
            f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg_name}"
            f"/providers/Microsoft.CognitiveServices/accounts/{account_name}/deployments?api-version={api_version_mgmt}"
        )
        try:
            r = requests.get(mgmt_url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
            r.raise_for_status()
            payload_json = r.json()
        except Exception as e:
            return func.HttpResponse(json.dumps({"error": f"ARM deployments list failed: {e}"}, ensure_ascii=False), status_code=500, mimetype="application/json")

        value = payload_json.get("value") if isinstance(payload_json, dict) else None
        names = []
        details = []
        for d in (value or []):
            name = d.get("name") if isinstance(d, dict) else None
            props = d.get("properties") if isinstance(d, dict) else {}
            model_props = props.get("model") if isinstance(props, dict) else {}
            model_name = model_props.get("name") if isinstance(model_props, dict) else None
            if name:
                names.append(name)
            details.append({
                "name": name,
                "model": model_name,
                "id": d.get("id") if isinstance(d, dict) else None,
                "type": d.get("type") if isinstance(d, dict) else None,
            })

        env_defaults = {
            "AZURE_OPENAI_MODEL": os.getenv("AZURE_OPENAI_MODEL"),
            "ORCHESTRATOR_MODEL_TRIVIAL": os.getenv("ORCHESTRATOR_MODEL_TRIVIAL"),
            "ORCHESTRATOR_MODEL_STANDARD": os.getenv("ORCHESTRATOR_MODEL_STANDARD"),
            "ORCHESTRATOR_MODEL_TOOLS": os.getenv("ORCHESTRATOR_MODEL_TOOLS"),
            "ORCHESTRATOR_MODEL_REASONING": os.getenv("ORCHESTRATOR_MODEL_REASONING"),
            "REASONING_MODELS": os.getenv("REASONING_MODELS"),
        }

        payload = {"deployments": names, "details": details, "env_defaults": env_defaults}
        return func.HttpResponse(json.dumps(payload, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")


def _build_search_web_tool_def():
    return {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Perform a web search via Azure Function (SearXNG).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    }

# Simple endpoint demonstrating automatic use of the `search_web` tool
@app.function_name("websearch_test")
@app.route(route="websearch-test", methods=["POST"])
def websearch_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}
    qp = getattr(req, 'params', {}) or {}
    prompt = (body.get("prompt") or qp.get("prompt") or "").strip()
    if not prompt:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'prompt'"}),
            status_code=400,
            mimetype="application/json",
        )

    model = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o")
    client = create_llm_client()

    # Messages
    messages = [{"role": "user", "content": prompt}]

    # Tool unique
    tools = [_build_search_web_tool_def()]

    try:
        # Premier appel : voir si le modèle déclenche la tool
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        result = {"answer": msg.content}
        if msg.tool_calls:
            # Le modèle a demandé la tool
            tc = msg.tool_calls[0]
            result["tool_called"] = {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }

            # ⚠️ Ici tu devrais appeler ton vrai backend SearXNG
            # Pour la démo, on simule une réponse
            fake_result = {
                "summary": f"(Résultat factice pour query={tc.function.arguments})"
            }

            # On boucle en envoyant la réponse tool
            follow_up = client.chat.completions.create(
                model=model,
                messages=messages + [
                    msg,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(fake_result),
                    },
                ],
            )
            result["answer"] = follow_up.choices[0].message.content

        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )

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
        return func.HttpResponse(json.dumps({"error": "Missing 'prompt' in request body"}, ensure_ascii=False), status_code=400, mimetype="application/json")

    try:
        started = time.perf_counter()
        client = create_llm_client()
        # Allow model override via body and query param
        qp = getattr(req, 'params', {}) or {}
        body_model = body.get("model") if isinstance(body, dict) else None
        logging.debug(f"ask: body_model={body_model} qp_model={qp.get('model') if qp else None}")
        qp_model = qp.get("model") if qp else None
        base_default = (os.getenv("AZURE_OPENAI_MODEL"))
        model = (body_model or qp_model, base_default) or base_default
        # If classic tools are available and caller did not force a model, prefer the tools-capable model
        try:
            if not body_model and not qp_model and len(get_builtin_tools_config()) > 0:
                model = (os.getenv("ORCHESTRATOR_MODEL_TOOLS"), model) or model
        except Exception:
            pass
        # Conversation: only when user_id provided
        user_id = (body.get("user_id") if isinstance(body, dict) else None) or (qp.get("user_id") or "")
        user_id = str(user_id).strip()
        conversation_id_raw = (body.get("conversation_id") if isinstance(body, dict) else None) or qp.get("conversation_id")
        conversation_id = str(conversation_id_raw).strip() if conversation_id_raw else None
        if conversation_id and conversation_id.lower() == "init":
            conversation_id = None
        new_conversation = False
        if user_id:
            if not conversation_id:
                try:
                    mem_id = cosmos_get_next_memory_id(user_id)
                except Exception:
                    mem_id = int(time.time())
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
            # Propager reasoning_effort si fourni (même en ask)
            try:
                req_effort = body.get("reasoning_effort") if isinstance(body, dict) else None
                if req_effort:
                    responses_args["reasoning"] = {"effort": req_effort}
            except Exception:
                pass
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
            # Provide user_id to tool heuristics (doc service fallbacks)
            try:
                if user_id:
                    responses_args["x_user_id"] = user_id
            except Exception:
                pass
            if responses_args.get("tools"):
                tool_context = {"user_id": user_id} if user_id else None
                output_text, response = run_responses_with_tools(client, responses_args, tool_context=tool_context)
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
            # Propager reasoning_effort si fourni (sans historique)
            try:
                req_effort = body.get("reasoning_effort") if isinstance(body, dict) else None
                if req_effort:
                    responses_args["reasoning"] = {"effort": req_effort}
            except Exception:
                pass
            try:
                tools = get_builtin_tools_config()
                if tools:
                    responses_args["tools"] = tools
                    responses_args["tool_choice"] = "auto"
            except Exception:
                pass
            # Provide user_id to tool heuristics (doc service fallbacks)
            try:
                if user_id:
                    responses_args["x_user_id"] = user_id
            except Exception:
                pass
            if responses_args.get("tools"):
                tool_context = {"user_id": user_id} if user_id else None
                output_text, response = run_responses_with_tools(client, responses_args, tool_context=tool_context)
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
        duration_ms = int((time.perf_counter() - started) * 1000)
        # Optional memory persist if user_id provided
        persisted: bool = False
        try:
            if user_id:
                logging.debug(f"Persisting conversation turn user_id={user_id} conversation_id={conversation_id}")
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
                persisted = True
                logging.debug("Persist OK")
        except Exception as e:
            logging.exception(f"Persist failed: {e}")
        payload = {"output_text": output_text, "model": model, "duration_ms": duration_ms}
        # Expose classic tool usage when available
        try:
            used_tools = getattr(response, "_classic_tools_used", None)
            if isinstance(used_tools, list) and used_tools:
                payload["tool_used"] = used_tools
        except Exception:
            pass
        # Echo reasoning effort if present in request
        try:
            req_effort = body.get("reasoning_effort") if isinstance(body, dict) else None
            if req_effort:
                payload["reasoning_effort"] = str(req_effort).lower()
        except Exception:
            pass
        if user_id:
            payload["user_id"] = user_id
            payload["conversation_id"] = conversation_id
            payload["new_conversation"] = new_conversation
            payload["persisted"] = persisted
        resp = func.HttpResponse(json.dumps(payload, ensure_ascii=False), status_code=200, mimetype="application/json")
        resp.headers["X-Model-Used"] = model
        if user_id and conversation_id:
            resp.headers["X-Conversation-Id"] = conversation_id
        return resp
    except Exception as e:
        logging.exception("ask failed")
        return func.HttpResponse(json.dumps({"error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")




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
            return func.HttpResponse(json.dumps({"error": "Missing 'prompt'"}, ensure_ascii=False), status_code=400, mimetype="application/json")

        execute = str((body.get("execute") if isinstance(body, dict) else qp.get("execute")) or "true").lower() in ("1", "true", "yes", "on")
        constraints = body.get("constraints") if isinstance(body, dict) and isinstance(body.get("constraints"), dict) else {}
        allowed_tools = body.get("allowed_tools") if isinstance(body, dict) else None
        # Merge top-level flags into constraints for backward compatibility
        if isinstance(body, dict) and isinstance(constraints, dict):
            top_level_flags = {
                "prefer_reasoning": body.get("prefer_reasoning"),
                "preferReasoning": body.get("preferReasoning"),
                "maxLatencyMs": body.get("maxLatencyMs"),
                # Accept snake_case and map it to camelCase expected downstream
                "maxLatencyMs_from_snake": body.get("max_latency_ms"),
            }
            for key, value in top_level_flags.items():
                if value is None:
                    continue
                if key == "maxLatencyMs_from_snake":
                    constraints.setdefault("maxLatencyMs", value)
                else:
                    constraints.setdefault(key, value)
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
        reasoning_effort = (body.get("reasoning_effort") if isinstance(body, dict) else (qp.get("reasoning_effort") if qp else None)) or "low"

        decision_payload = {
            "mode": mode,
            "selected_model": selected_model,
            "use_reasoning": (mode == "deep"),
            "reasoning_effort": reasoning_effort if mode == "deep" else None,
        }
        # No memory persistence for orchestrator decisions (by design)

        if not execute:
            return func.HttpResponse(json.dumps(decision_payload, ensure_ascii=False), mimetype="application/json")

        # Execute using AOAI directly or with tools via Responses API
        client = create_llm_client()
        started = time.perf_counter()
        # Normalize conversation_id
        orig_missing_conversation_id = False
        conversation_id_raw = (body.get("conversation_id") if isinstance(body, dict) else None) or qp.get("conversation_id")
        conversation_id = str(conversation_id_raw).strip() if conversation_id_raw else None
        if conversation_id and conversation_id.lower() == "init":
            conversation_id = None
        if not conversation_id and user_id:
            # Derive canonical conversation_id as <user_id>_<memory_id>
            try:
                mem_id = cosmos_get_next_memory_id(user_id)
            except Exception:
                mem_id = int(time.time())
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
            else:
                # No history: keep input minimal; do not inject system message to avoid forcing language
                responses_args["input"] = [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
            # Drop web search unless explicitly allowed
            try:
                if not (isinstance(normalized_tools, list) and ("*" in normalized_tools or "search_web" in normalized_tools)):
                    if responses_args.get("tools"):
                        responses_args["tools"] = [
                            t
                            for t in responses_args["tools"]
                            if (t.get("name") or t.get("function", {}).get("name")) != "search_web"
                        ]
            except Exception:
                pass
            # If caller restricted allowed_tools, filter classic tools accordingly and optionally force a single tool
            try:
                if isinstance(normalized_tools, list) and responses_args.get("tools"):
                    filtered_tools = []
                    classic_names: List[str] = []
                    for t in responses_args["tools"]:
                        ttype = t.get("type")
                        name = t.get("name") or t.get("function", {}).get("name")
                        if ttype == "function":
                            if ("*" in normalized_tools) or (name in normalized_tools):
                                filtered_tools.append(t)
                                classic_names.append(name or "")
                            else:
                                continue
                        else:
                            # Keep MCP tool configs regardless of allow-list here
                            filtered_tools.append(t)
                    if filtered_tools:
                        responses_args["tools"] = filtered_tools
                        # When only one classic function remains, just keep 'auto' with a single choice
                        # to avoid invalid tool_choice schema errors
                        only_classics = [n for n in classic_names if n]
                        if len(only_classics) == 1:
                            responses_args["tool_choice"] = "auto"
            except Exception:
                pass
            # Optional pre-execution when a single classic tool is allowed
            try:
                if isinstance(normalized_tools, list) and responses_args.get("tools"):
                    # Identify remaining classic tools after filtering
                    remaining_classics = [
                        (t.get("name") or t.get("function", {}).get("name"))
                        for t in responses_args.get("tools", [])
                        if t.get("type") == "function"
                    ]
                    if len(remaining_classics) == 1 and remaining_classics[0] == "convert_word_to_pdf":
                        import re as _re
                        m = _re.search(r"([\w\-./]+\.(?:docx|doc))", prompt, flags=_re.IGNORECASE)
                        filename = m.group(1) if m else None
                        if filename:
                            blob_path = filename if ("/" in filename) else (f"{user_id}/{filename}" if user_id else None)
                            if blob_path:
                                tool_out = execute_tool_call("convert_word_to_pdf", {"blob": blob_path})
                                # Post-synthesis: one-short confirmation based on tool output
                                system_msg = build_system_message_text()
                                summary_prompt = (
                                    "You received tool results (see <context>). "
                                    "Produce a short confirmation that directly answers the user's request using ONLY this context. "
                                    "Do not include sample code, steps, or extra explanations. One sentence max.\n\n"
                                    f"User question: {prompt}\n\n<context>\n{tool_out}\n</context>\n"
                                )
                                args2 = {
                                    "model": selected_model,
                                    "input": [
                                        {"role": "system", "content": [{"type": "input_text", "text": system_msg}]},
                                        {"role": "user", "content": [{"type": "input_text", "text": summary_prompt}]},
                                    ],
                                    "text": {"format": {"type": "text"}, "verbosity": "medium"},
                                    "store": False,
                                }
                                final_resp = client.responses.create(**args2)
                                final_text = getattr(final_resp, "output_text", None) or ""
                                if final_text.strip():
                                    output_text = final_text
                                    response = final_resp
                                    try:
                                        setattr(response, "_classic_tools_used", [{"name": "convert_word_to_pdf", "arguments": {"blob": blob_path}, "type": "classic", "direct": True}])
                                    except Exception:
                                        pass
                                    duration_ms = int((time.perf_counter() - started) * 1000)
                                    # Persist if applicable
                                    try:
                                        if user_id and conversation_id:
                                            cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
                                    except Exception:
                                        pass
                                    # Build payload similar to normal flow
                                    payload = {
                                        **decision_payload,
                                        "output_text": output_text,
                                        "duration_ms": duration_ms,
                                        "conversation_id": conversation_id,
                                        "new_conversation": orig_missing_conversation_id,
                                    }
                                    try:
                                        used_tools = getattr(response, "_classic_tools_used", None)
                                        if isinstance(used_tools, list) and used_tools:
                                            payload["tool_used"] = used_tools
                                    except Exception:
                                        pass
                                    resp = func.HttpResponse(json.dumps(payload, ensure_ascii=False), mimetype="application/json")
                                    try:
                                        if user_id and conversation_id:
                                            resp.headers["X-Conversation-Id"] = conversation_id
                                    except Exception:
                                        pass
                                    return resp
            except Exception:
                pass
            # If any tools are configured, use tool loop to allow repeated tool calls
            if responses_args.get("tools"):
                tool_context = {"user_id": user_id} if user_id else None
                output_text, response = run_responses_with_tools(client, responses_args, tool_context=tool_context)
                # Fallback: if no textual output, retry once without tools to ensure an answer
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
        duration_ms = int((time.perf_counter() - started) * 1000)

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
            "conversation_id": conversation_id,
            "new_conversation": orig_missing_conversation_id,
        }
        # Include classic tool usage when available
        try:
            used_tools = getattr(response, "_classic_tools_used", None)
            if isinstance(used_tools, list) and used_tools:
                payload["tool_used"] = used_tools
        except Exception:
            pass
        resp = func.HttpResponse(json.dumps(payload, ensure_ascii=False), mimetype="application/json")
        try:
            resp.headers["X-Conversation-Id"] = conversation_id
        except Exception:
            pass
        return resp
    except Exception as e:
        logging.exception("orchestrate failed")
        return func.HttpResponse(json.dumps({"error": str(e)}, ensure_ascii=False), status_code=500, mimetype="application/json")


def _get_json(req: func.HttpRequest) -> dict:
    try:
        body = req.get_json()
    except Exception:
        body = {}
    return body or {}

def _qp(req: func.HttpRequest) -> dict:
    return getattr(req, "params", {}) or {}

def _json_response(payload, status=200):
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status,
        mimetype="application/json",
    )


def _call_list_images_backend(args: dict) -> tuple[int, dict]:
    base = os.getenv("DOCSVC_BASE_URL")
    url = f"{base.rstrip('/')}/api/users/images"
    timeout_s = float(os.getenv("DOCSVC_TIMEOUT_SECONDS"))

    payload = {"user_id": args.get("user_id")}
    if "pageSize" in args and args["pageSize"]:
        payload["pageSize"] = int(args["pageSize"])

    r = requests.get(url, json=payload, timeout=timeout_s)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    return r.status_code, data

def _build_list_images_tool_def():
    return {
        "type": "function",
        "function": {
            "name": "list_images",
            "description": "List a user's images.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier"
                    },
                    "pageSize": {
                        "type": "integer",
                        "description": "Max items per page (optional)"
                    }
                },
                "required": ["user_id"],
            },
        },
    }

@app.function_name("list_images_test")
@app.route(route="list-images-test", methods=["POST"])
def list_images_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'prompt'"}),
            status_code=400,
            mimetype="application/json",
        )

    model = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o")
    client = create_llm_client()

    messages = [{"role": "user", "content": prompt}]
    if body.get("user_id"):
        messages.append({"role": "system", "content": f"user_id={body['user_id']}"})
    if body.get("pageSize"):
        messages.append({"role": "system", "content": f"pageSize={body['pageSize']}"})

    tools = [_build_list_images_tool_def()]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        result = {"answer": msg.content}

        if msg.tool_calls:
            tc = msg.tool_calls[0]
            result["tool_called"] = {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }

            base = os.getenv("DOCSVC_BASE_URL").rstrip("/")
            func_key = os.getenv("DOCSVC_FUNCTION_KEY")
            backend_url = f"{base}/users/images?code={func_key}"

            headers = {"Content-Type": "application/json"}
            r = requests.get(backend_url, json=json.loads(tc.function.arguments), headers=headers, timeout=20)
            try:
                backend_data = r.json()
            except Exception:
                backend_data = {"raw": r.text}

            follow_up = client.chat.completions.create(
                model=model,
                messages=messages + [
                    msg,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(backend_data),
                    },
                ],
            )
            result["answer"] = follow_up.choices[0].message.content
            result["backend_result"] = backend_data

        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


def _build_list_templates_tool_def():
    return {
        "type": "function",
        "function": {
            "name": "list_templates_http",
            "description": "List a user's templates (optionally include shared).",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier"
                    },
                    "pageSize": {
                        "type": "integer",
                        "description": "Max items per page (optional)"
                    },
                    "includeShared": {
                        "type": "boolean",
                        "description": "Whether to include shared templates"
                    }
                },
                "required": ["user_id"],
            },
        },
    }


@app.function_name("list_templates_test")
@app.route(route="list-templates-test", methods=["POST"])
def list_templates_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'prompt'"}),
            status_code=400,
            mimetype="application/json",
        )

    model = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o")
    client = create_llm_client()

    messages = [{"role": "user", "content": prompt}]
    if body.get("user_id"):
        messages.append({"role": "system", "content": f"user_id={body['user_id']}"})
    if body.get("pageSize"):
        messages.append({"role": "system", "content": f"pageSize={body['pageSize']}"})
    if body.get("includeShared") is not None:
        messages.append({"role": "system", "content": f"includeShared={body['includeShared']}"})

    tools = [_build_list_templates_tool_def()]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        result = {"answer": msg.content}

        if msg.tool_calls:
            tc = msg.tool_calls[0]
            result["tool_called"] = {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }

            base = os.getenv("DOCSVC_BASE_URL").rstrip("/")
            func_key = os.getenv("DOCSVC_FUNCTION_KEY")
            backend_url = f"{base}/users/templates?code={func_key}"

            headers = {"Content-Type": "application/json"}
            r = requests.get(backend_url, json=json.loads(tc.function.arguments), headers=headers, timeout=int(os.getenv("DOCSVC_TIMEOUT_SECONDS", "20")))
            try:
                backend_data = r.json()
            except Exception:
                backend_data = {"raw": r.text}

            follow_up = client.chat.completions.create(
                model=model,
                messages=messages + [
                    msg,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(backend_data),
                    },
                ],
            )
            result["answer"] = follow_up.choices[0].message.content
            result["backend_result"] = backend_data

        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


def _build_list_shared_templates_tool_def():
    return {
        "type": "function",
        "function": {
            "name": "list_shared_templates",
            "description": "List shared templates (all locales) from the org prefix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pageSize": {
                        "type": "integer",
                        "description": "Max items per page (optional)"
                    }
                },
                "additionalProperties": False
            },
        },
    }


@app.function_name("list_shared_templates_test")
@app.route(route="list-shared-templates-test", methods=["GET"])
def list_shared_templates_test(req: func.HttpRequest) -> func.HttpResponse:
    # Pas de body en GET, on prend query params
    prompt = (req.params.get("prompt") or "").strip()
    if not prompt:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'prompt'"}),
            status_code=400,
            mimetype="application/json",
        )

    model = os.getenv("AZURE_OPENAI_MODEL")
    client = create_llm_client()

    messages = [{"role": "user", "content": prompt}]
    if req.params.get("pageSize"):
        messages.append({"role": "system", "content": f"pageSize={req.params['pageSize']}"})

    tools = [_build_list_shared_templates_tool_def()]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        result = {"answer": msg.content}

        if msg.tool_calls:
            tc = msg.tool_calls[0]
            result["tool_called"] = {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }

            # Appel backend: GET avec query params + clé de fonction
            base = os.getenv("DOCSVC_BASE_URL").rstrip("/")
            func_key = os.getenv("DOCSVC_FUNCTION_KEY")
            backend_url = f"{base}/templates"

            args = {}
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            params = {}
            if "pageSize" in args and args["pageSize"]:
                try:
                    params["pageSize"] = int(args["pageSize"])
                except Exception:
                    pass
            params["code"] = func_key  # auth_level=FUNCTION

            all_items = []
            next_token = None

            while True:
                params = {}
                if "pageSize" in args and args["pageSize"]:
                    try:
                        params["pageSize"] = int(args["pageSize"])
                    except Exception:
                        pass
                if next_token:
                    params["continuationToken"] = next_token
                params["code"] = func_key  # auth_level=FUNCTION

                r = requests.get(
                    backend_url,
                    params=params,
                    timeout=int(os.getenv("DOCSVC_TIMEOUT_SECONDS", "20")),
                )
                data = r.json()
                all_items.extend(data.get("items", []))
                next_token = data.get("continuationToken")
                if not next_token:
                    break

            backend_data = {"items": all_items}


            follow_up = client.chat.completions.create(
                model=model,
                messages=messages + [
                    msg,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(backend_data),
                    },
                ],
            )
            result["answer"] = follow_up.choices[0].message.content
            result["backend_result"] = backend_data

        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


def _build_convert_word_to_pdf_tool_def():
    return {
        "type": "function",
        "function": {
            "name": "convert_word_to_pdf",
            "description": "Convert an existing .docx/.dotx in blob storage to PDF.",
            "parameters": {
                "type": "object",
                "properties": {
                    "blob": {
                        "type": "string",
                        "description": "Blob path of the source .docx/.dotx (e.g., 'user123/new.docx')"
                    },
                    "dest": {
                        "type": "string",
                        "description": "Optional destination blob path for the PDF"
                    }
                },
                "required": ["blob"]
            }
        }
    }


@app.function_name("convert_word_to_pdf_test")
@app.route(route="convert-word-to-pdf-test", methods=["POST"])
def convert_word_to_pdf_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'prompt'"}),
            status_code=400,
            mimetype="application/json",
        )

    model = os.getenv("AZURE_OPENAI_MODEL")
    client = create_llm_client()

    # On laisse le modèle déclencher la tool ; on lui file juste les args si fournis
    messages = [{"role": "user", "content": prompt}]
    if body.get("blob"):
        messages.append({"role": "system", "content": f"blob={body['blob']}"})
    if body.get("dest"):
        messages.append({"role": "system", "content": f"dest={body['dest']}"})

    tools = [_build_convert_word_to_pdf_tool_def()]

    try:
        # 1) Appel modèle
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        result = {"answer": msg.content}

        # 2) Si tool appelée → appel backend réel (POST + query params)
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            result["tool_called"] = {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }

            base = os.getenv("DOCSVC_BASE_URL").rstrip("/")
            func_key = os.getenv("DOCSVC_FUNCTION_KEY")
            backend_url = f"{base}/convert/word-to-pdf"

            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            params = {"code": func_key}
            if "blob" in args and args["blob"]:
                params["blob"] = args["blob"]
            if "dest" in args and args["dest"]:
                params["dest"] = args["dest"]

            r = requests.post(
                backend_url,
                params=params,   # le backend lit blob/dest en query
                timeout=int(os.getenv("DOCSVC_TIMEOUT_SECONDS", "20")),
            )
            try:
                backend_data = r.json()
            except Exception:
                backend_data = {"raw": r.text}

            # 3) Boucle de synthèse
            follow_up = client.chat.completions.create(
                model=model,
                messages=messages + [
                    msg,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(backend_data),
                    },
                ],
            )
            result["answer"] = follow_up.choices[0].message.content
            result["backend_result"] = backend_data

        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


def _build_init_user_tool_def():
    return {
        "type": "function",
        "function": {
            "name": "init_user",
            "description": "Initialize user folders in blob storage (creates .keep in required subdirs).",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User identifier"
                    }
                },
                "required": ["user_id"]
            }
        }
    }


@app.function_name("init_user_test")
@app.route(route="init-user-test", methods=["POST"])
def init_user_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'prompt'"}),
            status_code=400,
            mimetype="application/json",
        )

    model = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o")
    client = create_llm_client()

    messages = [{"role": "user", "content": prompt}]
    if body.get("user_id"):
        messages.append({"role": "system", "content": f"user_id={body['user_id']}"})

    tools = [_build_init_user_tool_def()]

    try:
        # 1) Appel modèle
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        result = {"answer": msg.content}

        # 2) Si tool appelée → POST backend avec JSON + ?code=
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            result["tool_called"] = {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }

            base = os.getenv("DOCSVC_BASE_URL").rstrip("/")
            func_key = os.getenv("DOCSVC_FUNCTION_KEY")
            backend_url = f"{base}/users/init?code={func_key}"

            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            headers = {"Content-Type": "application/json"}
            r = requests.post(
                backend_url,
                json=args,
                headers=headers,
                timeout=int(os.getenv("DOCSVC_TIMEOUT_SECONDS", "20")),
            )
            try:
                backend_data = r.json()
            except Exception:
                backend_data = {"raw": r.text}

            # 3) Synthèse finale
            created = backend_data.get("created") or []
            try:
                uid = (json.loads(tc.function.arguments or "{}")).get("user_id")
            except Exception:
                uid = None

            if not created:
                result["answer"] = f"L'espace pour '{uid}' est déjà initialisé."
            else:
                # Extraire les noms de dossiers (sans .keep)
                folders = []
                for ph in created:
                    parts = str(ph).split("/")
                    # parts = [user_id, maybe folder, ".keep"]
                    if len(parts) >= 2 and parts[1] and parts[1] != ".keep":
                        folders.append(parts[1])
                folders = sorted(set(folders))

                if folders:
                    result["answer"] = f"Votre espace '{uid}' a été créé sur le blob. Dossiers : {', '.join(folders)}."
                else:
                    # Si tu ne veux rien lister du tout :
                    result["answer"] = f"Votre espace '{uid}' a été créé sur le blob."

            result["backend_result"] = backend_data

        return func.HttpResponse(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )


def _build_mcp_hello_tools():
    sse = os.getenv("TOOLS_SSE_URL", "").rstrip("/")
    if not sse:
        raise RuntimeError("Missing TOOLS_SSE_URL")
    key = os.getenv("TOOLS_FUNCTIONS_KEY", "")
    return [{
        "type": "mcp",
        "server_label": "func-mcp",
        "server_url": f"{sse}?code={key}",
        "allowed_tools": ["hello_mcp"],
        "require_approval": "never"
    }]


@app.function_name("hello_mcp_test")
@app.route(route="hello-mcp-test", methods=["POST"])
def hello_mcp_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}
    prompt = (body.get("prompt") or "Call hello_mcp").strip()
    if not prompt:
        return func.HttpResponse(json.dumps({"error":"Missing 'prompt'"}), status_code=400, mimetype="application/json")

    client = create_llm_client()
    model = os.getenv("AZURE_OPENAI_MODEL")

    try:
        tools = _build_mcp_hello_tools()
        resp = client.responses.create(
            model=model,
            input=[{"role":"user","content":[{"type":"input_text","text": prompt}]}],
            tools=tools,
            tool_choice="auto",
            text={"format":{"type":"text"}}
        )
        answer = getattr(resp, "output_text", "") or ""
        return func.HttpResponse(json.dumps({"answer": answer}, ensure_ascii=False),
                                 mimetype="application/json")
    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


def _build_mcp_word_create_tools():
    sse = os.getenv("TOOLS_SSE_URL", "").rstrip("/")
    if not sse:
        raise RuntimeError("Missing TOOLS_SSE_URL")
    key = os.getenv("TOOLS_FUNCTIONS_KEY", "")
    return [{
        "type": "mcp",
        "server_label": "func-mcp",
        "server_url": f"{sse}?code={key}",
        "allowed_tools": ["word_create_document"],
        "require_approval": "never",
    }]

@app.function_name("word_create_document_test")
@app.route(route="word-create-document-test", methods=["POST"])
def word_create_document_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        body = {}

    prompt = (body.get("prompt") or "Create a Word document using word_create_document.").strip()

    # Arguments MCP (tous optionnels sauf user_id si tu veux cibler un dossier)
    args = {
        "user_id": body.get("user_id"),
        "filename": body.get("filename"),
        "title": body.get("title"),
        "author": body.get("author"),
    }
    # Nettoie les None
    args = {k: v for k, v in args.items() if v is not None}

    client = create_llm_client()
    model = os.getenv("AZURE_OPENAI_MODEL")

    try:
        tools = _build_mcp_word_create_tools()

        # Hint minimal pour que le modèle passe bien les arguments fournis
        msgs = []
        if args:
            msgs.append({
                "role": "system",
                "content": [{"type": "input_text", "text": f"Call the MCP tool 'word_create_document' with these exact JSON arguments:\n{json.dumps(args)}"}],
            })
        msgs.append({
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        })

        resp = client.responses.create(
            model=model,
            input=msgs,
            tools=tools,
            tool_choice="auto",
            text={"format": {"type": "text"}}
        )

        answer = getattr(resp, "output_text", "") or ""
        return func.HttpResponse(json.dumps({"answer": answer}, ensure_ascii=False), mimetype="application/json")

    except Exception as e:
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
