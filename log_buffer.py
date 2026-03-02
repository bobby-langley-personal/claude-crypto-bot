"""
Thread-safe in-memory log ring buffer for the dashboard debug panel.

How it works:
  1. A LogBuffer object holds the last N log records in memory (deque).
  2. A LogBufferHandler is a standard Python logging.Handler that
     appends every log record into the buffer.
  3. main.py attaches the handler to the root logger so ALL log messages
     (from every module) are captured automatically.
  4. The dashboard reads buffer.get_recent(n) each refresh cycle to
     display the latest activity.

Usage (in main.py):
    buf = LogBuffer()
    logging.getLogger().addHandler(LogBufferHandler(buf))

    # later in dashboard:
    entries = buf.get_recent(15)
"""
import logging
import threading
from collections import deque
from datetime import datetime

# Rich markup colour for each log level
LEVEL_STYLE: dict[str, str] = {
    "DEBUG":    "dim white",
    "INFO":     "white",
    "WARNING":  "bold yellow",
    "ERROR":    "bold red",
    "CRITICAL": "bold red reverse",
}


class LogBuffer:
    """Stores the most recent `maxlen` log records in memory."""

    def __init__(self, maxlen: int = 500):
        self._buf:  deque  = deque(maxlen=maxlen)
        self._lock: threading.Lock = threading.Lock()

    def append(self, level: str, name: str, message: str) -> None:
        """Add a log record. Called by LogBufferHandler.emit()."""
        entry = {
            "ts":      datetime.now().strftime("%H:%M:%S"),
            "level":   level,
            # Use only the last segment of the logger name (e.g. "trading_engine"
            # instead of "crypto_bot.trading_engine") to keep the display tidy.
            "name":    name.split(".")[-1],
            "message": message,
            "style":   LEVEL_STYLE.get(level, "white"),
        }
        with self._lock:
            self._buf.append(entry)

    def get_recent(self, n: int = 20) -> list[dict]:
        """Return the last `n` records, oldest first."""
        with self._lock:
            return list(self._buf)[-n:]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


class LogBufferHandler(logging.Handler):
    """
    Standard Python logging Handler that writes into a LogBuffer.

    Attach it to the root logger to capture everything:
        root_logger = logging.getLogger()
        root_logger.addHandler(LogBufferHandler(buf))
    """

    def __init__(self, buffer: LogBuffer, level: int = logging.DEBUG):
        super().__init__(level)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(
                level   = record.levelname,
                name    = record.name,
                message = record.getMessage(),
            )
        except Exception:
            # Never let logging machinery crash the bot
            self.handleError(record)
