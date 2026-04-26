"""Entrypoint for hosting panels (Pterodactyl/wispbyte) that expect `python run.py`.

Just delegates to the Click CLI with `monitor` as the default subcommand.
You can override by passing args: `python run.py resolve --date ... --slug ...`.
"""
from __future__ import annotations

import sys

from src.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("monitor")
    main()
