"""Система облачных обновлений через GitHub Releases.

Архитектура:
    1. UpdateChecker.check() — фоновой поток, ходит в GitHub API,
       парсит latest-release, сравнивает версии, отдаёт ReleaseInfo
       сигналом updateAvailable (или noUpdate / errorOccurred).
    2. UpdateDownloader.start() — фоновой поток, качает установщик
       с прогрессом (downloadProgress), проверяет SHA-256, отдаёт путь
       к локальному .exe сигналом downloadFinished.
    3. run_installer() — запускает Inno Setup в тихом режиме
       (/SILENT /NORESTART /CLOSEAPPLICATIONS) и просит приложение
       завершиться.

Версия приложения — единственная точка истины: APP_VERSION ниже.
build.bat читает её и подставляет в installer.iss.

Перед использованием задайте GITHUB_OWNER и GITHUB_REPO под свой репозиторий.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ──────────────────────────────────────────────────────────────────────────
#  Конфигурация
# ──────────────────────────────────────────────────────────────────────────

#: Текущая версия приложения. ЕДИНСТВЕННАЯ ТОЧКА ИСТИНЫ.
#: При релизе: поднять здесь, прогнать build.bat — он сам обновит installer.iss.
APP_VERSION = "0.1.0"

#: GitHub-репозиторий, где публикуются релизы.
#: ВАЖНО: замените на свои реальные значения перед первым релизом.
GITHUB_OWNER = "Namba1337"
GITHUB_REPO = "snt_helper_app"

#: Personal Access Token для приватного репозитория.
#: Создайте fine-grained PAT с правом Contents: Read-only.
#: Можно задать через переменную окружения GITHUB_TOKEN или вписать напрямую.
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "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")

#: Шаблон имени основного установщика в release assets.
#: Должен соответствовать OutputBaseFilename из installer.iss.
INSTALLER_NAME_PATTERN = re.compile(r"MoySadovod_Setup_v[\d.]+\.exe$", re.IGNORECASE)

#: Таймаут сетевых запросов (секунды).
NETWORK_TIMEOUT = 15

#: User-Agent для GitHub API (требуется по их правилам).
USER_AGENT = f"MoySadovod/{APP_VERSION} (+https://github.com/{GITHUB_OWNER}/{GITHUB_REPO})"


# ──────────────────────────────────────────────────────────────────────────
#  Сравнение версий (semver-подобное, без зависимости от packaging)
# ──────────────────────────────────────────────────────────────────────────

def _parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' / '1.2' → (1, 2, 3) / (1, 2, 0).

    Хвост вида '-beta.1' игнорируется — релиз-кандидаты считаются равными релизу
    (для нашего использования это безопасно: канал у нас один).
    """
    v = v.strip().lstrip("vV")
    v = re.split(r"[-+]", v, maxsplit=1)[0]  # отрезать '-beta', '+build'
    parts: list[int] = []
    for chunk in v.split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group()) if m else 0)
    # нормализуем длину до 3 (major.minor.patch)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    """True, если remote-версия строго новее local."""
    try:
        return _parse_version(remote) > _parse_version(local)
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
#  Описание релиза
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReleaseInfo:
    """Информация о найденном на сервере релизе."""
    version: str                     # 'v1.2.3' (тег как есть)
    notes: str                       # release notes (markdown как есть)
    download_url: str                # ссылка на .exe установщика
    asset_name: str                  # имя файла .exe
    size_bytes: int                  # размер установщика
    sha256_url: Optional[str]        # ссылка на .sha256 (если есть)
    sha256_expected: Optional[str]   # хеш, если получится скачать .sha256


# ──────────────────────────────────────────────────────────────────────────
#  Сетевые помощники
# ──────────────────────────────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext:
    """Системный SSL-контекст. На Windows PyInstaller иногда не видит
    certifi — используем default_context() с системными корнями.
    """
    return ssl.create_default_context()


