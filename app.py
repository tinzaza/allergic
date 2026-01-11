# app.py
import sqlite3, json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup

app = Flask(__name__)
app.secret_key = "very_secret_key_here"

DB_PATH = "database.db"

# ---------------- DB ---------------- #
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # USERS (เหมือนเดิม)
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        full_name TEXT
    )
    """)

    # 👉 NEW: PATIENT PROFILE TABLE
    c.execute("""
    CREATE TABLE IF NOT EXISTS patient_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        email TEXT,
        phone TEXT,
        address TEXT,
        dob TEXT,
        gender TEXT,
        emergency_contact TEXT,
        insurance_provider TEXT,
        hospital_number TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # SYMPTOMS (เหมือนเดิม)
    c.execute("""
    CREATE TABLE IF NOT EXISTS symptoms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        total_score INTEGER,
        avg_vas REAL,
        pattern TEXT,
        recommendation TEXT,
        follow_up INTEGER DEFAULT 0,
        created_at TEXT,
        raw_form TEXT
    )
    """)
        # 👉 NEW: PATIENT HISTORY / ENVIRONMENT (Signup step 2)
    c.execute("""
    CREATE TABLE IF NOT EXISTS patient_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,

        symptom_worse_morning INTEGER,
        symptom_worse_exercise INTEGER,
        symptom_worse_dust INTEGER,
        symptom_worse_other TEXT,

        season_summer INTEGER,
        season_rainy INTEGER,
        season_winter INTEGER,
        season_all_year INTEGER,
        season_change INTEGER,

        duration_per_year TEXT,
        weekly_frequency TEXT,
        time_of_day TEXT,

        living_area TEXT,
        near_road INTEGER,
        housing_type TEXT,
        air_conditioner INTEGER,
        pet TEXT,

        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    

    conn.commit()
    conn.close()
init_db()
# ---------------- Helpers ---------------- #
def classify_pattern(days_per_week: int) -> str:
    return "persistent" if days_per_week >= 4 else "intermittent"

def calculate_follow_up(prev_follow_up, avg_vas, pattern, used_steroid_before):
    # reset condition
    if avg_vas < 5 and pattern == "intermittent":
        return 0

    # first time worsening
    if avg_vas >= 5 and prev_follow_up == 0:
        return 1

    # follow_up = 1 logic
    if prev_follow_up == 1:
        if used_steroid_before == "yes":
            return 2
        return 1

    # follow_up = 2 stays 2
    if prev_follow_up >= 2:
        return 2

    return prev_follow_up

# ---------------- Medicine Algorithm ---------------- #
def generate_recommendation(pattern, avg_vas, follow_up, used_steroid_answer):
    saline = (
        "ล้างจมูกด้วยน้ำเกลือ (Normal saline irrigation)\n"
        "– วันละ 1–2 ครั้ง\n\n"
    )

    oral_ah = (
        "ยาต้านฮิสตามีนชนิดรับประทาน รุ่นที่ 2\n"
        "– วันละ 1 ครั้ง\n\n"
    )

    leuko = (
        "Leukotriene receptor antagonist (LTRA)\n"
        "– วันละ 1 ครั้ง\n\n"
    )

    incs_standard = (
        "ยาสเตียรอยด์พ่นจมูก\n"
        "– 2 sprays/nostril วันละครั้ง\n"
    )

    incs_high = (
        "ยาสเตียรอยด์พ่นจมูก (เพิ่มขนาดยา)\n"
        "– 2 sprays/nostril วันละ 2 ครั้ง\n"
    )

    # ================= STATE 0 =================
    if follow_up == 0:
        if pattern == "intermittent" and avg_vas < 5:
            return saline + "เลือกอย่างใดอย่างหนึ่ง\n\n" + oral_ah + "หรือ\n\n" + leuko

        if (pattern == "intermittent" and avg_vas >= 5) or \
           (pattern == "persistent" and avg_vas < 5):
            return saline + "เลือกอย่างใดอย่างหนึ่ง\n\n" + oral_ah + "หรือ\n\n" + incs_standard

        if pattern == "persistent" and avg_vas >= 5:
            return saline + incs_standard

    # ================= STATE 1 =================
    if follow_up == 1:
        if avg_vas < 5:
            return "อาการดีขึ้น → ลดระดับยา และใช้ยาต่ออีก 2 สัปดาห์"

        if used_steroid_answer == "no":
            return saline + incs_standard

        return (
            "ส่งพบแพทย์เฉพาะทาง\n"
            "ประเมินการวินิจฉัยและการใช้ยา\n\n"
            + incs_high
        )

    # ================= STATE 2 =================
    if follow_up == 2:
        if avg_vas < 5:
            return "อาการดีขึ้น → ลดระดับยา และใช้ยาต่ออีก 2 สัปดาห์"

        return (
            "ส่งพบแพทย์เฉพาะทาง\n"
            "ประเมินการวินิจฉัยและการใช้ยา\n\n"
            + incs_high
        )

    # ================= STATE 3 =================
    if follow_up == 3:
        if avg_vas < 5:
            return "อาการดีขึ้น → ลดระดับยา และใช้ยาต่ออีก 2 สัปดาห์"
        return (
        "ภูมิคุ้มกันบัมบัดด้วยสารก่อภูมิแพ้\n"
        "ควรได้รับการผ่าตัด"
        )

# ---------------- Routes ---------------- #

@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("login"))

# ---------- Login ---------- #
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (request.form["username"],)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], request.form["password"]):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            return redirect(url_for(
                "doctor_dashboard" if user["role"]=="doctor" else "patient_form"
            ))
        flash("Invalid login","danger")
    return render_template("login.html")

# ---------- Signup ---------- #
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        conn = get_db()
        try:
            role = request.form["role"]

            # 0️⃣ CHECK DOCTOR CODE FIRST (สำคัญมาก)
            if role == "doctor":
                if request.form.get("doctor_code") != "SECRET123":
                    flash("Invalid doctor signup code", "danger")
                    return redirect(url_for("signup"))

            # 1️⃣ CREATE USER
            cur = conn.execute(
                "INSERT INTO users (username, password, role, full_name) VALUES (?,?,?,?)",
                (
                    request.form["username"],
                    generate_password_hash(request.form["password"]),
                    role,
                    request.form["full_name"]
                )
            )
            user_id = cur.lastrowid  # 🔑

            # 2️⃣ IF PATIENT → CREATE PROFILE
            if role == "patient":
                conn.execute("""
                    INSERT INTO patient_profiles
                    (user_id, email, phone, address, dob, gender,
                     emergency_contact, insurance_provider, hospital_number)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    user_id,
                    request.form.get("email"),
                    request.form.get("phone"),
                    request.form.get("address"),
                    request.form.get("dob"),
                    request.form.get("gender"),
                    request.form.get("emergency_contact"),
                    request.form.get("insurance_provider"),
                    request.form.get("hospital_number")
                ))

                # 3️⃣ PATIENT HISTORY (Signup Step 2)
                conn.execute("""
                    INSERT INTO patient_history (
                        user_id,

                        symptom_worse_morning,
                        symptom_worse_exercise,
                        symptom_worse_dust,
                        symptom_worse_other,

                        season_summer,
                        season_rainy,
                        season_winter,
                        season_all_year,
                        season_change,

                        duration_per_year,
                        weekly_frequency,
                        time_of_day,

                        living_area,
                        near_road,
                        housing_type,
                        air_conditioner,
                        pet
                    )
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    user_id,

                    int(bool(request.form.get("symptom_worse_morning"))),
                    int(bool(request.form.get("symptom_worse_exercise"))),
                    int(bool(request.form.get("symptom_worse_dust"))),
                    request.form.get("symptom_worse_other"),

                    int(bool(request.form.get("season_summer"))),
                    int(bool(request.form.get("season_rainy"))),
                    int(bool(request.form.get("season_winter"))),
                    int(bool(request.form.get("season_all_year"))),
                    int(bool(request.form.get("season_change"))),

                    request.form.get("duration_per_year"),
                    request.form.get("weekly_frequency"),
                    request.form.get("time_of_day"),

                    request.form.get("living_area"),
                    int(bool(request.form.get("near_road"))),
                    request.form.get("housing_type"),
                    int(bool(request.form.get("air_conditioner"))),
                    request.form.get("pet")
                ))

            conn.commit()

            flash(
                "สมัครสมาชิกสำเร็จ กรุณาเข้าสู่ระบบและกรอกแบบประเมินอาการ\n\n"
                "\n\nSignup successful. Please log in and complete the assessment form.",
                "success"
            )
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Username already exists", "danger")

        except Exception as e:
            conn.rollback()
            flash(f"Signup error: {e}", "danger")

        finally:
            conn.close()
        
    return render_template("signup.html")


# ---------- Doctor Dashboard ---------- #
@app.route("/doctor_dashboard")
def doctor_dashboard():
    if session.get("role")!="doctor":
        return redirect(url_for("login"))
    conn=get_db()
    patients = conn.execute("""
        SELECT 
            u.id,
            u.full_name,
            p.phone,
            p.email,
            COUNT(s.id) AS record_count
        FROM users u
        LEFT JOIN patient_profiles p ON u.id = p.user_id
        LEFT JOIN symptoms s ON u.id = s.user_id
        WHERE u.role = 'patient'
        GROUP BY u.id
        ORDER BY u.full_name
    """).fetchall()

    conn.close()
    return render_template("doctor_dashboard.html",patients=patients)

# ---------- Patient Detail ---------- #
@app.route("/patient/<int:patient_id>")
def patient_detail(patient_id):
    if session.get("role") != "doctor":
        return redirect(url_for("login"))

    conn = get_db()

    patient = conn.execute("""
        SELECT 
            u.id,
            u.full_name,

            -- profile
            p.email,
            p.phone,
            p.address,
            p.dob,
            p.gender,
            p.emergency_contact,
            p.insurance_provider,
            p.hospital_number,

            -- history
            h.symptom_worse_morning,
            h.symptom_worse_exercise,
            h.symptom_worse_dust,
            h.symptom_worse_other,

            h.season_summer,
            h.season_rainy,
            h.season_winter,
            h.season_all_year,
            h.season_change,

            h.duration_per_year,
            h.weekly_frequency,
            h.time_of_day,

            h.living_area,
            h.near_road,
            h.housing_type,
            h.air_conditioner,
            h.pet

        FROM users u
        LEFT JOIN patient_profiles p ON u.id = p.user_id
        LEFT JOIN patient_history h ON u.id = h.user_id
        WHERE u.id = ?
    """, (patient_id,)).fetchone()

    rows = conn.execute("""
        SELECT * FROM symptoms
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (patient_id,)).fetchall()

    vas_rows = conn.execute("""
        SELECT DATE(created_at) AS date, avg_vas, recommendation
        FROM symptoms
        WHERE user_id = ?
        ORDER BY DATE(created_at)
    """, (patient_id,)).fetchall()

    conn.close()

    reports = [{
        "created_at": r["created_at"],
        "pattern": r["pattern"],
        "avg_vas": r["avg_vas"],
        "follow_up": r["follow_up"],
        "recommendation": r["recommendation"],
        "data": json.loads(r["raw_form"]) if r["raw_form"] else {}
    } for r in rows]

    return render_template(
        "patient_detail.html",
        patient=patient,
        reports=reports,
        vas_rows=vas_rows
    )

