from __future__ import annotations

import logging
import subprocess

SERVICE_NAME = "com.anfedoro.smartdictate"
ACCOUNT_NAME = "postprocess-api-key"


def get_postprocess_api_key() -> str | None:
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                ACCOUNT_NAME,
                "-s",
                SERVICE_NAME,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    key = result.stdout.strip()
    return key or None


def set_postprocess_api_key(api_key: str) -> None:
    try:
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                ACCOUNT_NAME,
                "-s",
                SERVICE_NAME,
                "-w",
                api_key,
                "-U",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        logging.getLogger(__name__).warning(
            "Failed to save API key to Keychain: %s",
            detail,
        )
        raise RuntimeError("Failed to save API key to Keychain.") from exc


def delete_postprocess_api_key() -> None:
    try:
        subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                ACCOUNT_NAME,
                "-s",
                SERVICE_NAME,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return
