"""Unit and contract tests for SurfaceComposer specification, validation, and security invariants."""

from charlie.surface_spec import (
    MAX_DEPTH,
    MAX_PRIMITIVES,
    MAX_TABLE_ROWS,
    MAX_TEXT_LEN,
    PrimitiveType,
    validate_surface_spec,
)


def test_valid_surface_spec():
    raw = {
        "schema_version": 1,
        "surface_id": "gpu-compare-1",
        "title": "GPU Hardware Comparison",
        "target": "workspace",
        "revision": 1,
        "surface_type": "comparison",
        "summary": "Comparing RTX 4090 vs RX 7900 XTX",
        "layout": {"type": "stack", "gap": 12},
        "primitives": [
            {"type": "heading", "data": {"text": "GPU Benchmark Breakdown", "level": 2}},
            {"type": "metric", "data": {"label": "RTX 4090 VRAM", "value": "24", "unit": "GB", "status": "success"}},
            {
                "type": "table",
                "data": {
                    "columns": [
                        {"key": "model", "label": "Model"},
                        {"key": "vram", "label": "VRAM", "monospace": True},
                        {"key": "perf", "label": "4K FPS", "align": "right"},
                    ],
                    "rows": [
                        {"model": "RTX 4090", "vram": "24GB", "perf": "120"},
                        {"model": "RX 7900 XTX", "vram": "24GB", "perf": "105"},
                    ],
                },
            },
            {
                "type": "chart",
                "data": {
                    "chartType": "bar",
                    "title": "Relative 4K Gaming Performance",
                    "unit": "%",
                    "data": [
                        {"label": "RTX 4090", "value": 100},
                        {"label": "RX 7900 XTX", "value": 88},
                    ],
                },
            },
        ],
        "actions": [
            {"id": "act-sort", "label": "Sort by Performance", "action_id": "sort_perf", "variant": "default"},
        ],
    }

    valid, errors, spec = validate_surface_spec(raw)
    assert valid is True
    assert errors == []
    assert spec is not None
    assert spec.surface_id == "gpu-compare-1"
    assert spec.schema_version == 1
    assert len(spec.primitives) == 4
    assert len(spec.actions) == 1


def test_reject_unsupported_schema_version():
    raw = {
        "schema_version": 99,
        "surface_id": "invalid-ver",
        "title": "Invalid Version",
    }
    valid, errors, spec = validate_surface_spec(raw)
    assert valid is False
    assert any("schema_version" in e for e in errors)


def test_reject_script_and_html_injection():
    raw = {
        "schema_version": 1,
        "surface_id": "xss-test",
        "title": "<script>alert('pwned')</script>",
        "primitives": [
            {"type": "text", "data": {"text": "Click here: javascript:alert(document.cookie)"}},
            {"type": "image", "data": {"src": "javascript:alert(1)"}},
        ],
    }
    valid, errors, spec = validate_surface_spec(raw)
    assert valid is False
    assert len(errors) >= 2
    assert any("Dangerous" in e or "Unsafe" in e for e in errors)


def test_reject_excessive_nesting_depth():
    # Construct node with depth > MAX_DEPTH (5)
    deep_node: dict = {"type": "text", "data": {"text": "Deep Text"}}
    for _ in range(MAX_DEPTH + 2):
        deep_node = {"type": "layout", "data": {}, "children": [deep_node]}

    raw = {
        "schema_version": 1,
        "surface_id": "deep-nesting",
        "title": "Excessive Depth",
        "primitives": [deep_node],
    }
    valid, errors, spec = validate_surface_spec(raw)
    assert valid is False
    assert any("Nesting depth" in e for e in errors)


def test_reject_excessive_primitives_count():
    prims = [{"type": "text", "data": {"text": f"item {i}"}} for i in range(MAX_PRIMITIVES + 10)]
    raw = {
        "schema_version": 1,
        "surface_id": "too-many-prims",
        "title": "Too Many Primitives",
        "primitives": prims,
    }
    valid, errors, spec = validate_surface_spec(raw)
    assert valid is False
    assert any("primitive count exceeds" in e for e in errors)


def test_reject_excessive_table_rows():
    rows = [{"id": i, "val": i} for i in range(MAX_TABLE_ROWS + 10)]
    raw = {
        "schema_version": 1,
        "surface_id": "big-table",
        "title": "Large Table",
        "primitives": [
            {"type": "table", "data": {"columns": [{"key": "id", "label": "ID"}], "rows": rows}},
        ],
    }
    valid, errors, spec = validate_surface_spec(raw)
    assert valid is False
    assert any("Table rows" in e for e in errors)


def test_reject_excessive_text_length():
    huge_text = "A" * (MAX_TEXT_LEN + 500)
    raw = {
        "schema_version": 1,
        "surface_id": "huge-text",
        "title": "Huge Text",
        "primitives": [
            {"type": "text", "data": {"text": huge_text}},
        ],
    }
    valid, errors, spec = validate_surface_spec(raw)
    assert valid is False
    assert any("Text length" in e for e in errors)


def test_all_approved_primitive_types_supported():
    for ptype in PrimitiveType:
        raw = {
            "schema_version": 1,
            "surface_id": f"test-{ptype.value}",
            "title": f"Test {ptype.value}",
            "primitives": [
                {"type": ptype.value, "data": {"text": "hello", "label": "test"}},
            ],
        }
        valid, errors, spec = validate_surface_spec(raw)
        assert valid is True, f"Failed on primitive type {ptype.value}: {errors}"
