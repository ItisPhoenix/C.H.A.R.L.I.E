import asyncio
import threading

import httpx

from charlie.web_gateway import RuntimeWebGateway


def test_web_gateway_serves_snapshot_and_routes_authenticated_command(tmp_path):
    """The full documented operator flow: bootstrap, then read, then command.

    Three properties are load-bearing and each one refuses rather than allows:
    the session token is the only thing that mints a cookie, ``/api/scene``
    requires that cookie, and a state-changing POST requires an explicit
    allowlisted ``Origin`` (httpx sends none by default).
    """
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    commands = []

    async def handle(command):
        commands.append(command)
        return {"accepted": True}

    gateway = RuntimeWebGateway(
        loop=loop,
        command_handler=handle,
        snapshot_getter=lambda: {
            "version": 1,
            "revision": 0,
            "title": "Charlie is ready",
            "summary": "Ready",
            "details": [],
        },
        static_dir=tmp_path,
        port=0,
    )
    try:
        gateway.start()
        # Read the port AFTER start(): with port=0 the real port is only known once
        # the socket is bound, which start() does.
        origin = f"http://127.0.0.1:{gateway.port}"

        # An unauthenticated caller must not receive a session.
        with httpx.Client(base_url=origin) as anon:
            assert anon.get("/api/scene").status_code == 401
            assert anon.cookies.get("charlie_web_token") is None

        with httpx.Client(base_url=origin) as client:
            bootstrap = client.get(f"/?token={gateway.token}", follow_redirects=False)
            assert bootstrap.status_code == 303
            assert client.cookies.get("charlie_web_token") == gateway.token

            snapshot = client.get("/api/scene")
            assert snapshot.status_code == 200
            assert snapshot.json()["title"] == "Charlie is ready"

            # A missing Origin is a refusal, not a pass.
            assert client.post("/api/commands", json={"type": "noop"}).status_code == 403

            command = client.post(
                "/api/commands",
                json={"type": "submit_text", "text": "hello"},
                headers={"Origin": origin},
            )
            assert command.status_code == 200
            assert command.json() == {"accepted": True}
        assert commands == [{"type": "submit_text", "text": "hello"}]
    finally:
        gateway.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()
