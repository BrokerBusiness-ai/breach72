import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "submission_check", Path(__file__).resolve().parents[1] / "scripts" / "submission_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def test_placeholders_accepted():
    text = "SAMPLE_MODE=true\nZETARIS_API_KEY=your-zetaris-api-key\nZETARIS_ORG_ID=your-org-id\nNVIDIA_API_KEY=\n"
    assert sc.env_placeholders_ok(text) == []


def test_real_looking_key_rejected():
    text = "# comment\nNVIDIA_API_KEY=abc123realvalue\nZETARIS_ORG_ID=acme\n"
    assert sc.env_placeholders_ok(text) == ["NVIDIA_API_KEY", "ZETARIS_ORG_ID"]


def test_secret_patterns():
    assert sc.find_secrets("key = nvapi-" + "A" * 30)
    assert sc.find_secrets("sk-" + "a1" * 12)
    assert sc.find_secrets("ghp_" + "x" * 30)
    assert sc.find_secrets("-----BEGIN RSA PRIVATE KEY-----")
    assert not sc.find_secrets("NVIDIA_API_KEY=your-nvidia-api-key; risk-score; sk-short")


def test_long_dash_count():
    assert sc.find_long_dashes("a" + chr(0x2013) + "b" + chr(0x2014) + "c") == 2
    assert sc.find_long_dashes("plain - ascii -- hyphens") == 0


def test_read_text_skips_binary(tmp_path):
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\x00\x01\x02")
    text = tmp_path / "x.txt"
    text.write_text("hello", encoding="utf-8")
    assert sc.read_text(binary) is None
    assert sc.read_text(text) == "hello"
