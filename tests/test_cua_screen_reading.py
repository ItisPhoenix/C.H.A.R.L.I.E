"""Supporting OCR ownership checks, not live-host acceptance."""

from types import SimpleNamespace

from charlie.computer.backend import DesktopBackend


def test_screen_reading_uses_only_cua_capture(monkeypatch):
    from charlie.desktop import ocr

    backend = DesktopBackend()
    image = b"authorized-window-image"
    monkeypatch.setattr(backend, "capture_window", lambda: (
        '[0] Edit "Document"', "data:image/png;base64,YXV0aG9yaXplZC13aW5kb3ctaW1hZ2U=",
    ))
    monkeypatch.setattr(ocr.config, "desktop_ocr_enabled", True)
    monkeypatch.setattr(ocr, "OCR_AVAILABLE", True)
    monkeypatch.setattr(ocr, "capture", lambda: (_ for _ in ()).throw(AssertionError("Unbounded capture")))

    def recognize(pixels):
        assert pixels == image
        return [SimpleNamespace(name="Visible document text")]

    monkeypatch.setattr(ocr, "ocr_marks", recognize)
    result = backend.read_screen()
    assert "Visible document text" in result
    assert "[0]" in result


def test_refused_capture_never_reaches_ocr(monkeypatch):
    from charlie.desktop import ocr

    backend = DesktopBackend()
    monkeypatch.setattr(backend, "capture_window", lambda: ("Refused: permission denied", None))
    monkeypatch.setattr(ocr, "ocr_marks", lambda pixels: (_ for _ in ()).throw(AssertionError("Refusal bypass")))
    assert backend.read_screen() == "Refused: permission denied"


def test_accessibility_text_survives_unavailable_ocr(monkeypatch):
    from charlie.desktop import ocr

    backend = DesktopBackend()
    monkeypatch.setattr(backend, "capture_window", lambda: ('[0] Text "Status"', "image"))
    monkeypatch.setattr(ocr.config, "desktop_ocr_enabled", False)
    monkeypatch.setattr(ocr, "OCR_AVAILABLE", True)

    result = backend.read_screen()

    assert '[0] Text "Status"' in result
    assert "OCR is unavailable" in result
