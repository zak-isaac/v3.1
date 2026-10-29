"""Shared test isolation. No production files or real roster data are touched.

See v31_roster._source() for the IND3_V31_TEST_SKIP_ONEDRIVE flag: it only
skips the hardcoded production OneDrive Drax path lookup, leaving the
IND3_V31_ROSTER_PATH / ASSOCIATES_PATH override mechanism and the private-dir
fallback choices completely untouched. Production behavior (the flag unset)
is unchanged.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_roster_source(monkeypatch):
    """Keep every test on synthetic roster fixtures, never the real Drax file.

    Individual tests remain free to set IND3_V31_ROSTER_PATH / ASSOCIATES_PATH
    themselves to exercise that override explicitly; this fixture only takes
    away the machine-specific OneDrive collision, nothing else.
    """
    monkeypatch.setenv("IND3_V31_TEST_SKIP_ONEDRIVE", "1")
