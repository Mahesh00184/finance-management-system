import os
import sys
import sqlite3
import threading
import secrets
import socket
import webbrowser
import tkinter as tk
import json
import re

from datetime import datetime
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from datetime import datetime

import pandas as pd
import ttkbootstrap as ttk

from tkinter import messagebox
from PIL import ImageTk, Image as PILImage

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image
)
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    flash,
    render_template_string,
    send_file,
    abort
)

from werkzeug.security import generate_password_hash, check_password_hash
from waitress import create_server


# ============================================================
# CONFIGURATION
# ============================================================

SHOP_NAME = "Finance Management"
SHOP_ADDRESS = "Nagapattinam"
SHOP_PHONE = ""

WEB_HOST = "0.0.0.0"
WEB_PORT = 8080


def resource_path(relative_path):
    candidates = []

    if hasattr(sys, "_MEIPASS"):
        candidates.append(
            os.path.join(sys._MEIPASS, relative_path)
        )

    candidates.extend([
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            relative_path
        ),
        os.path.join(
            os.getcwd(),
            relative_path
        ),
        os.path.join(
            os.path.dirname(sys.executable),
            relative_path
        )
    ])

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return candidates[0]


def get_data_directory():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)

    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_data_directory()

LOGO_PATH = resource_path("logo.png")
DB_PATH = os.path.join(BASE_DIR, "finance.db")
EXCEL_FILE = os.path.join(BASE_DIR, "All_Customers.xlsx")
RECEIPT_FOLDER = os.path.join(BASE_DIR, "receipts")
BACKUP_FOLDER = os.path.join(BASE_DIR, "backup")
SECRET_FILE = os.path.join(BASE_DIR, ".portal_secret")

os.makedirs(RECEIPT_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)


# ============================================================
# PHONE NORMALIZATION
# ============================================================

def normalize_phone(phone):
    """
    Converts phone numbers to digits.

    Examples:
        +91 94895 74395 -> 9489574395
        94895-74395     -> 9489574395

    Indian numbers longer than 10 digits use the final 10 digits.
    """

    digits = "".join(
        character
        for character in str(phone or "")
        if character.isdigit()
    )

    if len(digits) > 10:
        digits = digits[-10:]

    return digits


# ============================================================
# DATABASE CONNECTION
# ============================================================

def create_database_connection(row_factory=False):
    database = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    if row_factory:
        database.row_factory = sqlite3.Row

    database.create_function(
        "NORMALIZE_PHONE",
        1,
        normalize_phone
    )

    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA busy_timeout=30000")
    database.execute("PRAGMA foreign_keys=ON")

    return database


conn = create_database_connection()
cursor = conn.cursor()


# ============================================================
# DATABASE INITIALIZATION AND MIGRATION
# ============================================================

