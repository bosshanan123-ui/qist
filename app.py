"""Qist - Installment Management System (Flask + Supabase), deployable on Vercel."""
import calendar
import math
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from markupsafe import Markup
from supabase import create_client
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,  # Vercel request limit is ~4.5 MB
)

# ---- Business rules (from the project report) -------------------------------
MIN_DOWN_PCT = 20      # minimum down payment, % of installment price
FINE_PCT = 2           # late fine, % of the monthly installment
MAX_OVERDUE = 3        # customer is blocked from new applications at this many overdue installments
CNIC_RE = re.compile(r"^\d{5}-\d{7}-\d$")
PHONE_RE = re.compile(r"^03\d{2}-?\d{7}$")
HOME = {"admin": "admin", "officer": "officer", "customer": "dashboard"}

# ---- Database ---------------------------------------------------------------
_sb = None


def db():
    global _sb
    if _sb is None:
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _sb


def T(name):
    return db().table(name)


def first(query):
    rows = query.limit(1).execute().data
    return rows[0] if rows else None


def get_plans():
    return T("plans").select("*").order("months").execute().data


def audit(action, detail=""):
    T("audit_log").insert({"actor_id": g.user["id"] if g.get("user") else None,
                           "action": action, "detail": detail}).execute()


def now():
    return datetime.now(timezone.utc).isoformat()


# ---- Helpers ----------------------------------------------------------------
def to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def calc(price, months, markup, down=None):
    total = round(price * (1 + markup / 100))
    min_down = math.ceil(total * MIN_DOWN_PCT / 100)
    down = min_down if down is None else down
    monthly = math.ceil(max(total - down, 0) / months)
    return {"total": total, "min_down": min_down, "down": down, "monthly": monthly}


def decorate(i):
    """Add overdue flag and fine to an installment row. Fine = 2% of the installment, charged only when late."""
    i["overdue"] = i["status"] == "pending" and date.fromisoformat(i["due_date"]) < date.today()
    i["fine"] = i["fine_charged"] if i["status"] == "paid" else (round(i["amount"] * FINE_PCT / 100) if i["overdue"] else 0)
    i["due_total"] = i["amount"] + i["fine"]
    return i


def new_txn():
    return f"IMS-{date.today():%y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def overdue_count(uid):
    ids = [a["id"] for a in T("applications").select("id").eq("customer_id", uid).eq("status", "active").execute().data]
    if not ids:
        return 0
    return len(T("installments").select("id").in_("application_id", ids).eq("status", "pending")
               .lt("due_date", str(date.today())).execute().data)


ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


def upload(file, folder):
    if not file or not file.filename:
        return None
    data = file.read()
    if not data or len(data) > 2 * 1024 * 1024 or file.mimetype not in ALLOWED:
        return None
    path = f"{folder}/{uuid.uuid4().hex}.{ALLOWED[file.mimetype]}"
    db().storage.from_("documents").upload(path, data, {"content-type": file.mimetype})
    return path


def signed(path):
    r = db().storage.from_("documents").create_signed_url(path, 3600)
    return r.get("signedURL") or r.get("signedUrl")


def settle(p):
    """Mark the installment behind a confirmed payment as paid; close the plan when nothing is left."""
    inst = first(T("installments").select("*").eq("id", p["installment_id"]))
    T("installments").update({"status": "paid", "paid_on": str(date.today()),
                              "fine_charged": max(0, p["amount"] - inst["amount"])}).eq("id", inst["id"]).execute()
    T("payments").update({"status": "confirmed"}).eq("id", p["id"]).execute()
    left = T("installments").select("id").eq("application_id", p["application_id"]).eq("status", "pending").execute().data
    if not left:
        T("applications").update({"status": "completed", "updated_at": now()}).eq("id", p["application_id"]).execute()


def next_due(app_id):
    insts = T("installments").select("*").eq("application_id", app_id).eq("status", "pending").order("seq").limit(1).execute().data
    return decorate(insts[0]) if insts else None


# ---- Request plumbing -------------------------------------------------------
app.jinja_env.filters["money"] = lambda v: f"Rs {float(v or 0):,.0f}"
app.jinja_env.filters["label"] = lambda s: str(s).replace("_", " ").capitalize()
app.jinja_env.globals["csrf_field"] = lambda: Markup(f'<input type="hidden" name="_csrf" value="{session.get("csrf", "")}">')


