from __future__ import annotations

import functools
import logging
import time
from typing import Callable, Optional, Type, Tuple, TypeVar

F = TypeVar("F", bound=Callable)


def build_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)-8s] %(name)s - %(message)s")
        )
        logger.addHandler(handler)
    return logger


def retry(
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger: Optional[logging.Logger] = None,
) -> Callable[[F], F]:
    """Decorator: retry with exponential backoff."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        if logger:
                            logger.error(
                                "%s failed after %d attempts: %s",
                                fn.__qualname__,
                                max_attempts,
                                exc,
                            )
                        raise
                    if logger:
                        logger.warning(
                            "%s attempt %d/%d failed: %s. Retrying in %.1fs…",
                            fn.__qualname__,
                            attempt,
                            max_attempts,
                            exc,
                            wait,
                        )
                    time.sleep(wait)
                    wait *= backoff

        return wrapper  # type: ignore[return-value]

    return decorator


def retry_call(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger: Optional[logging.Logger] = None,
    **kwargs,
):
    wait = delay
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            if logger:
                logger.warning(
                    "Attempt %d/%d failed for %s: %s. Retrying in %.1fs…",
                    attempt,
                    max_attempts,
                    getattr(fn, "__name__", str(fn)),
                    exc,
                    wait,
                )
            time.sleep(wait)
            wait *= backoff
    raise last_exc  # type: ignore[misc]
