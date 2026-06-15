import os, uuid, threading, time, random, math
from datetime import datetime, timedelta, date
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import create_engine, Column, String, DateTime, Float, Integer, Text, Date, Index, func, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from jose import jwt, JWTError
from dotenv import load_dotenv
import httpx

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
DATABASE_URL = os.getenv("DATABASE_URL")
ENERGY_SERVICE_URL = os.getenv("ENERGY_SERVICE_URL", "http://127.0.0.1:2003")
DEVICE_SERVICE_URL = os.getenv("DEVICE_SERVICE_URL", "http://127.0.0.1:2002")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:2006")
USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://127.0.0.1:2001")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "1"))
ELECTRICITY_RATE = float(os.getenv("ELECTRICITY_RATE", "0.24"))
CARBON_FACTOR = float(os.getenv("CARBON_FACTOR", "0.92"))

app = FastAPI(title="Analytics Microservice")

# ===================== DATABASE =====================
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class RawReading(Base):
    __tablename__ = "raw_readings"
    id = Column(String(50), primary_key=True)
    meter_id = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    voltage = Column(Float, nullable=False)
    current = Column(Float, nullable=False)
    power = Column(Float, nullable=False)
    energy_kwh = Column(Float, nullable=False)
    frequency = Column(Float, nullable=False)
    power_factor = Column(Float, nullable=False)
    status = Column(String(20), nullable=False)

class HourlyAggregate(Base):
    __tablename__ = "hourly_aggregates"
    id = Column(String(50), primary_key=True)
    meter_id = Column(String(50), nullable=False)
    hour_start = Column(DateTime, nullable=False)
    total_energy_kwh = Column(Float, nullable=False)
    avg_power = Column(Float, nullable=False)
    avg_voltage = Column(Float, nullable=False)
    avg_current = Column(Float, nullable=False)
    avg_frequency = Column(Float, nullable=False)
    avg_pf = Column(Float, nullable=False)
    normal_count = Column(Integer, default=0)
    warning_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    total_readings = Column(Integer, default=0)

class DailyAggregate(Base):
    __tablename__ = "daily_aggregates"
    id = Column(String(50), primary_key=True)
    meter_id = Column(String(50), nullable=False)
    day = Column(Date, nullable=False)
    total_energy_kwh = Column(Float, nullable=False)
    peak_power = Column(Float)
    avg_power = Column(Float)
    normal_hours = Column(Integer, default=0)
    warning_hours = Column(Integer, default=0)
    error_hours = Column(Integer, default=0)

