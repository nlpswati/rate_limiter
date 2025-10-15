from fastapi import FastAPI, Request, Form, HTTPException, status,Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import time
import secrets


from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, func
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
    limit_per_minute = Column(Integer, default=30)
    limit_per_day = Column(Integer, default=5000)
    logs = relationship("APIRequestLog", back_populates="user")

class ApiKey(Base):
    __tablename__ = "apikeys"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    key = Column(String, unique=True)
    active = Column(Boolean, default=True)
    tier = Column(String, default="free")  # free, pro, enterprise
    limit_per_minute = Column(Integer, default=30)
    limit_per_day = Column(Integer, default=5000)
    expires_at = Column(DateTime, nullable=True)

class APIRequestLog(Base):
    __tablename__ = "apirequestlogs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    endpoint = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    response_time = Column(Float)
    status = Column(String)  # "ok" or "rate_limited"
    api_key = Column(String, nullable=True)  # which API key made the call

    user = relationship("User", back_populates="logs")

# Create DB
engine = create_engine("sqlite:///rate_limiter.db")
Base.metadata.create_all(engine)
# Lightweight SQLite migration to add missing columns
from sqlalchemy import text
def _ensure_columns():
    with engine.connect() as conn:
        # Check existing columns in users
        res = conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()
        col_names = {row[1] for row in res}  # row[1] is name
        if "limit_per_day" not in col_names:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN limit_per_day INTEGER DEFAULT 5000")
        if "limit_per_minute" not in col_names:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN limit_per_minute INTEGER DEFAULT 30")
        # Check apirequestlogs has api_key
        res2 = conn.exec_driver_sql("PRAGMA table_info(apirequestlogs)").fetchall()
        log_cols = {row[1] for row in res2}
        if "api_key" not in log_cols:
            conn.exec_driver_sql("ALTER TABLE apirequestlogs ADD COLUMN api_key TEXT")
        # apikeys.expires_at
        res3 = conn.exec_driver_sql("PRAGMA table_info(apikeys)").fetchall()
        key_cols = {row[1] for row in res3}
        if "expires_at" not in key_cols:
            conn.exec_driver_sql("ALTER TABLE apikeys ADD COLUMN expires_at DATETIME")

_ensure_columns()

# Ensure apikeys table exists (for older DBs)
ApiKey.__table__.create(bind=engine, checkfirst=True)

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

# Default per-minute limit (Free tier)
DEFAULT_LIMIT_PER_MINUTE = 30

# -------------------------
# Plan definitions
# -------------------------
PLANS = {
    "free": {
        "name": "Free (Developer)",
        "rpm": 10,
        "rpd": 5000,
    },
    "pro": {
        "name": "Pro (Startup)",
        "rpm": 15,
        "rpd": 100_000,
    },
    "enterprise": {
        "name": "Enterprise (Custom)",
        "rpm": 20,  # baseline; can be higher per-contract
        "rpd": None,   # unlimited per day
    },
}

def plan_from_limit(limit_per_minute: int):
    if limit_per_minute is None:
        return PLANS["free"]
    if limit_per_minute >= 1000:
        return PLANS["enterprise"]
    if limit_per_minute >= 200:
        return PLANS["pro"]
    return PLANS["free"]
from fastapi import Request, HTTPException
import time
from sqlalchemy.orm import Session
import hashlib
import os

