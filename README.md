# ProcureAI ERP backend

Flask + SQLite REST API for the ProcureAI ERP frontend.

```powershell
py -m pip install -r requirements.txt
py app.py
```

Open `http://localhost:5000` for the mirrored ProcureAI web app. The API is available at `http://localhost:5000/api`. Health check: `GET /api/health`.
Login with `admin@procureai.local` / `admin123`, then send the returned token as `Authorization: Bearer <token>`.

Routes: `/api/dashboard`, `/api/suppliers`, `/api/products`, `/api/purchase-orders`, `/api/invoices`.
