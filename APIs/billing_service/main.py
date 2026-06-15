import os, uuid, threading, time, hashlib
from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import create_engine, Column, String, Float, Date, DateTime, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from jose import jwt, JWTError
from dotenv import load_dotenv
import httpx

load_dotenv()

SECRET_KEY               = os.getenv("SECRET_KEY")
ALGORITHM                = os.getenv("ALGORITHM", "HS256")
DATABASE_URL             = os.getenv("DATABASE_URL")
USER_SERVICE_URL         = os.getenv("USER_SERVICE_URL",         "http://127.0.0.1:2001")
ANALYTICS_SERVICE_URL    = os.getenv("ANALYTICS_SERVICE_URL",    "http://127.0.0.1:2004")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:2006")
PAYHERE_MERCHANT_ID      = os.getenv("PAYHERE_MERCHANT_ID")
PAYHERE_SECRET           = os.getenv("PAYHERE_SECRET")
PAYHERE_NOTIFY_URL       = os.getenv("PAYHERE_NOTIFY_URL")
PAYHERE_RETURN_URL       = os.getenv("PAYHERE_RETURN_URL")
PAYHERE_CANCEL_URL       = os.getenv("PAYHERE_CANCEL_URL")
ELECTRICITY_RATE         = float(os.getenv("ELECTRICITY_RATE", "0.24"))

app = FastAPI(title="Billing Microservice")

# Allow frontend to call this service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== DATABASE =====================
engine       = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base         = declarative_base()

class Bill(Base):
    __tablename__ = "bills"
    bill_id        = Column(String(50), primary_key=True)
    user_id        = Column(String(50), nullable=False)
    meter_id       = Column(String(50), nullable=False)
    billing_period = Column(String(20), nullable=False)   # "YYYY-MM"
    units_consumed = Column(Float,      nullable=False)
    amount         = Column(Float,      nullable=False)
    due_date       = Column(Date,       nullable=False)
    status         = Column(String(20), nullable=False, default="pending")

class Payment(Base):
    __tablename__ = "payments"
    payment_id     = Column(String(50),  primary_key=True)
    bill_id        = Column(String(50),  ForeignKey("bills.bill_id"), nullable=False)
    user_id        = Column(String(50),  nullable=False)
    transaction_id = Column(String(100))
    amount         = Column(Float,       nullable=False)
    payment_method = Column(String(50),  default="payhere")
    payment_date   = Column(DateTime)
    payment_status = Column(String(20),  nullable=False, default="initiated")

class PaymentLog(Base):
    __tablename__ = "payment_logs"
    log_id     = Column(String(50), primary_key=True)
    payment_id = Column(String(50), ForeignKey("payments.payment_id"), nullable=False)
    message    = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ===================== AUTH =====================
security = HTTPBearer()

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return decode_token(credentials.credentials)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_service_token() -> str:
    payload = {
        "user_id": "billing_service",
        "role":    "system",
        "exp":     datetime.utcnow() + timedelta(hours=1)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# ===================== HELPERS =====================
def get_all_users() -> list:
    token   = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{USER_SERVICE_URL}/users", headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"Error fetching users: {e}")
        return []

def get_monthly_consumption(meter_id: str, period: str) -> Optional[float]:
    token   = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{ANALYTICS_SERVICE_URL}/analytics/consumption",
                params={"meter_id": meter_id, "period": "monthly", "date": period},
                headers=headers
            )
            resp.raise_for_status()
            return resp.json().get("total_energy_kwh")
    except Exception as e:
        print(f"Error fetching consumption for meter {meter_id}: {e}")
        return None

def send_notification(user_email: str, subject: str, message: str):
    token   = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=5) as client:
            client.post(
                f"{NOTIFICATION_SERVICE_URL}/send",
                json={"email": user_email, "subject": subject, "message": message},
                headers=headers
            ).raise_for_status()
    except Exception as e:
        print(f"Notification failed: {e}")

# ===================== PAYHERE HASH =====================
def generate_payhere_hash(merchant_id: str, order_id: str, amount: float, currency: str) -> str:
    """
    PayHere hash formula (MUST follow this exactly):
      secret_hash = MD5(merchant_secret).upper()
      final_hash  = MD5(merchant_id + order_id + formatted_amount + currency + secret_hash).upper()
    """
    formatted_amount = f"{amount:.2f}"
    secret_hash      = hashlib.md5(PAYHERE_SECRET.encode("utf-8")).hexdigest().upper()
    main_string      = f"{merchant_id}{order_id}{formatted_amount}{currency}{secret_hash}"
    return hashlib.md5(main_string.encode("utf-8")).hexdigest().upper()

