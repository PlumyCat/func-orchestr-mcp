import sys
import pathlib
import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.services import tools


def test_docsvc_tools_use_context_for_user_id(monkeypatch):
    calls = []

    def fake_request(method, path_template, *, path_params=None, json_body=None, timeout=60):
        calls.append((method, path_template, path_params))
        return "ok"

    monkeypatch.setattr(tools, "_docsvc_request", fake_request)

    ctx = {"user_id": "u123"}

    init_result = tools.execute_tool_call("init_user", {}, ctx)
    images_result = tools.execute_tool_call("list_images", {}, ctx)
    templates_result = tools.execute_tool_call("list_templates_http", {}, ctx)

    assert init_result == "ok"
    assert images_result == "ok"
    assert templates_result == "ok"

    assert calls[0] == ("POST", "/users/{userId}/init", {"userId": "u123"})
    assert calls[1] == ("GET", "/users/{userId}/images", {"userId": "u123"})
    assert calls[2] == ("GET", "/users/{userId}/templates", {"userId": "u123"})
