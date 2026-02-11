"""HTTP helpers: session with retries, rate limiting and user agent."""
import time
import logging
from typing import Callable
import requests
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from .config import cfg

logger = logging.getLogger(__name__)


class RateLimitedSession(requests.Session):
    def __init__(self, rate_limit_seconds: float = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rate_limit_seconds = cfg.rate_limit_seconds if rate_limit_seconds is None else rate_limit_seconds
        self._last_request = 0.0
        self.headers.update({"User-Agent": cfg.user_agent})

    def request(self, *args, **kwargs):
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self.rate_limit_seconds:
            to_sleep = self.rate_limit_seconds - elapsed
            logger.debug("Rate limiting: sleeping %.2fs", to_sleep)
            time.sleep(to_sleep)
        resp = super().request(*args, **kwargs)
        self._last_request = time.time()
        return resp


def resilient_get(session: RateLimitedSession, url: str, **kwargs) -> requests.Response:
    @retry(wait=wait_exponential(multiplier=0.5, max=10),
           stop=stop_after_attempt(5),
           retry=retry_if_exception_type(Exception))
    def _get(u):
        logger.debug("GET %s", u)
        r = session.get(u, timeout=20, **kwargs)
        r.raise_for_status()
        return r

    return _get(url)
