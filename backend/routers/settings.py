import os

from dotenv import load_dotenv, set_key, unset_key
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import llm
from config import Settings, resolved_openrouter_model, settings

router = APIRouter()

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


def _ensure_env():
    """Создать .env при отсутствии и подгрузить его в os.environ."""
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w") as f:
            f.write("")
    load_dotenv(ENV_PATH, override=True)


class SettingsUpdate(BaseModel):
    """Частичный PUT: присылаются ТОЛЬКО изменённые поля (§5 спеки)."""
    api_key: str | None = None
    model: str | None = None
    confidence_threshold: float | None = None


@router.get("")
def get_settings():
    """Настройки + capabilities (§5): фронт скрывает поля, backend enforce'ит."""
    _ensure_env()
    is_gateway = settings.LLM_PROVIDER == "gateway"
    return {
        "provider": settings.LLM_PROVIDER,
        "can_edit_model": not is_gateway,
        "cost_available": not is_gateway,
        # gateway: ключ не нужен → true, чтобы UI не показывал призыв ввода (§5)
        "api_key_set": True if is_gateway
        else bool(os.getenv("OPENROUTER_API_KEY", "").startswith("sk-")),
        # модель — через алиас-цепочку §1 (не ручной os.getenv — дрейф логики);
        # свежий Settings() перечитывает environ, обновлённый _ensure_env()
        # Settings(_env_file=ENV_PATH): роутер держит абсолютный ENV_PATH для
        # записи через set_key, и читать он обязан тот же файл. Без этого свежий
        # Settings() читал бы backend/.env в обход подменённого в тестах ENV_PATH.
        "model": settings.GATEWAY_MODEL if is_gateway
        else resolved_openrouter_model(Settings(_env_file=ENV_PATH)),
        "confidence_threshold": float(os.getenv("CONFIDENCE_THRESHOLD", "0.7")),
    }


@router.put("")
def update_settings(data: SettingsUpdate):
    """PUT: в gateway-режиме api_key/model запрещены (403); openrouter — пересборка провайдера."""
    _ensure_env()
    if settings.LLM_PROVIDER == "gateway" and (data.api_key is not None or data.model is not None):
        raise HTTPException(status_code=403,
                            detail="В контурном режиме ключ и модель задаются при деплое")
    llm_changed = False
    if data.api_key is not None:
        set_key(ENV_PATH, "OPENROUTER_API_KEY", data.api_key)
        os.environ["OPENROUTER_API_KEY"] = data.api_key
        llm_changed = True
    if data.model is not None:
        # namespaced-ключ: запись в легаси AI_MODEL перекрывалась бы приоритетом
        # OPENROUTER_MODEL из env (алиас-цепочка §1) — PUT молча не действовал бы
        set_key(ENV_PATH, "OPENROUTER_MODEL", data.model)
        os.environ["OPENROUTER_MODEL"] = data.model
        # зачистка легаси-строки: не оставляем два источника модели в одном .env
        unset_key(ENV_PATH, "AI_MODEL")
        os.environ.pop("AI_MODEL", None)
        llm_changed = True
    if data.confidence_threshold is not None:
        set_key(ENV_PATH, "CONFIDENCE_THRESHOLD", str(data.confidence_threshold))
        os.environ["CONFIDENCE_THRESHOLD"] = str(data.confidence_threshold)
    if llm_changed:
        # Атомарная замена провайдера (§5): свежие Settings() перечитывают
        # обновлённый os.environ — латентный баг «ключ не действует до рестарта» закрыт.
        # Синглтон config.settings обновляем точечно, чтобы GET видел актуальный ключ.
        fresh = Settings()
        settings.OPENROUTER_API_KEY = fresh.OPENROUTER_API_KEY
        settings.OPENROUTER_MODEL = fresh.OPENROUTER_MODEL
        llm.init_provider(fresh)
    return {"message": "Настройки сохранены"}
