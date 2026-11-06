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


LEGACY_SETTINGS_MAP = {
    "email_to": "recipients",
    "email_from_name": "sender_name",
    "dc_number": "distribution_center",
}


def import_legacy_settings(private_dir: Path, legacy_db_path, *, force: bool = False) -> dict:
    """Carry forward facility/email settings an admin already configured in
    the old V2/V3 app's key-value `settings` table, so a cutover doesn't
    quietly blank out the passdown recipient list.

    Deliberately narrow: only recipients/sender_name/distribution_center
    map onto V3.1's settings model. Everything else in the old table
    (Teams webhook, Graph client id, lead PIN hash, Drax path, AI toggle)
    belongs to systems V3.1 either replaced or dropped entirely on purpose,
    and is left alone here.

    Refuses to overwrite a `recipients` value an admin already entered in
    V3.1 unless force=True -- this is a one-time cutover bridge, not a
    standing sync, and a real admin's later choice must win over a replay.
    """
    import sqlite3

    current = load(private_dir)
    if current.get("recipients") and not force:
        return {"applied": False,
                "reason": "recipients already configured; pass force=True to overwrite",
                "current": public_fields(current)}

    with sqlite3.connect(f"file:{legacy_db_path}?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        legacy = {row["key"]: row["value"] for row in src.execute("SELECT key,value FROM settings")}

    update = {}
    for legacy_key, settings_key in LEGACY_SETTINGS_MAP.items():
        value = legacy.get(legacy_key)
        if value:
            update[settings_key] = value.strip('"')  # dc_number was stored as a JSON string literal

    if "recipients" in update:
        parse_recipients(update["recipients"])  # raises ValueError if malformed; never save bad addresses

    saved = save(private_dir, update)
    return {"applied": True, "imported_keys": sorted(update), "settings": public_fields(saved)}
