import os
import json
from typing import Any, Dict, Optional, List, Tuple


def normalize_allowed_tools(raw_value: Any) -> Optional[List[str]]:
    try:
        if isinstance(raw_value, list):
            if len(raw_value) == 1 and isinstance(raw_value[0], str):
                single = raw_value[0].strip()
                if single.startswith("[") and single.endswith("]"):
                    parsed = json.loads(single)
                    return [str(x) for x in parsed if isinstance(x, (str, int, float))]
                if "," in single:
                    return [p.strip() for p in single.split(",") if p.strip()]
            return [str(x) for x in raw_value if isinstance(x, (str, int, float))]
        if isinstance(raw_value, str):
            trimmed = raw_value.strip()
            if trimmed.startswith("[") and trimmed.endswith("]"):
                parsed = json.loads(trimmed)
                return [str(x) for x in parsed if isinstance(x, (str, int, float))]
            if "," in trimmed:
                return [p.strip() for p in trimmed.split(",") if p.strip()]
            if trimmed:
                return [trimmed]
    except Exception:
        pass
    return None


def resolve_mcp_config(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Prefer locked server-to-server config
    # Allow override via request only when explicitly enabled
    allow_override = "false"

    if allow_override and body.get("mcp_url"):
        server_url = body.get("mcp_url")
        headers: Optional[Dict[str, str]] = None
        if isinstance(body.get("mcp_headers"), dict):
            headers = {str(k): str(v) for k, v in body["mcp_headers"].items()}
        else:
            key = body.get("mcp_key")
            if key:
                headers = {"x-functions-key": key}
    else:
        server_url = os.getenv("TOOLS_SSE_URL") 
        key = os.getenv("TOOLS_FUNCTIONS_KEY") 
        headers = {"x-functions-key": key} if key else None

    if not server_url:
        # If allowed_tools is empty or explicitly disabled, we return None to run without tools
        normalized_allowed = normalize_allowed_tools(body.get("allowed_tools"))
        if not normalized_allowed:
            return None
        # Otherwise, still error because tools were requested but no server URL is configured
        raise ValueError("Missing MCP SSE URL. Configure TOOLS_SSE_URL.")

    # Determine allow-list behavior based on caller intent
    raw_allowed = body.get("allowed_tools")
    # Explicit disable: empty list
    if isinstance(raw_allowed, list) and len(raw_allowed) == 0:
        return None
    # '*' means allow all → omit allow-list entirely
    if (isinstance(raw_allowed, str) and raw_allowed.strip() == "*") or (
        isinstance(raw_allowed, list) and len(raw_allowed) == 1 and str(raw_allowed[0]).strip() == "*"
    ):
        allowed_tools = None
    else:
        normalized_allowed = normalize_allowed_tools(raw_allowed)
        if normalized_allowed is not None:
            allowed_tools = normalized_allowed
        else:
            # If caller did not specify anything, keep a conservative default list
            allowed_tools = ["hello_mcp"]

    def _normalize_require_approval(val: Any) -> str:
        try:
            if isinstance(val, str):
                v = val.strip().lower()
                if v in ("never", "initial", "always"):
                    return v
            if val is True:
                return "always"
            if val is False or val is None:
                return "never"
        except Exception:
            pass
        return "never"

    require_approval = _normalize_require_approval(body.get("require_approval"))
    tool_cfg = {
        "type": "mcp",
        "server_label": body.get("server_label") or ("remote-mcp-function" if server_url.startswith("https://") else "local-mcp-function"),
        "server_url": server_url,
        "require_approval": require_approval,
        **({"headers": headers} if headers else {}),
    }
    # Only include allow-list when restricting; omit to allow all tools
    if allowed_tools is not None:
        tool_cfg["allowed_tools"] = allowed_tools
    return tool_cfg


# --- Built-in classic tools (non-MCP) ---------------------------------------------------------

def _websearch_env() -> Tuple[Optional[str], Optional[str]]:
    url = os.getenv("WEBSEARCH_FUNCTION_URL")
    key = os.getenv("WEBSEARCH_FUNCTION_KEY")
    return (url, key)


def _build_search_web_tool_def() -> Dict[str, Any]:
    return {
        "type": "function",
        "name": "search_web",
        "description": "Perform a web search via Azure Function (SearXNG) with optional focus modes.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "focus_mode": {
                    "type": "string",
                    "description": "Optional focus mode to steer engines/categories.",
                    "enum": [
                        "webSearch",
                        "academicSearch",
                        "wolframAlphaSearch",
                        "youtubeSearch",
                        "imageSearch",
                        "socialSearch",
                        "newsSearch"
                    ]
                },
                "question": {"type": "string", "description": "User question prompting the search (optional)."},
                "user_language": {"type": "string", "description": "User language hint, e.g., 'fr' or 'en' (optional)."},
                "context": {"type": "string", "description": "Optional context to help summarize results."}
            },
            "required": ["query"]
        }
    }


