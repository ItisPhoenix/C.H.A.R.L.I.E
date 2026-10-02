"""Precision tests for the plugin AST gate and the code-exec tool description.

The P3-29 hardening (``tests/test_plugin_ast_gate.py``) made the gate refuse a
bare-name reference to *every* banned builtin regardless of what the name is
doing there. That closed the indirection routes, but it also rejected code that
never touches a builtin:

    type = 3          -> "Rejected: dangerous builtin 'type' is blocked."
    vars = {}         -> "Rejected: dangerous builtin 'vars' is blocked."
    open = 1          -> "Rejected: dangerous builtin 'open' is blocked."
    compile = f       -> "Rejected: dangerous builtin 'compile' is blocked."
    print(__name__)   -> "Rejected: private attribute access not allowed."

Assigning to a name is not *using* the builtin of that name, and the message was
actively misleading. ``plugins.py`` already reasons about exactly this when it
deliberately keeps ``os``/``sys``/``subprocess``/``io`` out of
``_DISALLOWED_NAMES``: "banning them as identifiers would only reject harmless
local names". The same argument applies to the names that were kept. The same
argument applies to bare ``__name__``/``__doc__``, which are not namespace
handles and reveal nothing actionable.

This is a false-positive / correctness defect, NOT a security hole. The gate is
strictly *more* restrictive before the fix, and it stays bypass-resistant for
the routes that matter: ``__builtins__``/``__loader__``/``__spec__`` remain in
``_DISALLOWED_NAMES`` (load position), the ``ast.Attribute`` rule is untouched,
and every aliasing/indirection spelling is still refused.

So this file pins BOTH directions:

* benign snippets the gate must accept (a false positive here is a defect);
* the full original exploit corpus, which must still be refused.

Every acceptance assertion is a semantic postcondition -- the snippet reached
the subprocess *and* returned 0 -- rather than "no error key", so a gate that
rejects nothing but also runs nothing cannot pass. Every refusal assertion checks
that no subprocess ran at all.

Evidence class: TEST/MOCK (in-process gate + subprocess against real Python,
writing only into pytest ``tmp_path``).
"""

from __future__ import annotations

import os

import pytest

from charlie.plugins import CodeExecPlugin

SENTINEL = "GATE_PRECISION_SENTINEL"


def _run_gate(source: str) -> dict:
    """Run *source* through the plugin AST gate and return the raw result."""
    return CodeExecPlugin().call_tool("code_exec_python", {"code": source})


# ---------------------------------------------------------------------------
# Direction 1 -- benign snippets the gate must NOT reject
# ---------------------------------------------------------------------------
# None of these read a banned builtin. They only *bind* names that collide with
# one, or read the two dunders that are not namespace handles.


BENIGN_STORE_SNIPPETS = [
    # The four collisions named in the defect report.
    pytest.param("type = 3", id="assign_type"),
    pytest.param("vars = {}", id="assign_vars"),
    pytest.param("open = 1", id="assign_open"),
    pytest.param("compile = lambda src: src", id="assign_compile"),
    # Store position reached through the other binding forms.
    pytest.param("open = other = 2\nprint(other)", id="chained_assignment"),
    pytest.param("def f():\n    type = 1\n    kind = 'x'\n    return kind\nprint(f())",
                 id="store_inside_nested_function"),
    pytest.param("x = 7\nprint([x for open in range(3)])",
                 id="comprehension_target"),
    pytest.param("count = 0\nfor open in range(2):\n    count += 1\nprint(count)",
                 id="for_loop_target"),
    pytest.param("exec = 1\nprint('bound')", id="assign_exec_never_read"),
    pytest.param("(compile := 3)\nprint('bound')", id="walrus_target"),
]

BENIGN_DUNDER_SNIPPETS = [
    pytest.param("print(__name__)", "__main__", id="read_name_dunder"),
    pytest.param("print(__doc__)", "None", id="read_doc_dunder"),
    pytest.param("print(__name__.upper())", "__MAIN__", id="read_name_dunder_method"),
]


@pytest.mark.parametrize("source", BENIGN_STORE_SNIPPETS)
def test_store_position_collision_is_not_a_rejection(source):
    """Binding a name that collides with a builtin is not a use of it."""
    result = _run_gate(source)

    assert "error" not in result, (
        f"gate rejected benign code that never reads a builtin: {result!r}. "
        f"source={source!r}"
    )
    assert result.get("returncode") == 0, (
        f"benign code failed at runtime: {result!r}. source={source!r}"
    )


@pytest.mark.parametrize("source, expected", BENIGN_DUNDER_SNIPPETS)
def test_benign_dunder_names_are_readable(source, expected):
    """``__name__``/``__doc__`` are metadata, not a namespace handle."""
    result = _run_gate(source)

    assert "error" not in result, (
        f"gate refused a benign dunder read: {result!r}. source={source!r}"
    )
    assert result.get("returncode") == 0, (
        f"benign dunder read failed at runtime: {result!r}. source={source!r}"
    )
    assert expected in result.get("output", ""), (
        f"unexpected output for {source!r}: {result!r}"
    )


