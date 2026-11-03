"""IND3 V3.1 facility & passdown email settings.

Small JSON-backed config, independent of the Drax roster and shift data.
Scope is deliberately limited for this pass: we persist the settings an
Administration admin enters (distribution center, passdown recipients,
sender display name, optional Graph client id) and expose a public
subset for building the downloaded passdown .eml's To:/Subject headers.

Not yet wired in this pass (flagged, not silently skipped):
  * Sending mail via Microsoft Graph instead of a local .eml download.
The Graph client id field just persists its value for a future
integration pass.
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_FILE = "facility_settings.json"

DEFAULTS: dict = {
    "distribution_center": "IND3",
    "recipients": "",
    "sender_name": "IND3 Shift Passdown",
    "graph_client_id": "",
}


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
    for populating the passdown email's To:/Subject and sender name --
    the Graph client id stays management-only."""
    return {
        "distribution_center": settings.get("distribution_center", DEFAULTS["distribution_center"]),
        "recipients": settings.get("recipients", DEFAULTS["recipients"]),
        "sender_name": settings.get("sender_name", DEFAULTS["sender_name"]),
    }
