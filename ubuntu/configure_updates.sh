#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Запустите через sudo'; exit 1; }
python3 - <<'PY'
import getpass,json
from pathlib import Path
p=Path('/etc/nexsearch/github.json')
if not p.exists():
 token=getpass.getpass('GitHub token с доступом к Nexol-LLC/NexSearch: ')
 if not token:raise SystemExit('Токен не задан')
 p.write_text(json.dumps({'username':'x-access-token','token':token}));p.chmod(0o600)
ask=Path('/etc/nexsearch/github-askpass')
ask.write_text('#!/usr/bin/python3\nimport json,sys\nc=json.load(open("/etc/nexsearch/github.json"));print(c["username"] if "username" in sys.argv[1].lower() else c["token"])\n');ask.chmod(0o700)
PY
for name in nexsearch-update-retry.service nexsearch-update-retry.timer; do
  install -m 0644 "/opt/nexsearch/current/ubuntu/$name" "/etc/systemd/system/$name"
done
systemctl daemon-reload
systemctl enable --now nexsearch-update.timer nexsearch-update-retry.timer
systemctl start nexsearch-update.service
echo 'Проверка в 10:00 МСК и повтор отложенных/неудачных обновлений каждые 5 минут настроены.'
