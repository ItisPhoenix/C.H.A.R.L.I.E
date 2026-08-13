"""Harmless managed-terminal smoke check."""

import asyncio

from charlie.terminal_service import TerminalManager


async def main() -> int:
    manager = TerminalManager()
    session = await manager.create()
    try:
        await manager.write(session.session_id, "echo CHARLIE_TERMINAL_SMOKE")
        for _ in range(10):
            await asyncio.sleep(0.1)
            if "CHARLIE_TERMINAL_SMOKE" in manager.snapshot(session.session_id)["output"]:
                print("terminal smoke: ok")
                return 0
        print("terminal smoke: output not observed")
        return 1
    finally:
        await manager.close_all()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
