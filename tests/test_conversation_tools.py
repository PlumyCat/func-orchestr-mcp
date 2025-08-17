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

    def create(self, **kwargs):
        call = SimpleNamespace(
            id="call-search",
            function=SimpleNamespace(name="search_web", arguments=json.dumps({"query": "hello"})),
        )
        submit = SimpleNamespace(tool_calls=[call])
        required = SimpleNamespace(submit_tool_outputs=submit)
        return SimpleNamespace(id="resp1", status="requires_action", required_action=required)

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
            return SimpleNamespace(id="resp2", status="requires_action", required_action=required)
        # final response
        return SimpleNamespace(id="resp3", status="completed", output_text="final output")


class FakeClient:
    def __init__(self, model):
        self.responses = FakeResponses(model)


@pytest.mark.parametrize("model_name", ["model-router", "gpt-oss-120b"])
def test_multiple_tool_iterations_with_special_models(model_name, caplog, monkeypatch):
    fake_client = FakeClient(model_name)
    executed = []

    def fake_execute(name, args):
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
