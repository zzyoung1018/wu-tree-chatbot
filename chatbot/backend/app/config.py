from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
if ROOT.name == 'backend':
    ROOT = ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / '.env'), extra='ignore', hide_input_in_errors=True)
    deepseek_api_key: SecretStr = SecretStr('')
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    app_env: str = 'development'
    access_password: SecretStr = SecretStr('')
    cookie_secure: bool = False
    knowledge_dir: Path = ROOT / 'data/knowledge'
    state_dir: Path = ROOT / 'state'
    verify_answers: bool = True
    model_timeout: float = Field(default=90, gt=0)
    max_model_concurrency: int = Field(default=6, ge=1, le=64)
    qa_top_entries: int = Field(default=8, ge=1, le=20)
    explore_top_entries: int = Field(default=6, ge=1, le=15)
    trusted_proxies: list[str] = Field(default_factory=lambda: [
        '127.0.0.1', '::1', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'])
    allowed_origins: list[str] = Field(default_factory=list)
    retrieval_mode: Literal['vector'] = 'vector'
    retrieval_candidate_limit: int = Field(default=500, ge=1, le=500)
    retrieval_context_chars: int = Field(default=100000, ge=6000, le=500000)
    embedding_api_key: SecretStr = SecretStr('')
    embedding_base_url: str = ''
    embedding_model: str = 'qwen3.7-text-embedding'
    embedding_dimensions: int = 1024
    embedding_timeout: float = 30
    rerank_enabled: bool = True
    rerank_model: str = 'qwen3.7-text-rerank'
    rerank_timeout: float = 20
    rerank_enrichment: Literal['off', 'lean', 'full'] = 'off'
    theme_pool: Literal['off', 'on'] = 'off'

    @model_validator(mode='after')
    def validate_paths(self):
        from ipaddress import ip_network
        for network in self.trusted_proxies:
            ip_network(network, strict=False)
        for origin in self.allowed_origins:
            url = urlsplit(origin)
            if (url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password
                or url.path or url.query or url.fragment):
                raise ValueError('ALLOWED_ORIGINS must contain exact HTTP(S) origins without paths')
        for name in ('knowledge_dir', 'state_dir'):
            p = getattr(self, name)
            setattr(self, name, p if p.is_absolute() else ROOT / p)
        if self.app_env == 'production' and len(self.access_password.get_secret_value()) < 16:
            raise ValueError('生产环境必须配置至少 16 位的 ACCESS_PASSWORD')
        if self.embedding_dimensions not in (256, 512, 768, 1024, 1536, 2048, 2560):
            raise ValueError('Unsupported EMBEDDING_DIMENSIONS')
        if self.embedding_base_url:
            url = urlsplit(self.embedding_base_url)
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or '{' in self.embedding_base_url:
                raise ValueError('EMBEDDING_BASE_URL must be a complete HTTPS API Host without credentials or placeholders')
        return self
