"""Настройки приложения, НЕ входящие в файл проекта (.snt): список недавних
баз (MRU) и путь к базе, с которой велась работа последней.

Хранится в %APPDATA%/MoySadovod/app_state.json — отдельно от data/, т.к.
data/ — это рабочая копия ТЕКУЩЕЙ базы (её содержимое подменяется при
загрузке/создании новой базы), а этот файл переживает такие подмены.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MoySadovod"
_STATE_FILE = _STATE_DIR / "app_state.json"

_MAX_RECENT = 8


def _read() -> dict:
    try:
        if _STATE_FILE.exists():
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _write(data: dict) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_last_project() -> Optional[str]:
    """Путь к базе, с которой велась работа последней (или None)."""
    path = _read().get("last_project")
    if path and os.path.isfile(path):
        return path
    return None


def get_recent_projects() -> list[str]:
    """MRU-список путей, только существующие на диске файлы."""
    return [p for p in _read().get("recent_projects", []) if os.path.isfile(p)]


def remember_project(path: str) -> None:
    """Отметить путь как последний активный и поднять в начало MRU-списка."""
    path = str(Path(path).resolve())
    data = _read()
    recent = [p for p in data.get("recent_projects", [])
              if os.path.isfile(p) and p != path]
    recent.insert(0, path)
    data["recent_projects"] = recent[:_MAX_RECENT]
    data["last_project"] = path
    _write(data)


def forget_project(path: str) -> None:
    """Убрать путь из MRU и из last_project (например, если открыть не удалось)."""
    path = str(Path(path).resolve())
    data = _read()
    data["recent_projects"] = [p for p in data.get("recent_projects", []) if p != path]
    if data.get("last_project") == path:
        data["last_project"] = None
    _write(data)