# Ensure an admin user exists so the dashboard is viewable
def _ensure_admin_user():
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username=="admin").first()
        if not admin:
            api_key = secrets.token_hex(16)
            admin = User(
                username="admin",
                password="admin",  # demo only
                api_key=api_key,
                limit_per_minute=PLANS["enterprise"]["rpm"],
                limit_per_day=None,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
        # Sync clients in-memory mapping
        clients[admin.api_key] = {"name": admin.username, "limit_per_minute": admin.limit_per_minute or DEFAULT_LIMIT_PER_MINUTE}
        time_usage.setdefault(admin.api_key, 0)
    finally:
        db.close()

_ensure_admin_user()

def rate_limiter_and_log(request: Request, db: Session, user: User, api_key_value: str = None):
    now = int(time.time())
    current_minute_window = now // 60

    # Count requests in the last minute/day from logs
    one_minute_ago = datetime.datetime.utcnow() - datetime.timedelta(seconds=60)
    one_day_ago = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    recent_minute = db.query(APIRequestLog).filter(
        APIRequestLog.user_id == user.id,
        APIRequestLog.timestamp >= one_minute_ago,
        APIRequestLog.status == "ok",
        (APIRequestLog.api_key == api_key_value) if api_key_value else True,
    ).count()
    recent_day = db.query(APIRequestLog).filter(
        APIRequestLog.user_id == user.id,
        APIRequestLog.timestamp >= one_day_ago,
        APIRequestLog.status == "ok",
        (APIRequestLog.api_key == api_key_value) if api_key_value else True,
    ).count()

    start_time = time.time()

    # If key specified, prefer key-level limits; else fallback to user-level
    rpm_limit = None
    rpd_limit = None
    api_key_obj = None
    if api_key_value:
        api_key_obj = db.query(ApiKey).filter(ApiKey.key == api_key_value, ApiKey.user_id == user.id).first()
        if not api_key_obj or not api_key_obj.active:
            raise HTTPException(status_code=403, detail="API key inactive or not found")
        if api_key_obj.expires_at and api_key_obj.expires_at <= datetime.datetime.utcnow():
            raise HTTPException(status_code=403, detail="API key expired")
        rpm_limit = api_key_obj.limit_per_minute
        rpd_limit = api_key_obj.limit_per_day
    if rpm_limit is None:
        rpm_limit = user.limit_per_minute or DEFAULT_LIMIT_PER_MINUTE
    if rpd_limit is None:
        rpd_limit = user.limit_per_day  # None means unlimited

    if recent_minute >= rpm_limit or (rpd_limit is not None and recent_day >= rpd_limit):
        # Log rate-limited request
        db.add(APIRequestLog(
            user_id=user.id,
            endpoint=request.url.path,
            response_time=0,
            status="rate_limited",
            api_key=api_key_value
        ))
        
        # Check for consecutive rate limit violations and auto-deactivate if needed
        if api_key_value and api_key_obj:
            # Count recent rate-limited requests in the last 5 minutes
            five_minutes_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
            recent_rate_limits = db.query(APIRequestLog).filter(
                APIRequestLog.user_id == user.id,
                APIRequestLog.timestamp >= five_minutes_ago,
                APIRequestLog.status == "rate_limited",
                APIRequestLog.api_key == api_key_value
            ).count()
            
            # Auto-deactivate after 3 consecutive rate limit violations (for testing)
            if recent_rate_limits >= 3:
                api_key_obj.active = False
                db.add(api_key_obj)
                print(f"Auto-deactivated API key {api_key_value} due to excessive rate limiting ({recent_rate_limits} violations)")
        
        db.commit()
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    response = "ok"  # placeholder, actual call_next will be awaited in FastAPI

    elapsed = time.time() - start_time
    # Log successful request
    db.add(APIRequestLog(
        user_id=user.id,
        endpoint=request.url.path,
        response_time=elapsed,
        status="ok",
        api_key=api_key_value
    ))
    db.commit()

    return response

# Log retention cleanup based on plan retention windows
def cleanup_old_logs(db: Session):
    # Determine per-key retention, default to 30 days if unknown
    now = datetime.datetime.utcnow()
    keys = db.query(ApiKey).all()
    for k in keys:
        retention_days = 30
        if k.tier == "free":
            retention_days = 7
        elif k.tier == "pro":
            retention_days = 30
        elif k.tier == "enterprise":
            retention_days = 180
        cutoff = now - datetime.timedelta(days=retention_days)
        db.query(APIRequestLog).filter(APIRequestLog.api_key==k.key, APIRequestLog.timestamp < cutoff).delete()
    db.commit()

# Deactivate expired keys
def deactivate_expired_keys(db: Session):
    now = datetime.datetime.utcnow()
    expired_keys = db.query(ApiKey).filter(ApiKey.active==True, ApiKey.expires_at != None, ApiKey.expires_at <= now).all()
    for k in expired_keys:
        k.active = False
        db.add(k)
    if expired_keys:
        db.commit()

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
    public_paths = [
        "/", "/login", "/register", "/static",
        "/welcome", "/change_plan", "/create_key", "/toggle_key", "/delete_key",
        "/dashboard", "/data", "/user/"
    ]

    if any(request.url.path.startswith(p) for p in public_paths) or request.url.path.startswith("/static"):
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
    # Hash password using PBKDF2
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    hashed = "pbkdf2$100000$" + salt.hex() + "$" + dk.hex()
    # Default plan: Free
    user = User(username=username, password=hashed, api_key=api_key, limit_per_minute=PLANS["free"]["rpm"], limit_per_day=PLANS["free"]["rpd"])
    db.add(user)
    db.commit()
    db.refresh(user)

    # Add to clients dict
    clients[api_key] = {"name": username, "limit_per_minute": PLANS["free"]["rpm"]}

    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})
