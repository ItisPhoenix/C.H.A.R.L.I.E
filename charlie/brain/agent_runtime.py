"""
Agent Runtime -- Executes a single agent's task with independent LLM context.

Agent Runtime

This module provides:
- AgentResult dataclass for structured execution results
- AgentTaskContext for progress reporting and cancellation
- AgentRuntime: ReAct loop executor that runs an agent's task with its own
  system prompt and tool subset, independent of the main Brain conversation.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from charlie.brain.tool_call_parser import ToolCallParser
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Result of an agent's task execution."""

    success: bool
    task: str
    agent_name: str
    summary: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None


class AgentTaskContext:
    """Context passed to the agent during execution for progress reporting and cancellation."""

    def __init__(self, task_id: str, agent_name: str):
        self.task_id = task_id
        self.agent_name = agent_name
        self._cancelled = False

    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self):
        self._cancelled = True


# ---------------------------------------------------------------------------
# Agent Runtime
# ---------------------------------------------------------------------------


class AgentRuntime:
    """Executes a single agent's task with independent LLM context."""

    def __init__(self, brain: Any):
        self.brain = brain
        self._parser = ToolCallParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self, agent_spec: Any, task: str, context: str = "",
        task_chain: list = None,
    ) -> AgentResult:
        """Execute a task using the agent's spec.

        1. Build system prompt from agent_spec.system_prompt + skill content
        2. Build tool subset from agent_spec.tools
        3. If task_chain provided, execute each step sequentially via ReAct loop
        4. Otherwise run single ReAct loop: Think -> Act -> Observe -> ... until done
        5. Return AgentResult with summary and tool calls
        """
        # Sequential task chain execution
        if task_chain and len(task_chain) > 1:
            return await self._execute_chain(agent_spec, task, task_chain, context)

        return await self._run_react_loop(agent_spec, task, context)

    async def _execute_chain(
        self, agent_spec: Any, goal: str, task_chain: list, context: str = ""
    ) -> AgentResult:
        """Execute a chain of sub-tasks sequentially, feeding results forward."""
        start_time = time.time()
        agent_name = getattr(agent_spec, "name", "unknown")
        all_tool_calls = []
        step_results = []

        for i, step in enumerate(task_chain):
            step_desc = step.get("description", step.get("tool", f"step {i+1}"))
            logger.info("chain_step | agent=%s | step=%d/%d | desc=%s",
                        agent_name, i+1, len(task_chain), step_desc[:60])

            # Feed previous results into context
            prev_context = "\n".join(step_results[-3:]) if step_results else ""
            step_context = f"{context}\nPrevious results:\n{prev_context}" if prev_context else context

            result = await self._run_react_loop(agent_spec, step_desc, step_context)
            all_tool_calls.extend(result.tool_calls)
            step_results.append(result.summary)

            if not result.success:
                logger.warning("chain_step_failed | step=%d | agent=%s", i+1, agent_name)

        return AgentResult(
            success=True,
            task=goal,
            agent_name=agent_name,
            summary="\n\n".join(step_results),
            tool_calls=all_tool_calls,
        )

    async def _run_react_loop(
        self, agent_spec: Any, task: str, context: str = ""
    ) -> AgentResult:
        """Single ReAct loop execution."""
        start_time = time.time()
        agent_name = getattr(agent_spec, "name", "unknown")
        task_id = f"{agent_name}_{int(start_time)}"

        ctx = AgentTaskContext(task_id=task_id, agent_name=agent_name)

        logger.info(
            f"agent_runtime_start | agent={agent_name} | task='{task[:60]}...'"
        )

        # Build messages
        system_prompt = self._build_system_prompt(agent_spec, context)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        # Get tool subset
        tools = list(getattr(agent_spec, "tools", []))

        try:
            result = await self._run_react_loop(messages, tools, agent_spec, ctx)
            result.duration_seconds = time.time() - start_time
            logger.info(
                f"agent_runtime_done | agent={agent_name} | "
                f"success={result.success} | duration={result.duration_seconds:.1f}s | "
                f"tool_calls={len(result.tool_calls)}"
            )
            return result

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"agent_runtime_crash | agent={agent_name} | error={e}"
            )
            return AgentResult(
                success=False,
                task=task,
                agent_name=agent_name,
                summary=f"Runtime error: {e}",
                duration_seconds=duration,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # ReAct loop
    # ------------------------------------------------------------------

    async def _run_react_loop(
        self,
        messages: list,
        tools: list[str],
        agent_spec: Any,
        ctx: AgentTaskContext,
        step_timeout: int = 30,
    ) -> AgentResult:
        """Run the ReAct loop for an agent.

        Iterates: call LLM -> extract tool calls -> execute tools -> feed
        results back until the agent signals completion, hits max iterations,
        times out, or is cancelled.
        """
        config = getattr(agent_spec, "config", {}) or {}
        max_iterations = config.get("max_chain_depth", 8)
        timeout = config.get("timeout_seconds", 120)
        tool_calls_made: list[dict] = []
        start_time = time.time()

        for iteration in range(max_iterations):
            # Cancellation check
            if ctx.is_cancelled():
                logger.info(f"agent_cancelled | agent={ctx.agent_name}")
                return AgentResult(
                    success=False,
                    task=ctx.task_id,
                    agent_name=ctx.agent_name,
                    summary="Cancelled",
                    tool_calls=tool_calls_made,
                )

            # Timeout check
            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.warning(
                    f"agent_timeout | agent={ctx.agent_name} | elapsed={elapsed:.1f}s"
                )
                return AgentResult(
                    success=False,
                    task=ctx.task_id,
                    agent_name=ctx.agent_name,
                    summary="Timeout",
                    tool_calls=tool_calls_made,
                )

            # Per-step timeout
            elapsed = time.time() - start_time
            if elapsed > step_timeout:
                logger.warning(f"agent_step_timeout | agent={ctx.agent_name}")
                return AgentResult(success=False, task=ctx.task_id, agent_name=ctx.agent_name, summary="Step timeout", tool_calls=tool_calls_made)

            # Per-step timeout check
            elapsed = time.time() - start_time
            if iteration > 0 and elapsed > 60:
                logger.warning(f"agent_step_timeout | agent={ctx.agent_name} | elapsed={elapsed:.1f}s")
                return AgentResult(success=False, task=ctx.task_id, agent_name=ctx.agent_name, summary="Step timeout", tool_calls=tool_calls_made)

            # Call LLM
            logger.debug(
                f"agent_react_iter | agent={ctx.agent_name} | iter={iteration + 1}/{max_iterations}"
            )
            response = await self._call_llm(messages, tools)
            if not response:
                return AgentResult(
                    success=False,
                    task=ctx.task_id,
                    agent_name=ctx.agent_name,
                    summary="LLM call failed",
                    tool_calls=tool_calls_made,
                )

            # Extract tool calls from the response
            extracted = self._extract_tool_calls(response)
            if not extracted:
                # No tool calls means the agent is done -- use the response as summary
                summary = self._parser.sanitize_final_answer(response)
                return AgentResult(
                    success=True,
                    task=ctx.task_id,
                    agent_name=ctx.agent_name,
                    summary=summary[:500] if summary else response[:500],
                    tool_calls=tool_calls_made,
                )

            # Append assistant message to conversation
            messages.append({"role": "assistant", "content": response})

            # Execute each tool call
            for tc in extracted:
                if ctx.is_cancelled():
                    break

                tool_name = tc.get("tool", "")
                tool_args = tc.get("args", {})

                logger.info(
                    f"agent_tool_call | agent={ctx.agent_name} | tool={tool_name}"
                )
                result = await self._execute_tool(tc, tools)

                tool_calls_made.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result": str(result)[:200],
                    }
                )

                # Feed observation back into the conversation
                messages.append(
                    {"role": "user", "content": f"OBSERVATION: {result}"}
                )

        # Max iterations reached
        logger.warning(
            f"agent_max_iterations | agent={ctx.agent_name} | iters={max_iterations}"
        )
        return AgentResult(
            success=True,
            task=ctx.task_id,
            agent_name=ctx.agent_name,
            summary="Max iterations reached",
            tool_calls=tool_calls_made,
        )

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _call_llm(self, messages: list, tools: list[str]) -> str:
        """Call the LLM with the agent's messages and tool subset.

        Tries brain.llm_client.complete() first (preferred), falls back to
        brain.model_manager.nim_chat().
        """
        try:
            llm_client = getattr(self.brain, "llm_client", None)
            if llm_client is not None:
                response = await llm_client.complete(
                    messages, temperature=0.3, max_tokens=1024
                )
                # LLMResponse has .content attribute
                content = getattr(response, "content", None)
                if content:
                    return content

            # Fallback: model_manager.nim_chat()
            model_manager = getattr(self.brain, "model_manager", None)
            if model_manager is not None:
                raw = await model_manager.nim_chat(
                    messages, temperature=0.3, max_tokens=1024
                )
                if isinstance(raw, dict):
                    choices = raw.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        return message.get("content", "")
                return str(raw)

            logger.error("agent_no_llm_backend | no llm_client or model_manager")
            return ""

        except Exception as e:
            logger.error(f"agent_llm_call_failed | {e}")
            return ""

    # ------------------------------------------------------------------
    # Tool call extraction
    # ------------------------------------------------------------------

    def _extract_tool_calls(self, response: str) -> list[dict]:
        """Extract tool calls from LLM response.

        Looks for the standard JSON action format used by CHARLIE's LLM:
        {"action": "tool_name", "action_input": {...}}

        Also handles arrays of tool calls and regex fallbacks.
        """
        if not response or not isinstance(response, str):
            return []

        # Use the existing parser to extract action + args
        parsed = self._parser.parse(response)
        action = parsed.action
        action_input = parsed.action_input

        if action and action != "none":
            return [{"tool": action, "args": action_input}]

        # Try to find multiple tool calls in array format
        # e.g. [{"tool": "search", "args": {...}}, ...]
        array_match = re.search(r"\[[\s\S]*\]", response)
        if array_match:
            try:
                calls = json.loads(array_match.group(0))
                if isinstance(calls, list):
                    valid = []
                    for c in calls:
                        if isinstance(c, dict) and "tool" in c:
                            valid.append(
                                {
                                    "tool": c["tool"],
                                    "args": c.get("args", {}),
                                }
                            )
                    if valid:
                        return valid
            except (json.JSONDecodeError, ValueError):
                pass

        return []

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, tool_call: dict, allowed_tools: list[str],
        retries: int = 2,
    ) -> str:
        """Execute a single tool call via brain.execute_tools().

        Validates the tool is in the agent's allowed set before executing.
        """
        tool_name = tool_call.get("tool", "")

        if tool_name not in allowed_tools:
            msg = f"Error: Tool '{tool_name}' not allowed for this agent. Allowed: {allowed_tools}"
            logger.warning(f"agent_tool_denied | {msg}")
            return msg

        last_error = ""
        for attempt in range(retries + 1):
            try:
                result = self.brain.execute_tools(
                    {"tool": tool_name, "args": tool_call.get("args", {})}
                )
                return str(result)
            except Exception as e:
                last_error = str(e)
                if attempt < retries:
                    logger.warning(f"agent_tool_retry | tool={tool_name} | attempt={attempt+1}/{retries} | {e}")
                    __import__("time").sleep(0.5 * (attempt + 1))
        logger.error(f"agent_tool_error | tool={tool_name} | {last_error}")
        return f"Error after {retries+1} attempts: {last_error}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self, agent_spec: Any, context: str) -> str:
        """Build the system prompt for the agent's LLM call.

        Combines the agent's system_prompt with any additional context and
        instructions for the ReAct format.
        """
        base_prompt = getattr(agent_spec, "system_prompt", "You are a helpful AI agent.")

        parts = [base_prompt]

        # Inject skill content if the agent has skills defined
        skills = getattr(agent_spec, "skills", []) or []
        if skills:
            parts.append(f"Available skills: {', '.join(skills)}")

        # Inject additional context
        if context:
            parts.append(f"Additional context:\n{context}")

        # ReAct format instructions
        parts.append(
            "When you need to use a tool, respond with JSON in this format:\n"
            '{"action": "tool_name", "action_input": {"arg1": "value1"}, "final_answer": ""}\n'
            "When you are done and have the final answer, respond with:\n"
            '{"action": "none", "action_input": {}, "final_answer": "Your answer here"}\n'
            "Always use the JSON action format. Do not output plain text."
        )

        return "\n\n".join(parts)