def test_rejection_message_does_not_misdescribe_a_store_position():
    """A store collision must not be reported as a blocked builtin call."""
    result = _run_gate("type = 3")

    assert "error" not in result, f"still rejected: {result!r}"
    assert "dangerous builtin" not in str(result.get("error", ""))


# Documented boundary of this fix: the gate has no symbol table, so it cannot
# tell "the builtin `type`" from "the local `type`" once the name is *read*.
# A Load is therefore still treated as a use of the builtin, even for a name
# the snippet itself just rebound. That is strictly more restrictive, so it
# cannot open a route -- but it means `type = 3; print(type)` is still refused.
# Recorded here so the behaviour is pinned rather than discovered later.

@pytest.mark.parametrize(
    "source",
    [
        pytest.param("type = 3\nprint(type)", id="rebind_then_read_type"),
        pytest.param("open = 1\nprint(open)", id="rebind_then_read_open"),
        pytest.param("vars = {}\nprint(vars)", id="rebind_then_read_vars"),
    ],
)
def test_rebinding_then_reading_a_banned_name_is_still_refused(source):
    result = _run_gate(source)

    assert "error" in result, (
        f"reading a banned builtin name is no longer refused: {result!r}. "
        f"source={source!r}"
    )
    assert "Rejected" in result["error"], f"{result!r}"
    assert result.get("returncode") is None, f"a subprocess ran: {result!r}"


# ---------------------------------------------------------------------------
# Direction 2 -- the original exploit corpus must stay refused
# ---------------------------------------------------------------------------
# Same semantic shape as tests/test_plugin_ast_gate.py: each snippet writes
# SENTINEL into tmp_path if it executes, so a gate that merely relabels the
# result while letting the code run cannot pass.