@app.post("/login")
def login_user(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # Verify password (supports legacy plain-text stored passwords)
    ok = False
    if user.password and user.password.startswith("pbkdf2$"):
        try:
            _algo, iter_s, salt_hex, hash_hex = user.password.split("$")
            iters = int(iter_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
            dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iters)
            ok = secrets.compare_digest(dk, expected)
        except Exception:
            ok = False
    else:
        ok = (user.password == password)
        # Migrate legacy passwords to hashed on successful login
        if ok:
            salt = os.urandom(16)
            dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
            user.password = "pbkdf2$100000$" + salt.hex() + "$" + dk.hex()
            db.add(user)
            db.commit()
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    session_id = secrets.token_hex(16)
    sessions[session_id] = username

    # Ensure the user is in clients dict
    if user.api_key not in clients:
        clients[user.api_key] = {"name": username, "limit_per_minute": user.limit_per_minute}
        time_usage[user.api_key] = 0

    # Admin goes straight to dashboard, others to welcome
    redirect_path = "/dashboard" if username == "admin" else "/welcome"
    response = RedirectResponse(redirect_path, status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="session_id", value=session_id, httponly=True, samesite="lax")
    return response

# -------------------------
# Welcome page showing API key and plan
# -------------------------
@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request, db: Session = Depends(get_db)):
    # Auto-deactivate expired keys before displaying
    deactivate_expired_keys(db)
    session_id = request.cookies.get("session_id")
    username = sessions.get(session_id)
    if not username:
        return RedirectResponse("/login")

    user = db.query(User).filter(User.username==username).first()
    if not user:
        return RedirectResponse("/login")

    # Determine tier from limit_per_minute
    limit = user.limit_per_minute or 30
    if limit >= 1000:
        tier_key = "enterprise"
    elif limit >= 200:
        tier_key = "pro"
    else:
        tier_key = "free"
    if limit >= 1000:
        tier = {
            "name": "Enterprise (Custom)",
            "price": "$199+/month",
            "rpm": "1,000–10,000+",
            "rpd": "Unlimited",
            "retention": "180 days",
            "dashboard": "Advanced",
            "email": "Email & SMS (priority)",
            "custom_limits": "Dynamic policies",
        }
    elif limit >= 200:
        tier = {
            "name": "Pro (Startup)",
            "price": "$29/month",
            "rpm": 200,
            "rpd": "100,000",
            "retention": "30 days",
            "dashboard": "Full analytics & logs",
            "email": "Email alerts",
            "custom_limits": "Custom limits (manual rotation)",
        }
    else:
        tier = {
            "name": "Free (Developer)",
            "price": "$0",
            "rpm": 30,
            "rpd": "5,000",
            "retention": "7 days",
            "dashboard": "Basic stats",
            "email": "No",
            "custom_limits": "No",
        }

    # Load user's keys for listing
    keys = db.query(ApiKey).filter(ApiKey.user_id==user.id).all()

    return templates.TemplateResponse(
        "welcome.html",
        {
            "request": request,
            "username": user.username,
            "api_key": user.api_key,
            "tier": tier,
            "tier_key": tier_key,
            "keys": keys,
        },
    )

