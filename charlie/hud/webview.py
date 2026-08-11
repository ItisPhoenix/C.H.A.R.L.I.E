"""Lazy QWebEngineView host for one HUD surface."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView


def build_webview(html_path: Path) -> QWebEngineView:
    """Create a QWebEngineView with a transparent page background loading html_path."""
    view = QWebEngineView()
    view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    view.setStyleSheet("background: transparent;")
    view.page().setBackgroundColor(QColor(Qt.GlobalColor.transparent))
    view.load(QUrl.fromLocalFile(str(html_path)))
    return view