def _exploit_sources(sentinel: str) -> list[tuple[str, str]]:
    return [
        # 1. The root cause: `__builtins__` is a bare Name in the `python -c`
        #    child's globals. In load position it is in _DISALLOWED_NAMES.
        (
            "builtins_handle_open",
            f"b = __builtins__\nb.open({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 2. Same handle, capability reached via exec+compile.
        (
            "builtins_handle_exec_compile",
            "b = __builtins__\n"
            "b.exec(b.compile(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\", 'x', 'exec'))",
        ),
        # 3. get() the dynamic-import builtin by name, sidestepping a
        #    `.__import__` spelling.
        (
            "builtins_handle_getattr_dynamic_import",
            "b = __builtins__\n"
            f"os = b.getattr(b, '_' + '__import__')('os')\n"
            f"os.remove({sentinel!r}) if os.path.exists({sentinel!r}) else None\n"
            f"open_via_b = b.open\n"
            f"open_via_b({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 4-8. Banned builtins aliased into a local. Load position, so still
        #      refused by the _DISALLOWED_NAMES rule.
        (
            "aliased_exec",
            "f = exec\n"
            "f(\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")",
        ),
        (
            "aliased_eval",
            "f = eval\n"
            "f(\"open(" + repr(sentinel) + ", 'w').write(" + repr(SENTINEL) + ")\")",
        ),
        (
            "aliased_dunder_import",
            f"f = __import__\nf('os').system('echo {SENTINEL}')",
        ),
        (
            "aliased_open",
            f"f = open\nf({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 9. Alias hidden behind a nested def, so the call site is an
        #    unremarkable local name.
        (
            "nested_function_alias_exec",
            "def outer():\n"
            "    def inner():\n"
            "        g = exec\n"
            "        g(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\")\n"
            "    return inner()\n"
            "outer()",
        ),
        # 10. dict-of-callables indirection.
        (
            "dict_of_callables",
            "table = {'go': eval}\n"
            "table['go'](\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\")",
        ),
        # 11. Class body: a Call to a Name, kept as a regression pin.
        (
            "exec_in_class_body",
            "class Payload:\n"
            "    exec(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\")",
        ),
        # 12. Plain call, no indirection at all.
        (
            "direct_open_call",
            f"open({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # --- Load-gating must not open a Store-then-use hole ---
        # 13. Store binds it, the *second* occurrence is the Load that matters.
        (
            "store_then_load_exec",
            "exec = 1\nexec(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\")",
        ),
        # 14. Same via a walrus target.
        (
            "walrus_then_load_eval",
            "(eval := 1)\neval(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\")",
        ),
        # 15. A parameter named `exec` is a Store; the body is a Load.
        (
            "param_named_exec",
            "def f(exec):\n"
            "    return exec(\"open(" + repr(sentinel) + ", 'w').write("
            + repr(SENTINEL) + ")\")\nf(eval)",
        ),
        # 16. Attribute reach of a banned builtin off the namespace handle.
        (
            "builtins_handle_import_attr",
            f"__builtins__.__import__('os').system('echo {SENTINEL}')",
        ),
        # 17. Loader/spec handles stay in _DISALLOWED_NAMES.
        (
            "loader_handle",
            f"f = __loader__\nopen({sentinel!r}, 'w').write({SENTINEL!r})",
        ),
        # 18. os/system reached through a plain attribute call.
        (
            "direct_os_system",
            "import os\nos.system('echo " + SENTINEL + "')",
        ),
    ]


EXPLOIT_IDS = [name for name, _ in _exploit_sources("unused-sentinel")]


def _assert_refused(result: dict, name: str) -> None:
    assert "error" in result, (
        f"{name}: AST gate did NOT reject the snippet. Observed result: {result!r}. "
        f"The snippet reached the subprocess -- returncode "
        f"{result.get('returncode')!r}, output {result.get('output', '')[:200]!r}."
    )
    assert "Rejected" in result["error"], (
        f"{name}: gate failed closed but without the documented rejection: {result!r}"
    )
    assert result.get("returncode") is None, (
        f"{name}: a subprocess actually ran, so the gate is bypassable: {result!r}"
    )


def test_exploit_corpus_is_not_shrinking():
    """Guard the corpus itself: a shrinking sample set must not look green."""
    assert len(EXPLOIT_IDS) >= 17
    assert len(set(EXPLOIT_IDS)) == len(EXPLOIT_IDS)


@pytest.mark.parametrize("exploit_id", EXPLOIT_IDS)
def test_exploit_still_refused_after_precision_fix(exploit_id, tmp_path):
    """Every originally-exploitable route is still refused and never runs."""
    sentinel = str(tmp_path / "gate_precision_sentinel.txt")
    sources = dict(_exploit_sources(sentinel))

    result = _run_gate(sources[exploit_id])

    _assert_refused(result, exploit_id)
    assert not os.path.exists(sentinel), (
        f"{exploit_id} executed and wrote {sentinel}; file contents: "
        f"{open(sentinel, encoding='utf-8').read()!r}"
    )


# ---------------------------------------------------------------------------
# Judgement call: dunder names stay refused in Store position
# ---------------------------------------------------------------------------
# Deliberate and asymmetric with the Load exception: a *read* of `__name__` is
# ordinary introspection, but *rebinding* a dunder in the `python -c` child
# rewrites the module identity metadata the snippet itself can read back. No
# benign plugin code does that, and refusing it cannot weaken the gate.

@pytest.mark.parametrize(
    "source",
    [
        pytest.param("__name__ = 'spoofed'", id="store_name_dunder"),
        pytest.param("__doc__ = 'spoofed'", id="store_doc_dunder"),
        pytest.param("__x = 1", id="store_other_dunder"),
    ],
)
def test_dunder_names_still_refused_in_store_position(source):
    result = _run_gate(source)

    assert "error" in result, f"dunder store no longer refused: {result!r}"
    assert "Rejected" in result["error"], f"refused without documented reason: {result!r}"


# ---------------------------------------------------------------------------
# Defect 2 -- the model-facing tool description must be truthful
# ---------------------------------------------------------------------------
# The registered description is the string the LLM reads when deciding whether
# to call this tool. It claimed "Network and system-level calls are blocked",
# which the AST gate never enforced and never will.


class _EnabledPluginsConfig:
    plugins_enabled = True
    plugin_allow_dirs: list = []


def _registered_code_exec_description() -> str:
    from charlie.tools import ToolRegistry, register_plugin_tools_into

    registry = ToolRegistry()
    manager = register_plugin_tools_into(registry, _EnabledPluginsConfig())
    assert manager is not None
    assert "plugin_code_exec_python" in registry.get_tool_names()
    for definition in registry.get_tool_definitions():
        if definition["function"]["name"] == "plugin_code_exec_python":
            return definition["function"]["description"]
    raise AssertionError("plugin_code_exec_python is not registered")


def test_code_exec_description_does_not_claim_network_blocking():
    description = _registered_code_exec_description()

    assert "Network and system-level calls are blocked" not in description, (
        "code_exec_python description still makes the false claim that network "
        f"and system-level calls are blocked: {description!r}"
    )


def test_code_exec_description_states_what_is_actually_enforced():
    description = _registered_code_exec_description()

    assert "__builtins__" in description, (
        f"description omits the __builtins__ namespace handle: {description!r}"
    )
    assert "builtin" in description, (
        f"description omits the banned-builtin refusal: {description!r}"
    )
    assert "dunder" in description, (
        f"description omits the dunder-access refusal: {description!r}"
    )


def test_code_exec_description_does_not_overclaim_containment():
    """The validator's own docstring says it is NOT a hard sandbox."""
    description = _registered_code_exec_description().lower()

    assert "isolated" not in description, (
        f"description implies isolation: {description!r}"
    )
    assert "not a hard sandbox" in description, (
        f"description does not disclose that containment is absent: {description!r}"
    )
    # The disclosure must be a negation, not an assertion of containment.
    assert "not a hard sandbox" in description and "is a hard sandbox" not in description, (
        f"description asserts containment: {description!r}"
    )
    assert "no os-level isolation" in description, (
        f"description does not disclose the absence of OS-level isolation: {description!r}"
    )
    assert "network control" in description, (
        f"description does not disclose the absence of network control: {description!r}"
    )