def get_builtin_tools_config() -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    tools.append(_build_search_web_tool_def())
    tools.extend(_build_docsvc_tool_defs())
    return tools


def has_builtin_tools() -> bool:
    return len(get_builtin_tools_config()) > 0


def _call_websearch_backend(args: Dict[str, Any]) -> str:
    import logging
    url, key = _websearch_env()
    logging.info(f"[websearch] Backend URL: {_redact_secrets(url or 'None')}")
    
    if not url:
        logging.error("[websearch] Backend not configured")
        return "Websearch backend not configured."
    # Build URL with key only if not already embedded
    final_url = url
    if key and ("?code=" not in url) and ("&code=" not in url):
        sep = "&" if ("?" in url) else "?"
        final_url = f"{url}{sep}code={key}"
    
    logging.info(f"[websearch] Final URL: {_redact_secrets(final_url)}")
    payload: Dict[str, Any] = {}
    if isinstance(args, dict):
        query = str(args.get("query") or "").strip()
        if query:
            # Try both 'query' and 'q' formats for compatibility
            payload["query"] = query
            payload["q"] = query
        focus = str(args.get("focus_mode") or "").strip()
        question = str(args.get("question") or "").strip() or query
        if question:
            payload["question"] = question
        user_language = str(args.get("user_language") or "").strip()
        if user_language:
            payload["user_language"] = user_language
        context_txt = str(args.get("context") or "").strip()
        if context_txt:
            payload["context"] = context_txt
        # Heuristic focus mode if not provided
        if not focus:
            text = f"{query} {question} {context_txt}".lower()
            if any(k in text for k in ("arxiv", "scholar", "pubmed", "doi", "preprint", "paper")):
                focus = "academicSearch"
            elif any(k in text for k in ("wolfram", "derivative", "integral", "equation", "solve", "compute")):
                focus = "wolframAlphaSearch"
            elif any(k in text for k in ("youtube", "video", "channel", "watch")):
                focus = "youtubeSearch"
            elif any(k in text for k in ("image", "images", "photo", "jpg", "png", "gif", "diagram", "logo")):
                focus = "imageSearch"
            elif any(k in text for k in ("twitter", "x.com", "reddit", "mastodon", "instagram", "tiktok", "linkedin", "hacker news", "lobsters")):
                focus = "socialSearch"
            elif any(k in text for k in ("weather", "météo", "today", "now", "forecast", "news", "breaking", "update")):
                focus = "newsSearch"
            else:
                focus = "webSearch"
        payload["focus_mode"] = focus
    
    logging.info(f"[websearch] Request payload: {payload}")
    
    # Lazy import to avoid module-level failure
    try:
        import requests  # type: ignore
    except Exception as e:
        logging.error(f"[websearch] Requests import failed: {e}")
        return f"requests import failed: {e}"
    try:
        # SearxNG expects form data, not JSON
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        # Convert payload to form data format, focusing on the essential 'q' parameter
        form_data = {}
        if payload.get("query"):
            form_data["q"] = payload["query"]
        if payload.get("focus_mode"):
            form_data["format"] = "json"  # Ensure JSON response
        
        logging.info(f"[websearch] Sending POST request to {_redact_secrets(final_url)}")
        logging.info(f"[websearch] Form data: {form_data}")
        resp = requests.post(final_url, headers=headers, data=form_data, timeout=30)
        logging.info(f"[websearch] Response status: {resp.status_code}")
        text = resp.text or ""
        logging.info(f"[websearch] Response text length: {len(text)}")
        if len(text) < 300:
            logging.info(f"[websearch] Response text: {text}")
        else:
            logging.info(f"[websearch] Response text (truncated): {text[:250]}...")
            
        try:
            data = resp.json()
            logging.info(f"[websearch] Response JSON parsed successfully. Keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
        except Exception as e:
            logging.warning(f"[websearch] Failed to parse JSON response: {e}")
            data = None
            
        if resp.status_code >= 400:
            safe_text = _redact_secrets(text)
            logging.error(f"[websearch] HTTP error {resp.status_code}: {safe_text[:200]}")
            return f"websearch error {resp.status_code}: {safe_text[:500]}"
        # Prefer structured fields when available
        if isinstance(data, dict):
            logging.info(f"[websearch] Processing structured response with keys: {list(data.keys())}")
            for key in ("output_text", "summary", "result", "content"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    logging.info(f"[websearch] Found result in field '{key}': {val[:100]}...")
                    return val
            # Fallback: pretty-print compact json but check if actually empty
            if not any(data.values()) or data.get('error'):
                error_msg = f"No search results found. Service returned: {json.dumps(data, ensure_ascii=False)[:200]}"
                logging.warning(f"[websearch] Empty or error response: {error_msg}")
                return error_msg
            try:
                fallback_result = json.dumps(data, ensure_ascii=False)
                logging.info(f"[websearch] Returning JSON fallback: {fallback_result[:100]}...")
                return fallback_result
            except Exception:
                logging.warning("[websearch] Failed to serialize response JSON, returning raw text")
                return text
        if not text.strip():
            logging.warning("[websearch] No text content in response")
            return "No search results returned by the websearch service."
        logging.info(f"[websearch] Returning raw text response: {text[:100]}...")
        return text
    except requests.exceptions.ConnectTimeout:
        error_msg = f"Websearch service timeout. Check if service is running at {_redact_secrets(final_url)}"
        logging.error(f"[websearch] {error_msg}")
        return error_msg
    except requests.exceptions.ConnectionError:
        error_msg = f"Cannot connect to websearch service. Check if service is running at {_redact_secrets(final_url)}"
        logging.error(f"[websearch] {error_msg}")
        return error_msg
    except Exception as e:
        error_msg = f"websearch call failed: {_redact_secrets(str(e))}"
        logging.error(f"[websearch] {error_msg}")
        return error_msg


def execute_tool_call(name: str, arguments: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    import logging
    tool = (name or "").strip().lower()
    logging.info(f"[tools] Executing tool: {name} with arguments: {arguments}")
    
    if tool in ("search_web", "websearch"):
        result = _call_websearch_backend(arguments or {})
        logging.info(f"[tools] Tool {name} result length: {len(result)} chars")
        if len(result) < 200:
            logging.info(f"[tools] Tool {name} result: {result}")
        else:
            logging.info(f"[tools] Tool {name} result (truncated): {result[:150]}...")
        return result
    # Document service tools
    if tool == "convert_word_to_pdf":
        result = _docsvc_convert_word_to_pdf(arguments or {})
        logging.info(f"[tools] Tool {name} result: {result[:100]}...")
        return result
    if tool == "init_user":
        result = _docsvc_init_user(arguments or {}, context)
        logging.info(f"[tools] Tool {name} result: {result[:100]}...")
        return result
    if tool == "list_images":
        result = _docsvc_list_images(arguments or {}, context)
        logging.info(f"[tools] Tool {name} result: {result[:100]}...")
        return result
    if tool == "list_shared_templates":
        result = _docsvc_list_shared_templates(arguments or {})
        logging.info(f"[tools] Tool {name} result: {result[:100]}...")
        return result
    if tool == "list_templates_http":
        result = _docsvc_list_templates_http(arguments or {}, context)
        logging.info(f"[tools] Tool {name} result: {result[:100]}...")
        return result
    
    logging.error(f"[tools] Unknown tool requested: {name}")
    return f"Unknown tool: {name}"



# --- Document service (classic tools grouped) --------------------------------------------------

def _redact_secrets(text: str) -> str:
    try:
        val = str(text or "")
    except Exception:
        return ""
    try:
        import re
        # redact code=... (function keys) up to next & or end
        val = re.sub(r"(code=)[^&\s]+", r"\1***", val, flags=re.IGNORECASE)
    except Exception:
        pass
    return val

def _docsvc_env() -> Tuple[Optional[str], Optional[str]]:
    base = os.getenv("DOCSVC_BASE_URL") 
    key = os.getenv("DOCSVC_FUNCTION_KEY")
    return (base, key)


def _docsvc_build_url(path_template: str, path_params: Optional[Dict[str, str]] = None) -> str:
    base, key = _docsvc_env()
    if not base:
        return ""
    base = base.strip()
    # Ensure scheme
    lower = base.lower()
    if not lower.startswith("http://") and not lower.startswith("https://"):
        if "localhost" in lower or "127.0.0.1" in lower:
            base = f"http://{base}"
        else:
            base = f"https://{base}"
    base = base.rstrip("/")
    path = path_template.format(**(path_params or {}))
    if not path.startswith("/"):
        path = "/" + path
    final_url = f"{base}{path}"
    if key and ("?code=" not in final_url) and ("&code=" not in final_url):
        sep = "&" if ("?" in final_url) else "?"
        final_url = f"{final_url}{sep}code={key}"
    return final_url


def _docsvc_request(method: str, path_template: str, *, path_params: Optional[Dict[str, str]] = None, json_body: Optional[Dict[str, Any]] = None, timeout: int = 60) -> str:
    url = _docsvc_build_url(path_template, path_params)
    if not url:
        return "Document service backend not configured. Set DOCSVC_BASE_URL."
    try:
        import requests  # type: ignore
    except Exception as e:
        return f"requests import failed: {e}"
    try:
        headers = {"Content-Type": "application/json"}
        method_upper = (method or "GET").upper()
        if method_upper == "GET":
            resp = requests.get(url, headers=headers, timeout=timeout)
        elif method_upper == "POST":
            resp = requests.post(url, headers=headers, data=json.dumps(json_body or {}), timeout=timeout)
        else:
            return f"Unsupported method: {method}"
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = None
        if resp.status_code >= 400:
            safe_text = _redact_secrets(text)
            return f"docsvc error {resp.status_code}: {safe_text[:500]}"
        if isinstance(data, dict):
            try:
                return json.dumps(data, ensure_ascii=False)
            except Exception:
                return text
        if isinstance(data, list):
            try:
                return json.dumps(data, ensure_ascii=False)
            except Exception:
                return text
        return _redact_secrets(text)
    except Exception as e:
        return f"docsvc call failed: {_redact_secrets(str(e))}"


def _build_docsvc_tool_defs() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "convert_word_to_pdf",
            "description": "Convert a Microsoft Word document (stored in blob) to PDF via document service.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "blob": {"type": "string", "description": "Blob path under the container, e.g., 'user123/new.docx'."}
                },
                "required": ["blob"]
            },
        },
        {
            "type": "function",
            "name": "init_user",
            "description": "Initialize the user's blob container.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (optional, will use current user if not provided)."}
                },
                "required": []
            },
        },
        {
            "type": "function",
            "name": "list_images",
            "description": "List images available in the user's blob container.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (optional, will use current user if not provided)."}
                },
                "required": []
            },
        },
        {
            "type": "function",
            "name": "list_shared_templates",
            "description": "List shared templates.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {}
            },
        },
        {
            "type": "function",
            "name": "list_templates_http",
            "description": "List templates for a specific user.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier (optional, will use current user if not provided)."}
                },
                "required": []
            },
        },
    ]


