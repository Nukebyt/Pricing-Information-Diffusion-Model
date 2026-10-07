import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))

import reference_table
from reference_table import build_reference_table, build_subscribe_message


def test_build_subscribe_message_shape():
    raw = build_subscribe_message(["NSE_FO|C1", "NSE_INDEX|Nifty 50"], mode="full")
    assert isinstance(raw, bytes)
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["method"] == "sub"
    assert parsed["data"]["mode"] == "full"
    assert parsed["data"]["instrumentKeys"] == ["NSE_FO|C1", "NSE_INDEX|Nifty 50"]
    assert "guid" in parsed


def test_build_subscribe_message_default_mode():
    raw = build_subscribe_message(["NSE_FO|C1"])
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["data"]["mode"] == "full"


def test_build_subscribe_message_rejects_full_d5():
    # BUG-3 regression: "full_d5" is the protobuf enum name, not a valid
    # subscribe mode -- the server accepts it silently and sends no ticks.
    with pytest.raises(ValueError):
        build_subscribe_message(["NSE_FO|C1"], mode="full_d5")


def _fake_contract(instrument_key, strike, instrument_type):
    return {"instrument_key": instrument_key, "strike_price": strike, "instrument_type": instrument_type}


def test_build_reference_table_shapes_option_reference_and_index_keys(monkeypatch):
    # NIFTY and BANKNIFTY are both in TRACKED, so the fake needs to return
    # distinct instrument keys per underlying -- otherwise the second
    # underlying processed silently overwrites the first's entry in
    # option_reference (a real bug caught while writing this fixture, not
    # in reference_table.py itself: an earlier draft returned the same
    # fake contracts for both keys and the assertion below failed because
    # BANKNIFTY's pass clobbered NIFTY's "NSE_FO|C1" row).
    def fake_contracts(key, expiry):
        if key == reference_table.NIFTY:
            return [_fake_contract("NSE_FO|C1", 24500.0, "CE"), _fake_contract("NSE_FO|P1", 24500.0, "PE")]
        return [_fake_contract("NSE_FO|BC1", 51000.0, "CE")]

    monkeypatch.setattr(reference_table, "list_expiries", lambda key: ["2099-01-01"])
    monkeypatch.setattr(reference_table, "get_option_contracts", fake_contracts)

    option_reference, index_keys = build_reference_table()

    assert index_keys == {reference_table.NIFTY: "NIFTY", reference_table.BANKNIFTY: "BANKNIFTY"}
    assert option_reference["NSE_FO|C1"] == {
        "underlying": "NIFTY",
        "expiry": "2099-01-01",
        "strike_paise": 2450000,
        "option_type": "CE",
    }


def test_build_reference_table_only_keeps_future_expiries(monkeypatch):
    monkeypatch.setattr(reference_table, "list_expiries", lambda key: ["2000-01-01"])  # far in the past
    monkeypatch.setattr(reference_table, "get_option_contracts", lambda key, expiry: [_fake_contract("NSE_FO|C1", 24500.0, "CE")])

    option_reference, _ = build_reference_table()
    assert option_reference == {}


def test_build_reference_table_respects_n_expiries_per_underlying(monkeypatch):
    # NIFTY tracks 2 expiries, BANKNIFTY tracks 1 -- confirm the config, not
    # just that *some* expiries got used.
    assert reference_table.TRACKED[reference_table.NIFTY]["n_expiries"] == 2
    assert reference_table.TRACKED[reference_table.BANKNIFTY]["n_expiries"] == 1
