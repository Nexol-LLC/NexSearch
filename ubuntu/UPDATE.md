# Обновление Ubuntu-хоста

Установка через install.sh не заменяет уже настроенный хост. Перед обновлением сохраните /etc/nexsearch/host.env, /var/lib/nexsearch/identity и host-secrets.enc в защищённом месте.

Для обновления уже установленной 1.4 выполните:

1. Скопируйте новый подписанный HostUbuntu в отдельную папку.
2. Проверьте его доверенным ключом установленной программы: `/opt/nexsearch/venv/bin/python /opt/nexsearch/ubuntu/verify_release.py /путь/HostUbuntu /opt/nexsearch/updates-public.pem`. При ошибке остановитесь.
3. `sudo systemctl start nexsearch-backup`, затем проверьте успешное восстановление в журнале и вкладке резервных копий.
4. Остановите службу `sudo systemctl stop nexsearch`. Скопируйте прежние исходники /opt/nexsearch в отдельный защищённый каталог, сохранив venv. Распакуйте проверенный source.tar.gz в /opt/nexsearch. Не заменяйте /etc/nexsearch/host.env и /var/lib/nexsearch.
5. Установите зависимости при необходимости, обновите systemd-файлы из ubuntu, выполните `sudo systemctl daemon-reload` и `sudo systemctl start nexsearch`.

При сбое остановите службу и верните прежние файлы программы из отдельной копии. Откат базы — отдельная операция восстановления проверенного дампа; не выполняйте её автоматически поверх рабочих данных.

Для перехода с 1.3 сначала создайте `pg_dump --format=custom` пользователем ОС postgres и проверьте его восстановлением в отдельную базу. Новые SQL-таблицы создаются пользователем владельца схемы, не веб-приложением: после остановки службы выполните schema.sql и schema14.sql из нового комплекта через psycopg под ОС postgres, `SET ROLE nexsearch_owner`, `set_config('nexol.is_admin','true',true)`. Повторно выдайте nexsearch_app SELECT/INSERT/UPDATE/DELETE на таблицы и USAGE/SELECT на последовательности, отзовите UPDATE/DELETE на audit_events. Затем установите новый код и службы резервного копирования. Сохраните прежние секреты и identity хоста.
