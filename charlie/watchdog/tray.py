"""
charlie/watchdog/tray.py

System tray icon for Charlie daemon.
Shows status, provides menu for dashboard launch, restart, and exit.
"""

import threading

from charlie.utils.logger import get_logger

logger = get_logger("Tray")


class TrayIcon:
    """
    System tray icon for Charlie daemon.
    Uses pystray (cross-platform) with PIL fallback for icon generation.
    """

    def __init__(self, daemon=None, on_open_dashboard=None, on_restart=None):
        self.daemon = daemon
        self.on_open_dashboard = on_open_dashboard
        self.on_restart = on_restart
        self._icon = None
        self._thread = None

    def start(self):
        """Start tray icon in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="TrayIcon")
        self._thread.start()

    def stop(self):
        """Stop the tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def update_status(self, status: str):
        """Update tray icon tooltip/status."""
        if self._icon:
            try:
                self._icon.title = f"Charlie Daemon — {status}"
            except Exception:
                pass

    def _run(self):
        """Run the tray icon event loop."""
        try:
            import pystray
        except ImportError:
            logger.warning("pystray_not_available | tray_icon_disabled")
            return

        # Create a simple icon image
        image = self._create_icon_image()

        # Build menu
        menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", self._on_open_dashboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                self._on_toggle_autostart,
                checked=lambda item: self._is_autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart", self._on_restart),
            pystray.MenuItem("Exit", self._on_exit),
        )

        self._icon = pystray.Icon(
            name="CharlieDaemon",
            icon=image,
            title="Charlie Daemon — Running",
            menu=menu,
        )

        try:
            self._icon.run()
        except Exception as e:
            logger.error("tray_icon_error", error=str(e))

    def _create_icon_image(self):
        """Create a simple green circle icon."""
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # Green circle
        draw.ellipse([8, 8, 56, 56], fill=(0, 200, 100, 255))
        # Inner highlight
        draw.ellipse([16, 16, 40, 40], fill=(0, 255, 130, 200))
        return image

    def _on_open_dashboard(self, icon, item):
        """Open the dashboard in the default browser."""
        import webbrowser

        if self.on_open_dashboard:
            self.on_open_dashboard()
        else:
            webbrowser.open("http://localhost:3000")

    def _on_toggle_autostart(self, icon, item):
        """Toggle auto-start with Windows."""
        from charlie.utils.autostart import disable, enable, is_enabled

        if is_enabled():
            disable()
            logger.info("autostart_disabled_via_tray")
        else:
            enable()
            logger.info("autostart_enabled_via_tray")

    def _is_autostart_enabled(self) -> bool:
        """Check if auto-start is enabled."""
        try:
            from charlie.utils.autostart import is_enabled

            return is_enabled()
        except Exception:
            return False

    def _on_restart(self, icon, item):
        """Restart the daemon.

        Sets ``reboot_event`` so the supervisor's main monitor thread performs
        the restart (Reqs 14.3, 14.4) instead of invoking ``daemon.reboot``
        directly from this tray thread. Falls back to the legacy direct call if
        the daemon predates the event.
        """
        if self.on_restart:
            self.on_restart()
        elif self.daemon:
            reboot_event = getattr(self.daemon, "reboot_event", None)
            if reboot_event is not None:
                reboot_event.set()
            else:
                self.daemon.reboot()

    def _on_exit(self, icon, item):
        """Exit the daemon.

        Sets ``shutdown_event`` so the supervisor's main monitor thread performs
        the teardown (Reqs 14.3, 14.4) instead of invoking ``daemon.stop``
        directly from this tray thread. Falls back to the legacy direct call if
        the daemon predates the event.
        """
        logger.info("tray_exit_requested")
        if self.daemon:
            shutdown_event = getattr(self.daemon, "shutdown_event", None)
            if shutdown_event is not None:
                shutdown_event.set()
            else:
                self.daemon.stop()
        else:
            import sys

            sys.exit(0)
