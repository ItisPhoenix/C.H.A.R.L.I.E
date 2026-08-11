"""Lazy QWebEngineView host for one HUD surface."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView


def build_webview(url: str) -> QWebEngineView:
    """Create a QWebEngineView with a transparent page background loading url."""
    view = QWebEngineView()
    view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    view.setStyleSheet("background: transparent;")
    view.page().setBackgroundColor(QColor(Qt.GlobalColor.transparent))
    view.load(QUrl(url))
    return view