def table_exists(table_name):
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
    """, (table_name,))

    return cursor.fetchone() is not None


def create_portal_accounts_table():
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customer_accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)


def migrate_customer_accounts_table():
    """
    Migrates the old portal-account structure:

        customer_id, username, password_hash...

    to the new structure:

        phone, username, password_hash...

    This allows one portal account to display multiple bills
    registered with the same phone number.
    """

    if not table_exists("customer_accounts"):
        create_portal_accounts_table()
        conn.commit()
        return

    cursor.execute("PRAGMA table_info(customer_accounts)")
    columns_info = cursor.fetchall()

    column_names = {
        column[1]
        for column in columns_info
    }

    # New schema is already present.
    if "phone" in column_names and "customer_id" not in column_names:
        return

    old_accounts = []

    try:
        if "phone" in column_names:
            cursor.execute("""
                SELECT
                    phone,
                    username,
                    password_hash,
                    active,
                    created_at
                FROM customer_accounts
            """)

            old_accounts = cursor.fetchall()

        elif "customer_id" in column_names:
            cursor.execute("""
                SELECT
                    c.phone,
                    a.username,
                    a.password_hash,
                    a.active,
                    a.created_at
                FROM customer_accounts a
                LEFT JOIN customers c
                    ON c.id = a.customer_id
            """)

            old_accounts = cursor.fetchall()

    except Exception as error:
        print("Portal account migration read error:", error)

    conn.commit()

    cursor.execute("PRAGMA foreign_keys=OFF")

    try:
        cursor.execute(
            "DROP TABLE IF EXISTS customer_accounts_legacy"
        )

        cursor.execute("""
            ALTER TABLE customer_accounts
            RENAME TO customer_accounts_legacy
        """)

        create_portal_accounts_table()

        saved_phones = set()
        saved_usernames = set()

        for account in old_accounts:
            phone = normalize_phone(account[0])
            username = str(account[1] or "").strip()
            password_hash = account[2]
            active = int(account[3] or 0)
            created_at = account[4] or datetime.now().strftime(
                "%d-%m-%Y %H:%M:%S"
            )

            if not phone or not username or not password_hash:
                continue

            if phone in saved_phones:
                continue

            if username in saved_usernames:
                continue

            try:
                cursor.execute("""
                    INSERT INTO customer_accounts(
                        phone,
                        username,
                        password_hash,
                        active,
                        created_at
                    )
                    VALUES(?, ?, ?, ?, ?)
                """, (
                    phone,
                    username,
                    password_hash,
                    active,
                    created_at
                ))

                saved_phones.add(phone)
                saved_usernames.add(username)

            except sqlite3.IntegrityError:
                pass

        cursor.execute(
            "DROP TABLE IF EXISTS customer_accounts_legacy"
        )

        conn.commit()

    except Exception as error:
        conn.rollback()
        print("Portal account migration error:", error)

        # Try to restore the old table if migration failed.
        if not table_exists("customer_accounts") and table_exists(
            "customer_accounts_legacy"
        ):
            cursor.execute("""
                ALTER TABLE customer_accounts_legacy
                RENAME TO customer_accounts
            """)
            conn.commit()

    finally:
        cursor.execute("PRAGMA foreign_keys=ON")


def initialize_database():
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill TEXT NOT NULL UNIQUE,
            name TEXT,
            phone TEXT,
            address TEXT,
            loan REAL NOT NULL DEFAULT 0,
            metal TEXT,
            weight TEXT,
            details TEXT,
            date TEXT,
            status TEXT NOT NULL DEFAULT 'Open'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY(customer_id)
                REFERENCES customers(id)
                ON DELETE CASCADE
        )
    """)

    conn.commit()

    migrate_customer_accounts_table()

    cursor.execute("""
        SELECT id
        FROM users
        WHERE username = ?
    """, ("admin",))

    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO users(
                username,
                password,
                role
            )
            VALUES(?, ?, ?)
        """, (
            "admin",
            "1234",
            "admin"
        ))

        conn.commit()


initialize_database()


def get_web_database():
    return create_database_connection(row_factory=True)


def get_live_gold_rate():
    fallback_rate = 7200.0
    endpoints = [
        "https://www.goldpricesindia.com/cities/tamil-nadu/",
        "https://www.goldpricesindia.com/cities/chennai/",
        "https://api.gold-api.com/price/XAU",
        "https://api.metals.live/v1/spot"
    ]

    def parse_goldpricesindia_rate(text):
        if not isinstance(text, str):
            return None

        patterns = [
            r"Gold\s+Price\s+Today\s+in\s+Tamil\s+Nadu\s+is\s+([\d,]+)\s+Indian\s+Rupee\s*\(INR\)/gram\s+24K",
            r"([\d,]+)\s+Indian\s+Rupee\s*\(INR\)/gram\s+24K",
            r"1\s*g\s*24K\s*\|\s*([\d,]+)",
            r"Rs/\s*g\s*24K.*?([\d,]+)",
            r"([\d,]+)\s+Rs/\s*g"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                try:
                    value = float(match.group(1).replace(",", ""))
                    if 1000 <= value <= 500000:
                        return value
                except ValueError:
                    continue

        return None

    for endpoint in endpoints:
        try:
            request = Request(
                endpoint,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"
                }
            )

            with urlopen(request, timeout=10) as response:
                payload = response.read().decode("utf-8", errors="ignore")

            if "goldpricesindia.com" in endpoint:
                rate = parse_goldpricesindia_rate(payload)
                if rate is not None:
                    return rate

            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = None

            if isinstance(data, dict):
                if "price" in data and isinstance(data["price"], (int, float)):
                    return float(data["price"]) / 31.1035 * 1000

                if "price_gram_24k" in data and isinstance(data["price_gram_24k"], (int, float)):
                    return float(data["price_gram_24k"])

                if "gold" in data and isinstance(data["gold"], (int, float)):
                    return float(data["gold"])

            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    for key in ["gold", "price", "xau", "XAU"]:
                        if key in first and isinstance(first[key], (int, float)):
                            return float(first[key])

        except (URLError, HTTPError, ValueError, TypeError, json.JSONDecodeError):
            continue

    return fallback_rate


# ============================================================
# BACKUP
# ============================================================

def create_backup(filename):
    destination = os.path.join(
        BACKUP_FOLDER,
        filename
    )

    source_connection = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    backup_connection = sqlite3.connect(
        destination
    )

    try:
        source_connection.backup(
            backup_connection
        )
    finally:
        backup_connection.close()
        source_connection.close()

    return destination


def auto_backup():
    try:
        filename = (
            "auto_"
            + datetime.now().strftime("%d-%m-%Y")
            + ".db"
        )

        destination = os.path.join(
            BACKUP_FOLDER,
            filename
        )

        if not os.path.exists(destination):
            create_backup(filename)

    except Exception as error:
        print("Automatic backup error:", error)


# ============================================================
# EXCEL EXPORT
# ============================================================

def update_excel():
    try:
        dataframe = pd.read_sql_query("""
            SELECT
                c.bill,
                c.name,
                c.phone,
                c.address,
                c.loan,
                c.metal,
                c.weight,
                c.details,
                c.date,
                c.status,
                COALESCE(SUM(p.amount), 0) AS paid,
                MAX(
                    c.loan - COALESCE(SUM(p.amount), 0),
                    0
                ) AS balance
            FROM customers c
            LEFT JOIN payments p
                ON p.customer_id = c.id
            GROUP BY c.id
            ORDER BY c.id DESC
        """, conn)

        dataframe.columns = [
            "Bill",
            "Name",
            "Phone",
            "Address",
            "Loan",
            "Metal",
            "Weight",
            "Details",
            "Date",
            "Status",
            "Paid",
            "Balance"
        ]

        dataframe.to_excel(
            EXCEL_FILE,
            index=False
        )

    except Exception as error:
        print("Excel export error:", error)


# ============================================================
# CUSTOMER AND PAYMENT HELPERS
# ============================================================

def get_bill_summary(customer_id):
    cursor.execute("""
        SELECT
            c.id,
            c.bill,
            c.name,
            c.phone,
            c.loan,
            c.status,
            COALESCE(SUM(p.amount), 0) AS paid
        FROM customers c
        LEFT JOIN payments p
            ON p.customer_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
    """, (customer_id,))

    row = cursor.fetchone()

    if row is None:
        return None

    loan = float(row[4] or 0)
    paid = float(row[6] or 0)
    balance = max(loan - paid, 0)

    return {
        "id": row[0],
        "bill": row[1],
        "name": row[2] or "Customer",
        "phone": row[3] or "",
        "loan": loan,
        "status": row[5],
        "paid": paid,
        "balance": balance
    }


def record_payment(customer_id, amount):
    summary = get_bill_summary(customer_id)

    if summary is None:
        raise ValueError("Bill was not found.")

    if summary["balance"] <= 0:
        raise ValueError(
            "This bill has no remaining balance."
        )

    if amount <= 0:
        raise ValueError(
            "Payment amount must be greater than zero."
        )

    if amount > summary["balance"]:
        raise ValueError(
            f"Payment exceeds the remaining balance "
            f"of ₹ {summary['balance']:,.2f}."
        )

    cursor.execute("""
        INSERT INTO payments(
            customer_id,
            amount,
            date
        )
        VALUES(?, ?, ?)
    """, (
        customer_id,
        amount,
        datetime.now().strftime("%d-%m-%Y")
    ))

    new_balance = summary["balance"] - amount

    if new_balance <= 0.001:
        cursor.execute("""
            UPDATE customers
            SET status = 'Closed'
            WHERE id = ?
        """, (customer_id,))
    else:
        cursor.execute("""
            UPDATE customers
            SET status = 'Open'
            WHERE id = ?
              AND status = 'Closed'
        """, (customer_id,))

    conn.commit()
    update_excel()

    return {
        "customer_name": summary["name"],
        "bill": summary["bill"],
        "amount": amount,
        "balance": max(new_balance, 0)
    }


def open_local_file(filepath):
    try:
        normalized_path = os.path.abspath(
            filepath
        ).replace("\\", "/")

        webbrowser.open_new_tab(
            "file:///" + normalized_path
        )
    except Exception:
        webbrowser.open(filepath)


# ============================================================
# PDF RECEIPT
# ============================================================

def generate_receipt_pdf(customer_id):
    database = get_web_database()

    try:
        customer = database.execute("""
            SELECT *
            FROM customers
            WHERE id = ?
        """, (customer_id,)).fetchone()

        if customer is None:
            raise ValueError("Customer was not found.")

        payments = database.execute("""
            SELECT
                id,
                amount,
                date
            FROM payments
            WHERE customer_id = ?
            ORDER BY id DESC
        """, (customer_id,)).fetchall()

        paid_row = database.execute("""
            SELECT
                COALESCE(SUM(amount), 0) AS paid
            FROM payments
            WHERE customer_id = ?
        """, (customer_id,)).fetchone()

        loan = float(customer["loan"] or 0)
        paid = float(paid_row["paid"] or 0)
        balance = max(loan - paid, 0)

        filename = f"{customer['bill']}.pdf"
        filepath = os.path.join(
            RECEIPT_FOLDER,
            filename
        )

        document = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=40,
            leftMargin=40,
            topMargin=35,
            bottomMargin=35
        )

        styles = getSampleStyleSheet()
        elements = []

        if os.path.exists(LOGO_PATH):
            try:
                logo = Image(
                    LOGO_PATH,
                    width=2 * inch,
                    height=1 * inch
                )

                logo.hAlign = "CENTER"
                elements.append(logo)

            except Exception:
                pass

        title_style = ParagraphStyle(
            name="ShopTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=19,
            textColor=colors.HexColor("#B8860B"),
            alignment=1
        )

        address_style = ParagraphStyle(
            name="ShopAddress",
            parent=styles["Normal"],
            fontSize=11,
            alignment=1
        )

        note_style = ParagraphStyle(
            name="Note",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.red,
            leading=15
        )

        elements.append(
            Paragraph(
                SHOP_NAME,
                title_style
            )
        )

        elements.append(
            Paragraph(
                SHOP_ADDRESS,
                address_style
            )
        )

        if SHOP_PHONE:
            elements.append(
                Paragraph(
                    SHOP_PHONE,
                    address_style
                )
            )

        elements.append(Spacer(1, 20))

        customer_data = [
            ["Bill Number", customer["bill"]],
            ["Customer Name", customer["name"] or "Customer"],
            ["Phone", customer["phone"] or "-"],
            ["Address", customer["address"] or "-"],
            ["Loan Amount", f"Rs. {loan:,.2f}"],
            ["Paid Amount", f"Rs. {paid:,.2f}"],
            ["Balance", f"Rs. {balance:,.2f}"],
            ["Metal", customer["metal"] or "-"],
            ["Weight", customer["weight"] or "-"],
            ["Details", customer["details"] or "-"],
            ["Loan Date", customer["date"]],
            ["Status", customer["status"]],
            [
                "Receipt Date",
                datetime.now().strftime("%d-%m-%Y")
            ]
        ]

        customer_table = Table(
            customer_data,
            colWidths=[
                2 * inch,
                4.5 * inch
            ]
        )

        customer_table.setStyle(
            TableStyle([
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.7,
                    colors.grey
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (0, -1),
                    colors.HexColor("#EEEEEE")
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (0, -1),
                    "Helvetica-Bold"
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP"
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    8
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    8
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7
                )
            ])
        )

        elements.append(customer_table)
        elements.append(Spacer(1, 25))

        elements.append(
            Paragraph(
                "<b>Payment History</b>",
                styles["Heading3"]
            )
        )

        payment_data = [
            [
                "Number",
                "Date",
                "Amount"
            ]
        ]

        if payments:
            for payment_number, payment in enumerate(
                payments,
                start=1
            ):
                payment_data.append([
                    str(payment_number),
                    payment["date"],
                    f"Rs. {float(payment['amount']):,.2f}"
                ])
        else:
            payment_data.append([
                "-",
                "No payments recorded",
                "-"
            ])

        payment_table = Table(
            payment_data,
            colWidths=[
                1 * inch,
                3 * inch,
                2.5 * inch
            ]
        )

        payment_table.setStyle(
            TableStyle([
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.7,
                    colors.grey
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#D9D9D9")
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold"
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (0, -1),
                    "CENTER"
                ),
                (
                    "ALIGN",
                    (2, 1),
                    (2, -1),
                    "RIGHT"
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7
                )
            ])
        )

        elements.append(payment_table)
        elements.append(Spacer(1, 25))

        elements.append(
            Paragraph(
                "<b>Important:</b> Payment must be made at least "
                "once every three months.",
                note_style
            )
        )

        elements.append(Spacer(1, 45))

        elements.append(
            Paragraph(
                "Authorized Signature: ______________________________",
                styles["Normal"]
            )
        )

        document.build(elements)

        return filepath

    finally:
        database.close()


# ============================================================
# CUSTOMER WEBSITE
# ============================================================

def load_or_create_secret():
    environment_secret = os.environ.get(
        "FINANCE_SECRET_KEY"
    )

    if environment_secret:
        return environment_secret

    if os.path.exists(SECRET_FILE):
        try:
            with open(
                SECRET_FILE,
                "r",
                encoding="utf-8"
            ) as secret_file:
                saved_secret = secret_file.read().strip()

                if saved_secret:
                    return saved_secret

        except Exception:
            pass

    new_secret = secrets.token_hex(32)

    try:
        with open(
            SECRET_FILE,
            "w",
            encoding="utf-8"
        ) as secret_file:
            secret_file.write(new_secret)

    except Exception:
        pass

    return new_secret


web_app = Flask(__name__)
web_app.secret_key = load_or_create_secret()

web_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False
)


PORTAL_CSS = """
<style>
:root {
    --background: #0d1117;
    --card: #171c24;
    --card-light: #202630;
    --gold: #ffc107;
    --text: #f5f5f5;
    --muted: #aab2bd;
    --border: #343c48;
    --green: #27ae60;
    --red: #dc3545;
    --blue: #0d6efd;
    --orange: #ff9f43;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    background: var(--background);
    font-family: Arial, Helvetica, sans-serif;
}

header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    padding: 18px max(20px, calc((100% - 1200px) / 2));
    color: #111;
    background: var(--gold);
}

header h1 {
    margin: 0;
    font-size: 22px;
}

header p {
    margin: 4px 0 0;
}

main {
    width: min(1200px, calc(100% - 30px));
    margin: 30px auto;
}

.login-box {
    width: min(430px, 100%);
    margin: 70px auto;
    padding: 30px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 15px;
    box-shadow: 0 15px 45px rgba(0, 0, 0, .35);
}

.login-box h2 {
    margin-top: 0;
    color: var(--gold);
}

label {
    display: block;
    margin: 18px 0 7px;
    font-weight: bold;
}

input {
    width: 100%;
    padding: 13px;
    color: white;
    background: #0e1218;
    border: 1px solid var(--border);
    border-radius: 7px;
}

input:focus {
    outline: none;
    border-color: var(--gold);
}

button,
.button {
    display: inline-block;
    padding: 11px 16px;
    color: #111;
    background: var(--gold);
    border: 0;
    border-radius: 7px;
    font-weight: bold;
    text-decoration: none;
    cursor: pointer;
}

form button {
    width: 100%;
    margin-top: 22px;
}

.logout {
    padding: 10px 16px;
    color: white;
    background: #111;
    border-radius: 7px;
    text-decoration: none;
}

.muted {
    color: var(--muted);
    line-height: 1.5;
}

.welcome {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    margin-bottom: 25px;
}

.welcome h2 {
    margin-bottom: 7px;
}

.summary {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 18px;
    margin-bottom: 25px;
}

.summary-card {
    padding: 23px;
    background: var(--card);
    border-top: 4px solid var(--blue);
    border-radius: 10px;
}

.summary-card.paid {
    border-color: var(--green);
}

.summary-card.balance {
    border-color: var(--gold);
}

    .summary-card.gold {
        border-color: var(--orange);
    }

    .summary-card span {
        display: block;
        margin-bottom: 10px;
        color: var(--muted);
    }

    .summary-card small {
        display: block;
        margin-top: 8px;
    font-size: 25px;
}

.card {
    margin-bottom: 25px;
    padding: 25px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
}

