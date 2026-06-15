import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware 
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# Backend service URLs
SERVICE_MAP = {
    "user": os.getenv("USER_SERVICE_URL", "http://127.0.0.1:2001"),
    "device": os.getenv("DEVICE_SERVICE_URL", "http://127.0.0.1:2002"),
    "energy": os.getenv("ENERGY_SIMULATOR_URL", "http://127.0.0.1:2003"),
    "analytics": os.getenv("ANALYTICS_SERVICE_URL", "http://127.0.0.1:2004"),
    "billing": os.getenv("BILLING_SERVICE_URL", "http://127.0.0.1:2005"),
    "notification": os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:2006"),
}


PUBLIC_ROUTES = {
    # User auth (POST)
    ("POST", "/register"),
    ("POST", "/login"),
    ("POST", "/forgot-password"),
    ("POST", "/reset-password"),

    # Swagger / OpenAPI (GET)
    ("GET", "/docs"),
    ("GET", "/openapi.json"),
    ("GET", "/redoc"),
    ("GET", "/favicon.ico"),

    # Health checks (GET)
    ("GET", "/health"),
}

app = FastAPI(title="SmartEnergy API Gateway")

# ---------- CORS Middleware ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "http://localhost:3000",
        
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Helper functions ----------
def verify_token(token: str) -> dict:
    """Verify JWT token and return payload."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

async def forward_request_with_path(service_url: str, path: str, request: Request) -> Response:
    """Forward the request using a specific path."""
    client = httpx.AsyncClient(base_url=service_url, timeout=30.0)
    try:
        url = httpx.URL(path=path, query=request.url.query.encode("utf-8"))
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        body = await request.body()

        response = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Service unavailable: {e}")
    finally:
        await client.aclose()

# ---------- Gateway index page ----------
@app.get("/docs", include_in_schema=False)
async def gateway_docs():
    """A simple page linking to each service's Swagger UI."""
    links = "".join(
        f'<li><a href="/{name}/docs" target="_blank">{name.capitalize()} Service</a></li>'
        for name in SERVICE_MAP.keys()
    )
    html = f"""
    <html>
    <head><title>SmartEnergy API Gateway</title></head>
    <body>
        <h2>SmartEnergy – Service Documentation</h2>
        <ul>{links}</ul>
        <p>Click a link to open the Swagger UI for that service.</p>
    </body>
    </html>
    """
    return Response(content=html, media_type="text/html")

# ---------- Main catch-all route ----------
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def gateway(request: Request, path: str):
    # Allow CORS preflight (OPTIONS) without authentication
    if request.method == "OPTIONS":
        return Response(status_code=200)

    
    parts = path.split("/")
    service_name = parts[0] if parts else ""

    if service_name not in SERVICE_MAP:
        raise HTTPException(status_code=404, detail="Service not found")

    
    actual_path = "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"

    
    is_public = (request.method, actual_path) in PUBLIC_ROUTES
    if not is_public:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid token")
        token = auth_header.split(" ")[1]
        verify_token(token)

    
    target_url = SERVICE_MAP[service_name]
    return await forward_request_with_path(target_url, actual_path, request)
