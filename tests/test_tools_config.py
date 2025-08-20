from app.services.tools import get_builtin_tools_config


def _has_search_web(tools):
    return any(
        t.get("name") == "search_web" or t.get("function", {}).get("name") == "search_web"
        for t in tools
    )


def test_search_web_not_present_when_backend_missing(monkeypatch):
    monkeypatch.delenv("WEBSEARCH_FUNCTION_URL", raising=False)
    monkeypatch.delenv("WEBSEARCH_FUNCTION_KEY", raising=False)
    tools = get_builtin_tools_config()
    assert not _has_search_web(tools)


def test_search_web_present_when_backend_configured(monkeypatch):
    monkeypatch.setenv("WEBSEARCH_FUNCTION_URL", "https://example.com")
    monkeypatch.setenv("WEBSEARCH_FUNCTION_KEY", "secret")
    tools = get_builtin_tools_config()
    assert _has_search_web(tools)