# -------------------------
# Dashboard: admin view
# -------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    # Only allow admin to view (username 'admin')
    session_id = request.cookies.get("session_id")
    if not session_id or sessions.get(session_id) != "admin":
        return RedirectResponse("/login")

    # Auto-deactivate expired keys before displaying
    deactivate_expired_keys(db)

    users_q = db.query(User).all()
    users_view = []
    total_keys = 0
    total_requests_sum = 0
    total_rate_limited_sum = 0
    active_keys = 0
    expired_keys = 0
    
    # Calculate time-based metrics
    now = datetime.datetime.utcnow()
    last_hour = now - datetime.timedelta(hours=1)
    last_24h = now - datetime.timedelta(hours=24)
    last_7d = now - datetime.timedelta(days=7)
    
    requests_last_hour = db.query(APIRequestLog).filter(APIRequestLog.timestamp >= last_hour).count()
    requests_last_24h = db.query(APIRequestLog).filter(APIRequestLog.timestamp >= last_24h).count()
    requests_last_7d = db.query(APIRequestLog).filter(APIRequestLog.timestamp >= last_7d).count()
    
    rate_limited_last_hour = db.query(APIRequestLog).filter(
        APIRequestLog.timestamp >= last_hour, 
        APIRequestLog.status == "rate_limited"
    ).count()
    rate_limited_last_24h = db.query(APIRequestLog).filter(
        APIRequestLog.timestamp >= last_24h, 
        APIRequestLog.status == "rate_limited"
    ).count()
    
    # Get top users by activity
    try:
        top_users = db.query(
            User.username, 
            func.count(APIRequestLog.id).label('total_requests')
        ).join(APIRequestLog, User.id == APIRequestLog.user_id).group_by(
            User.id, User.username
        ).order_by(func.count(APIRequestLog.id).desc()).limit(5).all()
    except Exception:
        top_users = []
    
    # Get recent activity (last 10 requests)
    try:
        recent_activity = db.query(APIRequestLog).order_by(
            APIRequestLog.timestamp.desc()
        ).limit(10).all()
    except Exception:
        recent_activity = []
    
    for u in users_q:
        total_requests = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id).count()
        rate_limited = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id, APIRequestLog.status=="rate_limited").count()
        keys = db.query(ApiKey).filter(ApiKey.user_id==u.id).all()
        key_views = []
        for k in keys:
            key_req = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id, APIRequestLog.api_key==k.key, APIRequestLog.status=="ok").count()
            key_limited = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id, APIRequestLog.api_key==k.key, APIRequestLog.status=="rate_limited").count()
            key_views.append({
                "key": k.key,
                "active": k.active,
                "tier": k.tier,
                "limit_per_minute": k.limit_per_minute,
                "limit_per_day": k.limit_per_day,
                "requests": key_req,
                "limited": key_limited,
            })
            if k.active:
                active_keys += 1
            else:
                expired_keys += 1
                
        users_view.append({
            "id": u.id,
            "username": u.username,
            "api_key": u.api_key,
            "limit_per_minute": u.limit_per_minute,
            "total_requests": total_requests,
            "rate_limited": rate_limited,
            "api_keys": key_views,
        })
        total_keys += len(keys)
        total_requests_sum += total_requests
        total_rate_limited_sum += rate_limited

    # Calculate success rate
    success_rate = ((total_requests_sum - total_rate_limited_sum) / total_requests_sum * 100) if total_requests_sum > 0 else 0
    
    # Calculate system health metrics
    system_health = "excellent"
    if success_rate < 80:
        system_health = "critical"
    elif success_rate < 90:
        system_health = "warning"
    elif success_rate < 95:
        system_health = "good"

    totals = {
        "users": len(users_view),
        "keys": total_keys,
        "active_keys": active_keys,
        "expired_keys": expired_keys,
        "reqs": total_requests_sum,
        "limited": total_rate_limited_sum,
        "success_rate": round(success_rate, 1),
        "requests_last_hour": requests_last_hour,
        "requests_last_24h": requests_last_24h,
        "requests_last_7d": requests_last_7d,
        "rate_limited_last_hour": rate_limited_last_hour,
        "rate_limited_last_24h": rate_limited_last_24h,
        "system_health": system_health,
        "top_users": top_users,
        "recent_activity": recent_activity,
    }

    return templates.TemplateResponse("dashboard.html", {"request": request, "users": users_view, "totals": totals})

