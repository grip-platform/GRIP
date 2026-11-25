"""
Centralized logging configuration for copilot modules.

Provides a factory function for consistent logger configuration across
all copilot modules, ensuring uniform formatting and appropriate log levels.

Usage:
    from aide.logger import get_logger
    logger = get_logger(__name__)

    # Use appropriate levels:
    logger.debug("Routine step execution")  # High-frequency operations
    logger.info("Ritual enabled: morning-brief")  # State changes
    logger.warning("Token nearing expiry")  # Recoverable issues
    logger.error("API call failed")  # Fatal/serious issues
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Default log level from environment
DEFAULT_LEVEL = os.environ.get("COPILOT_LOG_LEVEL", "INFO").upper()

# Log file location (optional)
LOG_FILE = os.environ.get("COPILOT_LOG_FILE", "")

# Track if we've configured the root copilot logger
_configured = False


def configure_root_logger(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    format_string: Optional[str] = None,
) -> None:
    """
    Configure the root 'copilot' logger.

    Called automatically on first get_logger() call, but can be called
    explicitly to override defaults.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Default: INFO or COPILOT_LOG_LEVEL env.
        log_file: Optional file path to write logs to. Default: COPILOT_LOG_FILE env or None.
        format_string: Custom format string. Default uses standard format.
    """
    global _configured

    log_level = getattr(logging, (level or DEFAULT_LEVEL), logging.INFO)
    fmt = format_string or "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Get the copilot root logger
    root = logging.getLogger("copilot")
    root.setLevel(log_level)

    # Clear existing handlers to avoid duplicates on reconfigure
    root.handlers.clear()

    # Console handler (stderr)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(log_level)
    console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(console)

    # File handler (if configured)
    file_path = log_file or LOG_FILE
    if file_path:
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(file_path)
            file_handler.setLevel(log_level)
            file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
            root.addHandler(file_handler)
        except (OSError, PermissionError) as e:
            root.warning("Could not create log file %s: %s", file_path, e)

    # Don't propagate to root logger (avoid duplicate logs)
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a copilot module.

    Ensures consistent configuration across all copilot loggers.
    The logger hierarchy follows the copilot.* namespace.

    Args:
        name: Module name, typically __name__

    Returns:
        Configured logger instance

    Example:
        from aide.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Ritual started: %s", ritual_id)
    """
    global _configured

    # Configure root logger on first use
    if not _configured:
        configure_root_logger()

    # Ensure name is in copilot namespace
    if not name.startswith("copilot"):
        name = f"copilot.{name}"

    return logging.getLogger(name)


