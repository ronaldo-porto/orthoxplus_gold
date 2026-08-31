from collections.abc import Mapping
from pathlib import Path

from research_contract_guard import (
    CONTRACT_GUARD_VERSION,
    guarded_post_only_price,
    resolve_book_from_state_mapping,
)


class _Level:
    def __init__(self, price):
        self.price = price


class _Book:
    def __init__(self, bid, ask):
        self.bids = [_Level(bid)]
        self.asks = [_Level(ask)]


class _LazyBooksLike(Mapping):
    """Reproduces the important LazyBooks contract: Mapping, not dict."""
    def __init__(self, raw):
        self._raw = dict(raw)
        self.getitem_calls = 0

    def __getitem__(self, key):
        self.getitem_calls += 1
        return self._raw[key]

    def __iter__(self):
        return iter(self._raw)

    def __len__(self):
        return len(self._raw)


def test_lazy_books_mapping_is_not_dict_but_resolves_authoritative_l1():
    books = _LazyBooksLike({54: _Book(308.00, 308.45)})
    assert isinstance(books, Mapping)
    assert not isinstance(books, dict)  # exact V4.12.13 failure condition
    book = resolve_book_from_state_mapping(books, 54)
    assert book is not None
    assert book.bids[0].price == 308.00
    assert book.asks[0].price == 308.45
    assert books.getitem_calls == 1


def test_lazy_mapping_touch_drives_sell_reprice():
    books = _LazyBooksLike({54: _Book(308.00, 308.45)})
    book = resolve_book_from_state_mapping(books, 54)
    price = guarded_post_only_price(
        side="sell", original_price=308.01,
        best_bid=book.bids[0].price, best_ask=book.asks[0].price,
        tick_size=0.01, reject_streak=1,
    )
    assert abs(price - 308.46) < 1e-12


def test_lazy_mapping_touch_drives_buy_reprice():
    books = _LazyBooksLike({54: _Book(308.00, 308.45)})
    book = resolve_book_from_state_mapping(books, 54)
    price = guarded_post_only_price(
        side="buy", original_price=308.44,
        best_bid=book.bids[0].price, best_ask=book.asks[0].price,
        tick_size=0.01, reject_streak=2,
    )
    assert abs(price - 307.98) < 1e-12


def test_missing_mapping_book_fails_closed():
    books = _LazyBooksLike({})
    assert resolve_book_from_state_mapping(books, 54) is None


def test_strategy_no_longer_requires_builtin_dict_for_guard_touch():
    src = (Path(__file__).parents[1] / "agents" / "strategy" / "Strategy1_Research.py").read_text()
    assert 'RESEARCH_POLICY_VERSION = "lean_authority_cleanup_v4_15_1"' in src
    assert 'resolve_book_from_state_mapping(books, book_id)' in src
    assert 'books.get(book_id) if isinstance(books, dict) else None' not in src
    # Same bug also existed in submitted-quote L1 snapshot registration.
    assert src.count('resolve_book_from_state_mapping(books, book_id)') >= 2
    assert 'touch_source="STATE_BOOKS_MAPPING"' in src
    assert 'no_touch_reason=no_touch_reason' in src


def test_contract_guard_version_bumped():
    assert CONTRACT_GUARD_VERSION == "authoritative_l1_contract_guard_v4_12_14"
