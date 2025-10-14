from fastapi import FastAPI, Request, Form, HTTPException, status,Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import time
import secrets


from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime

Base = declarative_base()
from sqlalchemy.orm import Session

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password = Column(String)  # store hashed password
    api_key = Column(String, unique=True)
    limit_per_minute = Column(Integer, default=3)
    logs = relationship("APIRequestLog", back_populates="user")

class APIRequestLog(Base):
    __tablename__ = "apirequestlogs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    endpoint = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    response_time = Column(Float)
    status = Column(String)  # "ok" or "rate_limited"

    user = relationship("User", back_populates="logs")

# Create DB
engine = create_engine("sqlite:///rate_limiter.db")
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)




app = FastAPI(title="API Key Management Service")

# Templates folder
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory stores
users = {}       # username -> password
clients = {}     # api_key -> {"name": username, "limit_per_minute": int}
sessions = {}    # session_id -> username
counters = {"minute": {}, "day": {}}
time_usage = {}  # api_key -> total seconds

# Default per-minute limit
DEFAULT_LIMIT_PER_MINUTE = 3
from fastapi import Request, HTTPException
import time
from sqlalchemy.orm import Session

def rate_limiter_and_log(request: Request, db: Session, user: User):
    now = int(time.time())
    current_minute_window = now // 60

    # Count requests in the last minute from logs
    one_minute_ago = datetime.datetime.utcnow() - datetime.timedelta(seconds=60)
    recent_count = db.query(APIRequestLog).filter(
        APIRequestLog.user_id == user.id,
        APIRequestLog.timestamp >= one_minute_ago,
    ).count()

    start_time = time.time()

    if recent_count >= user.limit_per_minute:
        # Log rate-limited request
        db.add(APIRequestLog(
            user_id=user.id,
            endpoint=request.url.path,
            response_time=0,
            status="rate_limited"
        ))
        db.commit()
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    response = "ok"  # placeholder, actual call_next will be awaited in FastAPI

    elapsed = time.time() - start_time
    # Log successful request
    db.add(APIRequestLog(
        user_id=user.id,
        endpoint=request.url.path,
        response_time=elapsed,
        status="ok"
    ))
    db.commit()

    return response

# -------------------------
# Utility functions
# -------------------------
def _is_expired(entry, now_ts):
    return entry is None or entry.get("expires_at", 0) <= now_ts

def increment_with_ttl(scope, key, ttl_seconds, now_ts):
    bucket = counters[scope]
    entry = bucket.get(key)
    if _is_expired(entry, now_ts):
        bucket[key] = {"count": 1, "expires_at": now_ts + ttl_seconds}
        return 1
    entry["count"] += 1
    return entry["count"]

def get_count(scope, key, now_ts):
    entry = counters[scope].get(key)
    if _is_expired(entry, now_ts):
        counters[scope].pop(key, None)
        return 0
    return int(entry.get("count", 0))

# -------------------------
# Middleware: Rate limiter
# -------------------------
@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    public_paths = ["/", "/login", "/register", "/static", "/dashboard"]
    
    if request.url.path in public_paths or request.url.path.startswith("/static"):
        return await call_next(request)

    api_key = request.headers.get("x-api-key")
    if not api_key or api_key not in clients:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Get DB session
    db = SessionLocal()
    try:
        username = clients[api_key]["name"]
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Rate-limit & log request
        rate_limiter_and_log(request, db, user)
    finally:
        db.close()

    response = await call_next(request)
    return response


# -------------------------
# Routes: User registration/login
# -------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
def register_user(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username==username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")

    api_key = secrets.token_hex(16)
    user = User(username=username, password=password, api_key=api_key)
    db.add(user)
    db.commit()
    db.refresh(user)

    # Add to clients dict
    clients[api_key] = {"name": username, "limit_per_minute": DEFAULT_LIMIT_PER_MINUTE}

    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})
@app.post("/login")
def login_user(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username, User.password == password).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    session_id = secrets.token_hex(16)
    sessions[session_id] = username

    # Ensure the user is in clients dict
    if user.api_key not in clients:
        clients[user.api_key] = {"name": username, "limit_per_minute": user.limit_per_minute}
        time_usage[user.api_key] = 0

    response = RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session_id", value=session_id)
    return response

# -------------------------
# Dashboard: admin view
# -------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    users_q = db.query(User).all()
    users_view = []
    for u in users_q:
        total_requests = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id).count()
        rate_limited = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id, APIRequestLog.status=="rate_limited").count()
        users_view.append({
            "id": u.id,
            "username": u.username,
            "api_key": u.api_key,
            "limit_per_minute": u.limit_per_minute,
            "total_requests": total_requests,
            "rate_limited": rate_limited,
        })

    return templates.TemplateResponse("dashboard.html", {"request": request, "users": users_view})

@app.get("/user/{user_id}", response_class=HTMLResponse)
def user_details(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id==user_id).first()
    logs = db.query(APIRequestLog).filter(APIRequestLog.user_id==user_id).order_by(APIRequestLog.timestamp.desc()).all()
    rows = [f"<tr><td>{log.timestamp}</td><td>{log.endpoint}</td><td>{log.response_time:.3f}s</td><td>{log.status}</td></tr>" for log in logs]
    table_rows = "".join(rows)
    html = f"""
    <html>
      <head><title>{user.username} Logs</title></head>
      <body>
        <h2>{user.username} API Logs</h2>
        <table border='1'>
          <tr><th>Timestamp</th><th>Endpoint</th><th>Response Time</th><th>Status</th></tr>
          {table_rows}
        </table>
        <a href='/dashboard'>Back to dashboard</a>
      </body>
    </html>
    """
    return HTMLResponse(html)

# -------------------------
# Sample rate-limited API
# # -------------------------
# @app.get("/data")
# def get_data(request: Request):
#     return {"status": "ok", "data": "This is your rate-limited API response."}
@app.get("/data")
def get_data(request: Request, db: Session = Depends(get_db)):
    api_key = request.headers.get("x-api-key")
    if not api_key or api_key not in clients:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    
    # Get user from DB
    username = clients[api_key]["name"]
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Apply rate limiting & log
    rate_limiter_and_log(request, db, user)
    
    return {"status": "ok", "data": "This is your rate-limited API response."}
