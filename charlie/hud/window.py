"""SurfaceWindow: frameless, translucent, always-on-top window hosting one QWebEngineView."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from charlie.hud.webview import build_webview

_SPIKE_WIDTH = 360
_SPIKE_HEIGHT = 200


class SurfaceWindow(QWidget):
    """Frameless translucent always-on-top window hosting one HUD surface."""

    def __init__(self, html_path: Path) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(_SPIKE_WIDTH, _SPIKE_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build_webview(html_path))


def main() -> None:
    """Live spike: show one floating translucent web surface for manual verification."""
    app = QApplication([])
    html_path = Path(__file__).parent / "_spike.html"
    window = SurfaceWindow(html_path)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