@app.before_request
def guard():
    session.setdefault("csrf", uuid.uuid4().hex)
    g.user = session.get("u")
    if request.method == "POST" and request.form.get("_csrf") != session["csrf"]:
        abort(400)


@app.context_processor
def inject():
    return {"user": g.get("user"), "today": date.today(), "MIN_DOWN_PCT": MIN_DOWN_PCT, "FINE_PCT": FINE_PCT}


def login_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if roles and g.user["role"] not in roles:
                abort(403)
            return f(*a, **k)
        return w
    return deco


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def err(e):
    msg = {400: "That request could not be verified. Go back and try again.",
           403: "Your account does not have access to this page.",
           404: "We could not find that page.",
           413: "The files are too large. Keep each upload under 2 MB."}.get(e.code, "Something went wrong.")
    return render_template("error.html", code=e.code, msg=msg), e.code


# ---- Public + auth ----------------------------------------------------------
@app.route("/")
def index():
    products = T("products").select("*").eq("active", True).order("created_at", desc=True).limit(6).execute().data
    return render_template("index.html", products=products, plans=get_plans())


@app.route("/products")
def products():
    items = T("products").select("*").eq("active", True).order("category").execute().data
    return render_template("products.html", products=items)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("name", "email", "phone", "cnic")}
        f["email"] = f["email"].lower()
        pw = request.form.get("password", "")
        e = None
        if len(f["name"]) < 3: e = "Enter your full name."
        elif "@" not in f["email"]: e = "Enter a valid email address."
        elif not PHONE_RE.match(f["phone"]): e = "Phone number must look like 0300-1234567."
        elif not CNIC_RE.match(f["cnic"]): e = "CNIC must look like 35202-1234567-1."
        elif len(pw) < 8: e = "Password needs at least 8 characters."
        elif first(T("users").select("id").eq("email", f["email"])): e = "An account with this email already exists."
        elif first(T("users").select("id").eq("cnic", f["cnic"])): e = "An account with this CNIC already exists."
        if e:
            flash(e, "error")
            return render_template("auth.html", mode="register", form=f)
        T("users").insert({**f, "password_hash": generate_password_hash(pw), "role": "customer"}).execute()
        flash("Account created. Log in to continue.", "success")
        return redirect(url_for("login"))
    return render_template("auth.html", mode="register", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        u = first(T("users").select("*").eq("email", email))
        if not u or not check_password_hash(u["password_hash"], request.form.get("password", "")):
            flash("Email or password is incorrect.", "error")
        elif not u["active"]:
            flash("This account is disabled. Contact the shop admin.", "error")
        else:
            session.clear()
            session["csrf"] = uuid.uuid4().hex
            session["u"] = {"id": u["id"], "name": u["name"], "role": u["role"]}
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for(HOME[u["role"]]))
    return render_template("auth.html", mode="login", form={})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---- Customer ---------------------------------------------------------------
@app.route("/dashboard")
@login_required("customer")
def dashboard():
    apps = T("applications").select("*, product:products(name,image_url,category)").eq("customer_id", g.user["id"]) \
        .order("created_at", desc=True).execute().data
    by_app = defaultdict(list)
    if apps:
        for i in T("installments").select("*").in_("application_id", [a["id"] for a in apps]).order("seq").execute().data:
            by_app[i["application_id"]].append(decorate(i))
    for a in apps:
        ins = by_app[a["id"]]
        a["paid_n"] = sum(1 for i in ins if i["status"] == "paid")
        a["next"] = next((i for i in ins if i["status"] == "pending"), None)
        a["overdue_n"] = sum(1 for i in ins if i["overdue"])
    return render_template("dashboard.html", apps=apps, overdue=sum(a["overdue_n"] for a in apps))


