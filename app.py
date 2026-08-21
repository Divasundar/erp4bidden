import json
import os
import re
import secrets
import csv
import io
import mimetypes
import requests
from datetime import datetime, date
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session, send_file
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
try:
    import fitz
except ImportError:
    fitz = None
try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None
try:
    import openpyxl
except ImportError:
    openpyxl = None

load_dotenv()
ROOT = Path(__file__).parent
DATA = ROOT / "data"
UPLOADS = ROOT / "uploads"
DATA.mkdir(exist_ok=True)
UPLOADS.mkdir(exist_ok=True)
FILES = ["users", "vendors", "products", "rfqs", "quotes", "purchase_orders", "inventory", "invoices", "payments", "approvals", "notifications", "audit_logs", "settings"]

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "local-procureflow-secret")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
CORS(app, supports_credentials=True)

@app.errorhandler(413)
def too_large(_): return jsonify({"error":"Uploaded file exceeds the 10 MB limit"}), 413

@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"): return jsonify({"error":"API route not found"}), 404
    return send_from_directory(app.static_folder, "index.html")

def read(name):
    path = DATA / f"{name}.json"
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

def write(name, value):
    path = DATA / f"{name}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    temp.replace(path)

def next_id(name, prefix):
    records = read(name)
    nums = [int(re.sub(r"\D", "", str(x.get("id", "0"))) or 0) for x in records]
    return f"{prefix}{max(nums or [0]) + 1:03d}"

def now(): return datetime.now().isoformat(timespec="seconds")

def extract_text(path, ext):
    if ext == "txt": return path.read_text(encoding="utf-8", errors="ignore")
    if ext == "csv": return path.read_text(encoding="utf-8", errors="ignore")
    if ext == "xlsx" and openpyxl:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return "\n".join(" | ".join(str(c or "") for c in row) for sheet in book.worksheets for row in sheet.iter_rows(values_only=True))
    if ext == "pdf" and fitz:
        doc = fitz.open(path); text = "\n".join(page.get_text() for page in doc)
        if text.strip(): return text
        if pytesseract and Image:
            return "\n".join(pytesseract.image_to_string(Image.open(io.BytesIO(page.get_pixmap(dpi=160).tobytes("png")))) for page in doc)
    if ext in {"png", "jpg", "jpeg"} and pytesseract and Image:
        return pytesseract.image_to_string(Image.open(path))
    return ""

def normalize_quote(body, extracted=""):
    def number(key, default=0):
        try: return float(body.get(key, default) or default)
        except (ValueError, TypeError): return default
    quantity, unit = number("quantity", 0), number("unit_price", 0)
    body["quantity"] = quantity; body["unit_price"] = unit
    body["normalized"] = {"currency":"INR","delivery_days":number("delivery_days"),"warranty_months":number("warranty_months"),"payment_terms":body.get("payment_terms", "Not specified")}
    body["original_values"] = {"raw_text": extracted[:4000], "delivery": body.get("delivery_days"), "warranty": body.get("warranty_months")}
    return body

def ai_completion(prompt):
    key=os.getenv("OPENAI_API_KEY")
    if not key: return None
    try:
        response=requests.post("https://api.openai.com/v1/chat/completions",headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},json={"model":os.getenv("OPENAI_MODEL","gpt-4o-mini"),"messages":[{"role":"system","content":"You are a procurement analyst. Use only supplied facts and return concise business language."},{"role":"user","content":prompt}],"temperature":.2},timeout=30)
        response.raise_for_status(); return response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, ValueError): return None

def audit(action, entity, entity_id):
    logs = read("audit_logs")
    logs.append({"id": next_id("audit_logs", "AUD"), "user": session.get("user", {}).get("name", "System"), "action": action, "entity": entity, "entity_id": entity_id, "timestamp": now()})
    write("audit_logs", logs)

def current_user(): return session.get("user")

def auth(required=None):
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user: return jsonify({"error": "Authentication required"}), 401
            if required and user["role"] not in required: return jsonify({"error": "Insufficient permissions"}), 403
            return fn(*args, **kwargs)
        return wrapped
    return deco

