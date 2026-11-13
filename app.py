import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = os.environ.get("DATABASE_PATH", str(ROOT / "marketplace.sqlite3"))
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
PAYPAL_ENV = os.environ.get("PAYPAL_ENV", "sandbox")
PAYPAL_API = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


def connect():
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                price_cents INTEGER NOT NULL CHECK(price_cents > 0)
            );
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                access_hash TEXT NOT NULL, items TEXT NOT NULL, total_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        if not db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
            db.executemany(
                "INSERT INTO products (name, description, price_cents) VALUES (?, ?, ?)",
                [
                    ("NJIT Notebook", "A notebook for your next big idea.", 1299),
                    ("Campus Water Bottle", "Stay hydrated on campus.", 1899),
                    ("Study Tote", "Carry your essentials to class.", 1599),
                ],
            )
    if DATABASE != ":memory:":
        os.chmod(DATABASE, 0o600)


def paypal_request(method, path, body=None):
    client = os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    if not client or not secret or PAYPAL_ENV not in PAYPAL_API:
        raise ValueError("PayPal checkout is not configured.")
    import base64

    basic = base64.b64encode(f"{client}:{secret}".encode()).decode()
    request = urllib.request.Request(
        PAYPAL_API[PAYPAL_ENV] + "/v1/oauth2/token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        token = json.load(response)["access_token"]
    request = urllib.request.Request(
        PAYPAL_API[PAYPAL_ENV] + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def amount(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def create_order(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid checkout information.")
    name = data.get("name")
    email = data.get("email")
    cart = data.get("items")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Please enter your name.")
    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Please enter a valid email address.")
    if not isinstance(cart, list) or not 1 <= len(cart) <= 100:
        raise ValueError("Your cart is empty or invalid.")

    items = []
    seen = set()
    with connect() as db:
        for item in cart:
            if not isinstance(item, dict):
                raise ValueError("Invalid cart item.")
            product_id, quantity = item.get("id"), item.get("quantity")
            if (type(product_id) is not int or type(quantity) is not int or
                    product_id in seen or not 1 <= quantity <= 99):
                raise ValueError("Invalid cart item.")
            product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if not product:
                raise ValueError("A product in your cart is no longer available.")
            seen.add(product_id)
            items.append({"id": product_id, "name": product["name"],
                          "price_cents": product["price_cents"], "quantity": quantity})

        total = sum(item["price_cents"] * item["quantity"] for item in items)
        result = paypal_request("POST", "/v2/checkout/orders", {
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": amount(total)}}],
            "payment_source": {"paypal": {"experience_context": {
                "return_url": BASE_URL + "/?checkout=return",
                "cancel_url": BASE_URL + "/?checkout=cancel",
                "user_action": "PAY_NOW",
            }}},
        })
        order_id = result.get("id")
        approve = next((link.get("href") for link in result.get("links", [])
                        if link.get("rel") in ("payer-action", "approve")), None)
        parsed = urllib.parse.urlparse(approve or "")
        allowed_host = "www.sandbox.paypal.com" if PAYPAL_ENV == "sandbox" else "www.paypal.com"
        if not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", order_id) or (
                parsed.scheme != "https" or parsed.hostname != allowed_host):
            raise ValueError("PayPal did not provide a valid checkout link.")
        access = secrets.token_urlsafe(32)
        customer_id = db.execute("INSERT INTO customers (name, email) VALUES (?, ?)",
                                 (name.strip(), email.strip())).lastrowid
        db.execute("INSERT INTO orders (id, customer_id, access_hash, items, total_cents) VALUES (?, ?, ?, ?, ?)",
                   (order_id, customer_id, hashlib.sha256(access.encode()).hexdigest(), json.dumps(items), total))
    return {"id": order_id, "access": access, "approve_url": approve}


def capture_order(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid order.")
    order_id, access = data.get("id"), data.get("access")
    if not isinstance(order_id, str) or not isinstance(access, str) or len(access) > 100:
        raise ValueError("Invalid order.")
    with connect() as db:
        order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order or not hmac.compare_digest(order["access_hash"], hashlib.sha256(access.encode()).hexdigest()):
            raise ValueError("Order not found.")
        if order["status"] == "paid":
            return {"status": "paid"}
        path = "/v2/checkout/orders/" + urllib.parse.quote(order_id, safe="")
        try:
            result = paypal_request("POST", path + "/capture", {})
        except urllib.error.HTTPError as error:
            if error.code != 422:
                raise
            result = paypal_request("GET", path)
        captures = [capture for unit in result.get("purchase_units", [])
                    for capture in unit.get("payments", {}).get("captures", [])]
        if (result.get("id") != order_id or result.get("status") != "COMPLETED" or
                len(captures) != 1 or captures[0].get("status") != "COMPLETED" or
                captures[0].get("amount") != {"currency_code": "USD", "value": amount(order["total_cents"])}):
            raise ValueError("Payment has not been completed. Please contact the store if you were charged.")
        db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
    return {"status": "paid"}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        content = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/products":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0].strip()
            with connect() as db:
                products = [dict(row) for row in db.execute(
                    "SELECT id, name, description, price_cents FROM products WHERE instr(lower(name || ' ' || description), lower(?)) > 0 ORDER BY id",
                    (query,))]
            return self.send_json(200, products)
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path not in files:
            return self.send_json(404, {"error": "Not found."})
        filename, content_type = files[path]
        content = (ROOT / "static" / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/api/orders", "/api/orders/capture"):
            return self.send_json(404, {"error": "Not found."})
        origin = self.headers.get("Origin")
        if origin and origin != BASE_URL:
            return self.send_json(403, {"error": "Invalid origin."})
        if self.headers.get_content_type() != "application/json":
            return self.send_json(415, {"error": "Expected JSON."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(length))
            result = create_order(data) if path == "/api/orders" else capture_order(data)
            self.send_json(200, result)
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "Invalid request."})
        except (urllib.error.URLError, TimeoutError, OSError, KeyError):
            self.send_json(502, {"error": "Payment service unavailable. Please try again."})


if __name__ == "__main__":
    if PAYPAL_ENV not in PAYPAL_API:
        raise SystemExit("PAYPAL_ENV must be sandbox or live.")
    parsed_base = urllib.parse.urlparse(BASE_URL)
    if (parsed_base.path or parsed_base.query or parsed_base.fragment or not parsed_base.netloc or
            (parsed_base.scheme != "https" and not (
                parsed_base.scheme == "http" and parsed_base.hostname in ("localhost", "127.0.0.1", "::1")))):
        raise SystemExit("BASE_URL must be an HTTPS origin (HTTP is allowed for localhost).")
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Marketplace listening on http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
