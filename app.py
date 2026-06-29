import requests
import os
import sqlite3
import hashlib
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, render_template, redirect, session, jsonify, flash, url_for
from functools import wraps
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
app.secret_key = "SUPER_SECRET_SAAS_KEY_2026"  # مفتاح تشفير الجلسات

# ================= DATABASE UTILITIES =================
# جلب مسار المجلد الحالي للمشروع ديناميكياً
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

def db():
    # استخدام المسار الديناميكي لضمان استقرار السيرفر السحابي
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    # إنشاء الجدول الأساسي للمستخدمين مع دعم حقول الـ SaaS الكاملة
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        avatar TEXT,
        plan TEXT DEFAULT 'free',
        is_admin INTEGER DEFAULT 0,
        api_key TEXT,
        api_requests_count INTEGER DEFAULT 0,
        max_api_limits INTEGER DEFAULT 50
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 🔄 تحديث تلقائي آمن لقواعد البيانات القديمة إذا كانت موجودة لمنع أي خطأ برميجي
    try:
        conn.execute("ALTER TABLE users ADD COLUMN api_requests_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN max_api_limits INTEGER DEFAULT 50")
    except sqlite3.OperationalError:
        pass
        
    conn.commit()
    conn.close()

with app.app_context():
    init_db()

# ================= HELPERS & DECORATORS =================
def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()

