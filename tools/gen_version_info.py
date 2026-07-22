"""Генерирует файл версии для PyInstaller (--version-file) из APP_VERSION.

Windows читает отсюда вкладку «Подробно» в свойствах .exe (описание,
версия файла/продукта, авторские права). Используется в build.bat перед
вызовом pyinstaller. Единственная точка истины по-прежнему APP_VERSION
в core/updater.py — здесь она только читается и разворачивается в
4-компонентный numeric-кортеж, который требует формат VSVersionInfo.

Запуск:
    python tools/gen_version_info.py <путь_к_выходному_файлу>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMPANY_NAME = "Namba1337"
PRODUCT_NAME = "Мой Садовод"
FILE_DESCRIPTION = "Мой Садовод"
LEGAL_COPYRIGHT = "Copyright © 2026 Namba1337"
ORIGINAL_FILENAME = "MoySadovod.exe"
INTERNAL_NAME = "MoySadovod"

TEMPLATE = """# UTF-8
#
# Автосгенерировано tools/gen_version_info.py — не редактировать вручную,
# правьте APP_VERSION в core/updater.py.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v_tuple}),
    prodvers=({v_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'041904b0',
        [StringStruct(u'CompanyName', u'{company}'),
        StringStruct(u'FileDescription', u'{description}'),
        StringStruct(u'FileVersion', u'{v_dots}'),
        StringStruct(u'InternalName', u'{internal_name}'),
        StringStruct(u'LegalCopyright', u'{copyright}'),
        StringStruct(u'OriginalFilename', u'{original_filename}'),
        StringStruct(u'ProductName', u'{product}'),
        StringStruct(u'ProductVersion', u'{v_dots}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1049, 1200])])
  ]
)
"""


def _read_app_version() -> str:
    text = (ROOT / "core" / "updater.py").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("Не удалось найти APP_VERSION в core/updater.py")
    return m.group(1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Использование: python tools/gen_version_info.py <output_path>")

    version = _read_app_version()
    parts = [int(p) for p in version.split(".")]
    # Windows требует РОВНО 4 числа в бинарной структуре VS_FIXEDFILEINFO
    # (filevers/prodvers) — это ограничение формата, обойти нельзя.
    tuple_parts = list(parts)
    while len(tuple_parts) < 4:
        tuple_parts.append(0)
    v_tuple = ", ".join(str(p) for p in tuple_parts[:4])
    # А вот отображаемая в свойствах файла строка (FileVersion/ProductVersion)
    # может быть любым текстом — оставляем её как есть, без добавления .0.
    v_dots = version

    content = TEMPLATE.format(
        v_tuple=v_tuple,
        v_dots=v_dots,
        company=COMPANY_NAME,
        description=FILE_DESCRIPTION,
        internal_name=INTERNAL_NAME,
        copyright=LEGAL_COPYRIGHT,
        original_filename=ORIGINAL_FILENAME,
        product=PRODUCT_NAME,
    )

    out_path = Path(sys.argv[1])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"Файл версии для PyInstaller: {out_path} (версия {v_dots})")


if __name__ == "__main__":
    main()