# ---------- Patient Form ---------- #
@app.route("/patient_form", methods=["GET","POST"])
def patient_form():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    last = conn.execute("""
        SELECT * FROM symptoms
        WHERE user_id=?
        ORDER BY created_at DESC
        LIMIT 1
    """,(session["user_id"],)).fetchone()

    follow_up = last["follow_up"] if last else 0
    need_followup = follow_up in (1,2)
    next_allowed = None

    if last:
        last_date = datetime.fromisoformat(last["created_at"])
        next_allowed = last_date + timedelta(days=14)

    # ---------- POST ----------
    if request.method == "POST":
        report_date = datetime.fromisoformat(request.form["report_date"])

        if last and report_date < next_allowed:
            flash(f"กรอกได้อีกครั้งวันที่ {next_allowed:%Y-%m-%d}", "warning")
            return redirect(url_for("patient_form"))

        freq = int(request.form["symptom_frequency"])
        top4 = [int(request.form[k]) for k in
            ["sneeze_often","itchy_nose","runny_nose","stuffy_nose"]]

        avg_vas = round(sum(top4)/4, 1)
        pattern = classify_pattern(freq)
        used_steroid = request.form.get("used_steroid_before", "no")
        prev_follow_up = follow_up   # 👈 สำคัญมาก

        # 1️⃣ ให้ยา "ก่อน"
        recommendation = generate_recommendation(
            pattern,
            avg_vas,
            prev_follow_up,
            used_steroid
        )

        # 2️⃣ ค่อยคำนวณ follow_up ใหม่
        next_follow_up = prev_follow_up

        # reset
        if avg_vas < 5 and pattern == "intermittent":
            next_follow_up = 0

        # first worsening
        elif prev_follow_up == 0 and avg_vas >= 5:
            next_follow_up = 1

        # follow_up = 1
        elif prev_follow_up == 1 and avg_vas >= 5:
            if used_steroid == "yes":
                next_follow_up = 2   # เคยใช้ steroid → ไปขั้นสุดท้าย
            else:
                next_follow_up = 1   # ยังไม่เคยใช้ → อยู่ขั้นเดิม

        # follow_up = 2 (stay here; referral already applies)
        elif prev_follow_up == 2 and avg_vas >= 5:
            next_follow_up = 3

        raw_form = {k: request.form.get(k) for k in request.form}

        conn.execute("""
            INSERT INTO symptoms
            (user_id, avg_vas, pattern, recommendation, follow_up, created_at, raw_form)
            VALUES (?,?,?,?,?,?,?)
        """, (
            session["user_id"],
            avg_vas,
            pattern,
            recommendation,
            next_follow_up,
            report_date.isoformat(),
            json.dumps(raw_form)
        ))
        conn.commit()
        conn.close()
        flash("บันทึกข้อมูลเรียบร้อย ดูผลการประเมินที่หน้า Result", "success")
        return redirect(url_for("patient_form", show_result="1"))

    # ================= GET =================
    reports = conn.execute(
        "SELECT * FROM symptoms WHERE user_id=? ORDER BY created_at DESC",
        (session["user_id"],)
    ).fetchall()
    conn.close()

    latest_html = ""
    show_result = request.args.get("show_result")

    if reports:
        r = reports[0]
        latest_html = Markup(
            f"<b>Date:</b> {r['created_at'][:10]}<br>"
            f"<b>Pattern:</b> {r['pattern']}<br>"
            f"<b>VAS:</b> {r['avg_vas']}<br>"
            f"<b>Follow-up:</b> {r['follow_up']}<br>"
            f"<pre>{r['recommendation']}</pre>"
        )

    return render_template(
        "patient_form.html",
        reports=reports,
        latest_html=latest_html,
        today=datetime.utcnow().strftime("%Y-%m-%d"),
        need_followup=need_followup
    )

# ---------- Logout ---------- #
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

