from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LOGIN_ITEM_LABEL = "com.anfedoro.smartdictate.login"


def ensure_login_item_start() -> None:
    if not getattr(sys, "frozen", False):
        return
    logger = logging.getLogger(__name__)
    executable = Path(sys.executable).resolve()
    agent_path = Path.home() / "Library" / "LaunchAgents" / f"{LOGIN_ITEM_LABEL}.plist"
    payload = {
        "Label": LOGIN_ITEM_LABEL,
        "ProgramArguments": [str(executable)],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": ["Aqua"],
        "WorkingDirectory": str(executable.parent),
    }
    try:
        current = None
        if agent_path.exists():
            current = plistlib.loads(agent_path.read_bytes())
        if current != payload:
            agent_path.parent.mkdir(parents=True, exist_ok=True)
            agent_path.write_bytes(plistlib.dumps(payload, sort_keys=True))
    except Exception as exc:
        logger.warning("Failed to write launch agent plist: %s", exc)
        return
    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"{domain}/{LOGIN_ITEM_LABEL}"
    try:
        bootstrap = subprocess.run(
            ["launchctl", "bootstrap", domain, str(agent_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if bootstrap.returncode != 0:
            subprocess.run(
                ["launchctl", "bootout", service],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["launchctl", "bootstrap", domain, str(agent_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["launchctl", "enable", service],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning("Failed to register login item launch agent: %s", exc)