# ===================== BACKGROUND JOB =====================
def generate_monthly_bills():
    while True:
        now = datetime.utcnow()
        if now.day == 1 and now.hour == 0 and now.minute == 5:
            period = f"{now.year-1}-12" if now.month == 1 else f"{now.year}-{now.month-1:02d}"
            print(f"Generating bills for period {period}")
            users = get_all_users()
            db    = SessionLocal()
            try:
                for user in users:
                    meter_id = user.get("meter_id") or user["id"]
                    if not user.get("active"):
                        continue
                    existing = db.query(Bill).filter(
                        Bill.user_id == user["id"],
                        Bill.meter_id == meter_id,
                        Bill.billing_period == period
                    ).first()
                    if existing:
                        continue
                    usage = get_monthly_consumption(meter_id, period)
                    if usage is None:
                        continue
                    amount   = round(usage * ELECTRICITY_RATE, 2)
                    due_date = date.today() + timedelta(days=14)
                    db.add(Bill(
                        bill_id=str(uuid.uuid4()),
                        user_id=user["id"],
                        meter_id=meter_id,
                        billing_period=period,
                        units_consumed=usage,
                        amount=amount,
                        due_date=due_date,
                        status="pending"
                    ))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Bill generation error: {e}")
            finally:
                db.close()
        time.sleep(60)

@app.on_event("startup")
def start_background_jobs():
    threading.Thread(target=generate_monthly_bills, daemon=True).start()

# ===================== BILLING ENDPOINTS =====================