def _docsvc_convert_word_to_pdf(args: Dict[str, Any]) -> str:
    blob = str(args.get("blob") or "").strip()
    if not blob:
        return "Missing required parameter: blob"
    # Endpoint expects ?blob=<path> as query param, no body required
    # Build full path with query
    path = f"/convert/word-to-pdf?blob={blob}"
    # We still POST but without body
    return _docsvc_request("POST", path)


def _docsvc_init_user(args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    user_id = str(args.get("user_id") or "").strip()
    # If user_id not provided in args, try to get from context
    if not user_id and context:
        user_id = str(context.get("user_id") or "").strip()
    if not user_id:
        return "Missing required parameter: user_id"
    resp_text = _docsvc_request("POST", "/users/{userId}/init", path_params={"userId": user_id})
    # Prefer a concise human-readable confirmation when possible
    try:
        parsed = json.loads(resp_text)
        if isinstance(parsed, dict):
            created = parsed.get("created")
            num_created = len(created) if isinstance(created, list) else None
            if num_created is not None:
                return f"Initialized user blob container for '{parsed.get('userId') or user_id}'. Created {num_created} placeholder items."
            if parsed.get("userId"):
                return f"Initialized user blob container for '{parsed.get('userId')}'."
    except Exception:
        pass
    return resp_text


def _docsvc_list_images(args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    user_id = str(args.get("user_id") or "").strip()
    # If user_id not provided in args, try to get from context
    if not user_id and context:
        user_id = str(context.get("user_id") or "").strip()
    if not user_id:
        return "Missing required parameter: user_id"
    return _docsvc_request("GET", "/users/{userId}/images", path_params={"userId": user_id})


def _docsvc_list_shared_templates(args: Dict[str, Any]) -> str:
    return _docsvc_request("GET", "/templates")


def _docsvc_list_templates_http(args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
    user_id = str(args.get("user_id") or "").strip()
    # If user_id not provided in args, try to get from context
    if not user_id and context:
        user_id = str(context.get("user_id") or "").strip()
    if not user_id:
        return "Missing required parameter: user_id"
    return _docsvc_request("GET", "/users/{userId}/templates", path_params={"userId": user_id})