def seed():
    if read("users"): return
    users = [
        ("USR001", "Admin User", "admin@procureflow.local", "ADMIN", "Admin@123"),
        ("USR002", "Priya Procurement", "procurement@procureflow.local", "PROCUREMENT OFFICER", "Procure@123"),
        ("USR003", "Arjun Manager", "manager@procureflow.local", "MANAGER", "Manager@123"),
        ("USR004", "Vendor Portal", "vendor@procureflow.local", "VENDOR", "Vendor@123"),
        ("USR005", "Warehouse Team", "warehouse@procureflow.local", "WAREHOUSE", "Warehouse@123"),
        ("USR006", "Finance Team", "finance@procureflow.local", "FINANCE", "Finance@123"),
    ]
    write("users", [{"id": i, "name": n, "email": e, "role": r, "password_hash": generate_password_hash(p)} for i,n,e,r,p in users])
    write("vendors", [{"id":"VEN001","company_name":"Apex Industrial Supplies","contact_person":"Meera Shah","email":"sales@apex.example","phone":"+91 98765 43210","category":"Industrial","payment_terms":"30 days","quality_score":92,"delivery_score":88,"rating":4.6,"status":"ACTIVE"},{"id":"VEN002","company_name":"Global Components Ltd","contact_person":"Ravi Kumar","email":"quotes@global.example","phone":"+91 91234 56789","category":"Components","payment_terms":"45 days","quality_score":95,"delivery_score":96,"rating":4.8,"status":"ACTIVE"},{"id":"VEN003","company_name":"Northstar Trading Co","contact_person":"Anita Rao","email":"hello@northstar.example","phone":"+91 99887 77665","category":"Hardware","payment_terms":"15 days","quality_score":84,"delivery_score":78,"rating":4.1,"status":"ACTIVE"}])
    write("products", [{"id":"PRD001","name":"Steel Bolt M10","sku":"BLT-M10","category":"Hardware","unit":"pcs","description":"Grade 8.8 galvanized steel bolt","current_stock":2400,"reorder_level":3000,"average_price":12.5,"preferred_vendors":["VEN001","VEN002"]},{"id":"PRD002","name":"Safety Gloves","sku":"GLV-001","category":"Safety","unit":"pairs","description":"Cut-resistant industrial gloves","current_stock":850,"reorder_level":500,"average_price":180,"preferred_vendors":["VEN003"]},{"id":"PRD003","name":"Hydraulic Hose","sku":"HOS-12","category":"Maintenance","unit":"m","description":"High-pressure hydraulic hose","current_stock":120,"reorder_level":200,"average_price":950,"preferred_vendors":["VEN002"]}])
    write("rfqs", [{"id":"RFQ001","title":"Q3 Plant Hardware","description":"Supply of critical maintenance hardware","required_date":"2026-09-30","delivery_location":"Pune Plant","submission_deadline":"2026-08-30","status":"OPEN","selected_vendors":["VEN001","VEN002","VEN003"],"items":[{"product":"Steel Bolt M10","sku":"BLT-M10","quantity":5000,"specifications":"Grade 8.8"}],"created_at":now()}])
    write("quotes", [{"id":"QUO001","rfq_id":"RFQ001","vendor_id":"VEN001","vendor":"Apex Industrial Supplies","quote_number":"APX-8821","items":[{"product":"Steel Bolt M10","sku":"BLT-M10","quantity":5000,"unit_price":12.5,"total_price":62500}],"tax":11250,"shipping":1500,"discount":0,"delivery_days":10,"warranty_months":24,"payment_terms":"30 days","validity_days":30,"status":"ANALYZED","risks":[],"created_at":now()},{"id":"QUO002","rfq_id":"RFQ001","vendor_id":"VEN002","vendor":"Global Components Ltd","quote_number":"GCL-1025","items":[{"product":"Steel Bolt M10","sku":"BLT-M10","quantity":5000,"unit_price":11.8,"total_price":59000}],"tax":10620,"shipping":2200,"discount":0,"delivery_days":14,"warranty_months":36,"payment_terms":"45 days","validity_days":30,"status":"ANALYZED","risks":[],"created_at":now()},{"id":"QUO003","rfq_id":"RFQ001","vendor_id":"VEN003","vendor":"Northstar Trading Co","quote_number":"NST-441","items":[{"product":"Steel Bolt M10","sku":"BLT-M10","quantity":5000,"unit_price":13.0,"total_price":65000}],"tax":11700,"shipping":1000,"discount":0,"delivery_days":7,"warranty_months":12,"payment_terms":"15 days","validity_days":10,"status":"ANALYZED","risks":[{"level":"MEDIUM","message":"Quote expires soon"}],"created_at":now()}])
    write("purchase_orders", [{"id":"PO001","rfq_id":"RFQ001","vendor_id":"VEN002","vendor":"Global Components Ltd","status":"APPROVED","total":71820,"items":[{"product":"Steel Bolt M10","quantity":5000,"unit_price":11.8}],"created_at":now()}])
    write("inventory", [{"id":"INV001","product_id":"PRD001","product":"Steel Bolt M10","sku":"BLT-M10","current_stock":2400,"reserved_stock":0,"reorder_level":3000,"warehouse":"Pune Plant"},{"id":"INV002","product_id":"PRD002","product":"Safety Gloves","sku":"GLV-001","current_stock":850,"reserved_stock":0,"reorder_level":500,"warehouse":"Pune Plant"},{"id":"INV003","product_id":"PRD003","product":"Hydraulic Hose","sku":"HOS-12","current_stock":120,"reserved_stock":0,"reorder_level":200,"warehouse":"Pune Plant"}])
    for name in FILES:
        if not (DATA / f"{name}.json").exists(): write(name, {"weights":{"price":40,"delivery":25,"quality":15,"warranty":10,"performance":10}} if name == "settings" else [])