def _auth_headers() -> dict:
    h: dict = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _http_get(url: str, *, timeout: int = NETWORK_TIMEOUT) -> bytes:
    """GET через urllib (stdlib) с User-Agent и опциональным токеном."""
    req = urllib.request.Request(url, headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read()


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """При редиректе с github.com на сторонний домен (S3) убираем Authorization.

    GitHub asset download: github.com → 302 → S3 pre-signed URL.
    Если отправить Authorization на S3 — он вернёт 400 SignatureDoesNotMatch.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        from urllib.parse import urlparse
        if urlparse(newurl).netloc not in ("github.com", "api.github.com"):
            new_req.remove_header("Authorization")
            new_req.remove_header("authorization")
        return new_req


# ──────────────────────────────────────────────────────────────────────────
#  Проверка наличия обновлений
# ──────────────────────────────────────────────────────────────────────────

class _CheckWorker(QThread):
    finished_ok = pyqtSignal(object)   # ReleaseInfo | None (None = нет обновлений)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            api_url = (
                f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
            )
            raw = _http_get(api_url)
            data = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Релизов ещё нет — это не ошибка, просто нет обновлений.
                self.finished_ok.emit(None)
                return
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
            return
        except Exception as e:
            self.failed.emit(str(e))
            return

        tag = (data.get("tag_name") or data.get("name") or "").strip()
        if not tag:
            self.failed.emit("В ответе GitHub нет tag_name")
            return

        if not is_newer(tag, APP_VERSION):
            self.finished_ok.emit(None)
            return

        # Ищем нужный asset — установщик Inno Setup.
        installer_asset = None
        sha256_asset = None
        for asset in data.get("assets", []) or []:
            name = asset.get("name", "")
            if INSTALLER_NAME_PATTERN.search(name):
                installer_asset = asset
            elif name.lower().endswith(".sha256"):
                sha256_asset = asset

        if not installer_asset:
            self.failed.emit(
                f"В релизе {tag} не найден установщик "
                f"(ожидалось имя вида MoySadovod_Setup_vX.Y.Z.exe)"
            )
            return

        # Подтянем .sha256 (если есть) — он маленький, можно прямо тут.
        sha256_expected: Optional[str] = None
        sha256_url: Optional[str] = None
        if sha256_asset:
            sha256_url = sha256_asset.get("browser_download_url")
            try:
                sha_raw = _http_get(sha256_url).decode("utf-8", errors="replace")
                # Формат файла: "<hex>  <filename>" или просто "<hex>"
                first_token = sha_raw.strip().split()[0] if sha_raw.strip() else ""
                if re.fullmatch(r"[0-9a-fA-F]{64}", first_token):
                    sha256_expected = first_token.lower()
            except Exception:
                # Не критично — продолжим без проверки хеша, но предупредим.
                pass

        self.finished_ok.emit(ReleaseInfo(
            version=tag,
            notes=(data.get("body") or "").strip(),
            download_url=installer_asset["browser_download_url"],
            asset_name=installer_asset["name"],
            size_bytes=int(installer_asset.get("size") or 0),
            sha256_url=sha256_url,
            sha256_expected=sha256_expected,
        ))


class UpdateChecker(QObject):
    """Фасад для проверки обновлений. Использовать как одноразовый объект."""

    updateAvailable = pyqtSignal(object)   # ReleaseInfo
    noUpdate = pyqtSignal()
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_CheckWorker] = None

    def check(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        w = _CheckWorker(self)
        w.finished_ok.connect(self._on_done)
        w.failed.connect(self.errorOccurred.emit)
        self._worker = w
        w.start()

    def _on_done(self, info: Optional[ReleaseInfo]) -> None:
        if info is None:
            self.noUpdate.emit()
        else:
            self.updateAvailable.emit(info)


# ──────────────────────────────────────────────────────────────────────────
#  Скачивание установщика с прогрессом
# ──────────────────────────────────────────────────────────────────────────

class _DownloadWorker(QThread):
    progress = pyqtSignal(int, int)   # bytes_done, bytes_total
    finished_ok = pyqtSignal(str)     # путь к скачанному файлу
    failed = pyqtSignal(str)

    def __init__(self, info: ReleaseInfo, dest_path: str,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._info = info
        self._dest_path = dest_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        url = self._info.download_url
        dest = self._dest_path
        try:
            req = urllib.request.Request(url, headers=_auth_headers())
            opener = urllib.request.build_opener(
                _StripAuthOnRedirect(), urllib.request.HTTPSHandler(context=_ssl_context())
            )
            with opener.open(req, timeout=NETWORK_TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length") or
                            self._info.size_bytes or 0)
                hasher = hashlib.sha256()
                done = 0
                chunk_size = 64 * 1024
                tmp_path = dest + ".part"
                with open(tmp_path, "wb") as f:
                    while True:
                        if self._cancel:
                            try:
                                f.close()
                                os.remove(tmp_path)
                            except OSError:
                                pass
                            self.failed.emit("Загрузка отменена")
                            return
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)

                # Атомарно переименуем .part → финальное имя.
                if os.path.exists(dest):
                    os.remove(dest)
                os.rename(tmp_path, dest)

                # Проверка целостности.
                if self._info.sha256_expected:
                    actual = hasher.hexdigest().lower()
                    if actual != self._info.sha256_expected.lower():
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                        self.failed.emit(
                            "Проверка целостности не пройдена (SHA-256 не совпал).\n"
                            "Файл удалён. Попробуйте позже."
                        )
                        return
        except Exception as e:
            self.failed.emit(f"Ошибка загрузки: {e}")
            return

        self.finished_ok.emit(dest)


class UpdateDownloader(QObject):
    """Фасад для скачивания установщика."""

    downloadProgress = pyqtSignal(int, int)
    downloadFinished = pyqtSignal(str)   # путь к локальному .exe
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_DownloadWorker] = None

    def start(self, info: ReleaseInfo) -> None:
        if self._worker and self._worker.isRunning():
            return
        # Сохраняем в %TEMP% — Inno Setup сам спросит UAC, если нужно.
        tmp_dir = Path(tempfile.gettempdir()) / "MoySadovod_update"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        dest = str(tmp_dir / info.asset_name)

        w = _DownloadWorker(info, dest, self)
        w.progress.connect(self.downloadProgress.emit)
        w.finished_ok.connect(self.downloadFinished.emit)
        w.failed.connect(self.errorOccurred.emit)
        self._worker = w
        w.start()

    def cancel(self) -> None:
        if self._worker:
            self._worker.cancel()


# ──────────────────────────────────────────────────────────────────────────
#  Запуск установщика
# ──────────────────────────────────────────────────────────────────────────

def run_installer(installer_path: str) -> bool:
    """Запустить установщик Inno Setup в тихом режиме и вернуть True при успехе.

    Флаги Inno Setup:
      /SILENT              — без визарда, только прогресс-окно
      /NORESTART           — не перезагружать ПК автоматически
      /CLOSEAPPLICATIONS   — закрыть запущенное приложение перед обновлением
      /RESTARTAPPLICATIONS — запустить после установки
    """
    if not os.path.isfile(installer_path):
        return False
    if sys.platform != "win32":
        return False

    try:
        # Используем CREATE_NEW_PROCESS_GROUP, чтобы установщик пережил
        # завершение родителя.
        import subprocess
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [
                installer_path,
                "/SILENT",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
            ],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def human_size(n: int) -> str:
    """Удобочитаемый размер: '12,3 МБ'."""
    n = float(max(n, 0))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            s = f"{n:.1f}".replace(".", ",")
            return f"{s} {unit}"
        n /= 1024
    return "?"
