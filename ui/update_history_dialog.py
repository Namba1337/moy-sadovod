"""Диалог «История обновлений» — список релизов с GitHub Releases."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from core.updater import APP_VERSION, ReleaseHistoryEntry, ReleaseHistoryFetcher
from ui.dialogs import BaseDialog
from ui.theme import C, FS
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


class UpdateHistoryDialog(BaseDialog):
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
        self.setStyleSheet(self.base_qss() + f"""
            QLabel#historyStatus {{ color: {C.TEXT_FAINT}; font-size: {FS.SMALL}px; }}
            QWidget#historyContents {{ background: transparent; }}
            QFrame#releaseRow {{
                background: {C.BG_SUBTLE}; border: 1px solid {C.BORDER_LIGHT};
                border-radius: 8px;
            }}
            QLabel#releaseVersion {{ color: {C.BRAND}; font-size: {FS.BODY}px; font-weight: 700; }}
            QLabel#releaseDate {{ color: {C.TEXT_FAINT}; font-size: {FS.CAPTION}px; }}
            QLabel#releaseNotes {{ color: {C.TEXT_BODY}; font-size: {FS.SMALL}px; }}
            QLabel#releaseCurrentPill {{
                background: rgba(7,65,79,0.1); color: {C.BRAND};
                border: 1px solid rgba(7,65,79,0.35); border-radius: 8px;
                padding: 1px 8px; font-size: {FS.CAPTION}px; font-weight: 600;
            }}
        """)
        self._fetcher.fetch()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addLayout(self.make_header("История обновлений", closable=True))

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

    def closeEvent(self, event) -> None:
        # Без явной остановки живой QThread-загрузки Qt крашится при
        # уничтожении диалога (см. core.updater.ReleaseHistoryFetcher.stop).
        self._fetcher.stop()
        super().closeEvent(event)
