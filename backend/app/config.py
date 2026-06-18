"""Configuration bootstrap.

Reuses the metayaml-based pattern from the original lawer_assistant backend:
YAML files are merged with python expressions (``${env['KEY']}``) and logging
is configured at import time so every module shares the same handlers.
"""
from copy import deepcopy
import logging.config
import os
from pathlib import Path

from dotenv import load_dotenv
from metayaml import read

PROJECT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parents[0]

load_dotenv(dotenv_path=PROJECT_DIR / ".env")

try:
    project_dir = Path(os.environ["APP_DIR"])
except KeyError:
    project_dir = PROJECT_DIR
    os.environ["APP_DIR"] = project_dir.as_posix()

default_config = Path(
    os.environ.get("DEFAULT_CONFIG", project_dir / "app/config/config.yaml")
).as_posix()
configs = [default_config]

config_path = os.environ.get("CONFIG_PATH")
if config_path:
    configs.append(config_path)

logs_path = os.environ.get("LOGS_PATH")
if not logs_path:
    logs_path = (project_dir / "var/log/app").as_posix()
    os.environ["LOGS_PATH"] = logs_path

os.makedirs(logs_path, exist_ok=True)

CONFIG = read(
    configs,
    {"join": os.path.join, "env": os.environ, "deepcopy": deepcopy, "logs_path": logs_path},
)


def get_config() -> dict:
    return CONFIG


logging.config.dictConfig(CONFIG["logging"])
