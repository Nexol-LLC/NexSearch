from pathlib import Path
import json
try:VERSION=json.loads((Path(__file__).resolve().parents[1]/'version.json').read_text(encoding='utf-8'))['version']
except (OSError,ValueError,KeyError):VERSION='1.4.1'
