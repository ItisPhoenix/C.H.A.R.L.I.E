"""Charlie Dashboard — expanded view with real-time data and controls.

Shows conversation transcript with timestamps, latency stats, memory sidebar,
and functional controls (mute, barge-in, clear, save, settings).
"""

import logging
import os
from datetime import datetime
from typing import Optional

try:
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
        QLabel, QPushButton, QCheckBox, QFrame,
        QSplitter, QListWidget, QListWidgetItem,
        QDialog, QDialogButtonBox, QSlider, QMessageBox,
    )
    from PySide6.QtCore import Qt, Slot, QTimer, QSettings
    from PySide6.QtGui import QFont, QColor
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False

if HAS_PYSIDE6:
    _BASE = QWidget
else:
    _BASE = object  # type: ignore[assignment]

logger = logging.getLogger("charlie.dashboard")

# Theme constants
_BG = "#1a1a2e"
_BG_PANEL = "#16213e"
_ACCENT = "#4a6fa5"
_TEXT = "#e0e0e0"
_TEXT_DIM = "#888888"
_BORDER = "#2a2a4a"


# ═══════════════════════════════════════════════════════════════════════════
# Settings Dialog
# ═══════════════════════════════════════════════════════════════════════════

class _SettingsDialog(QDialog):
    """Settings popup with barge-in, sleep timeout, and particle toggle."""

    def __init__(self, parent: Optional[QWidget] = None,
                 barge_in: bool = True, sleep_min: int = 5,
                 show_particles: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Charlie — Settings")
        self.setMinimumWidth(320)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {_BG}; color: {_TEXT}; }}
            QLabel {{ color: {_TEXT}; font-size: 11px; }}
            QSlider::groove:horizontal {{ background: {_BORDER}; height: 4px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {_ACCENT}; width: 14px; margin: -5px 0;
                                          border-radius: 7px; }}
            QCheckBox {{ color: {_TEXT}; spacing: 6px; }}
        """)

        layout = QVBoxLayout(self)

        # Barge-in
        self.chk_barge = QCheckBox("Allow barge-in (interrupt Charlie while speaking)")
        self.chk_barge.setChecked(barge_in)
        layout.addWidget(self.chk_barge)

        # Sleep timeout
        layout.addWidget(QLabel(f"Sleep timeout: {sleep_min} min"))
        self.slider_sleep = QSlider(Qt.Orientation.Horizontal)
        self.slider_sleep.setRange(1, 10)
        self.slider_sleep.setValue(sleep_min)
        self.slider_sleep.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_sleep.setTickInterval(1)
        self.slider_sleep.valueChanged.connect(
            lambda v: layout.itemAt(1).widget().setText(f"Sleep timeout: {v} min")  # type: ignore[union-attr]
        )
        layout.addWidget(self.slider_sleep)

        # Show particles
        self.chk_particles = QCheckBox("Show energy particles")
        self.chk_particles.setChecked(show_particles)
        layout.addWidget(self.chk_particles)

        # OK / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════

class CharlieDashboard(_BASE):
    """Dashboard window with transcript, status, memory, and controls."""

    def __init__(self, bridge: object = None, parent: Optional[object] = None) -> None:
        if not HAS_PYSIDE6:
            return
        super().__init__(parent)  # type: ignore[arg-type]
        self.bridge = bridge
        self.setWindowTitle("Charlie — Dashboard")
        self.setMinimumSize(700, 450)
        self.resize(900, 600)
        self.setStyleSheet(f"""
            QWidget {{ background-color: {_BG}; color: {_TEXT}; font-family: 'Segoe UI', sans-serif; }}
            QTextEdit {{ background-color: {_BG_PANEL}; color: {_TEXT}; border: 1px solid {_BORDER};
                         border-radius: 4px; padding: 8px; selection-background-color: {_ACCENT}; }}
            QLabel {{ color: {_TEXT}; }}
            QFrame {{
                background-color: {_BG_PANEL}; border: 1px solid {_BORDER};
                border-radius: 6px; padding: 8px;
            }}
            QPushButton {{ background-color: {_ACCENT}; color: white; border: none;
                           border-radius: 4px; padding: 6px 14px; font-weight: bold; font-size: 11px; }}
            QPushButton:hover {{ background-color: #5a7fb5; }}
            QPushButton:pressed {{ background-color: #3a5f95; }}
            QCheckBox {{ color: {_TEXT}; spacing: 6px; font-size: 11px; }}
            QListWidget {{
                background-color: {_BG_PANEL}; color: {_TEXT}; border: 1px solid {_BORDER};
                border-radius: 4px; padding: 4px; font-size: 10px;
            }}
            QListWidget::item {{
                background-color: #16213e; border-radius: 6px; padding: 8px;
                margin: 2px 0;
            }}
            QListWidget::item:selected {{ background-color: {_ACCENT}; }}
        """)

        # Restore position/size
        self._settings = QSettings("Charlie", "Dashboard")
        geom = self._settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

        self._build_ui()
        self._connect_bridge()

        # 30-second memory refresh timer
        self._memory_timer = QTimer(self)
        self._memory_timer.timeout.connect(self._refresh_memory)
        self._memory_timer.start(30000)

        # Voice/engine references (set externally)
        self._voice = None
        self._brain = None

    def set_voice(self, voice: object) -> None:
        """Wire voice engine for controls."""
        self._voice = voice

    def set_brain(self, brain: object) -> None:
        """Wire brain for memory queries."""
        self._brain = brain

    def closeEvent(self, event) -> None:  # noqa: N802
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Transcript (60%) ─────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Transcript")
        title.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 13px;")
        left_layout.addWidget(title)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setFont(QFont("Cascadia Mono, Consolas, monospace", 10))
        left_layout.addWidget(self.transcript)

        # Transcript buttons row
        btn_row = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Chat")
        self.btn_clear.setFixedHeight(28)
        self.btn_save = QPushButton("Save Transcript")
        self.btn_save.setFixedHeight(28)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # ── Right: panels (40%) ────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Status panel (card-style)
        status_frame = QFrame()
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 10, 10, 10)
        status_title = QLabel("Status")
        status_title.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 12px;")
        status_layout.addWidget(status_title)

        self.lbl_backend = QLabel("Backend: —")
        self.lbl_asr_to_llm = QLabel("ASR → LLM: —")
        self.lbl_llm_to_tts = QLabel("LLM → TTS: —")
        self.lbl_total_e2e = QLabel("Total E2E: —")
        self.lbl_mode = QLabel("Mode: idle")
        self.lbl_memory = QLabel("Memory: 0 facts")
        self.lbl_screen = QLabel("Screen: —")
        for lbl in (self.lbl_backend, self.lbl_asr_to_llm, self.lbl_llm_to_tts,
                     self.lbl_total_e2e, self.lbl_mode, self.lbl_memory, self.lbl_screen):
            lbl.setStyleSheet(f"color: {_TEXT}; font-size: 11px;")
            status_layout.addWidget(lbl)
        right_layout.addWidget(status_frame)

        # Memory sidebar
        memory_frame = QFrame()
        memory_layout = QVBoxLayout(memory_frame)
        memory_layout.setContentsMargins(10, 10, 10, 10)
        memory_title = QLabel("Memory")
        memory_title.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 12px;")
        memory_layout.addWidget(memory_title)

        self.memory_list = QListWidget()
        self.memory_list.setMaximumHeight(150)
        memory_layout.addWidget(self.memory_list)
        right_layout.addWidget(memory_frame)

        # Controls
        controls_frame = QFrame()
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_title = QLabel("Controls")
        controls_title.setStyleSheet(f"color: {_ACCENT}; font-weight: bold; font-size: 12px;")
        controls_layout.addWidget(controls_title)

        self.chk_barge_in = QCheckBox("Allow barge-in")
        self.chk_barge_in.setChecked(True)
        controls_layout.addWidget(self.chk_barge_in)

        ctrl_row = QHBoxLayout()
        self.btn_mute = QPushButton("Mute Mic")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setFixedHeight(28)
        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setFixedHeight(28)
        ctrl_row.addWidget(self.btn_mute)
        ctrl_row.addWidget(self.btn_settings)
        controls_layout.addLayout(ctrl_row)
        right_layout.addWidget(controls_frame)

        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        # Status bar
        self.status_bar = QLabel("Ready")
        self.status_bar.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px; padding: 2px;")
        root.addWidget(self.status_bar)

        # Wire button signals
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_mute.clicked.connect(self._on_mute)
        self.btn_settings.clicked.connect(self._on_settings)
        self.chk_barge_in.toggled.connect(self._on_barge_toggle)

    # ── Bridge connections ───────────────────────────────────────────────

    def _connect_bridge(self) -> None:
        if not HAS_PYSIDE6 or not self.bridge:
            return
        bridge = self.bridge
        if hasattr(bridge, "transcript_chunk") and hasattr(bridge.transcript_chunk, "connect"):
            bridge.transcript_chunk.connect(self._on_transcript)
        if hasattr(bridge, "latency_updated") and hasattr(bridge.latency_updated, "connect"):
            bridge.latency_updated.connect(self._on_latency)
        if hasattr(bridge, "backend_changed") and hasattr(bridge.backend_changed, "connect"):
            bridge.backend_changed.connect(self._on_backend)
        if hasattr(bridge, "memory_stats") and hasattr(bridge.memory_stats, "connect"):
            bridge.memory_stats.connect(self._on_memory_stats)
        if hasattr(bridge, "state_changed") and hasattr(bridge.state_changed, "connect"):
            bridge.state_changed.connect(self._on_state)
        if hasattr(bridge, "screen_context_changed") and hasattr(bridge.screen_context_changed, "connect"):
            bridge.screen_context_changed.connect(self._on_screen)

    # ── Signal handlers ──────────────────────────────────────────────────

    @Slot(str)
    def _on_transcript(self, text: str) -> None:
        ts = datetime.now().strftime("[%H:%M:%S] ")
        self.transcript.append(f"{ts}{text}")
        sb = self.transcript.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    @Slot(float, float, float)
    def _on_latency(self, asr_to_llm: float, llm_to_tts: float, total: float) -> None:
        def _fmt(ms: float) -> str:
            return f"{ms:.0f}ms" if ms >= 0 else "N/A"
        self.lbl_asr_to_llm.setText(f"ASR → LLM: {_fmt(asr_to_llm)}")
        self.lbl_llm_to_tts.setText(f"LLM → TTS: {_fmt(llm_to_tts)}")
        self.lbl_total_e2e.setText(f"Total E2E: {_fmt(total)}")

    @Slot(str)
    def _on_backend(self, label: str) -> None:
        self.lbl_backend.setText(f"Backend: {label}")

    @Slot(int, int)
    def _on_memory_stats(self, core_facts: int, sessions: int) -> None:
        self.lbl_memory.setText(f"Memory: {core_facts} facts | {sessions} sessions")

    @Slot(str)
    def _on_state(self, state: str) -> None:
        self.lbl_mode.setText(f"Mode: {state}")
        self.status_bar.setText(f"State: {state}")

    @Slot(str)
    def _on_screen(self, title: str) -> None:
        display = title[:50] + "…" if len(title) > 50 else title
        self.lbl_screen.setText(f"Screen: {display}")

    # ── Controls ─────────────────────────────────────────────────────────

    def _on_clear(self) -> None:
        """Clear the transcript."""
        self.transcript.clear()

    def _on_save(self) -> None:
        """Save transcript to logs/transcripts/."""
        transcript_dir = "logs/transcripts"
        os.makedirs(transcript_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        path = os.path.join(transcript_dir, f"{ts}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.transcript.toPlainText())
            QMessageBox.information(self, "Saved", f"Transcript saved to:\n{path}")
        except Exception as e:
            logger.error(f"Failed to save transcript: {e}")
            QMessageBox.warning(self, "Error", f"Failed to save: {e}")

    def _on_mute(self) -> None:
        """Toggle mic mute with confirmation."""
        if not self._voice:
            return
        if self.btn_mute.isChecked():
            result = QMessageBox.question(
                self, "Mute Microphone",
                "Muting will stop listening until unmuted. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if result == QMessageBox.StandardButton.Yes:
                self._voice.stop()
                self.btn_mute.setText("Unmute Mic")
            else:
                self.btn_mute.setChecked(False)
        else:
            self._voice.start()
            self.btn_mute.setText("Mute Mic")

    def _on_barge_toggle(self, checked: bool) -> None:
        """Toggle barge-in on the voice engine."""
        if self._voice and hasattr(self._voice, 'barge_in_enabled'):
            self._voice.barge_in_enabled = checked
            logger.info(f"Barge-in {'enabled' if checked else 'disabled'}")

    def _on_settings(self) -> None:
        """Open settings dialog."""
        barge = self.chk_barge_in.isChecked()
        dialog = _SettingsDialog(self, barge_in=barge)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chk_barge_in.setChecked(dialog.chk_barge.isChecked())
            # Sleep timeout and particles could be applied here

    # ── Memory refresh ───────────────────────────────────────────────────

    def _refresh_memory(self) -> None:
        """Periodically refresh memory facts from brain."""
        if not self._brain:
            return
        try:
            brain = self._brain
            mm = getattr(brain, 'memory_manager', None)
            if mm and hasattr(mm, 'get_core_facts'):
                facts = mm.get_core_facts(limit=5)
                self.memory_list.clear()
                for fact in facts:
                    item = QListWidgetItem(f"• {fact}")
                    item.setForeground(QColor(_TEXT_DIM))
                    self.memory_list.addItem(item)
                if self.bridge and hasattr(self.bridge, 'on_memory_stats'):
                    total = getattr(mm, '_facts_count', len(facts))
                    self.bridge.on_memory_stats(total, 0)
        except Exception as e:
            logger.debug(f"Memory refresh error: {e}")
