"""Диалог «История обновлений» — список релизов с GitHub Releases."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.updater import APP_VERSION, ReleaseHistoryEntry, ReleaseHistoryFetcher
from ui.detail_widget import _FramelessDialog
from ui.update_dialog import _markdown_to_plain


class _ReleaseRow(QFrame):
    """Одна карточка релиза: версия + дата + release notes."""

    def __init__(self, entry: ReleaseHistoryEntry, is_current: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("releaseRow")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        ver = QLabel(f"v{entry.version}", objectName="releaseVersion")
        head.addWidget(ver)
        if is_current:
            cur = QLabel("текущая", objectName="releaseCurrentPill")
            head.addWidget(cur)
        head.addStretch()
        if entry.published:
            head.addWidget(QLabel(entry.published, objectName="releaseDate"))
        lay.addLayout(head)

        plain = _markdown_to_plain(entry.notes) or "Описание изменений не указано."
        notes = QLabel(plain, objectName="releaseNotes")
        notes.setWordWrap(True)
        lay.addWidget(notes)


class UpdateHistoryDialog(_FramelessDialog):
    """Окно со списком релизов приложения (GitHub Releases)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История обновлений")
        self.setModal(True)
        self.setMinimumSize(560, 620)

        self._fetcher = ReleaseHistoryFetcher(self)
        self._fetcher.historyReady.connect(self._on_history)
        self._fetcher.errorOccurred.connect(self._on_error)

        self._setup_ui()
        self.setStyleSheet(self._frame_qss() + """
            QLabel { background: transparent; color: #374151; }
            QLabel#panelTitle { color: #111827; font-size: 15px; font-weight: 700; }
            QPushButton#btnPanelClose {
                background: transparent; border: none; color: #9CA3AF;
                font-size: 15px; font-weight: 600; border-radius: 12px;
            }
            QPushButton#btnPanelClose:hover { background: #F3F4F6; color: #374151; }
            QLabel#historyStatus { color: #9CA3AF; font-size: 12px; }
            QScrollArea { background: transparent; border: none; }
            QWidget#historyContents { background: transparent; }
            QFrame#releaseRow {
                background: #F8F9FA; border: 1px solid #E5E7EB; border-radius: 8px;
            }
            QLabel#releaseVersion { color: #07414F; font-size: 13px; font-weight: 700; }
            QLabel#releaseDate { color: #9CA3AF; font-size: 11px; }
            QLabel#releaseNotes { color: #374151; font-size: 12px; }
            QLabel#releaseCurrentPill {
                background: rgba(7,65,79,0.1); color: #07414F;
                border: 1px solid rgba(7,65,79,0.35); border-radius: 8px;
                padding: 1px 8px; font-size: 11px; font-weight: 600;
            }
        """)
        self._fetcher.fetch()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("История обновлений", objectName="panelTitle")
        header.addWidget(title)
        header.addStretch()
        btn_close = QPushButton("✕", objectName="btnPanelClose")
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_close.clicked.connect(self.reject)
        header.addWidget(btn_close)
        layout.addLayout(header)

        self._status_lbl = QLabel("Загрузка списка релизов…", objectName="historyStatus")
        layout.addWidget(self._status_lbl)

        self._contents = QWidget(objectName="historyContents")
        self._contents_lay = QVBoxLayout(self._contents)
        self._contents_lay.setContentsMargins(0, 0, 4, 0)
        self._contents_lay.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidget(self._contents)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollBar:vertical { width: 6px; background: transparent; border: none; }"
            "QScrollBar::handle:vertical { background: #C9D8E2; border-radius: 3px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        layout.addWidget(scroll, stretch=1)

    def _clear_contents(self):
        while self._contents_lay.count():
            item = self._contents_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_history(self, entries: list[ReleaseHistoryEntry]):
        self._clear_contents()
        if not entries:
            self._status_lbl.setText("Релизы не найдены.")
            return
        self._status_lbl.setText(f"Установлена версия v{APP_VERSION}")
        for entry in entries:
            row = _ReleaseRow(entry, entry.version == APP_VERSION, self._contents)
            self._contents_lay.addWidget(row)
        self._contents_lay.addStretch()

    def _on_error(self, message: str):
        self._clear_contents()
        self._status_lbl.setText(f"Не удалось загрузить историю обновлений: {message}")
