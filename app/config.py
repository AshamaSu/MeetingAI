"""环境配置。容器内用环境变量；本地开发自动读项目根 .env。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env():
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


class Settings:
    def __init__(self):
        self.speakr_url = os.environ.get("SPEAKR_URL", "http://127.0.0.1:8899").rstrip("/")
        # 多账号:逗号分隔的 token 列表(每个对应一个 speakr 用户),
        # 请求 403/401 时自动换下一个;单账号用 SPEAKR_TOKEN 即可
        self.speakr_tokens = [t.strip() for t in os.environ.get("SPEAKR_TOKENS", "").split(",") if t.strip()]
        if not self.speakr_tokens:
            self.speakr_tokens = [os.environ.get("SPEAKR_TOKEN", "")]
        # Webhook secret(s)：创建 webhook 时只返回一次；轮换宽限期可配 previous
        self.webhook_secret = os.environ.get("WEBHOOK_SECRET", "")
        self.webhook_secret_previous = os.environ.get("WEBHOOK_SECRET_PREVIOUS", "")
        self.db_path = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data" / "sensitive.db")))
        self.wordlist_path = Path(os.environ.get("WORDLIST_PATH", str(BASE_DIR / "wordlists" / "sensitive.csv")))
        self.signature_tolerance = int(os.environ.get("SIGNATURE_TOLERANCE", "300"))
        self.summary_retry_seconds = int(os.environ.get("SUMMARY_RETRY_SECONDS", "30"))
        self.max_attempts = int(os.environ.get("MAX_ATTEMPTS", "8"))
        self.worker_interval = float(os.environ.get("WORKER_INTERVAL", "2"))
        self.worker_enabled = os.environ.get("WORKER_ENABLED", "1").lower() not in ("0", "false", "no")
        # 浏览器可达的服务基址（如 http://82.156.1.74:8898）；为空则 widget 回退 notes 通道
        self.api_url_public = os.environ.get("API_URL_PUBLIC", "").rstrip("/")
        # LLM 精判（OpenAI 兼容 /chat/completions）；不启用则纯词表
        self.llm_enabled = os.environ.get("LLM_ENABLED", "").lower() in ("1", "true", "yes")
        self.llm_base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.llm_api_key = os.environ.get("LLM_API_KEY", "")
        self.llm_model = os.environ.get("LLM_MODEL", "qwen")
        self.llm_timeout = float(os.environ.get("LLM_TIMEOUT", "20"))
        # /alerts 看板 Basic Auth；留空则不启用看板
        self.alert_user = os.environ.get("ALERT_USER", "")
        self.alert_pass = os.environ.get("ALERT_PASS", "")
        # 提示文案语言（notes/summary 块；speakr UI 语言：zh/en/de/es/fr/pt/ru）
        self.notify_lang = os.environ.get("NOTIFY_LANG", "zh")
        self.port = int(os.environ.get("PORT", "8898"))

    @property
    def webhook_secrets(self):
        """全部接受的 webhook secret:主 secret + 轮换宽限 + 多用户追加(csv)。"""
        extra = [s.strip() for s in os.environ.get("WEBHOOK_SECRETS", "").split(",") if s.strip()]
        s = [self.webhook_secret]
        if self.webhook_secret_previous:
            s.append(self.webhook_secret_previous)
        return [x for x in s + extra if x]


settings = Settings()
