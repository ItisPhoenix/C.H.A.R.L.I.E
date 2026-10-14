"""OpenAPI / GPT Actions adapter.

Parses an OpenAPI 3.x spec (the same format GPT Actions and the legacy
OpenAI tool manifest use) and registers one charlie.tools.registry tool
per operation that makes a guarded public HTTP call.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import yaml

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_]")
_HTTP_METHODS = ("get", "head", "options", "post", "put", "patch", "delete")
_DESTINATION_HEADERS = frozenset({
    "host", ":authority", ":scheme", "forwarded", "x-forwarded-host",
    "x-forwarded-proto", "proxy-authorization", "proxy-connection",
})
_DESTINATION_PARAMETERS = frozenset({"host", "authority", "url", "base_url", "server", "server_url"})


@dataclass
class OpenAPIOperation:
    operation_id: str
    method: str
    path: str
    description: str
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    request_body_schema: Optional[Dict[str, Any]] = None


@dataclass
class OpenAPISpec:
    title: str
    base_url: str
    operations: List[OpenAPIOperation]


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("_", text.strip("/")) or "root"


def _infer_base_url(doc: Dict[str, Any]) -> str:
    servers = doc.get("servers") or []
    if servers and isinstance(servers[0], dict):
        return str(servers[0].get("url", ""))
    return ""


def parse_openapi_spec(text: str, base_url: str = "") -> OpenAPISpec:
    """Parse an OpenAPI 3.x spec (JSON or YAML -- YAML is a JSON superset,
    so one parser covers both) into a flat operation list."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or "paths" not in doc:
        raise ValueError("Not a valid OpenAPI spec: missing 'paths'")

    operations: List[OpenAPIOperation] = []
    for path, path_item in (doc.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        shared_params = [p for p in path_item.get("parameters", []) if isinstance(p, dict)]
        for method, op in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            operation_id = str(op.get("operationId") or f"{method}_{_slugify(path)}")
            request_body_schema = None
            body = op.get("requestBody")
            if isinstance(body, dict):
                content = body.get("content", {})
                json_body = content.get("application/json", {})
                if isinstance(json_body, dict):
                    request_body_schema = json_body.get("schema")
            own_params = [p for p in op.get("parameters", []) if isinstance(p, dict)]
            operations.append(
                OpenAPIOperation(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    description=str(op.get("summary") or op.get("description") or ""),
                    parameters=shared_params + own_params,
                    request_body_schema=request_body_schema,
                )
            )

    return OpenAPISpec(
        title=str((doc.get("info") or {}).get("title", "untitled")),
        base_url=base_url or _infer_base_url(doc),
        operations=operations,
    )


def _operation_schema(op: OpenAPIOperation) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for p in op.parameters:
        name = p.get("name")
        if not name:
            continue
        properties[name] = {
            "type": (p.get("schema") or {}).get("type", "string"),
            "description": p.get("description", f"{p.get('in', 'query')} parameter"),
        }
        if p.get("required"):
            required.append(name)
    if op.request_body_schema:
        properties["body"] = (
            op.request_body_schema if isinstance(op.request_body_schema, dict) else {"type": "object"}
        )
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _call_operation(
    base_url: str,
    op: OpenAPIOperation,
    param_locations: Dict[str, str],
    headers: Dict[str, str],
    timeout: float,
    kwargs: Dict[str, Any],
) -> str:
    path = op.path
    query: Dict[str, Any] = {}
    call_headers = dict(headers)
    body = kwargs.pop("body", None)
    for key, value in kwargs.items():
        location = param_locations.get(key, "query")
        if location == "path":
            if key.casefold() in _DESTINATION_PARAMETERS:
                return "OpenAPI call refused: destination override parameters are not allowed."
            path = path.replace("{" + key + "}", str(value))
        elif location == "header":
            if key.casefold() in _DESTINATION_HEADERS or key.casefold() in _DESTINATION_PARAMETERS:
                return "OpenAPI call refused: destination override headers are not allowed."
            call_headers[key] = str(value)
        else:
            query[key] = value
    if any(key.casefold() in _DESTINATION_HEADERS for key in call_headers):
        return "OpenAPI call refused: destination override headers are not allowed."
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    try:
        from charlie.research.fetch import _get_browser_shaped, validate_public_url

        validated_base = validate_public_url(base_url)
        validated_url = validate_public_url(url)
        base_parts = urlparse(validated_base)
        target_parts = urlparse(validated_url)
        if (target_parts.scheme, target_parts.hostname, target_parts.port) != (
            base_parts.scheme, base_parts.hostname, base_parts.port
        ):
            return "OpenAPI call refused: operation path changed the declared destination."
        status, text, _ = asyncio.run(_get_browser_shaped(
            validated_url,
            timeout_s=timeout,
            method=op.method,
            headers=call_headers,
            params=query or None,
            json_body=body,
            follow_redirects=False,
        ))
        if 300 <= status < 400:
            return "OpenAPI call refused: redirects are disabled for imported operations."
        if status >= 400:
            return f"OpenAPI call error: HTTP {status}"
        return text[:10000]
    except Exception as exc:
        return f"OpenAPI call error: {type(exc).__name__}"


def register_openapi_operations(
    registry: Any,
    spec: OpenAPISpec,
    prefix: str = "api_",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 15.0,
) -> List[str]:
    """Register one registry tool per operation. Each call makes the real
    HTTP request through the pinned public-web transport against spec.base_url.
    Returns registered names."""
    registered: List[str] = []
    for header in (headers or {}):
        if header.casefold() in _DESTINATION_HEADERS or header.casefold() in _DESTINATION_PARAMETERS:
            raise ValueError("OpenAPI destination override headers are not allowed")
    for op in spec.operations:
        tool_name = f"{prefix}{_slugify(op.operation_id)}"
        param_locations = {p["name"]: p.get("in", "query") for p in op.parameters if p.get("name")}

        def _invoke(op=op, param_locations=param_locations, **kwargs: Any) -> str:
            return _call_operation(spec.base_url, op, param_locations, headers or {}, timeout, kwargs)

        registry.register_tool(
            name=tool_name,
            description=f"[{spec.title}] {op.description or f'{op.method} {op.path}'}",
            schema=_operation_schema(op),
            owner="extensions",
            risk_class="security_sensitive",
            required_leases=("network",),
        )(_invoke)
        registered.append(tool_name)
    return registered
