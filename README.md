# SmartEnergy – Intelligent Energy Management System

Microservices platform for real‑time energy monitoring, analytics, billing, and smart device management.  
Built with **FastAPI**, **MySQL**, **JWT**, and **Chart.js**.

---

## Services

| Service            | Port  |
|--------------------|-------|
| User Service       | 2001  |
| Device Service     | 2002  |
| Energy Simulator   | 2003  |
| Analytics Service  | 2004  |
| Billing Service    | 2005  |
| Notification Service | 2006 |
| API Gateway        | 2000  |

---

## Quick Start

1. Install dependencies for each service (`requirements.txt`).
2. Create MySQL databases (see SQL scripts in each service).
3. Add `.env` files with `SECRET_KEY`, `DATABASE_URL`,(never commit).
4. Start all services with `uvicorn main:app --port <port> --reload`.
5. Serve frontend: `python -m http.server 5501`.
6. Open `http://localhost:5501/index.html`.

---

## Auth

Login → JWT token. Roles: `home_user`, `energy_manager`, `system_admin`.

---
