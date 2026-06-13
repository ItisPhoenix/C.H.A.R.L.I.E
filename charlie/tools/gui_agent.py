"""
charlie/tools/gui_agent.py

Autonomous vision-driven GUI agent.
Sees the screen, understands what's there, and acts.
"""

import json
import logging
import time

try:
    import pyautogui
except ImportError:
    pyautogui = None

from charlie.tools.tool_decorator import tool, RiskTier

logger = logging.getLogger("charlie.tools.gui_agent")

if pyautogui is not None:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05

# Valid actions the vision model can request
VALID_ACTIONS = {"click", "type", "scroll", "hotkey", "press", "done"}


@tool(
    name="gui_do",
    description=(
        "Autonomously perform a task on the screen by describing what you want done in natural language. "
        "The agent captures the screen, analyzes it with the vision model, and executes actions until done."
    ),
    category="desktop",
    risk_tier=RiskTier.TIER_2,
)
def gui_do(task: str, max_steps: int = 10) -> str:
    """
    Autonomous GUI agent. Captures screen, sends to vision model,
    gets coordinates, clicks/types/scrolls, repeats until done.

    Examples:
      - "Click the File menu, then click Save"
      - "Type 'hello world' in the search box and press Enter"
      - "Close the popup dialog"
    """
    from charlie.tools._vision_bridge import get_brain

    brain = get_brain()
    if not brain or not hasattr(brain, "vision_handler"):
        return "Vision handler not available"

    history: list[str] = []

    for step in range(max_steps):
        # 1. Capture screen
        screenshot = brain.vision_handler.capture_screen()
        if screenshot is None:
            return f"Step {step + 1}: Failed to capture screen"

        # 2. Send to vision model with task context
        prompt = (
            f'You are a GUI automation agent. Task: "{task}"\n'
            f"Step {step + 1} of {max_steps}.\n"
            f"Previous actions: {history[-3:] if history else 'none'}\n\n"
            f"Analyze this screenshot. What single action should I take next?\n\n"
            f"Respond with ONLY valid JSON (no markdown):\n"
            f'{{"action": "click|type|scroll|hotkey|press|done",\n'
            f'  "x": <pixel x>, "y": <pixel y>,\n'
            f'  "text": "<text to type, if action=type>",\n'
            f'  "keys": ["key1", "key2"],\n'
            f'  "key": "<single key to press>",\n'
            f'  "direction": "up|down|left|right",\n'
            f'  "reason": "<why this action>"}}'
        )

        response = brain.vision_handler.ask_vision(screenshot, prompt)
        if not response:
            return f"Step {step + 1}: No response from vision model"

        # 3. Parse response
        content = response.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            action = json.loads(content)
        except json.JSONDecodeError:
            return f"Step {step + 1}: Invalid JSON from vision model: {response[:200]}"

        action_type = action.get("action", "")
        if action_type not in VALID_ACTIONS:
            return f"Step {step + 1}: Unknown action '{action_type}'"

        if action_type == "done":
            return f"Task completed in {step + 1} steps. Actions: {history}"

        # 4. Execute action
        try:
            if action_type == "click":
                x, y = int(action["x"]), int(action["y"])
                pyautogui.click(x, y)
                history.append(f"click({x}, {y}) - {action.get('reason', '')}")

            elif action_type == "type":
                text = action.get("text", "")
                pyautogui.typewrite(text, interval=0.02)
                history.append(f"type('{text[:30]}') - {action.get('reason', '')}")

            elif action_type == "scroll":
                direction = action.get("direction", "down")
                clicks = 3 if direction in ("up", "left") else -3
                x = int(action.get("x", pyautogui.position().x))
                y = int(action.get("y", pyautogui.position().y))
                pyautogui.scroll(clicks, x=x, y=y)
                history.append(f"scroll({direction}) - {action.get('reason', '')}")

            elif action_type == "hotkey":
                keys = action.get("keys", [])
                pyautogui.hotkey(*keys)
                history.append(f"hotkey({'+'.join(keys)}) - {action.get('reason', '')}")

            elif action_type == "press":
                key = action.get("key", "enter")
                pyautogui.press(key)
                history.append(f"press('{key}') - {action.get('reason', '')}")

        except Exception as e:
            return f"Step {step + 1}: Action failed: {e}"

        # 5. Wait for UI to respond
        time.sleep(0.5)

    return f"Task not completed after {max_steps} steps. Actions: {history}"
