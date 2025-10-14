# # from fastapi import FastAPI, Request, HTTPException
# # import time

# # app = FastAPI(title="Rate Limiter Service")

# # # In-memory API key store (in real app, use DB)
# # # Keys aligned with test client headers
# # clients = {
# #     "KEY_BASIC": {"limit_per_minute": 5},
# #     "KEY_PREMIUM": {"limit_per_minute": 10},
# # }

# # # In-memory counters with TTL (no external services)
# # # Structure: counters[scope][key] = {"count": int, "expires_at": epoch_seconds}
# # counters = {
# #     "minute": {},
# #     "day": {},
# # }

# # def _is_expired(entry, now_ts):
# #     return entry is None or entry.get("expires_at", 0) <= now_ts

# # def increment_with_ttl(scope, key, ttl_seconds, now_ts):
# #     bucket = counters[scope]
# #     entry = bucket.get(key)
# #     if _is_expired(entry, now_ts):
# #         # Initialize fresh window
# #         bucket[key] = {"count": 1, "expires_at": now_ts + ttl_seconds}
# #         return 1
# #     # Still valid window
# #     entry["count"] += 1
# #     return entry["count"]

# # def get_count(scope, key, now_ts):
# #     entry = counters[scope].get(key)
# #     if _is_expired(entry, now_ts):
# #         # Lazy cleanup of expired entries
# #         counters[scope].pop(key, None)
# #         return 0
# #     return int(entry.get("count", 0))

# # @app.middleware("http")
# # async def rate_limiter(request: Request, call_next):
# #     # Allow unauthenticated access to public endpoints
# #     # public_paths = {"/", "/dashboard", "/status", "/docs", "/redoc", "/openapi.json"}
# #     public_paths = {"/", "/dashboard", "/status", "/docs", "/redoc", "/openapi.json", "/data"}

# #     if request.url.path in public_paths:
# #         return await call_next(request)

# #     api_key = request.headers.get("x-api-key")
# #     if not api_key or api_key not in clients:
# #         raise HTTPException(status_code=401, detail="Invalid or missing API key")

# #     limit = clients[api_key]["limit_per_minute"]

# #     # Count requests per key per minute
# #     now = int(time.time())
# #     current_minute_window = now // 60
# #     current_day_window = now // 86400

# #     minute_key = f"min:{api_key}:{current_minute_window}"
# #     day_key = f"day:{api_key}:{current_day_window}"

# #     # Increment counters in-memory with TTL
# #     minute_count = increment_with_ttl("minute", minute_key, 60, now)
# #     _ = increment_with_ttl("day", day_key, 86400, now)

# #     if minute_count > limit:
# #         raise HTTPException(status_code=429, detail="Rate limit exceeded")

# #     response = await call_next(request)
# #     return response


# # @app.get("/")
# # def root():
# #     return {"message": "Welcome to Rate Limiter Service"}

# # @app.get("/data")
# # def get_data():
# #     return {"status": "ok", "data": "sample"}


# # @app.get("/status")
# # def get_status():
# #     now = int(time.time())
# #     current_minute_window = now // 60
# #     current_day_window = now // 86400

# #     status = {}
# #     for key in clients.keys():
# #         minute_key = f"min:{key}:{current_minute_window}"
# #         day_key = f"day:{key}:{current_day_window}"

# #         minute_used = get_count("minute", minute_key, now)
# #         day_used = get_count("day", day_key, now)
# #         status[key] = {
# #             "limit_per_minute": clients[key]["limit_per_minute"],
# #             "minute_used": minute_used,
# #             "day_used": day_used,
# #         }
# #     return status


