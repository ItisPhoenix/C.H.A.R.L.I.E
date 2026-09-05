"""Tests for /api/memory/facts returning real subject/predicate/object triples.

Regression test for the bug where the endpoint mapped every knowledge-graph
node to a fake {node_type} is {content} triple instead of reading the edges
table where add_fact stores the real relationships.
"""

import pytest

import charlie.web_server as web_server


@pytest.mark.asyncio
async def test_get_memory_facts_returns_real_triples(monkeypatch):
    async def request(operation, payload):
        assert operation == "get_facts"
        return {
            "request_id": "r",
            "operation": operation,
            "success": True,
            "data": {
                "facts": [
                    {"subject": "Alice", "predicate": "works_on", "object": "graphs"},
                    {"subject": "Bob", "predicate": "knows", "object": "Alice"},
                ]
            },
        }

    monkeypatch.setattr(web_server, "_request_authoritative_memory_operation", request)
    result = await web_server.get_memory_facts()
    facts = result["facts"]
    assert {"subject": "Alice", "predicate": "works_on", "object": "graphs"} in facts
    assert {"subject": "Bob", "predicate": "knows", "object": "Alice"} in facts
    assert all(f["predicate"] != "is" for f in facts)


@pytest.mark.asyncio
async def test_get_memory_facts_no_graph_returns_empty(monkeypatch):
    async def request(operation, payload):
        return {"request_id": "r", "operation": operation, "success": True, "data": {"facts": []}}

    monkeypatch.setattr(web_server, "_request_authoritative_memory_operation", request)
    assert await web_server.get_memory_facts() == {"facts": []}


@pytest.mark.asyncio
async def test_get_memory_facts_empty_graph_returns_empty(monkeypatch):
    async def request(operation, payload):
        return {"request_id": "r", "operation": operation, "success": True, "data": {"facts": []}}

    monkeypatch.setattr(web_server, "_request_authoritative_memory_operation", request)
    assert await web_server.get_memory_facts() == {"facts": []}
