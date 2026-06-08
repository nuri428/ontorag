"""Single vs multi-agent loop selection at the chat-endpoint boundary.

The selector reads the ``AGENT_MODE`` environment variable and returns
a freshly-constructed agent that matches one of two shapes:

* ``AGENT_MODE=single`` (default) — :class:`~ontorag.chat.agent.AgentLoop`.
  Behaviour unchanged from v1.1.
* ``AGENT_MODE=multi`` — :class:`~ontorag.chat.multi_agent.loop.MultiAgentLoop`.
  Adds router + evaluator + Persistence loop on top of the same
  ``AgentLoop`` per iteration.

Both classes expose the same ``run(user_message)`` async-generator
contract, so the chat route can iterate the result without caring
which mode is active. The selector exists only to centralise the env
var lookup and the constructor wiring.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Union

from ontorag.chat.agent import AgentLoop
from ontorag.chat.multi_agent.loop import MultiAgentLoop
from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import GraphStore

logger = logging.getLogger(__name__)


AGENT_MODE_SINGLE = "single"
AGENT_MODE_MULTI = "multi"
_VALID_MODES = frozenset({AGENT_MODE_SINGLE, AGENT_MODE_MULTI})
_ENV_AGENT_MODE = "AGENT_MODE"

# Union of the two agent shapes. Both expose ``run(user_message)`` as
# an async generator yielding SSE event dicts — that is the protocol
# the chat route depends on.
ChatAgent = Union[AgentLoop, MultiAgentLoop]


def get_agent_mode() -> str:
    """Return the active agent mode from the environment.

    Falls back to :data:`AGENT_MODE_SINGLE` when unset or invalid so a
    typo in the env var can never silently break production behaviour.
    """
    raw = os.environ.get(_ENV_AGENT_MODE, AGENT_MODE_SINGLE).strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            "AGENT_MODE=%r is not recognised; falling back to %s",
            raw,
            AGENT_MODE_SINGLE,
        )
        return AGENT_MODE_SINGLE
    return raw


def make_chat_agent(
    store: GraphStore,
    llm: LLMProvider,
    *,
    schema_context: str | None = None,
    initial_history: list[dict[str, Any]] | None = None,
    has_ontology_data: bool = False,
    bayes_engine: Any | None = None,
    max_iterations: int | None = None,
) -> ChatAgent:
    """Construct the right agent for the current ``AGENT_MODE``.

    The constructor arguments are the union of what the two agent
    shapes need. Single mode ignores ``bayes_engine`` and
    ``max_iterations`` — they are forwarded to multi mode only.
    """
    mode = get_agent_mode()
    if mode == AGENT_MODE_MULTI:
        kwargs: dict[str, Any] = {
            "store": store,
            "llm": llm,
            "schema_context": schema_context,
            "initial_history": initial_history,
            "has_ontology_data": has_ontology_data,
            "bayes_engine": bayes_engine,
        }
        if max_iterations is not None:
            kwargs["max_iterations"] = max_iterations
        return MultiAgentLoop(**kwargs)
    return AgentLoop(
        store=store,
        llm=llm,
        schema_context=schema_context,
        initial_history=initial_history,
        has_ontology_data=has_ontology_data,
    )
