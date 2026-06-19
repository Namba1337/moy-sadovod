"""Система облачных обновлений через публичный JSON-манифест.

Архитектура:
    1. UpdateChecker.check() — фоновой поток, скачивает UPDATE_MANIFEST_URL,
       сравнивает версии, отдаёт ReleaseInfo сигналом updateAvailable
       (или noUpdate / errorOccurred).
    2. UpdateDownloader.start() — фоновой поток, качает установщик
       с прогрессом (downloadProgress), проверяет SHA-256, отдаёт путь
       к локальному .exe сигналом downloadFinished.
    3. run_installer() — запускает Inno Setup в тихом режиме
       (/SILENT /NORESTART /CLOSEAPPLICATIONS) и просит приложение
       завершиться.

Формат манифеста (публичный Gist или любой HTTPS-URL):
    {
        "version": "1.2.3",
        "notes":   "Что нового в этой версии...",
        "download_url": "https://..../MoySadovod_Setup_v1.2.3.exe",
        "sha256":  "abcdef0123456789...",   // опционально, 64 hex-символа
        "size_bytes": 12345678              // опционально
    }

При каждом релизе достаточно обновить JSON в Gist — никаких изменений
в коде или перекомпиляции не требуется.
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
#: При релизе: поднять здесь → прогнать build.bat → обновить Gist.
APP_VERSION = "0.4.1"

#: URL публичного JSON-манифеста обновлений (GitHub Gist raw или любой HTTPS).
#: Как создать Gist: https://gist.github.com → New gist → Public.
#: Скопируйте ссылку «Raw» и вставьте сюда.
#: Пример: "https://gist.githubusercontent.com/Namba1337/<id>/raw/update.json"
UPDATE_MANIFEST_URL: str = "https://gist.githubusercontent.com/Namba1337/39df34c920f5105092075ef4bc316c09/raw/update.json"

#: Таймаут сетевых запросов (секунды).
NETWORK_TIMEOUT = 15

#: User-Agent для HTTP-запросов.
USER_AGENT = f"MoySadovod/{APP_VERSION}"


# ──────────────────────────────────────────────────────────────────────────
#  Сравнение версий (semver-подобное, без зависимости от packaging)
# ──────────────────────────────────────────────────────────────────────────

def _parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' / '1.2' → (1, 2, 3) / (1, 2, 0).

    Хвост вида '-beta.1' игнорируется — канал у нас один.
    """
    v = v.strip().lstrip("vV")
    v = re.split(r"[-+]", v, maxsplit=1)[0]
    parts: list[int] = []
    for chunk in v.split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group()) if m else 0)
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
    """Информация о доступном обновлении, прочитанная из манифеста."""
    version: str                    # '1.2.3'
    notes: str                      # release notes (произвольный текст)
    download_url: str               # прямая ссылка на .exe установщика
    asset_name: str                 # имя файла (из URL)
    size_bytes: int                 # размер в байтах (0 если не указан)
    sha256_expected: Optional[str]  # SHA-256 для проверки целостности


# ──────────────────────────────────────────────────────────────────────────
#  Сетевые помощники
# ──────────────────────────────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext:
    """Системный SSL-контекст.

    На Windows PyInstaller иногда не находит certifi —
    используем default_context() с системными корнями.
    """
    return ssl.create_default_context()


def _http_get(url: str, *, timeout: int = NETWORK_TIMEOUT) -> bytes:
    """Простой GET-запрос по HTTPS. Без авторизации — только публичные URL."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context())
    )
    with opener.open(req, timeout=timeout) as r:
        return r.read()


# ──────────────────────────────────────────────────────────────────────────
#  Проверка наличия обновлений
# ──────────────────────────────────────────────────────────────────────────

class _CheckWorker(QThread):
    finished_ok = pyqtSignal(object)  # ReleaseInfo | None
    failed = pyqtSignal(str)

    def run(self) -> None:
        # Проверяем, что URL манифеста задан
        if "REPLACE_WITH_YOUR_GIST_ID" in UPDATE_MANIFEST_URL:
            self.failed.emit("URL манифеста обновлений не настроен.")
            return

        try:
            raw = _http_get(UPDATE_MANIFEST_URL)
            data = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
            return
        except Exception as e:
            self.failed.emit(str(e))
            return

        version = str(data.get("version", "")).strip()
        if not version:
            self.failed.emit("В манифесте отсутствует поле 'version'.")
            return

        if not is_newer(version, APP_VERSION):
            self.finished_ok.emit(None)
            return

        download_url = str(data.get("download_url", "")).strip()
        if not download_url:
            self.failed.emit("В манифесте отсутствует поле 'download_url'.")
            return

        # Имя файла берём из URL
        asset_name = Path(download_url.split("?")[0]).name or "MoySadovod_Setup.exe"

        # SHA-256 — опционально
        sha256_raw = str(data.get("sha256", "")).strip().lower()
        sha256_expected: Optional[str] = None
        if re.fullmatch(r"[0-9a-f]{64}", sha256_raw):
            sha256_expected = sha256_raw

        self.finished_ok.emit(ReleaseInfo(
            version=version,
            notes=str(data.get("notes", "")).strip(),
            download_url=download_url,
            asset_name=asset_name,
            size_bytes=int(data.get("size_bytes", 0) or 0),
            sha256_expected=sha256_expected,
        ))


class UpdateChecker(QObject):
    """Фасад для проверки обновлений. Использовать как одноразовый объект."""

    updateAvailable = pyqtSignal(object)  # ReleaseInfo
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
    progress = pyqtSignal(int, int)  # bytes_done, bytes_total
    finished_ok = pyqtSignal(str)   # путь к скачанному файлу
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
        dest = self._dest_path
        try:
            req = urllib.request.Request(
                self._info.download_url,
                headers={"User-Agent": USER_AGENT},
            )
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=_ssl_context())
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

                # Атомарно переименуем .part → финальное имя
                if os.path.exists(dest):
                    os.remove(dest)
                os.rename(tmp_path, dest)

                # Проверка целостности
                if self._info.sha256_expected:
                    actual = hasher.hexdigest().lower()
                    if actual != self._info.sha256_expected:
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
    downloadFinished = pyqtSignal(str)  # путь к локальному .exe
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_DownloadWorker] = None

    def start(self, info: ReleaseInfo) -> None:
        if self._worker and self._worker.isRunning():
            return
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
