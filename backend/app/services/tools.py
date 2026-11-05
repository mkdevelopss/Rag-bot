"""Agent tools: built-in knowledge-base tools plus per-namespace HTTP tools.

HTTP tools let other apps (MarketBid, DistributionOS, ...) expose live data
such as order status or stock levels to the agent without changing this code.
They are declared in agent_config.json; see README "Connecting your apps".
"""

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TOOL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
BUILTIN_TOOL_NAMES = {"search_knowledge_base", "list_documents", "read_document"}
MAX_TOOL_RESULT_CHARS = 6000


@dataclass
class HttpTool:
    name: str
    description: str
    parameters: dict[str, Any]
    url: str
    method: str = "POST"
    secret_env: str = ""
    timeout_seconds: float = 10.0

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class NamespaceProfile:
    assistant_name: str = "Inkwell"
    instructions: str = ""
    tools: list[HttpTool] = field(default_factory=list)


class AgentConfig:
    """Loads agent_config.json. Missing file = defaults for every namespace."""

    def __init__(self, path: Path):
        self.path = path
        self.profiles: dict[str, NamespaceProfile] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self.profiles = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        profiles = {}
        for namespace, data in (raw.get("namespaces") or {}).items():
            tools = []
            for item in data.get("tools") or []:
                name = item["name"]
                if not TOOL_NAME_RE.match(name) or name in BUILTIN_TOOL_NAMES:
                    raise ValueError(f"Invalid or reserved tool name '{name}' in {self.path}")
                tools.append(HttpTool(
                    name=name,
                    description=item["description"],
                    parameters=item.get("parameters") or {"type": "object", "properties": {}},
                    url=item["url"],
                    method=item.get("method", "POST").upper(),
                    secret_env=item.get("secret_env", ""),
                    timeout_seconds=float(item.get("timeout_seconds", 10)),
                ))
            profiles[namespace] = NamespaceProfile(
                assistant_name=data.get("assistant_name", "Inkwell"),
                instructions=data.get("instructions", ""),
                tools=tools,
            )
        self.profiles = profiles

    def profile(self, namespace: str) -> NamespaceProfile:
        """Exact namespace first, then glob keys like "distos-*" (one per tenant)."""
        if namespace in self.profiles:
            return self.profiles[namespace]
        for pattern, profile in self.profiles.items():
            if any(char in pattern for char in "*?[") and fnmatch.fnmatchcase(namespace, pattern):
                return profile
        return NamespaceProfile()


def call_http_tool(
    tool: HttpTool,
    arguments: dict[str, Any],
    namespace: str,
    trusted_context: dict[str, Any] | None,
) -> str:
    """Call an app's tool endpoint.

    `trusted_context` comes from the calling backend (e.g. the logged-in user's id
    and role), never from the model, so the app can enforce its own permissions.
    """
    import httpx

    headers = {"Content-Type": "application/json", "User-Agent": "inkwell-agent/2"}
    if tool.secret_env:
        secret = os.environ.get(tool.secret_env, "")
        if not secret:
            return f"Tool '{tool.name}' is not configured (missing {tool.secret_env})."
        headers["X-Tool-Secret"] = secret
    payload = {"arguments": arguments, "context": trusted_context or {}, "namespace": namespace}
    try:
        if tool.method == "GET":
            params = {**{k: v for k, v in arguments.items() if isinstance(v, (str, int, float, bool))}}
            response = httpx.get(tool.url, params=params, headers=headers, timeout=tool.timeout_seconds)
        else:
            response = httpx.request(tool.method, tool.url, json=payload, headers=headers, timeout=tool.timeout_seconds)
    except httpx.HTTPError as exc:
        logger.warning("Tool %s failed: %s", tool.name, exc)
        return f"Tool '{tool.name}' could not be reached. Tell the user the live data is unavailable right now."
    if response.status_code >= 400:
        logger.warning("Tool %s returned %s", tool.name, response.status_code)
        return f"Tool '{tool.name}' returned an error (HTTP {response.status_code}): {response.text[:500]}"
    text = response.text
    return text if len(text) <= MAX_TOOL_RESULT_CHARS else text[:MAX_TOOL_RESULT_CHARS] + "\n...[truncated]"


BUILTIN_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Search the uploaded documents and knowledge base. Use for any question about "
                "policies, products, procedures, prices, or facts. Write a specific query; call "
                "again with different wording if results are weak."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Standalone search query"},
                    "document_id": {
                        "type": "string",
                        "description": "Optional: limit the search to one document (from list_documents)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List the documents in the knowledge base with their ids and sizes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": (
                "Read consecutive sections of one document in order. Use for summaries or when "
                "you need the full context around a passage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "start_section": {"type": "integer", "description": "0-based section index", "default": 0},
                    "count": {"type": "integer", "description": "Sections to read (max 8)", "default": 6},
                },
                "required": ["document_id"],
            },
        },
    },
]