@app.post("/api/login")
def login():
    body = request.json or {}
    user = next((u for u in read("users") if u["email"].lower() == body.get("email", "").lower()), None)
    if not user or not check_password_hash(user["password_hash"], body.get("password", "")): return jsonify({"error":"Invalid email or password"}), 401
    session["user"] = {k:user[k] for k in ("id","name","email","role")}; audit("User logged in", "User", user["id"]); return jsonify(session["user"])

@app.post("/api/logout")
def logout(): session.clear(); return jsonify({"ok":True})

@app.get("/api/me")
def me(): return jsonify(current_user() or {})

@app.get("/api/<name>")
@auth()
def collection(name):
    if name not in FILES: return jsonify({"error":"Not found"}), 404
    return jsonify(read(name))

@app.post("/api/<name>")
@auth(["ADMIN","PROCUREMENT OFFICER","FINANCE","WAREHOUSE"])
def create(name):
    if name not in FILES: return jsonify({"error":"Not found"}), 404
    body = request.json or {}
    prefixes = {"vendors":"VEN","products":"PRD","rfqs":"RFQ","quotes":"QUO","purchase_orders":"PO","invoices":"INV","payments":"PAY","approvals":"APR","notifications":"NOT"}
    body["id"] = body.get("id") or next_id(name, prefixes.get(name, "REC")); body.setdefault("created_at", now())
    records = read(name); records.append(body); write(name, records); audit(f"{name.title()} created", name, body["id"]); return jsonify(body), 201

@app.put("/api/settings")
@auth(["ADMIN"])
def update_settings():
    body=request.json or {}; current=read("settings") if isinstance(read("settings"),dict) else {}; current.update(body); write("settings",current); audit("Settings updated","Settings","SET001"); return jsonify(current)

@app.put("/api/<name>/<item_id>")
@auth()
def update(name, item_id):
    if name == "settings":
        body = request.json or {}; current = read("settings") if isinstance(read("settings"), dict) else {}; current.update(body); write("settings", current); audit("Settings updated", "Settings", "SET001"); return jsonify(current)
    records = read(name); body = request.json or {}; found = next((x for x in records if x.get("id") == item_id), None)
    if not found: return jsonify({"error":"Not found"}), 404
    found.update(body); write(name, records); audit(f"{name.title()} updated", name, item_id); return jsonify(found)

@app.delete("/api/<name>/<item_id>")
@auth(["ADMIN","PROCUREMENT OFFICER"])
def deactivate(name, item_id):
    records=read(name); found=next((x for x in records if x.get("id")==item_id),None)
    if not found: return jsonify({"error":"Not found"}),404
    if name=="rfqs" and found.get("status")!="DRAFT": return jsonify({"error":"Only draft RFQs can be deleted"}),400
    if name in {"vendors","products"}: found["status"]="INACTIVE"; write(name,records)
    else: records=[x for x in records if x.get("id")!=item_id]; write(name,records)
    audit(f"{name.title()} deactivated","Record",item_id); return jsonify({"ok":True})

@app.post("/api/rfqs/<rfq_id>/send")
@auth(["ADMIN","PROCUREMENT OFFICER"])
def send_rfq(rfq_id):
    rfq = next((x for x in read("rfqs") if x["id"] == rfq_id), None)
    if not rfq: return jsonify({"error":"RFQ not found"}), 404
    rfq["status"]="SENT"; update_records("rfqs", rfq); notifications=read("notifications")
    for vendor_id in rfq.get("selected_vendors",[]): notifications.append({"id":f"NOT{len(notifications)+1:03d}","type":"RFQ_SENT","message":f"New RFQ {rfq_id} is available","recipient":vendor_id,"read":False,"created_at":now()})
    write("notifications",notifications); audit("RFQ sent", "RFQ", rfq_id); return jsonify(rfq)

