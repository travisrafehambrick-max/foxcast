"""The dated ticker table: companies are keyed by CIK, tickers are dated attributes."""

from datetime import date

import pytest

from foxcast.sources.tickers import SecurityKey, TickerTable, parse_sec_ticker_file


@pytest.mark.unit
def test_parse_sec_ticker_file() -> None:
    payload = b'{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}'
    assert parse_sec_ticker_file(payload) == {"AAPL": 320193}


@pytest.mark.unit
def test_lookup_respects_dates() -> None:
    table = TickerTable().observe({"ABC": 111}, as_of=date(2026, 1, 5))
    assert table.lookup("ABC", date(2026, 1, 5)) == SecurityKey(cik=111, share_class="ABC")
    assert table.lookup("ABC", date(2026, 1, 4)) is None
    assert table.lookup("ZZZ", date(2026, 1, 5)) is None


@pytest.mark.unit
def test_reused_ticker_maps_to_different_companies_by_date() -> None:
    table = (
        TickerTable()
        .observe({"ABC": 111}, as_of=date(2026, 1, 5))
        .observe({}, as_of=date(2026, 3, 2))
        .observe({"ABC": 222}, as_of=date(2026, 6, 1))
    )
    assert table.lookup("ABC", date(2026, 2, 1)) == SecurityKey(cik=111, share_class="ABC")
    assert table.lookup("ABC", date(2026, 4, 1)) is None
    assert table.lookup("ABC", date(2026, 7, 1)) == SecurityKey(cik=222, share_class="ABC")


@pytest.mark.unit
def test_ticker_change_keeps_the_same_company_key() -> None:
    table = (
        TickerTable()
        .observe({"OLD": 333}, as_of=date(2026, 1, 5))
        .observe({"NEW": 333}, as_of=date(2026, 2, 2))
    )
    old = table.lookup("OLD", date(2026, 1, 10))
    new = table.lookup("NEW", date(2026, 2, 10))
    assert old is not None and new is not None
    assert old.cik == new.cik == 333


@pytest.mark.unit
def test_observe_returns_a_new_table_and_leaves_the_old_one_unchanged() -> None:
    first = TickerTable().observe({"ABC": 111}, as_of=date(2026, 1, 5))
    second = first.observe({}, as_of=date(2026, 2, 1))
    assert first.lookup("ABC", date(2026, 3, 1)) is not None
    assert second.lookup("ABC", date(2026, 3, 1)) is None


@pytest.mark.unit
def test_observations_must_move_forward_in_time() -> None:
    table = TickerTable().observe({"ABC": 111}, as_of=date(2026, 1, 5))
    with pytest.raises(ValueError, match="after"):
        table.observe({"ABC": 111}, as_of=date(2026, 1, 4))
