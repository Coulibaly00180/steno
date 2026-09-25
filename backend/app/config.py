from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

QUEUE_NAME = "video-ai"
# Indexing for the semantic search (catch-up, re-index after a correction):
# the worker serves it only when QUEUE_NAME is empty, so it never delays an analysis.
INDEX_QUEUE_NAME = "video-ai-index"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://videoai:videoai@postgres:5432/videoai"
    redis_url: str = "redis://redis:6379/0"
    data_dir: Path = Path("/data")
    max_video_hours: float = 6.0
    max_upload_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)
    ffprobe_timeout_seconds: int = Field(default=60, ge=1, le=600)
    ffmpeg_timeout_seconds: int = Field(default=7200, ge=1, le=21600)
    recover_interrupted_jobs_on_startup: bool = True
    # Workers processing videos in parallel (compose `deploy.replicas`): the queue estimates count them.
    worker_replicas: int = Field(default=1, ge=1, le=16)

    ollama_url: str = "http://ollama:11434"
    llm_model: str = "qwen3:8b"
    llm_timeout_seconds: int = 900
    # Hard cap on generated tokens; the final summary asks for up to ~4 000.
    llm_max_output_tokens: int = Field(default=4096, ge=64, le=8192)
    # Sent to Ollama on every call: without it the effective window is Ollama's
    # default, which silently truncates long prompts.
    llm_num_ctx: int = Field(default=8192, ge=2048, le=131072)

    # Multilingual embeddings for the semantic search (phase 4): the library
    # mixes French and English, and a question may be in either language.
    embedding_model: str = "bge-m3"

    # Speaker diarization (n°8): ONNX models baked into the image (see Dockerfile).
    diarization_models_dir: Path = Path("/opt/models/diarization")
    diarization_threads: int = Field(default=4, ge=1, le=32)

    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 5
    # A 30-second Whisper step takes seconds on a GPU and under a minute on a
    # CPU: half an hour without a single segment means the process is stuck.
    whisper_stall_timeout_seconds: int = Field(default=1800, ge=60)

    summary_chunk_chars: int = 12000
    # Small blocks: at 9 000 characters the model copied most lines untranslated,
    # at 4 000 still 13 %, at 2 500 none (see llm.translate_chunk).
    translation_chunk_chars: int = 2500

    cors_origins: str = "http://localhost:3000"

    # Watched folder (n°9): a file must keep the same size and date this long
    # before it is imported (a copy in progress grows).
    watch_stable_seconds: int = Field(default=10, ge=1, le=3600)
    scheduler_poll_seconds: int = Field(default=5, ge=1, le=300)
    # Library import (n°13): an archive with its media can be large.
    max_import_bytes: int = Field(default=50 * 1024 ** 3, gt=0)
    pg_dump_timeout_seconds: int = Field(default=3600, ge=10)

    # Live transcript of a browser recording (n°11): a preview only, the full
    # transcription runs at the end. A small model keeps up in real time on a
    # CPU and leaves the GPU memory to the worker and Ollama.
    live_whisper_model: str = "small"
    live_poll_seconds: float = Field(default=1.0, gt=0, le=30)
    # Imports from a link (n°12).
    max_download_bytes: int = Field(default=4 * 1024 ** 3, gt=0)
    download_timeout_seconds: int = Field(default=60, ge=5, le=600)
    # Links to the local network (a NAS…) are refused unless allowed: otherwise
    # any page could make Sténo query the Docker services.
    url_import_allow_private: bool = False

    # Access from the local network (n°15): the address the HTTPS proxy answers
    # on (set by the Windows installer) and its port, shown in the settings.
    lan_address: str = ""
    https_port: int = Field(default=8443, ge=1, le=65535)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


settings = Settings()
