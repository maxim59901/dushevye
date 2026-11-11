# Бэкапы VDS «Душевые»

Простыми словами: раз в сутки сервер сам складывает копию сайта в архив. Скрипт на вашем ПК забирает свежий архив на шару.

## Что копируется

- Папка сайта `/var/www/dushevye`. `app.db` и `catalog.db` попадают в архив снимком SQLite (`backup`), а не копией файла, который мог быть записан наполовину
- Конфиги nginx сайта, sshd (`sshd_config` и `sshd_config.d`, без приватных host key), unit `dushevye-api`, `/etc/cron.d/dushevye`, root crontab и `/etc/dushevye` (там лежит `api.env`)

Каталог архивов `/var/backups/dushevye` — режим `700`. Сам архив — `600`. Скрипт ничего не меняет в sshd и не перезаписывает живые базы.

## На сервере (VDS 178.212.15.231)

- Скрипт: `/usr/local/bin/dushevye-backup.sh`
- Архивы: `/var/backups/dushevye/dushevye-YYYY-MM-DD_HHMM.tgz`
- Храним последние **7** архивов на сервере
- Cron: каждый день около **03:00** (часовой пояс сервера — см. `timedatectl`)

## На вашем ПК (шара)

- Скрипт в git: `deploy/pull-vds-daily.ps1` (на шаре исторически `Z:\My Progect\Dushevye-Stanok\backups\pull-vds-daily.ps1`)
- Куда кладёт: `Z:\My Progect\Dushevye-Stanok\backups\daily\`
- Дубль (если шара пишет): `Z:\bobrlink-backups\dushevye-vds\`
- Если дубль недоступен — ошибку не поднимает, остаётся копия под проектом

## Как запустить вручную

1. На VDS: `sudo /usr/local/bin/dushevye-backup.sh`
2. На ПК (PowerShell):  
   `powershell -File "Z:\My Progect\Dushevye-Stanok\backups\pull-vds-daily.ps1"`

Пароль SSH **не** хранится в файлах на шаре. Для входа — ключ SSH или переменная окружения / менеджер паролей (Vault).

## Пробный бэкап

Дата и результат пробного прогона записываются рядом с архивом в `backups\daily\` (имя файла с датой).
