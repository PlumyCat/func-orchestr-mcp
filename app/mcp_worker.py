import json
import logging
import os
import time
from typing import Any, Dict, Optional, List

import azure.functions as func
from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient

from .services.conversation import (
    create_llm_client,
    build_responses_args,
    run_responses_with_tools,
    build_system_message_text,
)
from .services.tools import resolve_mcp_config, normalize_allowed_tools
from .services.memory import (
    get_next_memory_id as cosmos_get_next_memory_id,
    get_conversation_messages as cosmos_get_conversation_messages,
    upsert_conversation_turn as cosmos_upsert_conversation_turn,
)


bp = func.Blueprint()

QUEUE_NAME = "mcpjobs-copilot"


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
        "container": "jobs",
    }


def _update_job_status(job_id: str, status: str, progress: int, message: str, tool: str = "", 
                      partial_text: str = "", final_text: str = "", created_at: Optional[str] = None,
                      selected_model: Optional[str] = None, mode: Optional[str] = None, used_tools: Optional[List[str]] = None) -> None:
    try:
        storage = _get_storage_clients()
        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        
        # Preserve existing data first
        try:
            existing = json.loads(blob_client.download_blob().readall().decode("utf-8"))
        except Exception:
            existing = {}
        
        payload = {
            "status": status,
            "progress": progress,
            "message": message,
            "tool": tool,
            "partial_text": partial_text,
            "final_text": final_text,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        
        # Handle used_tools list
        if used_tools is not None:
            payload["used_tools"] = used_tools
        elif existing.get("used_tools"):
            payload["used_tools"] = existing["used_tools"]
        
        # Preserve important fields from existing data
        if created_at:
            payload["createdAt"] = created_at
        elif existing.get("createdAt"):
            payload["createdAt"] = existing["createdAt"]
            
        if selected_model:
            payload["selected_model"] = selected_model
        elif existing.get("selected_model"):
            payload["selected_model"] = existing["selected_model"]
            
        if mode:
            payload["mode"] = mode
        elif existing.get("mode"):
            payload["mode"] = existing["mode"]
            
        blob_client.upload_blob(json.dumps(payload, ensure_ascii=False), overwrite=True)
        logging.info(f"[mcp-worker] Job {job_id} status updated: {status} ({progress}%) - {message}")
    except Exception:
        logging.exception(f"[mcp-worker] Failed to update job {job_id} status")


@bp.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection="AzureWebJobsStorage")
def mcp_process_worker(msg: func.QueueMessage) -> None:
    job_id = None
    try:
        logging.info(f"[mcp-worker] Received message from queue {QUEUE_NAME}")
        payload = json.loads(msg.get_body().decode("utf-8"))
        logging.info(f"[mcp-worker] Parsed payload: {payload}")
        
        job_id = payload.get("job_id")
        body = payload.get("body") or {}
        job_type = payload.get("type", "mcp")  # "mcp" or "orchestrate" or "ask"
        if not job_id:
            logging.error("[mcp-worker] No job_id in payload")
            return
        
        logging.info(f"[mcp-worker] Starting job {job_id} (type: {job_type})")

        # Get existing creation time
        storage = _get_storage_clients()
        blob_client = storage["blob"].get_blob_client(container=storage["container"], blob=f"{job_id}.json")
        created_at: Optional[str] = None
        try:
            existing = json.loads(blob_client.download_blob().readall().decode("utf-8"))
            created_at = existing.get("createdAt")
        except Exception:
            pass

        # Mark as running (will be updated with model info below)

        # Extract request parameters
        prompt = (body.get("prompt") or "") if isinstance(body, dict) else ""
        user_id = str((body.get("user_id") or "").strip()) if isinstance(body, dict) else ""
        conversation_id_raw = str((body.get("conversation_id") or "").strip()) if isinstance(body, dict) else ""
        conversation_id = conversation_id_raw or None
        if conversation_id and conversation_id.lower() == "init":
            conversation_id = None

        client = create_llm_client()
        
        if job_type == "orchestrate":
            # Use orchestration data from the request
            model = os.getenv("AZURE_OPENAI_MODEL")
            reasoning_effort = body.get("reasoning_effort") or "low"
            mode = body.get("mode", "standard")
            mcp_tool_cfg = body.get("mcp_tool_cfg")
            
            # Build responses args with orchestration data
            responses_args: Dict[str, Any] = build_responses_args(
                model, prompt, mcp_tool_cfg, reasoning_effort
            )
            
            # Mark as running with correct model info
            _update_job_status(job_id, "running", 10, "Thinking...", created_at=created_at, selected_model=model, mode=mode)
            logging.info(f"[mcp-worker] Orchestrate job {job_id}: mode={mode}, model={model}, reasoning_effort={reasoning_effort}")
        elif job_type == "ask":
            # ASK mode - manual model selection
            model = body.get("selected_model")
            reasoning_effort = body.get("reasoning_effort") or "low"
            
            # Resolve MCP config to ensure tools work in ask mode
            mcp_tool_cfg = resolve_mcp_config(body)
            
            # Build basic responses args 
            responses_args: Dict[str, Any] = build_responses_args(
                model, prompt, mcp_tool_cfg, reasoning_effort
            )
            
            # Mark as running with correct model info
            _update_job_status(job_id, "running", 10, "Thinking...", created_at=created_at, selected_model=model, mode="ask")
            logging.info(f"[mcp-worker] Ask job {job_id}: model={model}, reasoning_effort={reasoning_effort}")
        else:
            # MCP mode - original logic
            model = body.get("model") or os.getenv("AZURE_OPENAI_MODEL")
            reasoning_effort = (body.get("reasoning_effort") or "low").lower()
            mcp_tool_cfg = resolve_mcp_config(body)
            
            responses_args: Dict[str, Any] = build_responses_args(
                model, prompt, mcp_tool_cfg, reasoning_effort
            )
            
            # Mark as running with correct model info
            _update_job_status(job_id, "running", 10, "Thinking...", created_at=created_at, selected_model=model, mode="mcp")

        # Handle allowed_tools filtering
        try:
            raw_allowed = body.get("allowed_tools") if isinstance(body, dict) else None
            normalized_allowed = normalize_allowed_tools(raw_allowed)
            if normalized_allowed is not None:
                # Filter tools to only include allowed ones
                if responses_args.get("tools"):
                    filtered = []
                    for t in responses_args["tools"]:
                        # Check both direct name and function.name for different tool types
                        name = t.get("name") or t.get("function", {}).get("name")
                        if name in normalized_allowed:
                            filtered.append(t)
                    responses_args["tools"] = filtered
                    logging.info(f"[mcp-worker] Job {job_id} filtered tools to: {[t.get('name') or t.get('function', {}).get('name') for t in filtered]}")
        except Exception:
            pass

        # Check for classic function tools and reasoning effort
        has_classic_tools = any(
            (t.get("type") == "function") for t in (responses_args.get("tools") or [])
        )
        
        # Determine if this is a reasoning task (deep thinking)
        is_reasoning_task = reasoning_effort in ("medium", "high") or any(
            keyword in prompt.lower() 
            for keyword in ["analyser", "expliquer", "pourquoi", "comment", "stratégie", "plan", "réfléchir", "détaillé"]
        )

        # Add conversation context if available
        try:
            if user_id and conversation_id:
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
                    # Insert conversation history between existing system message and current user prompt
                    current_input = responses_args.get("input", [])
                    system_messages = [msg for msg in current_input if msg.get("role") == "system"]
                    user_messages = [msg for msg in current_input if msg.get("role") == "user"]
                    
                    responses_args["input"] = (
                        system_messages
                        + msgs
                        + user_messages
                    )
        except Exception:
            pass

        output_text: Optional[str] = None
        tools_used_during_run: List[str] = []

        if has_classic_tools:
            # Use synchronous tool loop for classic function tools
            _update_job_status(job_id, "running", 20, "Processing with tools...", created_at=created_at)
            
            # Force explicit tool selection for tool usage
            # If we have exactly one tool after filtering, require its usage
            filtered_tools = responses_args.get("tools", [])
            if len(filtered_tools) == 1:
                responses_args["tool_choice"] = "required"
                logging.info(f"[mcp-worker] Job {job_id} forcing tool usage with tool_choice=required")
            else:
                responses_args["tool_choice"] = "auto"
            
            # Create context with user_id for tools
            tool_context = {"user_id": user_id} if user_id else None
            output_text, response = run_responses_with_tools(client, responses_args, tool_context)
            
            # Log the final output for debugging
            logging.info(f"[mcp-worker] Job {job_id} completed tool execution. Output length: {len(output_text) if output_text else 0}")
            if output_text and len(output_text) < 500:
                logging.info(f"[mcp-worker] Job {job_id} output: {output_text}")
            elif output_text:
                logging.info(f"[mcp-worker] Job {job_id} output (truncated): {output_text[:400]}...")
            
            # Extract actual tools used from response metadata
            try:
                actual_tools_used = getattr(response, "_classic_tools_used", [])
                logging.info(f"[mcp-worker] Job {job_id} tools metadata: {actual_tools_used}")
                
                if actual_tools_used and isinstance(actual_tools_used, list):
                    for tool_info in actual_tools_used:
                        if isinstance(tool_info, dict) and "name" in tool_info:
                            tool_name = tool_info["name"]
                            tool_args = tool_info.get("arguments", {})
                            logging.info(f"[mcp-worker] Job {job_id} used tool: {tool_name} with args: {tool_args}")
                            
                            if tool_name not in tools_used_during_run:
                                tools_used_during_run.append(tool_name)
                            
                            # Update status with specific tool message
                            if tool_name.lower() == "search_web":
                                _update_job_status(job_id, "running", 50, "Web search in progress...", tool=tool_name, created_at=created_at)
                            elif tool_name in ["list_templates_http", "list_images", "convert_word_to_pdf"]:
                                _update_job_status(job_id, "running", 50, f"Using tool: {tool_name}", tool=tool_name, created_at=created_at)
                            else:
                                _update_job_status(job_id, "running", 50, f"Using tool: {tool_name}", tool=tool_name, created_at=created_at)
            except Exception:
                logging.exception(f"[mcp-worker] Failed to extract tool usage for job {job_id}")
            
            if not output_text:
                try:
                    no_tools_args = dict(responses_args)
                    no_tools_args.pop("tools", None)
                    no_tools_args.pop("tool_choice", None)
                    response = client.responses.create(**no_tools_args)
                    output_text = getattr(response, "output_text", None)
                except Exception:
                    pass
                    
            _update_job_status(job_id, "running", 90, "Finalizing response...", created_at=created_at)
        else:
            # Use streaming for better progress updates
            if is_reasoning_task:
                _update_job_status(job_id, "running", 15, "Deep analysis in progress...", created_at=created_at)
            else:
                _update_job_status(job_id, "running", 15, "Generating response...", created_at=created_at)
            
            partial_chunks: List[str] = []
            progress_value: int = 20
            
            try:
                with client.responses.stream(**responses_args) as stream:
                    for event in stream:
                        if getattr(event, "type", None) == "response.output_text.delta":
                            delta = getattr(event, "delta", "")
                            if delta:
                                partial_chunks.append(delta)
                                progress_value = min(90, progress_value + 2)
                                
                                # Update progress with partial content
                                message = "Deep thinking..." if is_reasoning_task else "Generating..."
                                _update_job_status(
                                    job_id, "running", progress_value, 
                                    message, 
                                    partial_text="".join(partial_chunks),
                                    created_at=created_at
                                )
                                
                    final_response = stream.get_final_response()
                    output_text = getattr(final_response, "output_text", None) or "".join(partial_chunks)
            except Exception:
                logging.exception(f"[mcp-worker] Streaming failed for job {job_id}, falling back to create")
                try:
                    response = client.responses.create(**responses_args)
                    output_text = getattr(response, "output_text", None)
                except Exception as e_inner:
                    logging.exception(f"[mcp-worker] Create failed for job {job_id}")
                    output_text = f"Error: {e_inner}"

        # Mark as completed
        final_status = "completed" if output_text and not output_text.startswith("Error:") else "failed"
        final_message = "Completed" if final_status == "completed" else f"Error: {output_text}"
        
        # Prepare final tool field with all used tools
        final_tool_field = ", ".join(tools_used_during_run) if tools_used_during_run else ""
        
        _update_job_status(
            job_id, final_status, 100, final_message,
            tool=final_tool_field,
            final_text=output_text or "",
            used_tools=tools_used_during_run,
            created_at=created_at
        )

        # Save conversation turn if configured
        try:
            if user_id and conversation_id and output_text:
                cosmos_upsert_conversation_turn(user_id, conversation_id, prompt, output_text)
        except Exception:
            logging.exception(f"[mcp-worker] Failed to save conversation for job {job_id}")

        logging.info(f"[mcp-worker] Job {job_id} completed successfully")

    except Exception as e:
        logging.exception(f"[mcp-worker] Job {job_id} processing failed: {e}")
        try:
            if job_id:
                _update_job_status(job_id, "failed", 100, f"Error: {str(e)}")
        except Exception:
            logging.exception(f"[mcp-worker] Failed to mark job {job_id} as failed")
        # Re-raise to trigger Azure Functions retry mechanism
        raise