.card h3 {
    margin-top: 0;
}

.table-wrapper {
    overflow-x: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th,
td {
    padding: 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
}

th {
    color: var(--gold);
    background: var(--card-light);
}

tr:hover td {
    background: #1d232d;
}

.status {
    display: inline-block;
    min-width: 72px;
    padding: 7px 12px;
    border-radius: 30px;
    font-weight: bold;
    text-align: center;
}

.status-open {
    color: #111;
    background: var(--gold);
}

.status-closed {
    color: white;
    background: var(--green);
}

.loan-details {
    margin-top: 12px;
    padding: 15px;
    background: var(--card-light);
    border: 1px solid var(--border);
    border-radius: 8px;
}

.loan-details summary {
    color: var(--gold);
    font-weight: bold;
    cursor: pointer;
}

.details-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-top: 15px;
}

.detail {
    padding: 13px;
    background: #161b22;
    border-radius: 7px;
}

.detail span {
    display: block;
    margin-bottom: 6px;
    color: var(--muted);
    font-size: 13px;
}

.alert {
    margin-bottom: 18px;
    padding: 14px;
    border-radius: 7px;
}

.alert-danger {
    color: #ffd7db;
    background: #4d1c22;
}

.alert-warning {
    color: #fff0b5;
    background: #4b3c12;
}

.alert-success {
    color: #c9f7d4;
    background: #153e20;
}

.notice {
    padding: 17px;
    color: #ffd2d6;
    background: #41191e;
    border-left: 4px solid var(--red);
    border-radius: 7px;
}

footer {
    padding: 25px;
    color: var(--muted);
    text-align: center;
}

@media (max-width: 800px) {
    header h1 {
        font-size: 17px;
    }

    .summary,
    .details-grid {
        grid-template-columns: 1fr;
    }

    .welcome {
        align-items: flex-start;
        flex-direction: column;
    }

    .button {
        white-space: nowrap;
    }
}
</style>
"""


LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <title>Customer Login - {{ shop_name }}</title>

    {{ css|safe }}
</head>

<body>
    <header>
        <div>
            <h1>{{ shop_name }}</h1>
            <p>{{ shop_address }}</p>
        </div>
    </header>

    <main>
        <section class="login-box">
            <h2>Customer Portal</h2>

            <p class="muted">
                Login to view all bills registered with your
                phone number.
            </p>

            {% with messages = get_flashed_messages(with_categories=true) %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }}">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endwith %}

            <form method="post">
                <label for="username">Username</label>

                <input
                    id="username"
                    name="username"
                    type="text"
                    autocomplete="username"
                    required
                >

                <label for="password">Password</label>

                <input
                    id="password"
                    name="password"
                    type="password"
                    autocomplete="current-password"
                    required
                >

                <button type="submit">
                    Login
                </button>
            </form>

            <p class="muted">
                Contact the finance office if you need your
                portal login details.
            </p>
        </section>
    </main>

    <footer>
        {{ shop_name }} - Customer Portal
    </footer>
</body>
</html>
"""


DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <title>My Loans - {{ shop_name }}</title>

    {{ css|safe }}
</head>

<body>
    <header>
        <div>
            <h1>{{ shop_name }}</h1>
            <p>{{ shop_address }}</p>
        </div>

        <a
            class="logout"
            href="{{ url_for('portal_logout') }}"
        >
            Logout
        </a>
    </header>

    <main>
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% for category, message in messages %}
                <div class="alert alert-{{ category }}">
                    {{ message }}
                </div>
            {% endfor %}
        {% endwith %}

        <section class="welcome">
            <div>
                <h2>Welcome, {{ customer_name }}</h2>

                <p class="muted">
                    Phone:
                    <strong>{{ phone }}</strong>
                </p>

                <p class="muted">
                    Bills registered:
                    <strong>{{ bills|length }}</strong>
                </p>
            </div>
        </section>

        <section class="summary">
            <article class="summary-card balance">
                <span>Total outstanding</span>

                <strong>
                    ₹ {{ "{:,.2f}".format(total_balance) }}
                </strong>
            </article>
        </section>

        <section class="card">
            <h3>Your Bills and Loans</h3>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Bill Number</th>
                            <th>Date</th>
                            <th>Loan</th>
                            <th>Paid</th>
                            <th>Balance</th>
                            <th>Status</th>
                            <th>Receipt</th>
                        </tr>
                    </thead>

                    <tbody>
                        {% for bill in bills %}
                            <tr>
                                <td>
                                    <strong>{{ bill["bill"] }}</strong>
                                </td>

                                <td>{{ bill["date"] }}</td>

                                <td>
                                    ₹ {{ "{:,.2f}".format(bill["loan"]) }}
                                </td>

                                <td>
                                    ₹ {{ "{:,.2f}".format(bill["paid"]) }}
                                </td>

                                <td>
                                    ₹ {{ "{:,.2f}".format(bill["balance"]) }}
                                </td>

                                <td>
                                    <span
                                        class="status status-{{ bill['status']|lower }}"
                                    >
                                        {{ bill["status"] }}
                                    </span>
                                </td>

                                <td>
                                    <a
                                        class="button"
                                        href="{{ url_for(
                                            'portal_receipt',
                                            bill_id=bill['id']
                                        ) }}"
                                    >
                                        PDF
                                    </a>
                                </td>
                            </tr>

                            <tr>
                                <td colspan="7">
                                    <details class="loan-details">
                                        <summary>
                                            View details for
                                            {{ bill["bill"] }}
                                        </summary>

                                        <div class="details-grid">
                                            <div class="detail">
                                                <span>Name</span>
                                                <strong>
                                                    {{ bill["name"] or "Customer" }}
                                                </strong>
                                            </div>

                                            <div class="detail">
                                                <span>Phone</span>
                                                <strong>
                                                    {{ bill["phone"] or "-" }}
                                                </strong>
                                            </div>

                                            <div class="detail">
                                                <span>Address</span>
                                                <strong>
                                                    {{ bill["address"] or "-" }}
                                                </strong>
                                            </div>

                                            <div class="detail">
                                                <span>Metal</span>
                                                <strong>
                                                    {{ bill["metal"] or "-" }}
                                                </strong>
                                            </div>

                                            <div class="detail">
                                                <span>Weight</span>
                                                <strong>
                                                    {{ bill["weight"] or "-" }}
                                                </strong>
                                            </div>

                                            <div class="detail">
                                                <span>Details</span>
                                                <strong>
                                                    {{ bill["details"] or "-" }}
                                                </strong>
                                            </div>
                                        </div>
                                    </details>
                                </td>
                            </tr>
                        {% else %}
                            <tr>
                                <td colspan="7">
                                    No bills were found for this phone number.
                                </td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </section>

        <section class="card">
            <h3>Payment History - All Bills</h3>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Bill Number</th>
                            <th>Payment Date</th>
                            <th>Amount</th>
                        </tr>
                    </thead>

                    <tbody>
                        {% for payment in payments %}
                            <tr>
                                <td>{{ payment["bill"] }}</td>
                                <td>{{ payment["date"] }}</td>

                                <td>
                                    ₹ {{ "{:,.2f}".format(payment["amount"]) }}
                                </td>
                            </tr>
                        {% else %}
                            <tr>
                                <td colspan="3">
                                    No payments have been recorded.
                                </td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </section>

        <section class="notice">
            <strong>Important:</strong>
            Payment must be made at least once every three months
            for each open bill.
        </section>
    </main>

    <footer>
        Contact the finance office for assistance.
    </footer>
