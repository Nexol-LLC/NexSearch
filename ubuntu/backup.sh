#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo'; exit 1; }
install -d -o postgres -g postgres -m 0700 /var/lib/nexsearch-backups
install -d -o root -g root -m 0700 /var/lib/nexsearch-backups/identity
for name in host-key.pem host-cert.pem host-identity.json; do
 install -o root -g root -m 0600 "/var/lib/nexsearch/identity/$name" "/var/lib/nexsearch-backups/identity/$name"
done
install -o root -g root -m 0600 /var/lib/nexsearch/host-secrets.enc /var/lib/nexsearch-backups/identity/host-secrets.enc
exec runuser -u postgres -- /opt/nexsearch/venv/bin/python /opt/nexsearch/ubuntu/backup_pg.py
