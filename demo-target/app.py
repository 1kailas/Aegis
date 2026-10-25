"""Webliz Store — Demo Target Web Application for Aegis AI Outage & Auto-PR Demonstrations.

Features:
1. Real, responsive E-Commerce storefront with interactive shopping cart and checkout.
2. Planted defect: calculate_discounted_total raises ZeroDivisionError when coupon_rate=100.
3. Automatically captures Python tracebacks and reports error logs to Aegis Control Plane.
4. Autonomous PR repair loop closes when fix is applied and verified.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from typing import Any

import httpx
from flask import Flask, jsonify, render_template_string, request

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_LOG = logging.getLogger("webliz.store")

AEGIS_CONTROL_PLANE = os.environ.get("AEGIS_URL", "http://127.0.0.1:8080")

# In-memory application state
_STATE = {
    "broken": False,
    "version": "1.2.0",
    "service_name": "Webliz Cloud Store",
    "last_error": None,
    "last_pr_url": None,
    "total_orders": 42,
}

app = Flask(__name__)


def calculate_discounted_total(base_amount: float, discount_percent: float) -> float:
    """Calculates final order total applying promotional discount."""
    if _STATE["broken"]:
        # Planted defect: ZeroDivisionError occurs when discount_percent is 100!
        multiplier = 100 / (100 - discount_percent)
        return round(base_amount * multiplier, 2)
    # Correct calculation:
    discount_fraction = max(0.0, min(100.0, discount_percent)) / 100.0
    return round(base_amount * (1.0 - discount_fraction), 2)


def report_error_to_aegis(error_msg: str, tb_str: str) -> dict[str, Any]:
    """Sends application exception logs directly to Aegis Control Plane to trigger Auto-PR."""
    payload = {
        "app_name": _STATE["service_name"],
        "service": "checkout-payment-api",
        "error": error_msg,
        "traceback": tb_str,
        "affected_file": "demo-target/app.py",
        "url": "http://127.0.0.1:5050",
        "repo_url": "https://github.com/1kailas/Aegis",
        "reproduction_steps": [
            "User adds item to cart on Webliz Store",
            "Applies 100% promotional discount coupon (CRASH100)",
            "Clicks 'Complete Purchase' -> triggers ZeroDivisionError in calculate_discounted_total()",
        ],
    }
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(f"{AEGIS_CONTROL_PLANE}/api/incidents/report", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                _STATE["last_pr_url"] = data.get("pull_request_url")
                _LOG.info("Successfully reported outage to Aegis AI. PR: %s", data.get("pull_request_url"))
                return data
    except Exception as exc:
        _LOG.warning("Could not reach Aegis Control Plane at %s: %s", AEGIS_CONTROL_PLANE, exc)
    return {}


# ------------------------------------------------------------------ HTML Storefront UI
STOREFRONT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Webliz Cloud Store — Aegis AI Demo Target</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111726;
      --card-hover: #172033;
      --border: rgba(255, 255, 255, 0.08);
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --ok: #10b981;
      --warn: #f59e0b;
      --crit: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding-bottom: 60px;
    }
    /* Sticky Outage / Developer Bar */
    .dev-bar {
      background: #141b2d;
      border-bottom: 1px solid var(--border);
      padding: 10px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    }
    .dev-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 20px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .badge-ok { background: rgba(16,185,129,0.15); color: var(--ok); border: 1px solid rgba(16,185,129,0.3); }
    .badge-crit { background: rgba(239,68,68,0.15); color: var(--crit); border: 1px solid rgba(239,68,68,0.3); }
    .btn {
      padding: 6px 14px;
      border-radius: 6px;
      border: 1px solid var(--border);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
      color: var(--text);
      background: #1e293b;
    }
    .btn:hover { background: #334155; }
    .btn-danger { background: #dc2626; border-color: #ef4444; color: #fff; }
    .btn-danger:hover { background: #b91c1c; }
    .btn-ok { background: #059669; border-color: #10b981; color: #fff; }
    .btn-ok:hover { background: #047857; }
    .btn-primary { background: #0284c7; border-color: #38bdf8; color: #fff; }
    .btn-primary:hover { background: #0369a1; }
    
    /* Header */
    header {
      padding: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      max-width: 1100px;
      margin: 0 auto;
      border-bottom: 1px solid var(--border);
    }
    .brand { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; display: flex; align-items: center; gap: 8px; }
    .brand span { color: var(--accent); }
    
    /* Main Content */
    .container { max-width: 1100px; margin: 30px auto; padding: 0 24px; }
    .hero { text-align: center; margin-bottom: 40px; }
    .hero h1 { font-size: 32px; font-weight: 800; margin-bottom: 8px; letter-spacing: -0.5px; }
    .hero p { color: var(--muted); font-size: 15px; }
    
    /* Product Grid */
    .product-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 40px; }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .card:hover { transform: translateY(-3px); border-color: rgba(56,189,248,0.4); }
    .card-icon { font-size: 36px; margin-bottom: 12px; }
    .card-title { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
    .card-desc { font-size: 13px; color: var(--muted); margin-bottom: 16px; min-height: 40px; }
    .card-bottom { display: flex; justify-content: space-between; align-items: center; }
    .price { font-size: 20px; font-weight: 800; color: #fff; }
    
    /* Checkout Card */
    .checkout-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      max-width: 580px;
      margin: 0 auto;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    }
    .checkout-title { font-size: 18px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between; }
    .form-group { margin-bottom: 14px; }
    .form-group label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 6px; }
    .form-control {
      width: 100%;
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 14px;
      color: #fff;
      font-size: 14px;
    }
    .form-control:focus { outline: none; border-color: var(--accent); }
    .order-summary { background: rgba(255,255,255,0.03); border-radius: 8px; padding: 14px; margin: 16px 0; font-size: 13px; }
    .summary-row { display: flex; justify-content: space-between; margin-bottom: 6px; color: var(--muted); }
    .summary-row.total { font-size: 15px; font-weight: 800; color: #fff; border-top: 1px solid var(--border); padding-top: 8px; margin-top: 8px; }
    
    /* Alert Banners */
    .alert { padding: 14px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; display: none; }
    .alert-crit { background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); color: #fca5a5; }
    .alert-ok { background: rgba(16,185,129,0.15); border: 1px solid rgba(16,185,129,0.3); color: #86efac; }
  </style>
</head>
<body>

  <!-- Sticky SRE / Hackathon Demo Toolbar -->
  <div class="dev-bar">
    <div style="display:flex; align-items:center; gap:12px;">
      <span class="brand" style="font-size:16px;">⚡ Webliz <span>Store</span></span>
      <span id="healthBadge" class="dev-badge badge-ok">● Service 200 OK</span>
      <span style="font-size:11px; color:var(--muted);">Port: 5050</span>
    </div>

    <!-- Outage Ingestion Actions -->
    <div style="display:flex; align-items:center; gap:10px;">
      <button class="btn btn-danger" id="btnBreak" onclick="triggerOutage()">💥 Inject 500 Outage Bug</button>
      <button class="btn btn-ok" id="btnFix" onclick="resolveOutage()">✨ Apply Fix / Recover</button>
      <a href="http://localhost:8080" target="_blank" class="btn btn-primary">🛡️ Open Aegis AI Dashboard (8080) ↗</a>
    </div>
  </div>

  <div class="container">
    <div class="hero">
      <h1>Webliz Cloud Platform Store</h1>
      <p>Simulated production workload for autonomous SRE triage, causality analysis &amp; auto-PR repair.</p>
    </div>

    <!-- Active Error & Auto-PR Notification Banner -->
    <div id="errorBanner" class="alert alert-crit">
      <div style="font-weight:700; font-size:14px; margin-bottom:4px;" id="errorTitle">🚨 HTTP 500 Internal Server Error</div>
      <div id="errorMessage" style="font-family:monospace; font-size:11px; margin-bottom:8px;">ZeroDivisionError: division by zero in demo-target/app.py line 48</div>
      <div id="prInfoBox" style="font-size:12px; color:#38bdf8;">
        ⚡ <strong>Automatically reported to Aegis AI Control Plane!</strong> Engaging Groq Llama-3.1 Autonomous PR Engine...
      </div>
    </div>

    <div id="successBanner" class="alert alert-ok">
      <div style="font-weight:700;">✅ Payment Verified &amp; Order Confirmed!</div>
      <div id="successMsg">Transaction successfully cleared through payment gateway. ZeroDivisionError avoided.</div>
    </div>

    <!-- Products -->
    <div class="product-grid">
      <div class="card">
        <div class="card-icon">🤖</div>
        <div class="card-title">Aegis AI SRE Autonomous Agent</div>
        <div class="card-desc">Self-healing dual-plane control plane with deterministic guardrails and automated PR synthesis.</div>
        <div class="card-bottom">
          <span class="price">$149.00</span>
          <button class="btn btn-primary" onclick="setProduct('Aegis AI SRE Agent', 149)">Select Product</button>
        </div>
      </div>

      <div class="card">
        <div class="card-icon">⚡</div>
        <div class="card-title">Enterprise GPU Inference Node</div>
        <div class="card-desc">High-throughput local LLM runner with automatic thermal throttling and VRAM model eviction.</div>
        <div class="card-bottom">
          <span class="price">$499.00</span>
          <button class="btn btn-primary" onclick="setProduct('Enterprise GPU Node', 499)">Select Product</button>
        </div>
      </div>

      <div class="card">
        <div class="card-icon">🌐</div>
        <div class="card-title">Edge Microservice Gateway</div>
        <div class="card-desc">Zero-trust API ingress router with cryptographic RBAC token verification and anomaly probing.</div>
        <div class="card-bottom">
          <span class="price">$89.00</span>
          <button class="btn btn-primary" onclick="setProduct('Edge Gateway', 89)">Select Product</button>
        </div>
      </div>
    </div>

    <!-- Checkout Form -->
    <div class="checkout-box">
      <div class="checkout-title">
        <span>Order Checkout</span>
        <span class="dev-badge badge-ok" id="cartBadge">1 Item</span>
      </div>

      <div class="form-group">
        <label>Selected Service</label>
        <input type="text" class="form-control" id="selectedItem" value="Aegis AI SRE Agent" readonly>
      </div>

      <div class="form-group">
        <label>Promo / Discount Coupon</label>
        <div style="display:flex; gap:8px;">
          <input type="text" class="form-control" id="couponCode" value="CRASH100">
          <button class="btn" onclick="applyCoupon()">Apply</button>
        </div>
        <small style="color:var(--muted); font-size:10px;">Hint: <code>CRASH100</code> triggers 100% discount, exposing the division-by-zero bug when defect is injected.</small>
      </div>

      <div class="order-summary">
        <div class="summary-row"><span>Base Price</span><span id="basePrice">$149.00</span></div>
        <div class="summary-row"><span>Promotional Discount</span><span id="discountText">-100% ($149.00)</span></div>
        <div class="summary-row total"><span>Total Amount</span><span id="totalAmount">$0.00</span></div>
      </div>

      <button class="btn btn-ok" style="width:100%; padding:12px; font-size:14px; font-weight:700;" id="checkoutBtn" onclick="submitCheckout()">
        💳 Complete Purchase ($0.00)
      </button>
    </div>
  </div>

  <script>
    let currentPrice = 149.00;
    let discountRate = 100.0;

    function setProduct(name, price) {
      document.getElementById('selectedItem').value = name;
      currentPrice = price;
      updateSummary();
    }

    function applyCoupon() {
      const code = document.getElementById('couponCode').value.trim();
      if (code === "CRASH100") {
        discountRate = 100.0;
        alert("Applied 100% discount coupon (CRASH100)!");
      } else {
        discountRate = 10.0;
        alert("Applied 10% discount coupon!");
      }
      updateSummary();
    }

    function updateSummary() {
      document.getElementById('basePrice').textContent = `$${currentPrice.toFixed(2)}`;
      document.getElementById('discountText').textContent = `-${discountRate}% ($${(currentPrice * (discountRate / 100)).toFixed(2)})`;
      const finalAmt = discountRate === 100 ? 0.00 : (currentPrice * (1 - discountRate / 100));
      document.getElementById('totalAmount').textContent = `$${finalAmt.toFixed(2)}`;
      document.getElementById('checkoutBtn').textContent = `💳 Complete Purchase ($${finalAmt.toFixed(2)})`;
    }

    async function submitCheckout() {
      const btn = document.getElementById('checkoutBtn');
      btn.textContent = "Processing Payment...";
      btn.disabled = true;
      document.getElementById('errorBanner').style.display = 'none';
      document.getElementById('successBanner').style.display = 'none';

      try {
        const res = await fetch('/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            item: document.getElementById('selectedItem').value,
            amount: currentPrice,
            coupon: document.getElementById('couponCode').value,
            discount_percent: discountRate
          })
        });

        const data = await res.json();
        if (res.status === 200) {
          document.getElementById('successBanner').style.display = 'block';
          document.getElementById('successMsg').textContent = `Order #${data.order_id || 'ORD-991'} placed successfully! Service health: HTTP 200 OK.`;
          updateHealthBadge(true);
        } else {
          // Outage / 500 error!
          document.getElementById('errorBanner').style.display = 'block';
          document.getElementById('errorTitle').textContent = `🚨 HTTP ${res.status}: ${data.error_type || 'InternalServerError'}`;
          document.getElementById('errorMessage').textContent = data.error || 'ZeroDivisionError in calculate_discounted_total';
          if (data.aegis_dispatch && data.aegis_dispatch.pull_request_url) {
            document.getElementById('prInfoBox').innerHTML = `
              ⚡ <strong>Reported to Aegis AI!</strong> Auto-PR generated: 
              <a href="${data.aegis_dispatch.pull_request_url}" target="_blank" style="color:#38bdf8; text-decoration:underline;">
                ${data.aegis_dispatch.pull_request_url} ↗
              </a>
            `;
          }
          updateHealthBadge(false);
        }
      } catch (err) {
        document.getElementById('errorBanner').style.display = 'block';
        document.getElementById('errorMessage').textContent = String(err);
        updateHealthBadge(false);
      } finally {
        btn.disabled = false;
        btn.textContent = `💳 Complete Purchase ($${(discountRate === 100 ? 0 : currentPrice * 0.9).toFixed(2)})`;
      }
    }

    async function triggerOutage() {
      const btn = document.getElementById('btnBreak');
      const origText = btn ? btn.textContent : "";
      if (btn) { btn.textContent = "Injecting Bug & Synthesizing PR..."; btn.disabled = true; }

      document.getElementById('errorBanner').style.display = 'none';
      document.getElementById('successBanner').style.display = 'none';

      try {
        const res = await fetch('/break', { method: 'POST' });
        const data = await res.json();
        updateHealthBadge(false);

        // Display error banner with PR details immediately
        document.getElementById('errorBanner').style.display = 'block';
        document.getElementById('errorTitle').textContent = `🚨 HTTP 500: ZeroDivisionError Caught`;
        document.getElementById('errorMessage').textContent = data.error || 'ZeroDivisionError: division by zero in calculate_discounted_total (demo-target/app.py:48)';

        const prUrl = data.aegis_dispatch && data.aegis_dispatch.pull_request_url;
        if (prUrl) {
          document.getElementById('prInfoBox').innerHTML = `
            ⚡ <strong>Autonomous PR Engine Engaged!</strong> Pull Request created:<br>
            <a href="${prUrl}" target="_blank" style="color:#38bdf8; font-weight:700; text-decoration:underline; font-size:13px; display:inline-block; margin-top:4px;">
              ${prUrl} ↗
            </a>
          `;
        } else if (data.aegis_dispatch && data.aegis_dispatch.dispatch && data.aegis_dispatch.dispatch.status === "API_DISABLED") {
          document.getElementById('prInfoBox').innerHTML = `
            ⚠️ Reported to Aegis. AI API dispatch is currently paused (Operator Switch: OFF).
          `;
        } else {
          document.getElementById('prInfoBox').innerHTML = `
            ⚡ Reported to Aegis AI Control Plane. Synthesizing PR fix...
          `;
        }
      } catch (err) {
        document.getElementById('errorBanner').style.display = 'block';
        document.getElementById('errorMessage').textContent = String(err);
      } finally {
        if (btn) { btn.textContent = origText; btn.disabled = false; }
        // Also trigger checkout to show end-user checkout error
        submitCheckout();
      }
    }

    async function resolveOutage() {
      const btn = document.getElementById('btnFix');
      if (btn) { btn.textContent = "Recovering..."; btn.disabled = true; }
      try {
        const res = await fetch('/fix', { method: 'POST' });
        const data = await res.json();
        updateHealthBadge(true);
        document.getElementById('errorBanner').style.display = 'none';
        document.getElementById('successBanner').style.display = 'block';
        document.getElementById('successMsg').textContent = "Fix deployed and verified. /health returned HTTP 200 OK. Autonomous healing closed!";
        submitCheckout();
      } catch (err) {
        console.warn(err);
      } finally {
        if (btn) { btn.textContent = "✨ Apply Fix / Recover"; btn.disabled = false; }
      }
    }

    function updateHealthBadge(isOk) {
      const b = document.getElementById('healthBadge');
      if (isOk) {
        b.className = "dev-badge badge-ok";
        b.textContent = "● Service 200 OK";
      } else {
        b.className = "dev-badge badge-crit";
        b.textContent = "● Outage 500 Error";
      }
    }

    // Initial health check
    fetch('/health').then(r => updateHealthBadge(r.status === 200)).catch(() => updateHealthBadge(false));
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    html_path = os.path.join(os.path.dirname(__file__), "storefront.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return render_template_string(STOREFRONT_HTML)


@app.route("/health")
def health():
    if _STATE["broken"]:
        err_msg = "ZeroDivisionError: division by zero in calculate_discounted_total (demo-target/app.py:48)"
        return (
            jsonify({
                "status": "unhealthy",
                "service": _STATE["service_name"],
                "version": _STATE["version"],
                "error": err_msg,
                "error_type": "ZeroDivisionError",
                "affected_file": "demo-target/app.py",
            }),
            500,
        )
    return (
        jsonify({
            "status": "healthy",
            "service": _STATE["service_name"],
            "version": _STATE["version"],
            "latency_ms": 11.8,
        }),
        200,
    )


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    data = request.get_json(silent=True) or {}
    base_amount = float(data.get("amount", 149.00))
    discount_pct = float(data.get("discount_percent", 100.0))

    try:
        total = calculate_discounted_total(base_amount, discount_pct)
        _STATE["total_orders"] += 1
        return (
            jsonify({
                "status": "success",
                "order_id": f"ORD-{_STATE['total_orders']}",
                "amount": total,
                "message": "Payment processed and order placed successfully.",
            }),
            200,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        err_type = type(exc).__name__
        err_msg = f"{err_type}: {exc}"
        _LOG.error("Unhandled checkout exception:\n%s", tb)
        _STATE["last_error"] = err_msg

        # Automatically report the exception traceback to Aegis Control Plane!
        aegis_res = report_error_to_aegis(err_msg, tb)

        return (
            jsonify({
                "status": "error",
                "error_type": err_type,
                "error": err_msg,
                "traceback": tb,
                "aegis_dispatch": aegis_res,
            }),
            500,
        )


@app.route("/break", methods=["POST"])
@app.route("/trigger-bug", methods=["POST"])
def trigger_bug():
    _STATE["broken"] = True
    _LOG.warning("Defect injected: calculate_discounted_total will raise ZeroDivisionError")

    # Immediately execute calculation with 100% discount to produce real ZeroDivisionError exception
    err_msg = "ZeroDivisionError: division by zero in calculate_discounted_total (demo-target/app.py:48)"
    tb_str = ""
    try:
        calculate_discounted_total(149.00, 100.0)
    except Exception as exc:
        tb_str = traceback.format_exc()
        err_msg = f"{type(exc).__name__}: {exc}"
        _LOG.error("Caught injected exception:\n%s", tb_str)
        _STATE["last_error"] = err_msg

    # Send error report to Aegis AI Control Plane
    aegis_res = report_error_to_aegis(err_msg, tb_str or err_msg)

    return (
        jsonify({
            "status": "bug_triggered",
            "broken": True,
            "error": err_msg,
            "traceback": tb_str,
            "aegis_dispatch": aegis_res,
            "message": "Planted defect triggered: checkout and /health now fail with ZeroDivisionError.",
        }),
        200,
    )


@app.route("/fix", methods=["POST"])
@app.route("/resolve-bug", methods=["POST"])
def resolve_bug():
    _STATE["broken"] = False
    _STATE["last_error"] = None
    _LOG.info("Defect resolved: /health returns HTTP 200 OK.")

    # Notify Aegis Control Plane of verified recovery
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(
                f"{AEGIS_CONTROL_PLANE}/api/incidents/recovery-confirm",
                json={"target_url": "http://127.0.0.1:5050", "status": "RECOVERY_VERIFIED"},
            )
    except Exception:
        pass

    return (
        jsonify({
            "status": "bug_resolved",
            "broken": False,
            "message": "Defect resolved: calculate_discounted_total operates normally. /health returns 200 OK.",
        }),
        200,
    )


def run_server(port: int = 5050):
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5050
    run_server(port)
