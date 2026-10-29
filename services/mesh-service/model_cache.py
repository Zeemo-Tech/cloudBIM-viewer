"""Private, disposable cache for content-addressed design geometry only."""
import os
from pathlib import Path
import tempfile


def cache_root():
    path = Path(os.environ.get('CLOUDBIM_MODEL_CACHE_DIR',
                              str(Path(tempfile.gettempdir())/f'cloudbim-model-cache-{os.getuid()}')))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
