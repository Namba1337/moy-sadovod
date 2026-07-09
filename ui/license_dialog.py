"""Диалог активации/проверки подписки.

Открывается по запросу пользователя (бейдж/кнопка «Купить» в шапке,
ссылка «Ввести ключ» в TrialNoticeDialog) — приложение само по себе
никогда не блокируется отсутствием лицензии. Пользователь вставляет
ключ, полученный на лендинге после оплаты через ЮKassa; проверка
полностью офлайн (core.license.parse_and_verify).
"""
from __future__ import annotations

import webbrowser
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from core.license import LicenseStatus, check_saved_license, parse_and_verify, save_token
from ui.detail_widget import _FramelessDialog

#: Страница покупки/продления подписки. Настроить перед релизом лицензирования.
LANDING_URL = "https://REPLACE_WITH_YOUR_LANDING_URL"


def _format_status_text(status: LicenseStatus) -> str:
    if status.info is None:
        return status.reason
    until = datetime.fromtimestamp(status.info.expires_at).strftime("%d.%m.%Y")
    if status.valid and not status.in_grace:
        return f"Подписка активна до {until}."
    if status.in_grace:
        return (f"Подписка истекла {until}. Действует льготный период "
                f"— продлите в ближайшее время.")
    return f"Подписка истекла {until}."


class LicenseDialog(_FramelessDialog):
    """Возвращает Accepted, если после диалога есть действующая лицензия."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self._status: LicenseStatus = check_saved_license()
        self._setup_ui()
        self._apply_styles()
        self._refresh_status_label()

    # ── UI ────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setWindowTitle("Активация подписки")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        title = QLabel("Активация подписки", objectName="licTitle")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        self._status_label = QLabel("", objectName="licStatus")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        hint = QLabel(
            "Вставьте лицензионный ключ, полученный после оплаты:",
            objectName="licHint",
        )
        root.addWidget(hint)

        self._input = QPlainTextEdit(objectName="licInput")
        self._input.setPlaceholderText("MSD1....")
        self._input.setFixedHeight(90)
        root.addWidget(self._input)

        self._error_label = QLabel("", objectName="licError")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        root.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_buy = QPushButton("Купить / продлить", objectName="btnSecondary")
        self._btn_buy.setFixedHeight(34)
        self._btn_buy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_buy.clicked.connect(self._on_buy_clicked)
        btn_row.addWidget(self._btn_buy)

        btn_row.addStretch()

        self._btn_cancel = QPushButton("Отмена", objectName="btnSecondary")
        self._btn_cancel.setFixedHeight(34)
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)

        self._btn_activate = QPushButton("Активировать", objectName="btnPrimary")
        self._btn_activate.setFixedHeight(34)
        self._btn_activate.setMinimumWidth(120)
        self._btn_activate.setDefault(True)
        self._btn_activate.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_activate.clicked.connect(self._on_activate_clicked)
        btn_row.addWidget(self._btn_activate)

        root.addLayout(btn_row)

    def _apply_styles(self) -> None:
        self.setStyleSheet(self._frame_qss() + """
            QLabel { background: transparent; }
            QLabel#licTitle { color: #1F2937; }
            QLabel#licStatus { color: #3C4654; font-size: 13px; }
            QLabel#licHint { color: #6B7280; font-size: 12px; }
            QLabel#licError { color: #B91C1C; font-size: 12px; }
            QPlainTextEdit#licInput {
                background: #F8F9FA; border: 1px solid #E5E7EB; border-radius: 8px;
                color: #1F2937; padding: 8px; font-family: Consolas, monospace;
                font-size: 12px;
            }
            QPushButton#btnPrimary {
                background: #07414F; color: #FFFFFF; border: none; border-radius: 6px;
                padding: 6px 18px; font-size: 13px; font-weight: 600;
            }
            QPushButton#btnPrimary:hover   { background: #0B5A6E; }
            QPushButton#btnPrimary:pressed { background: #062F38; }
            QPushButton#btnSecondary {
                background: #E5E7EB; color: #6B7280;
                border: 1px solid #D1D5DB; border-radius: 6px;
                padding: 6px 14px; font-size: 13px;
            }
            QPushButton#btnSecondary:hover { background: #E5E7EB; color: #374151; }
        """)

    # ── Поведение ─────────────────────────────────────────────────────

    def _refresh_status_label(self) -> None:
        self._status_label.setText(_format_status_text(self._status))

    def _on_buy_clicked(self) -> None:
        if "REPLACE_WITH_YOUR_LANDING_URL" in LANDING_URL:
            self._error_label.setText(
                "Страница покупки ещё не настроена. Обратитесь к разработчику."
            )
            self._error_label.setVisible(True)
            return
        webbrowser.open(LANDING_URL)

    def _on_activate_clicked(self) -> None:
        token = self._input.toPlainText().strip()
        status = parse_and_verify(token)
        if not status.valid:
            self._error_label.setText(status.reason)
            self._error_label.setVisible(True)
            return

        save_token(token)
        self._status = status
        self.accept()
