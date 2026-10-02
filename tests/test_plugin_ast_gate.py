"""Lockdown tests for the plugin AST gate behind ``plugin_code_exec_python``.

Audit item P3-29 ("Plugin AST gate bypassable") was UNRESOLVED: it was not
established whether ``CodeExecPlugin._exec_python``'s static gate could be
defeated so that code the gate advertises as blocked actually runs.

Resolution: the gate WAS bypassable. It rejected a banned builtin only in
*call* position, never in *load* position, and it never inspected ``ast.Name``
identifiers at all. ``__builtins__`` is a plain Name in the ``python -c`` child's
globals, so it survived every check; every builtin reachable off it as
``b.<attr>`` survived too, because the attribute rule only rejected *dunder*
attribute names and never the banned-builtin set.

Every exploit below writes a sentinel file into a pytest ``tmp_path``. The
assertions are semantic postconditions -- "the sentinel does not exist" and "the
gate refused" -- rather than "a string matched", so a gate that silently lets
code through and merely relabels the result cannot pass.

The same files also pin the mitigating control Plan.md relies on: the tool is
``security_sensitive``, so ``autonomy.evaluate`` demands approval. A gate
bypass here is therefore not an approval bypass.

Evidence class: TEST/MOCK (in-process subprocess execution against real Python;
no user files touched).
"""

from __future__ import annotations

import os

import pytest

from charlie.plugins import CodeExecPlugin

SENTINEL = "P3_29_SENTINEL_WRITTEN"


# ---------------------------------------------------------------------------
# Adversarial plugin source samples
# ---------------------------------------------------------------------------
# Each takes the sentinel path and writes SENTINEL to it if it executes. The
# gate must refuse every one of them *before* the subprocess is launched, so the
# sentinel must never appear on disk.


def _exploit_sources(sentinel: str) -> list[tuple[str, str]]:
    """Adversarial snippets, each of which writes *sentinel* if it executes."""
    return [
        # 1. Root cause: `__builtins__` is a Name, and the gate never looked at
        #    Names. Every builtin is then reachable as `b.<attr>`, and the
        #    attribute rule only rejected *dunder* attribute names -- so the
        #    banned-builtin list never applied to the strongest handle on it.
        (
            "builtins_module_attribute_open",
            f"b = __builtins__\nb.open({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 2. Same handle, capability reached through exec/compile so the snippet
        #    can go on to import and spawn a process.
        (
            "builtins_module_exec_compile",
            "b = __builtins__\n"
            "b.exec(b.compile(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\", 'x', 'exec'))",
        ),
        # 3. get() the dynamic-import builtin by name so even the dunder check on
        #    `.__import__` is sidestepped, then spend the imported os on a write.
        (
            "builtins_module_getattr_dynamic_import",
            "b = __builtins__\n"
            f"os = b.getattr(b, '_' + '__import__')('os')\n"
            f"os.remove({sentinel!r}) if os.path.exists({sentinel!r}) else None\n"
            f"open_via_b = b.open\n"
            f"open_via_b({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 4. Banned builtin loaded into a variable. The old rule only rejected
        #    `exec(...)` in call position, so `f = exec; f(...)` sailed past.
        (
            "aliased_exec_builtin",
            "f = exec\n"
            "f(\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")",
        ),
        # 5. Same shape with eval, which needs no statement body.
        (
            "aliased_eval_builtin",
            "f = eval\n"
            "f(\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")",
        ),
        # 6. Alias the dunder import builtin by bare Name load, then shell out.
        (
            "aliased_dunder_import_builtin",
            f"f = __import__\nf('os').system('echo {SENTINEL}')",
        ),
        # 7. Alias open itself -- the shortest bare-Name alias of all.
        (
            "aliased_open_builtin",
            f"f = open\nf({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 8. Alias hidden behind a nested function definition, so the eventual
        #    call position is an unremarkable local name.
        (
            "nested_function_alias_exec",
            "def outer():\n"
            "    def inner():\n"
            "        g = exec\n"
            "        g(\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")\n"
            "    return inner()\n"
            "outer()",
        ),
        # 9. exec inside a class body: still a Call to a Name, so this was
        #    already covered -- kept as a regression pin.
        (
            "exec_in_class_body",
            "class Payload:\n"
            "    exec(\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")",
        ),
        # 10. dict-of-callables indirection, invoked by an unremarkable name.
        (
            "dict_of_callables_indirection",
            "table = {'go': eval}\n"
            "table['go'](\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")",
        ),
    ]