@app.route("/apply/<uuid:pid>", methods=["GET", "POST"])
@login_required("customer")
def apply(pid):
    prod = first(T("products").select("*").eq("id", str(pid)).eq("active", True))
    if not prod:
        abort(404)
    plans = get_plans()
    blocked = None
    if prod["stock"] < 1:
        blocked = "This product is out of stock, so new applications are closed."
    elif overdue_count(g.user["id"]) >= MAX_OVERDUE:
        blocked = f"You have {MAX_OVERDUE} or more overdue installments. Clear them before applying again."
    if request.method == "POST" and not blocked:
        m, down = to_int(request.form.get("months")), to_int(request.form.get("down"))
        plan = next((p for p in plans if p["months"] == m), None)
        f = {k: request.form.get(k, "").strip() for k in ("address", "guarantor_name", "guarantor_cnic", "guarantor_phone", "guarantor_address")}
        c = calc(prod["cash_price"], m, float(plan["markup_pct"]), down) if plan else None
        e = None
        if not plan: e = "Choose an installment plan."
        elif down < c["min_down"] or down >= c["total"]: e = f"Down payment must be at least {c['min_down']:,} and less than the total price."
        elif len(f["address"]) < 10: e = "Enter your full home address."
        elif len(f["guarantor_name"]) < 3: e = "Enter the guarantor's full name."
        elif not CNIC_RE.match(f["guarantor_cnic"]): e = "Guarantor CNIC must look like 35202-1234567-1."
        elif not PHONE_RE.match(f["guarantor_phone"]): e = "Guarantor phone must look like 0300-1234567."
        elif len(f["guarantor_address"]) < 10: e = "Enter the guarantor's full address."
        else:
            cnic = upload(request.files.get("cnic_doc"), g.user["id"])
            bill = upload(request.files.get("bill_doc"), g.user["id"])
            if not cnic or not bill:
                e = "Upload your CNIC copy and a utility bill (JPG, PNG or PDF, up to 2 MB each)."
        if e:
            flash(e, "error")
        else:
            row = T("applications").insert({**f, "customer_id": g.user["id"], "product_id": prod["id"], "months": m,
                                            "total_price": c["total"], "down_payment": down, "monthly_amount": c["monthly"],
                                            "cnic_doc": cnic, "bill_doc": bill}).execute().data[0]
            audit("application_submitted", f"{prod['name']} / {m} months")
            flash("Application submitted. A verification officer will review it.", "success")
            return redirect(url_for("application", app_id=row["id"]))
    return render_template("apply.html", prod=prod, plans=plans, blocked=blocked, form=request.form)


@app.post("/application/<uuid:app_id>/resubmit")
@login_required("customer")
def resubmit(app_id):
    a = load_app(str(app_id))
    if a["status"] != "needs_info":
        abort(400)
    upd = {"status": "submitted", "updated_at": now()}
    for field in ("cnic_doc", "bill_doc"):
        p = upload(request.files.get(field), g.user["id"])
        if p:
            upd[field] = p
    T("applications").update(upd).eq("id", a["id"]).execute()
    audit("application_resubmitted", a["id"])
    flash("Updated documents sent for review.", "success")
    return redirect(url_for("application", app_id=a["id"]))


@app.post("/application/<uuid:app_id>/pay")
@login_required("customer")
def pay(app_id):
    a = load_app(str(app_id))
    method, ref = request.form.get("method"), request.form.get("reference", "").strip()
    inst = next_due(a["id"]) if a["status"] == "active" else None
    if not inst: abort(400)
    if method not in ("jazzcash", "easypaisa", "bank") or len(ref) < 6:
        flash("Choose a payment method and enter the transaction reference from your receipt.", "error")
    elif first(T("payments").select("id").eq("installment_id", inst["id"]).eq("status", "pending")):
        flash("You already sent a payment for this installment. It is waiting for confirmation.", "error")
    else:
        T("payments").insert({"application_id": a["id"], "installment_id": inst["id"], "amount": inst["due_total"],
                              "method": method, "reference": ref, "txn_id": new_txn()}).execute()
        audit("payment_submitted", f"{a['id']} / installment {inst['seq']}")
        flash("Payment sent. The shop will confirm it and your receipt will appear here.", "success")
    return redirect(url_for("application", app_id=a["id"]))


# ---- Shared application view ------------------------------------------------
def load_app(app_id):
    a = first(T("applications").select(
        "*, customer:users!customer_id(id,name,email,phone,cnic), product:products(name,category,image_url,cash_price), officer:users!officer_id(name)"
    ).eq("id", app_id))
    if not a or (g.user["role"] == "customer" and a["customer"]["id"] != g.user["id"]):
        abort(404)
    return a


