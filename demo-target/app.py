"""Demo Target Web Service for Aegis AI Hackathon Demonstrations.

Simulates a production microservice with a health check endpoint and a triggerable defect.
Used to demonstrate end-to-end autonomous triage:
Detection (HTTP 500) -> Diagnosis (Gemini) -> Repair Brief (Devin) -> PR -> Live Recovery Verification.
"""

from __future__ import annotations

import json
import os
import sys

# In-memory service state
_STATE = {
    "broken": False,
    "version": "1.0.0",
    "service_name": "checkout-payment-api",
}

try:
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.route("/")
    def index():
        return jsonify({
            "service": _STATE["service_name"],
            "status": "degraded" if _STATE["broken"] else "operational",
            "version": _STATE["version"],
            "endpoints": ["/health", "/break", "/fix", "/checkout"],
        })

    @app.route("/health")
    def health():
        if _STATE["broken"]:
            return (
                jsonify({
                    "status": "unhealthy",
                    "service": _STATE["service_name"],
                    "error": "Unhandled NullPointer: PaymentGatewayException at checkout.py:42",
                    "affected_module": "payment_gateway/checkout.py",
                }),
                500,
            )
        return (
            jsonify({
                "status": "healthy",
                "service": _STATE["service_name"],
                "version": _STATE["version"],
                "latency_ms": 12.4,
            }),
            200,
        )

    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():
        if _STATE["broken"]:
            return (
                jsonify({
                    "error": "InternalServerError: NullPointerReference in payment gateway response parser",
                    "status_code": 500,
                }),
                500,
            )
        return jsonify({"status": "success", "order_id": "ORD-99412", "amount": 149.00}), 200

    @app.route("/break", methods=["POST"])
    def trigger_bug():
        _STATE["broken"] = True
        return (
            jsonify({
                "status": "bug_triggered",
                "broken": True,
                "message": "Planted defect triggered: /health now returns HTTP 500 InternalServerError",
            }),
            200,
        )

    @app.route("/fix", methods=["POST"])
    def resolve_bug():
        _STATE["broken"] = False
        return (
            jsonify({
                "status": "bug_resolved",
                "broken": False,
                "message": "Planted defect resolved: /health now returns HTTP 200 OK",
            }),
            200,
        )

    def run_server(port: int = 5050):
        app.run(host="0.0.0.0", port=port, debug=False)

except ImportError:
    # Fallback to standard library http.server for zero-dependency execution
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class SimpleTargetHandler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/health":
                if _STATE["broken"]:
                    self._send(500, {
                        "status": "unhealthy",
                        "service": _STATE["service_name"],
                        "error": "Unhandled NullPointer: PaymentGatewayException at checkout.py:42",
                        "affected_module": "payment_gateway/checkout.py",
                    })
                else:
                    self._send(200, {
                        "status": "healthy",
                        "service": _STATE["service_name"],
                        "version": _STATE["version"],
                        "latency_ms": 12.4,
                    })
            elif path == "/checkout":
                if _STATE["broken"]:
                    self._send(500, {"error": "InternalServerError in payment parser"})
                else:
                    self._send(200, {"status": "success", "order_id": "ORD-99412", "amount": 149.00})
            else:
                self._send(200, {
                    "service": _STATE["service_name"],
                    "status": "degraded" if _STATE["broken"] else "operational",
                    "version": _STATE["version"],
                })

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/break":
                _STATE["broken"] = True
                self._send(200, {
                    "status": "bug_triggered",
                    "broken": True,
                    "message": "Planted defect triggered: /health now returns HTTP 500",
                })
            elif path == "/fix":
                _STATE["broken"] = False
                self._send(200, {
                    "status": "bug_resolved",
                    "broken": False,
                    "message": "Planted defect resolved: /health now returns HTTP 200 OK",
                })
            else:
                self._send(404, {"error": "Not found"})

    def run_server(port: int = 5050):
        httpd = HTTPServer(("0.0.0.0", port), SimpleTargetHandler)
        httpd.serve_forever()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    run_server(port)

