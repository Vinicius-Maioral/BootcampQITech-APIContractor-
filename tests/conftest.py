from pathlib import Path
import sys
from os import path, environ

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))

if not environ.get("APP_ENV") or environ.get("APP_ENV") == "local":
    from dotenv import load_dotenv

    load_dotenv(path.join(str(root), ".env"))

    if environ.get("SERVER_LOCALHOST") is None:
        environ["SERVER_LOCALHOST"] = "0.0.0.0"
