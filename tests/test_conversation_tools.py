import json
import logging
from types import SimpleNamespace
import sys
import pathlib

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.services import conversation


class FakeResponses:
    def __init__(self, model):
        self.model = model
        self.submit_models = []
        self._store = {}

    def create(self, **kwargs):
        call = SimpleNamespace(
            id="call-search",
            function=SimpleNamespace(name="search_web", arguments=json.dumps({"query": "hello"})),
        )
        submit = SimpleNamespace(tool_calls=[call])
        required = SimpleNamespace(submit_tool_outputs=submit)
        resp = SimpleNamespace(id="resp1", status="requires_action", required_action=required)
        self._store[resp.id] = resp
        return resp

    def submit_tool_outputs(self, response_id, tool_outputs, model=None):
        # record model parameter used for submit
        self.submit_models.append(model)
        if response_id == "resp1":
            call = SimpleNamespace(
                id="call-convert",
                function=SimpleNamespace(
                    name="convert_word_to_pdf", arguments=json.dumps({"blob": "file.docx"})
                ),
            )
            submit = SimpleNamespace(tool_calls=[call])
            required = SimpleNamespace(submit_tool_outputs=submit)
            resp = SimpleNamespace(id="resp2", status="requires_action", required_action=required)
        else:
            # final response
            resp = SimpleNamespace(id="resp3", status="completed", output_text="final output")
        self._store[resp.id] = resp
        return resp

    def wait(self, id, **kwargs):
        return self._store[id]


class FakeClient:
    def __init__(self, model):
        self.responses = FakeResponses(model)


@pytest.mark.parametrize("model_name", ["model-router", "gpt-oss-120b"])
def test_multiple_tool_iterations_with_special_models(model_name, caplog, monkeypatch):
    fake_client = FakeClient(model_name)
    executed = []

    def fake_execute(name, args, context=None):
        executed.append(name)
        return f"{name}-result"
    from app.services import tools as tools_module
    monkeypatch.setattr(tools_module, "execute_tool_call", fake_execute)
    caplog.set_level(logging.INFO)

    args = {
        "model": model_name,
        "input": [],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }

    output_text, _ = conversation.run_responses_with_tools(
        fake_client, args, allow_post_synthesis=False
    )

    assert output_text == "final output"
    assert executed == ["search_web", "convert_word_to_pdf"]
    assert fake_client.responses.submit_models == [model_name, model_name]
    messages = [rec.message for rec in caplog.records]
    assert any("iteration 1" in msg for msg in messages)
    assert any("iteration 2" in msg for msg in messages)


class LongChainResponses:
    def __init__(self, chain_length):
        self.chain_length = chain_length
        self.step = 0
        self._store = {}

    def _build_response(self):
        if self.step < self.chain_length:
            call = SimpleNamespace(
                id=f"call{self.step}",
                function=SimpleNamespace(name=f"tool{self.step}", arguments=json.dumps({})),
            )
            submit = SimpleNamespace(tool_calls=[call])
            required = SimpleNamespace(submit_tool_outputs=submit)
            resp = SimpleNamespace(
                id=f"resp{self.step}", status="requires_action", required_action=required
            )
        else:
            resp = SimpleNamespace(id=f"resp{self.step}", status="completed", output_text="done")
        self._store[resp.id] = resp
        return resp

    def create(self, **kwargs):
        return self._build_response()

    def submit_tool_outputs(self, response_id, tool_outputs, model=None):
        self.step += 1
        return self._build_response()

    def wait(self, id, **kwargs):
        return self._store[id]


class LongChainClient:
    def __init__(self, chain_length):
        self.responses = LongChainResponses(chain_length)


@pytest.mark.parametrize("limit, expect_done", [(10, True), (5, False)])
def test_long_tool_chain(monkeypatch, limit, expect_done, caplog):
    monkeypatch.setenv("MAX_TOOL_LOOPS", str(limit))
    fake_client = LongChainClient(chain_length=7)
    executed = []

    def fake_execute(name, args, context=None):
        executed.append(name)
        return "ok"

    from app.services import tools as tools_module

    monkeypatch.setattr(tools_module, "execute_tool_call", fake_execute)
    caplog.set_level(logging.INFO)

    args = {
        "model": "gpt-4.1",
        "input": [],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }

    output_text, resp = conversation.run_responses_with_tools(
        fake_client, args, allow_post_synthesis=False
    )

    if expect_done:
        assert output_text == "done"
        assert len(executed) == 7
    else:
        assert output_text is None
        assert len(executed) == limit
        assert getattr(resp, "status", None) == "requires_action"
        assert any("tool loop limit" in rec.message for rec in caplog.records)


class FakeMCPResponses:
    def __init__(self):
        self.submissions = []
        self._store = {}

    def create(self, **kwargs):
        call = SimpleNamespace(id="mcp-call1", type="mcp", mcp=SimpleNamespace(method="one"))
        submit = SimpleNamespace(tool_calls=[call])
        required = SimpleNamespace(submit_tool_outputs=submit)
        resp = SimpleNamespace(id="resp1", status="requires_action", required_action=required)
        self._store[resp.id] = resp
        return resp

    def submit_tool_outputs(self, response_id, tool_outputs, model=None):
        self.submissions.append((response_id, tool_outputs))
        if response_id == "resp1":
            call = SimpleNamespace(id="mcp-call2", type="mcp", mcp=SimpleNamespace(method="two"))
            submit = SimpleNamespace(tool_calls=[call])
            required = SimpleNamespace(submit_tool_outputs=submit)
            resp = SimpleNamespace(id="resp2", status="requires_action", required_action=required)
        else:
            resp = SimpleNamespace(id="resp3", status="completed", output_text="mcp done")
        self._store[resp.id] = resp
        return resp

    def wait(self, id, **kwargs):
        return self._store[id]


