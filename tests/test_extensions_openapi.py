"""Tests for the OpenAPI adapter (charlie/extensions/openapi_import.py)."""

import httpx
import pytest

from charlie.extensions.openapi_import import (
    parse_openapi_spec,
    register_openapi_operations,
)

_SAMPLE_SPEC = """
{
  "openapi": "3.0.0",
  "info": {"title": "Widget API"},
  "servers": [{"url": "https://api.example.com"}],
  "paths": {
    "/widgets/{id}": {
      "get": {
        "operationId": "getWidget",
        "summary": "Get a widget by id",
        "parameters": [
          {"name": "id", "in": "path", "required": true, "schema": {"type": "string"}}
        ]
      }
    },
    "/widgets": {
      "post": {
        "operationId": "createWidget",
        "summary": "Create a widget",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
            }
          }
        }
      }
    }
  }
}
"""


class TestParseOpenAPISpec:
    def test_parses_title_and_base_url(self):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        assert spec.title == "Widget API"
        assert spec.base_url == "https://api.example.com"

    def test_parses_operations(self):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        ids = {op.operation_id for op in spec.operations}
        assert ids == {"getWidget", "createWidget"}

    def test_get_operation_has_path_parameter(self):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        op = next(o for o in spec.operations if o.operation_id == "getWidget")
        assert op.method == "GET"
        assert op.path == "/widgets/{id}"
        assert any(p["name"] == "id" and p["in"] == "path" for p in op.parameters)

    def test_post_operation_has_request_body_schema(self):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        op = next(o for o in spec.operations if o.operation_id == "createWidget")
        assert op.method == "POST"
        assert op.request_body_schema["type"] == "object"

    def test_explicit_base_url_overrides_servers(self):
        spec = parse_openapi_spec(_SAMPLE_SPEC, base_url="https://override.example.com")
        assert spec.base_url == "https://override.example.com"

    def test_missing_paths_raises(self):
        with pytest.raises(ValueError, match="paths"):
            parse_openapi_spec('{"openapi": "3.0.0", "info": {"title": "x"}}')

    def test_yaml_input_also_parses(self):
        yaml_spec = """
openapi: "3.0.0"
info:
  title: YAML API
paths:
  /ping:
    get:
      operationId: ping
      summary: Ping
"""
        spec = parse_openapi_spec(yaml_spec)
        assert spec.title == "YAML API"
        assert spec.operations[0].operation_id == "ping"


class _FakeRegistry:
    def __init__(self):
        self.registered = {}

    def register_tool(self, name, description, schema, **_):
        def decorator(func):
            self.registered[name] = {"description": description, "schema": schema, "func": func}
            return func

        return decorator


class TestRegisterOpenAPIOperations:
    def test_registers_one_tool_per_operation(self):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        registry = _FakeRegistry()

        registered = register_openapi_operations(registry, spec)

        assert set(registered) == {"api_getWidget", "api_createWidget"}

    def test_path_parameter_substituted_into_url(self, monkeypatch):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        registry = _FakeRegistry()
        register_openapi_operations(registry, spec)
        captured = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return httpx.Response(200, text="ok", request=httpx.Request(method, url))

        monkeypatch.setattr(httpx, "request", fake_request)

        result = registry.registered["api_getWidget"]["func"](id="42")

        assert result == "ok"
        assert captured["url"] == "https://api.example.com/widgets/42"
        assert captured["method"] == "GET"

    def test_body_parameter_sent_as_json(self, monkeypatch):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        registry = _FakeRegistry()
        register_openapi_operations(registry, spec)
        captured = {}

        def fake_request(method, url, **kwargs):
            captured.update(kwargs)
            return httpx.Response(200, text="created", request=httpx.Request(method, url))

        monkeypatch.setattr(httpx, "request", fake_request)

        result = registry.registered["api_createWidget"]["func"](body={"name": "widget-1"})

        assert result == "created"
        assert captured["json"] == {"name": "widget-1"}

    def test_http_error_returns_error_string_not_raise(self, monkeypatch):
        spec = parse_openapi_spec(_SAMPLE_SPEC)
        registry = _FakeRegistry()
        register_openapi_operations(registry, spec)

        def fake_request(method, url, **kwargs):
            raise httpx.ConnectError("boom", request=httpx.Request(method, url))

        monkeypatch.setattr(httpx, "request", fake_request)

        result = registry.registered["api_getWidget"]["func"](id="1")

        assert "OpenAPI call error" in result
