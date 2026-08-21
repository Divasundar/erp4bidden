# 4Bidden ERP backend

Flask + SQLite REST API for the 4Bidden ERP frontend.

```powershell
py -m pip install -r requirements.txt
py app.py
```

Open `http://localhost:5000` for the mirrored 4Bidden ERP web app. The API is available at `http://localhost:5000/api`. Health check: `GET /api/health`.
Login with `admin` / `admin123` or `operator` / `user123`. Users are stored in `users.txt`; registration is available at `POST /api/auth/register` with `username` and `password`. Send the returned token as `Authorization: Bearer <token>`.

To enable the ERP chatbot, set `GEMINI_API_KEY` in the server environment (never commit the key). The authenticated `POST /api/chat` endpoint accepts `{ "message": "...", "context": {} }` and returns `{ "answer": "..." }`.

Routes: `/api/dashboard`, `/api/suppliers`, `/api/products`, `/api/purchase-orders`, `/api/invoices`.
