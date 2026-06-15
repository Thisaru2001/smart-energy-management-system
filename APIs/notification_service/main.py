import os, uuid, smtplib
from email.mime.text import MIMEText
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, status as http_status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
DATABASE_URL = os.getenv("DATABASE_URL")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

app = FastAPI(title="Notification Microservice")

# ===================== DATABASE =====================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String(50), primary_key=True)
    user_id = Column(String(50), default=None, index=True)
    recipient_email = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    body = Column(Text)
    status = Column(String(20), nullable=False, default="pending")
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

# ===================== EMAIL SENDER =====================
def send_email(recipient: str, subject: str, body: str) -> bool:
    if not SMTP_HOST:
        print(f"\n--- FAKE EMAIL ---\nTo: {recipient}\nSubject: {subject}\n{body}\n")
        return True
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USERNAME
    msg["To"] = recipient
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, [recipient], msg.as_string())
        return True
    except Exception as e:
        print(f"Email sending failed: {e}")
        return False

# ===================== REQUEST MODEL =====================
class SendRequest(BaseModel):
    email: str
    subject: str
    message: str
    user_id: str | None = None   # optional – for associating with a user

# ===================== ENDPOINTS =====================

@app.post("/send")
def send_notification(
    req: SendRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Send a notification and log it."""
    success = send_email(req.email, req.subject, req.message)
    status = "sent" if success else "failed"

    # If user_id not provided, fallback to the token's user_id (if present)
    user_id = req.user_id or current_user.get("user_id")

    log = Notification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        recipient_email=req.email,
        subject=req.subject,
        body=req.message,
        status=status
    )
    db.add(log)
    db.commit()

    if not success:
        raise HTTPException(status_code=500, detail="Email could not be sent")
    return {"status": "sent", "message": "Notification sent successfully"}

@app.get("/notifications")
def get_notifications(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Get notifications for the logged‑in user."""
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID missing in token")
    logs = db.query(Notification).filter(Notification.user_id == user_id).order_by(Notification.created_at.desc()).limit(limit).all()
    return [
        {
            "id": n.id,
            "user_id": n.user_id,
            "recipient_email": n.recipient_email,
            "subject": n.subject,
            "body": n.body,
            "status": n.status,
            "created_at": n.created_at.isoformat()
        } for n in logs
    ]

@app.delete("/notifications")
def delete_all_notifications(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete all notifications for the logged‑in user."""
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID missing in token")
    deleted = db.query(Notification).filter(Notification.user_id == user_id).delete()
    db.commit()
    return {"message": f"Deleted {deleted} notifications"}

@app.delete("/notifications/{notification_id}")
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete a specific notification (only if it belongs to the user)."""
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID missing in token")
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found or access denied")
    db.delete(notification)
    db.commit()
    return {"message": "Notification deleted"}

@app.get("/health")
def health():
    return {"status": "running", "smtp_configured": bool(SMTP_HOST)}