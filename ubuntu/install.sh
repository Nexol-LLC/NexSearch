#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите sudo bash install.sh'; exit 1; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ ! -f /etc/nexsearch/host.env ]] || { echo 'Хост уже настроен. Установка не заменяет существующую базу.'; exit 1; }
apt-get update
apt-get install -y python3 python3-venv postgresql postgresql-contrib
id nexsearch >/dev/null 2>&1 || useradd --system --home /var/lib/nexsearch --shell /usr/sbin/nologin nexsearch
install -d -m 0755 /opt/nexsearch
install -d -o nexsearch -g nexsearch -m 0700 /var/lib/nexsearch
install -d -o root -g nexsearch -m 0750 /etc/nexsearch
tar -xzf source.tar.gz -C /opt/nexsearch
python3 -m venv /opt/nexsearch/venv
/opt/nexsearch/venv/bin/pip install -r /opt/nexsearch/network/requirements.txt
systemctl start postgresql
install -d -o postgres -g postgres -m 0700 /var/lib/nexsearch-provision
sudo -u postgres /opt/nexsearch/venv/bin/python /opt/nexsearch/ubuntu/provision.py
install -o root -g nexsearch -m 0640 /var/lib/nexsearch-provision/host.env /etc/nexsearch/host.env
install -d -o nexsearch -g nexsearch -m 0700 /var/lib/nexsearch/identity
cp /opt/nexsearch/network/identity/host-{identity.json,cert.pem,key.pem} /var/lib/nexsearch/identity/
cp /opt/nexsearch/network/identity/host-secrets.enc /var/lib/nexsearch/
chown -R nexsearch:nexsearch /var/lib/nexsearch
chown root:nexsearch /etc/nexsearch/host.env
chmod 0640 /etc/nexsearch/host.env
chmod 0600 /var/lib/nexsearch/identity/* /var/lib/nexsearch/host-secrets.enc
chmod 0700 /opt/nexsearch/network/identity
install -m 0644 nexsearch.service /etc/systemd/system/nexsearch.service
install -d -o postgres -g postgres -m 0700 /var/lib/nexsearch-backups
install -m 0644 /opt/nexsearch/ubuntu/nexsearch-backup.service /etc/systemd/system/nexsearch-backup.service
install -m 0644 /opt/nexsearch/ubuntu/nexsearch-backup.timer /etc/systemd/system/nexsearch-backup.timer
systemctl daemon-reload
systemctl enable --now nexsearch nexsearch-backup.timer
echo 'Хост запущен. Для подключения используйте Employee из этого же комплекта 1.4.'
echo 'Разрешите в брандмауэре TCP 8743 и UDP 5353 только для вашей локальной подсети.'
echo 'Пароль директора задан при установке; доступ к интерфейсу — через приложение сотрудника.'
