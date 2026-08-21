# ProcureFlow ERP

A runnable AI-powered procurement ERP MVP using Flask, JSON files, and a vanilla JavaScript frontend. It covers vendor/product management, RFQs, quotation upload and analysis, deterministic vendor comparison, approvals, inventory receipt, finance records, audit logging, analytics, role-based access, and responsive enterprise UI.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

Demo credentials:

| Role | Email | Password |
|---|---|---|
| Admin | admin@procureflow.local | Admin@123 |
| Procurement Officer | procurement@procureflow.local | Procure@123 |
| Manager | manager@procureflow.local | Manager@123 |
| Vendor | vendor@procureflow.local | Vendor@123 |
| Warehouse | warehouse@procureflow.local | Warehouse@123 |
| Finance | finance@procureflow.local | Finance@123 |

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env` when connecting an LLM provider. The deterministic scoring engine remains authoritative for recommendations.
