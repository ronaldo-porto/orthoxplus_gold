"""One place that keeps the frozen-file digest pins honest.

Five test files pin the sha256 of ``taos/im/validator/trade.py`` -- the validator code we are
scored by -- so that a silent change to it cannot pass unnoticed.  From 2026-09-02 to 2026-09-18
all five carried ``137a4a7f...``, which matches NO revision the file has ever had (its only two
commits give ``d9b3b00c...`` and ``3d5c8f59...``).  Because the assertion sat in the middle of a
longer test, every assertion after it had been dark for fifteen builds.

This file asserts the two things that failure needed: the copies agree with each other, and they
agree with the file on disk.  Whoever updates the pin next has one test telling them all five.
"""
import re
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).parents[1]
VALIDATOR_TRADE = ROOT / "taos" / "im" / "validator" / "trade.py"
PIN = re.compile(r'^VALIDATOR_TRADE_SHA256 = "([0-9a-f]{64})"$', re.M)

PINNING_FILES = (
    "test_research_v4_15_2_acquisition_quality.py",
    "test_research_v4_15_3_entry_ev_calibration.py",
    "test_research_v4_16_0_simplified_authority.py",
    "test_research_v4_16_1_p0_runtime.py",
    "test_research_v4_16_2_economics_contract.py",
)


def _pins():
    found = {}
    for name in PINNING_FILES:
        src = (ROOT / "tests" / name).read_text(encoding="utf-8")
        m = PIN.search(src)
        assert m, f"{name} no longer carries a VALIDATOR_TRADE_SHA256 pin"
        found[name] = m.group(1)
    return found


def test_every_copy_of_the_pin_agrees():
    values = set(_pins().values())
    assert len(values) == 1, f"the pin has drifted between files: {_pins()}"


def test_the_pin_matches_the_file_on_disk():
    digest = sha256(VALIDATOR_TRADE.read_bytes()).hexdigest()
    pins = _pins()
    assert set(pins.values()) == {digest}, (
        f"pinned {sorted(set(pins.values()))} but taos/im/validator/trade.py is {digest}. "
        "Either the validator changed under us -- read the diff before re-pinning -- or the pin "
        "was never right."
    )


def test_the_digest_check_is_its_own_test_in_every_file():
    """The 2026-09 failure was masking, not the pin itself: keep the assertion unshared."""
    for name in PINNING_FILES:
        src = (ROOT / "tests" / name).read_text(encoding="utf-8")
        assert "def test_validator_trade_is_frozen():" in src, name
        assert src.count("== VALIDATOR_TRADE_SHA256") == 1, (
            f"{name}: the digest assertion must appear once, in its own test"
        )
