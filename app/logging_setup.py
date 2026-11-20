"""Structured JSON logging (structlog) with secret redaction."""
import logging
import sys

import structlog

from app.redaction import redact


def _redact_processor(_, __, event_dict):
    return redact(event_dict, max_len=4000)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso"), _redact_processor,
                    structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


log = structlog.get_logger("workiq")