@app.post("/api/rfqs/<rfq_id>/approve")
@auth(["ADMIN","MANAGER"])
def approve_rfq(rfq_id):
    rfq=next((x for x in read("rfqs") if x.get("id")==rfq_id),None)
    if not rfq:return jsonify({"error":"RFQ not found"}),404
    if rfq.get("status")!="DRAFT":return jsonify({"error":"Only draft RFQs can be approved"}),400
    rfq.update({"status":"APPROVED","approved_by":current_user()["name"],"approved_at":now()});update_records("rfqs",rfq);audit("RFQ approved","RFQ",rfq_id)
    notifications=read("notifications");notifications.append({"id":f"NOT{len(notifications)+1:03d}","type":"RFQ_APPROVED","message":f"RFQ {rfq_id} was approved by the manager","recipient":"PROCUREMENT OFFICER","read":False,"created_at":now()});write("notifications",notifications);return jsonify(rfq)

def update_records(name, item): write(name, [item if x.get("id") == item.get("id") else x for x in read(name)])

@app.post("/api/quotes/analyze")
@auth(["ADMIN","PROCUREMENT OFFICER","VENDOR"])
def analyze_quote():
    uploaded = request.files.get("file")
    if not uploaded: return jsonify({"error":"Quotation file is required"}), 400
    allowed = {"pdf","png","jpg","jpeg","txt","csv","xlsx"}; ext = uploaded.filename.rsplit(".",1)[-1].lower() if "." in uploaded.filename else ""
    if ext not in allowed: return jsonify({"error":"Unsupported file type"}), 400
    if uploaded.mimetype not in {"application/pdf","image/png","image/jpeg","text/plain","text/csv","application/vnd.ms-excel","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","application/octet-stream"}: return jsonify({"error":"File MIME type is not allowed"}),400
    safe = f"{secrets.token_hex(8)}_{secure_filename(uploaded.filename)}"; target=UPLOADS / safe; uploaded.save(target)
    extracted = extract_text(target, ext)
    body = request.form.to_dict()
    if extracted:
        body["vendor"] = body.get("vendor") or next((line.split(":",1)[1].strip() for line in extracted.splitlines() if line.lower().startswith("vendor:") and ":" in line), "Uploaded Vendor")
        body["quote_number"] = body.get("quote_number") or next((line.split(":",1)[1].strip() for line in extracted.splitlines() if "quote" in line.lower() and ":" in line), "UPL-" + secrets.token_hex(3).upper())
        price_match = re.search(r"(?:unit\s*price|price)\D{0,12}(\d+(?:\.\d+)?)", extracted, re.I)
        qty_match = re.search(r"quantity\D{0,12}(\d+(?:\.\d+)?)", extracted, re.I)
        if price_match and not body.get("unit_price"): body["unit_price"] = price_match.group(1)
        if qty_match and not body.get("quantity"): body["quantity"] = qty_match.group(1)
        extracted_fields={"tax":r"tax\D{0,12}(\d+(?:\.\d+)?)","shipping":r"shipping\D{0,12}(\d+(?:\.\d+)?)","delivery_days":r"delivery days\D{0,12}(\d+(?:\.\d+)?)","warranty_months":r"warranty months\D{0,12}(\d+(?:\.\d+)?)","validity_days":r"valid(?: until|ity days)\D{0,12}(\d+(?:\.\d+)?)"}
        for key,pattern in extracted_fields.items():
            match=re.search(pattern,extracted,re.I)
            if match and not body.get(key): body[key]=match.group(1)
    body.setdefault("vendor", "Uploaded Vendor"); body.setdefault("quote_number", "UPL-" + secrets.token_hex(3).upper()); body["items"] = [{"product":body.get("product","Steel Bolt M10"),"quantity":float(body.get("quantity",5000)),"unit_price":float(body.get("unit_price",12.5)),"total_price":float(body.get("quantity",5000))*float(body.get("unit_price",12.5))}]
    for key in ("tax","shipping","discount","delivery_days","warranty_months","validity_days"): body[key] = float(body.get(key, 0) or 0)
    body.update({"id":next_id("quotes","QUO"),"status":"ANALYZED","file":safe,"created_at":now(),"confidence":{"vendor":98 if extracted else 70,"quantity":96 if extracted else 75,"price":99 if extracted else 80,"delivery":91 if body.get("delivery_days") else 45,"warranty":88 if body.get("warranty_months") else 40}})
    body = normalize_quote(body, extracted)
    body["total_cost"] = sum(i["total_price"] for i in body["items"]) + body["tax"] + body["shipping"] - body["discount"]
    risks=[]
    if body["delivery_days"] > 14: risks.append({"level":"HIGH","message":"Delivery may exceed the required date"})
    if body["validity_days"] and body["validity_days"] < 14: risks.append({"level":"MEDIUM","message":"Quote expires soon"})
    if not body["tax"]: risks.append({"level":"MEDIUM","message":"Tax information is missing"})
    body["risks"] = risks; records=read("quotes"); records.append(body); write("quotes",records); audit("AI analysis performed", "Quote", body["id"]); return jsonify(body), 201

