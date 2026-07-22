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

Фолбэк на GitVerse (на случай блокировки GitHub в РФ):
    Репозиторий зеркалируется на gitverse.ru/namba1337/moy-sadovod.
    Releases API GitVerse требует токен даже для чтения публичного
    репозитория, поэтому вместо него используется его же публичный
    Contents API (не требует авторизации) — он отдаёт содержимое файла
    updates/latest.json (см. tools/gen_update_manifest.py) в base64.
    Если запрос к GitHub падает по сети (не 404 — 404 значит просто
    «релизов ещё нет», это не повод для фолбэка), проверка обновлений
    и скачивание установщика при сбое пробуют этот манифест на GitVerse.
"""

from __future__ import annotations

import base64
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
APP_VERSION = "0.7.0"

#: Таймаут сетевых запросов (секунды).
NETWORK_TIMEOUT = 15

#: User-Agent для HTTP-запросов.
USER_AGENT = f"MoySadovod/{APP_VERSION}"

#: Публичный репозиторий на GitHub — источник обновлений и истории релизов
#: (GitHub Releases API, без авторизации).
GITHUB_REPO = "Namba1337/moy-sadovod"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_LATEST_RELEASE_API = f"{GITHUB_RELEASES_API}/latest"

#: Зеркало на GitVerse — фолбэк, если GitHub недоступен (блокировки в РФ).
#: Читаем не Releases API (там нужен токен даже для публичного репозитория),
#: а публичный Contents API — он отдаёт содержимое updates/latest.json.
GITVERSE_REPO = "namba1337/moy-sadovod"
GITVERSE_MANIFEST_API = (
    f"https://gitverse.ru/sc/sbt/api/v1/repos/{GITVERSE_REPO}"
    "/contents/updates/latest.json?ref=main"
)


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
#  Фолбэк на манифест GitVerse (см. docstring модуля)
# ──────────────────────────────────────────────────────────────────────────

def _fetch_gitverse_manifest() -> Optional[dict]:
    """Скачать и разобрать updates/latest.json с зеркала на GitVerse.

    Использует публичный Contents API (без токена). Возвращает None,
    если файл пуст/отсутствует; бросает исключение при сетевой ошибке.
    """
    raw = _http_get(GITVERSE_MANIFEST_API, headers={"Accept": "application/json"})
    data = json.loads(raw.decode("utf-8"))
    content_b64 = data.get("content", "")
    if not content_b64:
        return None
    return json.loads(base64.b64decode(content_b64).decode("utf-8"))


def _manifest_to_release_info(manifest: dict) -> Optional["ReleaseInfo"]:
    version = str(manifest.get("version", "")).strip()
    if not version:
        return None
    return ReleaseInfo(
        version=version,
        notes=str(manifest.get("notes", "") or ""),
        download_url=str(manifest.get("download_url", "")).strip(),
        asset_name=str(manifest.get("asset_name", "")).strip() or "MoySadovod_Setup.exe",
        size_bytes=int(manifest.get("size_bytes", 0) or 0),
        sha256_expected=manifest.get("sha256") or None,
    )


# ──────────────────────────────────────────────────────────────────────────
#  Проверка наличия обновлений
# ──────────────────────────────────────────────────────────────────────────

class _CheckWorker(QThread):
    finished_ok = pyqtSignal(object)  # ReleaseInfo | None
    failed = pyqtSignal(str)

    def run(self) -> None:
        info, err = self._check_github()

        if err == "no_release":
            # GitHub доступен, но опубликованных релизов ещё нет — это не
            # повод пробовать зеркало, это легитимное «обновлений нет».
            self.finished_ok.emit(None)
            return

        if info is None:
            # GitHub недоступен (сеть/таймаут — типичный симптом блокировки
            # в РФ) либо релиз там неполный — пробуем зеркало на GitVerse.
            try:
                manifest = _fetch_gitverse_manifest()
                info = _manifest_to_release_info(manifest) if manifest else None
            except Exception:
                info = None
            if info is None:
                self.failed.emit(err or "Не удалось проверить обновления.")
                return

        if not is_newer(info.version, APP_VERSION):
            self.finished_ok.emit(None)
            return

        self.finished_ok.emit(info)

    @staticmethod
    def _check_github() -> tuple[Optional["ReleaseInfo"], Optional[str]]:
        """Запросить последний релиз GitHub.

        Возвращает (ReleaseInfo, None) при успехе, (None, "no_release")
        если релизов ещё нет, (None, сообщение_об_ошибке) при любой другой
        проблеме (в этом случае вызывающий код пробует зеркало GitVerse).
        """
        try:
            raw = _http_get(
                GITHUB_LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            data = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "no_release"
            return None, f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return None, str(e)

        tag = str(data.get("tag_name", "")).strip()
        version = tag.lstrip("vV")
        if not version:
            return None, "В ответе GitHub отсутствует тег релиза."

        assets = data.get("assets") or []
        exe_asset = next(
            (a for a in assets if str(a.get("name", "")).lower().endswith(".exe")),
            None,
        )
        if exe_asset is None:
            return None, "В релизе не найден установщик (.exe)."

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

        return ReleaseInfo(
            version=version,
            notes=str(data.get("body", "") or "").strip(),
            download_url=download_url,
            asset_name=asset_name,
            size_bytes=size_bytes,
            sha256_expected=sha256_expected,
        ), None


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

    def stop(self) -> None:
        """Безопасно остановить фоновый поток перед уничтожением объекта.

        Qt не позволяет уничтожать работающий QThread — при закрытии
        приложения, пока GitHub/GitVerse ещё не ответили (до ~30 сек на
        оба источника), обычное закрытие окна крашило приложение
        (`QThread: Destroyed while thread is still running`). Вызывать
        из closeEvent перед тем, как отпустить владельца этого объекта.
        """
        w = self._worker
        if w is None:
            return
        try:
            w.finished_ok.disconnect()
        except TypeError:
            pass
        try:
            w.failed.disconnect()
        except TypeError:
            pass
        if w.isRunning():
            w.terminate()
            w.wait(2000)


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

    def stop(self) -> None:
        """См. UpdateChecker.stop() — та же защита от краша Qt при
        закрытии диалога, пока поток ещё не ответил."""
        w = self._worker
        if w is None:
            return
        try:
            w.finished_ok.disconnect()
        except TypeError:
            pass
        try:
            w.failed.disconnect()
        except TypeError:
            pass
        if w.isRunning():
            w.terminate()
            w.wait(2000)


# ──────────────────────────────────────────────────────────────────────────
#  Скачивание установщика с прогрессом
# ──────────────────────────────────────────────────────────────────────────

class _DownloadCancelled(Exception):
    pass


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
        url = self._info.download_url
        tried_mirror = False
        last_error: Optional[str] = None

        while True:
            try:
                self._download_from(url)
            except _DownloadCancelled:
                self.failed.emit("Загрузка отменена")
                return
            except Exception as e:
                last_error = str(e)
                # Основная ссылка зависла/недоступна (типичный симптом
                # блокировки GitHub в РФ) — один раз пробуем зеркало.
                if tried_mirror:
                    self.failed.emit(f"Ошибка загрузки: {last_error}")
                    return
                tried_mirror = True
                mirror_url = self._find_mirror_url(url)
                if mirror_url is None:
                    self.failed.emit(f"Ошибка загрузки: {last_error}")
                    return
                url = mirror_url
                continue
            else:
                self.finished_ok.emit(self._dest_path)
                return

    @staticmethod
    def _find_mirror_url(current_url: str) -> Optional[str]:
        """Найти альтернативную ссылку на установщик в манифесте GitVerse."""
        try:
            manifest = _fetch_gitverse_manifest()
        except Exception:
            return None
        if not manifest:
            return None
        alt = str(manifest.get("download_url", "")).strip()
        if not alt or alt == current_url:
            return None
        return alt

    def _download_from(self, url: str) -> None:
        """Скачать файл по url.

        GitVerse не разрешает прикладывать к релизу .exe напрямую (список
        допустимых расширений — .zip, .7z и т.п.), поэтому его зеркало
        отдаёт установщик запакованным в .zip. Ссылки-вложения GitVerse
        вида .../api/attachments/<uuid> не содержат расширения в самом URL
        (тип — только в Content-Disposition ответа), поэтому zip определяем
        не по ссылке, а по содержимому скачанного файла (сигнатура `PK`).
        """
        dest = self._dest_path
        tmp_path = dest + ".part"

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context())
        )
        with opener.open(req, timeout=NETWORK_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            chunk_size = 64 * 1024
            with open(tmp_path, "wb") as f:
                while True:
                    if self._cancel:
                        try:
                            f.close()
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        raise _DownloadCancelled()
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    self.progress.emit(done, total)

        if self._is_zip_file(tmp_path):
            self._extract_installer(tmp_path, dest)
        else:
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(tmp_path, dest)
        self._verify_sha256(dest)

    @staticmethod
    def _is_zip_file(path: str) -> bool:
        try:
            with open(path, "rb") as f:
                sig = f.read(2)
        except OSError:
            return False
        return sig == b"PK"

    @staticmethod
    def _extract_installer(zip_path: str, dest: str) -> None:
        """Достать единственный .exe из архива в dest и удалить архив."""
        import zipfile
        try:
            with zipfile.ZipFile(zip_path) as zf:
                exe_names = [n for n in zf.namelist() if n.lower().endswith(".exe")]
                if not exe_names:
                    raise ValueError("в архиве не найден установщик (.exe)")
                with zf.open(exe_names[0]) as src, open(dest, "wb") as out:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass

    def _verify_sha256(self, path: str) -> None:
        if not self._info.sha256_expected:
            return
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        if hasher.hexdigest().lower() != self._info.sha256_expected:
            try:
                os.remove(path)
            except OSError:
                pass
            raise ValueError(
                "проверка целостности не пройдена (SHA-256 не совпал)"
            )


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

    def stop(self) -> None:
        """Безопасно остановить фоновый поток перед уничтожением объекта.

        `cancel()` — кооперативный флаг, который поток проверяет только
        между чанками уже идущей загрузки; если поток ещё не дошёл до
        этого места (застрял в подключении к GitHub/GitVerse, до ~30 сек
        на оба источника), `cancel()` его не остановит. Вызывать отсюда
        из closeEvent/reject перед уничтожением владельца этого объекта.
        """
        w = self._worker
        if w is None:
            return
        w.cancel()
        try:
            w.progress.disconnect()
        except TypeError:
            pass
        try:
            w.finished_ok.disconnect()
        except TypeError:
            pass
        try:
            w.failed.disconnect()
        except TypeError:
            pass
        if w.isRunning():
            w.terminate()
            w.wait(2000)


# ──────────────────────────────────────────────────────────────────────────
#  Запуск установщика
# ──────────────────────────────────────────────────────────────────────────

def run_installer(installer_path: str) -> bool:
    """Запустить установщик Inno Setup в тихом режиме и вернуть True при успехе.

    Флаги Inno Setup:
      /SILENT              — без визарда, только прогресс-окно
      /NORESTART           — не перезагружать ПК автоматически
      /CLOSEAPPLICATIONS   — закрыть запущенное приложение перед обновлением
                              (подстраховка — см. задержку ниже)

    Установщик запускается не мгновенно, а через обёртку с ~2-секундной
    задержкой (`ping` вместо `timeout` — тот падает с ошибкой без консоли,
    а нам нужен именно бесконсольный запуск). Наш процесс к этому моменту
    уже успевает полностью завершиться (QApplication.quit() вызывается
    сразу следом) и снять блокировку с MoySadovod.exe — без этого
    Inno Setup иногда не успевал закрыть нас через Restart Manager и
    показывал диалог «Не удалось автоматически закрыть все приложения».

    Повторный запуск приложения после установки — не через
    /RESTARTAPPLICATIONS (это дублировало бы запуск с [Run]-секцией
    installer.iss в редких гонках), а через постоянную [Run]-запись
    в installer.iss (работает и в тихом режиме).
    """
    if not os.path.isfile(installer_path):
        return False
    if sys.platform != "win32":
        return False

    try:
        import subprocess
        # DETACHED_PROCESS/CREATE_NEW_PROCESS_GROUP конфликтуют с
        # CREATE_NO_WINDOW (Windows игнорирует скрытие окна) — отсюда
        # всплывавшее консольное окно с "ping". Достаточно CREATE_NO_WINDOW:
        # он и создаёт независимый от нас процесс, и не показывает окно.
        CREATE_NO_WINDOW = 0x08000000

        command = (
            'ping -n 3 127.0.0.1 >nul & '
            f'"{installer_path}" /SILENT /NORESTART /CLOSEAPPLICATIONS'
        )
        subprocess.Popen(
            command,
            shell=True,
            creationflags=CREATE_NO_WINDOW,
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