# # @app.get("/dashboard")
# # def dashboard():
# #     data = get_status()
# #     # Simple HTML dashboard
# #     rows = []
# #     for key, stats in data.items():
# #         rows.append(
# #             f"<tr><td>{key}</td><td>{stats['minute_used']}/{stats['limit_per_minute']}</td><td>{stats['day_used']}</td></tr>"
# #         )
# #     table_rows = "".join(rows)
# #     html = f"""
# #     <html>
# #       <head>
# #         <title>Rate Limiter Dashboard</title>
# #         <style>
# #           body {{ font-family: Arial, sans-serif; margin: 24px; }}
# #           table {{ border-collapse: collapse; width: 600px; }}
# #           th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
# #           th {{ background: #f4f4f4; }}
# #         </style>
# #       </head>
# #       <body>
# #         <h2>API Usage</h2>
# #         <table>
# #           <thead>
# #             <tr><th>API Key</th><th>Per Minute</th><th>Day Used</th></tr>
# #           </thead>
# #           <tbody>
# #             {table_rows}
# #           </tbody>
# #         </table>
# #       </body>
# #     </html>
# #     """
# #     return html


# from fastapi import FastAPI, Request, Form, HTTPException, status
# from fastapi.responses import HTMLResponse, RedirectResponse
# from fastapi.staticfiles import StaticFiles
# from fastapi.templating import Jinja2Templates
# import time
# import secrets

# app = FastAPI(title="API Key Management Service")

# # Templates folder
# templates = Jinja2Templates(directory="templates")
# app.mount("/static", StaticFiles(directory="static"), name="static")

# # In-memory stores
# users = {}  # username -> password
# clients = {}  # api_key -> {"name": username, "limit_per_minute": int}
# sessions = {}  # session_id -> username
# counters = {"minute": {}, "day": {}}
# time_usage = {}  # api_key -> total seconds

# # Default per-minute limit
# DEFAULT_LIMIT_PER_MINUTE = 3

# # -------------------------
# # Utility functions
# # -------------------------
# def _is_expired(entry, now_ts):
#     return entry is None or entry.get("expires_at", 0) <= now_ts

# def increment_with_ttl(scope, key, ttl_seconds, now_ts):
#     bucket = counters[scope]
#     entry = bucket.get(key)
#     if _is_expired(entry, now_ts):
#         bucket[key] = {"count": 1, "expires_at": now_ts + ttl_seconds}
#         return 1
#     entry["count"] += 1
#     return entry["count"]

# def get_count(scope, key, now_ts):
#     entry = counters[scope].get(key)
#     if _is_expired(entry, now_ts):
#         counters[scope].pop(key, None)
#         return 0
#     return int(entry.get("count", 0))

# # -------------------------
# # Middleware: Rate limiter
# # -------------------------
# @app.middleware("http")
# async def rate_limiter(request: Request, call_next):
#     # Only skip static pages and login/register
#     public_paths = {"/", "/login", "/register", "/static", "/dashboard"}

#     if any(request.url.path.startswith(p) for p in public_paths):
#         return await call_next(request)

#     api_key = request.headers.get("x-api-key")
#     if not api_key or api_key not in clients:
#         raise HTTPException(status_code=401, detail="Invalid or missing API key")

#     limit = clients[api_key]["limit_per_minute"]
#     now = int(time.time())
#     current_minute_window = now // 60
#     current_day_window = now // 86400

#     minute_key = f"min:{api_key}:{current_minute_window}"
#     day_key = f"day:{api_key}:{current_day_window}"

#     minute_count = increment_with_ttl("minute", minute_key, 60, now)
#     _ = increment_with_ttl("day", day_key, 86400, now)

#     if minute_count > limit:
#         raise HTTPException(status_code=429, detail="Rate limit exceeded")

#     start_time = time.time()
#     response = await call_next(request)
#     elapsed = time.time() - start_time
#     time_usage[api_key] = time_usage.get(api_key, 0) + elapsed

#     return response

# # -------------------------
# # Routes: User registration/login
# # -------------------------
# @app.get("/", response_class=HTMLResponse)
# def home(request: Request):
#     return templates.TemplateResponse("home.html", {"request": request})

# @app.get("/register", response_class=HTMLResponse)
# def register_page(request: Request):
#     return templates.TemplateResponse("register.html", {"request": request})

# @app.post("/register")
# def register_user(username: str = Form(...), password: str = Form(...)):
#     if username in users:
#         raise HTTPException(status_code=400, detail="Username already exists")
#     users[username] = password
#     return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

# @app.get("/login", response_class=HTMLResponse)
# def login_page(request: Request):
#     return templates.TemplateResponse("login.html", {"request": request})