@app.route("/application/<uuid:app_id>")
@login_required()
def application(app_id):
    a = load_app(str(app_id))
    insts = [decorate(i) for i in T("installments").select("*").eq("application_id", a["id"]).order("seq").execute().data]
    pays = T("payments").select("*").eq("application_id", a["id"]).order("created_at", desc=True).execute().data
    nxt = next((i for i in insts if i["status"] == "pending"), None)
    waiting = bool(nxt) and any(p.get("installment_id") == nxt["id"] and p["status"] == "pending" for p in pays)
    docs = {k: signed(a[k]) for k in ("cnic_doc", "bill_doc") if a.get(k)}
    paid = sum(p["amount"] for p in pays if p["status"] == "confirmed")
    remaining = sum(i["amount"] for i in insts if i["status"] == "pending")
    return render_template("application.html", a=a, insts=insts, pays=pays, nxt=nxt, waiting=waiting,
                           docs=docs, paid=paid, remaining=remaining)


@app.route("/receipt/<uuid:pid>")
@login_required()
def receipt(pid):
    p = first(T("payments").select("*").eq("id", str(pid)).eq("status", "confirmed"))
    if not p:
        abort(404)
    a = load_app(p["application_id"])
    inst = first(T("installments").select("*").eq("id", p["installment_id"])) if p.get("installment_id") else None
    return render_template("receipt.html", p=p, a=a, inst=inst)


# ---- Verification officer ---------------------------------------------------
@app.route("/officer")
@login_required("officer")
def officer():
    q = request.args.get("q", "").strip()
    sel = "*, customer:users!customer_id(name,cnic,phone), product:products(name)"
    if q:
        ids = [u["id"] for u in T("users").select("id").eq("cnic", q).execute().data]
        apps = T("applications").select(sel).in_("customer_id", ids).order("created_at", desc=True).execute().data if ids else []
    else:
        apps = T("applications").select(sel).in_("status", ["submitted", "needs_info"]).order("created_at").execute().data
    return render_template("officer.html", apps=apps, q=q)


@app.post("/application/<uuid:app_id>/verify")
@login_required("officer")
def verify(app_id):
    a = load_app(str(app_id))
    status = {"approved": "verified", "rejected": "rejected", "needs_info": "needs_info"}.get(request.form.get("decision"))
    notes = request.form.get("notes", "").strip()
    if a["status"] not in ("submitted", "needs_info") or not status: abort(400)
    if len(notes) < 5:
        flash("Add remarks from your visit before saving.", "error")
        return redirect(url_for("application", app_id=a["id"]))
    T("applications").update({"status": status, "officer_notes": notes, "officer_id": g.user["id"], "updated_at": now()}).eq("id", a["id"]).execute()
    audit("verification_" + status, a["id"])
    flash("Verification result saved.", "success")
    return redirect(url_for("officer"))


# ---- Admin ------------------------------------------------------------------
@app.post("/application/<uuid:app_id>/decide")
@login_required("admin")
def decide(app_id):
    a = load_app(str(app_id))
    act, notes = request.form.get("action"), request.form.get("notes", "").strip()
    if act == "reject" and a["status"] in ("submitted", "needs_info", "verified"):
        T("applications").update({"status": "rejected", "admin_notes": notes, "updated_at": now()}).eq("id", a["id"]).execute()
        audit("admin_rejected", a["id"])
        flash("Application rejected.", "success")
    elif act == "approve" and a["status"] == "verified":
        prod = first(T("products").select("*").eq("id", a["product_id"]))
        if prod["stock"] < 1:
            flash("This product is out of stock. Restock it before approving.", "error")
            return redirect(url_for("application", app_id=a["id"]))
        T("products").update({"stock": prod["stock"] - 1}).eq("id", prod["id"]).execute()
        start = date.today()
        T("installments").insert([{"application_id": a["id"], "seq": n, "due_date": str(add_months(start, n)),
                                   "amount": a["monthly_amount"]} for n in range(1, a["months"] + 1)]).execute()
        T("payments").insert({"application_id": a["id"], "kind": "down", "amount": a["down_payment"], "method": "cash",
                              "status": "confirmed", "txn_id": new_txn(), "recorded_by": g.user["id"],
                              "reference": "Down payment"}).execute()
        T("applications").update({"status": "active", "start_date": str(start), "admin_notes": notes, "updated_at": now()}).eq("id", a["id"]).execute()
        audit("plan_activated", a["id"])
        flash("Plan activated. The installment schedule has been created.", "success")
    else:
        abort(400)
    return redirect(url_for("application", app_id=a["id"]))


