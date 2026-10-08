"""Logging for a process (the API, the worker), set up once at its start."""

import logging

# They log each request's URL, and ARC_RPC_URL carries the team's token in its path: a line of
# theirs would be the secret. Their warnings and errors still show.
_URL_LOGGERS = ("httpx", "httpcore")


def configure(level: str) -> None:
    logging.basicConfig(level=level)
    for name in _URL_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelName(level)))
