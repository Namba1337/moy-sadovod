"""Офлайн-проверка подписки по лицензионному токену.

Формат токена (аналог JWT, но с единственным поддерживаемым алгоритмом):

    MSD1.<payload_b64>.<signature_b64>

    payload_b64    — base64url(JSON {"sub": str, "iat": int, "exp": int, "plan": str})
    signature_b64  — base64url(Ed25519(payload_b64.encode()))

Токен выпускается вне приложения (лендинг + оплата через ЮKassa) закрытым
Ed25519-ключом, который никогда не попадает в приложение. Здесь встроен
только публичный ключ — им можно только проверять подпись, подделать
токен нельзя.

Приложение никогда не обращается к серверу само: пользователь получает
токен на лендинге/почтой и вставляет его в LicenseDialog. Проверка —
полностью офлайн (подпись + срок действия).
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.utils import DATA_DIR

TOKEN_PREFIX = "MSD1"

#: Публичный ключ (raw 32 байта, base64url) издателя лицензий.
#: Приватная пара хранится только у разработчика/на лендинге — см. tools/issue_license.py.
#: ЗАМЕНИТЬ на реальный публичный ключ перед первым релизом лицензирования.
PUBLIC_KEY_B64 = "pqvmHrmwIEJrLKVSotxL0DdrJJROQm3IMxUinZ67zrA"

#: Сколько дней после истечения `exp` приложение ещё считает лицензию
#: действительной (терпимость к тому, что пользователь не успел продлить).
GRACE_PERIOD_DAYS = 3

LICENSE_FILE = DATA_DIR / "license.json"


@dataclass(frozen=True)
class LicenseInfo:
    """Результат разбора валидного токена."""
    subject: str          # идентификатор лицензии (email/название СНТ)
    plan: str             # тариф ('standard' и т.п.)
    issued_at: int        # unix timestamp
    expires_at: int       # unix timestamp


@dataclass(frozen=True)
class LicenseStatus:
    """Итог проверки: valid=True — можно работать (в т.ч. в grace-периоде)."""
    valid: bool
    info: Optional[LicenseInfo]
    reason: str            # человекочитаемая причина (для UI/лога)
    in_grace: bool = False  # срок истёк, но ещё в пределах GRACE_PERIOD_DAYS
    days_left: int = 0      # дней до истечения (может быть отрицательным в grace)


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _load_public_key() -> Ed25519PublicKey:
    raw = _b64url_decode(PUBLIC_KEY_B64)
    return Ed25519PublicKey.from_public_bytes(raw)


def parse_and_verify(token: str) -> LicenseStatus:
    """Проверить подпись и срок токена. Не трогает файловую систему."""
    token = (token or "").strip()
    if not token:
        return LicenseStatus(False, None, "Лицензионный ключ не указан.")

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        return LicenseStatus(False, None, "Неверный формат ключа.")

    _, payload_b64, sig_b64 = parts

    try:
        signature = _b64url_decode(sig_b64)
    except Exception:
        return LicenseStatus(False, None, "Неверный формат ключа.")

    try:
        _load_public_key().verify(signature, payload_b64.encode("ascii"))
    except InvalidSignature:
        return LicenseStatus(False, None, "Ключ повреждён или подделан.")
    except Exception:
        return LicenseStatus(False, None, "Не удалось проверить ключ.")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
        info = LicenseInfo(
            subject=str(payload["sub"]),
            plan=str(payload.get("plan", "standard")),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
        )
    except Exception:
        return LicenseStatus(False, None, "Не удалось прочитать данные ключа.")

    now = int(time.time())
    days_left = (info.expires_at - now) // 86400

    if now <= info.expires_at:
        return LicenseStatus(True, info, "Подписка активна.", days_left=days_left)

    grace_end = info.expires_at + GRACE_PERIOD_DAYS * 86400
    if now <= grace_end:
        return LicenseStatus(
            True, info, "Подписка истекла, действует льготный период.",
            in_grace=True, days_left=days_left,
        )

    return LicenseStatus(False, info, "Срок действия подписки истёк.", days_left=days_left)


def load_saved_token() -> Optional[str]:
    if not LICENSE_FILE.exists():
        return None
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
        token = data.get("token")
        return str(token) if token else None
    except Exception:
        return None


def save_token(token: str) -> None:
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(
        json.dumps({"token": token}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def check_saved_license() -> LicenseStatus:
    """Проверить лицензию, сохранённую локально (без ввода пользователем)."""
    token = load_saved_token()
    if not token:
        return LicenseStatus(False, None, "Лицензионный ключ не найден.")
    return parse_and_verify(token)
