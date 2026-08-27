import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path so we can import charlie
sys.path.insert(0, str(Path(__file__).parent.parent))

from charlie.utils import make_id
from tests.isolation import IsolatedEventBus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("harness")

async def main():
    logger.info("Using isolated test EventBus recorder; live Charlie ports are unavailable.")
    async with IsolatedEventBus() as bus:

        input("\n[1/4] Press Enter to show a Watcher Alert widget (role=warning)...")
        await bus.send_command({
            "type": "test_emit_event",
            "payload": {
                "event_type": "presentation_intent",
                "event_payload": {
                    "id": make_id(),
                    "kind": "widget",
                    "title": "Watcher Alert",
                    "summary": "Memory usage is above 90%",
                    "attention_level": "high",
                    "replayable": False,
                }
            }
        })

        input("\n[2/4] Press Enter to show a Background Task notification (role=info)...")
        await bus.send_command({
            "type": "test_emit_event",
            "payload": {
                "event_type": "presentation_intent",
                "event_payload": {
                    "id": make_id(),
                    "kind": "notification",
                    "title": "Background Task",
                    "summary": "Summarizing 50 documents...",
                    "attention_level": "inform",
                    "replayable": False,
                }
            }
        })

        input("\n[3/4] Press Enter to trigger a Tool Approval Modal (risk=destructive)...")
        request_id = make_id()
        await bus.send_command({
            "type": "test_emit_event",
            "payload": {
                "event_type": "presentation_intent",
                "event_payload": {
                    "id": request_id,
                    "kind": "attention",
                    "title": "Approval needed: shell_execute",
                    "summary": "Deleting everything as requested.",
                    "content": {
                        "request_id": request_id,
                        "tool_name": "shell_execute",
                        "arguments": {"command": "rm -rf /"},
                        "reason": "Deleting everything as requested.",
                        "risk_class": "destructive",
                    },
                    "attention_level": "critical",
                    "dismiss_policy": "manual",
                    "replayable": True,
                }
            }
        })

        input("\n[4/4] Press Enter to finish isolated presentation recording...")
        logger.info("No desktop, browser, EventBus, database, store, or live runtime access performed.")

        print("\nAll events emitted. Visual verification complete!")

if __name__ == "__main__":
    asyncio.run(main())
