from rate_limit_probe.timefmt import join, ordinal, plural, secs


def test_secs():
    assert [secs(x) for x in (None, 0.5, 2, 2.04, 10.5, 10.500000001, 16.6, 125)] == [
        "?", "0.5 s", "2 s", "2 s", "10 s", "10 s", "17 s", "2 min 5 s"]


def test_words():
    assert plural(1, "request") == "1 request" and plural(2, "request") == "2 requests"
    assert join(["a", "b", "c"]) == "a, b and c"
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]
