"""Where code, models and recordings live on macOS and Windows."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT/'models'
ASR_MODEL = MODELS/'qwen3-asr'
DOWNLOAD_STATUS = MODELS/'download-status.json'


def default_recordings():
    if os.environ.get('LOCALVOICE_RECORDINGS'):
        return Path(os.environ['LOCALVOICE_RECORDINGS'])
    if sys.platform == 'win32':
        return Path.home()/'Documents'/'LocalVoice Aufnahmen'
    return ROOT/'Aufnahmen'


HOST = 'diesem PC' if sys.platform == 'win32' else 'deinem Mac'
FILE_BROWSER = 'Explorer' if sys.platform == 'win32' else 'Finder'


def asr_ready():
    return (ASR_MODEL/'.ready.json').exists()
