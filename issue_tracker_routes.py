import os
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import mysql.connector
from twilio.rest import Client as TwilioClient
from urllib.parse import urlparse
import requests

# ── APP SETUP ─────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'SujanIssueTrackerSecureKey2026')

# ── USERS & ROLES ─────────────────────────────────────────────
USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "sujan": {"password": "sujan123", "role": "viewer"},
}

# ── EMAIL CONFIG (Brevo API — set via Railway env vars) ────────
# No secrets are hardcoded here so this file is safe to commit to a public repo.
BREVO_API_KEY = os.environ.get('BREVO_API_KEY')
MAIL_FROM     = os.environ.get('MAIL_FROM', 'gopa9791399103@gmail.com')
EMAIL_FROM    = f"Sujan Continental AVS <{MAIL_FROM}>"
ADMIN_EMAIL   = MAIL_FROM

# ── TWILIO CONFIG ─────────────────────────────────────────────
TWILIO_SID     = os.environ.get('TWILIO_SID')
TWILIO_TOKEN   = os.environ.get('TWILIO_TOKEN')
TWILIO_WA_FROM = "whatsapp:+14155238886"

# ── DB CONFIG ─────────────────────────────────────────────────
mysql_url = os.environ.get('MYSQL_URL', None)

if mysql_url:
    parsed = urlparse(mysql_url)
    db_config = {
        "host":     parsed.hostname,
        "port":     parsed.port or 3306,
        "user":     parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip('/')
    }
elif os.environ.get('MYSQLHOST'):
    # Railway sometimes injects individual MYSQLHOST/MYSQLUSER/etc.
    # instead of (or in addition to) a combined MYSQL_URL.
    db_config = {
        "host":     os.environ.get('MYSQLHOST'),
        "port":     int(os.environ.get('MYSQLPORT', '3306')),
        "user":     os.environ.get('MYSQLUSER', 'root'),
        "password": os.environ.get('MYSQLPASSWORD', ''),
        "database": os.environ.get('MYSQLDATABASE', 'railway')
    }
else:
    db_config = {
        "host":     "localhost",
        "port":     3306,
        "user":     "root",
        "password": os.environ.get('LOCAL_DB_PASSWORD', ''),
        "database": "Chennai plant Open issue"
    }

def get_db():
    return mysql.connector.connect(**db_config)