@app.post("/application/<uuid:app_id>/cash")
@login_required("admin")
def cash(app_id):
    a = load_app(str(app_id))
    inst = next_due(a["id"]) if a["status"] == "active" else None
    if not inst: abort(400)
    for p in T("payments").select("id").eq("installment_id", inst["id"]).eq("status", "pending").execute().data:
        T("payments").update({"status": "rejected"}).eq("id", p["id"]).execute()
    p = T("payments").insert({"application_id": a["id"], "installment_id": inst["id"], "amount": inst["due_total"],
                              "method": "cash", "status": "pending", "txn_id": new_txn(), "recorded_by": g.user["id"],
                              "reference": "Paid at counter"}).execute().data[0]
    settle(p)
    audit("cash_recorded", f"{a['id']} / installment {inst['seq']}")
    flash("Cash payment recorded and receipt created.", "success")
    return redirect(url_for("application", app_id=a["id"]))


@app.post("/admin/payment/<uuid:pid>/<action>")
@login_required("admin")
def payment_action(pid, action):
    p = first(T("payments").select("*").eq("id", str(pid)).eq("status", "pending"))
    if not p or action not in ("confirm", "reject"): abort(404)
    if action == "confirm":
        settle(p)
    else:
        T("payments").update({"status": "rejected"}).eq("id", p["id"]).execute()
    T("payments").update({"recorded_by": g.user["id"]}).eq("id", p["id"]).execute()
    audit(f"payment_{action}ed", p["txn_id"])
    flash("Payment confirmed." if action == "confirm" else "Payment rejected.", "success")
    return redirect(url_for("admin"))


@app.route("/admin")
@login_required("admin")
def admin():
    q = re.sub(r"[^\w\s@.\-]", "", request.args.get("q", ""))[:40].strip()
    found = []
    if q:
        found = T("users").select("*").eq("role", "customer").or_(f"name.ilike.%{q}%,phone.ilike.%{q}%,cnic.ilike.%{q}%").limit(20).execute().data
        if found:
            theirs = T("applications").select("id,customer_id,status,product:products(name)").in_("customer_id", [u["id"] for u in found]).execute().data
            for u in found:
                u["apps"] = [x for x in theirs if x["customer_id"] == u["id"]]
    apps = T("applications").select("id,status,total_price").execute().data
    pend = [decorate(i) for i in T("installments").select("*").eq("status", "pending").execute().data]
    od = [i for i in pend if i["overdue"]]
    stats = {
        "customers": T("users").select("id", count="exact").eq("role", "customer").execute().count or 0,
        "active": sum(1 for a in apps if a["status"] == "active"),
        "sales": sum(a["total_price"] for a in apps if a["status"] in ("active", "completed")),
        "outstanding": sum(i["amount"] for i in pend),
        "overdue_n": len(od), "overdue_amt": sum(i["due_total"] for i in od),
    }
    pay_sel = "*, application:applications(id, customer:users!customer_id(name), product:products(name))"
    pending_pay = T("payments").select(pay_sel).eq("status", "pending").order("created_at").execute().data
    awaiting = T("applications").select("id,total_price,months,customer:users!customer_id(name),product:products(name)") \
        .eq("status", "verified").order("updated_at").execute().data
    return render_template("admin.html", stats=stats, pending_pay=pending_pay, awaiting=awaiting, q=q, found=found)


@app.route("/admin/products", methods=["GET", "POST"])
@login_required("admin")
def admin_products():
    if request.method == "POST":
        name, price = request.form.get("name", "").strip(), to_int(request.form.get("cash_price"))
        if len(name) < 2 or price <= 0:
            flash("Enter a product name and a cash price above zero.", "error")
        else:
            T("products").insert({"name": name, "category": request.form.get("category", "").strip() or "General",
                                  "description": request.form.get("description", "").strip(), "cash_price": price,
                                  "stock": max(0, to_int(request.form.get("stock"))),
                                  "image_url": request.form.get("image_url", "").strip() or None}).execute()
            audit("product_added", name)
            flash("Product added.", "success")
        return redirect(url_for("admin_products"))
    items = T("products").select("*").order("created_at", desc=True).execute().data
    return render_template("admin_products.html", items=items, plans=get_plans())


