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

app = Flask(__name__, template_folder='.')
app.secret_key = "SUPER_SECRET_SAAS_KEY_2026"  # مفتاح تشفير الجلسات

# ================= DATABASE UTILITIES =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    # جدول المستخدمين
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        avatar TEXT,
        plan TEXT DEFAULT 'BASIC',
        is_admin INTEGER DEFAULT 0,
        api_key TEXT,
        api_requests_count INTEGER DEFAULT 0,
        max_api_limits INTEGER DEFAULT 50
    )
    """)
    
    # جدول المهام المطور (يدعم المهام الفرعية، الحالات، والأولويات)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task TEXT,
        status TEXT DEFAULT 'pending', -- 'pending' أو 'completed'
        priority TEXT DEFAULT 'medium', -- 'high', 'medium', 'low'
        parent_id INTEGER DEFAULT NULL, -- يُشير إلى المعرّف الرئيسي إذا كانت مهمة فرعية
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE CASCADE
    )
    """)
    
    # جدول سجلات الأنشطة
    conn.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # ترقية الجداول القديمة إن وجدت دون فقدان البيانات
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN status TEXT DEFAULT 'pending'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'medium'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN api_requests_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN max_api_limits INTEGER DEFAULT 50")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'BASIC'")
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
        if "username" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return redirect("/login")
        conn = db()
        user = conn.execute("SELECT is_admin FROM users WHERE id=?", (session.get("user_id"),)).fetchone()
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
            return jsonify({"error": "Missing API Key", "message": "برجاء إرسال X-API-KEY"}), 401

        conn = db()
        user = conn.execute("SELECT * FROM users WHERE api_key=?", (api_key,)).fetchone()

        if not user:
            conn.close()
            return jsonify({"error": "Invalid API Key"}), 403

        if user["api_requests_count"] >= user["max_api_limits"]:
            conn.close()
            return jsonify({"error": "API Limit Exceeded"}), 429

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
        </div>
        """
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls() 
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return False

def get_live_weather():
    default_weather = {"temp": "28.0", "wind": "10.9", "hint": "☀️ طقس اليوم ممتاز للإنتاجية!", "success": True}
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=36.91&longitude=3.91&current_weather=true"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            temp = data["current_weather"]["temperature"]
            wind = data["current_weather"]["windspeed"]
            return {"temp": temp, "wind": wind, "hint": "☀️ طقس اليوم ممتاز للإنتاجية والعمل الفعلي!", "success": True}
    except Exception as e:
        print(f"[WEATHER ERROR] {e}")
    return default_weather

# ================= ROUTES =================
@app.route("/")
def index():
    if "username" in session:
        return redirect("/dashboard")
    return redirect("/login")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'username' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        raw_password = request.form.get('password', '')
        
        if not username or not raw_password:
            return render_template('register.html', error="الرجاء ملء جميع الحقول!")
            
        password = hash_pw(raw_password)
        filename = "default.png"
        generated_key = secrets.token_hex(20)
        
        conn = db()
        try:
            conn.execute("""
                INSERT INTO users(username, password, avatar, plan, is_admin, api_key, api_requests_count, max_api_limits)
                VALUES (?, ?, ?, 'BASIC', 0, ?, 0, 50)
            """, (username, password, filename, generated_key))
            conn.commit()
            
            user_row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            
            session['username'] = username
            if user_row:
                session['user_id'] = user_row['id']
                log_activity(user_row['id'], "register")
                
            conn.close()
            return redirect(url_for('dashboard'))
            
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('register.html', error="اسم المستخدم هذا مسجل بالفعل!")
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        raw_password = request.form.get('password', '')
        
        if not username or not raw_password:
            return render_template('login.html', error="❌ برجاء إدخال اسم المستخدم وكلمة المرور")
            
        password = hash_pw(raw_password)
        
        conn = db()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
        conn.close()
        
        if user:
            session['username'] = username
            session['user_id'] = user['id']
            log_activity(user['id'], "login")
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="❌ خطأ في الاسم أو كلمة المرور")
            
    return render_template('login.html')

@app.route("/logout")
def logout():
    if "user_id" in session:
        log_activity(session["user_id"], "logout")
    session.clear()
    return redirect("/login")

@app.route('/dashboard', methods=["GET", "POST"])
def dashboard():
    if 'username' not in session or 'user_id' not in session:
        session.clear()
        return redirect(url_for('login'))
        
    username = session['username']
    user_id = session['user_id']
    
    conn = db()
    user_row = conn.execute('SELECT plan, api_key FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if not user_row:
        conn.close()
        session.clear()
        return redirect(url_for('login'))
        
    plan = user_row['plan']
    api_key = user_row['api_key']
    
    if plan == 'pro' and not api_key:
        api_key = secrets.token_hex(20)
        conn.execute("UPDATE users SET api_key = ? WHERE id = ?", (api_key, user_id))
        conn.commit()

    # جلب المهام الرئيسية فقط (التي ليس لها parent_id)
    main_tasks = conn.execute("""
        SELECT * FROM tasks 
        WHERE user_id=? AND parent_id IS NULL 
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    
    tasks = []
    for m_task in main_tasks:
        # جلب المهام الفرعية الخاصة بكل مهمة رئيسية لربطها بالواجهة المتقدمة لاحقاً
        sub_tasks_rows = conn.execute("SELECT * FROM tasks WHERE parent_id=?", (m_task["id"],)).fetchall()
        subs = []
        for s_row in sub_tasks_rows:
            subs.append({
                "id": s_row["id"],
                "title": s_row["task"],
                "status": s_row["status"]
            })
            
        tasks.append({
            "id": m_task["id"],
            "user_id": m_task["user_id"],
            "title": m_task["task"],
            "status": m_task["status"],
            "priority": m_task["priority"],
            "sub_tasks": subs
        })
    
    weather = get_live_weather()
    conn.close()
    
    return render_template('dashboard.html', 
                           username=username, 
                           plan=plan, 
                           tasks=tasks, 
                           weather=weather, 
                           user={"username": username, "plan": plan, "api_key": api_key})

