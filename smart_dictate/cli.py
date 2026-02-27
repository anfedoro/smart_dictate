from __future__ import annotations

import multiprocessing
import multiprocessing.spawn
import sys

from smart_dictate.app import DictateApp
from smart_dictate.logging_setup import setup_logging


def main() -> int:
    if getattr(sys, "frozen", False):
        multiprocessing.freeze_support()
        if multiprocessing.spawn.is_forking(sys.argv):
            return 0
        if multiprocessing.parent_process() is not None:
            return 0
        if multiprocessing.current_process().name != "MainProcess":
            return 0
    setup_logging()
    DictateApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
