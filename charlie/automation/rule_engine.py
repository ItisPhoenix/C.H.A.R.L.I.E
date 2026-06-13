"""Rule Engine — matches events against known automation rules."""

from __future__ import annotations

import ast
import json
import logging
import os

from charlie.automation.models import AutomationRule, Event

logger = logging.getLogger("charlie.automation.rule_engine")

# Dangerous AST node types — reject these to prevent code injection.
# Everything else is allowed (with __builtins__: {} for runtime safety).
_UNSAFE_AST_NODES = (
    ast.Import,
    ast.ImportFrom,  # import statements
    ast.FunctionDef,
    ast.AsyncFunctionDef,  # function definitions
    ast.ClassDef,  # class definitions
    ast.Delete,  # del statements
    ast.Raise,  # raise statements
    ast.Try,
    ast.ExceptHandler,  # try/except
    ast.Global,
    ast.Nonlocal,  # scope manipulation
    ast.Yield,
    ast.YieldFrom,  # generators
    ast.Await,  # async
    ast.NamedExpr,  # walrus operator
)

_UNSAFE_NAMES = {
    "exec",
    "eval",
    "compile",
    "__import__",
    "open",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
}

PERSIST_PATH = "charlie/config/automation_rules.json"


class RuleEngine:
    """Matches events against automation rules and returns applicable actions."""

    def __init__(self):
        self._rules: list[AutomationRule] = []

    def add_rule(self, rule: AutomationRule):
        """Add a rule to the engine."""
        self._rules.append(rule)
        logger.info(f"rule_added | name={rule.name} | trigger={rule.trigger}")

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name. Returns True if found."""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    def get_rule(self, name: str) -> AutomationRule | None:
        """Get a rule by name."""
        return next((r for r in self._rules if r.name == name), None)

    def get_all_rules(self) -> list[AutomationRule]:
        """Return all rules."""
        return list(self._rules)

    def match(self, event: Event) -> list[AutomationRule]:
        """Find rules that match this event (by trigger + condition)."""
        matched = []
        for rule in self._rules:
            if not rule.enabled:
                continue
            if rule.trigger != event.type:
                continue
            if self._evaluate_condition(rule.condition, event):
                matched.append(rule)
        return matched

    # Allowed AST node types for safe evaluation
    _SAFE_NODES = (
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Attribute,
        ast.Compare,
        ast.BoolOp,
        ast.UnaryOp,
        ast.IfExp,
        ast.List,
        ast.Tuple,
        ast.Set,
        ast.Dict,
        ast.Starred,
        ast.Call,  # Allowed for safe method calls only
    )

    # Safe method names that can be called on objects
    _SAFE_METHODS = {
        "get",
        "keys",
        "values",
        "items",
        "lower",
        "upper",
        "strip",
        "startswith",
        "endswith",
        "split",
        "join",
        "replace",
        "find",
        "count",
        "index",
        "copy",
        "pop",
        "append",
        "extend",
        "insert",
        "remove",
        "sort",
        "reverse",
        "format",
        "encode",
        "decode",
    }

    _SAFE_OPERATORS = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.Is: lambda a, b: a is b,
        ast.IsNot: lambda a, b: a is not b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    def _evaluate_condition(self, condition: str, event: Event) -> bool:
        """Safely evaluate a condition expression against event data.

        Uses a whitelist AST walker — only allows safe node types.
        No eval(), no exec(), no builtins. Completely eliminates code injection.
        """
        try:
            tree = ast.parse(condition, mode="eval")
            return self._safe_eval_node(tree.body, {"data": event.data})
        except Exception as e:
            logger.warning(f"condition_eval_failed | condition={condition} | error={e}")
            return False

    def _safe_eval_node(self, node, namespace: dict):
        """Recursively evaluate an AST node using only safe operations."""
        if not isinstance(node, self._SAFE_NODES + (ast.expr, ast.operator, ast.cmpop, ast.boolop, ast.unaryop)):
            raise ValueError(f"Blocked AST node type: {type(node).__name__}")

        # Constants
        if isinstance(node, ast.Constant):
            return node.value

        # Names (variables)
        if isinstance(node, ast.Name):
            if node.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[node.id]
            if node.id in namespace:
                return namespace[node.id]
            raise ValueError(f"Undefined name: {node.id}")

        # Attribute access (e.g., data.key)
        if isinstance(node, ast.Attribute):
            obj = self._safe_eval_node(node.value, namespace)
            if isinstance(obj, dict):
                return obj.get(node.attr)
            return getattr(obj, node.attr, None)

        # Function/method calls (only safe methods allowed)
        if isinstance(node, ast.Call):
            # Only allow method calls (obj.method(args)), not bare function calls
            if isinstance(node.func, ast.Attribute):
                obj = self._safe_eval_node(node.func.value, namespace)
                method_name = node.func.attr
                if method_name not in self._SAFE_METHODS:
                    raise ValueError(f"Blocked method: {method_name}")
                args = [self._safe_eval_node(arg, namespace) for arg in node.args]
                method = getattr(obj, method_name, None)
                if method is None:
                    raise ValueError(f"Method not found: {method_name}")
                return method(*args)
            else:
                raise ValueError(f"Blocked function call: {ast.dump(node.func)[:50]}")

        # Comparison (a == b, a in b, etc.)
        if isinstance(node, ast.Compare):
            left = self._safe_eval_node(node.left, namespace)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._safe_eval_node(comparator, namespace)
                op_func = self._SAFE_OPERATORS.get(type(op))
                if op_func is None:
                    raise ValueError(f"Blocked operator: {type(op).__name__}")
                if not op_func(left, right):
                    return False
                left = right
            return True

        # Boolean operations (and/or)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for value in node.values:
                    if not self._safe_eval_node(value, namespace):
                        return False
                return True
            elif isinstance(node.op, ast.Or):
                for value in node.values:
                    if self._safe_eval_node(value, namespace):
                        return True
                return False
            raise ValueError(f"Blocked boolop: {type(node.op).__name__}")

        # Unary operations (not)
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not self._safe_eval_node(node.operand, namespace)
            if isinstance(node.op, ast.USub):
                return -self._safe_eval_node(node.operand, namespace)
            raise ValueError(f"Blocked unaryop: {type(node.op).__name__}")

        # Ternary (a if b else c)
        if isinstance(node, ast.IfExp):
            if self._safe_eval_node(node.test, namespace):
                return self._safe_eval_node(node.body, namespace)
            return self._safe_eval_node(node.orelse, namespace)

        # Collections
        if isinstance(node, ast.List):
            return [self._safe_eval_node(elt, namespace) for elt in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._safe_eval_node(elt, namespace) for elt in node.elts)
        if isinstance(node, ast.Set):
            return {self._safe_eval_node(elt, namespace) for elt in node.elts}
        if isinstance(node, ast.Dict):
            return {
                self._safe_eval_node(k, namespace): self._safe_eval_node(v, namespace)
                for k, v in zip(node.keys, node.values)
            }

        raise ValueError(f"Blocked AST node: {type(node).__name__}")

    def update_rule(self, name: str, **kwargs) -> bool:
        """Update a rule's properties. Returns True if found."""
        rule = self.get_rule(name)
        if not rule:
            return False
        for key, value in kwargs.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        return True

    def save_rules(self, path: str = PERSIST_PATH):
        """Persist rules to JSON file."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = [r.to_dict() for r in self._rules]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"rules_saved | count={len(self._rules)} | path={path}")
        except Exception as e:
            logger.error(f"rules_save_failed | {e}")

    def load_rules(self, path: str = PERSIST_PATH):
        """Load rules from JSON file. Merges with existing rules."""
        try:
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing_names = {r.name for r in self._rules}
            loaded = 0
            for d in data:
                rule = AutomationRule.from_dict(d)
                if rule.name not in existing_names:
                    self._rules.append(rule)
                    loaded += 1
            logger.info(f"rules_loaded | loaded={loaded} | total={len(self._rules)}")
        except Exception as e:
            logger.error(f"rules_load_failed | {e}")