class FakeMCPClient:
    def __init__(self):
        self.responses = FakeMCPResponses()


def test_mcp_tool_loop(monkeypatch):
    fake_client = FakeMCPClient()
    # ensure classic execute_tool_call not used
    from app.services import tools as tools_module
    called = []

    def fake_execute(name, args, context=None):
        called.append(name)
        return "unused"

    monkeypatch.setattr(tools_module, "execute_tool_call", fake_execute)

    args = {
        "model": "gpt-4.1",
        "input": [],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }

    output_text, _ = conversation.run_responses_with_tools(
        fake_client, args, allow_post_synthesis=False
    )

    assert output_text == "mcp done"
    assert len(fake_client.responses.submissions) == 2
    assert all(len(outs) == 1 for _, outs in fake_client.responses.submissions)
    assert called == []


class FakeContextResponses:
    def __init__(self):
        self._store = {}

    def create(self, **kwargs):
        call = SimpleNamespace(
            id="call1",
            function=SimpleNamespace(name="list_images", arguments=json.dumps({})),
        )
        submit = SimpleNamespace(tool_calls=[call])
        required = SimpleNamespace(submit_tool_outputs=submit)
        resp = SimpleNamespace(id="resp1", status="requires_action", required_action=required)
        self._store[resp.id] = resp
        return resp

    def submit_tool_outputs(self, response_id, tool_outputs, model=None):
        return SimpleNamespace(id="resp2", status="completed", output_text="ok")

    def wait(self, id, **kwargs):
        return self._store[id]


class FakeContextClient:
    def __init__(self):
        self.responses = FakeContextResponses()


def test_tool_context_propagated(monkeypatch):
    fake_client = FakeContextClient()
    from app.services import tools as tools_module

    received = {}

    def fake_execute(name, args, context=None):
        received["name"] = name
        received["args"] = args
        received["context"] = context
        return "listed"

    monkeypatch.setattr(tools_module, "execute_tool_call", fake_execute)

    args = {
        "model": "gpt-4.1",
        "input": [],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }

    output_text, _ = conversation.run_responses_with_tools(
        fake_client,
        args,
        allow_post_synthesis=False,
        tool_context={"user_id": "u123"},
    )

    assert output_text == "ok"
    assert received["name"] == "list_images"
    assert received["args"] == {}
    assert received["context"] == {"user_id": "u123"}


class FakeInProgressResponses:
    def __init__(self):
        self.stage = 0

    def create(self, **kwargs):
        return SimpleNamespace(id="resp-initial", status="in_progress")

    def wait(self, id, **kwargs):
        if id == "resp-initial" and self.stage == 0:
            self.stage = 1
            call = SimpleNamespace(
                id="call-search",
                function=SimpleNamespace(name="search_web", arguments=json.dumps({"query": "hello"})),
            )
            submit = SimpleNamespace(tool_calls=[call])
            required = SimpleNamespace(submit_tool_outputs=submit)
            return SimpleNamespace(id="resp-initial", status="requires_action", required_action=required)
        if id == "resp-after-submit" and self.stage == 2:
            self.stage = 3
            return SimpleNamespace(id="resp-after-submit", status="completed", output_text="final output")
        return SimpleNamespace(id=id, status="completed")

    def submit_tool_outputs(self, response_id, tool_outputs, model=None):
        self.stage = 2
        return SimpleNamespace(id="resp-after-submit", status="in_progress")


class FakeInProgressClient:
    def __init__(self):
        self.responses = FakeInProgressResponses()


def test_in_progress_polling(monkeypatch):
    fake_client = FakeInProgressClient()
    from app.services import tools as tools_module
    executed = []

    def fake_execute(name, args, context=None):
        executed.append(name)
        return "search-result"

    monkeypatch.setattr(tools_module, "execute_tool_call", fake_execute)

    args = {
        "model": "gpt-4.1",
        "input": [],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }

    output_text, _ = conversation.run_responses_with_tools(
        fake_client, args, allow_post_synthesis=False
    )

    assert output_text == "final output"
    assert executed == ["search_web"]


class FakeCompletedResponses:
    def create(self, **kwargs):
        return SimpleNamespace(id="r1", status="completed", output_text="done")

    def wait(self, id, **kwargs):
        return SimpleNamespace(id="r1", status="completed", output_text="done")


class FakeCompletedClient:
    def __init__(self):
        self.responses = FakeCompletedResponses()


def test_no_websearch_when_not_allowed(monkeypatch):
    fake_client = FakeCompletedClient()
    from app.services import tools as tools_module
    calls = []

    def fake_execute(name, args, context=None):
        calls.append(name)
        return "unused"

    monkeypatch.setattr(tools_module, "execute_tool_call", fake_execute)

    args = {
        "model": "gpt-4.1",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "what is the weather in Paris?"}]}
        ],
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "store": False,
    }

    output_text, _ = conversation.run_responses_with_tools(fake_client, args)

    assert output_text == "done"
    assert calls == []