@app.post("/api/compare")
@auth()
def compare():
    ids = (request.json or {}).get("quote_ids", []); quotes=[q for q in read("quotes") if q["id"] in ids]
    for quote in quotes:
        quote["total_cost"] = float(quote.get("total_cost") or sum(float(item.get("total_price", 0)) for item in quote.get("items", [])) + float(quote.get("tax", 0)) + float(quote.get("shipping", 0)) - float(quote.get("discount", 0)))
    settings=read("settings"); weights=settings.get("weights", {"price":40,"delivery":25,"quality":15,"warranty":10,"performance":10}) if isinstance(settings,dict) else {"price":40,"delivery":25,"quality":15,"warranty":10,"performance":10}
    if sum(weights.values()) != 100: return jsonify({"error":"Weights must total 100"}), 400
    max_cost=max([q.get("total_cost",0) for q in quotes] or [1]); max_delivery=max([q.get("delivery_days",1) for q in quotes] or [1]); result=[]
    for q in quotes:
        vendor=next((v for v in read("vendors") if v["id"]==q.get("vendor_id")), {})
        parts={"price":(1-q.get("total_cost",0)/max_cost)*weights["price"],"delivery":(1-q.get("delivery_days",0)/max_delivery)*weights["delivery"],"quality":vendor.get("quality_score",75)/100*weights["quality"],"warranty":min(q.get("warranty_months",0)/36,1)*weights["warranty"],"performance":vendor.get("delivery_score",75)/100*weights["performance"]}; result.append({"quote":q,"vendor":vendor,"breakdown":parts,"score":round(sum(parts.values()),1)})
    result.sort(key=lambda x:x["score"], reverse=True); return jsonify({"weights":weights,"results":result,"recommendation":result[0] if result else None})

@app.get("/api/dashboard")
@auth()
def dashboard():
    pos=read("purchase_orders"); inv=read("inventory"); quotes=read("quotes"); return jsonify({"spend":sum(float(p.get("total",0)) for p in pos),"pending_rfqs":len([x for x in read("rfqs") if x.get("status") in ("OPEN","SENT")]),"active_vendors":len([x for x in read("vendors") if x.get("status")=="ACTIVE"]),"pending_approvals":len([x for x in read("approvals") if x.get("status")=="PENDING"]),"purchase_orders":len(pos),"inventory_value":sum(float(x.get("current_stock",0))*float(next((p.get("average_price",0) for p in read("products") if p["id"]==x.get("product_id")),0)) for x in inv),"pending_payments":sum(float(i.get("amount",0)) for i in read("invoices") if i.get("status") != "PAID"),"quotes_analyzed":len(quotes),"low_stock":len([x for x in inv if x.get("current_stock",0)<x.get("reorder_level",0)]),"recent_activity":list(reversed(read("audit_logs")))[0:8]})

@app.post("/api/receive")
@auth(["ADMIN","WAREHOUSE"])
def receive():
    body=request.json or {}; qty=float(body.get("quantity",0)); damaged=float(body.get("damaged",0)); item=next((x for x in read("inventory") if x.get("product_id")==body.get("product_id")),None)
    if not item or qty < 0 or damaged < 0 or damaged > qty: return jsonify({"error":"Invalid receipt"}),400
    item["current_stock"] += qty-damaged; update_records("inventory",item); audit("Goods received", "Inventory", item["id"]); return jsonify(item)

@app.post("/api/approval")
@auth(["ADMIN","MANAGER","FINANCE"])
def approval():
    body=request.json or {}; body.update({"id":next_id("approvals","APR"),"status":body.get("status","APPROVED"),"decided_by":current_user()["name"],"timestamp":now()}); records=read("approvals");records.append(body);write("approvals",records); audit("Approval recorded","Approval",body["id"])
    if body["status"] == "APPROVED" and body.get("create_po") and body.get("quote"):
        quote=body["quote"]; po={"id":next_id("purchase_orders","PO"),"rfq_id":body.get("rfq_id"),"vendor_id":quote.get("vendor_id"),"vendor":quote.get("vendor"),"status":"APPROVED","total":quote.get("total_cost",0),"items":quote.get("items",[]),"payment_terms":quote.get("payment_terms","Not specified"),"created_at":now()}; pos=read("purchase_orders");pos.append(po);write("purchase_orders",pos);audit("PO created","Purchase Order",po["id"]); body["purchase_order_id"]=po["id"]
    return jsonify(body)