EXPLOIT_IDS = [name for name, _ in _exploit_sources("unused-sentinel")]


def _run_gate(source: str) -> dict:
    """Run *source* through the plugin AST gate and return the raw result."""
    return CodeExecPlugin().call_tool("code_exec_python", {"code": source})


def _assert_refused(result: dict, name: str) -> None:
    """The gate refused: no subprocess result, and an explicit rejection."""
    assert "error" in result, (
        f"{name}: AST gate did NOT reject the snippet. Observed result: {result!r}. "
        "The snippet reached the subprocess -- returncode "
        f"{result.get('returncode')!r}, output {result.get('output', '')[:200]!r}."
    )
    assert "Rejected" in result["error"], (
        f"{name}: gate failed closed but without the documented rejection: {result!r}"
    )
    assert result.get("returncode") is None, (
        f"{name}: a subprocess actually ran, so the gate is bypassable: {result!r}"
    )


# ---------------------------------------------------------------------------
# Case B -- exploit proof: these are the vectors that were live
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exploit_id", EXPLOIT_IDS)
def test_builtin_indirection_is_rejected_and_never_runs(exploit_id, tmp_path):
    """No indirection route may both pass the gate and touch the filesystem."""
    sentinel = str(tmp_path / "p3_29_sentinel.txt")
    sources = dict(_exploit_sources(sentinel))

    result = _run_gate(sources[exploit_id])

    _assert_refused(result, exploit_id)
    assert not os.path.exists(sentinel), (
        f"{exploit_id} executed and wrote {sentinel}; "
        f"file contents: {open(sentinel, encoding='utf-8').read()!r}"
    )


def test_all_exploit_vectors_are_enumerated():
    """Guard the corpus itself: a shrinking sample set must not look green."""
    assert len(EXPLOIT_IDS) >= 10
    assert len(set(EXPLOIT_IDS)) == len(EXPLOIT_IDS)


# ---------------------------------------------------------------------------
# The same gate, reached through the published registry tool name
# ---------------------------------------------------------------------------


class _EnabledPluginsConfig:
    plugins_enabled = True
    plugin_allow_dirs: list = []


def test_registry_tool_plugin_code_exec_python_refuses_indirection(tmp_path):
    """plugin_code_exec_python is the model-facing name; gate it there too."""
    from charlie.tools import ToolRegistry, register_plugin_tools_into

    sentinel = str(tmp_path / "p3_29_registry_sentinel.txt")
    registry = ToolRegistry()
    manager = register_plugin_tools_into(registry, _EnabledPluginsConfig())
    assert manager is not None
    assert "plugin_code_exec_python" in registry.get_tool_names()

    result = registry.execute_tool(
        "plugin_code_exec_python",
        {"code": f"b = __builtins__\nb.open({sentinel!r}, 'w').write({SENTINEL!r})"},
    )

    assert "Rejected" in result, (
        f"plugin_code_exec_python ran the snippet instead of refusing: {result!r}"
    )
    assert not os.path.exists(sentinel), (
        f"plugin_code_exec_python wrote {sentinel}: {open(sentinel, encoding='utf-8').read()!r}"
    )


def test_registry_tool_plugin_code_exec_python_still_runs_benign_code():
    """Hardening must not turn the sandbox into a blanket refusal."""
    from charlie.tools import ToolRegistry, register_plugin_tools_into

    registry = ToolRegistry()
    register_plugin_tools_into(registry, _EnabledPluginsConfig())

    result = registry.execute_tool(
        "plugin_code_exec_python", {"code": "print(sum(range(10)))"}
    )

    assert "45" in result, f"benign pure computation no longer runs: {result!r}"


# ---------------------------------------------------------------------------
# Benign pure computation must survive the hardening
# ---------------------------------------------------------------------------


