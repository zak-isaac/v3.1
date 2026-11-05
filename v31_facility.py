"""IND3 V3.1 facility & passdown email settings.

Small JSON-backed config, independent of the Drax roster and shift data.
Scope is deliberately limited: we persist the settings an Administration
admin enters (distribution center, passdown recipients, sender display
name) and expose a public subset for building the downloaded passdown
.eml's To:/Subject headers.

Sending mail via Microsoft Graph instead of a local .eml download was
considered and dropped -- not needed, and a half-configured integration
with nothing behind it is worse than no integration at all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SETTINGS_FILE = "facility_settings.json"

DEFAULTS: dict = {
    "distribution_center": "IND3",
    "recipients": "",
    "sender_name": "IND3 Shift Passdown",
}


def parse_recipients(value: str) -> list[str]:
    """Normalize comma/semicolon/newline-separated Outlook To recipients.

    Keep the first occurrence, reject malformed or unsafe addresses, and do
    not add any default or guessed recipients when none are configured.
    """
    if not value:
        return []
    recipients: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\r\n]+", value):
        address = token.strip()
        if not address:
            continue
        if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+", address):
            raise ValueError(f"Invalid recipient email: {address}")
        folded = address.casefold()
        if folded not in seen:
            seen.add(folded)
            recipients.append(address)
    return recipients


def _path(private_dir: Path) -> Path:
    return private_dir / SETTINGS_FILE


def load(private_dir: Path) -> dict:
    path = _path(private_dir)
    if not path.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save(private_dir: Path, settings: dict) -> dict:
    private_dir.mkdir(parents=True, exist_ok=True)
    merged = load(private_dir)
    merged.update({k: v for k, v in settings.items() if k in DEFAULTS})
    _path(private_dir).write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def public_fields(settings: dict) -> dict:
    """Subset safe to expose to any signed-in MST (not just management)
    for populating the passdown email's To:/Subject and sender name."""
    return {
        "distribution_center": settings.get("distribution_center", DEFAULTS["distribution_center"]),
        "recipients": settings.get("recipients", DEFAULTS["recipients"]),
        "sender_name": settings.get("sender_name", DEFAULTS["sender_name"]),
    }
