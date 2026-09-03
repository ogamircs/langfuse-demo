"""A tiny in-process HTTP server that captures OTLP/HTTP protobuf trace exports.

Used to verify the Langfuse span tree without a real Langfuse instance: the Langfuse SDK
exports to `<base_url>/api/public/otel/v1/traces`, so pointing LANGFUSE_BASE_URL at this
stub lets tests decode exactly what Langfuse would have received.
"""
from __future__ import annotations

import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if self.path.endswith("/v1/traces"):
            req = ExportTraceServiceRequest()
            req.ParseFromString(body)
            self.server.spans.extend(_flatten(req))  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()
            self.wfile.write(b"")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

    def do_GET(self):  # noqa: N802
        """Serve canned JSON for Langfuse public API reads (dashboard tests)."""
        import json as _json
        body = b'{"status":"ok"}'
        path = self.path.split("?", 1)[0]
        # longest matching prefix wins, so "/prompts/<name>" beats "/prompts"
        for prefix in sorted((self.server.canned or {}), key=len, reverse=True):  # type: ignore[attr-defined]
            if path.startswith(prefix) or prefix in path:
                body = _json.dumps(self.server.canned[prefix]).encode()  # type: ignore[attr-defined]
                break
        self.server.requests.append(self.path)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence
        pass


def _attr_value(v: Any) -> Any:
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_attr_value(x) for x in v.array_value.values]
    return getattr(v, kind)


def _flatten(req: ExportTraceServiceRequest) -> list[dict[str, Any]]:
    out = []
    for rs in req.resource_spans:
        for ss in rs.scope_spans:
            for s in ss.spans:
                out.append({
                    "name": s.name,
                    "trace_id": s.trace_id.hex(),
                    "span_id": s.span_id.hex(),
                    "parent_span_id": s.parent_span_id.hex() if s.parent_span_id else None,
                    "start": s.start_time_unix_nano,
                    "end": s.end_time_unix_nano,
                    "attributes": {a.key: _attr_value(a.value) for a in s.attributes},
                    "status": s.status.code,
                })
    return out


class OtlpStub:
    def __init__(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.spans = []  # type: ignore[attr-defined]
        self.server.canned = {}  # type: ignore[attr-defined]
        self.server.requests = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def requests(self) -> list[str]:
        return self.server.requests  # type: ignore[attr-defined]

    def serve(self, path_prefix: str, payload: Any) -> None:
        self.server.canned[path_prefix] = payload  # type: ignore[attr-defined]

    @property
    def spans(self) -> list[dict[str, Any]]:
        return self.server.spans  # type: ignore[attr-defined]

    def start(self) -> "OtlpStub":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def by_type(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for s in self.spans:
            out.setdefault(str(s["attributes"].get("langfuse.observation.type", "span")), []).append(s)
        return out