class ActivityLog(Base):
    __tablename__ = "activity_log"
    id = Column(String(50), primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    type = Column(String(50), nullable=False)
    message = Column(String(255), nullable=False)
    details = Column(Text)

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
        "user_id": "analytics_service",
        "role": "system_admin",
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# ===================== POLLING & STORAGE =====================
def fetch_latest_reading() -> Optional[dict]:
    token = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{ENERGY_SERVICE_URL}/readings/latest", headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"Error fetching reading: {e}")
        return None

def poll_and_store():
    while True:
        reading = fetch_latest_reading()
        if reading:
            db = SessionLocal()
            try:
                raw = RawReading(
                    id=str(uuid.uuid4()),
                    meter_id=reading["meterId"],      # keeps whatever the simulator sends (SM001)
                    timestamp=datetime.fromisoformat(reading["timestamp"]),
                    voltage=reading["voltage"],
                    current=reading["current"],
                    power=reading["power"],
                    energy_kwh=reading["energy_kwh"],
                    frequency=reading["frequency"],
                    power_factor=reading["power_factor"],
                    status=reading["status"]
                )
                db.add(raw)
                db.commit()
            except Exception as e:
                db.rollback()
            finally:
                db.close()
        time.sleep(POLL_INTERVAL)

# ===================== AGGREGATION JOBS (using SM-001) =====================
def compute_hourly_aggregate(start: datetime, end: datetime):
    db = SessionLocal()
    try:
        # Delete any existing aggregate for this hour (idempotent)
        db.execute(
            text("DELETE FROM hourly_aggregates WHERE meter_id = :m AND hour_start = :s"),
            {"m": "SM-001", "s": start}
        )
        # Calculate total energy as sum of power * (1/3600) h / 1000 = kWh
        result = db.execute(
            text("""
                SELECT
                    meter_id,
                    COUNT(*) as cnt,
                    SUM(power * :interval_hours / 1000) as total_energy_kwh,
                    AVG(power) as avg_power,
                    AVG(voltage) as avg_voltage,
                    AVG(current) as avg_current,
                    AVG(frequency) as avg_frequency,
                    AVG(power_factor) as avg_pf,
                    SUM(CASE WHEN status = 'NORMAL' THEN 1 ELSE 0 END) as norm_cnt,
                    SUM(CASE WHEN status = 'WARNING' THEN 1 ELSE 0 END) as warn_cnt,
                    SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END) as err_cnt
                FROM raw_readings
                WHERE meter_id = :m AND timestamp >= :s AND timestamp < :e
                GROUP BY meter_id
            """),
            {"m": "SM-001", "s": start, "e": end, "interval_hours": 1.0/3600.0}
        ).fetchone()
        
        if result and result.cnt > 0:
            agg = HourlyAggregate(
                id=str(uuid.uuid4()),
                meter_id="SM-001",
                hour_start=start,
                total_energy_kwh=round(result.total_energy_kwh or 0.0, 3),
                avg_power=round(result.avg_power or 0.0, 2),
                avg_voltage=round(result.avg_voltage or 0.0, 2),
                avg_current=round(result.avg_current or 0.0, 2),
                avg_frequency=round(result.avg_frequency or 0.0, 2),
                avg_pf=round(result.avg_pf or 0.0, 2),
                normal_count=result.norm_cnt or 0,
                warning_count=result.warn_cnt or 0,
                error_count=result.err_cnt or 0,
                total_readings=result.cnt
            )
            db.add(agg)
            db.commit()
            if result.avg_power and result.avg_power > 1000:
                msg = f"High average power {result.avg_power:.0f}W during hour {start.strftime('%H:%M')}"
                log_event(db, "peak_usage", msg)
                owner_id = get_meter_owner("SM-001")
                if owner_id:
                    send_notification(owner_id, "Peak Usage Alert", msg)
    except Exception as e:
        db.rollback()
        print(f"Hourly aggregation error: {e}")
    finally:
        db.close()

def hourly_aggregation_job():
    while True:
        time.sleep(60)
        now = datetime.utcnow()
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)
        # Always recompute current hour’s aggregate (idempotent – deletes old one)
        compute_hourly_aggregate(current_hour_start, current_hour_start + timedelta(hours=1))
        # At the top of the hour, also finalise the previous hour (just in case)
        if now.minute == 0:
            previous_hour_start = current_hour_start - timedelta(hours=1)
            compute_hourly_aggregate(previous_hour_start, current_hour_start)

def compute_daily_aggregate(day: date):
    db = SessionLocal()
    try:
        db.execute(
            text("DELETE FROM daily_aggregates WHERE meter_id = :m AND day = :d"),
            {"m": "SM-001", "d": day}
        )
        result = db.execute(
            text("""
                SELECT
                    meter_id,
                    SUM(total_energy_kwh) as daily_energy,
                    MAX(avg_power) as peak_power,
                    AVG(avg_power) as avg_power,
                    SUM(normal_count) as norm_cnt,
                    SUM(warning_count) as warn_cnt,
                    SUM(error_count) as err_cnt
                FROM hourly_aggregates
                WHERE meter_id = :m AND DATE(hour_start) = :d
                GROUP BY meter_id
            """),
            {"m": "SM-001", "d": day}
        ).fetchone()
        if result:
            agg = DailyAggregate(
                id=str(uuid.uuid4()),
                meter_id="SM-001",
                day=day,
                total_energy_kwh=round(result.daily_energy, 3) if result.daily_energy else 0.0,
                peak_power=round(result.peak_power, 2) if result.peak_power else 0.0,
                avg_power=round(result.avg_power, 2) if result.avg_power else 0.0,
                normal_hours=result.norm_cnt or 0,
                warning_hours=result.warn_cnt or 0,
                error_hours=result.err_cnt or 0
            )
            db.add(agg)
            db.commit()
    except Exception as e:
        db.rollback()
    finally:
        db.close()

def daily_aggregation_job():
    while True:
        time.sleep(3600)
        now = datetime.utcnow()
        if now.hour == 0 and now.minute == 0:
            yesterday = (now - timedelta(days=1)).date()
            compute_daily_aggregate(yesterday)

def cleanup_raw_data():
    while True:
        time.sleep(86400)
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=5)
            deleted = db.query(RawReading).filter(RawReading.timestamp < cutoff).delete()
            db.commit()
            if deleted:
                print(f"Cleanup: removed {deleted} raw readings older than 5 days")
        except Exception as e:
            db.rollback()
        finally:
            db.close()

def log_event(db: Session, etype: str, message: str, details: str = None):
    event = ActivityLog(
        id=str(uuid.uuid4()),
        timestamp=datetime.utcnow(),
        type=etype,
        message=message,
        details=details
    )
    db.add(event)
    db.commit()

