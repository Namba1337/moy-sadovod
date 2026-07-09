"""Ненавязчивое уведомление об ознакомительной версии при старте.

В отличие от прежнего блокирующего гейта — приложение всегда доступно.
Диалог только информирует и предлагает купить/ввести ключ; «Продолжить»
просто закрывает окно, работа продолжается в ознакомительном режиме.
"""
from __future__ import annotations

import webbrowser
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.detail_widget import _FramelessDialog
from ui.license_dialog import LANDING_URL


class TrialNoticeDialog(_FramelessDialog):
    """Возвращает Accepted, если пользователь активировал лицензию прямо отсюда."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.activated = False
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Лицензирование")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)

        title = QLabel("Вы используете ознакомительную версию", objectName="tnTitle")
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        title.setFont(f)
        title.setWordWrap(True)
        root.addWidget(title)

        body = QLabel(
            "Вы можете купить лицензию сейчас, либо продолжить пользоваться "
            "программой в ознакомительном режиме.",
            objectName="tnBody",
        )
        body.setWordWrap(True)
        root.addWidget(body)

        self._link_enter_key = QPushButton("Ввести лицензионный ключ", objectName="btnLink")
        self._link_enter_key.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link_enter_key.setFlat(True)
        self._link_enter_key.clicked.connect(self._on_enter_key_clicked)
        root.addWidget(self._link_enter_key, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addSpacing(4)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        bottom_row.addStretch()

        self._btn_buy = QPushButton("Купить лицензию", objectName="btnSecondary")
        self._btn_buy.setFixedHeight(34)
        self._btn_buy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_buy.clicked.connect(self._on_buy_clicked)
        bottom_row.addWidget(self._btn_buy)

        self._btn_continue = QPushButton("Продолжить", objectName="btnPrimary")
        self._btn_continue.setFixedHeight(34)
        self._btn_continue.setMinimumWidth(110)
        self._btn_continue.setDefault(True)
        self._btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_continue.clicked.connect(self.reject)
        bottom_row.addWidget(self._btn_continue)

        root.addLayout(bottom_row)

    def _apply_styles(self) -> None:
        self.setStyleSheet(self._frame_qss() + """
            QLabel { background: transparent; }
            QLabel#tnTitle { color: #1F2937; }
            QLabel#tnBody { color: #6B7280; font-size: 13px; }
            QPushButton#btnLink {
                background: transparent; border: none; color: #07414F;
                font-size: 13px; text-decoration: underline; text-align: left;
                padding: 0px;
            }
            QPushButton#btnLink:hover { color: #0B5A6E; }
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

    def _on_buy_clicked(self) -> None:
        if "REPLACE_WITH_YOUR_LANDING_URL" in LANDING_URL:
            self._btn_buy.setText("Страница покупки не настроена")
            return
        webbrowser.open(LANDING_URL)

    def _on_enter_key_clicked(self) -> None:
        from ui.detail_widget import _exec_dialog
        from ui.license_dialog import LicenseDialog
        dlg = LicenseDialog(self)
        accepted = _exec_dialog(dlg, self) == dlg.DialogCode.Accepted
        if accepted:
            self.activated = True
            self.accept()
