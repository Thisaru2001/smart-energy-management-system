import random
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

app = FastAPI(title="Energy Simulator – Latest Reading Only")

# ========== AUTH ==========
security = HTTPBearer()

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return decode_token(credentials.credentials)

# ========== SIMULATED READING GENERATOR ==========
# Add a global variable to keep the cumulative energy value
METER_IDS = ["SM-001"]
cumulative_energy = 0.0



def generate_reading() -> dict:
    global cumulative_energy
    now = datetime.utcnow()
    hour = now.hour

    # Realistic base power according to the hour of the day
    if 0 <= hour < 5:
        base_power = random.uniform(50, 150)       # night
    elif 5 <= hour < 9:
        base_power = random.uniform(400, 800)      # morning peak
    elif 9 <= hour < 16:
        base_power = random.uniform(150, 400)      # daytime
    elif 16 <= hour < 20:
        base_power = random.uniform(600, 1200)     # evening peak
    else:  # 20‑23
        base_power = random.uniform(200, 500)      # late evening

    # Add small random fluctuation (±20 W)
    power = base_power + random.uniform(-20, 20)
    power = max(0, power)   # never negative

    voltage = 230 + random.uniform(-2, 2)          # stable grid voltage
    current = power / voltage if voltage > 0 else 0
    frequency = 50.0 + random.uniform(-0.05, 0.05)
    pf = random.uniform(0.92, 0.99)                # good power factor for a home

    # Cumulative energy update
    energy_inc = (power / 1000) * (1 / 3600)       # kWh per second
    cumulative_energy += energy_inc
    energy_kwh = round(cumulative_energy, 3)

    status = random.choice(["NORMAL"] * 98 + ["WARNING"] * 2)  # mostly normal

    return {
        "meterId": "SM-001",
        "timestamp": now.isoformat(),
        "voltage": round(voltage, 2),
        "current": round(current, 2),
        "power": round(power, 2),
        "energy_kwh": energy_kwh,
        "frequency": round(frequency, 2),
        "power_factor": round(pf, 2),
        "status": status
    }

# ========== ONLY ENDPOINT ==========
@app.get("/readings/latest")
def latest_reading(current_user: dict = Depends(get_current_user)):
    """Return a fresh, live simulated reading."""
    return generate_reading()

@app.get("/health")
def health():
    return {"status": "running"}