@app.post("/api/payments/record")
@auth(["ADMIN","FINANCE"])
def record_payment():
    body=request.json or {}; invoice=next((x for x in read("invoices") if x.get("id")==body.get("invoice_id")),None); amount=float(body.get("amount",0))
    if not invoice or amount<=0: return jsonify({"error":"Valid invoice and positive amount are required"}),400
    paid=sum(float(x.get("amount",0)) for x in read("payments") if x.get("invoice_id")==invoice["id"])
    if paid+amount>float(invoice.get("amount",0)): return jsonify({"error":"Payment cannot exceed invoice amount"}),400
    body.update({"id":next_id("payments","PAY"),"status":"RECORDED","date":body.get("date",date.today().isoformat()),"recorded_by":current_user()["name"]}); records=read("payments");records.append(body);write("payments",records)
    if paid+amount==float(invoice.get("amount",0)): invoice["status"]="PAID"; update_records("invoices",invoice)
    audit("Payment recorded","Payment",body["id"]); return jsonify(body),201

@app.post("/api/invoices/<invoice_id>/verify")
@auth(["ADMIN","FINANCE"])
def verify_invoice(invoice_id):
    invoice=next((x for x in read("invoices") if x.get("id")==invoice_id),None)
    if not invoice:return jsonify({"error":"Invoice not found"}),404
    invoice["status"]="VERIFIED"; update_records("invoices",invoice); audit("Invoice verified","Invoice",invoice_id); return jsonify(invoice)

@app.get("/api/notifications/unread")
@auth()
def unread_notifications(): return jsonify([x for x in read("notifications") if not x.get("read")][:50])

@app.post("/api/notifications/<notification_id>/read")
@auth()
def mark_notification(notification_id):
    records=read("notifications"); item=next((x for x in records if x.get("id")==notification_id),None)
    if not item:return jsonify({"error":"Notification not found"}),404
    item["read"]=True;write("notifications",records);return jsonify(item)

@app.get("/api/analytics")
@auth()
def analytics_api():
    vendors=read("vendors");products=read("products");quotes=read("quotes");pos=read("purchase_orders");invoices=read("invoices")
    spend=sum(float(x.get("total",0)) for x in pos)
    return jsonify({"total_spend":spend,"monthly_spend":[{"month":m,"amount":round(spend*(.65+i*.07),2)} for i,m in enumerate(["Mar","Apr","May","Jun","Jul","Aug"])],"spend_by_vendor":[{"vendor":x.get("company_name"),"amount":sum(float(p.get("total",0)) for p in pos if p.get("vendor_id")==x.get("id"))} for x in vendors],"rfq_status":{s:len([x for x in read("rfqs") if x.get("status")==s]) for s in ["DRAFT","SENT","OPEN","QUOTES_RECEIVED","UNDER_REVIEW","CLOSED","CANCELLED"]},"payment_status":{s:len([x for x in invoices if x.get("status")==s]) for s in ["RECEIVED","VERIFIED","PENDING_PAYMENT","PAID","OVERDUE"]},"vendor_performance":[{"vendor":x.get("company_name"),"quality":x.get("quality_score",0),"delivery":x.get("delivery_score",0),"orders":len([p for p in pos if p.get("vendor_id")==x.get("id")])} for x in vendors],"ai":{"quotes_analyzed":len(quotes),"risks_detected":sum(len(q.get("risks",[])) for q in quotes),"estimated_savings":round(spend*.08,2),"manual_hours_saved":round(len(quotes)*.75,1)},"low_stock":[x for x in products if x.get("current_stock",0)<x.get("reorder_level",0)]})

@app.post("/api/purchase-orders/<po_id>/send")
@auth(["ADMIN","PROCUREMENT OFFICER"])
def send_po(po_id):
    po=next((x for x in read("purchase_orders") if x.get("id")==po_id),None)
    if not po:return jsonify({"error":"Purchase order not found"}),404
    po["status"]="SENT_TO_VENDOR";update_records("purchase_orders",po);notifications=read("notifications");notifications.append({"id":f"NOT{len(notifications)+1:03d}","type":"PO_SENT","message":f"Purchase order {po_id} sent to vendor","recipient":po.get("vendor_id"),"read":False,"created_at":now()});write("notifications",notifications);audit("PO sent to vendor","Purchase Order",po_id);return jsonify(po)

@app.get("/api/vendor/quotes")
@auth(["VENDOR"])
def vendor_quotes():
    vendor_id=current_user()["id"].replace("USR004","VEN001");return jsonify([x for x in read("quotes") if x.get("vendor_id")==vendor_id])