@app.get("/user/{user_id}", response_class=HTMLResponse)
def user_details(user_id: int, request: Request, db: Session = Depends(get_db)):
    # Admin-only
    session_id = request.cookies.get("session_id")
    if not session_id or sessions.get(session_id) != "admin":
        return RedirectResponse("/login")

    user = db.query(User).filter(User.id==user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Aggregate usage: last 24 hours by hour, last 30 days by day
    now = datetime.datetime.utcnow()
    # Prepare buckets
    hours = [now - datetime.timedelta(hours=i) for i in range(23,-1,-1)]
    hour_labels = [h.replace(minute=0, second=0, microsecond=0) for h in hours]
    hour_ok = [0]*24
    hour_limited = [0]*24

    day_points = [now - datetime.timedelta(days=i) for i in range(29,-1,-1)]
    day_labels = [d.replace(hour=0, minute=0, second=0, microsecond=0) for d in day_points]
    day_ok = [0]*30
    day_limited = [0]*30

    # Fetch logs for windows
    logs_24h = db.query(APIRequestLog).filter(APIRequestLog.user_id==user_id, APIRequestLog.timestamp >= now - datetime.timedelta(hours=24)).all()
    for log in logs_24h:
        ts = log.timestamp.replace(minute=0, second=0, microsecond=0)
        if ts in hour_labels:
            idx = hour_labels.index(ts)
            if log.status == "ok":
                hour_ok[idx] += 1
            else:
                hour_limited[idx] += 1

    logs_30d = db.query(APIRequestLog).filter(APIRequestLog.user_id==user_id, APIRequestLog.timestamp >= now - datetime.timedelta(days=30)).all()
    for log in logs_30d:
        ts = log.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if ts in day_labels:
            idx = day_labels.index(ts)
            if log.status == "ok":
                day_ok[idx] += 1
            else:
                day_limited[idx] += 1

    # Summary cards
    total_ok = sum(day_ok)
    total_limited = sum(day_limited)
    keys = db.query(ApiKey).filter(ApiKey.user_id==user_id).all()

    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "user": user,
        "keys": keys,
        "hour_labels": [h.isoformat() for h in hour_labels],
        "hour_ok": hour_ok,
        "hour_limited": hour_limited,
        "day_labels": [d.date().isoformat() for d in day_labels],
        "day_ok": day_ok,
        "day_limited": day_limited,
        "total_ok": total_ok,
        "total_limited": total_limited,
    })

