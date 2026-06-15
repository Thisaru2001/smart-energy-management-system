from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from jose import jwt, JWTError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os, uuid

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
app = FastAPI(title="Device Microservice (Meter)")

# =========================
# DB
# =========================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# =========================
# SECURITY
# =========================
security = HTTPBearer()

# =========================
# MODELS
# =========================
class Device(Base):
    __tablename__ = "devices"

    id = Column(String(50), primary_key=True)
    meter_number = Column(String(100), nullable=False, unique=True)   # smart meter number
    user_id = Column(String(50), nullable=False)                      # assigned user ID
    location = Column(String(200))
    active = Column(Boolean, default=True)                            # soft delete flag
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

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
def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

# =========================
# DEPENDENCIES
# =========================
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Extract and validate JWT, return payload."""
    return decode_token(credentials.credentials)

def require_role(required_roles: set):
    """Dependency factory to enforce role(s)."""
    def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user.get("role") not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have the required permissions"
            )
        return current_user
    return role_checker

# =========================
# REQUEST MODELS
# =========================
class DeviceRegisterRequest(BaseModel):
    meter_number: str
    user_id: str
    location: str | None = None

class DeviceUpdateRequest(BaseModel):
    meter_number: str | None = None
    user_id: str | None = None
    location: str | None = None

# =========================
# ENDPOINTS
# =========================

# -----------------------------------------------
# REGISTER DEVICE (only energy_manager / system_admin)
# -----------------------------------------------
@app.post("/devices", status_code=201)
def register_device(
    req: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role({"energy_manager", "system_admin"}))
):
    # Check if meter_number already exists
    existing = db.query(Device).filter(Device.meter_number == req.meter_number).first()
    if existing:
        raise HTTPException(status_code=400, detail="Meter number already registered")

    device = Device(
        id=str(uuid.uuid4()),
        meter_number=req.meter_number,
        user_id=req.user_id,
        location=req.location,
        active=True
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return {
        "message": "Device registered successfully",
        "device": {
            "id": device.id,
            "meter_number": device.meter_number,
            "user_id": device.user_id,
            "location": device.location,
            "active": device.active,
            "created_at": device.created_at.isoformat()
        }
    }

# -----------------------------------------------
# GET ALL DEVICES (only energy_manager / system_admin)
# -----------------------------------------------
@app.get("/devices")
def get_all_devices(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role({"energy_manager", "system_admin"}))
):
    devices = db.query(Device).all()
    return [
        {
            "id": d.id,
            "meter_number": d.meter_number,
            "user_id": d.user_id,
            "location": d.location,
            "active": d.active,
            "created_at": d.created_at.isoformat()
        }
        for d in devices
    ]

# -----------------------------------------------
# GET SPECIFIC DEVICE (any authenticated user)
# -----------------------------------------------
@app.get("/devices/{device_id}")
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "id": device.id,
        "meter_number": device.meter_number,
        "user_id": device.user_id,
        "location": device.location,
        "active": device.active,
        "created_at": device.created_at.isoformat()
    }

# -----------------------------------------------
# UPDATE DEVICE (only energy_manager / system_admin)
# -----------------------------------------------
@app.put("/devices/{device_id}")
def update_device(
    device_id: str,
    req: DeviceUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role({"energy_manager", "system_admin"}))
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if req.meter_number is not None:
        # Check if new meter_number is already taken
        existing = db.query(Device).filter(
            Device.meter_number == req.meter_number,
            Device.id != device_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Meter number already in use")
        device.meter_number = req.meter_number

    if req.user_id is not None:
        device.user_id = req.user_id

    if req.location is not None:
        device.location = req.location

    db.commit()
    return {"message": "Device updated"}

# -----------------------------------------------
# SOFT DELETE DEVICE (only system_admin)
# -----------------------------------------------
@app.put("/devices/delete/{device_id}")
def soft_delete_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role({"system_admin"}))
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.active = False
    db.commit()
    return {"message": "Device deactivated (soft delete)"}