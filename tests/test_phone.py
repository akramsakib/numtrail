import pytest

from numtrail.phone import COUNTRIES, InvalidPhone, parse


@pytest.mark.parametrize(
    "raw,dial,national,iso",
    [
        ("+60123456789", "60", "123456789", "MY"),
        ("60123456789", "60", "123456789", "MY"),
        ("+60 12-345 6789", "60", "123456789", "MY"),
        ("+15550100", "1", "5550100", "US"),
        ("+33644637111", "33", "644637111", "FR"),
        ("+44 7911 123456", "44", "7911123456", "GB"),
        ("+971501234567", "971", "501234567", "AE"),
        ("+998901234567", "998", "901234567", "UZ"),
    ],
)
def test_parse_forms(raw, dial, national, iso):
    phone = parse(raw)
    assert (phone.dial, phone.national, phone.iso) == (dial, national, iso)
    assert phone.e164 == f"+{dial}{national}"
    assert phone.digits == f"{dial}{national}"


def test_explicit_country_code_wins():
    phone = parse("0644637111", "33")
    assert phone.e164 == "+33644637111"
    assert phone.iso == "FR"


def test_longest_country_code_match():
    # 998 (Uzbekistan) must beat 9 (not assigned) and 99 (not assigned)
    assert parse("+998901234567").dial == "998"


@pytest.mark.parametrize("raw", ["", "   ", "abc", "+1", "+1234567890123456789"])
def test_rejects_nonsense(raw):
    with pytest.raises(InvalidPhone):
        parse(raw)


def test_every_iso_code_is_two_letters():
    assert all(len(v) == 2 and v.isalpha() for v in COUNTRIES.values())
