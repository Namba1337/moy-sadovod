"""Dev-инструмент выпуска лицензионных ключей (подписка).

Не входит в сборку приложения (PyInstaller) — запускается только
разработчиком локально или на бэкенде лендинга.

Использование:

    # один раз — сгенерировать пару ключей
    python tools/issue_license.py keygen

    Публичный ключ, который выведет команда, нужно скопировать в
    core/license.py → PUBLIC_KEY_B64. Приватный ключ сохраняется в
    tools/keys/license_private_key.pem — храните его вне репозитория
    (в .gitignore уже добавлено tools/keys/), это единственный секрет,
    которым подписываются лицензии.

    # выпустить ключ на 30 дней
    python tools/issue_license.py issue --sub "ivanov@example.com" --days 30

    # выпустить ключ до конкретной даты
    python tools/issue_license.py issue --sub "СНТ Ромашка" --until 2026-12-31

    # посмотреть все ранее выпущенные ключи
    python tools/issue_license.py list

Каждый выпущенный ключ добавляется строкой в tools/keys/issued_licenses.csv
(тоже вне репозитория, см. .gitignore) — простой локальный реестр: кому,
когда и до какой даты выпущена лицензия.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

TOKEN_PREFIX = "MSD1"
KEY_DIR = Path(__file__).parent / "keys"
PRIVATE_KEY_PATH = KEY_DIR / "license_private_key.pem"
LOG_PATH = KEY_DIR / "issued_licenses.csv"
LOG_FIELDS = ["issued_at", "sub", "plan", "expires_at", "token"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def cmd_keygen(_args: argparse.Namespace) -> None:
    if PRIVATE_KEY_PATH.exists():
        print(f"Приватный ключ уже существует: {PRIVATE_KEY_PATH}")
        print("Удалите его вручную, если действительно хотите сгенерировать новый "
              "(это сделает недействительными все ранее выпущенные лицензии).")
        sys.exit(1)

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    PRIVATE_KEY_PATH.write_bytes(pem)

    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    print(f"Приватный ключ сохранён: {PRIVATE_KEY_PATH}")
    print("\nВставьте это значение в core/license.py -> PUBLIC_KEY_B64:\n")
    print(_b64url(public_raw))


def _load_private_key() -> Ed25519PrivateKey:
    if not PRIVATE_KEY_PATH.exists():
        print(f"Не найден приватный ключ: {PRIVATE_KEY_PATH}\n"
              f"Сначала выполните: python tools/issue_license.py keygen")
        sys.exit(1)
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)


def _append_log(*, sub: str, plan: str, iat: int, exp: int, token: str) -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "issued_at": datetime.fromtimestamp(iat, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "sub": sub,
            "plan": plan,
            "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d"),
            "token": token,
        })


def cmd_list(_args: argparse.Namespace) -> None:
    if not LOG_PATH.exists():
        print("Ключи ещё не выпускались.")
        return

    with open(LOG_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("Ключи ещё не выпускались.")
        return

    now = datetime.now(tz=timezone.utc)
    print(f"{'Выпущен':<17} {'Кому':<28} {'Тариф':<10} {'До':<12} {'Статус'}")
    for row in rows:
        try:
            exp_dt = datetime.strptime(row["expires_at"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_left = (exp_dt - now).days
            status = f"истёк {-days_left} дн. назад" if days_left < 0 else f"{days_left} дн. осталось"
        except Exception:
            status = "?"
        print(f"{row['issued_at']:<17} {row['sub']:<28} {row['plan']:<10} {row['expires_at']:<12} {status}")


def cmd_issue(args: argparse.Namespace) -> None:
    now = int(time.time())
    if args.until:
        exp_dt = datetime.strptime(args.until, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        exp = int(exp_dt.timestamp())
    else:
        exp = now + args.days * 86400

    payload = {"sub": args.sub, "iat": now, "exp": exp, "plan": args.plan}
    payload_b64 = _b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    private_key = _load_private_key()
    signature = private_key.sign(payload_b64.encode("ascii"))
    token = f"{TOKEN_PREFIX}.{payload_b64}.{_b64url(signature)}"

    print(f"Лицензия для: {args.sub}")
    print(f"Тариф: {args.plan}")
    print(f"Действует до: {datetime.fromtimestamp(exp, tz=timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("\nКлюч:\n")
    print(token)

    _append_log(sub=args.sub, plan=args.plan, iat=now, exp=exp, token=token)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("keygen", help="Сгенерировать пару ключей (один раз)")

    p_issue = sub.add_parser("issue", help="Выпустить лицензионный ключ")
    p_issue.add_argument("--sub", required=True, help="Идентификатор лицензии (email / название СНТ)")
    p_issue.add_argument("--days", type=int, default=30, help="Срок действия в днях от текущего момента (по умолчанию 30)")
    p_issue.add_argument("--until", help="Дата окончания YYYY-MM-DD (перекрывает --days)")
    p_issue.add_argument("--plan", default="standard", help="Название тарифа (по умолчанию standard)")

    sub.add_parser("list", help="Показать все ранее выпущенные ключи")

    args = parser.parse_args()
    if args.command == "keygen":
        cmd_keygen(args)
    elif args.command == "issue":
        cmd_issue(args)
    elif args.command == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
