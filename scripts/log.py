import logging

class LogFormatter(logging.Formatter):
    # ANSI Escape Codes
    grey = "\x1b[38;20m"
    blue = "\x1b[34;20m"
    green = "\x1b[32;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    bold_pink = "\x1b[1;35m" # For logger name (markers)
    reset = "\x1b[0m"

    LEVEL_COLORS = {
        logging.DEBUG: blue,
        logging.INFO: green,
        logging.WARNING: yellow,
        logging.ERROR: red,
        logging.CRITICAL: bold_red
    }

    def format(self, record):
        # Ensure time is formatted
        record.asctime = self.formatTime(record, self.datefmt)

        # Determine colors
        level_color = self.LEVEL_COLORS.get(record.levelno, self.grey)

        # Format Logger Name (Base.Marker)
        # "marker only things after the . should be marked"
        name = record.name
        if "." in name:
            base, marker = name.split(".", 1)
            # Base (Default) + .Marker (Pink)
            fmt_name = f"{base}{self.bold_pink}/{marker}{self.reset}"
        else:
            fmt_name = name

        # Format Message
        message = record.getMessage()

        log_str = (
            f"[{record.asctime}] "
            f"[{record.threadName}/{level_color}{record.levelname}{self.reset}] "
            f"({fmt_name}): "
            f"{level_color}{message}{self.reset}"
        )

        return log_str
