#!/usr/bin/env bash
# Run this on the already configured office Ubuntu host, never on employee computers.
set -Eeuo pipefail
umask 077
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run: sudo bash connect-office.sh'
  exit 1
fi
bundle_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin=/opt/nexsearch/venv/bin/python
if [ ! -x "$python_bin" ]; then
  echo 'Existing NexSearch office host not found at /opt/nexsearch. Nothing changed.'
  exit 1
fi
private_dir="$(mktemp -d /var/tmp/nexsearch-office-connect.XXXXXXXX)"
trap 'rm -rf -- "$private_dir"' EXIT
"$python_bin" "$bundle_dir/envelope.py" --envelope "$bundle_dir/office-tunnel.signed.json" \
  --output "$private_dir"
cp -- "$bundle_dir/frp_0.71.1_linux_amd64.tar.gz" "$private_dir/"
bash "$private_dir/connect-office.sh"
