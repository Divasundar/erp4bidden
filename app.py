from datetime import datetime, timedelta
from functools import wraps
import os
import sqlite3
import secrets
import json
import urllib.error
import urllib.request
import urllib.parse

from flask import Flask, g, jsonify, request, send_from_directory

app = Flask(__name__)
@app.after_request
def cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response
DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "procureai.db"))
USERS_FILE = os.getenv("USERS_FILE", os.path.join(os.path.dirname(__file__), "users.txt"))
TOKENS = {}
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "public")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user');
CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, phone TEXT, category TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT, unit TEXT DEFAULT 'unit', stock REAL NOT NULL DEFAULT 0, reorder_level REAL NOT NULL DEFAULT 0, unit_cost REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_orders (id INTEGER PRIMARY KEY, po_number TEXT UNIQUE NOT NULL, supplier_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', total REAL NOT NULL DEFAULT 0, notes TEXT, created_at TEXT NOT NULL, FOREIGN KEY(supplier_id) REFERENCES suppliers(id));
CREATE TABLE IF NOT EXISTS purchase_items (id INTEGER PRIMARY KEY, purchase_order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity REAL NOT NULL, unit_cost REAL NOT NULL, FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id), FOREIGN KEY(product_id) REFERENCES products(id));
CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY, invoice_number TEXT UNIQUE NOT NULL, supplier_id INTEGER, purchase_order_id INTEGER, amount REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', due_date TEXT, created_at TEXT NOT NULL, FOREIGN KEY(supplier_id) REFERENCES suppliers(id));
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_error):
    conn = g.pop("db", None)
    if conn: conn.close()

def init_db():
    with app.app_context():
        conn = db(); conn.executescript(SCHEMA)
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
            conn.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)", ("Admin", "admin@procureai.local", "admin123", "admin"))
            conn.commit()

def row(r): return dict(r) if r else None
def rows(rs): return [dict(x) for x in rs]
def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token not in TOKENS: return jsonify(error="Authentication required"), 401
        g.user = TOKENS[token]; return fn(*args, **kwargs)
    return wrapped
def payload(required=()):
    data = request.get_json(silent=True) or {}
    missing = [x for x in required if not data.get(x)]
    if missing: return None, jsonify(error=f"Missing fields: {', '.join(missing)}"), 400
    return data, None, None
