"""Resource and writable-data paths for source and portable builds."""
from pathlib import Path
import os
import sys

FROZEN=bool(getattr(sys,'frozen',False))
RESOURCES=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
ROOT=Path(os.environ.get('COLLECTOR_HOME') or (Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent)).resolve()

def collector_command(*args):
    if FROZEN:return [sys.executable,'--collect',*map(str,args)]
    return [sys.executable,str(RESOURCES/'collector.py'),*map(str,args)]
