import os
import json
import re
import secrets
import hmac
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode, urlsplit
import pymysql

import bcrypt
import phonenumbers
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    jsonify, send_from_directory
)
from sqlalchemy import create_engine, text

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_URL = "sqlite:///" + os.path.join(BASE_DIR, "gerayoride.db")
DB_URL = (os.getenv("DATABASE_URL") or "").strip() or DEFAULT_DB_URL

try:
    engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
except Exception:
    if DB_URL != DEFAULT_DB_URL:
        DB_URL = DEFAULT_DB_URL
        engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
    else:
        raise

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_AVATAR = "https://cdn-icons-png.flaticon.com/512/149/149071.png"


def db(sql, params=None, one=False, commit=False):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        if commit:
            return None
        rows = [dict(row._mapping) for row in result]
        return rows[0] if rows and one else (rows if not one else None)


def init_db():
    if DB_URL.startswith("sqlite"):
        with engine.begin() as c:
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name VARCHAR(100) NOT NULL,
                email VARCHAR(100) UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL,
                account_type VARCHAR(20) NOT NULL,
                password VARCHAR(255) NOT NULL,
                verification_token VARCHAR(255),
                token_expires DATETIME,
                email_verified INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                photo VARCHAR(255),
                latitude REAL,
                longitude REAL,
                online_status VARCHAR(20) DEFAULT 'Offline'
            )""")
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS driver_documents(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                national_id_front VARCHAR(255),
                national_id_back VARCHAR(255),
                permit_license VARCHAR(255),
                plate_photo VARCHAR(255),
                plate_number VARCHAR(100),
                status VARCHAR(50) DEFAULT 'Pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                driver_id INTEGER,
                pickup VARCHAR(255) NOT NULL,
                destination VARCHAR(255) NOT NULL,
                ride_type VARCHAR(100) NOT NULL,
                distance_km REAL NOT NULL,
                price REAL NOT NULL,
                status VARCHAR(50) DEFAULT 'Pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS password_resets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token VARCHAR(255) NOT NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
    elif DB_URL.startswith(("mysql://", "mysql+")):
        with engine.begin() as c:
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                full_name VARCHAR(100) NOT NULL,
                email VARCHAR(100) NOT NULL UNIQUE,
                phone VARCHAR(20) NOT NULL,
                account_type ENUM('Client','Driver','Mechanic') NOT NULL,
                password VARCHAR(255) NOT NULL,
                verification_token VARCHAR(255),
                token_expires DATETIME,
                email_verified TINYINT(1) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                photo VARCHAR(255),
                latitude DECIMAL(10,8),
                longitude DECIMAL(11,8),
                online_status VARCHAR(20) DEFAULT 'Offline'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS driver_documents (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                national_id_front VARCHAR(255),
                national_id_back VARCHAR(255),
                permit_license VARCHAR(255),
                plate_photo VARCHAR(255),
                plate_number VARCHAR(100),
                status VARCHAR(50) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            account_type = c.exec_driver_sql("""SELECT COLUMN_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'
                  AND COLUMN_NAME='account_type'""").scalar()
            if account_type and "'Mechanic'" not in account_type:
                c.exec_driver_sql(
                    "ALTER TABLE users MODIFY account_type "
                    "ENUM('Client','Driver','Mechanic') NOT NULL"
                )
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                client_id INT NOT NULL,
                driver_id INT NULL,
                pickup VARCHAR(255) NOT NULL,
                destination VARCHAR(255) NOT NULL,
                ride_type VARCHAR(100) NOT NULL,
                distance_km DECIMAL(10,2) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                status VARCHAR(50) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(driver_id) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS password_resets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                token VARCHAR(255) NOT NULL,
                expires_at DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")


init_db()


def init_gerayofix_db():
    """Create GerayoFix tables without altering the existing ride tables."""
    if DB_URL.startswith("sqlite"):
        statements = [
            """CREATE TABLE IF NOT EXISTS mechanic_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE,
                business_name VARCHAR(120) NOT NULL, specialties VARCHAR(255) NOT NULL,
                service_area VARCHAR(255) NOT NULL, workshop_address VARCHAR(255),
                roadside_available INTEGER DEFAULT 1, workshop_available INTEGER DEFAULT 1,
                latitude REAL, longitude REAL, experience_years INTEGER DEFAULT 0,
                description TEXT, status VARCHAR(30) DEFAULT 'Pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS repair_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
                mechanic_id INTEGER, service_type VARCHAR(80) NOT NULL,
                assistance_type VARCHAR(30) NOT NULL, issue_description TEXT NOT NULL,
                location_text VARCHAR(255) NOT NULL, latitude REAL, longitude REAL,
                vehicle_type VARCHAR(40) NOT NULL, vehicle_details VARCHAR(255),
                status VARCHAR(30) DEFAULT 'Pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)"""
        ]
    else:
        statements = [
            """CREATE TABLE IF NOT EXISTS mechanic_profiles (
                id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL UNIQUE,
                business_name VARCHAR(120) NOT NULL, specialties VARCHAR(255) NOT NULL,
                service_area VARCHAR(255) NOT NULL, workshop_address VARCHAR(255),
                roadside_available TINYINT(1) DEFAULT 1, workshop_available TINYINT(1) DEFAULT 1,
                latitude DECIMAL(10,8), longitude DECIMAL(11,8), experience_years INT DEFAULT 0,
                description TEXT, status VARCHAR(30) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_mechanic_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            """CREATE TABLE IF NOT EXISTS repair_requests (
                id INT AUTO_INCREMENT PRIMARY KEY, customer_id INT NOT NULL,
                mechanic_id INT NULL, service_type VARCHAR(80) NOT NULL,
                assistance_type VARCHAR(30) NOT NULL, issue_description TEXT NOT NULL,
                location_text VARCHAR(255) NOT NULL, latitude DECIMAL(10,8), longitude DECIMAL(11,8),
                vehicle_type VARCHAR(40) NOT NULL, vehicle_details VARCHAR(255),
                status VARCHAR(30) DEFAULT 'Pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_repair_customer FOREIGN KEY(customer_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_repair_mechanic FOREIGN KEY(mechanic_id) REFERENCES mechanic_profiles(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        ]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


init_gerayofix_db()


def translations():
    lang = session.get("lang", "rw")
    if lang not in ("en", "rw"):
        lang = "rw"
    path = os.path.join(BASE_DIR, f"lang_{lang}.json")
    if not os.path.exists(path):
        lang = "en"
        path = os.path.join(BASE_DIR, "lang_en.json")
    with open(path, encoding="utf-8") as f:
        return lang, json.load(f)


@app.context_processor
def inject():
    lang, t = translations()
    query = request.args.to_dict(flat=True)
    query.pop("lang", None)
    query.pop("language", None)
    query.pop("next", None)
    next_page = request.path
    if query:
        next_page += "?" + urlencode(query)
    return {
        "lang": lang,
        "t": t,
        "cache_bust": int(datetime.now().timestamp()),
        "default_avatar": DEFAULT_AVATAR,
        "language_urls": {
            code: url_for("set_language", language=code, next=next_page)
            for code in ("rw", "en")
        },
    }


def tmsg(key):
    _, t = translations()
    return t.get(key, key)


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def save_upload(file_obj, prefix="file"):
    if not file_obj or not file_obj.filename:
        return ""
    original = os.path.basename(file_obj.filename).replace(" ", "_")
    filename = f"{int(datetime.now().timestamp() * 1000)}_{prefix}_{original}"
    file_obj.save(os.path.join(UPLOAD_DIR, filename))
    return f"uploads/{filename}"


# ---------------- PUBLIC PAGES ----------------

@app.route("/")
def index():
    requested = request.args.get("lang")
    if requested in ("en", "rw"):
        session["lang"] = requested
    booking_error = session.pop("booking_error", "")
    return render_template("index.html", booking_error=booking_error)


@app.route("/set-language")
def set_language():
    language = request.args.get("language")
    if language in ("rw", "en"):
        session["lang"] = language
    next_page = request.args.get("next", "/")
    if not next_page.startswith("/") or next_page.startswith("//"):
        next_page = url_for("index")
    return redirect(next_page)


@app.route("/begin-ride", methods=["POST"])
def begin_ride():
    pickup = request.form.get("pickup", "").strip()
    destination = request.form.get("destination", "").strip()
    ride_type = request.form.get("ride_type", "Passenger Ride").strip()
    if not pickup or not destination or len(pickup) > 255 or len(destination) > 255:
        session["booking_error"] = tmsg("home_booking_error")
        return redirect(url_for("index", _anchor="booking"))
    if ride_type not in ("Passenger Ride", "Food Delivery"):
        ride_type = "Passenger Ride"
    if session.get("user_id") and session.get("role") != "Client":
        session["booking_error"] = tmsg("home_client_account_error")
        return redirect(url_for("index", _anchor="booking"))

    session["booking_draft"] = {
        "pickup": pickup,
        "destination": destination,
        "ride_type": ride_type,
    }
    if session.get("user_id"):
        return redirect(url_for("client_dashboard"))
    return redirect(url_for("login"))


@app.route("/client")
def client():
    return render_template("client.html")


@app.route("/driver")
def driver():
    return render_template("driver.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ---------------- AUTH ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    message = ""
    next_url = request.values.get("next", "")
    parsed_next = urlsplit(next_url)
    if (
        not next_url.startswith("/")
        or next_url.startswith("//")
        or "\\" in next_url
        or parsed_next.scheme
        or parsed_next.netloc
    ):
        next_url = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = db("SELECT * FROM users WHERE email=:email", {"email": email}, one=True) if email and password else None
        if not email or not password:
            message = tmsg("login_credentials_required")
        elif not user:
            message = tmsg("email_not_found") if "email_not_found" in translations()[1] else "Email not found."
        elif not bcrypt.checkpw(password.encode(), user["password"].encode()):
            message = "Wrong password."
        else:
            session.update(
                user_id=user["id"],
                name=user["full_name"],
                email=user["email"],
                role=user["account_type"],
            )
            if next_url and user["account_type"] != "Mechanic":
                return redirect(next_url)
            mechanic_profile = db(
                "SELECT id,status FROM mechanic_profiles WHERE user_id=:id",
                {"id": user["id"]},
                one=True,
            )
            if user["account_type"] == "Mechanic" or mechanic_profile:
                if mechanic_profile and mechanic_profile["status"] == "Approved":
                    return redirect(url_for("gerayofix_dashboard"))
                if mechanic_profile:
                    return redirect(url_for("gerayofix_application_status"))
                return redirect(url_for("gerayofix_apply"))
            if user["account_type"] == "Client":
                return redirect(url_for("client_dashboard"))
            if user["account_type"] == "Mechanic":
                profile = db(
                    "SELECT id FROM mechanic_profiles WHERE user_id=:id",
                    {"id": user["id"]},
                    one=True,
                )
                return redirect(url_for(
                    "gerayofix_my_requests" if profile else "gerayofix_apply"
                ))
            if session.pop("booking_draft", None):
                session["booking_error"] = tmsg("home_client_account_error")
                return redirect(url_for("index", _anchor="booking"))
            doc = db("SELECT * FROM driver_documents WHERE user_id=:id", {"id": user["id"]}, one=True)
            if not doc:
                return redirect(url_for("document_verification"))
            if doc["status"] == "Approved":
                return redirect(url_for("driver_dashboard"))
            return redirect(url_for("approve"))
    return render_template("login.html", message=message, next_url=next_url)


@app.route("/register", methods=["GET", "POST"])
def register():
    message = ""
    account_type = request.args.get("account_type", "")
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        account_type = request.form.get("account_type", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        phone_is_valid = False
        phone_digits = re.sub(r"\D", "", phone)
        local_phone = len(phone_digits) == 10 and phone_digits.startswith("0")
        if (len(phone) <= 25
                and re.fullmatch(r"[+0-9(). -]+", phone)
                and local_phone):
            try:
                parsed_phone = phonenumbers.parse(phone, "RW")
                subscriber_number = str(parsed_phone.national_number)
                if (parsed_phone.country_code == 250
                        and len(subscriber_number) == 9
                        and subscriber_number.startswith(("72", "73", "78", "79"))
                        and phonenumbers.is_valid_number(parsed_phone)):
                    phone = phonenumbers.format_number(
                        parsed_phone, phonenumbers.PhoneNumberFormat.E164
                    )
                    phone_is_valid = True
            except phonenumbers.NumberParseException:
                pass

        if not phone_is_valid:
            message = tmsg("invalid_phone")
        elif (len(password) < 8 or len(password) > 64
                or not re.search(r"[A-Z]", password)
                or not re.search(r"[a-z]", password)
                or not re.search(r"[0-9]", password)
                or not re.search(r"[^A-Za-z0-9\s]", password)):
            message = tmsg("password_requirements_error")
        elif password != confirm:
            message = tmsg("password_not_match")
        elif account_type not in ("Client", "Driver", "Mechanic"):
            message = tmsg("error_invalid_account")
        elif db("SELECT id FROM users WHERE email=:email", {"email": email}, one=True):
            message = tmsg("email_exists")
        else:
            if account_type == "Mechanic":
                required_mechanic_fields = (
                    request.form.get("business_name", "").strip(),
                    request.form.get("specialties", "").strip(),
                    request.form.get("service_area", "").strip(),
                )
                services_selected = (
                    request.form.get("roadside_available")
                    or request.form.get("workshop_available")
                )
                if not all(required_mechanic_fields) or not services_selected:
                    message = tmsg("register_mechanic_required")
                    return render_template(
                        "register.html",
                        message=message,
                        selected_account_type=account_type,
                    )

            if account_type == "Driver":
                files = {
                    "id_front": request.files.get("id_front"),
                    "id_back": request.files.get("id_back"),
                    "permit_license": request.files.get("permit_license"),
                    "plate_photo": request.files.get("plate_photo"),
                }
                plate_number = request.form.get("plate_number", "").strip()
                if not all(file and getattr(file, "filename", "") for file in files.values()) or not plate_number:
                    message = tmsg("register_driver_required")
                    return render_template(
                        "register.html",
                        message=message,
                        selected_account_type=account_type,
                    )

            token = secrets.token_urlsafe(32)
            expires = datetime.utcnow() + timedelta(hours=24)
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            db("""INSERT INTO users(full_name,email,phone,account_type,password,verification_token,token_expires,email_verified)
                   VALUES(:n,:e,:p,:a,:pw,:tok,:exp,1)""",
               {"n": name, "e": email, "p": phone, "a": account_type,
                "pw": hashed, "tok": token, "exp": expires}, commit=True)

            user = db("SELECT id FROM users WHERE email=:email", {"email": email}, one=True)
            if account_type == "Driver":
                files = {
                    "id_front": request.files.get("id_front"),
                    "id_back": request.files.get("id_back"),
                    "permit_license": request.files.get("permit_license"),
                    "plate_photo": request.files.get("plate_photo"),
                }
                plate_number = request.form.get("plate_number", "").strip()
                document_values = {
                    "user_id": user["id"],
                    "id_front": save_upload(files["id_front"], "id_front"),
                    "id_back": save_upload(files["id_back"], "id_back"),
                    "permit_license": save_upload(files["permit_license"], "permit"),
                    "plate_photo": save_upload(files["plate_photo"], "plate"),
                    "plate_number": plate_number,
                }
                db("""INSERT INTO driver_documents(user_id,national_id_front,national_id_back,permit_license,plate_photo,plate_number,status)
                       VALUES(:user_id,:id_front,:id_back,:permit_license,:plate_photo,:plate_number,'Pending')""",
                   document_values, commit=True)
            elif account_type == "Mechanic":
                business_name = request.form.get("business_name", "").strip()[:120]
                specialties = request.form.get("specialties", "").strip()[:255]
                service_area = request.form.get("service_area", "").strip()[:255]
                workshop_address = request.form.get("workshop_address", "").strip()[:255]
                description = request.form.get("description", "").strip()[:2000]
                try:
                    experience_years = max(0, min(70, int(request.form.get("experience_years", "0") or 0)))
                except ValueError:
                    experience_years = 0
                try:
                    latitude = float(request.form.get("latitude", "") or 0)
                except ValueError:
                    latitude = None
                try:
                    longitude = float(request.form.get("longitude", "") or 0)
                except ValueError:
                    longitude = None
                roadside_available = 1 if request.form.get("roadside_available") else 0
                workshop_available = 1 if request.form.get("workshop_available") else 0
                db("""INSERT INTO mechanic_profiles(
                        user_id, business_name, specialties, service_area, workshop_address,
                        roadside_available, workshop_available, latitude, longitude,
                        experience_years, description, status, created_at)
                    VALUES(:user_id,:business_name,:specialties,:service_area,:workshop_address,
                        :roadside_available,:workshop_available,:latitude,:longitude,
                        :experience_years,:description,'Pending',CURRENT_TIMESTAMP)""",
                   {
                       "user_id": user["id"],
                       "business_name": business_name,
                       "specialties": specialties,
                       "service_area": service_area,
                       "workshop_address": workshop_address,
                       "roadside_available": roadside_available,
                       "workshop_available": workshop_available,
                       "latitude": latitude if latitude and -90 <= latitude <= 90 else None,
                       "longitude": longitude if longitude and -180 <= longitude <= 180 else None,
                       "experience_years": experience_years,
                       "description": description,
                   }, commit=True)

            return render_template(
                "verify.html",
                message=tmsg("register_success_active"),
            )
    return render_template(
        "register.html",
        message=message,
        selected_account_type=account_type,
    )


@app.route("/verify")
def verify():
    message = "Account verification is disabled on this system."
    return render_template("verify.html", message=message)


@app.route("/logout")
def logout():
    if session.get("user_id"):
        db("UPDATE users SET online_status='Offline' WHERE id=:id", {"id": session["user_id"]}, commit=True)
    session.clear()
    return redirect(url_for("index"))


# ---------------- GERAYOFIX: MECHANIC AND ROADSIDE REPAIR SERVICE ----------------
def _optional_coordinate(value, low, high):
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
        return number if low <= number <= high else None
    except (TypeError, ValueError):
        return None


@app.route("/gerayofix")
@login_required()
def gerayofix_find():
    area = request.args.get("area", "").strip()[:255]
    assistance = request.args.get("assistance", "").strip()
    lat = _optional_coordinate(request.args.get("lat"), -90, 90)
    lng = _optional_coordinate(request.args.get("lng"), -180, 180)
    query = "SELECT m.*,u.full_name,u.phone,u.email FROM mechanic_profiles m JOIN users u ON u.id=m.user_id WHERE m.status='Approved'"
    params = {}
    if area:
        query += " AND (m.service_area LIKE :area OR m.workshop_address LIKE :area)"
        params["area"] = f"%{area}%"
    if assistance == "Roadside":
        query += " AND m.roadside_available=1"
    elif assistance == "Workshop":
        query += " AND m.workshop_available=1"
    mechanics = db(query + " ORDER BY m.business_name", params)
    # When coordinates are supplied, calculate straight-line distance for display/sorting.
    if lat is not None and lng is not None:
        import math
        for mechanic in mechanics:
            mlat, mlng = mechanic.get("latitude"), mechanic.get("longitude")
            if mlat is not None and mlng is not None:
                a1, a2 = math.radians(lat), math.radians(float(mlat))
                dlat, dlng = a2-a1, math.radians(float(mlng)-lng)
                h = math.sin(dlat/2)**2 + math.cos(a1)*math.cos(a2)*math.sin(dlng/2)**2
                mechanic["distance_km"] = round(6371 * 2 * math.asin(min(1, math.sqrt(h))), 1)
            else:
                mechanic["distance_km"] = None
        mechanics.sort(key=lambda m: m["distance_km"] if m["distance_km"] is not None else 999999)
    own_profile = db("SELECT * FROM mechanic_profiles WHERE user_id=:id", {"id": session["user_id"]}, one=True)
    return render_template("gerayofix_find.html", mechanics=mechanics, area=area,
                           assistance=assistance, own_profile=own_profile)


@app.route("/gerayofix/apply", methods=["GET", "POST"])
@login_required()
def gerayofix_apply():
    profile = db("SELECT * FROM mechanic_profiles WHERE user_id=:id", {"id": session["user_id"]}, one=True)
    message = ""
    if profile and profile["status"] == "Pending" and request.method == "GET":
        return redirect(url_for("gerayofix_application_status"))
    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()[:120]
        specialties = request.form.get("specialties", "").strip()[:255]
        service_area = request.form.get("service_area", "").strip()[:255]
        workshop_address = request.form.get("workshop_address", "").strip()[:255]
        description = request.form.get("description", "").strip()[:2000]
        try:
            experience = max(0, min(70, int(request.form.get("experience_years", "0"))))
        except ValueError:
            experience = 0
        lat = _optional_coordinate(request.form.get("latitude"), -90, 90)
        lng = _optional_coordinate(request.form.get("longitude"), -180, 180)
        roadside = 1 if request.form.get("roadside_available") else 0
        workshop = 1 if request.form.get("workshop_available") else 0
        if not business_name or not specialties or not service_area or not (roadside or workshop):
            message = tmsg("gfix_apply_validation")
        elif profile:
            db("UPDATE mechanic_profiles SET business_name=:b,specialties=:s,service_area=:a,workshop_address=:w,description=:d,experience_years=:e,latitude=:lat,longitude=:lng,roadside_available=:r,workshop_available=:wh,status='Pending' WHERE user_id=:uid",
               {"b":business_name,"s":specialties,"a":service_area,"w":workshop_address,"d":description,"e":experience,"lat":lat,"lng":lng,"r":roadside,"wh":workshop,"uid":session["user_id"]}, commit=True)
            return redirect(url_for("gerayofix_application_status"))
        else:
            db("INSERT INTO mechanic_profiles(user_id,business_name,specialties,service_area,workshop_address,roadside_available,workshop_available,latitude,longitude,experience_years,description,status) VALUES(:uid,:b,:s,:a,:w,:r,:wh,:lat,:lng,:e,:d,'Pending')",
               {"uid":session["user_id"],"b":business_name,"s":specialties,"a":service_area,"w":workshop_address,"r":roadside,"wh":workshop,"lat":lat,"lng":lng,"e":experience,"d":description}, commit=True)
            return redirect(url_for("gerayofix_application_status"))
    profile = db("SELECT * FROM mechanic_profiles WHERE user_id=:id", {"id": session["user_id"]}, one=True)
    return render_template("gerayofix_apply.html", profile=profile, message=message)


@app.route("/gerayofix/application-status")
@login_required()
def gerayofix_application_status():
    profile = db(
        "SELECT * FROM mechanic_profiles WHERE user_id=:id",
        {"id": session["user_id"]},
        one=True,
    )
    if not profile:
        return redirect(url_for("gerayofix_apply"))
    if profile["status"] == "Approved":
        return redirect(url_for("gerayofix_dashboard"))
    return render_template("gerayofix_application_status.html", profile=profile)


@app.route("/gerayofix/dashboard")
@login_required()
def gerayofix_dashboard():
    profile = db(
        "SELECT * FROM mechanic_profiles WHERE user_id=:id",
        {"id": session["user_id"]},
        one=True,
    )
    if not profile:
        return redirect(url_for("gerayofix_apply"))
    if profile["status"] != "Approved":
        return redirect(url_for("gerayofix_application_status"))

    requests_list = db(
        """SELECT r.*,cu.full_name AS customer_name,cu.phone AS customer_phone
           FROM repair_requests r
           JOIN users cu ON cu.id=r.customer_id
           WHERE r.mechanic_id=:mechanic_id
           ORDER BY r.id DESC""",
        {"mechanic_id": profile["id"]},
    )
    stats = {
        "total": len(requests_list),
        "pending": sum(1 for repair in requests_list if repair["status"] == "Pending"),
        "accepted": sum(1 for repair in requests_list if repair["status"] == "Accepted"),
        "rejected": sum(1 for repair in requests_list if repair["status"] == "Declined"),
    }
    return render_template(
        "gerayofix_dashboard.html",
        profile=profile,
        requests_list=requests_list,
        stats=stats,
    )


@app.route("/gerayofix/request/<int:mechanic_id>", methods=["GET", "POST"])
@login_required()
def gerayofix_request(mechanic_id):
    mechanic = db("SELECT m.*,u.full_name,u.phone FROM mechanic_profiles m JOIN users u ON u.id=m.user_id WHERE m.id=:id AND m.status='Approved'", {"id":mechanic_id}, one=True)
    if not mechanic:
        return tmsg("gfix_mechanic_not_found"), 404
    message = ""
    if request.method == "POST":
        service_type = request.form.get("service_type", "").strip()[:80]
        assistance_type = request.form.get("assistance_type", "").strip()
        issue = request.form.get("issue_description", "").strip()[:3000]
        location = request.form.get("location_text", "").strip()[:255]
        vehicle = request.form.get("vehicle_type", "").strip()
        details = request.form.get("vehicle_details", "").strip()[:255]
        lat = _optional_coordinate(request.form.get("latitude"), -90, 90)
        lng = _optional_coordinate(request.form.get("longitude"), -180, 180)
        availability_ok = ((assistance_type == "Roadside" and mechanic["roadside_available"]) or
                           (assistance_type == "Workshop" and mechanic["workshop_available"]))
        if (assistance_type not in ("Roadside", "Workshop") or not availability_ok
                or vehicle not in ("Motorcycle", "Car", "Other") or not all([service_type, issue, location])):
            message = tmsg("gfix_request_validation")
        else:
            db("INSERT INTO repair_requests(customer_id,mechanic_id,service_type,assistance_type,issue_description,location_text,latitude,longitude,vehicle_type,vehicle_details,status) VALUES(:c,:m,:s,:a,:i,:l,:lat,:lng,:v,:d,'Pending')",
               {"c":session["user_id"],"m":mechanic_id,"s":service_type,"a":assistance_type,"i":issue,"l":location,"lat":lat,"lng":lng,"v":vehicle,"d":details}, commit=True)
            return redirect(url_for("gerayofix_my_requests"))
    return render_template("gerayofix_request.html", mechanic=mechanic, message=message)


@app.route("/gerayofix/my-requests")
@login_required()
def gerayofix_my_requests():
    profile = db("SELECT * FROM mechanic_profiles WHERE user_id=:uid", {"uid":session["user_id"]}, one=True)
    if profile:
        if profile["status"] == "Approved":
            return redirect(url_for("gerayofix_dashboard"))
        return redirect(url_for("gerayofix_application_status"))
    requests_list = db("SELECT r.*,m.business_name,cu.full_name AS customer_name,cu.phone AS customer_phone,mu.phone AS mechanic_phone FROM repair_requests r LEFT JOIN mechanic_profiles m ON m.id=r.mechanic_id JOIN users cu ON cu.id=r.customer_id LEFT JOIN users mu ON mu.id=m.user_id WHERE r.customer_id=:uid OR m.user_id=:uid ORDER BY r.id DESC", {"uid":session["user_id"]})
    return render_template("gerayofix_requests.html", requests_list=requests_list, profile=profile)


@app.route("/gerayofix/request-status/<int:request_id>", methods=["POST"])
@login_required()
def gerayofix_update_request(request_id):
    profile = db("SELECT * FROM mechanic_profiles WHERE user_id=:uid AND status='Approved'", {"uid":session["user_id"]}, one=True)
    action = request.form.get("action", "")
    decisions = {"Accept": "Accepted", "Reject": "Declined"}
    if not profile:
        return tmsg("gfix_not_authorized"), 403
    if action not in decisions:
        return tmsg("gfix_choose_decision"), 400
    current = db("SELECT * FROM repair_requests WHERE id=:id AND mechanic_id=:mid", {"id":request_id,"mid":profile["id"]}, one=True)
    if not current or current["status"] != "Pending":
        return redirect(url_for("gerayofix_dashboard"))
    db("UPDATE repair_requests SET status=:status,updated_at=CURRENT_TIMESTAMP WHERE id=:id", {"status":decisions[action],"id":request_id}, commit=True)
    return redirect(url_for("gerayofix_dashboard"))


# ---------------- CLIENT ----------------

@app.route("/client/dashboard", methods=["GET", "POST"])
@login_required("Client")
def client_dashboard():
    uid = session["user_id"]
    if request.method == "POST" and "update_profile" in request.form:
        user = db("SELECT * FROM users WHERE id=:id", {"id": uid}, one=True)
        photo = user.get("photo") or ""
        f = request.files.get("profile_photo")
        if f and f.filename:
            photo = save_upload(f, "profile")
        name = request.form.get("update_name", user["full_name"]).strip()
        phone = request.form.get("update_phone", user["phone"]).strip()
        db("UPDATE users SET full_name=:n, phone=:p, photo=:ph WHERE id=:id",
           {"n": name, "p": phone, "ph": photo, "id": uid}, commit=True)
        session["name"] = name
        return redirect(url_for("client_dashboard"))

    db("UPDATE users SET online_status='Online' WHERE id=:id", {"id": uid}, commit=True)
    user = db("SELECT * FROM users WHERE id=:id", {"id": uid}, one=True)
    pending_booking = session.pop("booking_draft", None)
    return render_template("client_dashboard.html", user=user, pending_booking=pending_booking)


@app.route("/request-ride", methods=["POST"])
@login_required("Client")
def request_ride():
    try:
        distance = float(request.form.get("distance_km") or 0)
    except ValueError:
        distance = 0
    ride_type = request.form.get("ride_type", "").strip()
    pickup = request.form.get("pickup", "").strip()
    destination = request.form.get("destination", "").strip()
    price = round(distance * 300, 2)
    if not pickup or not destination or not ride_type or distance <= 0:
        return redirect(url_for("client_dashboard"))
    db("""INSERT INTO requests(client_id,pickup,destination,ride_type,distance_km,price,status)
           VALUES(:client,:pickup,:destination,:ride_type,:distance,:price,'Pending')""",
       {"client": session["user_id"], "pickup": pickup, "destination": destination,
        "ride_type": ride_type, "distance": distance, "price": price}, commit=True)
    ride = db("SELECT id FROM requests WHERE client_id=:id ORDER BY id DESC LIMIT 1",
              {"id": session["user_id"]}, one=True)
    return redirect(url_for("ride_confirmation", ride_id=ride["id"]))


@app.route("/client/ride-status/<int:ride_id>")
@login_required("Client")
def ride_confirmation(ride_id):
    ride = db("SELECT * FROM requests WHERE id=:ride_id AND client_id=:client_id",
              {"ride_id": ride_id, "client_id": session["user_id"]}, one=True)
    if not ride:
        return redirect(url_for("client_dashboard"))
    return render_template("ride_confirmation.html", ride=ride)


@app.route("/client/request-driver", methods=["POST"])
@login_required("Client")
def request_driver():
    uid = session["user_id"]
    driver_id = request.form.get("driver_id")
    pickup = request.form.get("pickup", "").strip()
    destination = request.form.get("destination", "").strip()
    ride_type = request.form.get("ride_type", "Passenger Ride")
    try:
        distance = float(request.form.get("distance_km") or 0)
    except ValueError:
        distance = 0
    if not distance:
        distance = 1.0
    price = round(distance * 300, 2)
    db("""INSERT INTO requests(client_id,driver_id,pickup,destination,ride_type,distance_km,price,status)
           VALUES(:c,:d,:p,:dest,:r,:k,:pr,'Pending')""",
       {"c": uid, "d": driver_id, "p": pickup or "Current location", "dest": destination or "Destination",
        "r": ride_type, "k": distance, "pr": price}, commit=True)
    return redirect(url_for("client_dashboard"))


@app.route("/check-ride-status")
@login_required("Client")
def check_ride_status():
    ride = db("""SELECT r.*, u.full_name AS driver_name, u.phone AS driver_phone
                  FROM requests r LEFT JOIN users u ON u.id=r.driver_id
                  WHERE r.client_id=:id ORDER BY r.id DESC LIMIT 1""", {"id": session["user_id"]}, one=True)
    if not ride:
        return "<p>No ride request yet.</p>"
    driver = ""
    if ride.get("driver_name"):
        driver = f"<p><strong>Driver:</strong> {ride['driver_name']} · {ride.get('driver_phone','')}</p>"
    return f"<div class='status-result'><p><strong>{ride['pickup']}</strong> → <strong>{ride['destination']}</strong></p><p>Status: <strong>{ride['status']}</strong></p><p>Distance: {ride['distance_km']} KM · Price: {ride['price']} FRW</p>{driver}</div>"


@app.route("/fetch-nearby-drivers")
@login_required("Client")
def fetch_nearby_drivers():
    pickup = request.args.get('pickup', '')
    destination = request.args.get('destination', '')
    ride_type = request.args.get('ride_type', 'Passenger Ride')
    drivers = db("""SELECT id,full_name,phone,photo,latitude,longitude
                     FROM users
                     WHERE account_type='Driver' AND online_status='Online'
                     ORDER BY id DESC""")
    if not drivers:
        return "<p>No online drivers found.</p>"
    items = []
    for d in drivers:
        photo = url_for("uploads", name=d["photo"].replace("uploads/", "")) if d.get("photo") else DEFAULT_AVATAR
        items.append(f"""<div class='nearby-driver-box'>
          <div class='driver-left'><div class='driver-photo'><img src='{photo}' alt='Driver'></div>
          <div class='driver-info'><h4>{d['full_name']}</h4><p>{d['phone']} · Online</p></div></div>
          <div class='driver-actions'><a class='call-btn' href='tel:{d['phone']}'><i class='fas fa-phone'></i> Call</a>
          <form method='POST' action='{url_for('request_driver')}'><input type='hidden' name='driver_id' value='{d['id']}'><input type='hidden' name='pickup' value='{pickup}'><input type='hidden' name='destination' value='{destination}'><input type='hidden' name='ride_type' value='{ride_type}'><button class='request-driver-btn'>Request</button></form></div>
        </div>""")
    return "".join(items)


# ---------------- DRIVER ----------------

@app.route("/driver/dashboard", methods=["GET", "POST"])
@login_required("Driver")
def driver_dashboard():
    uid = session["user_id"]
    doc = db("SELECT * FROM driver_documents WHERE user_id=:id", {"id": uid}, one=True)
    if not doc:
        return redirect(url_for("document_verification"))
    if doc["status"] != "Approved":
        return redirect(url_for("approve"))

    if request.method == "POST":
        if "driver_status" in request.form:
            status = request.form.get("driver_status", "Offline")
            db("UPDATE users SET online_status=:status WHERE id=:id", {"status": status, "id": uid}, commit=True)
            if request.headers.get("X-Requested-With") or request.form.get("beacon"):
                return ("", 204)
            return redirect(url_for("driver_dashboard"))
        if "update_profile" in request.form:
            user = db("SELECT * FROM users WHERE id=:id", {"id": uid}, one=True)
            photo = user.get("photo") or ""
            f = request.files.get("profile_photo")
            if f and f.filename:
                photo = save_upload(f, "profile")
            name = request.form.get("update_name", user["full_name"]).strip()
            phone = request.form.get("update_phone", user["phone"]).strip()
            db("UPDATE users SET full_name=:n, phone=:p, photo=:ph WHERE id=:id",
               {"n": name, "p": phone, "ph": photo, "id": uid}, commit=True)
            session["name"] = name
            return redirect(url_for("driver_dashboard"))
        if "accept_request" in request.form:
            db("""UPDATE requests SET driver_id=:driver,status='Accepted'
                   WHERE id=:request AND status='Pending' AND (driver_id IS NULL OR driver_id=:driver)""",
               {"driver": uid, "request": request.form.get("request_id")}, commit=True)
        if "reject_request" in request.form:
            db("UPDATE requests SET status='Rejected' WHERE id=:request AND status='Pending'",
               {"request": request.form.get("request_id")}, commit=True)
        return redirect(url_for("driver_dashboard"))

    user = db("SELECT * FROM users WHERE id=:id", {"id": uid}, one=True)
    return render_template("driver_dashboard.html", user=user, document=doc, document_status=doc["status"])


@app.route("/driver/location", methods=["POST"])
@login_required("Driver")
def driver_location():
    data = request.get_json(silent=True) or request.form
    try:
        lat = float(data.get("lat", data.get("latitude")))
        lng = float(data.get("lng", data.get("longitude")))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid coordinates"), 400
    db("UPDATE users SET latitude=:lat,longitude=:lng,online_status='Online' WHERE id=:id",
       {"lat": lat, "lng": lng, "id": session["user_id"]}, commit=True)
    return jsonify(ok=True)


@app.route("/fetch-requests")
@login_required("Driver")
def fetch_requests():
    rows = db("""SELECT r.*,u.full_name,u.phone,u.photo
                  FROM requests r JOIN users u ON r.client_id=u.id
                  WHERE r.status='Pending' AND r.driver_id IS NULL
                  ORDER BY r.id DESC""")
    if not rows:
        return "<p>No pending ride requests.</p>"
    html = []
    for r in rows:
        html.append(f"""<div class='request-box'>
          <div class='request-item'><strong>{r['full_name']}</strong><span>{r['phone']}</span></div>
          <div class='request-item'><span>Pickup</span><strong>{r['pickup']}</strong></div>
          <div class='request-item'><span>Destination</span><strong>{r['destination']}</strong></div>
          <div class='request-item'><span>{r['ride_type']}</span><span>{r['distance_km']} KM · {r['price']} FRW</span></div>
          <form method='POST' action='{url_for('driver_dashboard')}'><input type='hidden' name='request_id' value='{r['id']}'><button class='accept-btn' name='accept_request'>Accept Ride</button><button class='reject-btn' name='reject_request'>Reject</button></form>
        </div>""")
    return "".join(html)


# ---------------- DRIVER DOCUMENTS ----------------

@app.route("/driver/documents", methods=["GET", "POST"])
@login_required("Driver")
def document_verification():
    uid = session["user_id"]
    existing = db("SELECT * FROM driver_documents WHERE user_id=:id", {"id": uid}, one=True)
    message = ""
    status = existing["status"] if existing else tmsg("not_submitted")

    if request.method == "POST":
        plate = request.form.get("plate_number", "").strip()
        fields = {
            "id_front": ("national_id_front", "front"),
            "id_back": ("national_id_back", "back"),
            "permit_license": ("permit_license", "permit"),
            "plate_photo": ("plate_photo", "plate"),
        }
        values = {}
        for form_name, (column, prefix) in fields.items():
            f = request.files.get(form_name)
            if f and f.filename:
                values[column] = save_upload(f, prefix)
            elif existing:
                values[column] = existing.get(column) or ""
            else:
                values[column] = ""

        if existing:
            db("""UPDATE driver_documents SET national_id_front=:front,national_id_back=:back,
                   permit_license=:permit,plate_photo=:plate_photo,plate_number=:plate,status='Pending'
                   WHERE user_id=:id""",
               {"front": values["national_id_front"], "back": values["national_id_back"],
                "permit": values["permit_license"], "plate_photo": values["plate_photo"],
                "plate": plate, "id": uid}, commit=True)
        else:
            db("""INSERT INTO driver_documents(user_id,national_id_front,national_id_back,permit_license,plate_photo,plate_number,status)
                   VALUES(:id,:front,:back,:permit,:plate_photo,:plate,'Pending')""",
               {"id": uid, "front": values["national_id_front"], "back": values["national_id_back"],
                "permit": values["permit_license"], "plate_photo": values["plate_photo"], "plate": plate}, commit=True)
        return redirect(url_for("approve"))

    return render_template("document_verification.html", status=status, document=existing, message=message)


@app.route("/approve")
@login_required("Driver")
def approve():
    doc = db("SELECT status FROM driver_documents WHERE user_id=:id", {"id": session["user_id"]}, one=True)
    status = doc["status"] if doc else "Pending"
    if status == "Approved":
        return redirect(url_for("driver_dashboard"))
    message = "Documents submitted successfully. Please wait for administrator approval." if status == "Pending" else "Your documents were rejected. Please update and resubmit them."
    return render_template("verify.html", message=message)


@app.route("/uploads/<path:name>")
def uploads(name):
    return send_from_directory(UPLOAD_DIR, name)


# ---------------- PASSWORD RESET ----------------

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = db("SELECT id FROM users WHERE email=:email", {"email": email}, one=True)
        if user:
            token = secrets.token_urlsafe(48)
            expires = datetime.utcnow() + timedelta(hours=1)
            db("DELETE FROM password_resets WHERE user_id=:id", {"id": user["id"]}, commit=True)
            db("INSERT INTO password_resets(user_id,token,expires_at) VALUES(:id,:token,:expires)",
               {"id": user["id"], "token": token, "expires": expires}, commit=True)
            return redirect(url_for("reset_password", token=token))
        message = "If that email exists, you can continue with the reset flow."
    return render_template("forgot_password.html", message=message)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.values.get("token", "")
    record = db("SELECT user_id FROM password_resets WHERE token=:token AND expires_at>:now",
                {"token": token, "now": datetime.utcnow()}, one=True)
    if not record:
        return render_template("reset.html", valid=False, token=token, message="Invalid or expired reset link.")
    message = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if password != confirm:
            return render_template("reset.html", valid=True, token=token, message="Passwords do not match.")
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db("UPDATE users SET password=:password WHERE id=:id", {"password": hashed, "id": record["user_id"]}, commit=True)
        db("DELETE FROM password_resets WHERE token=:token", {"token": token}, commit=True)
        return render_template("verify.html", message="Password updated successfully. You can now log in.")
    return render_template("reset.html", valid=True, token=token, message=message)


# ---------------- ADMIN ----------------

@app.route("/admin", methods=["GET", "POST"])
def admin():
    # The original PHP project did not have a separate admin-login page. Keep a simple
    # configurable gate for the Python version; set ADMIN_EMAIL/ADMIN_PASSWORD in .env.
    if not session.get("is_admin"):
        if request.method == "POST":
            admin_email = os.getenv("ADMIN_EMAIL", "").strip()
            admin_password = os.getenv("ADMIN_PASSWORD", "")
            if not admin_email or not admin_password:
                return render_template(
                    "admin_login.html",
                    message="Admin login is not configured. Set ADMIN_EMAIL and ADMIN_PASSWORD in your .env file, then restart the Flask app.",
                )

            submitted_email = request.form.get("email", "").strip()
            submitted_password = request.form.get("password", "")
            if (hmac.compare_digest(submitted_email.casefold(), admin_email.casefold())
                    and hmac.compare_digest(submitted_password, admin_password)):
                session["is_admin"] = True
                return redirect(url_for("admin"))
            return render_template("admin_login.html", message="Invalid administrator credentials.")
        return render_template("admin_login.html", message="")

    message = ""
    if request.args.get("approve"):
        db("UPDATE driver_documents SET status='Approved' WHERE id=:id", {"id": request.args["approve"]}, commit=True)
        message = "Driver approved successfully."
    elif request.args.get("reject"):
        db("UPDATE driver_documents SET status='Rejected' WHERE id=:id", {"id": request.args["reject"]}, commit=True)
        message = "Driver rejected successfully."
    elif request.args.get("approve_mechanic"):
        db("UPDATE mechanic_profiles SET status='Approved' WHERE id=:id", {"id": request.args["approve_mechanic"]}, commit=True)
        message = tmsg("gfix_admin_approved_message")
    elif request.args.get("reject_mechanic"):
        db("UPDATE mechanic_profiles SET status='Rejected' WHERE id=:id", {"id": request.args["reject_mechanic"]}, commit=True)
        message = tmsg("gfix_admin_rejected_message")
    elif request.args.get("delete"):
        db("DELETE FROM users WHERE id=:id", {"id": request.args["delete"]}, commit=True)
        message = "User deleted successfully."

    users = db("SELECT * FROM users ORDER BY id DESC")
    docs = db("""SELECT d.*,u.full_name,u.email
                 FROM driver_documents d JOIN users u ON u.id=d.user_id
                 ORDER BY d.id DESC""")
    mechanics = db("SELECT m.*,u.full_name,u.email,u.phone FROM mechanic_profiles m JOIN users u ON u.id=m.user_id ORDER BY m.id DESC")
    repair_count = db("SELECT COUNT(*) AS total FROM repair_requests", one=True)["total"]
    stats = {
        "users": len(users),
        "drivers": sum(1 for u in users if u["account_type"] == "Driver"),
        "clients": sum(1 for u in users if u["account_type"] == "Client"),
        "pending": sum(1 for d in docs if d["status"] == "Pending"),
        "mechanics": len(mechanics),
        "pending_mechanics": sum(1 for m in mechanics if m["status"] == "Pending"),
        "repair_requests": repair_count,
    }
    return render_template("admin.html", users=users, docs=docs, mechanics=mechanics, stats=stats, message=message)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))

@app.route("/db-test")
def db_test():
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            value = result.scalar()

        if value == 1:
            return "✅ DATABASE CONNECTED SUCCESSFULLY! Using DATABASE_URL."

        return "❌ Database responded, but test failed.", 500

    except Exception as e:
        return f"❌ DATABASE CONNECTION FAILED: {str(e)}", 500

@app.route("/db-tables")
def db_tables():
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SHOW TABLES"))
            tables = [row[0] for row in result]

        if tables:
            return f"✅ DATABASE TABLES EXIST: {', '.join(tables)}"
        else:
            return "❌ No tables found in the database.", 404

    except Exception as e:
        return f"❌ DATABASE TABLE CHECK FAILED: {str(e)}", 500
if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
    )