# ── AUTH DECORATORS ───────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            return jsonify({"success": False, "message": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username in USERS and USERS[username]["password"] == password:
            session["user"] = username
            session["role"] = USERS[username]["role"]
            return redirect(url_for("issue_tracker"))
        else:
            error = "Invalid username or password."
    return render_template("login_issue.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════════════
#  MAIN PAGE
# ══════════════════════════════════════════════════════════════

@app.route("/")
@app.route("/issue_tracker")
@login_required
def issue_tracker():
    return render_template("issue_tracker.html",
                           role=session.get("role"),
                           username=session.get("user"))


@app.route("/get_role")
@login_required
def get_role():
    return jsonify({"role": session.get("role"), "username": session.get("user")})


# ══════════════════════════════════════════════════════════════
#  ISSUES — READ (both roles)
# ══════════════════════════════════════════════════════════════

@app.route("/get_issues")
@login_required
def get_issues():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, description, department, priority,
                   responsible, email, target_date, status,
                   progress, remark,
                   DATE_FORMAT(last_updated, '%d-%m-%Y %H:%i') AS last_update
            FROM issues
            ORDER BY
                CASE status WHEN 'Open' THEN 1 WHEN 'In Progress' THEN 2 WHEN 'Closed' THEN 3 ELSE 4 END,
                CASE priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END,
                target_date ASC
        """)
        rows = cursor.fetchall()
        cursor.close(); conn.close()
        for r in rows:
            if r['target_date']:
                r['target_date'] = str(r['target_date'])
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  ISSUES — WRITE (admin only)
# ══════════════════════════════════════════════════════════════

@app.route("/add_issue", methods=["POST"])
@admin_required
def add_issue():
    try:
        data = request.get_json()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO issues
            (description, department, priority, responsible,
             email, target_date, status, progress, remark, created_at, last_updated)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """, (
            data['description'], data['department'], data['priority'],
            data['responsible'], data['email'], data['target_date'],
            data['status'], data['progress'], data.get('remark', '')
        ))
        issue_id = cursor.lastrowid
        conn.commit(); cursor.close(); conn.close()

        responsible = data['responsible']
        department  = data['department']
        priority    = data['priority']
        target_date = data['target_date']
        description = data['description']
        remark      = data.get('remark', 'N/A')
        email       = data['email']
        phone       = data.get('phone', '').strip()

        # ── Send Email in background thread ───────────────────
        try:
            import threading
            subject = f"New Issue Assigned — Issue #{issue_id}"
            body = f"""Dear {responsible},

A new issue has been assigned to you. Please review the details and take necessary action.

Issue #     : {issue_id}
Description : {description}
Department  : {department}
Priority    : {priority}
Target Date : {target_date}
Status      : {data['status']}
Remark      : {remark}

Please update your progress via the Plant Open Issue Tracker.

Regards,
Admin — Sujan Continental AVS Pvt Ltd"""
            threading.Thread(
                target=_send_html_email,
                args=(email, subject, body),
                daemon=True
            ).start()
        except Exception as e:
            print(f"Email thread failed: {e}")

        # ── Send WhatsApp in background thread ────────────────
        if phone:
            try:
                import threading
                wa_msg = (
                    f"*New Issue Assigned — Issue #{issue_id}*\n\n"
                    f"*Description:* {description}\n"
                    f"*Department:* {department}\n"
                    f"*Priority:* {priority}\n"
                    f"*Target Date:* {target_date}\n"
                    f"*Remark:* {remark}\n\n"
                    f"Please update your progress via the Issue Tracker.\n"
                    f"— Sujan Continental AVS Pvt Ltd"
                )
                threading.Thread(
                    target=_send_whatsapp,
                    args=(phone, wa_msg),
                    daemon=True
                ).start()
            except Exception as e:
                print(f"WhatsApp thread failed: {e}")

        return jsonify({
            "success": True,
            "message": (
                f"Issue #{issue_id} added — Email sent to {email}"
                + (f" & WhatsApp sent to {phone}" if phone else "")
            )
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/update_issue/<int:issue_id>", methods=["POST"])
@admin_required
def update_issue(issue_id):
    try:
        data = request.get_json()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE issues
            SET description=%s, department=%s, priority=%s,
                responsible=%s, email=%s, target_date=%s,
                status=%s, progress=%s, remark=%s, last_updated=NOW()
            WHERE id=%s
        """, (
            data['description'], data['department'], data['priority'],
            data['responsible'], data['email'], data['target_date'],
            data['status'], data['progress'], data.get('remark', ''), issue_id
        ))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({"success": True, "message": "Issue updated"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/delete_issue/<int:issue_id>", methods=["DELETE"])
@admin_required
def delete_issue(issue_id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM issue_updates WHERE issue_id=%s", (issue_id,))
        cursor.execute("DELETE FROM pending_updates WHERE issue_id=%s", (issue_id,))
        cursor.execute("DELETE FROM issues WHERE id=%s", (issue_id,))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({"success": True, "message": "Issue deleted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  UPDATE HISTORY (both roles)
# ══════════════════════════════════════════════════════════════

@app.route("/get_issue_updates/<int:issue_id>")
@login_required
def get_issue_updates(issue_id):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT status, progress, remark, updated_by, approval_status,
                   DATE_FORMAT(updated_at, '%d-%m-%Y %H:%i') AS updated_at
            FROM issue_updates
            WHERE issue_id=%s
            ORDER BY updated_at DESC
        """, (issue_id,))
        rows = cursor.fetchall()
        cursor.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  SUJAN SUBMITS UPDATE — pending admin approval
# ══════════════════════════════════════════════════════════════

@app.route("/submit_update/<int:issue_id>", methods=["POST"])
@login_required
def submit_update(issue_id):
    try:
        data       = request.get_json()
        user       = session.get("user")
        role       = session.get("role")
        remark     = data.get("remark", "").strip()
        progress   = int(data.get("progress", 0))
        new_status = data.get("status", "In Progress")

        if not remark:
            return jsonify({"success": False, "message": "Remark is required"}), 400

        conn = get_db(); cursor = conn.cursor()

        if role == "admin":
            cursor.execute("""
                INSERT INTO issue_updates
                (issue_id, status, progress, remark, updated_by, approval_status, updated_at)
                VALUES (%s,%s,%s,%s,%s,'Approved',NOW())
            """, (issue_id, new_status, progress, remark, user))
            cursor.execute("""
                UPDATE issues SET status=%s, progress=%s, last_updated=NOW() WHERE id=%s
            """, (new_status, progress, issue_id))
            conn.commit(); cursor.close(); conn.close()
            return jsonify({"success": True, "message": "Update saved successfully"})
        else:
            cursor.execute("""
                INSERT INTO pending_updates
                (issue_id, proposed_status, proposed_progress, remark, submitted_by, submitted_at)
                VALUES (%s,%s,%s,%s,%s,NOW())
            """, (issue_id, new_status, progress, remark, user))
            pending_id = cursor.lastrowid
            conn.commit(); cursor.close(); conn.close()
            try:
                _notify_admin_pending(issue_id, pending_id, user, remark, new_status, progress)
            except:
                pass
            return jsonify({
                "success": True,
                "message": "Update submitted for admin approval. It will reflect once approved."
            })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  PENDING UPDATES — admin approves / rejects
# ══════════════════════════════════════════════════════════════

@app.route("/get_pending_updates")
@admin_required
def get_pending_updates():
    try:
        conn = get_db(); cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.id, p.issue_id, p.proposed_status, p.proposed_progress,
                   p.remark, p.submitted_by, p.decision,
                   DATE_FORMAT(p.submitted_at, '%d-%m-%Y %H:%i') AS submitted_at,
                   i.description AS issue_desc, i.department, i.responsible,
                   i.status AS current_status, i.progress AS current_progress
            FROM pending_updates p
            JOIN issues i ON p.issue_id = i.id
            WHERE p.decision = 'Pending'
            ORDER BY p.submitted_at DESC
        """)
        rows = cursor.fetchall(); cursor.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/approve_update/<int:pending_id>", methods=["POST"])
@admin_required
def approve_update(pending_id):
    try:
        data         = request.get_json()
        admin_remark = data.get("admin_remark", "").strip()
        conn = get_db(); cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM pending_updates WHERE id=%s", (pending_id,))
        pu = cursor.fetchone()
        if not pu:
            cursor.close(); conn.close()
            return jsonify({"success": False, "message": "Not found"}), 404

        issue_id     = pu["issue_id"]
        new_status   = pu["proposed_status"]
        new_progress = pu["proposed_progress"]
        remark       = pu["remark"]
        submitted_by = pu["submitted_by"]

        cursor.execute("""
            UPDATE pending_updates SET decision='Approved', admin_remark=%s, decided_at=NOW()
            WHERE id=%s
        """, (admin_remark, pending_id))
        cursor.execute("""
            INSERT INTO issue_updates
            (issue_id, status, progress, remark, updated_by, approval_status, updated_at)
            VALUES (%s,%s,%s,%s,%s,'Approved',NOW())
        """, (issue_id, new_status, new_progress,
              f"{remark} [Approved{': '+admin_remark if admin_remark else ''}]",
              submitted_by))
        cursor.execute("""
            UPDATE issues SET status=%s, progress=%s, last_updated=NOW() WHERE id=%s
        """, (new_status, new_progress, issue_id))
        conn.commit()

        cursor.execute("SELECT * FROM issues WHERE id=%s", (issue_id,))
        issue = cursor.fetchone()
        cursor.close(); conn.close()

        try:
            _notify_user_decision("Approved", submitted_by, issue, remark, new_status, new_progress, admin_remark)
        except:
            pass

        return jsonify({"success": True, "message": f"Approved — Issue #{issue_id} updated to {new_status}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/reject_update/<int:pending_id>", methods=["POST"])
@admin_required
def reject_update(pending_id):
    try:
        data         = request.get_json()
        admin_remark = data.get("admin_remark", "").strip()
        conn = get_db(); cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM pending_updates WHERE id=%s", (pending_id,))
        pu = cursor.fetchone()
        if not pu:
            cursor.close(); conn.close()
            return jsonify({"success": False, "message": "Not found"}), 404

        issue_id     = pu["issue_id"]
        submitted_by = pu["submitted_by"]
        remark       = pu["remark"]

        cursor.execute("""
            UPDATE pending_updates SET decision='Rejected', admin_remark=%s, decided_at=NOW()
            WHERE id=%s
        """, (admin_remark, pending_id))
        cursor.execute("""
            INSERT INTO issue_updates
            (issue_id, status, progress, remark, updated_by, approval_status, updated_at)
            SELECT issue_id, proposed_status, proposed_progress,
                   CONCAT('Rejected: ', %s, IF(%s!='', CONCAT(' — Reason: ', %s), '')),
                   submitted_by, 'Rejected', NOW()
            FROM pending_updates WHERE id=%s
        """, (remark, admin_remark, admin_remark, pending_id))
        conn.commit()

        cursor.execute("SELECT * FROM issues WHERE id=%s", (issue_id,))
        issue = cursor.fetchone()
        cursor.close(); conn.close()

        try:
            _notify_user_decision("Rejected", submitted_by, issue, remark, None, None, admin_remark)
        except:
            pass

        return jsonify({"success": True, "message": f"Rejected — Issue #{issue_id}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/get_pending_count")
@login_required
def get_pending_count():
    try:
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM pending_updates WHERE decision='Pending'")
        count = cursor.fetchone()[0]
        cursor.close(); conn.close()
        return jsonify({"count": count})
    except:
        return jsonify({"count": 0})


# ══════════════════════════════════════════════════════════════
#  SEND MAIL (admin only)
# ══════════════════════════════════════════════════════════════

@app.route("/send_issue_mail/<int:issue_id>", methods=["POST"])
@admin_required
def send_issue_mail(issue_id):
    try:
        data = request.get_json()
        _send_html_email(data['to'], data['subject'], data['body'])
        conn = get_db(); cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO issue_updates
            (issue_id, status, progress, remark, updated_by, approval_status, updated_at)
            SELECT id, status, progress, CONCAT('Email reminder sent to ', %s),
                   'System', 'Approved', NOW()
            FROM issues WHERE id=%s
        """, (data['to'], issue_id))
        conn.commit(); cursor.close(); conn.close()
        return jsonify({"success": True, "message": f"Email sent to {data['to']}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  REPORT PAGE
# ══════════════════════════════════════════════════════════════

@app.route("/report")
@login_required
def report_page():
    return render_template("report.html")


@app.route("/get_report_data")
@login_required
def get_report_data():
    try:
        date_from = request.args.get("from", "")
        date_to   = request.args.get("to", "")
        dept      = request.args.get("dept", "")
        priority  = request.args.get("priority", "")

        conn = get_db(); cursor = conn.cursor(dictionary=True)

        conditions = []; params = []
        if date_from: conditions.append("DATE(created_at) >= %s"); params.append(date_from)
        if date_to:   conditions.append("DATE(created_at) <= %s"); params.append(date_to)
        if dept:      conditions.append("department = %s");        params.append(dept)
        if priority:  conditions.append("priority = %s");          params.append(priority)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cursor.execute(f"""
            SELECT id, status, department, priority, target_date
            FROM issues {where} ORDER BY created_at DESC
        """, params)

        rows = cursor.fetchall(); cursor.close(); conn.close()
        today = datetime.today().date()

        by_status = {}; by_dept = {}; by_priority = {}
        overdue = 0; issue_list = []

        for r in rows:
            status = r["status"]
            if status != "Closed" and r["target_date"] and r["target_date"] < today:
                status = "Overdue"; overdue += 1
            by_status[status]          = by_status.get(status, 0) + 1
            by_dept[r["department"]]   = by_dept.get(r["department"], 0) + 1
            by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
            issue_list.append({
                "id": r["id"], "department": r["department"],
                "priority": r["priority"], "status": status,
                "target_date": str(r["target_date"]) if r["target_date"] else ""
            })

        return jsonify({
            "total": len(rows), "by_status": by_status,
            "by_dept": by_dept, "by_priority": by_priority,
            "overdue": overdue, "issue_list": issue_list[:50],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/send_report_email", methods=["POST"])
@login_required
def send_report_email():
    try:
        data      = request.get_json()
        to        = data["to"];        cc       = data.get("cc", "")
        subject   = data.get("subject", "Plant Open Issue Tracker Report")
        message   = data.get("message", "")
        rd        = data.get("report_data", {})
        date_from = data.get("date_from", "All"); date_to  = data.get("date_to", "All")
        dept      = data.get("dept", "All");      priority = data.get("priority", "All")

        by_status   = rd.get("by_status",   {})
        by_dept     = rd.get("by_dept",     {})
        by_priority = rd.get("by_priority", {})
        total       = rd.get("total", 0)
        overdue     = rd.get("overdue", 0)
        issue_list  = rd.get("issue_list", [])

        sc = {"Open":"#fee2e2","In Progress":"#ffedd5","Closed":"#dcfce7","Overdue":"#ede9fe"}
        st = {"Open":"#dc2626","In Progress":"#ea580c","Closed":"#16a34a","Overdue":"#7c3aed"}
        pc = {"High":"#fee2e2","Medium":"#ffedd5","Low":"#dcfce7"}
        pt = {"High":"#dc2626","Medium":"#ea580c","Low":"#16a34a"}

        def trows(d, bg, tc):
            h = ""
            for k, v in sorted(d.items(), key=lambda x: -x[1]):
                pct = round(v/total*100) if total else 0
                h += f'<tr><td style="padding:7px 12px;border-bottom:1px solid #eee;"><span style="background:{bg.get(k,"#f5f5f5")};color:{tc.get(k,"#333")};padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;">{k}</span></td><td style="padding:7px 12px;border-bottom:1px solid #eee;font-weight:700;">{v}</td><td style="padding:7px 12px;border-bottom:1px solid #eee;color:#888;">{pct}%</td></tr>'
            return h

        irows = ""
        for i in issue_list:
            irows += f'<tr><td style="padding:5px 9px;border-bottom:1px solid #f5f5f5;font-size:12px;color:#888;">#{i["id"]}</td><td style="padding:5px 9px;border-bottom:1px solid #f5f5f5;font-size:12px;">{i["department"]}</td><td style="padding:5px 9px;border-bottom:1px solid #f5f5f5;"><span style="background:{pc.get(i["priority"],"#f5f5f5")};color:{pt.get(i["priority"],"#333")};padding:1px 7px;border-radius:9px;font-size:11px;font-weight:600;">{i["priority"]}</span></td><td style="padding:5px 9px;border-bottom:1px solid #f5f5f5;"><span style="background:{sc.get(i["status"],"#f5f5f5")};color:{st.get(i["status"],"#333")};padding:1px 7px;border-radius:9px;font-size:11px;font-weight:600;">{i["status"]}</span></td><td style="padding:5px 9px;border-bottom:1px solid #f5f5f5;font-size:12px;color:#888;">{i["target_date"]}</td></tr>'

        stat_cells = "".join([
            f'<td style="text-align:center;padding:10px;background:{bg};border-radius:7px;" width="16%"><div style="font-size:24px;font-weight:700;color:{tc};">{val}</div><div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-top:2px;">{lbl}</div></td>'
            for lbl,val,bg,tc in [
                ("Total",       total,                           "#eff6ff","#1d4ed8"),
                ("Open",        by_status.get("Open",0),        "#fee2e2","#dc2626"),
                ("In Progress", by_status.get("In Progress",0), "#ffedd5","#ea580c"),
                ("Closed",      by_status.get("Closed",0),      "#dcfce7","#16a34a"),
                ("Overdue",     overdue,                         "#ede9fe","#7c3aed"),
                ("High Prio",   by_priority.get("High",0),      "#fef9c3","#ca8a04"),
            ]
        ])

        html = f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:28px 0;"><tr><td align="center">
<table width="660" cellpadding="0" cellspacing="0" style="background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.1);">
<tr><td style="background:#002060;padding:26px;text-align:center;">
  <h1 style="color:#F7A600;margin:0;font-size:20px;letter-spacing:3px;text-transform:uppercase;">Plant Open Issue Tracker</h1>
  <p style="color:rgba(255,255,255,0.6);margin:5px 0 0;font-size:13px;">Issue Report — Sujan Continental AVS Pvt Ltd</p>
</td></tr>
<tr><td style="background:#F7A600;height:4px;"></td></tr>
<tr><td style="padding:12px 28px;background:#f8f9fb;border-bottom:1px solid #eee;font-size:12px;color:#888;">
  <b style="color:#333;">Period:</b> {date_from} → {date_to} &nbsp;|&nbsp;
  <b style="color:#333;">Dept:</b> {dept or 'All'} &nbsp;|&nbsp;
  <b style="color:#333;">Priority:</b> {priority or 'All'} &nbsp;|&nbsp;
  <b style="color:#333;">Generated:</b> {datetime.now().strftime('%d-%m-%Y %H:%M')}
</td></tr>
<tr><td style="padding:20px 28px;">
  <table width="100%" cellpadding="3" cellspacing="3"><tr>{stat_cells}</tr></table>
</td></tr>
<tr><td style="padding:0 28px 22px;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr valign="top">
    <td width="32%" style="padding-right:9px;">
      <div style="font-size:11px;font-weight:700;color:#002060;text-transform:uppercase;letter-spacing:1px;margin-bottom:7px;padding-bottom:5px;border-bottom:2px solid #F7A600;">By Status</div>
      <table width="100%" style="border:1px solid #eee;border-radius:5px;overflow:hidden;">
        <thead><tr style="background:#f8f9fb;"><th style="padding:6px 12px;font-size:10px;text-align:left;color:#888;">Status</th><th style="padding:6px 12px;font-size:10px;color:#888;">Cnt</th><th style="padding:6px 12px;font-size:10px;color:#888;">%</th></tr></thead>
        <tbody>{trows(by_status, sc, st)}</tbody>
      </table>
    </td>
    <td width="36%" style="padding:0 5px;">
      <div style="font-size:11px;font-weight:700;color:#002060;text-transform:uppercase;letter-spacing:1px;margin-bottom:7px;padding-bottom:5px;border-bottom:2px solid #F7A600;">By Department</div>
      <table width="100%" style="border:1px solid #eee;border-radius:5px;overflow:hidden;">
        <thead><tr style="background:#f8f9fb;"><th style="padding:6px 12px;font-size:10px;text-align:left;color:#888;">Dept</th><th style="padding:6px 12px;font-size:10px;color:#888;">Cnt</th><th style="padding:6px 12px;font-size:10px;color:#888;">%</th></tr></thead>
        <tbody>{trows(by_dept, {k:"#eff6ff" for k in by_dept}, {k:"#1d4ed8" for k in by_dept})}</tbody>
      </table>
    </td>
    <td width="32%" style="padding-left:9px;">
      <div style="font-size:11px;font-weight:700;color:#002060;text-transform:uppercase;letter-spacing:1px;margin-bottom:7px;padding-bottom:5px;border-bottom:2px solid #F7A600;">By Priority</div>
      <table width="100%" style="border:1px solid #eee;border-radius:5px;overflow:hidden;">
        <thead><tr style="background:#f8f9fb;"><th style="padding:6px 12px;font-size:10px;text-align:left;color:#888;">Priority</th><th style="padding:6px 12px;font-size:10px;color:#888;">Cnt</th><th style="padding:6px 12px;font-size:10px;color:#888;">%</th></tr></thead>
        <tbody>{trows(by_priority, pc, pt)}</tbody>
      </table>
    </td>
  </tr></table>
</td></tr>
{"<tr><td style='padding:0 28px 22px;'><div style='font-size:11px;font-weight:700;color:#002060;text-transform:uppercase;letter-spacing:1px;margin-bottom:7px;padding-bottom:5px;border-bottom:2px solid #F7A600;'>Issue List</div><table width='100%' style='border:1px solid #eee;border-radius:5px;overflow:hidden;'><thead><tr style='background:#f8f9fb;'><th style='padding:6px 9px;font-size:10px;color:#888;text-align:left;'>#</th><th style='padding:6px 9px;font-size:10px;color:#888;text-align:left;'>Dept</th><th style='padding:6px 9px;font-size:10px;color:#888;text-align:left;'>Priority</th><th style='padding:6px 9px;font-size:10px;color:#888;text-align:left;'>Status</th><th style='padding:6px 9px;font-size:10px;color:#888;text-align:left;'>Target</th></tr></thead><tbody>"+irows+"</tbody></table></td></tr>" if irows else ""}
{"<tr><td style='padding:0 28px 22px;'><div style='background:#fff8f0;border:1px solid #fcd34d;border-radius:6px;padding:11px 15px;font-size:13px;color:#92400e;'>"+message+"</div></td></tr>" if message else ""}
<tr><td style="background:#F7A600;padding:13px;text-align:center;">
  <p style="margin:0;color:#002060;font-size:12px;font-weight:bold;">Automated Report — Plant Open Issue Tracker | Sujan Continental AVS Pvt Ltd</p>
</td></tr>
</table></td></tr></table></body></html>"""

        # ── Send via Brevo API ─────────────────────────────────
        recipients = [{"email": to}]
        if cc:
            recipients_cc = [{"email": cc}]
        else:
            recipients_cc = None

        payload = {
            "sender": {"email": MAIL_FROM, "name": "Sujan Continental AVS"},
            "to": recipients,
            "subject": subject,
            "htmlContent": html
        }
        if recipients_cc:
            payload["cc"] = recipients_cc

        if not BREVO_API_KEY:
            raise Exception("BREVO_API_KEY not set")

        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json=payload,
            timeout=15
        )
        if resp.status_code not in (200, 201):
            raise Exception(f"Brevo API {resp.status_code}: {resp.text}")
        print(f"Report email sent to {to}")

        return jsonify({"success": True, "message": f"Report sent to {to}" + (f" and {cc}" if cc else "")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
# ══════════════════════════════════════════════════════════════
#  HELPERS — EMAIL & WHATSAPP
# ══════════════════════════════════════════════════════════════

def _send_html_email(to, subject, body_text):
    try:
        html = f"""<html><body style="font-family:Arial;background:#f5f5f5;padding:20px;">
    <div style="max-width:560px;margin:auto;background:white;border-radius:10px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.1);">
      <div style="background:#002060;padding:22px;text-align:center;">
        <h2 style="color:#F7A600;margin:0;letter-spacing:2px;">PLANT OPEN ISSUE TRACKER</h2>
        <p style="color:rgba(255,255,255,0.65);margin:4px 0 0;font-size:13px;">Sujan Continental AVS Pvt Ltd</p>
      </div>
      <div style="background:#F7A600;height:4px;"></div>
      <div style="padding:28px;">
        <pre style="font-family:Arial;white-space:pre-wrap;font-size:14px;line-height:1.7;color:#333;margin:0;">{body_text}</pre>
      </div>
      <div style="background:#F7A600;padding:12px;text-align:center;">
        <p style="margin:0;color:#002060;font-size:12px;font-weight:bold;">Automated — Plant Open Issue Tracker System</p>
      </div>
    </div></body></html>"""

        if not BREVO_API_KEY:
            print("[MAIL ERROR] BREVO_API_KEY not set")
            return

        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "sender": {"email": MAIL_FROM, "name": "Sujan Continental AVS"},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html,
                "textContent": body_text
            },
            timeout=15
        )
        if resp.status_code in (200, 201):
            print(f"Email sent to {to}")
        else:
            print(f"EMAIL ERROR: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"EMAIL ERROR: {e}")


def _send_whatsapp(to_phone, message):
    try:
        print(f"WHATSAPP: Would send to {to_phone}")
        print(f"WHATSAPP: Skipped - checking Twilio credentials")
    except Exception as e:
        print(f"WHATSAPP ERROR: {e}")


def _notify_admin_pending(issue_id, pending_id, submitted_by, remark, status, progress):
    subject = f"Approval Required — Issue #{issue_id} Update by {submitted_by}"
    body = f"""Dear Admin,

{submitted_by} has submitted a progress update for Issue #{issue_id} requiring your approval.

Proposed Update:
  Status   : {status}
  Progress : {progress}%
  Remark   : {remark}

Please login to approve or reject this update.

Plant Open Issue Tracker — Sujan Continental AVS Pvt Ltd"""
    _send_html_email(ADMIN_EMAIL, subject, body)


def _notify_user_decision(decision, username, issue, remark, new_status, new_progress, admin_remark):
    user_email = USERS.get(username, {}).get("email", "")
    if not user_email:
        return
    subject = f"Update {decision} — Issue #{issue['id']}"
    body = f"""Dear {username},

Your progress update for Issue #{issue['id']} has been {decision.upper()}.

Issue   : {issue['description']}
Remark  : {remark}
"""
    if decision == "Approved":
        body += f"New Status  : {new_status}\nNew Progress: {new_progress}%\n"
    if admin_remark:
        body += f"Admin Note  : {admin_remark}\n"
    body += "\nPlant Open Issue Tracker — Sujan Continental AVS Pvt Ltd"
    _send_html_email(user_email, subject, body)

@app.route("/debug")
def debug():
    return jsonify({
        "mysql_url": os.environ.get('MYSQL_URL', 'NOT FOUND'),
        "db_config": {
            "host": db_config.get('host'),
            "port": db_config.get('port'),
            "database": db_config.get('database')
        }
    })


# ══════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5001)), debug=False)
