# СНТ — Финансовый учёт

Десктопное приложение для просмотра и анализа банковских выписок СберБизнес.

---

## Установка и запуск

### 1. Установить Python
Скачать Python 3.10+ с https://python.org и установить.
При установке поставить галочку **"Add Python to PATH"**.

### 2. Установить зависимости
Открыть командную строку в папке с программой и выполнить:
```
pip install -r requirements.txt
```

### 3. Запустить программу
```
python main.py
```

---

## Сборка в .exe (для установки на другие компьютеры)

Выполнить в командной строке:
```
pyinstaller --onefile --windowed --name "СНТ_Учёт" main.py
```

Готовый `.exe` появится в папке `dist/`.

---

## Использование

1. Нажать кнопку **«Загрузить файл»**
2. Выбрать файл выписки СберБизнес (`.xlsx`)
3. Использовать фильтры:
   - **Поиск** — по контрагенту или назначению
   - **Тип операции** — все / поступления / списания
   - **Даты** — период операций
4. Нажать **«Сбросить»** для отмены фильтров

---

## Структура проекта

```
snt_app/
├── main.py          # Основной файл программы
├── requirements.txt # Зависимости
└── README.md        # Инструкция
```

---

## Облачные обновления

Приложение само проверяет наличие новой версии при запуске и через кнопку
**«Проверить обновления»** в сайдбаре. Источник — GitHub Releases приватного репозитория.

### Настройка (однократно)

#### 1. Создать GitHub Personal Access Token

1. Открыть **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
2. Нажать **Generate new token**, заполнить:
   - **Token name**: `MoySadovod Updater`
   - **Expiration**: по желанию (или No expiration)
   - **Repository access**: Only selected repositories → выбрать `snt_helper_app`
   - **Permissions → Contents**: `Read-only`
3. Скопировать токен (показывается один раз).

#### 2. Прописать токен в updater.py

Открыть `core/updater.py` и вставить токен:

```python
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "ghp_ВАШ_ТОКЕН_ЗДЕСЬ")
```

`GITHUB_OWNER` и `GITHUB_REPO` уже заполнены верно.

> **Безопасность:** токен имеет права только на чтение содержимого одного репозитория,
> поэтому его можно безопасно бандлить внутрь `.exe`.

### Выпуск новой версии

1. Поднять номер версии в `core/updater.py` — **единственная точка истины**:
   ```python
   APP_VERSION = "1.0.1"
   ```
2. Закоммитить и запушить все изменения через VS Code (Commit → Sync).

3. Запустить `build.bat` — он:
   - читает версию из `core/updater.py`,
   - собирает `dist\MoySadovod.exe` (PyInstaller),
   - упаковывает `installer\MoySadovod_Setup_v1.0.1.exe` (Inno Setup),
   - генерирует `installer\MoySadovod_Setup_v1.0.1.exe.sha256`.

   > `installer.iss` при этом **не изменяется** — версия передаётся через
   > флаг `/DAppVersion=` компилятору Inno Setup. Дополнительный коммит
   > после сборки не нужен.

4. Поставить аннотированный тег и запушить его:
   ```
   git tag -a v1.0.1 -m "v1.0.1"
   git push origin v1.0.1
   ```

5. Собрать список изменений для release notes (опционально):
   ```
   git log v1.0.0..v1.0.1 --oneline
   ```

6. На GitHub: **Releases → Draft new release** → выбрать тег `v1.0.1`,
   приложить ОБА файла (`.exe` и `.sha256`) как assets, заполнить
   release notes (их увидит пользователь в диалоге обновления) → **Publish**.

7. Открыть Gist и обновить `update.json`:
   ```json
   {
     "version": "1.0.1",
     "notes": "Что нового...",
     "download_url": "https://github.com/Namba1337/snt_helper_app_public_releases/releases/download/v1.0.1/MoySadovod_Setup_v1.0.1.exe",
     "sha256": "<хеш из .sha256 файла>",
     "size_bytes": 0
   }
   ```
   Нажать **Update** — установленные клиенты получат уведомление при
   следующем запуске.

После публикации все установленные клиенты при следующем запуске увидят
плашку «Доступна новая версия» с кнопкой «Обновить». При нажатии:
1. Скачивается установщик в `%TEMP%\MoySadovod_update\`.
2. Проверяется SHA-256 (из поля `sha256` в `update.json`).
3. Запускается Inno Setup в тихом режиме (`/SILENT /CLOSEAPPLICATIONS
   /RESTARTAPPLICATIONS`), приложение завершается, установщик
   обновляет файлы и заново запускает приложение.
