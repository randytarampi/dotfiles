import logging
import sys
import os


class MaxLevelFilter(logging.Filter):
    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno < self.max_level


class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: "\033[0;34m",  # Blue
        logging.WARNING: "\033[0;33m",  # Yellow
        logging.ERROR: "\033[0;31m",  # Red
        logging.CRITICAL: "\033[1;31m",  # Bold Red
    }
    RESET = "\033[0m"

    def __init__(self, use_color=True):
        fmt = "%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d): %(message)s"
        super().__init__(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        self.use_color = use_color

    def format(self, record):
        orig_levelname = record.levelname
        if self.use_color and record.levelno in self.COLORS:
            color = self.COLORS[record.levelno]
            record.levelname = f"{color}{orig_levelname}{self.RESET}"
        result = super().format(record)
        record.levelname = orig_levelname
        return result


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if imported multiple times
    if not logger.handlers:
        is_tty = sys.stdout.isatty() and sys.stderr.isatty()
        use_color = not (
            os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb" or not is_tty
        )
        formatter = ColoredFormatter(use_color=use_color)

        # stdout handler (DEBUG logs and below)
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.addFilter(MaxLevelFilter(logging.WARNING))
        stdout_handler.setFormatter(formatter)

        # stderr handler (WARNING and above)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(formatter)

        logger.addHandler(stdout_handler)
        logger.addHandler(stderr_handler)


# Automatically set up logging on import
setup_logging()

# Expose standard logging functions directly
info = logging.info
warning = logging.warning
error = logging.error
critical = logging.critical
debug = logging.debug
exception = logging.exception
log = logging.log
getLogger = logging.getLogger