# @app.post("/login")
# def login_user(username: str = Form(...), password: str = Form(...)):
#     if username not in users or users[username] != password:
#         raise HTTPException(status_code=401, detail="Invalid username or password")
#     # Generate session
#     session_id = secrets.token_hex(16)
#     sessions[session_id] = username

#     # Generate API key for user if not exists
#     existing_key = next((k for k, v in clients.items() if v["name"] == username), None)
#     if not existing_key:
#         api_key = secrets.token_hex(16)
#         clients[api_key] = {"name": username, "limit_per_minute": DEFAULT_LIMIT_PER_MINUTE}
#         time_usage[api_key] = 0

#     response = RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)
#     response.set_cookie(key="session_id", value=session_id)
#     return response

# # -------------------------
# # Dashboard: admin view
# # -------------------------
# @app.get("/dashboard", response_class=HTMLResponse)
# def dashboard(request: Request):
#     session_id = request.cookies.get("session_id")
#     if not session_id or session_id not in sessions:
#         return RedirectResponse("/login")

#     rows = []
#     now = int(time.time())
#     for key, stats in clients.items():
#         current_minute_window = now // 60
#         current_day_window = now // 86400
#         minute_key = f"min:{key}:{current_minute_window}"
#         day_key = f"day:{key}:{current_day_window}"
#         minute_used = get_count("minute", minute_key, now)
#         day_used = get_count("day", day_key, now)
#         total_time = round(time_usage.get(key, 0), 2)
#         rows.append(f"<tr><td>{stats['name']}</td><td>{key}</td>"
#                     f"<td>{minute_used}/{stats['limit_per_minute']}</td>"
#                     f"<td>{day_used}</td><td>{total_time}s</td></tr>")

#     table_rows = "".join(rows)
#     html = f"""
#     <html>
#       <head><title>Dashboard</title></head>
#       <body>
#         <h2>API Usage Dashboard</h2>
#         <table border='1'>
#           <tr><th>Name</th><th>API Key</th><th>Per Minute</th><th>Day Used</th><th>Total Time</th></tr>
#           {table_rows}
#         </table>
#       </body>
#     </html>
#     """
#     return HTMLResponse(html)
# @app.get("/dashboard_api/{api_key}", response_class=HTMLResponse)
# def dashboard_api(api_key: str):
#     now = int(time.time())
#     current_minute_window = now // 60
#     current_day_window = now // 86400
#     minute_key = f"min:{api_key}:{current_minute_window}"
#     day_key = f"day:{api_key}:{current_day_window}"
#     minute_used = get_count("minute", minute_key, now)
#     day_used = get_count("day", day_key, now)
#     total_time = round(time_usage.get(api_key, 0), 2)
#     html = f"""
#         <h2>API Key Dashboard</h2>
#         <p>API Key: {api_key}</p>
#         <p>Minute Used: {minute_used}</p>
#         <p>Day Used: {day_used}</p>
#         <p>Total Time: {total_time}s</p>
#     """
#     return HTMLResponse(html)

# # -------------------------
# # Sample rate-limited API
# # -------------------------
# @app.get("/data")
# def get_data():
#     return {"status": "ok", "data": "This is your rate-limited API response."}



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
    users = db.query(User).all()
    rows = []
    for u in users:
        total_requests = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id).count()
        rate_limited = db.query(APIRequestLog).filter(APIRequestLog.user_id==u.id, APIRequestLog.status=="rate_limited").count()
        rows.append(f"<tr><td><a href='/user/{u.id}'>{u.username}</a></td><td>{u.api_key}</td>"
                    f"<td>{u.limit_per_minute}</td><td>{total_requests}</td><td>{rate_limited}</td></tr>")

    table_rows = "".join(rows)
    html = f"""
    <html>
      <head><title>Dashboard</title></head>
      <body>
        <h2>Users Dashboard</h2>
        <table border='1'>
          <tr><th>Username</th><th>API Key</th><th>Per Minute</th><th>Total Requests</th><th>Rate Limited</th></tr>
          {table_rows}
        </table>
      </body>
    </html>
    """
    return HTMLResponse(html)

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
