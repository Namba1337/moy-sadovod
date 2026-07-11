"""Ненавязчивое уведомление об ознакомительной версии при старте.

В отличие от прежнего блокирующего гейта — приложение всегда доступно.
Диалог только информирует и предлагает купить/ввести ключ; «Продолжить»
просто закрывает окно, работа продолжается в ознакомительном режиме.
"""
from __future__ import annotations

import webbrowser
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui.buttons import LinkButton, PrimaryButton, SecondaryButton
from ui.dialogs import BaseDialog, exec_dialog
from ui.license_dialog import LANDING_URL
from ui.theme import C, FS


class TrialNoticeDialog(BaseDialog):
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

        title = QLabel("Вы используете ознакомительную версию", objectName="dlgTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        body = QLabel(
            "Вы можете купить лицензию сейчас, либо продолжить пользоваться "
            "программой в ознакомительном режиме.",
            objectName="tnBody",
        )
        body.setWordWrap(True)
        root.addWidget(body)

        self._link_enter_key = LinkButton("Ввести лицензионный ключ")
        self._link_enter_key.clicked.connect(self._on_enter_key_clicked)
        root.addWidget(self._link_enter_key, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addSpacing(4)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        bottom_row.addStretch()

        self._btn_buy = SecondaryButton("Купить лицензию")
        self._btn_buy.clicked.connect(self._on_buy_clicked)
        bottom_row.addWidget(self._btn_buy)

        self._btn_continue = PrimaryButton("Продолжить")
        self._btn_continue.setMinimumWidth(110)
        self._btn_continue.setDefault(True)
        self._btn_continue.clicked.connect(self.reject)
        bottom_row.addWidget(self._btn_continue)

        root.addLayout(bottom_row)

    def _apply_styles(self) -> None:
        self.setStyleSheet(self.base_qss() + f"""
            QLabel#tnBody {{ color: {C.TEXT_MUTED}; font-size: {FS.BODY}px; }}
        """)

    def _on_buy_clicked(self) -> None:
        if "REPLACE_WITH_YOUR_LANDING_URL" in LANDING_URL:
            self._btn_buy.setText("Страница покупки не настроена")
            return
        webbrowser.open(LANDING_URL)

    def _on_enter_key_clicked(self) -> None:
        from ui.license_dialog import LicenseDialog
        dlg = LicenseDialog(self)
        accepted = exec_dialog(dlg, self) == dlg.DialogCode.Accepted
        if accepted:
            self.activated = True
            self.accept()