def log_activity(user_id, activity):
    print(f"[LOG] user={user_id} action={activity}")
    try:
        conn = db()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO activity_logs (user_id, action, timestamp) 
            VALUES (?, ?, ?)
        """, (user_id, activity, current_time))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LOG ERROR] {e}")

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        conn = db()
        user = conn.execute("SELECT is_admin FROM users WHERE id=?", (session["user_id"],)).fetchone()
        conn.close()
        if not user or user["is_admin"] != 1:
            return "⛔ Access Denied: Admin Only", 403
        return f(*args, **kwargs)
    return wrapper

def api_key_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get("X-API-KEY") or request.args.get("api_key")
        if not api_key:
            return jsonify({"error": "Missing API Key", "message": "برجاء إرسال X-API-KEY في الـ Header أو الرابط"}), 401

        conn = db()
        user = conn.execute("SELECT * FROM users WHERE api_key=?", (api_key,)).fetchone()

        if not user:
            conn.close()
            return jsonify({"error": "Invalid API Key", "message": "مفتاح الـ API المستخدم غير صالح"}), 403

        if user["api_requests_count"] >= user["max_api_limits"]:
            conn.close()
            return jsonify({
                "error": "API Limit Exceeded",
                "message": f"لقد استهلكت حد الباقة الحالي ({user['max_api_limits']} طلب). يرجى الترقية إلى خطة PRO للحصول على حدود غير محدودة!"
            }), 429

        conn.execute("UPDATE users SET api_requests_count = api_requests_count + 1 WHERE id=?", (user["id"],))
        conn.commit()
        conn.close()

        request.api_user = user
        return f(*args, **kwargs)
    return wrapper

def send_email_notification(to_email, subject, body_content):
    sender_email = "drhassimac@gmail.com"  
    sender_password = "prcv fdof jvsa tmtb"  
    
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = to_email
        msg['Subject'] = subject
        
        html_content = f"""
        <div style="direction: rtl; text-align: right; font-family: sans-serif; border: 1px solid #e2e8f0; padding: 20px; border-radius: 12px;">
            <h2 style="color: #4f46e5;">🔥 منصة SaaS ULTRA الذكية</h2>
            <hr style="border: 0; border-top: 1px solid #e2e8f0;">
            <p style="font-size: 14px; color: #334155;">{body_content}</p>
            <br>
            <p style="font-size: 12px; color: #94a3b8;">هذا البريد تم إرساله تلقائياً من سيرفر المنصة السحابية الخاصة بك.</p>
        </div>
        """
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls() 
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        print(f"[EMAIL SUCCESS] تم إرسال بريد التنبيه بنجاح إلى {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] فشل إرسال البريد: {e}")
        return False

# ================= AUTHENTICATION ROUTES =================
# 🎯 تم إضافة هذا المسار الجذري لمنع خطأ الـ 404 وتوجيه الزائر تلقائياً
@app.route("/")
def index():
    if "user_id" in session:
        return redirect("/dashboard")
    return redirect("/login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = request.form["username"].strip()
        p = hash_pw(request.form["password"])

        filename = "default.png"
        file = request.files.get("avatar")
        if file and file.filename:
            filename = secure_filename(file.filename)
            os.makedirs("static/avatars", exist_ok=True)
            file.save("static/avatars/" + filename)

        conn = db()
        try:
            generated_key = secrets.token_hex(20)
            conn.execute("""
                INSERT INTO users(username, password, avatar, plan, is_admin, api_key, api_requests_count, max_api_limits)
                VALUES (?, ?, ?, 'free', 0, ?, 0, 50)
            """, (u, p, filename, generated_key))
            conn.commit()
            conn.close()
            return redirect("/login")
        except sqlite3.IntegrityError:
            conn.close()
            return render_template("register.html", error="اسم المستخدم مسجل مسبقاً! اختر اسماً آخر.")
        except Exception as e:
            conn.close()
            return render_template("register.html", error=f"حدث خطأ غير متوقع: {e}")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form["username"]
        p = hash_pw(request.form["password"])

        conn = db()
        user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (u, p)).fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            log_activity(user["id"], "login")
            return redirect("/dashboard")

        return render_template("login.html", error="❌ خطأ في اسم المستخدم أو كلمة المرور")

    return render_template("login.html")

@app.route("/logout")
def logout():
    if "user_id" in session:
        log_activity(session["user_id"], "logout")
    session.clear()
    return redirect("/login")

# ================= CORE SaaS & DASHBOARD =================
def get_live_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=36.91&longitude=3.91&current_weather=true"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            temp = data["current_weather"]["temperature"]
            wind = data["current_weather"]["windspeed"]
            
            ai_hint = "☀️ طقس اليوم معتدل ومناسب جداً لإنجاز المهام الميدانية والمكتبية بإنتاجية عالية!"
            if temp >= 35:
                ai_hint = "⚠️ تحذير: الطقس حار جداً اليوم خارجياً! يُنصح بجدولة المهام الميدانية في المساء والتركيز على العمل المكتبي في المكيف."
            elif temp <= 10:
                ai_hint = "❄️ تنبيه: الأجواء باردة جداً اليوم. حافظ على دفئك وركز على المهام التي لا تتطلب التنقل كثيراً."
            
            if wind >= 25:
                ai_hint = "💨 عاصف: سرعة الرياح قوية جداً! تجنب السفر أو الأعمال الخارجية المؤثرة واهتم بالمهام الداخلية."

            return {"temp": temp, "wind": wind, "hint": ai_hint, "success": True}
    except Exception as e:
        print(f"[WEATHER API ERROR] {e}")
    return {"success": False}

@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    tasks = conn.execute("SELECT * FROM tasks WHERE user_id=?", (session["user_id"],)).fetchall()
    conn.close()
    
    weather = get_live_weather()
    return render_template("dashboard.html", user=user, tasks=tasks, weather=weather)

@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = db()
    conn.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (id, session["user_id"]))
    conn.commit()
    conn.close()

    log_activity(session["user_id"], "delete_task")
    return redirect("/dashboard")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

    if request.method == "POST":
        file = request.files.get("avatar")
        if file and file.filename:
            filename = secure_filename(file.filename)
            os.makedirs("static/avatars", exist_ok=True)
            file.save("static/avatars/" + filename)

            conn.execute("UPDATE users SET avatar=? WHERE id=?", (filename, session["user_id"]))
            conn.commit()
            log_activity(session["user_id"], "updated_avatar")
        conn.close()
        return redirect("/profile")

    conn.close()
    return render_template("profile.html", user=user)

@app.route("/upgrade")
@login_required
def upgrade():
    conn = db()
    conn.execute("UPDATE users SET plan='pro', max_api_limits=5000 WHERE id=?", (session["user_id"],))
    conn.commit()
    conn.close()
    log_activity(session["user_id"], "upgraded_to_pro")
    return redirect("/dashboard")

@app.route("/generate-api-key")
@login_required
def generate_api_key():
    new_key = secrets.token_hex(20)
    conn = db()
    conn.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, session["user_id"]))
    conn.commit()
    conn.close()
    log_activity(session["user_id"], "generated_new_api_key")
    return redirect("/dashboard")

@app.route("/analytics")
@login_required
def analytics():
    conn = db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    
    if not user_row:
        conn.close()
        return "المستخدم غير موجود", 404
        
    user_data = {
        "username": user_row["username"],
        "plan": user_row["plan"] if "plan" in user_row.keys() else "free",
        "api_requests": user_row["api_requests_count"] if "api_requests_count" in user_row.keys() else 0
    }
    
    logs = conn.execute("SELECT * FROM activity_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 15", (session["user_id"],)).fetchall()
    conn.close()
    
    return render_template("analytics.html", user=user_data, logs=logs)

# ================= PROFESSIONAL ADMIN PANEL ROUTERS =================
@app.route("/admin")
@admin_required
def admin_panel():
    conn = db()
    users = conn.execute("SELECT * FROM users").fetchall()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    pro_users = conn.execute("SELECT COUNT(*) FROM users WHERE plan='pro'").fetchone()[0]
    conn.close()
    return render_template("admin.html", users=users, total_users=total_users, pro_users=pro_users)

@app.route("/admin/toggle-tier/<int:user_id>", methods=["POST"])
@admin_required
def toggle_tier(user_id):
    conn = db()
    user = conn.execute("SELECT plan FROM users WHERE id=?", (user_id,)).fetchone()
    if user:
        if user["plan"] == "pro":
            conn.execute("UPDATE users SET plan='free', max_api_limits=50 WHERE id=?", (user_id,))
        else:
            conn.execute("UPDATE users SET plan='pro', max_api_limits=5000 WHERE id=?", (user_id,))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_panel"))

# ================= BUSINESS-READY API ENDPOINTS =================
@app.route("/api/v1/resource", methods=["GET"])
@api_key_required
def get_saas_resource():
    user_data = request.api_user
    return jsonify({
        "status": "success",
        "author": "SaaS Core Engine v1.0",
        "api_usage": {
            "current_plan": user_data["plan"],
            "requests_used_total": user_data["api_requests_count"] + 1,
            "requests_remaining": user_data["max_api_limits"] - (user_data["api_requests_count"] + 1)
        },
        "payload": {
            "message": "مرحباً بك في واجهة الـ API الآمنة والمدفوعة!",
            "secret_content": "هنا تظهر البيانات الحصرية الحقيقية للنظام التي يدفع العملاء من أجلها."
        }
    }), 200

@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    conn = db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    
    conn.execute("UPDATE users SET plan='pro', api_requests_count=0, max_api_limits=5000 WHERE id=?", (session["user_id"],))
    
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO activity_logs (user_id, action, timestamp) VALUES (?, ?, ?)", 
                 (session["user_id"], "upgraded_to_pro", current_time))
    conn.commit()
    conn.close()
    
    # 🎯 يمكنك استبدال هذا بالإيميل الحقيقي للمستخدم لاحقاً
    user_email = "drhassimac@gmail.com" 
    
    subject = "💎 تهانينا! تم تفعيل الباقة الاحترافية بنجاح في SaaS ULTRA"
    body = f"مرحباً بك يا {user_row['username']}، نؤكد لك نجاح عملية الدفع وتفعيل باقة PRO لحسابك وفتح ميزات المساعد الذكي والـ AI بالكامل!"
    
    print("[SYSTEM] جاري محاولة الاتصال بسيرفر Google SMTP وإرسال البريد...")
    send_email_notification(user_email, subject, body)
    
    return redirect("/analytics")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
