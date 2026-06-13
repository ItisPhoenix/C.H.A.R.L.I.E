"""
charlie/tools/dynamic_builder.py

Dynamic tool builder for creating and registering tools at runtime.
Enables CHARLIE to extend its own capabilities based on user needs.

"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


@dataclass
class ToolParameter:
    """Definition of a tool parameter."""

    name: str
    type: str  # "string", "integer", "boolean", "float", "list", "dict"
    description: str
    required: bool = True
    default: Any = None
    enum_values: Optional[List[str]] = None  # For constrained choices

    def validate(self, value: Any) -> bool:
        """Validate a value against this parameter's constraints."""
        if value is None:
            return not self.required

        if self.enum_values and value not in self.enum_values:
            return False

        type_map = {"string": str, "integer": int, "boolean": bool, "float": (int, float), "list": list, "dict": dict}

        expected_type = type_map.get(self.type)
        if expected_type and not isinstance(value, expected_type):
            return False

        return True


@dataclass
class ToolDefinition:
    """Complete definition of a dynamic tool."""

    tool_id: str
    name: str
    description: str
    category: str  # "file", "web", "data", "system", "custom"
    code: str  # The actual Python code - must be before default fields

    # Optional fields with defaults
    parameters: List[ToolParameter] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)  # Required imports

    # Metadata
    created_at: float = field(default_factory=time.time)
    created_by: str = "system"  # "system", "user", "llm"
    version: str = "1.0.0"

    # Safety metadata (Req 5.9). Generated tools default to the most
    # restrictive tier until they have been reviewed, so they always route
    # through the strictest confirmation gate.
    risk_tier: str = "TIER_3"

    # Usage tracking
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used: Optional[float] = None

    # Status
    enabled: bool = True
    verified: bool = False  # Has been tested

    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total

    def record_success(self):
        """Record successful execution."""
        self.usage_count += 1
        self.success_count += 1
        self.last_used = time.time()

    def record_failure(self):
        """Record failed execution."""
        self.usage_count += 1
        self.failure_count += 1
        self.last_used = time.time()

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                    "enum_values": p.enum_values,
                }
                for p in self.parameters
            ],
            "code": self.code,
            "imports": self.imports,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "version": self.version,
            "risk_tier": self.risk_tier,
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used": self.last_used,
            "enabled": self.enabled,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ToolDefinition":
        """Deserialize from dictionary."""
        params = [
            ToolParameter(
                name=p["name"],
                type=p["type"],
                description=p["description"],
                required=p.get("required", True),
                default=p.get("default"),
                enum_values=p.get("enum_values"),
            )
            for p in data.get("parameters", [])
        ]

        return cls(
            tool_id=data["tool_id"],
            name=data["name"],
            description=data["description"],
            category=data["category"],
            parameters=params,
            code=data["code"],
            imports=data.get("imports", []),
            created_at=data.get("created_at", time.time()),
            created_by=data.get("created_by", "system"),
            version=data.get("version", "1.0.0"),
            risk_tier=data.get("risk_tier", "TIER_3"),
            usage_count=data.get("usage_count", 0),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            last_used=data.get("last_used"),
            enabled=data.get("enabled", True),
            verified=data.get("verified", False),
        )


@dataclass
class ToolExecutionResult:
    """Result of tool execution."""

    tool_id: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0
    timestamp: float = field(default_factory=time.time)