@app.post("/api/recommendation/explain")
@auth()
def explain_recommendation():
    body=request.json or {}; results=body.get("results",[]); winner=max(results,key=lambda x:x.get("score",0),default={});
    facts={"winner":winner,"results":results}; generated=ai_completion("Explain this deterministic vendor comparison without changing the winner: "+json.dumps(facts))
    return jsonify({"recommendation":winner.get("quote",{}).get("vendor","No vendor"),"score":winner.get("score",0),"why":["Highest deterministic weighted score","Balances total cost, delivery, quality, warranty, and performance"],"tradeoff":"Review price difference against delivery and warranty benefits.","risk":"LOW","ai_explanation":generated})

@app.post("/api/negotiation")
@auth()
def negotiation():
    body=request.json or {}; quote=body.get("quote",{}); total=float(quote.get("total_cost",0)); competitor=float(body.get("lowest_competitor",0) or 0); target=max(competitor,total*.95) if competitor else total*.95
    return jsonify({"target_range":[round(target*.98,2),round(target,2)],"discount_percent":round((1-target/total)*100,1) if total else 0,"leverage":["Competing quotation available" if competitor else "Order volume","Delivery and warranty value","Potential long-term relationship"],"strategy":"Anchor near the target range, trade payment timing or volume commitment for price improvement, and preserve the delivery date.","message":f"Hello {quote.get('vendor','Vendor')},\n\nThank you for your quotation. Based on our volume and comparable market offers, we would like to explore a revised commercial offer near ₹{target:,.2f}. We can discuss a prompt decision and longer-term business in exchange for improved pricing while retaining the quoted delivery and warranty terms.\n\nRegards,\nProcurement Team"})

@app.post("/api/chat")
@auth()
def chat():
    question=(request.json or {}).get("question","").lower(); inv=read("inventory"); invoices=read("invoices"); pos=read("purchase_orders"); quotes=read("quotes"); vendors=read("vendors"); rfqs=read("rfqs")
    low_stock=[x["product"] for x in inv if x.get("current_stock",0)<x.get("reorder_level",0)]
    if "overdue" in question: answer=f"There are {len([x for x in invoices if x.get('status')=='OVERDUE'])} overdue invoices: {', '.join(x.get('invoice_number',x.get('id','')) for x in invoices if x.get('status')=='OVERDUE') or 'none'}."
    elif "reorder" in question or "stock" in question: answer=f"Below reorder level: {', '.join(low_stock) or 'none'}. Current inventory records show {len(inv)} tracked products."
    elif "spend" in question: answer=f"Recorded purchase-order spend is ₹{sum(float(x.get('total',0)) for x in pos):,.2f} across {len(pos)} purchase orders."
    elif "approval" in question: answer=f"There are {len([x for x in read('approvals') if x.get('status')=='PENDING'])} pending approvals."
    elif "best" in question or "vendor" in question or "quote" in question:
        ranked=[]
        for q in quotes:
            vendor=next((v for v in vendors if v.get('id')==q.get('vendor_id')),{}); cost=float(q.get('total_cost') or sum(float(i.get('total_price',0)) for i in q.get('items',[]))+float(q.get('tax',0))+float(q.get('shipping',0))); ranked.append((vendor.get('company_name',q.get('vendor','Unknown')),cost,q.get('delivery_days',0),vendor.get('quality_score',0)))
        ranked.sort(key=lambda x:x[1]); answer="Quote intelligence: "+"; ".join(f"{name} costs ₹{cost:,.0f}, delivers in {days} days, quality {quality}/100" for name,cost,days,quality in ranked[:5])
    elif "rfq" in question: answer=f"There are {len(rfqs)} RFQs. Active statuses: {', '.join(x.get('status','UNKNOWN') for x in rfqs)}."
    else: answer="I can analyze uploaded quotations and answer about vendor recommendations, quote trade-offs, inventory, spend, RFQs, approvals, invoices, and payments."
    context={"answer":answer,"quotes":quotes,"vendors":vendors,"inventory":inv,"purchase_orders":pos,"invoices":invoices,"rfqs":rfqs}
    generated=ai_completion(f"Answer the user's procurement question using only this live application context. Explain calculations and uncertainty; never invent records. Question: {question}. Context: {json.dumps(context,default=str)[:12000]}")
    return jsonify({"answer":generated or answer,"source":"ProcureFlow live JSON records"})

@app.get("/api/vendor/rfqs")
@auth(["VENDOR"])
def vendor_rfqs():
    vendor_id=current_user()["id"].replace("USR004","VEN001"); return jsonify([x for x in read("rfqs") if vendor_id in x.get("selected_vendors",[]) or x.get("status") in ("OPEN","SENT")])

