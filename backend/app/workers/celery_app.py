"""Celery application using Redis as task transport and PostgreSQL for state."""

import os

from celery import Celery
from dotenv import dotenv_values

from backend.app.core.config import REPOSITORY_ROOT

_env_values = dotenv_values(REPOSITORY_ROOT / ".env")
_redis_url = os.getenv("REDIS_URL") or _env_values.get("REDIS_URL") or "redis://localhost:6379/0"

celery_app = Celery(
    "documind",
    broker=_redis_url,
    include=["backend.app.workers.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_ignore_result=True,
    task_default_queue="documind",
    broker_connection_retry_on_startup=True,
)