def now(): return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def file_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            f.write("admin|admin123|admin\noperator|user123|user\n")
    users = []
    with open(USERS_FILE, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("|")
            if len(parts) == 3: users.append(dict(id=len(users)+1, username=parts[0], password=parts[1], role=parts[2]))
    return users

@app.post("/api/auth/register")
def register():
    data, err, code = payload(("username", "password"))
    if err: return err, code
    users = file_users(); username = data["username"].strip().lower()
    if any(u["username"].lower() == username for u in users): return jsonify(error="Username is already registered"), 409
    with open(USERS_FILE, "a", encoding="utf-8") as f: f.write(f"{username}|{data['password']}|user\n")
    return jsonify(message="Registration successful", user={"username": username, "role": "user"}), 201

@app.post("/api/chat")
@require_auth
def chat():
    data, err, code = payload(("message",))
    if err: return err, code
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: return jsonify(error="Gemini is not configured. Set GEMINI_API_KEY on the server."), 503
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    prompt = ("You are 4Bidden ERP's operations assistant. Answer accurately and briefly using only the user's request and ERP context. "
              "Help with suppliers, inventory, purchase orders, invoices, approvals, and reporting. Never invent records or claim an action was completed. "
              "For legal, financial, safety, privacy, or compliance topics, give general information and recommend a qualified professional. "
              "Refuse unlawful, fraudulent, harmful, or unauthorized requests.\n\n" +
              f"ERP context: {json.dumps(data.get('context', {}), ensure_ascii=False)}\nUser: {data['message']}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800}}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={urllib.parse.quote(api_key)}"
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response: result = json.load(response)
        answer = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
        if not answer: return jsonify(error="Gemini returned no answer"), 502
        return jsonify(answer=answer, model=model)
    except urllib.error.HTTPError as exc:
        return jsonify(error="Gemini request failed", details=exc.read().decode(errors="replace")[:500]), 502
    except (urllib.error.URLError, TimeoutError):
        return jsonify(error="Gemini is temporarily unavailable"), 504

@app.get("/")
def frontend(): return send_from_directory(FRONTEND_DIR, "index.html")

@app.get("/assets/<path:filename>")
def frontend_assets(filename): return send_from_directory(os.path.join(FRONTEND_DIR, "assets"), filename)

@app.get("/favicon.svg")
def favicon(): return send_from_directory(FRONTEND_DIR, "favicon.svg")

@app.get("/api/health")
def health(): return jsonify(status="ok", service="procureai-backend", time=now())

@app.post("/api/auth/login")
def login():
    data, err, code = payload(("username", "password"))
    if err: return err, code
    user = next((u for u in file_users() if u["username"].lower() == data["username"].strip().lower() and u["password"] == data["password"]), None)
    if not user: return jsonify(error="Invalid email or password"), 401
    token = secrets.token_urlsafe(32); TOKENS[token] = user
    return jsonify(token=token, user={k: user[k] for k in ("id", "username", "role")})

def crud(table, fields, required=()):
    @require_auth
    def handler():
        conn = db()
        if request.method == "GET": return jsonify(data=rows(conn.execute(f"SELECT * FROM {table} ORDER BY id DESC")))
        data, err, code = payload(required)
        if err: return err, code
        cols = [x for x in fields if x in data]; vals = [data[x] for x in cols]
        marks = ",".join("?" for _ in cols)
        try:
            cur = conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({marks})", vals); conn.commit()
            return jsonify(data=row(conn.execute(f"SELECT * FROM {table} WHERE id=?", (cur.lastrowid,)).fetchone())), 201
        except sqlite3.IntegrityError as e: return jsonify(error=str(e)), 409
    return handler

app.add_url_rule("/api/suppliers", endpoint="suppliers", view_func=crud("suppliers", ("name","email","phone","category","status"), ("name",)), methods=["GET","POST"])
app.add_url_rule("/api/products", endpoint="products", view_func=crud("products", ("sku","name","category","unit","stock","reorder_level","unit_cost"), ("sku","name")), methods=["GET","POST"])
app.add_url_rule("/api/invoices", endpoint="invoices", view_func=crud("invoices", ("invoice_number","supplier_id","purchase_order_id","amount","status","due_date"), ("invoice_number","amount")), methods=["GET","POST"])

@app.get("/api/purchase-orders")
@require_auth
def purchase_orders():
    q = db().execute("SELECT p.*, s.name supplier_name FROM purchase_orders p JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.id DESC").fetchall()
    return jsonify(data=rows(q))

@app.post("/api/purchase-orders")
@require_auth
def create_po():
    data, err, code = payload(("supplier_id", "items"))
    if err: return err, code
    conn = db(); number = data.get("po_number", f"PO-{datetime.utcnow():%Y%m%d%H%M%S}")
    try:
        cur = conn.execute("INSERT INTO purchase_orders(po_number,supplier_id,status,notes,created_at) VALUES(?,?,?,?,?)", (number,data["supplier_id"],data.get("status","draft"),data.get("notes"),now()))
        total = 0
        for item in data["items"]:
            cost = float(item.get("unit_cost", 0)); qty = float(item["quantity"]); total += cost * qty
            conn.execute("INSERT INTO purchase_items(purchase_order_id,product_id,quantity,unit_cost) VALUES(?,?,?,?)", (cur.lastrowid,item["product_id"],qty,cost))
        conn.execute("UPDATE purchase_orders SET total=? WHERE id=?", (total,cur.lastrowid)); conn.commit()
        return jsonify(id=cur.lastrowid, po_number=number, total=total), 201
    except (sqlite3.IntegrityError, KeyError, ValueError) as e: return jsonify(error=str(e)), 400

@app.get("/api/dashboard")
@require_auth
def dashboard():
    conn=db(); low=conn.execute("SELECT COUNT(*) n FROM products WHERE stock <= reorder_level").fetchone()["n"]
    return jsonify(suppliers=conn.execute("SELECT COUNT(*) n FROM suppliers").fetchone()["n"], products=conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"], purchase_orders=conn.execute("SELECT COUNT(*) n FROM purchase_orders").fetchone()["n"], pending_invoices=conn.execute("SELECT COUNT(*) n FROM invoices WHERE status='pending'").fetchone()["n"], low_stock=low)

@app.cli.command("init-db")
def init_command(): init_db(); print("Database initialized")

init_db()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
