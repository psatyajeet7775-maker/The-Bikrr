import calendar
import os
from datetime import date, datetime
from functools import wraps

import pymysql
from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pymysql.cursors import DictCursor


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "BIKRR_SECRET_KEY", "Sandey_1110"
)

DB_CONFIG = {
    "host": os.environ.get("BIKRR_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("BIKRR_DB_PORT", "3306")),
    "user": os.environ.get("BIKRR_DB_USER", "root"),
    "password": os.environ.get("BIKRR_DB_PASSWORD", ""),
    "database": os.environ.get("BIKRR_DB_NAME", "bikrr"),
    "cursorclass": DictCursor,
    "autocommit": False,
}

DATABASE_READY = False


def add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def database_name():
    return DB_CONFIG["database"].replace("`", "``")


def ensure_database_exists():
    global DATABASE_READY
    if DATABASE_READY:
        return

    server_config = DB_CONFIG.copy()
    server_config.pop("database")
    server_config["autocommit"] = True

    connection = pymysql.connect(**server_config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database_name()}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        DATABASE_READY = True
    finally:
        connection.close()


def get_db():
    if "db" not in g:
        ensure_database_exists()
        g.db = pymysql.connect(**DB_CONFIG)
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("SHOW COLUMNS FROM users LIKE 'password'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN password VARCHAR(255) NOT NULL DEFAULT ''"
            )
        cursor.execute("SHOW COLUMNS FROM users LIKE 'password_hash'")
        if cursor.fetchone() is not None:
            cursor.execute("ALTER TABLE users MODIFY password_hash VARCHAR(255) NULL")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bikes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                model_name VARCHAR(160) NOT NULL,
                manufacturing_year INT NOT NULL,
                company_name VARCHAR(160) NOT NULL,
                odo_meter INT NOT NULL DEFAULT 0,
                estimated_mileage DECIMAL(6, 2) NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_bikes_user
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                bike_id INT NOT NULL,
                previous_service_date DATE NOT NULL,
                next_service_date DATE NOT NULL,
                oil_change_start_date DATE NOT NULL,
                oil_change_end_date DATE NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_services_user
                    FOREIGN KEY (user_id) REFERENCES users(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_services_bike
                    FOREIGN KEY (bike_id) REFERENCES bikes(id)
                    ON DELETE CASCADE
            )
            """
        )
    db.commit()


@app.errorhandler(pymysql.err.OperationalError)
def handle_database_error(error):
    return render_template(
        "database_error.html",
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"],
        error=error,
    ), 500


@app.before_request
def load_logged_in_user():
    init_db()
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        with get_db().cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            g.user = cursor.fetchone()


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def get_user_bikes():
    with get_db().cursor() as cursor:
        cursor.execute(
            """
            SELECT b.*,
                   s.previous_service_date,
                   s.next_service_date,
                   s.oil_change_start_date,
                   s.oil_change_end_date
            FROM bikes b
            LEFT JOIN services s ON s.id = (
                SELECT id FROM services
                WHERE bike_id = b.id
                ORDER BY previous_service_date DESC, id DESC
                LIMIT 1
            )
            WHERE b.user_id = %s
            ORDER BY b.created_at DESC
            """,
            (g.user["id"],),
        )
        return cursor.fetchall()


def get_user_bike(bike_id):
    with get_db().cursor() as cursor:
        cursor.execute(
            "SELECT * FROM bikes WHERE id = %s AND user_id = %s",
            (bike_id, g.user["id"]),
        )
        return cursor.fetchone()


@app.route("/")
def home():
    if g.user is None:
        return redirect(url_for("login"))
    return render_template("home.html", bikes=get_user_bikes())


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        with get_db().cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()

        if user is None or user["password"] != password:
            flash("Incorrect email or password.", "danger")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome back.", "success")
            return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not name or not email or not password:
            flash("All fields are required.", "danger")
            return render_template("register.html")

        try:
            with get_db().cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO users (name, email, password)
                    VALUES (%s, %s, %s)
                    """,
                    (name, email, password),
                )
            get_db().commit()
            flash("Account created. You can log in now.", "success")
            return redirect(url_for("login"))
        except pymysql.err.IntegrityError:
            get_db().rollback()
            flash("That email is already registered.", "danger")

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/bikes/new", methods=("GET", "POST"))
@login_required
def add_bike():
    if request.method == "POST":
        model_name = request.form["model_name"].strip()
        manufacturing_year = request.form.get("manufacturing_year", type=int)
        company_name = request.form["company_name"].strip()
        odo_meter = request.form.get("odo_meter", type=int) or 0
        estimated_mileage = request.form.get("estimated_mileage", type=float) or 0

        if not model_name or not manufacturing_year or not company_name:
            flash("Model name, manufacturing year, and company name are required.", "danger")
            return render_template("bike_form.html")

        with get_db().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bikes (
                    user_id, model_name, manufacturing_year, company_name,
                    odo_meter, estimated_mileage
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    g.user["id"],
                    model_name,
                    manufacturing_year,
                    company_name,
                    odo_meter,
                    estimated_mileage,
                ),
            )
        get_db().commit()
        flash("Bike added.", "success")
        return redirect(url_for("home"))

    return render_template("bike_form.html")


@app.route("/services/new", methods=("GET", "POST"))
@login_required
def add_service():
    bikes = get_user_bikes()
    if not bikes:
        flash("Add a bike before adding service details.", "warning")
        return redirect(url_for("add_bike"))

    if request.method == "POST":
        bike_id = request.form.get("bike_id", type=int)
        previous_service_raw = request.form["previous_service_date"]
        notes = request.form.get("notes", "").strip()
        bike = get_user_bike(bike_id)

        if bike is None:
            flash("Choose one of your bikes.", "danger")
            return render_template("service_form.html", bikes=bikes)

        try:
            previous_service_date = datetime.strptime(
                previous_service_raw, "%Y-%m-%d"
            ).date()
        except ValueError:
            flash("Enter a valid previous service date.", "danger")
            return render_template("service_form.html", bikes=bikes)

        next_service_date = add_months(previous_service_date, 6)
        oil_change_start_date = add_months(previous_service_date, 3)
        oil_change_end_date = add_months(previous_service_date, 4)

        with get_db().cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO services (
                    user_id, bike_id, previous_service_date, next_service_date,
                    oil_change_start_date, oil_change_end_date, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    g.user["id"],
                    bike_id,
                    previous_service_date,
                    next_service_date,
                    oil_change_start_date,
                    oil_change_end_date,
                    notes,
                ),
            )
        get_db().commit()
        flash("Service estimate added.", "success")
        return redirect(url_for("home"))

    return render_template("service_form.html", bikes=bikes)


if __name__ == "__main__":
    app.run(debug=True)
