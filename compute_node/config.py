from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    node_id: str = "gpu-1"   # (use a separate node_id per machine)
    internal_api_key: str = "change-me-in-production"
    storage_root: Path = Path("./data/compute")
    capacity: int = 1
    max_upload_bytes: int = 20 * 1024 * 1024 * 1024
    callback_timeout_seconds: float = 30.0
    cors_origins: str = ""
    algo_env: Path = Path("/root/miniconda3/envs/plantsegnerf_pp")
    frames_script: Path = Path(
        "/g12213021yx/Xin_Yang/project/my-project/PNPP/pnpp/osplantsegneRF/c_Frames_pp/get_frames_sharp.py"
    )
    frames_count: int = 100

    model_config = SettingsConfigDict(env_prefix="COMPUTE_", env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