@app.route('/add', methods=["POST"])
def add_task():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    task_text = request.form.get("title", "").strip()
    user_id = session['user_id']
    
    if task_text:
        conn = db()
        conn.execute("INSERT INTO tasks (user_id, task, status) VALUES (?, ?, 'pending')", (user_id, task_text))
        conn.commit()
        conn.close()
        log_activity(user_id, "add_task")
        
    return redirect(url_for('dashboard'))

@app.route("/toggle/<int:id>")
@login_required
def toggle_task(id):
    """تغيير حالة المهمة بين مكتملة وقيد الانتظار"""
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (id, session["user_id"])).fetchone()
    if task:
        new_status = 'completed' if task['status'] == 'pending' else 'pending'
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (new_status, id))
        conn.commit()
        log_activity(session["user_id"], f"toggle_task_{id}_to_{new_status}")
    conn.close()
    return redirect("/dashboard")
    
@app.route("/delete/<int:id>")
@login_required
def delete(id):
    conn = db()
    # مسح المهمة وأي مهمة فرعية مرتبطة بها
    conn.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (id, session["user_id"]))
    conn.execute("DELETE FROM tasks WHERE parent_id=? AND user_id=?", (id, session["user_id"]))
    conn.commit()
    conn.close()
    log_activity(session["user_id"], "delete_task")
    return redirect("/dashboard")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    return render_template("profile.html", user=user)

@app.route("/upgrade")
@login_required
def upgrade_page():
    conn = db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    conn.close()
    user_data = {"username": user_row["username"], "plan": user_row["plan"]}
    return render_template("upgrade.html", user=user_data)

@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    conn = db()
    conn.execute("UPDATE users SET plan='pro', max_api_limits=5000 WHERE id=?", (session["user_id"],))
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO activity_logs (user_id, action, timestamp) VALUES (?, ?, ?)", 
                 (session["user_id"], "upgraded_to_pro", current_time))
    conn.commit()
    conn.close()
    return redirect("/analytics")

@app.route("/analytics")
@login_required
def analytics():
    conn = db()
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    user_data = {
        "username": user_row["username"],
        "plan": user_row["plan"],
        "api_requests": user_row["api_requests_count"]
    }
    logs = conn.execute("SELECT * FROM activity_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 15", (session["user_id"],)).fetchall()
    conn.close()
    return render_template("analytics.html", user=user_data, logs=logs)

@app.route("/ai/decompose/<int:task_id>")
@login_required
def ai_decompose(task_id):
    """تفكيك ذكي حقيقي يقوم بإدراج مهام فرعية (Sub-tasks) بقاعدة البيانات ومبرمج للاتصال بالذكاء الاصطناعي لاحقاً"""
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, session["user_id"])).fetchone()
    
    if not task:
        conn.close()
        return "المهمة غير موجودة", 404
    
    # محاكاة لخطوات التفكيك التي يولدها الذكاء الاصطناعي (يمكن ربطها بـ API حقيقي مثل OpenAI/Gemini هنا)
    ai_generated_steps = [
        f"المرحلة الأولى: التحضير وجمع متطلبات ({task['task']})",
        f"المرحلة الثانية: البدء بالتنفيذ الفعلي للخطوة الأساسية",
        f"المرحلة الثالثة: المراجعة النهائية والاختبار والإنهاء"
    ]
    
    # إدراج هذه الخطوات كمهام فرعية حقيقية بقاعدة البيانات مربوطة بمعرف المهمة الأب parent_id
    for step in ai_generated_steps:
        conn.execute("""
            INSERT INTO tasks (user_id, task, status, parent_id) 
            VALUES (?, ?, 'pending', ?)
        """, (session["user_id"], step, task_id))
        
    conn.commit()
    conn.close()
    
    flash(f"🪄 تم تفكيك المهمة بنجاح إلى {len(ai_generated_steps)} خطوات فرعية حقيقية!", "ai_hint")
    return redirect("/dashboard")

# ================= API ENDPOINTS FOR THIRD-PARTY APPLICATIONS =================
@app.route("/api/v1/tasks", methods=["GET"])
@api_key_required
def api_get_tasks():
    conn = db()
    user_tasks = conn.execute("SELECT id, task, status, priority FROM tasks WHERE user_id=? AND parent_id IS NULL", (request.api_user["id"],)).fetchall()
    conn.close()
    return jsonify([dict(t) for t in user_tasks]), 200

if __name__ == "__main__":
    app.run(debug=True)
