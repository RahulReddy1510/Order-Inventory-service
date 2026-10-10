"""Unit tests for the RediSearch query builder (no Redis needed)."""

from app.services.search import build_query


def q(text="", **kw):
    defaults = {"category": None, "min_price": None, "max_price": None, "in_stock": False}
    return build_query(text, **{**defaults, **kw})


def test_empty_query_matches_all():
    assert q() == "*"


def test_tokens_get_prefix_matching_and_are_lowercased():
    assert q("Wire HEAD") == "wire* head*"


def test_single_char_tokens_are_not_prefixed():
    assert q("a usb") == "a usb*"


def test_query_syntax_is_neutralised():
    # Operators like @, |, -, (), {} must not reach RediSearch as syntax.
    assert q("@name:(evil) | -x {y}") == "name* evil* x y"


def test_filters():
    assert (
        q("lamp", category="home-office", min_price=10, max_price=50, in_stock=True)
        == r"lamp* @category:{home\-office} @price:[10 50] @stock:[1 +inf]"
    )


def test_open_ended_price_range():
    assert q(min_price=5) == "@price:[5 +inf]"
