"""Генерирует updates/latest.json — манифест для fallback-проверки обновлений
через зеркало (GitVerse), когда GitHub API недоступен (блокировки в РФ).

Единственная точка истины остаётся GitHub Release — публикуется как раньше
(build.bat → тег vX.Y.Z → GitHub Release с *.exe + *.exe.sha256). Этот скрипт
просто читает тот же самый публичный GitHub API и переносит данные в манифест,
подставляя вместо GitHub-ссылки на .exe — ссылку с зеркала.

Порядок действий при релизе:
    1. Опубликовать GitHub Release как обычно.
    2. GitVerse не разрешает прикладывать к релизу .exe напрямую (только
       .zip/.7z и т.п.) — запаковать тот же *.exe в .zip (один файл внутри
       архива) и загрузить архив в релиз на GitVerse. core/updater.py сам
       распознаёт .zip-ссылку и распаковывает её после скачивания.
    3. Скопировать прямую ссылку на .zip с GitVerse.
    4. Запустить:
       python tools/gen_update_manifest.py <ссылка_на_zip_на_GitVerse>
    5. Закоммитить и запушить updates/latest.json в оба remote (origin + gitverse).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.updater import GITHUB_LATEST_RELEASE_API, USER_AGENT  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Использование: python tools/gen_update_manifest.py <URL exe на GitVerse>"
        )
    gitverse_url = sys.argv[1]

    req = urllib.request.Request(
        GITHUB_LATEST_RELEASE_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)

    tag = str(data.get("tag_name", "")).strip()
    version = tag.lstrip("vV")
    if not version:
        raise SystemExit("В ответе GitHub отсутствует тег релиза.")

    assets = data.get("assets") or []
    exe_asset = next(
        (a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None
    )
    if exe_asset is None:
        raise SystemExit("В последнем GitHub Release нет .exe.")

    sha_asset = next(
        (a for a in assets if str(a.get("name", "")).lower().endswith(".sha256")), None
    )
    sha256 = None
    if sha_asset is not None:
        sha_req = urllib.request.Request(
            sha_asset["browser_download_url"], headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(sha_req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="ignore").strip()
            sha256 = raw.split()[0] if raw else None

    manifest = {
        "version": version,
        "notes": str(data.get("body", "") or "").strip(),
        "asset_name": exe_asset["name"],
        "size_bytes": int(exe_asset.get("size", 0) or 0),
        "sha256": sha256,
        "download_url": gitverse_url,
    }

    out_path = ROOT / "updates" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Записано: {out_path} (версия {version})")


if __name__ == "__main__":
    main()
