from app import reports
from app.security import device_canonical, device_sign, hash_password, verify_password, new_device_secret


def test_sign_and_verify_roundtrip(tmp_path):
    s = reports.Signer(tmp_path / "k.pem")
    canon, sig, kid = s.sign({"a": 1.234567, "b": [1, 2, {"z": "x"}]})
    assert reports.verify(s.public_pem, canon, sig)
    assert not reports.verify(s.public_pem, canon.replace("1.23", "9.99"), sig)
    assert kid == reports.Signer(tmp_path / "k.pem").key_id  # key persisted


def test_canonical_is_order_independent():
    assert reports.canonical({"b": 1, "a": 2.0}) == reports.canonical({"a": 2, "b": 1})


def test_password_hashing():
    h = hash_password("Correct-Horse-9")
    assert verify_password("Correct-Horse-9", h) and not verify_password("wrong", h)


def test_device_signature_changes_with_any_field():
    sec = new_device_secret()
    base = device_sign(sec, device_canonical("n", 1, 100, 230.0, 0.9, 50.0, 1, [10, 20]))
    assert base != device_sign(sec, device_canonical("n", 1, 100, 230.0, 0.9, 50.0, 1, [10, 21]))
    assert base != device_sign(sec, device_canonical("n", 2, 100, 230.0, 0.9, 50.0, 1, [10, 20]))