# ===================== NOTIFICATION HELPERS =====================
def get_meter_owner(meter_number: str) -> Optional[str]:
    token = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{DEVICE_SERVICE_URL}/devices", headers=headers)
            resp.raise_for_status()
            devices = resp.json()
            for d in devices:
                if d.get("meter_number") == meter_number:
                    return d.get("user_id")
    except Exception as e:
        print(f"Could not get owner for meter {meter_number}: {e}")
    return None

def get_user_email(user_id: str) -> Optional[str]:
    token = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{USER_SERVICE_URL}/user/{user_id}", headers=headers)
            resp.raise_for_status()
            return resp.json().get("email")
    except Exception as e:
        print(f"Could not get email for user {user_id}: {e}")
    return None

def send_notification(user_id: str, subject: str, message: str):
    email = get_user_email(user_id)
    if not email:
        print(f"Skipping notification – no email for user {user_id}")
        return
    token = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "email": email,
        "subject": subject,
        "message": message,
        "user_id": user_id
    }
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(f"{NOTIFICATION_SERVICE_URL}/send", json=payload, headers=headers)
            if resp.status_code == 200:
                print(f"Notification sent to {email}")
            else:
                print(f"Notification failed: {resp.status_code}")
    except Exception as e:
        print(f"Notification error: {e}")

@app.on_event("startup")
def startup():
    threading.Thread(target=poll_and_store, daemon=True).start()
    threading.Thread(target=hourly_aggregation_job, daemon=True).start()
    threading.Thread(target=daily_aggregation_job, daemon=True).start()
    threading.Thread(target=cleanup_raw_data, daemon=True).start()

# ===================== DASHBOARD ENDPOINTS (all use SM-001) =====================
@app.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)

    last_week_same_day = today - timedelta(days=7)
    first_day_this_month = today.replace(day=1)
    if today.month == 1:
        first_day_last_month = today.replace(year=today.year-1, month=12, day=1)
        last_day_last_month = today.replace(year=today.year-1, month=12, day=31)
    else:
        first_day_last_month = today.replace(month=today.month-1, day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)

    today_usage = db.query(HourlyAggregate).filter(
        HourlyAggregate.meter_id == "SM-001",
        func.date(HourlyAggregate.hour_start) == today
    ).with_entities(func.sum(HourlyAggregate.total_energy_kwh)).scalar() or 0.0

    yesterday_usage = db.query(HourlyAggregate).filter(
        HourlyAggregate.meter_id == "SM-001",
        func.date(HourlyAggregate.hour_start) == yesterday
    ).with_entities(func.sum(HourlyAggregate.total_energy_kwh)).scalar() or 0.0

    if yesterday_usage > 0:
        usage_change = round(((today_usage - yesterday_usage) / yesterday_usage) * 100, 1)
    else:
        usage_change = 0.0

    cost = round(today_usage * ELECTRICITY_RATE, 2)
    last_week_usage = db.query(HourlyAggregate).filter(
        HourlyAggregate.meter_id == "SM-001",
        func.date(HourlyAggregate.hour_start) == last_week_same_day
    ).with_entities(func.sum(HourlyAggregate.total_energy_kwh)).scalar() or 0.0
    if last_week_usage > 0:
        cost_change = round(((cost - last_week_usage * ELECTRICITY_RATE) / (last_week_usage * ELECTRICITY_RATE)) * 100, 1)
    else:
        cost_change = 0.0

    baseline_usage = db.query(HourlyAggregate).filter(
        HourlyAggregate.meter_id == "SM-001",
        HourlyAggregate.hour_start >= first_day_last_month,
        HourlyAggregate.hour_start <= last_day_last_month
    ).with_entities(func.sum(HourlyAggregate.total_energy_kwh) / func.count(func.distinct(func.date(HourlyAggregate.hour_start)))).scalar() or today_usage

    carbon_saved = round((baseline_usage - today_usage) * CARBON_FACTOR, 2)
    carbon_change = 0.0

    since_24h = datetime.utcnow() - timedelta(hours=24)
    total = db.query(RawReading).filter(RawReading.meter_id=="SM-001", RawReading.timestamp >= since_24h).count()
    normal = db.query(RawReading).filter(RawReading.meter_id=="SM-001", RawReading.timestamp >= since_24h, RawReading.status=="NORMAL").count()
    reliability = round((normal / total * 100), 2) if total > 0 else 100.0
    reliability_change = 0.02

    return {
        "today_usage_kwh": round(today_usage, 1),
        "usage_change_percent": usage_change,
        "estimated_cost": cost,
        "cost_change_percent": cost_change,
        "carbon_saved_kg": carbon_saved,
        "carbon_change_percent": carbon_change,
        "grid_reliability_percent": reliability,
        "reliability_change_percent": reliability_change,
        "meter_id": "SM-001"
    }

