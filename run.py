"""Breach72 command line.

    python run.py input_examples/case2_misdirected_email.json            # SAMPLE_MODE replay or live Zetaris
    python run.py input_examples/case2_misdirected_email.json --backend dev
    python run.py input_examples/case2_misdirected_email.json --out output_examples/case2.json
    python run.py --all --backend dev --out-dir output_examples
    python run.py --serve --port 8072                                     # HTTP API: GET /health, POST /run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Fixed order for --all: organisational memory is empty on the first case, then shared.
ALL_CASES = (
    "case2_misdirected_email",
    "case2b_recipient_deleted",
    "case1_lost_encrypted_laptop",
    "case3_ransomware_hr",
    "case4_processor_late_notice",
    "case5_no_detection_date",
)


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _run_one(case_path: Path, backend: str | None) -> dict:
    from app.orchestration.graph import run_case
    case = json.loads(case_path.read_text(encoding="utf-8"))
    return run_case(case, backend)


def _summary_line(result: dict) -> str:
    return f"{result.get('incident_id')} {result.get('decision')} {result.get('status')}"


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Breach72: GDPR Art. 33/34 breach investigation agents")
    ap.add_argument("case", nargs="?", type=Path, default=None,
                    help="path to a single case JSON (required unless --all)")
    ap.add_argument("--all", action="store_true",
                    help="run all input_examples cases in the fixed demo order")
    ap.add_argument("--backend", choices=["zetaris", "replay", "dev"], default=None)
    ap.add_argument("--out", type=Path, default=None, help="write single-case JSON here")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="with --all: write one JSON per case named after the input file")
    ap.add_argument("--keep-memory", action="store_true",
                    help="with --all: keep existing HMEM_DIR instead of a fresh empty temp dir")
    ap.add_argument("--serve", action="store_true", help="start the HTTP API (GET /health, POST /run)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8072)
    a = ap.parse_args(argv)
    if a.serve:
        server = make_server(a.host, a.port, a.backend)
        print(f"Breach72 API on http://{server.server_address[0]}:{server.server_address[1]}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if not a.all and a.case is None:
        ap.error("provide a case path or --all")

    if a.all:
        return _run_all(a.backend, a.out_dir, a.keep_memory)

    result = _run_one(a.case, a.backend)
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["status"] == "ok" else 2


def _run_all(backend: str | None, out_dir: Path | None, keep_memory: bool) -> int:
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    def run_cases() -> int:
        ok = True
        for name in ALL_CASES:
            path = ROOT / "input_examples" / f"{name}.json"
            result = _run_one(path, backend)
            print(_summary_line(result))
            if result.get("status") != "ok":
                ok = False
            if out_dir is not None:
                text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
                (out_dir / f"{name}.json").write_text(text, encoding="utf-8")
        return 0 if ok else 2

    if keep_memory:
        return run_cases()
    with tempfile.TemporaryDirectory(prefix="breach72-hmem-") as hmem:
        os.environ["HMEM_DIR"] = hmem
        return run_cases()


# ---------------------------------------------------------------- HTTP API (standard library only)

BACKENDS = ("zetaris", "replay", "dev")
CASE_ID_RE = re.compile(r"^[a-z0-9_]+$")
MAX_BODY_BYTES = 1_000_000


class BadRequest(Exception):
    pass


def resolve_request(body: dict, default_backend: str | None) -> tuple[dict, str | None]:
    """Turn a POST /run body into (case, backend). Raises BadRequest on invalid input."""
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    backend = body.get("backend", default_backend)
    if backend is not None and backend not in BACKENDS:
        raise BadRequest(f"backend must be one of {', '.join(BACKENDS)}")
    if "case_id" in body:
        case_id = body["case_id"]
        if not isinstance(case_id, str) or not CASE_ID_RE.match(case_id):
            raise BadRequest("case_id must match ^[a-z0-9_]+$")
        path = ROOT / "input_examples" / f"{case_id}.json"
        if not path.is_file():
            raise BadRequest(f"unknown case_id: {case_id}")
        return json.loads(path.read_text(encoding="utf-8")), backend
    case = {k: v for k, v in body.items() if k != "backend"}
    if "incident_id" not in case or "as_of" not in case:
        raise BadRequest("a case object needs at least incident_id and as_of (see input_examples/)")
    return case, backend


def make_server(host: str = "127.0.0.1", port: int = 8072, default_backend: str | None = None):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.orchestration.graph import run_case

    class Handler(BaseHTTPRequestHandler):
        server_version = "Breach72"

        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _error(self, code: int, kind: str, message: str, recoverable: bool) -> None:
            self._send(code, {"type": kind, "message": message, "recoverable": recoverable})

        def log_message(self, fmt, *args):  # request line only, never headers or env
            sys.stderr.write("api %s\n" % (fmt % args))

        def do_GET(self):
            if self.path.rstrip("/") == "/health":
                sample = os.environ.get("SAMPLE_MODE", "true").strip().lower() in ("1", "true", "yes")
                self._send(200, {"status": "ok", "sample_mode": sample})
            else:
                self._error(404, "not_found", "use GET /health or POST /run", True)

        def do_POST(self):
            if self.path.rstrip("/") != "/run":
                return self._error(404, "not_found", "use POST /run", True)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(400, "bad_request", "invalid Content-Length", True)
            if length > MAX_BODY_BYTES:
                return self._error(413, "payload_too_large", "body over 1 MB", True)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "null")
                case, backend = resolve_request(body, default_backend)
            except (ValueError, UnicodeDecodeError):
                return self._error(400, "bad_request", "body is not valid JSON", True)
            except BadRequest as exc:
                return self._error(400, "bad_request", str(exc), True)
            try:
                result = run_case(case, backend)
            except Exception as exc:  # never leak stack traces or environment values
                return self._error(500, "internal_error", type(exc).__name__, False)
            self._send(200, result)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    raise SystemExit(main())
