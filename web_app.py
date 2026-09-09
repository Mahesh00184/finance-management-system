import os
import sqlite3
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    abort
)
from werkzeug.security import check_password_hash


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DB_PATH = os.environ.get(
    "FINANCE_DB_PATH",
    os.path.join(BASE_DIR, "finance.db")
)

RECEIPT_FOLDER = os.environ.get(
    "FINANCE_RECEIPT_FOLDER",
    os.path.join(BASE_DIR, "receipts")
)

app = Flask(__name__)

# Set a long random value using an environment variable in production.
app.secret_key = os.environ.get(
    "FINANCE_SECRET_KEY",
    "change-this-development-secret-key"
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,  # Change to True when using HTTPS
)


def get_db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_db() as db:
        db.execute("""
        CREATE TABLE IF NOT EXISTS customer_accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        )
        """)
        db.commit()


def customer_login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if "customer_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))

        return view_function(*args, **kwargs)

    return wrapped_view


@app.route("/")
def home():
    if "customer_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "customer_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Enter your username and password.", "danger")
            return render_template("login.html")

        with get_db() as db:
            account = db.execute("""
                SELECT
                    customer_accounts.customer_id,
                    customer_accounts.username,
                    customer_accounts.password_hash,
                    customer_accounts.active,
                    customers.name
                FROM customer_accounts
                JOIN customers
                    ON customers.id = customer_accounts.customer_id
                WHERE customer_accounts.username = ?
            """, (username,)).fetchone()

        valid_login = (
            account is not None
            and account["active"] == 1
            and check_password_hash(
                account["password_hash"],
                password
            )
        )

        if not valid_login:
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        session.clear()
        session["customer_id"] = account["customer_id"]
        session["customer_name"] = account["name"]

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/dashboard")
@customer_login_required
def dashboard():
    customer_id = session["customer_id"]

    with get_db() as db:
        customer = db.execute("""
            SELECT
                id,
                bill,
                name,
                phone,
                address,
                loan,
                metal,
                weight,
                details,
                date,
                status
            FROM customers
            WHERE id = ?
        """, (customer_id,)).fetchone()

        if customer is None:
            session.clear()
            flash("Customer account was not found.", "danger")
            return redirect(url_for("login"))

        payments = db.execute("""
            SELECT id, amount, date
            FROM payments
            WHERE customer_id = ?
            ORDER BY id DESC
        """, (customer_id,)).fetchall()

        payment_total = db.execute("""
            SELECT COALESCE(SUM(amount), 0) AS paid
            FROM payments
            WHERE customer_id = ?
        """, (customer_id,)).fetchone()

    loan_amount = float(customer["loan"] or 0)
    paid_amount = float(payment_total["paid"] or 0)
    balance = max(loan_amount - paid_amount, 0)

    return render_template(
        "dashboard.html",
        customer=customer,
        payments=payments,
        loan_amount=loan_amount,
        paid_amount=paid_amount,
        balance=balance
    )


@app.route("/receipt")
@customer_login_required
def receipt():
    customer_id = session["customer_id"]

    with get_db() as db:
        customer = db.execute("""
            SELECT bill
            FROM customers
            WHERE id = ?
        """, (customer_id,)).fetchone()

    if customer is None:
        abort(404)

    filename = f"{customer['bill']}.pdf"
    full_path = os.path.join(RECEIPT_FOLDER, filename)

    if not os.path.isfile(full_path):
        flash(
            "Receipt has not been generated. Please contact the finance office.",
            "warning"
        )
        return redirect(url_for("dashboard"))

    return send_from_directory(
        RECEIPT_FOLDER,
        filename,
        as_attachment=True,
        download_name=filename
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {
        "status": "online",
        "database_found": os.path.isfile(DB_PATH)
    }


initialize_database()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=8080,
        debug=False
    )