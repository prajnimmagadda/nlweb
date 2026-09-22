"""A scripted stand-in for the Anthropic client, so runner and eval tests never call the API."""

import copy
import itertools
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from anthropic.types.beta import BetaMessage

_ids = itertools.count(1)


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(name: str, arguments: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id or f"toolu_{next(_ids)}", "name": name, "input": arguments}


def message(*content: dict[str, Any], stop_reason: str | None = None, model: str = "claude-opus-5",
            usage: dict[str, Any] | None = None, **extra: Any) -> BetaMessage:
    """A BetaMessage; stop_reason defaults to tool_use when the content calls a tool, else end_turn."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(c["type"] == "tool_use" for c in content) else "end_turn"
    return BetaMessage.model_validate({
        "id": f"msg_{next(_ids)}", "type": "message", "role": "assistant", "model": model,
        "content": list(content), "stop_reason": stop_reason, "stop_sequence": None,
        "usage": usage or {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80,
                           "cache_creation_input_tokens": 0},
        **extra,
    })


Step = BetaMessage | Exception | Callable[[dict[str, Any]], BetaMessage]


class ScriptedClient:
    """Mimics `anthropic.Anthropic().beta.messages.create`, replaying a script and recording requests."""

    def __init__(self, script: list[Step]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> BetaMessage:
        self.requests.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        if not self.script:
            raise AssertionError("the runner made more requests than the script has steps")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(kwargs) if callable(step) else step

    def tool_results(self, request_index: int) -> list[dict[str, Any]]:
        """The tool_result blocks the runner sent in a given request."""
        last = self.requests[request_index]["messages"][-1]
        return [block for block in last["content"] if block["type"] == "tool_result"]