</body>
</html>
"""


@web_app.route("/")
def portal_home():
    if session.get("customer_phone"):
        return redirect(
            url_for("portal_dashboard")
        )

    return redirect(
        url_for("portal_login")
    )


@web_app.route(
    "/login",
    methods=["GET", "POST"]
)
def portal_login():
    if session.get("customer_phone"):
        return redirect(
            url_for("portal_dashboard")
        )

    if request.method == "POST":
        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        if not username or not password:
            flash(
                "Enter your username and password.",
                "danger"
            )

        else:
            database = get_web_database()

            try:
                account = database.execute("""
                    SELECT
                        phone,
                        username,
                        password_hash,
                        active
                    FROM customer_accounts
                    WHERE username = ?
                """, (username,)).fetchone()

            finally:
                database.close()

            valid_login = (
                account is not None
                and int(account["active"]) == 1
                and check_password_hash(
                    account["password_hash"],
                    password
                )
            )

            if valid_login:
                session.clear()

                session["customer_phone"] = normalize_phone(
                    account["phone"]
                )

                session["portal_username"] = account[
                    "username"
                ]

                return redirect(
                    url_for("portal_dashboard")
                )

            flash(
                "Invalid username or password.",
                "danger"
            )

    return render_template_string(
        LOGIN_TEMPLATE,
        css=PORTAL_CSS,
        shop_name=SHOP_NAME,
        shop_address=SHOP_ADDRESS
    )


@web_app.route("/dashboard")
def portal_dashboard():
    phone = normalize_phone(
        session.get("customer_phone")
    )

    if not phone:
        flash(
            "Please log in to continue.",
            "warning"
        )

        return redirect(
            url_for("portal_login")
        )

    database = get_web_database()

    try:
        bills = database.execute("""
            SELECT
                c.id,
                c.bill,
                c.name,
                c.phone,
                c.address,
                c.loan,
                c.metal,
                c.weight,
                c.details,
                c.date,
                c.status,
                COALESCE(SUM(p.amount), 0) AS paid,
                MAX(
                    c.loan - COALESCE(SUM(p.amount), 0),
                    0
                ) AS balance
            FROM customers c
            LEFT JOIN payments p
                ON p.customer_id = c.id
            WHERE NORMALIZE_PHONE(c.phone) = ?
            GROUP BY c.id
            ORDER BY c.id DESC
        """, (phone,)).fetchall()

        payments = database.execute("""
            SELECT
                c.bill,
                p.amount,
                p.date,
                p.id
            FROM payments p
            JOIN customers c
                ON c.id = p.customer_id
            WHERE NORMALIZE_PHONE(c.phone) = ?
            ORDER BY p.id DESC
        """, (phone,)).fetchall()

    finally:
        database.close()

    customer_name = "Customer"

    for bill in bills:
        if bill["name"] and bill["name"].strip():
            customer_name = bill["name"].strip()
            break

    total_balance = sum(
        max(float(bill["balance"] or 0), 0)
        for bill in bills
    )

    gold_rate = get_live_gold_rate()
    gold_rate_updated = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

    return render_template_string(
        DASHBOARD_TEMPLATE,
        css=PORTAL_CSS,
        shop_name=SHOP_NAME,
        shop_address=SHOP_ADDRESS,
        customer_name=customer_name,
        phone=phone,
        bills=bills,
        payments=payments,
        total_balance=total_balance,
        gold_rate=gold_rate,
        gold_rate_updated=gold_rate_updated
    )


@web_app.route("/receipt")
def portal_receipt():
    phone = normalize_phone(
        session.get("customer_phone")
    )

    if not phone:
        return redirect(
            url_for("portal_login")
        )

    bill_id = request.args.get(
        "bill_id",
        type=int
    )

    if not bill_id:
        flash(
            "Bill number was not specified.",
            "danger"
        )

        return redirect(
            url_for("portal_dashboard")
        )

    database = get_web_database()

    try:
        bill = database.execute("""
            SELECT
                id,
                bill,
                phone
            FROM customers
            WHERE id = ?
        """, (bill_id,)).fetchone()

    finally:
        database.close()

    if bill is None:
        abort(404)

    if normalize_phone(bill["phone"]) != phone:
        abort(403)

    try:
        filepath = generate_receipt_pdf(
            bill_id
        )

        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath)
        )

    except Exception as error:
        flash(
            f"Could not generate receipt: {error}",
            "danger"
        )

        return redirect(
            url_for("portal_dashboard")
        )


@web_app.route("/logout")
def portal_logout():
    session.clear()

    flash(
        "You have been logged out.",
        "success"
    )

    return redirect(
        url_for("portal_login")
    )


@web_app.route("/health")
def portal_health():
    return {
        "status": "online",
        "shop": SHOP_NAME,
        "database_found": os.path.isfile(DB_PATH)
    }


# ============================================================
# DESKTOP APPLICATION
# ============================================================

class App:
    def __init__(self, root):
        self.root = root

        self.root.title(SHOP_NAME)
        self.root.geometry("1280x750")
        self.root.minsize(1050, 650)

        self.role = None
        self.current_user = None
        self.web_server = None

        auto_backup()
        self.start_website()
        self.login()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close_application
        )

    # --------------------------------------------------------
    # GENERAL HELPERS
    # --------------------------------------------------------

    def clear_root(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def start_website(self):
        if self.web_server is not None:
            return

        try:
            self.web_server = create_server(
                web_app,
                host=WEB_HOST,
                port=WEB_PORT,
                threads=6
            )

            web_thread = threading.Thread(
                target=self.web_server.run,
                daemon=True
            )

            web_thread.start()

            print(
                f"Portal running at "
                f"http://127.0.0.1:{WEB_PORT}"
            )

        except Exception as error:
            self.web_server = None
            print("Website startup error:", error)

    def get_local_ip(self):
        try:
            network_socket = socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM
            )

            network_socket.connect(
                ("8.8.8.8", 80)
            )

            local_ip = network_socket.getsockname()[0]

            network_socket.close()

            return local_ip

        except Exception:
            return "127.0.0.1"

    # --------------------------------------------------------
    # LOGIN
    # --------------------------------------------------------

    def login(self):
        self.clear_root()

        self.role = None
        self.current_user = None

        frame = ttk.Frame(
            self.root,
            padding=40
        )

        frame.place(
            relx=0.5,
            rely=0.5,
            anchor="center"
        )

        if os.path.exists(LOGO_PATH):
            try:
                image = PILImage.open(
                    LOGO_PATH
                ).resize((160, 85))

                self.login_logo = ImageTk.PhotoImage(
                    image,
                    master=self.root
                )

                ttk.Label(
                    frame,
                    image=self.login_logo
                ).pack(pady=10)

            except Exception:
                pass

        ttk.Label(
            frame,
            text=SHOP_NAME,
            font=("Arial", 20, "bold"),
            bootstyle="warning"
        ).pack(pady=15)

        ttk.Label(
            frame,
            text="Username"
        ).pack(anchor="w")

        self.username_entry = ttk.Entry(
            frame,
            width=35
        )

        self.username_entry.pack(
            pady=(5, 12)
        )

        ttk.Label(
            frame,
            text="Password"
        ).pack(anchor="w")

        self.password_entry = ttk.Entry(
            frame,
            width=35,
            show="*"
        )

        self.password_entry.pack(
            pady=(5, 12)
        )

        self.username_entry.bind(
            "<Return>",
            lambda event: self.password_entry.focus()
        )

        self.password_entry.bind(
            "<Return>",
            lambda event: self.check_login()
        )

        ttk.Button(
            frame,
            text="Login",
            width=25,
            bootstyle="success",
            command=self.check_login
        ).pack(pady=15)

        ttk.Label(
            frame,
            text=(
                f"Customer portal: "
                f"http://127.0.0.1:{WEB_PORT}"
            ),
            bootstyle="secondary"
        ).pack(pady=5)

        self.username_entry.focus()

    def check_login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        cursor.execute("""
            SELECT role
            FROM users
            WHERE username = ?
              AND password = ?
            ORDER BY id
            LIMIT 1
        """, (
            username,
            password
        ))

        result = cursor.fetchone()

        if result:
            self.role = result[0]
            self.current_user = username
            self.dashboard()

        else:
            messagebox.showerror(
                "Login Error",
                "Invalid username or password."
            )

    # --------------------------------------------------------
    # DASHBOARD
    # --------------------------------------------------------

    def dashboard(self):
        self.clear_root()

        sidebar = ttk.Frame(
            self.root,
            padding=12,
            bootstyle="dark"
        )

        sidebar.pack(
            side="left",
            fill="y"
        )

        content = ttk.Frame(
            self.root,
            padding=25
        )

        content.pack(
            side="right",
            expand=True,
            fill="both"
        )

        ttk.Label(
            sidebar,
            text=SHOP_NAME,
            font=("Arial", 11, "bold"),
            bootstyle="warning",
            wraplength=175
        ).pack(pady=8)

        ttk.Label(
            sidebar,
            text=(
                f"User: {self.current_user}\n"
                f"Role: {self.role}"
            ),
            justify="center"
        ).pack(pady=5)

        ttk.Separator(
            sidebar
        ).pack(fill="x", pady=8)

        sidebar_buttons = [
            (
                "Dashboard",
                "warning-outline",
                self.dashboard
            ),
            (
                "Add Customer / Bill",
                "success-outline",
                self.add_customer
            ),
            (
                "View Customers",
                "primary-outline",
                self.view_customers
            ),
            (
                "Add Payment",
                "info-outline",
                self.add_payment
            ),
            (
                "Export Excel",
                "success-outline",
                self.export_excel
            ),
            (
                "Manual Backup",
                "secondary-outline",
                self.manual_backup
            ),
            (
                "Customer Website",
                "info-outline",
                self.open_website
            ),
            (
                "Website Information",
                "light-outline",
                self.website_information
            )
        ]

        for text, style, command in sidebar_buttons:
            ttk.Button(
                sidebar,
                text=text,
                bootstyle=style,
                command=command
            ).pack(fill="x", pady=3)

        if self.role == "admin":
            ttk.Separator(
                sidebar
            ).pack(fill="x", pady=8)

            ttk.Button(
                sidebar,
                text="Create Staff",
                bootstyle="secondary-outline",
                command=self.create_staff
            ).pack(fill="x", pady=3)

        ttk.Button(
            sidebar,
            text="Change Password",
            bootstyle="danger-outline",
            command=self.change_password
        ).pack(fill="x", pady=3)

        ttk.Separator(
            sidebar
        ).pack(fill="x", pady=8)

        ttk.Button(
            sidebar,
            text="Logout",
            bootstyle="danger",
            command=self.login
        ).pack(fill="x", pady=3)

        cursor.execute("""
            SELECT COUNT(DISTINCT customer_key)
            FROM (
                SELECT
                    CASE
                        WHEN NORMALIZE_PHONE(phone) <> ''
                        THEN NORMALIZE_PHONE(phone)
                        ELSE 'CUSTOMER-' || id
                    END AS customer_key
                FROM customers
            )
        """)

        total_customers = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COUNT(*)
            FROM customers
        """)

        total_bills = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COUNT(*)
            FROM customers
            WHERE status = 'Open'
        """)

        open_bills = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COUNT(*)
            FROM customers
            WHERE status = 'Closed'
        """)

        closed_bills = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM payments
        """)

        total_paid = float(
            cursor.fetchone()[0] or 0
        )

        cursor.execute("""
            SELECT COALESCE(SUM(balance), 0)
            FROM (
                SELECT
                    MAX(
                        c.loan - COALESCE(SUM(p.amount), 0),
                        0
                    ) AS balance
                FROM customers c
                LEFT JOIN payments p
                    ON p.customer_id = c.id
                GROUP BY c.id
            )
        """)

        total_balance = float(
            cursor.fetchone()[0] or 0
        )

        ttk.Label(
            content,
            text=f"Welcome, {self.current_user}",
            font=("Arial", 22, "bold"),
            bootstyle="warning"
        ).pack(anchor="w", pady=(5, 20))

        cards_frame = ttk.Frame(content)
        cards_frame.pack(fill="x")

        statistics = [
            (
                "Open Bills",
                open_bills,
                "warning"
            ),
            (
                "Closed Bills",
                closed_bills,
                "success"
            )
        ]

        for index, (
            title,
            value,
            style
        ) in enumerate(statistics):

            card = ttk.LabelFrame(
                cards_frame,
                text=title,
                bootstyle=style,
                padding=18
            )

            card.grid(
                row=index // 3,
                column=index % 3,
                padx=10,
                pady=10,
                sticky="nsew"
            )

            ttk.Label(
                card,
                text=str(value),
                font=("Arial", 20, "bold"),
                bootstyle=style
            ).pack()

        for column in range(3):
            cards_frame.columnconfigure(
                column,
                weight=1
            )

        website_frame = ttk.LabelFrame(
            content,
            text="Customer Website",
            padding=18,
            bootstyle="info"
        )

        website_frame.pack(
            fill="x",
            pady=20
        )

        ttk.Label(
            website_frame,
            text=(
                f"Local URL: "
                f"http://127.0.0.1:{WEB_PORT}\n"
                f"Office Wi-Fi URL: "
                f"http://{self.get_local_ip()}:{WEB_PORT}"
            ),
            font=("Arial", 11)
        ).pack(anchor="w")

    # --------------------------------------------------------
    # ADD CUSTOMER / NEW BILL
    # --------------------------------------------------------

    def add_customer(
        self,
        prefill_phone="",
        prefill_name="",
        prefill_address=""
    ):
        window = ttk.Toplevel(self.root)
        window.title("Add Customer / New Bill")
        window.geometry("500x720")
        window.resizable(False, False)
        window.transient(self.root)

        ttk.Label(
            window,
            text="Add Customer / New Bill",
            font=("Arial", 17, "bold"),
            bootstyle="warning"
        ).pack(pady=(12, 4))

        ttk.Label(
            window,
            text=(
                "Use the same phone number to link "
                "multiple bills to one customer."
            ),
            bootstyle="secondary",
            wraplength=440
        ).pack(pady=(0, 6))

        # ---- SAVE BUTTON PINNED AT BOTTOM (always visible) ----
        save_frame = ttk.Frame(window, padding=(15, 12))
        save_frame.pack(side="bottom", fill="x")

        # ---- SCROLLABLE FORM ----
        canvas = tk.Canvas(
            window,
            borderwidth=0,
            highlightthickness=0
        )

        vsb = ttk.Scrollbar(
            window,
            orient="vertical",
            command=canvas.yview
        )

        form = ttk.Frame(canvas, padding=15)

        form.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )

        canvas_window = canvas.create_window(
            (0, 0),
            window=form,
            anchor="nw"
        )

        canvas.configure(yscrollcommand=vsb.set)

        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # keep form width equal to canvas width
        def _resize_form(event):
            canvas.itemconfig(
                canvas_window,
                width=event.width
            )
        canvas.bind("<Configure>", _resize_form)

        # mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(
                int(-1 * (event.delta / 120)),
                "units"
            )

        def _bind_wheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        # ---- FIELDS ----
        ttk.Label(form, text="Phone Number *").pack(anchor="w")
        phone_entry = ttk.Entry(form)
        phone_entry.pack(fill="x", pady=(4, 6))
        phone_entry.insert(0, prefill_phone)

        find_result = tk.StringVar()
        ttk.Label(
            form,
            textvariable=find_result,
            bootstyle="info",
            wraplength=430
        ).pack(anchor="w", pady=(0, 6))

        ttk.Label(form, text="Name (Optional)").pack(anchor="w")
        name_entry = ttk.Entry(form)
        name_entry.pack(fill="x", pady=(4, 8))
        name_entry.insert(0, prefill_name)

        ttk.Label(form, text="Address").pack(anchor="w")
        address_entry = ttk.Entry(form)
        address_entry.pack(fill="x", pady=(4, 8))
        address_entry.insert(0, prefill_address)

        ttk.Label(form, text="Loan Amount *").pack(anchor="w")
        loan_entry = ttk.Entry(form)
        loan_entry.pack(fill="x", pady=(4, 8))

        ttk.Label(form, text="Metal").pack(anchor="w")
        metal_entry = ttk.Entry(form)
        metal_entry.pack(fill="x", pady=(4, 8))

        ttk.Label(form, text="Weight").pack(anchor="w")
        weight_entry = ttk.Entry(form)
        weight_entry.pack(fill="x", pady=(4, 8))

        ttk.Label(form, text="Details").pack(anchor="w")
        details_entry = ttk.Entry(form)
        details_entry.pack(fill="x", pady=(4, 8))

        # ---- FIND EXISTING CUSTOMER ----
        def find_existing_customer():
            phone = normalize_phone(phone_entry.get())

            if not phone:
                find_result.set("")
                return None

            cursor.execute("""
                SELECT name, address, COUNT(*) OVER()
                FROM customers
                WHERE NORMALIZE_PHONE(phone) = ?
                ORDER BY id DESC
                LIMIT 1
            """, (phone,))

            existing = cursor.fetchone()

            if existing:
                find_result.set(
                    f"Existing customer found. "
                    f"{existing[2]} bill(s) already linked."
                )

                if not name_entry.get().strip() and existing[0]:
                    name_entry.insert(0, existing[0])

                if not address_entry.get().strip() and existing[1]:
                    address_entry.insert(0, existing[1])

                return existing

            find_result.set("New customer phone number.")
            return None

        ttk.Button(
            form,
            text="Find Existing Customer",
            bootstyle="info-outline",
            command=find_existing_customer
        ).pack(anchor="w", pady=(0, 8))

        phone_entry.bind(
            "<FocusOut>",
            lambda event: find_existing_customer()
        )

        # ---- SAVE ----
        def save_customer():
            phone = normalize_phone(phone_entry.get())

            if len(phone) < 7:
                messagebox.showerror(
                    "Validation Error",
                    "Enter a valid phone number.",
                    parent=window
                )
                return

            existing = find_existing_customer()

            name = name_entry.get().strip()
            address = address_entry.get().strip()

            if not name and existing and existing[0]:
                name = existing[0]

            if not address and existing and existing[1]:
                address = existing[1]

            if not name:
                name = "Customer"

            try:
                loan = float(loan_entry.get().strip())
                if loan <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Validation Error",
                    "Enter a valid loan amount.",
                    parent=window
                )
                return

            bill = "SSF" + datetime.now().strftime("%Y%m%d%H%M%S%f")

            try:
                cursor.execute("""
                    INSERT INTO customers(
                        bill, name, phone, address, loan,
                        metal, weight, details, date, status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    bill, name, phone, address, loan,
                    metal_entry.get().strip(),
                    weight_entry.get().strip(),
                    details_entry.get().strip(),
                    datetime.now().strftime("%d-%m-%Y"),
                    "Open"
                ))

                conn.commit()
                update_excel()

                cursor.execute("""
                    SELECT COUNT(*)
                    FROM customers
                    WHERE NORMALIZE_PHONE(phone) = ?
                """, (phone,))

                bill_count = cursor.fetchone()[0]

                messagebox.showinfo(
                    "Bill Created",
                    f"Bill created successfully.\n\n"
                    f"Bill number: {bill}\n"
                    f"Phone: {phone}\n"
                    f"Total linked bills: {bill_count}",
                    parent=window
                )

                window.destroy()
                self.dashboard()

            except Exception as error:
                conn.rollback()
                messagebox.showerror(
                    "Database Error",
                    str(error),
                    parent=window
                )

        ttk.Button(
            save_frame,
            text="Save Customer / Bill",
            bootstyle="success",
            command=save_customer
        ).pack(fill="x", ipady=7)

        phone_entry.focus()

    # --------------------------------------------------------
    # VIEW CUSTOMERS
    # --------------------------------------------------------

    def view_customers(self):
        window = ttk.Toplevel(self.root)
        window.title("View Customers and Bills")
        window.geometry("1180x720")
        window.transient(self.root)

        search_frame = ttk.Frame(
            window,
            padding=10
        )

        search_frame.pack(fill="x")

        ttk.Label(
            search_frame,
            text="Search:"
        ).pack(side="left", padx=4)

        search_variable = tk.StringVar()

        search_entry = ttk.Entry(
            search_frame,
            textvariable=search_variable,
            width=32
        )

        search_entry.pack(
            side="left",
            padx=4
        )

        ttk.Label(
            search_frame,
            text="Status:"
        ).pack(side="left", padx=(12, 4))

        status_variable = tk.StringVar(
            value="All"
        )

        status_combobox = ttk.Combobox(
            search_frame,
            textvariable=status_variable,
            values=[
                "All",
                "Open",
                "Closed"
            ],
            state="readonly",
            width=10
        )

        status_combobox.pack(
            side="left",
            padx=4
        )

        columns = (
            "Bill",
            "Name",
            "Phone",
            "Loan",
            "Paid",
            "Balance",
            "Status"
        )

        tree_frame = ttk.Frame(window)
        tree_frame.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=5
        )

        tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            bootstyle="warning"
        )

        vertical_scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=tree.yview
        )

        horizontal_scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="horizontal",
            command=tree.xview
        )

        tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set
        )

        tree.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        vertical_scrollbar.grid(
            row=0,
            column=1,
            sticky="ns"
        )

        horizontal_scrollbar.grid(
            row=1,
            column=0,
            sticky="ew"
        )

        tree_frame.rowconfigure(
            0,
            weight=1
        )

        tree_frame.columnconfigure(
            0,
            weight=1
        )

        column_widths = {
            "Bill": 210,
            "Name": 170,
            "Phone": 125,
            "Loan": 110,
            "Paid": 110,
            "Balance": 110,
            "Status": 90
        }

        sort_state = {
            "column": None,
            "reverse": False
        }

        def sort_tree(column):
            rows = [
                (
                    tree.set(item, column),
                    item
                )
                for item in tree.get_children("")
            ]

            reverse = (
                sort_state["column"] == column
                and not sort_state["reverse"]
            )

            sort_state["column"] = column
            sort_state["reverse"] = reverse

            try:
                rows.sort(
                    key=lambda item: float(
                        item[0]
                        .replace("₹", "")
                        .replace(",", "")
                        .strip()
                    ),
                    reverse=reverse
                )
            except ValueError:
                rows.sort(
                    key=lambda item: item[0].lower(),
                    reverse=reverse
                )

            for index, (_, item_id) in enumerate(rows):
                tree.move(
                    item_id,
                    "",
                    index
                )

        for column in columns:
            tree.heading(
                column,
                text=column,
                command=lambda selected_column=column: sort_tree(
                    selected_column
                )
            )

            tree.column(
                column,
                width=column_widths[column],
                anchor="center"
            )

        def load_customers():
            for item in tree.get_children():
                tree.delete(item)

            search_text = (
                "%"
                + search_variable.get().strip()
                + "%"
            )

            sql = """
                SELECT
                    c.id,
                    c.bill,
                    c.name,
                    c.phone,
                    c.loan,
                    COALESCE(SUM(p.amount), 0) AS paid,
                    MAX(
                        c.loan - COALESCE(SUM(p.amount), 0),
                        0
                    ) AS balance,
                    c.status
                FROM customers c
                LEFT JOIN payments p
                    ON p.customer_id = c.id
                WHERE (
                    c.bill LIKE ?
                    OR c.name LIKE ?
                    OR c.phone LIKE ?
                    OR c.address LIKE ?
                    OR c.metal LIKE ?
                    OR c.details LIKE ?
                )
            """

            parameters = [
                search_text,
                search_text,
                search_text,
                search_text,
                search_text,
                search_text
            ]

            if status_variable.get() != "All":
                sql += """
                    AND c.status = ?
                """

                parameters.append(
                    status_variable.get()
                )

            sql += """
                GROUP BY c.id
                ORDER BY c.id DESC
            """

            cursor.execute(
                sql,
                parameters
            )

            for customer in cursor.fetchall():
                tree.insert(
                    "",
                    "end",
                    iid=str(customer[0]),
                    values=(
                        customer[1],
                        customer[2] or "Customer",
                        customer[3] or "",
                        f"₹ {float(customer[4]):,.2f}",
                        f"₹ {float(customer[5]):,.2f}",
                        f"₹ {float(customer[6]):,.2f}",
                        customer[7]
                    )
                )

        search_variable.trace_add(
            "write",
            lambda *_: load_customers()
        )

        status_variable.trace_add(
            "write",
            lambda *_: load_customers()
        )

        ttk.Button(
            search_frame,
            text="Search",
            bootstyle="primary",
            command=load_customers
        ).pack(side="left", padx=4)

        def show_all():
            search_variable.set("")
            status_variable.set("All")
            load_customers()

        ttk.Button(
            search_frame,
            text="Show All",
            bootstyle="secondary",
            command=show_all
        ).pack(side="left", padx=4)

        # ----------------------------------------------------
        # INLINE PAYMENT PANEL
        # ----------------------------------------------------

        payment_frame = ttk.LabelFrame(
            window,
            text="Add Payment for Selected Bill",
            padding=10,
            bootstyle="info"
        )

        payment_frame.pack(
            fill="x",
            padx=10,
            pady=(0, 5)
        )

        selected_bill_variable = tk.StringVar(
            value="-"
        )

        selected_name_variable = tk.StringVar(
            value="-"
        )

        selected_balance_variable = tk.StringVar(
            value="-"
        )

        payment_amount_variable = tk.StringVar()

        ttk.Label(
            payment_frame,
            text="Bill:"
        ).grid(
            row=0,
            column=0,
            padx=5,
            pady=5,
            sticky="w"
        )

        ttk.Label(
            payment_frame,
            textvariable=selected_bill_variable,
            bootstyle="warning"
        ).grid(
            row=0,
            column=1,
            padx=5,
            pady=5,
            sticky="w"
        )

        ttk.Label(
            payment_frame,
            text="Customer:"
        ).grid(
            row=0,
            column=2,
            padx=5,
            pady=5,
            sticky="w"
        )

        ttk.Label(
            payment_frame,
            textvariable=selected_name_variable
        ).grid(
            row=0,
            column=3,
            padx=5,
            pady=5,
            sticky="w"
        )

        ttk.Label(
            payment_frame,
            text="Balance:"
        ).grid(
            row=0,
            column=4,
            padx=5,
            pady=5,
            sticky="w"
        )

        ttk.Label(
            payment_frame,
            textvariable=selected_balance_variable,
            bootstyle="danger"
        ).grid(
            row=0,
            column=5,
            padx=5,
            pady=5,
            sticky="w"
        )

        ttk.Label(
            payment_frame,
            text="Payment Amount:"
        ).grid(
            row=1,
            column=0,
            padx=5,
            pady=5,
            sticky="w"
        )

        payment_amount_entry = ttk.Entry(
            payment_frame,
            textvariable=payment_amount_variable,
            width=20
        )

        payment_amount_entry.grid(
            row=1,
            column=1,
            padx=5,
            pady=5,
            sticky="w"
        )

        def get_selected_customer_id(
            show_warning=True
        ):
            selection = tree.selection()

            if not selection:
                if show_warning:
                    messagebox.showwarning(
                        "Select Bill",
                        "Select a bill first.",
                        parent=window
                    )

                return None

            return int(selection[0])

        def update_selected_bill(
            event=None
        ):
            customer_id = get_selected_customer_id(
                show_warning=False
            )

            if customer_id is None:
                selected_bill_variable.set("-")
                selected_name_variable.set("-")
                selected_balance_variable.set("-")
                return

            summary = get_bill_summary(
                customer_id
            )

            if summary:
                selected_bill_variable.set(
                    summary["bill"]
                )

                selected_name_variable.set(
                    summary["name"]
                )

                selected_balance_variable.set(
                    f"₹ {summary['balance']:,.2f}"
                )

                payment_amount_variable.set("")
                payment_amount_entry.focus()

        tree.bind(
            "<<TreeviewSelect>>",
            update_selected_bill
        )

        def add_selected_payment():
            customer_id = get_selected_customer_id()

            if customer_id is None:
                return

            try:
                amount = float(
                    payment_amount_variable.get().strip()
                )

            except ValueError:
                messagebox.showerror(
                    "Payment Error",
                    "Enter a valid payment amount.",
                    parent=window
                )
                return

            try:
                result = record_payment(
                    customer_id,
                    amount
                )

                messagebox.showinfo(
                    "Payment Added",
                    f"Customer: {result['customer_name']}\n"
                    f"Bill: {result['bill']}\n"
                    f"Paid: ₹ {result['amount']:,.2f}\n"
                    f"Balance: ₹ {result['balance']:,.2f}",
                    parent=window
                )

                payment_amount_variable.set("")
                load_customers()

                if str(customer_id) in tree.get_children():
                    tree.selection_set(
                        str(customer_id)
                    )

                    update_selected_bill()

            except ValueError as error:
                messagebox.showerror(
                    "Payment Error",
                    str(error),
                    parent=window
                )

            except Exception as error:
                conn.rollback()

                messagebox.showerror(
                    "Database Error",
                    str(error),
                    parent=window
                )

        ttk.Button(
            payment_frame,
            text="Add Payment",
            bootstyle="success",
            command=add_selected_payment
        ).grid(
            row=1,
            column=2,
            padx=5,
            pady=5,
            sticky="w"
        )

        payment_amount_entry.bind(
            "<Return>",
            lambda event: add_selected_payment()
        )

        # ----------------------------------------------------
        # ACTION BUTTONS
        # ----------------------------------------------------

        button_frame = ttk.Frame(
            window,
            padding=10
        )

        button_frame.pack(fill="x")

        def view_details():
            customer_id = get_selected_customer_id()

            if customer_id:
                self.customer_details(
                    customer_id
                )

        def generate_selected_receipt():
            customer_id = get_selected_customer_id()

            if customer_id is None:
                return

            try:
                filepath = generate_receipt_pdf(
                    customer_id
                )

                open_local_file(filepath)

            except Exception as error:
                messagebox.showerror(
                    "Receipt Error",
                    str(error),
                    parent=window
                )

        def portal_account():
            customer_id = get_selected_customer_id()

            if customer_id:
                self.create_customer_portal(
                    customer_id
                )

        def new_bill_same_customer():
            customer_id = get_selected_customer_id()

            if customer_id is None:
                return

            cursor.execute("""
                SELECT
                    phone,
                    name,
                    address
                FROM customers
                WHERE id = ?
            """, (customer_id,))

            customer = cursor.fetchone()

            if customer:
                self.add_customer(
                    prefill_phone=customer[0] or "",
                    prefill_name=customer[1] or "",
                    prefill_address=customer[2] or ""
                )

        def close_bill():
            customer_id = get_selected_customer_id()

            if customer_id is None:
                return

            if messagebox.askyesno(
                "Close Bill",
                "Close the selected bill?",
                parent=window
            ):
                cursor.execute("""
                    UPDATE customers
                    SET status = 'Closed'
                    WHERE id = ?
                """, (customer_id,))

                conn.commit()
                update_excel()
                load_customers()

        def delete_bill():
            customer_id = get_selected_customer_id()

            if customer_id is None:
                return

            if self.role != "admin":
                messagebox.showerror(
                    "Permission Denied",
                    "Only an administrator can delete bills.",
                    parent=window
                )
                return

            if not messagebox.askyesno(
                "Delete Bill",
                "Delete this bill and all its payments?",
                parent=window
            ):
                return

            cursor.execute("""
                SELECT phone
                FROM customers
                WHERE id = ?
            """, (customer_id,))

            phone_row = cursor.fetchone()

            phone = normalize_phone(
                phone_row[0] if phone_row else ""
            )

            cursor.execute("""
                DELETE FROM payments
                WHERE customer_id = ?
            """, (customer_id,))

            cursor.execute("""
                DELETE FROM customers
                WHERE id = ?
            """, (customer_id,))

            if phone:
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM customers
                    WHERE NORMALIZE_PHONE(phone) = ?
                """, (phone,))

                remaining_bills = cursor.fetchone()[0]

                if remaining_bills == 0:
                    cursor.execute("""
                        DELETE FROM customer_accounts
                        WHERE phone = ?
                    """, (phone,))

            conn.commit()
            update_excel()
            load_customers()

        action_buttons = [
            (
                "View Details",
                "info",
                view_details
            ),
            (
                "PDF Receipt",
                "warning",
                generate_selected_receipt
            ),
            (
                "Portal Account",
                "secondary",
                portal_account
            ),
            (
                "New Bill - Same Customer",
                "primary",
                new_bill_same_customer
            ),
            (
                "Close Bill",
                "success",
                close_bill
            )
        ]

        for text, style, command in action_buttons:
            ttk.Button(
                button_frame,
                text=text,
                bootstyle=style,
                command=command
            ).pack(side="left", padx=4)

        if self.role == "admin":
            ttk.Button(
                button_frame,
                text="Delete Bill",
                bootstyle="danger",
                command=delete_bill
            ).pack(side="left", padx=4)

        tree.bind(
            "<Double-1>",
            lambda event: view_details()
        )

        load_customers()
        search_entry.focus()

    # --------------------------------------------------------
    # CUSTOMER DETAILS
    # --------------------------------------------------------

    def customer_details(self, customer_id):
        cursor.execute("""
            SELECT *
            FROM customers
            WHERE id = ?
        """, (customer_id,))

        customer = cursor.fetchone()

        if customer is None:
            messagebox.showerror(
                "Error",
                "Customer was not found."
            )
            return

        summary = get_bill_summary(
            customer_id
        )

        window = ttk.Toplevel(self.root)
        window.title("Customer Bill Details")
        window.geometry("700x680")
        window.transient(self.root)

        ttk.Label(
            window,
            text="Customer Bill Details",
            font=("Arial", 17, "bold"),
            bootstyle="warning"
        ).pack(pady=15)

        details_frame = ttk.Frame(
            window,
            padding=15
        )

        details_frame.pack(fill="x")

        details = [
            ("Bill Number", customer[1]),
            ("Name", customer[2] or "Customer"),
            ("Phone", customer[3] or "-"),
            ("Address", customer[4] or "-"),
            ("Loan", f"₹ {summary['loan']:,.2f}"),
            ("Paid", f"₹ {summary['paid']:,.2f}"),
            ("Balance", f"₹ {summary['balance']:,.2f}"),
            ("Metal", customer[6] or "-"),
            ("Weight", customer[7] or "-"),
            ("Details", customer[8] or "-"),
            ("Date", customer[9]),
            ("Status", customer[10])
        ]

        for row_number, (
            label,
            value
        ) in enumerate(details):

            ttk.Label(
                details_frame,
                text=f"{label}:",
                font=("Arial", 10, "bold")
            ).grid(
                row=row_number,
                column=0,
                sticky="w",
                pady=3
            )

            ttk.Label(
                details_frame,
                text=str(value),
                wraplength=450
            ).grid(
                row=row_number,
                column=1,
                sticky="w",
                padx=18,
                pady=3
            )

        ttk.Label(
            window,
            text="Payment History",
            font=("Arial", 13, "bold"),
            bootstyle="warning"
        ).pack(pady=(10, 4))

        payment_tree = ttk.Treeview(
            window,
            columns=(
                "Number",
                "Amount",
                "Date"
            ),
            show="headings",
            height=8
        )

        payment_tree.heading(
            "Number",
            text="Number"
        )

        payment_tree.heading(
            "Amount",
            text="Amount"
        )

        payment_tree.heading(
            "Date",
            text="Date"
        )

        payment_tree.column(
            "Number",
            width=80,
            anchor="center"
        )

        payment_tree.column(
            "Amount",
            width=180,
            anchor="center"
        )

        payment_tree.column(
            "Date",
            width=180,
            anchor="center"
        )

        payment_tree.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=10
        )

        cursor.execute("""
            SELECT
                id,
                amount,
                date
            FROM payments
            WHERE customer_id = ?
            ORDER BY id DESC
        """, (customer_id,))

        for number, payment in enumerate(
            cursor.fetchall(),
            start=1
        ):
            payment_tree.insert(
                "",
                "end",
                values=(
                    number,
                    f"₹ {float(payment[1]):,.2f}",
                    payment[2]
                )
            )

    # --------------------------------------------------------
    # STANDALONE ADD PAYMENT
    # --------------------------------------------------------

    def add_payment(self):
        window = ttk.Toplevel(self.root)
        window.title("Add Payment")
        window.geometry("460x400")
        window.resizable(False, False)
        window.transient(self.root)

        ttk.Label(
            window,
            text="Add Customer Payment",
            font=("Arial", 17, "bold"),
            bootstyle="warning"
        ).pack(pady=20)

        form = ttk.Frame(
            window,
            padding=15
        )

        form.pack(fill="x")

        ttk.Label(
            form,
            text="Bill Number"
        ).pack(anchor="w")

        bill_entry = ttk.Entry(form)
        bill_entry.pack(
            fill="x",
            pady=(5, 10)
        )

        bill_information = tk.StringVar()

        ttk.Label(
            form,
            textvariable=bill_information,
            bootstyle="info",
            wraplength=420
        ).pack(anchor="w", pady=(0, 10))

        ttk.Label(
            form,
            text="Payment Amount"
        ).pack(anchor="w")

        amount_entry = ttk.Entry(form)
        amount_entry.pack(
            fill="x",
            pady=(5, 12)
        )

        def find_bill():
            bill = bill_entry.get().strip()

            cursor.execute("""
                SELECT id
                FROM customers
                WHERE bill = ?
            """, (bill,))

            result = cursor.fetchone()

            if result is None:
                bill_information.set(
                    "Bill not found."
                )
                return None

            summary = get_bill_summary(
                result[0]
            )

            bill_information.set(
                f"Customer: {summary['name']} | "
                f"Phone: {summary['phone']} | "
                f"Balance: ₹ {summary['balance']:,.2f}"
            )

            return result[0]

        bill_entry.bind(
            "<FocusOut>",
            lambda event: find_bill()
        )

        bill_entry.bind(
            "<Return>",
            lambda event: (
                find_bill(),
                amount_entry.focus()
            )
        )

        def save_payment():
            customer_id = find_bill()

            if customer_id is None:
                messagebox.showerror(
                    "Payment Error",
                    "Bill was not found.",
                    parent=window
                )
                return

            try:
                amount = float(
                    amount_entry.get().strip()
                )

            except ValueError:
                messagebox.showerror(
                    "Payment Error",
                    "Enter a valid payment amount.",
                    parent=window
                )
                return

            try:
                result = record_payment(
                    customer_id,
                    amount
                )

                messagebox.showinfo(
                    "Payment Added",
                    f"Customer: {result['customer_name']}\n"
                    f"Bill: {result['bill']}\n"
                    f"Paid: ₹ {result['amount']:,.2f}\n"
                    f"Balance: ₹ {result['balance']:,.2f}",
                    parent=window
                )

                window.destroy()
                self.dashboard()

            except ValueError as error:
                messagebox.showerror(
                    "Payment Error",
                    str(error),
                    parent=window
                )

            except Exception as error:
                conn.rollback()

                messagebox.showerror(
                    "Database Error",
                    str(error),
                    parent=window
                )

        amount_entry.bind(
            "<Return>",
            lambda event: save_payment()
        )

        ttk.Button(
            window,
            text="Save Payment",
            width=25,
            bootstyle="success",
            command=save_payment
        ).pack(pady=20)

        bill_entry.focus()

    # --------------------------------------------------------
    # CUSTOMER PORTAL ACCOUNT
    # --------------------------------------------------------

    def create_customer_portal(self, customer_id):
        cursor.execute("""
            SELECT
                bill,
                name,
                phone
            FROM customers
            WHERE id = ?
        """, (customer_id,))

        customer = cursor.fetchone()

        if customer is None:
            messagebox.showerror(
                "Error",
                "Customer was not found."
            )
            return

        phone = normalize_phone(
            customer[2]
        )

        if len(phone) < 7:
            messagebox.showerror(
                "Portal Error",
                "This customer does not have a valid phone number."
            )
            return

        cursor.execute("""
            SELECT
                username,
                active
            FROM customer_accounts
            WHERE phone = ?
        """, (phone,))

        existing_account = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*)
            FROM customers
            WHERE NORMALIZE_PHONE(phone) = ?
        """, (phone,))

        bill_count = cursor.fetchone()[0]

        window = ttk.Toplevel(self.root)
        window.title("Customer Portal Account")
        window.geometry("500x540")
        window.resizable(False, False)
        window.transient(self.root)

        ttk.Label(
            window,
            text="Customer Portal Account",
            font=("Arial", 17, "bold"),
            bootstyle="warning"
        ).pack(pady=(15, 4))

        ttk.Label(
            window,
            text=customer[1] or "Customer",
            font=("Arial", 11, "bold")
        ).pack()

        ttk.Label(
            window,
            text=(
                f"Phone: {phone}\n"
                f"Linked bills: {bill_count}"
            ),
            justify="center",
            bootstyle="secondary"
        ).pack(pady=(3, 8))

        ttk.Label(
            window,
            text=(
                "This portal account will show every bill "
                "registered with this phone number."
            ),
            wraplength=440,
            justify="center",
            bootstyle="info"
        ).pack(pady=(0, 8))

        ttk.Separator(
            window
        ).pack(fill="x", padx=15)

        # Pack the button frame at the bottom before the form.
        # This guarantees the Save button is always visible.
        button_frame = ttk.Frame(
            window,
            padding=(15, 12)
        )

        button_frame.pack(
            side="bottom",
            fill="x"
        )

        form = ttk.Frame(
            window,
            padding=(20, 10)
        )

        form.pack(
            fill="both",
            expand=True
        )

        ttk.Label(
            form,
            text="Portal Username"
        ).pack(anchor="w", pady=(5, 2))

        username_variable = tk.StringVar(
            value=(
                existing_account[0]
                if existing_account
                else phone
            )
        )

        username_entry = ttk.Entry(
            form,
            textvariable=username_variable
        )

        username_entry.pack(
            fill="x",
            pady=(0, 12)
        )

        ttk.Label(
            form,
            text=(
                "New Password"
                if existing_account
                else "Password"
            )
        ).pack(anchor="w", pady=(5, 2))

        password_variable = tk.StringVar()

        password_entry = ttk.Entry(
            form,
            textvariable=password_variable,
            show="*"
        )

        password_entry.pack(
            fill="x",
            pady=(0, 12)
        )

        ttk.Label(
            form,
            text="Confirm Password"
        ).pack(anchor="w", pady=(5, 2))

        confirm_variable = tk.StringVar()

        confirm_entry = ttk.Entry(
            form,
            textvariable=confirm_variable,
            show="*"
        )

        confirm_entry.pack(
            fill="x",
            pady=(0, 10)
        )

        show_password_variable = tk.IntVar(
            value=0
        )

        def toggle_password_visibility():
            show_character = (
                ""
                if show_password_variable.get()
                else "*"
            )

            password_entry.configure(
                show=show_character
            )

            confirm_entry.configure(
                show=show_character
            )

        ttk.Checkbutton(
            form,
            text="Show Password",
            variable=show_password_variable,
            command=toggle_password_visibility,
            bootstyle="info"
        ).pack(anchor="w", pady=4)

        active_variable = tk.IntVar(
            value=(
                int(existing_account[1])
                if existing_account
                else 1
            )
        )

        ttk.Checkbutton(
            form,
            text="Portal account is active",
            variable=active_variable,
            bootstyle="success-round-toggle"
        ).pack(anchor="w", pady=8)

        if existing_account:
            ttk.Label(
                form,
                text=(
                    "Leave both password fields empty to keep "
                    "the current password."
                ),
                bootstyle="secondary",
                wraplength=430
            ).pack(anchor="w", pady=4)

        status_variable = tk.StringVar()

        ttk.Label(
            form,
            textvariable=status_variable,
            bootstyle="danger",
            wraplength=430
        ).pack(anchor="w", pady=5)

        def save_portal_account():
            username = username_variable.get().strip()
            password = password_variable.get()
            confirm_password = confirm_variable.get()

            status_variable.set("")

            if not username:
                status_variable.set(
                    "Enter a portal username."
                )
                return

            if existing_account:
                if password or confirm_password:
                    if len(password) < 8:
                        status_variable.set(
                            "Password must contain at least 8 characters."
                        )
                        return

                    if password != confirm_password:
                        status_variable.set(
                            "Passwords do not match."
                        )
                        return
            else:
                if not password or not confirm_password:
                    status_variable.set(
                        "Enter a password and confirm it."
                    )
                    return

                if len(password) < 8:
                    status_variable.set(
                        "Password must contain at least 8 characters."
                    )
                    return

                if password != confirm_password:
                    status_variable.set(
                        "Passwords do not match."
                    )
                    return

            try:
                if existing_account:
                    if password:
                        cursor.execute("""
                            UPDATE customer_accounts
                            SET
                                username = ?,
                                password_hash = ?,
                                active = ?
                            WHERE phone = ?
                        """, (
                            username,
                            generate_password_hash(
                                password
                            ),
                            active_variable.get(),
                            phone
                        ))

                    else:
                        cursor.execute("""
                            UPDATE customer_accounts
                            SET
                                username = ?,
                                active = ?
                            WHERE phone = ?
                        """, (
                            username,
                            active_variable.get(),
                            phone
                        ))

                else:
                    cursor.execute("""
                        INSERT INTO customer_accounts(
                            phone,
                            username,
                            password_hash,
                            active,
                            created_at
                        )
                        VALUES(?, ?, ?, ?, ?)
                    """, (
                        phone,
                        username,
                        generate_password_hash(
                            password
                        ),
                        active_variable.get(),
                        datetime.now().strftime(
                            "%d-%m-%Y %H:%M:%S"
                        )
                    ))

                conn.commit()

                messagebox.showinfo(
                    "Portal Account Saved",
                    f"Portal account saved successfully.\n\n"
                    f"Phone: {phone}\n"
                    f"Linked bills: {bill_count}\n"
                    f"Username: {username}\n\n"
                    f"Website:\n"
                    f"http://127.0.0.1:{WEB_PORT}",
                    parent=window
                )

                window.destroy()

            except sqlite3.IntegrityError:
                conn.rollback()

                status_variable.set(
                    "This username is already being used. "
                    "Choose another username."
                )

            except Exception as error:
                conn.rollback()

                status_variable.set(
                    f"Database error: {error}"
                )

        button_text = (
            "Update Portal Account"
            if existing_account
            else "Create Portal Account"
        )

        ttk.Button(
            button_frame,
            text=button_text,
            bootstyle="success",
            command=save_portal_account
        ).pack(
            fill="x",
            ipady=7
        )

        password_entry.bind(
            "<Return>",
            lambda event: confirm_entry.focus()
        )

        confirm_entry.bind(
            "<Return>",
            lambda event: save_portal_account()
        )

        username_entry.focus()

    # --------------------------------------------------------
    # CREATE STAFF
    # --------------------------------------------------------

    def create_staff(self):
        if self.role != "admin":
            messagebox.showerror(
                "Permission Denied",
                "Only an administrator can create staff."
            )
            return

        window = ttk.Toplevel(self.root)
        window.title("Create Staff")
        window.geometry("400x330")
        window.resizable(False, False)
        window.transient(self.root)

        ttk.Label(
            window,
            text="Create Staff Account",
            font=("Arial", 17, "bold"),
            bootstyle="warning"
        ).pack(pady=20)

        form = ttk.Frame(
            window,
            padding=15
        )

        form.pack(fill="x")

        ttk.Label(
            form,
            text="Username"
        ).pack(anchor="w")

        username_entry = ttk.Entry(form)
        username_entry.pack(
            fill="x",
            pady=(5, 12)
        )

        ttk.Label(
            form,
            text="Password"
        ).pack(anchor="w")

        password_entry = ttk.Entry(
            form,
            show="*"
        )

        password_entry.pack(
            fill="x",
            pady=(5, 12)
        )

        def save_staff():
            username = username_entry.get().strip()
            password = password_entry.get()

            if not username or not password:
                messagebox.showerror(
                    "Validation Error",
                    "Enter username and password.",
                    parent=window
                )
                return

            cursor.execute("""
                SELECT id
                FROM users
                WHERE username = ?
            """, (username,))

            if cursor.fetchone():
                messagebox.showerror(
                    "Username Error",
                    "This username already exists.",
                    parent=window
                )
                return

            cursor.execute("""
                INSERT INTO users(
                    username,
                    password,
                    role
                )
                VALUES(?, ?, ?)
            """, (
                username,
                password,
                "staff"
            ))

            conn.commit()

            messagebox.showinfo(
                "Staff Created",
                "Staff account created successfully.",
                parent=window
            )

            window.destroy()

        password_entry.bind(
            "<Return>",
            lambda event: save_staff()
        )

        ttk.Button(
            window,
            text="Create Staff",
            width=24,
            bootstyle="success",
            command=save_staff
        ).pack(pady=20)

        username_entry.focus()

    # --------------------------------------------------------
    # CHANGE PASSWORD
    # --------------------------------------------------------

    def change_password(self):
        window = ttk.Toplevel(self.root)
        window.title("Change Password")
        window.geometry("400x380")
        window.resizable(False, False)
        window.transient(self.root)

        ttk.Label(
            window,
            text="Change Password",
            font=("Arial", 17, "bold"),
            bootstyle="danger"
        ).pack(pady=20)

        form = ttk.Frame(
            window,
            padding=15
        )

        form.pack(fill="x")

        ttk.Label(
            form,
            text="Current Password"
        ).pack(anchor="w")

        current_entry = ttk.Entry(
            form,
            show="*"
        )

        current_entry.pack(
            fill="x",
            pady=(5, 12)
        )

        ttk.Label(
            form,
            text="New Password"
        ).pack(anchor="w")

        new_entry = ttk.Entry(
            form,
            show="*"
        )

        new_entry.pack(
            fill="x",
            pady=(5, 12)
        )

        ttk.Label(
            form,
            text="Confirm New Password"
        ).pack(anchor="w")

        confirm_entry = ttk.Entry(
            form,
            show="*"
        )

        confirm_entry.pack(
            fill="x",
            pady=(5, 12)
        )

        def save_password():
            cursor.execute("""
                SELECT id
                FROM users
                WHERE username = ?
                  AND password = ?
                LIMIT 1
            """, (
                self.current_user,
                current_entry.get()
            ))

            if cursor.fetchone() is None:
                messagebox.showerror(
                    "Password Error",
                    "Current password is incorrect.",
                    parent=window
                )
                return

            if len(new_entry.get()) < 4:
                messagebox.showerror(
                    "Password Error",
                    "New password must contain at least 4 characters.",
                    parent=window
                )
                return

            if new_entry.get() != confirm_entry.get():
                messagebox.showerror(
                    "Password Error",
                    "New passwords do not match.",
                    parent=window
                )
                return

            cursor.execute("""
                UPDATE users
                SET password = ?
                WHERE username = ?
            """, (
                new_entry.get(),
                self.current_user
            ))

            conn.commit()

            messagebox.showinfo(
                "Password Changed",
                "Password changed successfully.",
                parent=window
            )

            window.destroy()

        confirm_entry.bind(
            "<Return>",
            lambda event: save_password()
        )

        ttk.Button(
            window,
            text="Change Password",
            width=24,
            bootstyle="danger",
            command=save_password
        ).pack(pady=15)

        current_entry.focus()

    # --------------------------------------------------------
    # EXPORT, BACKUP AND WEBSITE
    # --------------------------------------------------------

    def export_excel(self):
        update_excel()

        if os.path.isfile(EXCEL_FILE):
            messagebox.showinfo(
                "Excel Export",
                f"Excel file created successfully:\n\n"
                f"{EXCEL_FILE}"
            )

            open_local_file(
                EXCEL_FILE
            )

        else:
            messagebox.showerror(
                "Export Error",
                "Could not create the Excel file."
            )

    def manual_backup(self):
        try:
            filename = (
                "manual_"
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
                + ".db"
            )

            destination = create_backup(
                filename
            )

            messagebox.showinfo(
                "Backup Created",
                f"Backup created successfully:\n\n"
                f"{destination}"
            )

        except Exception as error:
            messagebox.showerror(
                "Backup Error",
                str(error)
            )

    def open_website(self):
        self.start_website()

        webbrowser.open_new_tab(
            f"http://127.0.0.1:{WEB_PORT}"
        )

    def website_information(self):
        status = (
            "Running"
            if self.web_server
            else "Not running"
        )

        messagebox.showinfo(
            "Website Information",
            f"Status: {status}\n\n"
            f"On this computer:\n"
            f"http://127.0.0.1:{WEB_PORT}\n\n"
            f"On the same Wi-Fi:\n"
            f"http://{self.get_local_ip()}:{WEB_PORT}\n\n"
            f"For public internet access, use HTTPS hosting "
            f"or a secure Cloudflare Tunnel."
        )

    # --------------------------------------------------------
    # CLOSE APPLICATION
    # --------------------------------------------------------

    def close_application(self):
        try:
            conn.commit()
            conn.close()
        except Exception:
            pass

        try:
            if self.web_server is not None:
                self.web_server.close()
        except Exception:
            pass

        self.root.destroy()