@app.get("/user/{user_id}/key/{api_key}", response_class=HTMLResponse)
def api_key_details(user_id: int, api_key: str, request: Request, db: Session = Depends(get_db)):
    # Admin-only
    session_id = request.cookies.get("session_id")
    if not session_id or sessions.get(session_id) != "admin":
        return RedirectResponse("/login")

    user = db.query(User).filter(User.id==user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    key_obj = db.query(ApiKey).filter(ApiKey.key==api_key, ApiKey.user_id==user_id).first()
    if not key_obj:
        raise HTTPException(status_code=404, detail="API key not found")

    # Aggregate usage for this specific API key: last hour by minute, last 24 hours by hour, last 30 days by day
    now = datetime.datetime.utcnow()
    
    # Prepare buckets for last hour (by minute)
    minutes = [now - datetime.timedelta(minutes=i) for i in range(59,-1,-1)]
    minute_labels = [m.replace(second=0, microsecond=0) for m in minutes]
    minute_ok = [0]*60
    minute_limited = [0]*60
    
    # Prepare buckets for last 24 hours (by hour)
    hours = [now - datetime.timedelta(hours=i) for i in range(23,-1,-1)]
    hour_labels = [h.replace(minute=0, second=0, microsecond=0) for h in hours]
    hour_ok = [0]*24
    hour_limited = [0]*24

    # Prepare buckets for last 30 days (by day)
    day_points = [now - datetime.timedelta(days=i) for i in range(29,-1,-1)]
    day_labels = [d.replace(hour=0, minute=0, second=0, microsecond=0) for d in day_points]
    day_ok = [0]*30
    day_limited = [0]*30

    # Fetch logs for this specific API key - last hour (by minute)
    logs_last_hour = db.query(APIRequestLog).filter(
        APIRequestLog.user_id==user_id, 
        APIRequestLog.api_key==api_key,
        APIRequestLog.timestamp >= now - datetime.timedelta(hours=1)
    ).all()
    for log in logs_last_hour:
        ts = log.timestamp.replace(second=0, microsecond=0)
        if ts in minute_labels:
            idx = minute_labels.index(ts)
            if log.status == "ok":
                minute_ok[idx] += 1
            else:
                minute_limited[idx] += 1

    # Fetch logs for this specific API key - last 24 hours (by hour)
    logs_24h = db.query(APIRequestLog).filter(
        APIRequestLog.user_id==user_id, 
        APIRequestLog.api_key==api_key,
        APIRequestLog.timestamp >= now - datetime.timedelta(hours=24)
    ).all()
    for log in logs_24h:
        ts = log.timestamp.replace(minute=0, second=0, microsecond=0)
        if ts in hour_labels:
            idx = hour_labels.index(ts)
            if log.status == "ok":
                hour_ok[idx] += 1
            else:
                hour_limited[idx] += 1

    logs_30d = db.query(APIRequestLog).filter(
        APIRequestLog.user_id==user_id, 
        APIRequestLog.api_key==api_key,
        APIRequestLog.timestamp >= now - datetime.timedelta(days=30)
    ).all()
    for log in logs_30d:
        ts = log.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if ts in day_labels:
            idx = day_labels.index(ts)
            if log.status == "ok":
                day_ok[idx] += 1
            else:
                day_limited[idx] += 1

    # Summary cards for this API key
    total_ok = sum(day_ok)
    total_limited = sum(day_limited)
    
    # Get recent requests for this key (last 50)
    recent_requests = db.query(APIRequestLog).filter(
        APIRequestLog.user_id==user_id,
        APIRequestLog.api_key==api_key
    ).order_by(APIRequestLog.timestamp.desc()).limit(50).all()

    # Calculate key-specific metrics
    key_requests_last_hour = db.query(APIRequestLog).filter(
        APIRequestLog.user_id==user_id,
        APIRequestLog.api_key==api_key,
        APIRequestLog.timestamp >= now - datetime.timedelta(hours=1),
        APIRequestLog.status == "ok"
    ).count()
    
    key_rate_limits_last_hour = db.query(APIRequestLog).filter(
        APIRequestLog.user_id==user_id,
        APIRequestLog.api_key==api_key,
        APIRequestLog.timestamp >= now - datetime.timedelta(hours=1),
        APIRequestLog.status == "rate_limited"
    ).count()

    return templates.TemplateResponse("api_key_detail.html", {
        "request": request,
        "user": user,
        "api_key": key_obj,
        "minute_labels": [m.strftime('%H:%M') for m in minute_labels],
        "minute_ok": minute_ok,
        "minute_limited": minute_limited,
        "hour_labels": [h.isoformat() for h in hour_labels],
        "hour_ok": hour_ok,
        "hour_limited": hour_limited,
        "day_labels": [d.date().isoformat() for d in day_labels],
        "day_ok": day_ok,
        "day_limited": day_limited,
        "total_ok": total_ok,
        "total_limited": total_limited,
        "recent_requests": recent_requests,
        "key_requests_last_hour": key_requests_last_hour,
        "key_rate_limits_last_hour": key_rate_limits_last_hour,
    })

# -------------------------
# Sample rate-limited API
# # -------------------------
# @app.get("/data")
# def get_data(request: Request):
#     return {"status": "ok", "data": "This is your rate-limited API response."}
@app.get("/data")
def get_data(request: Request, db: Session = Depends(get_db)):
    api_key = request.headers.get("x-api-key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    key_row = db.query(ApiKey).filter(ApiKey.key==api_key).first()
    if not key_row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    # Optionally deactivate expired keys here as well
    deactivate_expired_keys(db)
    if not key_row.active:
        raise HTTPException(status_code=403, detail="API key inactive")

    user = db.query(User).filter(User.id == key_row.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Apply rate limiting & log
    rate_limiter_and_log(request, db, user, api_key_value=api_key)
    # Periodic cleanup (lightweight): once per request
    cleanup_old_logs(db)
    
    return {"status": "ok", "data": "This is your rate-limited API response."}

# -------------------------
# Change plan endpoint
# -------------------------
@app.post("/change_plan")
def change_plan(plan: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    username = sessions.get(session_id)
    if not username:
        return RedirectResponse("/login")

    plan_key = plan.lower()
    if plan_key not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan selection")

    u = db.query(User).filter(User.username==username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    # Update limits according to plan
    u.limit_per_minute = PLANS[plan_key]["rpm"]
    u.limit_per_day = PLANS[plan_key]["rpd"]
    db.add(u)
    db.commit()

    # Keep in-memory clients map consistent
    if u.api_key in clients:
        clients[u.api_key]["limit_per_minute"] = u.limit_per_minute

    return RedirectResponse("/welcome", status_code=status.HTTP_302_FOUND)

# Create new API key for the logged-in user
@app.post("/create_key")
def create_key(plan: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    username = sessions.get(session_id)
    if not username:
        return RedirectResponse("/login")

    plan_key = (plan or "free").lower()
    if plan_key not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan selection")

    u = db.query(User).filter(User.username==username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    new_key = secrets.token_hex(16)
    # Auto-set expiry based on plan
    now = datetime.datetime.utcnow()
    if plan_key == "free":
        exp_dt = now + datetime.timedelta(days=1)
    elif plan_key == "pro":
        exp_dt = now + datetime.timedelta(weeks=1)
    else:
        exp_dt = now + datetime.timedelta(days=30)
    k = ApiKey(
        user_id=u.id,
        key=new_key,
        active=True,
        tier=plan_key,
        limit_per_minute=PLANS[plan_key]["rpm"],
        limit_per_day=PLANS[plan_key]["rpd"],
        expires_at=exp_dt,
    )
    db.add(k)
    db.commit()

    return RedirectResponse("/welcome", status_code=status.HTTP_302_FOUND)

# Toggle active/inactive for an API key
@app.post("/toggle_key")
def toggle_key(key: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    username = sessions.get(session_id)
    if not username:
        return RedirectResponse("/login")
    u = db.query(User).filter(User.username==username).first()
    k = db.query(ApiKey).filter(ApiKey.key==key, ApiKey.user_id==u.id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found")
    k.active = not k.active
    db.add(k)
    db.commit()
    return RedirectResponse("/welcome", status_code=status.HTTP_302_FOUND)

# Delete an API key
@app.post("/delete_key")
def delete_key(key: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    username = sessions.get(session_id)
    if not username:
        return RedirectResponse("/login")
    u = db.query(User).filter(User.username==username).first()
    k = db.query(ApiKey).filter(ApiKey.key==key, ApiKey.user_id==u.id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found")
    db.delete(k)
    db.commit()
    return RedirectResponse("/welcome", status_code=status.HTTP_302_FOUND)
