#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите sudo bash install.sh'; exit 1; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ ! -f /etc/nexsearch/host.env ]] || { echo 'Хост уже установлен; существующая база сохранена.'; exit 1; }
apt-get update
apt-get install -y python3 python3-venv postgresql postgresql-contrib git ca-certificates ufw avahi-daemon
id nexsearch >/dev/null 2>&1 || useradd --system --home /var/lib/nexsearch --shell /usr/sbin/nologin nexsearch
install -d -m 0755 /opt/nexsearch/releases/initial /opt/nexsearch/control
install -d -m 0750 -o root -g nexsearch /etc/nexsearch /var/lib/nexsearch-updates /var/lib/nexsearch-updates/packages
install -d -m 0700 -o nexsearch -g nexsearch /var/lib/nexsearch /var/lib/nexsearch/identity
install -d -m 0700 -o postgres -g postgres /var/lib/nexsearch-provision /var/lib/nexsearch-backups
install -d -m 0700 /var/lib/nexsearch-updater
tar -xzf source.tar.gz -C /opt/nexsearch/releases/initial
install -d -m 0700 /opt/nexsearch/releases/initial/network/identity
ln -sfn /opt/nexsearch/releases/initial /opt/nexsearch/current
ln -sfn /opt/nexsearch/current/ubuntu /opt/nexsearch/ubuntu
ln -sfn /opt/nexsearch/current/network /opt/nexsearch/network
python3 -m venv /opt/nexsearch/venv
/opt/nexsearch/venv/bin/pip install -r /opt/nexsearch/current/network/requirements.txt
systemctl enable --now postgresql avahi-daemon
PYTHONPATH=/opt/nexsearch/current /opt/nexsearch/venv/bin/python - <<'PY'
from pathlib import Path
import json,shutil,getpass
from network.host_secrets import load,save
from network.tls import identity
from network.auth import HASHER
seed=Path('/opt/nexsearch/current/network/identity')
trust,cert,key=identity(seed)
Path('/opt/nexsearch/current/network/client-trust.json').write_text(json.dumps(trust))
if not (seed/'host-secrets.enc').exists():save({},seed/'host-secrets.enc',key)
data=load(seed/'host-secrets.enc',seed/'host-key.pem')
password_hash=data.get('bootstrap_admin_hash')
if not password_hash:password_hash=HASHER.hash(getpass.getpass('Пароль аккаунта host: '))
p=Path('/var/lib/nexsearch-provision/admin-seed.json');p.write_text(json.dumps({'password_hash':password_hash}));p.chmod(0o600);shutil.chown(p,user='postgres',group='postgres')
PY
runuser -u postgres -- /opt/nexsearch/venv/bin/python /opt/nexsearch/current/ubuntu/provision_hosted.py /var/lib/nexsearch-provision/admin-seed.json
install -m 0640 -o root -g nexsearch /var/lib/nexsearch-provision/host.env /etc/nexsearch/host.env
for name in host-key.pem host-cert.pem host-identity.json; do install -m 0600 -o nexsearch -g nexsearch "/opt/nexsearch/current/network/identity/$name" "/var/lib/nexsearch/identity/$name"; done
install -m 0600 -o nexsearch -g nexsearch /opt/nexsearch/current/network/identity/host-secrets.enc /var/lib/nexsearch/host-secrets.enc
install -m 0644 /opt/nexsearch/current/updates-public.pem /etc/nexsearch/updates-public.pem
install -m 0755 /opt/nexsearch/current/ubuntu/updater.py /opt/nexsearch/control/updater.py
install -d -m 0755 /opt/nexsearch/control/network
touch /opt/nexsearch/control/network/__init__.py
install -m 0644 /opt/nexsearch/current/network/updates.py /opt/nexsearch/control/network/updates.py
for name in nexsearch.service nexsearch-backup.service nexsearch-backup.timer nexsearch-update.service nexsearch-update.timer; do install -m 0644 "/opt/nexsearch/current/ubuntu/$name" "/etc/systemd/system/$name"; done
timedatectl set-timezone Europe/Moscow
for subnet in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
 ufw allow from "$subnet" to any port 22 proto tcp
 ufw allow from "$subnet" to any port 8743 proto tcp
 ufw allow from "$subnet" to any port 8742 proto udp
 ufw allow from "$subnet" to any port 5353 proto udp
done
ufw --force enable
rm -f /var/lib/nexsearch-provision/host.env /var/lib/nexsearch-provision/admin-seed.json
systemctl daemon-reload
systemctl enable --now nexsearch nexsearch-backup.timer
echo 'Хост установлен и запускается вместе с сервером. Вход: приложение сотрудника или https://IP:8743, аккаунт host.'
echo 'Обновления нового сервера: sudo bash /opt/nexsearch/current/ubuntu/configure_updates.sh'
