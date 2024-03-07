import logging
import sys
from logging.handlers import WatchedFileHandler

from aview_hpc._cli import main


def setup_logging(level='INFO'):
    handler = WatchedFileHandler('aview_hpc.log')
    formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s:%(message)s', '%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers for this file name, if any
    for old_handler in [h for h in root.handlers if (isinstance(h, WatchedFileHandler)
                                                     and h.baseFilename == handler.baseFilename)]:
        root.handlers.remove(old_handler)
    root.addHandler(handler)
    return logging.getLogger(__name__)


def exception_hook(exc_type, exc_value, exc_traceback):
    logging.getLogger().error("Uncaught exception",
                              exc_info=(exc_type, exc_value, exc_traceback))


if __name__ == '__main__':
    setup_logging()
    sys.excepthook = exception_hook
    main()
