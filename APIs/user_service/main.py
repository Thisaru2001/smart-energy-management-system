import httpx
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, String, Boolean, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os, uuid, secrets, random

# =========================
# LOAD ENV
# =========================
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_EXPIRE_MINUTES = int(os.getenv("ACCESS_EXPIRE_MINUTES", 30))
DATABASE_URL = os.getenv("DATABASE_URL")

# =========================
# APP
# =========================
app = FastAPI(title="User Microservice")

# =========================
# DB
# =========================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# =========================
# SECURITY
# =========================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def hash_password(p: str) -> str:
    return pwd_context.hash(p)

def verify_password(p: str, h: str) -> bool:
    return pwd_context.verify(p, h)

# =========================
# MODELS
# =========================
class User(Base):
    __tablename__ = "users"

    id = Column(String(50), primary_key=True)
    name = Column(String(100))
    email = Column(String(100), unique=True, index=True)
    password = Column(String(255))
    role = Column(String(50), default="home_user")
    active = Column(Boolean, default=True)   # soft delete
    verified = Column(Boolean, default=False)

class OTPStore(Base):
    __tablename__ = "otp_store"

    id = Column(String(50), primary_key=True)
    email = Column(String(100), index=True)
    code = Column(String(10))
    expires_at = Column(DateTime)

Base.metadata.create_all(bind=engine)

# =========================
# DB SESSION
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# =========================
# JWT
# =========================
def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=ACCESS_EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# ===================== NOTIFICATION HELPER =====================
def send_notification(email: str, subject: str, message: str):
    """Send email via Notification Service using a short‑lived service token."""
    notification_url = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:2006")
    # Create a 5‑minute token for the service itself
    payload = {
        "user_id": "user_service",
        "role": "system",
        "exp": datetime.utcnow() + timedelta(minutes=5)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "email": email,
        "subject": subject,
        "message": message,
        "user_id": None   # the notification service will use the email directly
    }
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(f"{notification_url}/send", json=data, headers=headers)
            if resp.status_code == 200:
                print(f"Notification sent to {email}")
            else:
                print(f"Notification failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Could not send notification: {e}")

# =========================
# DEPENDENCIES
# =========================
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Extract and validate JWT, return payload."""
    return decode_token(credentials.credentials)

# =========================
# REQUEST MODELS
# =========================
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str = "home_user"

class LoginRequest(BaseModel):
    email: str
    password: str

class UpdateUserRequest(BaseModel):
    name: str | None = None
    email: str | None = None      # added
    password: str | None = None   # added
    # role is intentionally omitted – regular users cannot change it

class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str

# =========================
# REGISTER (NO TOKEN)
# =========================
@app.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):

    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="User already exists")

    user = User(
        id=str(uuid.uuid4()),
        name=req.name,
        email=req.email,
        password=hash_password(req.password),
        role=req.role,
        active=True,
        verified=False
    )

    db.add(user)
    db.commit()
    # Send welcome email
    send_notification(
        user.email,
        "Welcome to SmartEnergy!",
        f"Hi {user.name}, thank you for registering. You can now monitor your energy usage."
    )

    return {"message": "User created successfully"}

# =========================
# LOGIN
# =========================
@app.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.email == req.email).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.active:
        raise HTTPException(status_code=403, detail="User deactivated")

    if not verify_password(req.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token({
        "user_id": user.id,
        "email": user.email,
        "role": user.role
    })

    return {
        "message": "Login successful",
        "token": token,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role
        }
    }

# =========================
# GET ALL USERS (REQUIRES TOKEN + ROLE: energy_manager OR system_admin)
# =========================
@app.get("/users")
def get_all_users(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    allowed_roles = {"energy_manager", "system_admin"}
    if current_user.get("role") not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view all users"
        )

    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "active": u.active
        }
        for u in users
    ]

# =========================
# GET USER BY ID
# =========================
@app.get("/user/{user_id}")
def get_user(user_id: str, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "active": user.active
    }

# =========================
# UPDATE USER (OWN PROFILE ONLY, NO ROLE CHANGE)
# =========================
@app.put("/user/{user_id}")
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    # 1. Only the owner can update their own profile
    if current_user["user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="You can only update your own profile"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)

    # 2. Update allowed fields
    if req.name is not None:
        user.name = req.name

    if req.email is not None:
        # Check if new email is already taken by another user
        existing = db.query(User).filter(User.email == req.email, User.id != user_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        user.email = req.email

    if req.password is not None:
        user.password = hash_password(req.password)

    # Role is **never** updated via this endpoint
    db.commit()

    return {
        "message": "Profile updated",
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role
        }
    }

# =========================
# SOFT DELETE (SYSTEM_ADMIN ONLY)
# =========================
@app.put("/user/delete/{user_id}")
def soft_delete(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user.get("role") != "system_admin":
        raise HTTPException(
            status_code=403,
            detail="Only system_admin can deactivate users"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)

    user.active = False
    db.commit()

    return {"message": "User deactivated (soft delete)"}

# =========================
# FORGOT PASSWORD (OTP)
# =========================
@app.post("/forgot-password")
@app.post("/forgot-password")
def forgot_password(email: str, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.email == email).first()
    if not user:
        return {"message": "If the email is registered, an OTP has been sent"}

    otp = str(random.randint(100000, 999999))
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    otp_entry = OTPStore(
        id=str(uuid.uuid4()),
        email=email,
        code=otp,
        expires_at=expires_at
    )
    db.add(otp_entry)
    db.commit()

    # Send OTP via the notification service
    send_notification(
        email,
        "Your Password Reset OTP",
        f"Your one-time password reset code is: {otp}. It expires in 10 minutes."
    )

    return {
        "message": "If the email is registered, an OTP has been sent",
        "otp": otp   # Keep for development; remove in production
    }

# =========================
# RESET PASSWORD
# =========================
@app.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):

    otp_record = (
        db.query(OTPStore)
        .filter(
            OTPStore.email == req.email,
            OTPStore.code == req.otp,
            OTPStore.expires_at > datetime.utcnow()
        )
        .first()
    )

    if not otp_record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password = hash_password(req.new_password)
        # Send confirmation email
    send_notification(
        user.email,
        "Password Reset Successful",
        "Your password has been changed successfully. If you did not do this, please contact support immediately."
    )
    db.delete(otp_record)
    db.commit()

    return {"message": "Password updated successfully"}