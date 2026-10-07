import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from paths import safe_filename


def test_colons_and_spaces_never_reach_a_filename():
    # BUG-8: a ':' in a Windows filename silently creates an NTFS alternate data stream
    assert safe_filename("PLACEBO 2026-10-06 10:30 IST") == "PLACEBO_2026-10-06_10_30_IST"
    assert ":" not in safe_filename("a:b/c\d*e?f\"g<h>i|j")


def test_ordinary_names_survive_and_runs_collapse():
    assert safe_filename("RBI MPC Oct 2026") == "RBI_MPC_Oct_2026"
    assert safe_filename("CPI September 2026 [pre_close]") == "CPI_September_2026_pre_close"
