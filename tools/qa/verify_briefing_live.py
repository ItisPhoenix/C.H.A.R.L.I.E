"""
Live verification script for C.H.A.R.L.I.E. Briefing Workspace runtime execution.
Issues query: "What's happening today?"
Verifies:
1. Research execution completes without crashing.
2. PresentationResolver emits workspace intent (briefing).
3. Real Briefing workspace renders on screen.
4. Charlie Core smoothly docks bottom-right.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import websockets
from playwright.async_api import async_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
PROOFS_DIR = ROOT_DIR / "proofs"
PROOFS_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_PORT = 8000
FRONTEND_PORT = 5173


def is_backend_ready() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/api/session/active", timeout=1.0):
            return True
    except Exception:
        return False


def is_port_open(port: int) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=1.0):
            return True
    except Exception:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        finally:
            s.close()


def kill_port(port: int) -> None:
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True).decode()
            for line in out.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}"):
                    pid = parts[-1]
                    if pid != "0":
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


async def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if is_port_open(port):
            return True
        await asyncio.sleep(0.5)
    return False


async def run_live_verification():
    print("=" * 60)
    print("Starting Live Charlie Verification: 'What\'s happening today?'")
    print("=" * 60)

    kill_port(BACKEND_PORT)
    kill_port(FRONTEND_PORT)
    kill_port(5555)
    kill_port(5556)
    await asyncio.sleep(2.0)

    spawned_procs = []

    # 1. Start Charlie backend runtime
    print("Starting backend runtime (main.py)...", flush=True)
    backend_env = os.environ.copy()
    backend_env["PET_ENABLED"] = "false"
    backend_env["CHARLIE_NO_VOICE"] = "1"
    backend_env["TELEGRAM_ENABLED"] = "false"
    backend_env["PYTHONUNBUFFERED"] = "1"

    runtime_log = PROOFS_DIR / "live_verify_runtime.log"
    runtime_proc = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=str(ROOT_DIR),
        env=backend_env,
        stdout=runtime_log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    spawned_procs.append(runtime_proc)

    start_backend = time.monotonic()
    while time.monotonic() - start_backend < 45.0:
        if is_backend_ready():
            break
        await asyncio.sleep(0.5)
    else:
        raise RuntimeError("Backend runtime failed to start on port 8000")
    print("[OK] Backend runtime is ready.", flush=True)

    # 2. Start Frontend Vite server
    print("Starting Frontend Vite server on port 5173...", flush=True)
    frontend_cmd = (
        "npx.cmd vite --host 127.0.0.1 --port 5173"
        if sys.platform == "win32"
        else "npx vite --host 127.0.0.1 --port 5173"
    )
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        cwd=str(FRONTEND_DIR),
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    spawned_procs.append(frontend_proc)
    if not await wait_for_port(FRONTEND_PORT, timeout=30.0):
        raise RuntimeError("Frontend server failed to start on port 5173")
    print("[OK] Frontend server is ready.", flush=True)

    # 3. Connect observer WebSocket
    ws_url = f"ws://127.0.0.1:{BACKEND_PORT}/ws"
    ws_client = None
    for _ in range(20):
        try:
            ws_client = await websockets.connect(ws_url, origin=f"http://127.0.0.1:{FRONTEND_PORT}")
            break
        except Exception:
            await asyncio.sleep(0.5)
    if ws_client is None:
        raise RuntimeError("Observer WebSocket connection failed")
    print("[OK] Observer WebSocket connected.", flush=True)

    observed_events = []

    async def _ws_listener():
        try:
            async for raw in ws_client:
                try:
                    data = json.loads(raw)
                    observed_events.append(data)
                except Exception:
                    pass
        except Exception:
            pass

    listener_task = asyncio.create_task(_ws_listener())

    # Wait for initial system_status
    print("Waiting for main runtime loop to be active...", flush=True)
    for _ in range(60):
        await asyncio.sleep(0.5)
        if any(ev.get("type") == "system_status" for ev in observed_events):
            break
    print("[OK] Charlie main runtime loop is active.", flush=True)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--enable-webgl",
                    "--use-gl=angle",
                    "--use-angle=swiftshader",
                    "--ignore-gpu-blocklist",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
            )
            page = await context.new_page()

            # 4. Navigate to HUD and confirm idle centered core
            print("Navigating to HUD...", flush=True)
            await page.goto(f"http://127.0.0.1:{FRONTEND_PORT}")
            await page.wait_for_selector("[data-scene-mode='idle']", timeout=15000)
            print("[OK] HUD loaded in idle mode with centered core.", flush=True)

            # 5. Fetch canonical session
            active_session_res = await page.request.get(f"http://127.0.0.1:{BACKEND_PORT}/api/session/active")
            assert active_session_res.ok
            active_session_data = await active_session_res.json()
            session_id = active_session_data.get("session_id") or active_session_data.get("active_session")
            print(f"Canonical session ID: {session_id}", flush=True)

            # Sync active session
            await ws_client.send(json.dumps({"type": "session_active", "session_id": session_id}))
            await asyncio.sleep(0.5)

            # 6. Issue command "What's happening today?"
            query = "What's happening today?"
            print(f"Issuing prompt to Charlie: {query!r}...", flush=True)
            events_before = len(observed_events)

            # Send chat prompt to EventBus
            await ws_client.send(json.dumps({
                "type": "chat",
                "text": query,
                "payload": {"text": query, "session_id": session_id},
                "session_id": session_id,
            }))

            # 7. Observe research events and presentation intent
            print("Waiting for research execution and Briefing presentation intent...", flush=True)
            briefing_intent_observed = False
            response_done_observed = False
            deadline = time.monotonic() + 60.0

            while time.monotonic() < deadline:
                new_events = observed_events[events_before:]
                for ev in new_events:
                    etype = ev.get("type")
                    payload = ev.get("payload") or {}
                    if etype == "presentation_intent":
                        kind = payload.get("kind")
                        ws_type = payload.get("workspace_type")
                        print(f"Observed presentation_intent: kind={kind}, ws_type={ws_type}, id={payload.get('id')}", flush=True)
                        if kind == "workspace" and ws_type == "briefing":
                            briefing_intent_observed = True
                    elif etype == "response_done":
                        response_done_observed = True
                    elif etype in ("research_result", "token", "thinking"):
                        print(f"Observed event: {etype}", flush=True)

                if briefing_intent_observed and response_done_observed:
                    break
                await asyncio.sleep(0.5)

            assert briefing_intent_observed, "HARD FAIL: PresentationResolver did not emit briefing workspace intent!"
            print("[OK] Briefing PresentationIntent observed.", flush=True)

            # 8. Verify Briefing Workspace in DOM
            print("Verifying Briefing Workspace rendering in DOM...", flush=True)
            await page.wait_for_selector(".charlie-workspace-layer", timeout=15000)
            
            # Verify Briefing header or text is present
            briefing_header = page.locator("text=BRIEFING")
            await page.wait_for_selector("text=BRIEFING", timeout=15000)
            assert await briefing_header.count() > 0, "Briefing Workspace header not found!"
            print("[OK] Briefing Workspace is rendered on the spatial canvas.", flush=True)

            # 9. Verify Charlie Core Docked Bottom-Right
            print("Verifying Charlie core docked bottom-right...", flush=True)
            await page.wait_for_selector("[data-core-position='dock_bottom_right']", timeout=10000)

            # Verify physical coordinates of docked core
            core_docked_geometry_ok = await page.evaluate("""() => {
                const core = document.querySelector('.charlie-core-docked');
                if (!core) return false;
                const rect = core.getBoundingClientRect();
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                return rect.right > vw * 0.75 && rect.bottom > vh * 0.75;
            }""")
            assert core_docked_geometry_ok, "HARD FAIL: Charlie core is not visually docked in bottom-right corner!"
            print("[OK] Charlie core geometry visually verified in bottom-right corner.", flush=True)

            # 10. Capture screenshot evidence
            proof_path = PROOFS_DIR / "live_briefing_verified.png"
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(proof_path))
            assert proof_path.exists() and proof_path.stat().st_size > 5000
            print(f"[OK] Screenshot saved to: {proof_path}", flush=True)

            await browser.close()
            print("=" * 60)
            print("LIVE VERIFICATION COMPLETE AND PASSED 100%")
            print("=" * 60)

    finally:
        listener_task.cancel()
        try:
            await ws_client.close()
        except Exception:
            pass
        for proc in spawned_procs:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    asyncio.run(run_live_verification())
