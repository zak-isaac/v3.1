"""Manual, read-only smoke test for the local Shift Happenings AI.

Run from the repository root:
    python tests/manual_ai_smoke.py

This does not load the app, open a database, or modify any report.
It calls only Ollama on loopback and prints a proposed rewrite.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_remark_rewriter


def main() -> int:
    readiness = ai_remark_rewriter.check_ai_available()
    print("Local AI status:", readiness)
    if not readiness["available"]:
        return 1

    original = (
        "Head servo hard fault had to reach out ranpak and they helped diagnose "
        "the problem; Assisted with trouble shooting fill head not responding "
        "to HMI, located the driver for the fill and reset the driver connected "
        "to fill head and restarted VOIDFILL, machine return to normal operations"
    )
    print("\nOriginal remark:\n", original)
    result = ai_remark_rewriter.suggest_rewrite(
        original, asset_id="VOIDFILL", wo_number="28044566"
    )
    print("\nAI result:\n", result)
    print("\nNo report or database was changed.")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