@app.post("/admin/products/<uuid:pid>")
@login_required("admin")
def admin_product_update(pid):
    price = to_int(request.form.get("cash_price"))
    if price <= 0: abort(400)
    T("products").update({"name": request.form.get("name", "").strip(), "cash_price": price,
                          "stock": max(0, to_int(request.form.get("stock"))),
                          "active": request.form.get("active") == "on"}).eq("id", str(pid)).execute()
    audit("product_updated", str(pid))
    flash("Product saved.", "success")
    return redirect(url_for("admin_products"))


@app.post("/admin/plans")
@login_required("admin")
def admin_plans():
    for p in get_plans():
        try:
            v = float(request.form.get(f"m{p['months']}", ""))
        except ValueError:
            continue
        if 0 <= v <= 100:
            T("plans").update({"markup_pct": v}).eq("months", p["months"]).execute()
    audit("plans_updated")
    flash("Markup rates saved. They apply to new applications only.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/users", methods=["GET", "POST"])
@login_required("admin")
def admin_users():
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("name", "email", "phone", "cnic")}
        f["email"], role, pw = f["email"].lower(), request.form.get("role"), request.form.get("password", "")
        e = None
        if role not in ("officer", "admin", "customer"): e = "Choose a role."
        elif len(f["name"]) < 3 or "@" not in f["email"]: e = "Enter a name and valid email."
        elif not PHONE_RE.match(f["phone"]) or not CNIC_RE.match(f["cnic"]): e = "Check the phone (0300-1234567) and CNIC (35202-1234567-1) formats."
        elif len(pw) < 8: e = "Password needs at least 8 characters."
        elif first(T("users").select("id").eq("email", f["email"])) or first(T("users").select("id").eq("cnic", f["cnic"])): e = "Email or CNIC already in use."
        if e: flash(e, "error")
        else:
            T("users").insert({**f, "role": role, "password_hash": generate_password_hash(pw)}).execute()
            audit("user_created", f"{f['email']} ({role})")
            flash("Account created.", "success")
        return redirect(url_for("admin_users"))
    users = T("users").select("id,name,email,phone,cnic,role,active,created_at").order("created_at", desc=True).execute().data
    return render_template("admin_users.html", users=users)


@app.post("/admin/users/<uuid:uid>/toggle")
@login_required("admin")
def admin_user_toggle(uid):
    if str(uid) == g.user["id"]:
        flash("You cannot disable your own account.", "error")
    else:
        u = first(T("users").select("id,active,email").eq("id", str(uid)))
        if not u: abort(404)
        T("users").update({"active": not u["active"]}).eq("id", u["id"]).execute()
        audit("user_disabled" if u["active"] else "user_enabled", u["email"])
        flash("Account updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/reports")
@login_required("admin")
def admin_reports():
    pays = T("payments").select("amount,created_at").eq("status", "confirmed").execute().data
    monthly = defaultdict(int)
    for p in pays:
        monthly[p["created_at"][:7]] += p["amount"]
    months = sorted(monthly.items(), reverse=True)[:6]
    od = [decorate(i) for i in T("installments").select("*").eq("status", "pending").lt("due_date", str(date.today())).execute().data]
    defaulters = []
    if od:
        info = {a["id"]: a for a in T("applications").select("id, customer:users!customer_id(name,phone,cnic), product:products(name)")
                .in_("id", list({i["application_id"] for i in od})).execute().data}
        grouped = defaultdict(lambda: {"n": 0, "amt": 0})
        for i in od:
            grouped[i["application_id"]]["n"] += 1
            grouped[i["application_id"]]["amt"] += i["due_total"]
        defaulters = sorted(({"app": info[k], **v} for k, v in grouped.items()), key=lambda r: -r["amt"])
    sold = T("applications").select("total_price, product:products(cash_price)").in_("status", ["active", "completed"]).execute().data
    profit = sum(s["total_price"] - s["product"]["cash_price"] for s in sold)
    fines = sum(i["fine_charged"] for i in T("installments").select("fine_charged").eq("status", "paid").execute().data)
    log = T("audit_log").select("*").order("created_at", desc=True).limit(40).execute().data
    return render_template("admin_reports.html", months=months, defaulters=defaulters, profit=profit, fines=fines, log=log)


if __name__ == "__main__":
    app.run(debug=True)
