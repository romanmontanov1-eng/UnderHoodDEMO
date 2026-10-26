# UnderHood — Montana Web

Публичный сайт детейлинг-студии: 11 страниц HTML/CSS/JavaScript.

## Render

- Репозиторий: https://github.com/romanmontanov1-eng/UnderHoodDEMO
- Тип сервиса: Static Site.
- Build command: `sh build.sh`
- Publish directory: `dist`
- Ветка: `main`
- Секреты для публичной части не требуются.

Не публикуйте корень репозитория. Скрипт сборки копирует только проверенные публичные файлы.

## Локальный запуск

`python -m http.server 8000`

Откройте http://localhost:8000. Для проверки сборки: `sh build.sh`, затем `python -m http.server 8000 --directory dist`.

## CMS и админка

Публичная часть продолжает получать контент и фото с прежнего CMS. Авторизация, редактирование, загрузка и хранение фото, резервные копии остаются на прежнем сервере. Для обновления контента его нужно сохранять работающим.

Подробности, результаты проверки и ограничения: [MIGRATION.md](MIGRATION.md).
