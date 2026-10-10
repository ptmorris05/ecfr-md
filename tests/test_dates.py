import datetime as dt

import pytest

from ecfr_md.params import to_date


class ArrowLike:
    """Stands in for arrow/pendulum objects, which expose ``.date()``."""

    def date(self) -> dt.date:
        return dt.date(2021, 2, 3)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dt.date(2024, 1, 5), dt.date(2024, 1, 5)),
        (dt.datetime(2024, 1, 5, 13, 45), dt.date(2024, 1, 5)),
        (dt.datetime(2024, 1, 5, tzinfo=dt.UTC), dt.date(2024, 1, 5)),
        (ArrowLike(), dt.date(2021, 2, 3)),
        ("2024-01-05", dt.date(2024, 1, 5)),
        ("20240105", dt.date(2024, 1, 5)),
        (" 2024-01-05 ", dt.date(2024, 1, 5)),
        ("2024-01-05T10:30:00Z", dt.date(2024, 1, 5)),
        ("Jan 5, 2024", dt.date(2024, 1, 5)),
        ("5 January 2024", dt.date(2024, 1, 5)),
        ("01/05/2024", dt.date(2024, 1, 5)),
        ("2024/01/05", dt.date(2024, 1, 5)),
        ("March 2020", dt.date(2020, 3, 1)),
        ("2024", dt.date(2024, 1, 1)),
        ("1/2/2024", dt.date(2024, 1, 2)),  # Month first.
    ],
)
def test_to_date(value, expected):
    assert to_date(value) == expected


def test_relative_dates_are_in_the_past():
    assert to_date("yesterday") == dt.date.today() - dt.timedelta(days=1)
    assert to_date("3 weeks ago") < dt.date.today()


@pytest.mark.parametrize(
    "value", ["not a date", "2025-16-01", "20251601", "5", "123", 12.5, None, object()]
)
def test_rejects_non_dates(value):
    with pytest.raises(ValueError):
        to_date(value)
