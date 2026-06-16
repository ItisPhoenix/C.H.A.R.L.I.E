"""WebSocket bridge between Charlie and the Electron buddy UI."""
import socketio
import asyncio
import time
import logging

logger = logging.getLogger("charlie.bridge")


class CharlieBridge:
    """Socket.IO server that the Electron buddy UI connects to."""

    def __init__(self, brain=None, port=8765):
        self.sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
        self.port = port
        self.connected_clients = set()
        self.brain = brain
        self.current_screen_context = 'general'

        @self.sio.event
        async def connect(sid, environ):
            self.connected_clients.add(sid)
            logger.info(f"Buddy UI connected: {sid}")

        @self.sio.event
        async def disconnect(sid):
            self.connected_clients.discard(sid)
            logger.info(f"Buddy UI disconnected: {sid}")

        @self.sio.event
        async def screen_context(sid, data):
            context = data.get('context', 'general')
            logger.info(f"User screen context: {context}")
            self.current_screen_context = context

    async def start(self):
        import uvicorn
        from fastapi import FastAPI

        app = FastAPI()
        socket_app = socketio.ASGIApp(self.sio, other_asgi_app=app)

        # Try ports 8765-8775
        for port_offset in range(11):
            port = self.port + port_offset
            try:
                config = uvicorn.Config(
                    socket_app, host="127.0.0.1", port=port, log_level="warning"
                )
                server = uvicorn.Server(config)
                self.port = port
                logger.info(f"Bridge server starting on port {port}")
                await server.serve()
                break
            except OSError as e:
                if "address already in use" in str(e).lower() or e.errno == 10048:
                    logger.warning(f"Port {port} in use, trying next...")
                    continue
                raise

    async def emit_state(self, state, mouth_value=0.0):
        """Emit current Charlie state to all connected buddy UIs."""
        if not self.connected_clients:
            return
        for sid in list(self.connected_clients):
            try:
                await self.sio.emit('charlie_state', {
                    'state': state,
                    'mouth_value': mouth_value,
                    'timestamp': time.time(),
                }, room=sid)
            except Exception as e:
                logger.warning(f"Failed to emit to {sid}: {e}")
    async def emit_text(self, text: str):
        """Emit spoken text to all connected buddy UIs for the speech bubble."""
        if not self.connected_clients:
            return
        for sid in list(self.connected_clients):
            try:
                await self.sio.emit('charlie_text', {
                    'text': text,
                    'timestamp': time.time(),
                }, room=sid)
            except Exception as e:
                logger.warning(f"Failed to emit text to {sid}: {e}")
    async def emit_emotion(self, emotion: str):
        """Emit current Charlie emotional state to all connected buddy UIs."""
        if not self.connected_clients:
            return
        for sid in list(self.connected_clients):
            try:
                await self.sio.emit('charlie_emotion', {
                    'emotion': emotion,
                    'timestamp': time.time(),
                }, room=sid)
            except Exception as e:
                logger.warning(f"Failed to emit emotion to {sid}: {e}")