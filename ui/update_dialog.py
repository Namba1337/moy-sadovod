"""Диалог уведомления о доступном обновлении.

Стиль выдержан в духе остального приложения: скруглённые карточки,
зелёная primary-кнопка, серая secondary-кнопка. Release notes рендерим
как обычный текст (markdown→plain) с поддержкой переносов.
"""
from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QTextBrowser,
    QVBoxLayout, QWidget,
)

from core.updater import (
    APP_VERSION, ReleaseInfo, UpdateDownloader, human_size, run_installer,
)
from ui.buttons import PrimaryButton, SecondaryButton
from ui.dialogs import BaseDialog
from ui.theme import C, FS


def _markdown_to_plain(text: str) -> str:
    """Очень лёгкий markdown→plain для release notes.

    Не пытаемся быть идеальными — задача в том, чтобы убрать визуальный шум
    `**`, `##`, ссылок-в-скобках и т.п., сохранив структуру списков.
    """
    if not text:
        return ""
    out = text
    # ## Заголовок → Заголовок (bold-эффект даст QSS)
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.MULTILINE)
    # **жирный** → жирный
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", out)
    out = re.sub(r"__([^_]+)__", r"\1", out)
    # *курсив* → курсив
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", out)
    # `code` → code
    out = re.sub(r"`([^`]+)`", r"\1", out)
    # [текст](url) → текст
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    # маркеры списков `- ` → `• `
    out = re.sub(r"^[-*]\s+", "• ", out, flags=re.MULTILINE)
    return out.strip()


class UpdateDialog(BaseDialog):
    """Окно «Доступно обновление».

    Состояния:
      1) idle      — показываем notes + кнопки «Обновить» / «Позже»
      2) loading   — прогресс-бар + «Отмена»
      3) ready     — короткое сообщение «Сейчас приложение закроется и запустится установщик»
      4) error     — текст ошибки + «Закрыть»
    """

    def __init__(self, info: ReleaseInfo, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self._info = info
        self._downloader = UpdateDownloader(self)
        self._downloader.downloadProgress.connect(self._on_progress)
        self._downloader.downloadFinished.connect(self._on_downloaded)
        self._downloader.errorOccurred.connect(self._on_error)
        self._setup_ui()
        self._apply_styles()

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setWindowTitle("Доступно обновление")
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        # Заголовок
        root.addLayout(self.make_header(
            f"Доступна новая версия {self._info.version}"))

        subtitle = QLabel(
            f"У вас установлена версия {APP_VERSION}.   "
            f"Размер обновления: {human_size(self._info.size_bytes)}",
            objectName="updateSubtitle",
        )
        root.addWidget(subtitle)

        # Что нового
        notes_label = QLabel("Что нового:", objectName="updateNotesLabel")
        root.addWidget(notes_label)

        self._notes_view = QTextBrowser(objectName="updateNotes")
        self._notes_view.setOpenExternalLinks(True)
        plain = _markdown_to_plain(self._info.notes) or "Описание изменений не указано."
        self._notes_view.setPlainText(plain)
        root.addWidget(self._notes_view, stretch=1)

        # Статус (используется во всех состояниях)
        self._status = QLabel("", objectName="updateStatus")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        root.addWidget(self._status)

        # Прогресс-бар (скрыт изначально)
        self._progress = QProgressBar(objectName="updateProgress")
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        # Предупреждение про целостность
        if not self._info.sha256_expected:
            warn = QLabel(
                "Для этой версии не опубликован .sha256-файл — "
                "проверка целостности будет пропущена.",
                objectName="updateWarn",
            )
            warn.setWordWrap(True)
            root.addWidget(warn)

        # Кнопки
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self._btn_later = SecondaryButton("Позже")
        self._btn_later.setMinimumWidth(96)
        self._btn_later.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_later)

        self._btn_primary = PrimaryButton("Обновить")
        self._btn_primary.setMinimumWidth(120)
        self._btn_primary.setDefault(True)
        self._btn_primary.clicked.connect(self._on_update_clicked)
        btn_row.addWidget(self._btn_primary)

        root.addLayout(btn_row)

    def _apply_styles(self) -> None:
        self.setStyleSheet(self.base_qss() + f"""
            QLabel#updateSubtitle {{ color: {C.TEXT_MUTED}; font-size: {FS.SMALL}px; }}
            QLabel#updateNotesLabel {{ color: {C.TEXT}; font-size: {FS.BODY}px; font-weight: 600; }}
            QLabel#updateStatus {{ color: {C.TEXT_BODY}; font-size: {FS.BODY}px; }}
            QLabel#updateWarn {{ color: {C.WARNING}; font-size: {FS.SMALL}px; }}
            QTextBrowser#updateNotes {{
                background: {C.BG_SUBTLE}; border: 1px solid {C.BORDER_LIGHT};
                border-radius: 8px; color: {C.TEXT}; padding: 10px;
            }}
            QProgressBar#updateProgress {{
                background: {C.BG_SURFACE}; border: 1px solid {C.BORDER};
                border-radius: 6px; color: {C.TEXT}; text-align: center; height: 20px;
            }}
            QProgressBar#updateProgress::chunk {{
                background: {C.BRAND}; border-radius: 5px;
            }}
        """)

    # ── Состояния ─────────────────────────────────────────────────────

    def _on_update_clicked(self) -> None:
        # Переходим в состояние «loading».
        self._btn_primary.setEnabled(False)
        self._btn_primary.setText("Загрузка…")
        self._btn_later.setText("Отмена")
        try:
            self._btn_later.clicked.disconnect()
        except TypeError:
            pass
        self._btn_later.clicked.connect(self._cancel_download)

        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status.setVisible(True)
        self._status.setText("Скачивание установщика…")

        self._downloader.start(self._info)

    def _cancel_download(self) -> None:
        self._downloader.cancel()
        self.reject()

    def closeEvent(self, event) -> None:
        # На случай закрытия окном (X/Escape) в обход кнопки «Отмена» —
        # без явной остановки живой QThread-загрузки Qt крашится при
        # уничтожении диалога (см. core.updater.UpdateDownloader.stop).
        self._downloader.stop()
        super().closeEvent(event)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = int(done * 100 / total)
            self._progress.setValue(pct)
            self._progress.setFormat(
                f"{human_size(done)} / {human_size(total)}  ({pct}%)"
            )
        else:
            # Размер неизвестен — индикатор без процентов.
            self._progress.setRange(0, 0)
            self._progress.setFormat(human_size(done))

    def _on_downloaded(self, path: str) -> None:
        self._status.setText(
            "Установщик загружен. Приложение сейчас закроется, "
            "и установка продолжится автоматически."
        )
        self._progress.setRange(0, 100)
        self._progress.setValue(100)

        ok = run_installer(path)
        if not ok:
            self._on_error("Не удалось запустить установщик.")
            return

        # Закрываем приложение, чтобы Inno Setup мог заменить файлы.
        self.accept()
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()

    def _on_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._status.setVisible(True)
        self._status.setText(f"Ошибка: {message}")
        self._status.setStyleSheet(
            f"color:{C.DANGER_HOVER};font-size:{FS.BODY}px;background:transparent;")

        self._btn_primary.setEnabled(True)
        self._btn_primary.setText("Повторить")
        try:
            self._btn_primary.clicked.disconnect()
        except TypeError:
            pass
        self._btn_primary.clicked.connect(self._on_update_clicked)

        self._btn_later.setText("Закрыть")
        try:
            self._btn_later.clicked.disconnect()
        except TypeError:
            pass
        self._btn_later.clicked.connect(self.reject)
