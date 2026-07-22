"""Система облачных обновлений через GitHub Releases (публичный репозиторий).

Архитектура:
    1. UpdateChecker.check() — фоновой поток, запрашивает у GitHub
       последний релиз (releases/latest), сравнивает версии, отдаёт
       ReleaseInfo сигналом updateAvailable (или noUpdate / errorOccurred).
    2. UpdateDownloader.start() — фоновой поток, качает установщик
       с прогрессом (downloadProgress), проверяет SHA-256, отдаёт путь
       к локальному .exe сигналом downloadFinished.
    3. run_installer() — запускает Inno Setup в тихом режиме
       (/SILENT /NORESTART /CLOSEAPPLICATIONS) и просит приложение
       завершиться.

При каждом релизе достаточно опубликовать GitHub Release с тегом
`vX.Y.Z` и приложенными файлами `*.exe` + `*.exe.sha256` — никаких
изменений в коде или перекомпиляции не требуется. GitHub API отдаёт
последний ОПУБЛИКОВАННЫЙ релиз (черновики и pre-release игнорируются).
Никакой авторизации не нужно — репозиторий публичный.
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
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ──────────────────────────────────────────────────────────────────────────
#  Конфигурация
# ──────────────────────────────────────────────────────────────────────────

#: Текущая версия приложения. ЕДИНСТВЕННАЯ ТОЧКА ИСТИНЫ.
#: При релизе: поднять здесь → прогнать build.bat → опубликовать GitHub Release.
APP_VERSION = "0.5.0"

#: Таймаут сетевых запросов (секунды).
NETWORK_TIMEOUT = 15

#: User-Agent для HTTP-запросов.
USER_AGENT = f"MoySadovod/{APP_VERSION}"

#: Публичный репозиторий на GitHub — источник обновлений и истории релизов
#: (GitHub Releases API, без авторизации).
GITHUB_REPO = "Namba1337/snt_helper_app"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_LATEST_RELEASE_API = f"{GITHUB_RELEASES_API}/latest"


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
    """Информация о доступном обновлении, прочитанная из GitHub Release."""
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


def _http_get(url: str, *, timeout: int = NETWORK_TIMEOUT,
              headers: Optional[dict] = None) -> bytes:
    """Простой GET-запрос по HTTPS. Без авторизации — только публичные URL."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
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
        try:
            raw = _http_get(
                GITHUB_LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            data = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Ещё нет ни одного опубликованного релиза.
                self.finished_ok.emit(None)
                return
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
            return
        except Exception as e:
            self.failed.emit(str(e))
            return

        tag = str(data.get("tag_name", "")).strip()
        version = tag.lstrip("vV")
        if not version:
            self.failed.emit("В ответе GitHub отсутствует тег релиза.")
            return

        if not is_newer(version, APP_VERSION):
            self.finished_ok.emit(None)
            return

        assets = data.get("assets") or []
        exe_asset = next(
            (a for a in assets if str(a.get("name", "")).lower().endswith(".exe")),
            None,
        )
        if exe_asset is None:
            self.failed.emit("В релизе не найден установщик (.exe).")
            return

        download_url = str(exe_asset.get("browser_download_url", "")).strip()
        asset_name = str(exe_asset.get("name", "")).strip() or "MoySadovod_Setup.exe"
        size_bytes = int(exe_asset.get("size", 0) or 0)

        # SHA-256 — из приложенного *.exe.sha256, если есть (опционально).
        sha256_expected: Optional[str] = None
        sha_asset = next(
            (a for a in assets if str(a.get("name", "")).lower().endswith(".sha256")),
            None,
        )
        if sha_asset is not None:
            sha_url = str(sha_asset.get("browser_download_url", "")).strip()
            try:
                sha_raw = _http_get(sha_url).decode("utf-8", errors="ignore")
                first_token = sha_raw.strip().split()[0] if sha_raw.strip() else ""
                if re.fullmatch(r"[0-9a-fA-F]{64}", first_token):
                    sha256_expected = first_token.lower()
            except Exception:
                pass  # Проверка целостности просто будет пропущена.

        self.finished_ok.emit(ReleaseInfo(
            version=version,
            notes=str(data.get("body", "") or "").strip(),
            download_url=download_url,
            asset_name=asset_name,
            size_bytes=size_bytes,
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
#  История релизов (GitHub Releases API)
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReleaseHistoryEntry:
    """Одна запись в истории обновлений (один GitHub Release)."""
    version: str        # '1.2.3' (без ведущей 'v')
    notes: str           # тело релиза (release notes), как есть
    published: str        # 'дд.мм.гггг' либо '' если дата не распознана


class _HistoryWorker(QThread):
    finished_ok = pyqtSignal(list)   # list[ReleaseHistoryEntry]
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            raw = _http_get(
                GITHUB_RELEASES_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            data = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.failed.emit(f"HTTP {e.code}: {e.reason}")
            return
        except Exception as e:
            self.failed.emit(str(e))
            return

        if not isinstance(data, list):
            self.failed.emit("Неожиданный формат ответа GitHub API.")
            return

        entries: list[ReleaseHistoryEntry] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag_name", "")).strip()
            version = tag.lstrip("vV") or tag
            if not version:
                continue
            notes = str(item.get("body", "") or "").strip()
            published_raw = str(item.get("published_at", "") or "")
            published = ""
            if published_raw:
                try:
                    published = datetime.strptime(
                        published_raw[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
                except Exception:
                    published = published_raw[:10]
            entries.append(ReleaseHistoryEntry(
                version=version, notes=notes, published=published))
        self.finished_ok.emit(entries)


class ReleaseHistoryFetcher(QObject):
    """Фасад для получения истории релизов с GitHub (публичный REST API,
    без авторизации — подходит только для публичных репозиториев)."""

    historyReady = pyqtSignal(list)   # list[ReleaseHistoryEntry]
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_HistoryWorker] = None

    def fetch(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        w = _HistoryWorker(self)
        w.finished_ok.connect(self.historyReady.emit)
        w.failed.connect(self.errorOccurred.emit)
        self._worker = w
        w.start()


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