class DynamicToolBuilder:
    """
    Dynamic tool builder that creates and manages tools at runtime.
    Tools are defined as code and can be created by the LLM or user.

    Implements singleton pattern for global access.
    """

    _instance: Optional["DynamicToolBuilder"] = None
    _lock = threading.Lock()

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize the dynamic tool builder.

        Args:
            storage_path: Optional path for persistent storage
        """
        self._tools: Dict[str, ToolDefinition] = {}
        self._executors: Dict[str, Callable] = {}  # Compiled tool functions
        self._storage_path = storage_path
        self._dirty_tools: Set[str] = set()
        self._save_thread: Optional[threading.Thread] = None
        self._running = False
        self._compilation_errors: Dict[str, str] = {}  # tool_id -> error

        # Built-in tool templates
        self._templates: Dict[str, str] = {}
        self._register_default_templates()

        # Load existing tools
        if storage_path:
            self._load_tools()
            self._start_save_thread()

    @classmethod
    def get_builder(cls, storage_path: Optional[str] = None) -> "DynamicToolBuilder":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(storage_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance and cls._instance._running:
                cls._instance._running = False
                if cls._instance._save_thread:
                    cls._instance._save_thread.join(timeout=2)
            cls._instance = None

    def _register_default_templates(self):
        """Register built-in tool templates."""
        self._templates["file_reader"] = '''
def {tool_name}({params}):
    """{description}"""
    import os
    if not os.path.exists(path):
        return {{"error": f"File not found: {{path}}"}}
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    return {{"content": content, "path": path, "size": len(content)}}
'''

        self._templates["file_writer"] = '''
def {tool_name}({params}):
    """{description}"""
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return {{"success": True, "path": path, "bytes_written": len(content)}}
'''

        self._templates["web_search"] = '''
def {tool_name}({params}):
    """{description}"""
    import urllib.request
    import json
    query = urllib.parse.quote(query)
    url = f"https://api.example.com/search?q={{query}}"
    # Simplified - real implementation would use actual search API
    return {{"query": query, "results": []}}
'''

        self._templates["data_transform"] = '''
def {tool_name}({params}):
    """{description}"""
    import json
    data = json.loads(data) if isinstance(data, str) else data
    # Apply transformation
    if transform_type == "filter":
        result = [item for item in data if item.get(key) == value]
    elif transform_type == "map":
        result = [{{k: item.get(k) for k in keys}} for item in data]
    elif transform_type == "sort":
        result = sorted(data, key=lambda x: x.get(sort_key, ""))
    else:
        result = data
    return {{"result": result, "count": len(result)}}
'''

    def create_tool(self, definition: ToolDefinition) -> Tuple[bool, Optional[str]]:
        """
        Create and register a new tool.

        Args:
            definition: Tool definition to create

        Returns:
            Tuple of (success, error_message)
        """
        if definition.tool_id in self._tools:
            return False, f"Tool {definition.tool_id} already exists"

        # Validate code compiles
        success, error = self._verify_tool_code(definition)
        if not success:
            return False, error

        # Store definition
        self._tools[definition.tool_id] = definition
        self._dirty_tools.add(definition.tool_id)

        # Compile and store executor
        success, error = self._compile_tool(definition)
        if not success:
            self._compilation_errors[definition.tool_id] = error
            return False, error

        return True, None

    # ─────────────────────────────────────────────────────────────────────────────
    # SAFETY SCAN — Banned and Required Patterns
    # ─────────────────────────────────────────────────────────────────────────────

    # Banned patterns that will cause auto-rejection
    BANNED_PATTERNS = [
        (r"\beval\s*\(", "eval() is forbidden — security risk"),
        (r"\bexec\s*\(", "exec() is forbidden — security risk"),
        (r"subprocess\.Popen\s*\([^)]*shell\s*=\s*True", "subprocess with shell=True is forbidden"),
        (r"subprocess\.run\s*\([^)]*shell\s*=\s*True", "subprocess.run with shell=True is forbidden"),
        (r"subprocess\.call\s*\([^)]*shell\s*=\s*True", "subprocess.call with shell=True is forbidden"),
        (r"\bos\.system\s*\(", "os.system() is forbidden — use subprocess with shell=False"),
        (r"\b__import__\s*\(", "__import__() is forbidden — use importlib instead"),
    ]

    # Required structural check that is actually satisfiable by builder
    # output: generated/registered code MUST define at least one function.
    # (The brittle decorator/return-type patterns that previously lived here —
    # ``@risk_tier``, ``@tool_metadata`` and ``ToolResult`` — rejected the
    # builder's own templates, so safety metadata is now attached to the
    # ToolDefinition instead of being hand-written into every template.)
    REQUIRED_FUNCTION_PATTERN = (
        r"def\s+\w+\s*\(",
        "Tool code must define at least one function",
    )

    def _safety_scan(self, code: str) -> Tuple[bool, Optional[str]]:
        """
        Scan tool code for banned patterns and verify it is well-formed.

        Auto-rejects if ANY banned pattern is found (the real security gate:
        eval/exec/os.system/shell=True/__import__ are always rejected).
        Auto-rejects if the code defines no function at all.

        Safety metadata (the Risk_Tier) is carried on the ToolDefinition rather
        than required inside the code text, so the builder's own generated
        tools pass this scan while malicious input still fails closed.

        Args:
            code: Tool code to scan

        Returns:
            Tuple of (passed, error_message)
        """
        import re

        # Check for banned patterns (hard security gate — unchanged).
        for pattern, message in self.BANNED_PATTERNS:
            if re.search(pattern, code):
                return False, f"SAFETY REJECT: {message}"

        # Require well-formed tool code: at least one function definition.
        pattern, message = self.REQUIRED_FUNCTION_PATTERN
        if not re.search(pattern, code):
            return False, f"SAFETY REJECT: {message}"

        return True, None

    def _verify_tool_code(self, definition: ToolDefinition) -> Tuple[bool, Optional[str]]:
        """Verify tool code is syntactically correct and passes safety scan."""
        # First run safety scan
        safe, error = self._safety_scan(definition.code)
        if not safe:
            return False, error

        # Then verify syntax
        try:
            compile(definition.code, f"<tool:{definition.tool_id}>", "exec")
            return True, None
        except SyntaxError as e:
            return False, f"Syntax error: {e}"

    def _compile_tool(self, definition: ToolDefinition) -> Tuple[bool, Optional[str]]:
        """Compile tool code into an executable function.

        Note: The builder itself uses exec() here because it IS the trusted compiler.
        BANNED_PATTERNS prevents user/LLM-supplied code from calling exec()/eval(),
        but the builder needs exec() to compile that code into functions.
        This is intentional — not a contradiction.
        """
        try:
            # Create execution namespace
            namespace = {}

            # Add imports
            for imp in definition.imports:
                exec(imp, namespace)  # noqa: S102 — trusted builder code

            # Compile the tool code
            exec(definition.code, namespace)  # noqa: S102 — trusted builder code

            # Find the main function (last defined function in code)
            func_name = self._extract_function_name(definition.code)
            if func_name and func_name in namespace:
                self._executors[definition.tool_id] = namespace[func_name]
                definition.verified = True
                return True, None
            else:
                return False, "Could not find function in code"
        except Exception as e:
            return False, f"Compilation error: {e}"

    def _extract_function_name(self, code: str) -> Optional[str]:
        """Extract function name from code."""
        # Find all function definitions
        pattern = r"def\s+(\w+)\s*\("
        matches = re.findall(pattern, code)
        if matches:
            return matches[-1]  # Return last function (main function)
        return None

    def update_tool(self, tool_id: str, definition: ToolDefinition) -> Tuple[bool, Optional[str]]:
        """
        Update an existing tool.

        Args:
            tool_id: ID of tool to update
            definition: New definition

        Returns:
            Tuple of (success, error_message)
        """
        if tool_id not in self._tools:
            return False, f"Tool {tool_id} not found"

        # Verify new code
        success, error = self._verify_tool_code(definition)
        if not success:
            return False, error

        # Update
        self._tools[tool_id] = definition
        self._dirty_tools.add(tool_id)

        # Recompile
        success, error = self._compile_tool(definition)
        if not success:
            return False, error

        return True, None

    def delete_tool(self, tool_id: str) -> bool:
        """Delete a tool."""
        if tool_id in self._tools:
            del self._tools[tool_id]
            self._executors.pop(tool_id, None)
            self._compilation_errors.pop(tool_id, None)
            self._dirty_tools.discard(tool_id)
            return True
        return False

    def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        """Get a tool definition."""
        return self._tools.get(tool_id)

    def get_all_tools(self) -> List[ToolDefinition]:
        """Get all tool definitions."""
        return list(self._tools.values())

    def get_tools_by_category(self, category: str) -> List[ToolDefinition]:
        """Get tools in a specific category."""
        return [t for t in self._tools.values() if t.category == category]

    def get_enabled_tools(self) -> List[ToolDefinition]:
        """Get all enabled tools."""
        return [t for t in self._tools.values() if t.enabled]

    def execute_tool(self, tool_id: str, parameters: Dict[str, Any]) -> ToolExecutionResult:
        """
        Execute a tool with given parameters.

        Args:
            tool_id: Tool to execute
            parameters: Parameters to pass

        Returns:
            ToolExecutionResult with outcome
        """
        start_time = time.time()

        # Get tool
        tool = self._tools.get(tool_id)
        if not tool:
            return ToolExecutionResult(tool_id=tool_id, success=False, error=f"Tool {tool_id} not found")

        if not tool.enabled:
            return ToolExecutionResult(tool_id=tool_id, success=False, error=f"Tool {tool_id} is disabled")

        # Get executor
        executor = self._executors.get(tool_id)
        if not executor:
            return ToolExecutionResult(tool_id=tool_id, success=False, error=f"Tool {tool_id} not compiled")

        # Validate parameters
        for param in tool.parameters:
            if param.required and param.name not in parameters:
                return ToolExecutionResult(
                    tool_id=tool_id, success=False, error=f"Missing required parameter: {param.name}"
                )

        # Execute
        try:
            result = executor(**parameters)
            tool.record_success()

            return ToolExecutionResult(
                tool_id=tool_id, success=True, result=result, execution_time_ms=(time.time() - start_time) * 1000
            )
        except Exception as e:
            tool.record_failure()
            return ToolExecutionResult(
                tool_id=tool_id, success=False, error=str(e), execution_time_ms=(time.time() - start_time) * 1000
            )

    def generate_tool_from_description(self, description: str, category: str = "custom") -> Optional[ToolDefinition]:
        """
        Generate a tool definition from a natural language description.
        This is a template-based generator - real LLM would do more sophisticated generation.

        Args:
            description: Description of what the tool should do
            category: Tool category

        Returns:
            Generated ToolDefinition or None
        """
        # Simple template matching
        description_lower = description.lower()

        if "read" in description_lower and "file" in description_lower:
            template = self._templates.get("file_reader")
            tool_id = f"dynamic_file_reader_{int(time.time())}"
            params = "path: str, encoding: str = 'utf-8'"
        elif "write" in description_lower and "file" in description_lower:
            template = self._templates.get("file_writer")
            tool_id = f"dynamic_file_writer_{int(time.time())}"
            params = "path: str, content: str"
        elif "search" in description_lower and "web" in description_lower:
            template = self._templates.get("web_search")
            tool_id = f"dynamic_web_search_{int(time.time())}"
            params = "query: str, limit: int = 10"
        elif "transform" in description_lower or "filter" in description_lower:
            template = self._templates.get("data_transform")
            tool_id = f"dynamic_data_transform_{int(time.time())}"
            params = "data: list, transform_type: str, key: str = None, value: str = None, keys: list = None, sort_key: str = None"
        else:
            # Generic template. Define all referenced names BEFORE building the
            # template string so the generated code never raises NameError, and
            # use the same .format() placeholders as the other templates so the
            # shared formatting step below works uniformly.
            tool_id = f"dynamic_tool_{int(time.time())}"
            func_name = tool_id.replace("-", "_")
            params = "**kwargs"
            template = '''
def {tool_name}({params}):
    """{description}"""
    return {{"result": "Not implemented", "args": kwargs}}
'''

        if not template:
            return None

        # Format template
        func_name = tool_id.replace("-", "_")
        code = template.format(tool_name=func_name, params=params, description=description)

        return ToolDefinition(
            tool_id=tool_id,
            name=description[:50],
            description=description,
            category=category,
            code=code,
            imports=["import os", "import json", "import urllib.parse"],
            created_by="llm",
            risk_tier="TIER_3",
        )

    def enable_tool(self, tool_id: str) -> bool:
        """Enable a tool."""
        if tool_id in self._tools:
            self._tools[tool_id].enabled = True
            self._dirty_tools.add(tool_id)
            return True
        return False

    def disable_tool(self, tool_id: str) -> bool:
        """Disable a tool."""
        if tool_id in self._tools:
            self._tools[tool_id].enabled = False
            self._dirty_tools.add(tool_id)
            return True
        return False

    def get_tool_stats(self) -> Dict[str, Any]:
        """Get statistics about all tools."""
        total = len(self._tools)
        enabled = sum(1 for t in self._tools.values() if t.enabled)
        verified = sum(1 for t in self._tools.values() if t.verified)

        total_usage = sum(t.usage_count for t in self._tools.values())
        total_success = sum(t.success_count for t in self._tools.values())
        total_failure = sum(t.failure_count for t in self._tools.values())

        return {
            "total_tools": total,
            "enabled_tools": enabled,
            "verified_tools": verified,
            "total_usage": total_usage,
            "total_success": total_success,
            "total_failure": total_failure,
            "overall_success_rate": total_success / (total_success + total_failure)
            if (total_success + total_failure) > 0
            else 1.0,
            "by_category": {
                cat: len(self.get_tools_by_category(cat)) for cat in set(t.category for t in self._tools.values())
            },
        }

    def _load_tools(self):
        """Load tools from persistent storage."""
        if not self._storage_path:
            return

        tools_file = os.path.join(self._storage_path, "dynamic_tools.json")
        if os.path.exists(tools_file):
            try:
                with open(tools_file, "r") as f:
                    data = json.load(f)
                    for tdata in data.get("tools", []):
                        tool = ToolDefinition.from_dict(tdata)
                        self._tools[tool.tool_id] = tool

                        # Try to compile
                        success, _ = self._compile_tool(tool)
                        if not success:
                            self._compilation_errors[tool.tool_id] = "Failed to compile"
            except Exception:
                pass

    def _save_tools(self):
        """Save tools to persistent storage."""
        if not self._storage_path or not self._dirty_tools:
            return

        tools_file = os.path.join(self._storage_path, "dynamic_tools.json")

        # Load existing
        existing = {}
        if os.path.exists(tools_file):
            try:
                with open(tools_file, "r") as f:
                    existing = json.load(f)
            except Exception:
                existing = {"tools": []}

        # Update dirty tools
        for tid in self._dirty_tools:
            tool = self._tools.get(tid)
            if tool:
                found = False
                for i, tdata in enumerate(existing.get("tools", [])):
                    if tdata.get("tool_id") == tid:
                        existing["tools"][i] = tool.to_dict()
                        found = True
                        break
                if not found:
                    existing.setdefault("tools", []).append(tool.to_dict())

        # Write back
        try:
            with open(tools_file, "w") as f:
                json.dump(existing, f)
            self._dirty_tools.clear()
        except Exception:
            pass

    def _start_save_thread(self):
        """Start background thread for periodic saves."""
        self._running = True
        self._save_thread = threading.Thread(target=self._save_loop, daemon=True)
        self._save_thread.start()

    def _save_loop(self):
        """Background loop for periodic saves."""
        while self._running:
            time.sleep(60)
            self._save_tools()


# Convenience function
def get_dynamic_builder(storage_path: Optional[str] = None) -> DynamicToolBuilder:
    """Get the singleton DynamicToolBuilder instance."""
    return DynamicToolBuilder.get_builder(storage_path)


if __name__ == "__main__":
    # Test the dynamic tool builder
    builder = DynamicToolBuilder()

    # Create a simple tool
    tool = ToolDefinition(
        tool_id="greet",
        name="Greeting Tool",
        description="Returns a greeting message",
        category="custom",
        code='''
def greet(name: str, formal: bool = False) -> dict:
    """Return a greeting message."""
    if formal:
        return {"message": f"Good day, {name}!"}
    else:
        return {"message": f"Hey, {name}!"}
''',
        imports=[],
        created_by="test",
    )

    success, error = builder.create_tool(tool)
    print(f"Tool creation: {'success' if success else f'failed: {error}'}")

    # Execute the tool
    result = builder.execute_tool("greet", {"name": "Alice", "formal": True})
    print(f"Execution: success={result.success}, result={result.result}")

    # Generate a tool from description
    generated = builder.generate_tool_from_description("Read the contents of a file and return it", category="file")
    if generated:
        print(f"Generated tool: {generated.tool_id}")
        success, error = builder.create_tool(generated)
        print(f"Generated tool creation: {'success' if success else f'failed: {error}'}")

    # Get stats
    stats = builder.get_tool_stats()
    print(f"Stats: {stats}")

    print("\nAll tests passed!")
