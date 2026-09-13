from app.auth import first_n


def test_first_n():
    assert first_n([1, 2, 3], 2) == [1, 2]