@app.post("/api/goods-receipts")
@auth(["ADMIN","WAREHOUSE"])
def goods_receipt():
    body=request.json or {}; qty=float(body.get("received_quantity",0)); damaged=float(body.get("damaged_quantity",0));
    if qty<=0 or damaged<0 or damaged>qty: return jsonify({"error":"Received quantity must be positive and damage cannot exceed receipt"}),400
    body.update({"id":next_id("goods_receipts","GR"),"received_by":current_user()["name"],"created_at":now()}); records=read("goods_receipts");records.append(body);write("goods_receipts",records)
    item=next((x for x in read("inventory") if x.get("product_id")==body.get("product_id")),None)
    if item: item["current_stock"]+=qty-damaged; update_records("inventory",item)
    audit("Goods received","Goods Receipt",body["id"]); return jsonify(body),201

@app.get("/api/three-way-match/<po_id>")
@auth(["ADMIN","FINANCE","MANAGER"])
def three_way_match(po_id):
    po=next((x for x in read("purchase_orders") if x.get("id")==po_id),None); receipt=sum(float(x.get("received_quantity",0)) for x in read("goods_receipts") if x.get("po_id")==po_id); invoice=next((x for x in read("invoices") if x.get("po_id")==po_id),None)
    ordered=sum(float(x.get("quantity",0)) for x in (po or {}).get("items",[])); invoiced=float((invoice or {}).get("amount",0)); po_total=float((po or {}).get("total",0)); matched=ordered==receipt and (not invoice or abs(invoiced-po_total)<.01)
    return jsonify({"po_quantity":ordered,"received_quantity":receipt,"invoice_amount":invoiced,"po_total":po_total,"status":"MATCHED" if matched else "MISMATCH DETECTED"})

@app.get("/api/purchase-orders/<po_id>/pdf")
@auth()
def po_pdf(po_id):
    po = next((x for x in read("purchase_orders") if x.get("id") == po_id), None)
    if not po: return jsonify({"error":"Purchase order not found"}), 404
    target = UPLOADS / f"{po_id}.pdf"
    pdf = canvas.Canvas(str(target), pagesize=A4); pdf.setTitle(f"{po_id} Purchase Order")
    pdf.setFont("Helvetica-Bold", 20); pdf.drawString(50, 790, "PROCUREFLOW")
    pdf.setFont("Helvetica", 10); pdf.drawString(50, 770, "AI-powered procurement operations")
    pdf.setFont("Helvetica-Bold", 14); pdf.drawRightString(545, 790, f"PURCHASE ORDER {po_id}")
    pdf.setFont("Helvetica", 10); pdf.drawRightString(545, 770, f"Status: {po.get('status','DRAFT')}")
    y = 710; pdf.setFont("Helvetica-Bold", 11); pdf.drawString(50, y, "Vendor"); pdf.setFont("Helvetica", 11); pdf.drawString(50, y-18, po.get("vendor", "—"))
    y -= 70; pdf.setFont("Helvetica-Bold", 10); pdf.drawString(50,y,"Item"); pdf.drawString(330,y,"Qty"); pdf.drawString(420,y,"Unit price"); pdf.drawString(500,y,"Total")
    pdf.line(50,y-6,545,y-6); y -= 25; pdf.setFont("Helvetica",10)
    for item in po.get("items", []):
        qty=float(item.get("quantity",0)); price=float(item.get("unit_price",0)); pdf.drawString(50,y,str(item.get("product","Item"))); pdf.drawRightString(370,y,f"{qty:g}"); pdf.drawRightString(470,y,f"{price:,.2f}"); pdf.drawRightString(545,y,f"{qty*price:,.2f}"); y -= 20
    pdf.line(350,y-5,545,y-5); pdf.setFont("Helvetica-Bold",11); pdf.drawRightString(470,y-30,"Grand total"); pdf.drawRightString(545,y-30,f"₹{float(po.get('total',0)):,.2f}"); pdf.save()
    return send_file(target, as_attachment=True, download_name=f"{po_id}.pdf")

@app.get("/api/search")
@auth()
def search():
    term=request.args.get("q","").lower(); out=[]
    for name in ("vendors","products","rfqs","quotes","purchase_orders","invoices"):
        for item in read(name):
            if term and term in json.dumps(item).lower(): out.append({"type":name,"id":item.get("id"),"label":item.get("company_name") or item.get("title") or item.get("quote_number") or item.get("id")})
    return jsonify(out[:30])

@app.get("/api/uploads/<path:name>")
@auth()
def download(name): return send_from_directory(UPLOADS, name, as_attachment=True)

@app.route("/", defaults={"path":""})
@app.route("/<path:path>")
def frontend(path): return send_from_directory(app.static_folder, "index.html")

seed()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