# ============================================================
# SINGLE-ROOT SPLASH SCREEN
# ============================================================

def show_splash_and_start():
    """
    Uses one ttk.Window only.

    The old error happened because one ttk.Window was destroyed
    after the splash and another ttk.Window was created.

    Here, the main root stays alive. A normal Tk Toplevel is used
    temporarily as the splash window.
    """

    root = ttk.Window(
        themename="darkly"
    )

    root.withdraw()

    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    splash.configure(bg="black")

    width = 500
    height = 300

    screen_width = splash.winfo_screenwidth()
    screen_height = splash.winfo_screenheight()

    x_position = int(
        (screen_width - width) / 2
    )

    y_position = int(
        (screen_height - height) / 2
    )

    splash.geometry(
        f"{width}x{height}"
        f"+{x_position}"
        f"+{y_position}"
    )

    if os.path.exists(LOGO_PATH):
        try:
            logo_image = PILImage.open(
                LOGO_PATH
            ).resize((220, 120))

            splash.logo_photo = ImageTk.PhotoImage(
                logo_image,
                master=splash
            )

            logo_label = tk.Label(
                splash,
                image=splash.logo_photo,
                bg="black",
                borderwidth=0
            )

            logo_label.pack(pady=(20, 10))

        except Exception:
            pass

    shop_label = tk.Label(
        splash,
        text=SHOP_NAME,
        font=("Arial", 20, "bold"),
        fg="#ffc107",
        bg="black"
    )

    shop_label.pack(pady=10)

    loading_label = tk.Label(
        splash,
        text="Loading...",
        font=("Arial", 10),
        fg="white",
        bg="black"
    )

    loading_label.pack(pady=5)

    def start_main_application():
        try:
            if splash.winfo_exists():
                splash.destroy()
        except Exception:
            pass

        root.application = App(root)
        root.deiconify()
        root.lift()
        root.focus_force()

    root.after(
        1800,
        start_main_application
    )

    root.mainloop()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    show_splash_and_start()