BENIGN_SNIPPETS = [
    ("print_builtin", "print(42)"),
    ("arithmetic_and_builtin_helpers", "print(sum(range(10)), max([3, 1, 2]))"),
    ("comprehension", "print([x * x for x in range(5)])"),
    ("string_methods", "print('abc'.upper().replace('a', 'b'))"),
    ("dict_and_sorting", "print(sorted({'b': 1, 'a': 2}.items(), key=lambda kv: kv[0]))"),
    ("class_definition_without_exec", "class P:\n    v = 3\nprint(P.v)"),
    ("function_definition", "def f(a, b=2):\n    return a + b\nprint(f(1))"),
    # No dunder attribute: `exc.__name__` is refused by the pre-existing
    # dunder-attribute rule and is deliberately kept refused.
    ("try_except", "try:\n    1 / 0\nexcept ZeroDivisionError as exc:\n    print('caught', str(exc))"),
    ("fstring_composition", "name = 'world'\nprint(f'hello {name.upper()}')"),
    ("generator_and_enum", "print(list(v for v in range(3) if v))"),
    ("set_operations", "print({1, 2} | {2, 3})"),
    ("boolean_and_none_builtins", "flag = None\nprint(flag is None, bool(flag or 1), isinstance(1, int))"),
]


@pytest.mark.parametrize(
    "source",
    [pytest.param(src, id=name) for name, src in BENIGN_SNIPPETS],
)
def test_benign_pure_computation_still_executes(source):
    result = _run_gate(source)

    assert "error" not in result, (
        f"gate rejected benign code: {result!r}. source={source!r}"
    )
    assert result.get("returncode") == 0, (
        f"benign code failed at runtime: {result!r}. source={source!r}"
    )


# ---------------------------------------------------------------------------
# The mitigating control Plan.md relies on: the tool stays approval-gated
# ---------------------------------------------------------------------------


def test_plugin_code_exec_python_is_security_sensitive():
    from charlie.autonomy import RiskClass, classify_action

    risk, _reason = classify_action("plugin_code_exec_python", {"code": "print(1)"})

    assert risk == RiskClass.SECURITY_SENSITIVE, (
        f"risk class drifted to {risk!r}; the P3-29 mitigation depends on it "
        "staying security_sensitive"
    )


def test_plugin_code_exec_python_requires_approval():
    from charlie.autonomy import Requirement, evaluate

    requirement, risk, _reason = evaluate(
        "plugin_code_exec_python", {"code": "print(1)"}
    )

    assert requirement == Requirement.APPROVE, (
        f"plugin_code_exec_python no longer requires approval ({requirement!r}, "
        f"risk={risk!r}); an AST-gate bypass would then be an approval bypass"
    )


def test_plugin_code_exec_python_registry_metadata_is_security_sensitive():
    from charlie.tools import ToolRegistry, register_plugin_tools_into

    registry = ToolRegistry()
    register_plugin_tools_into(registry, _EnabledPluginsConfig())

    assert registry.get_risk_class("plugin_code_exec_python") == "security_sensitive"


def test_no_fast_path_can_reach_plugin_code_exec_python_without_approval():
    """Fast paths call registry.execute_tool directly, bypassing autonomy.evaluate."""
    import charlie.fastpaths as fastpaths

    queries = (
        "check disk",
        "show telemetry",
        "volume up",
        "open windows settings",
        "focus notepad",
        "list files",
        "open example.com",
    )
    matchers = (
        fastpaths.match_system_workspace,
        fastpaths.match_system_telemetry,
        fastpaths.match_media_volume,
        fastpaths.match_windows_settings,
        fastpaths.match_focus_app,
        fastpaths.match_filesystem_basic,
        fastpaths.match_direct_url,
    )
    reachable = {
        match.tool_name
        for matcher in matchers
        for match in (matcher(query) for query in queries)
        if match is not None
    }

    assert "plugin_code_exec_python" not in reachable, (
        "a fast-path matcher can dispatch plugin_code_exec_python without "
        "passing through autonomy.evaluate, which would bypass approval"
    )