@app.get("/dashboard/energy-chart")
def energy_chart(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=6)
    days = []
    for i in range(7):
        d = start_date + timedelta(days=i)
        total = db.query(DailyAggregate).filter(
            DailyAggregate.meter_id == "SM-001",
            DailyAggregate.day == d
        ).with_entities(DailyAggregate.total_energy_kwh).scalar()
        if total is None and d == end_date:
            total = db.query(HourlyAggregate).filter(
                HourlyAggregate.meter_id == "SM-001",
                func.date(HourlyAggregate.hour_start) == d
            ).with_entities(func.sum(HourlyAggregate.total_energy_kwh)).scalar() or 0.0
        days.append({
            "date": d.isoformat(),
            "energy_kwh": round(total, 1) if total else 0.0
        })
    if len(days) >= 2:
        avg_last3 = sum(d["energy_kwh"] for d in days[-3:]) / 3
        forecast = round(avg_last3, 1)
    else:
        forecast = days[-1]["energy_kwh"] if days else 0.0
    return {
        "meter_id": "SM-001",
        "daily": days,
        "forecast_next_day": forecast
    }

@app.get("/dashboard/devices")
def get_devices(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    token = create_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{DEVICE_SERVICE_URL}/devices", headers=headers)
            if resp.status_code == 200:
                devices = resp.json()
                result = []
                for d in devices:
                    result.append({
                        "id": d["id"],
                        "name": d.get("meter_number", d.get("name", "Unknown")),  # use meter_number as name
                        "location": d.get("location", ""),
                        "status": "On" if d.get("active", True) else "Off"
                    })
                return result
    except Exception as e:
        print(f"Device service not reachable, using simulated devices: {e}")
    # Fallback
    simulated_devices = [
        {"id": "dev-001", "name": "Smart Thermostat", "location": "Living Room", "status": random.choice(["On", "Standby"])},
        {"id": "dev-002", "name": "Smart Plug", "location": "Home Office", "status": random.choice(["On", "Off"])},
        {"id": "dev-003", "name": "Smart Light", "location": "Kitchen", "status": random.choice(["On", "Standby", "Off"])},
        {"id": "dev-004", "name": "Smart TV", "location": "Bedroom", "status": "On"},
    ]
    return simulated_devices

@app.get("/dashboard/activity")
def recent_activity(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    activities = db.query(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(10).all()
    return [
        {
            "id": a.id,
            "timestamp": a.timestamp.isoformat(),
            "type": a.type,
            "message": a.message,
            "details": a.details
        } for a in activities
    ]

@app.get("/dashboard/tips")
def smart_tips(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    tips = []
    now = datetime.utcnow()
    evening_usage = db.query(HourlyAggregate).filter(
        HourlyAggregate.meter_id == "SM-001",
        HourlyAggregate.hour_start >= now.replace(hour=18, minute=0, second=0),
        HourlyAggregate.hour_start < now.replace(hour=22, minute=0, second=0)
    ).with_entities(func.avg(HourlyAggregate.avg_power)).scalar()
    if evening_usage and evening_usage > 800:
        tips.append({
            "title": "Peak hours",
            "description": "Shift high-usage tasks to morning (6-9 AM) to save up to 15%."
        })
    else:
        tips.append({
            "title": "Optimize usage",
            "description": "Your evening usage is efficient. Keep it up!"
        })
    tips.append({
        "title": "Device standby",
        "description": "Kitchen light has been on standby for 3h. Consider turning off."
    })
    tips.append({
        "title": "Add a smart plug",
        "description": "Control your devices remotely and reduce standby power."
    })
    return tips[:3]

@app.get("/dashboard/meter-status")
def meter_status(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    last = db.query(RawReading).filter(RawReading.meter_id=="SM-001").order_by(RawReading.timestamp.desc()).first()
    if last and (datetime.utcnow() - last.timestamp).total_seconds() < 10:
        status = "connected"
    else:
        status = "disconnected"
    return {
        "meter_id": "SM-001",
        "status": status
    }


@app.post("/debug/force-hourly")
def force_hourly():
    """Force an hourly aggregate for the current hour (debug)."""
    now = datetime.utcnow()
    start_of_hour = now.replace(minute=0, second=0, microsecond=0)
    end_of_hour = start_of_hour + timedelta(hours=1)
    compute_hourly_aggregate(start_of_hour, end_of_hour)
    return {"status": "done", "hour": start_of_hour.isoformat()}

@app.get("/health")
def health():
    return {"status": "running"}