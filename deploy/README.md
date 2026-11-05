# Деплой

Файлы для VDS лежат здесь, чтобы GitHub оставался источником правды. Этот каталог сам сервер не обновляет.

| Файл | Назначение |
|---|---|
| `nginx-dushevye.conf` | Корень сайта `/var/www/dushevye/public`, `/api/` на `127.0.0.1:8088`, заголовки CSP |
| `dushevye-api.service` | systemd. Секрет сессии читается из `/etc/dushevye/api.env` |
| `install-on-vds.sh` | Запускается **на сервере** из распакованного payload: рядом должны быть `public/`, `app/`, `data/` |
| `deploy-vds.py` | Заливает `vds-payload.tgz` (лежит рядом со скриптом) по SSH |
| `dushevye-backup.sh` | Ночной архив сайта на VDS, хранит 7 копий |
| `pull-vds-daily.ps1` | Забирает свежий архив на шару Windows |
| `api.env.example` | Имена переменных без секретов |

`vds-payload.tgz` и `api.env` в git не входят (см. `.gitignore`). Пароль SSH скрипт берёт только из `DUSHEVYE_VDS_PASS`, ключ — из `DUSHEVYE_VDS_KEY`. В аргументы командной строки пароль не передаётся.

Выкладка на VDS и настройка HTTPS/домена в задачу синхронизации репозитория не входят.