@app.get("/bills")
def get_user_bills(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    bills = db.query(Bill).filter(
        Bill.user_id == current_user["user_id"]
    ).order_by(Bill.billing_period.desc()).all()
    return [
        {
            "bill_id":        b.bill_id,
            "billing_period": b.billing_period,
            "units_consumed": b.units_consumed,
            "amount":         b.amount,
            "due_date":       b.due_date.isoformat(),
            "status":         b.status
        } for b in bills
    ]

@app.get("/bills/{bill_id}")
def get_bill_detail(bill_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    bill = db.query(Bill).filter(Bill.bill_id == bill_id, Bill.user_id == current_user["user_id"]).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    payments = db.query(Payment).filter(Payment.bill_id == bill_id).all()
    return {
        "bill_id":        bill.bill_id,
        "user_id":        bill.user_id,
        "meter_id":       bill.meter_id,
        "billing_period": bill.billing_period,
        "units_consumed": bill.units_consumed,
        "amount":         bill.amount,
        "due_date":       bill.due_date.isoformat(),
        "status":         bill.status,
        "payments": [
            {
                "payment_id":     p.payment_id,
                "transaction_id": p.transaction_id,
                "amount":         p.amount,
                "payment_date":   p.payment_date.isoformat() if p.payment_date else None,
                "payment_status": p.payment_status
            } for p in payments
        ]
    }

# ===================== INITIATE PAYMENT =====================

@app.post("/bills/{bill_id}/pay")
def initiate_payment(
    bill_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Returns all PayHere payment fields + hash to the frontend.
    The frontend JS SDK uses these directly with sandbox: true.
    No redirect URL is built here.
    """
    bill = db.query(Bill).filter(
        Bill.bill_id == bill_id,
        Bill.user_id == current_user["user_id"]
    ).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == "paid":
        raise HTTPException(status_code=400, detail="Bill already paid")

    # Fetch real user email
    user_email      = "user@example.com"
    user_first_name = "User"
    user_last_name  = bill.user_id
    try:
        svc_token = create_service_token()
        headers   = {"Authorization": f"Bearer {svc_token}"}
        with httpx.Client(timeout=10) as client:
            user_resp = client.get(f"{USER_SERVICE_URL}/user/{bill.user_id}", headers=headers)
            if user_resp.status_code == 200:
                u = user_resp.json()
                user_email      = u.get("email",      user_email)
                user_first_name = u.get("first_name", user_first_name)
                user_last_name  = u.get("last_name",  user_last_name)
    except Exception:
        pass

    # Create payment record
    payment = Payment(
        payment_id     = str(uuid.uuid4()),
        bill_id        = bill.bill_id,
        user_id        = bill.user_id,
        amount         = bill.amount,
        payment_method = "payhere",
        payment_status = "initiated"
    )
    db.add(payment)
    db.commit()

    db.add(PaymentLog(
        log_id     = str(uuid.uuid4()),
        payment_id = payment.payment_id,
        message    = "Payment initiated"
    ))
    db.commit()

    # Generate hash
    order_id = payment.payment_id
    currency = "LKR"
    the_hash = generate_payhere_hash(PAYHERE_MERCHANT_ID, order_id, bill.amount, currency)

    print(f"[PAYHERE] order_id={order_id} amount={bill.amount:.2f} hash={the_hash}")

    # Return all fields — frontend JS SDK uses these directly
    return {
        "merchant_id": PAYHERE_MERCHANT_ID,
        "order_id":    order_id,
        "items":       f"Electricity Bill {bill.billing_period}",
        "amount":      f"{bill.amount:.2f}",
        "currency":    currency,
        "hash":        the_hash,
        "return_url":  PAYHERE_RETURN_URL,
        "cancel_url":  PAYHERE_CANCEL_URL,
        "notify_url":  PAYHERE_NOTIFY_URL,
        "first_name":  user_first_name,
        "last_name":   user_last_name,
        "email":       user_email,
        "phone":       "0771234567",
        "address":     "Colombo",
        "city":        "Colombo",
        "country":     "Sri Lanka",
    }

# ===================== PAYHERE CALLBACKS =====================

@app.post("/payment/notify")
async def payhere_notify(request: Request, db: Session = Depends(get_db)):
    """PayHere server-to-server callback."""
    form = await request.form()
    data = dict(form)

    merchant_id      = data.get("merchant_id", "")
    order_id         = data.get("order_id", "")
    payhere_amount   = data.get("payhere_amount", "")
    payhere_currency = data.get("payhere_currency", "")
    status_code      = data.get("status_code", "")
    md5sig           = data.get("md5sig", "")

    # Verify signature
    secret_hash = hashlib.md5(PAYHERE_SECRET.encode("utf-8")).hexdigest().upper()
    local_sig   = hashlib.md5(
        f"{merchant_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash}".encode("utf-8")
    ).hexdigest().upper()

    if local_sig != md5sig:
        print(f"[PAYHERE NOTIFY] Invalid signature. Expected {local_sig}, got {md5sig}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payment = db.query(Payment).filter(Payment.payment_id == order_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if status_code == "2":       # success
        payment.payment_status = "success"
        payment.transaction_id = data.get("payment_id", "N/A")
        payment.payment_date   = datetime.utcnow()
        bill = db.query(Bill).filter(Bill.bill_id == payment.bill_id).first()
        if bill:
            bill.status = "paid"
        db.commit()
        db.add(PaymentLog(log_id=str(uuid.uuid4()), payment_id=payment.payment_id,
                          message=f"Payment success. txn_id={payment.transaction_id}"))
        db.commit()
        # Email notification
        try:
            svc_token = create_service_token()
            with httpx.Client(timeout=10) as client:
                ur = client.get(f"{USER_SERVICE_URL}/user/{payment.user_id}",
                                headers={"Authorization": f"Bearer {svc_token}"})
                email = ur.json().get("email", "")
                if email and bill:
                    send_notification(email, "Payment Confirmed",
                                      f"Bill {bill.billing_period} paid. Amount: {payment.amount:.2f} LKR.")
        except Exception:
            pass

    elif status_code in ("-1", "-2", "-3"):
        payment.payment_status = "failed"
        db.commit()
        db.add(PaymentLog(log_id=str(uuid.uuid4()), payment_id=payment.payment_id,
                          message=f"Payment failed/cancelled. status_code={status_code}"))
        db.commit()

    else:   # 0 = pending
        db.add(PaymentLog(log_id=str(uuid.uuid4()), payment_id=payment.payment_id,
                          message=f"Payment pending. status_code={status_code}"))
        db.commit()

    return {"status": "ok"}


@app.get("/payment/return")
async def payhere_return():
    return RedirectResponse(url=PAYHERE_RETURN_URL)


@app.get("/payments")
def payment_history(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    payments = (
        db.query(Payment)
        .filter(Payment.user_id == current_user["user_id"])
        .order_by(Payment.payment_date.desc())
        .all()
    )
    return [
        {
            "payment_id":     p.payment_id,
            "bill_id":        p.bill_id,
            "transaction_id": p.transaction_id,
            "amount":         p.amount,
            "payment_method": p.payment_method,
            "payment_date":   p.payment_date.isoformat() if p.payment_date else None,
            "payment_status": p.payment_status
        } for p in payments
    ]


@app.get("/health")
def health():
    return {"status": "running"}