"""SurfaceWindow: frameless, translucent, always-on-top window hosting one QWebEngineView."""
from typing import Optional, Tuple

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from charlie.hud.webview import build_webview

_DRAG_STRIP_HEIGHT = 8


class SurfaceWindow(QWidget):
    """Frameless translucent always-on-top window hosting one HUD surface.

    QWebEngineView captures mouse events itself, so a thin undecorated top strip
    (left out of the webview via layout margins) is the drag handle. No setMask()
    rounded-corner clip: on Windows, QRegion masking a WA_TranslucentBackground
    widget drops alpha-blended pixels, leaving only fully-opaque content visible
    (confirmed live -- the card background disappeared, only text remained). CSS
    border-radius already rounds the visible card; a real click-through cutout on
    the corners is future work if it's ever needed.
    """

    def __init__(
        self, url: str, rect: Tuple[int, int, int, int], draggable: bool = True, click_through: bool = False
    ) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if click_through:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._draggable = draggable
        self._drag_origin: Optional[QPoint] = None

        x, y, w, h = rect
        self.setGeometry(x, y, w, h)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, _DRAG_STRIP_HEIGHT if draggable else 0, 0, 0)
        layout.addWidget(build_webview(url))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._draggable and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._draggable and self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
