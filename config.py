from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    allowed_user_ids: str
    database_url: str = "sqlite+aiosqlite:///./poster.db"

    telethon_api_id: int = 0
    telethon_api_hash: str = ""
    telethon_phone: str = ""
    telethon_session_name: str = "userbot"

    @property
    def allowed_user_id_set(self) -> set[int]:
        return {int(uid.strip()) for uid in self.allowed_user_ids.split(",") if uid.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
