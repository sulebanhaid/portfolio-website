from functools import wraps
from datetime import date
import os
import uuid

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort
)
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error


app = Flask(__name__)

# Configuration is read from environment variables so that no secrets are
# stored in the source code. The defaults match a local XAMPP setup.
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)


def get_db_connection():
    try:
        return mysql.connector.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            user=os.environ.get("DB_USER", "root"),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "bce_resit")
        )
    except Error as error:
        print(f"Database connection error: {error}")
        return None


def admin_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in.")
            return redirect(url_for("login"))

        if session.get("role") != "admin":
            abort(403)

        return view_function(*args, **kwargs)

    return wrapped_view


@app.route("/")
def home():
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            e.event_id,
            e.title,
            e.description,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            c.category_name
        FROM events e
        JOIN categories c
            ON e.category_id = c.category_id
        WHERE e.status = 'active'
        ORDER BY e.event_date, e.start_time
    """)

    events = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template("index.html", events=events)


@app.route("/event/<int:event_id>")
def event_details(event_id):
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            e.event_id,
            e.title,
            e.description,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            e.status,
            c.category_name,
            COALESCE(
                SUM(
                    CASE
                        WHEN b.booking_status = 'confirmed'
                        THEN b.quantity
                        ELSE 0
                    END
                ),
                0
            ) AS tickets_sold
        FROM events e
        JOIN categories c
            ON e.category_id = c.category_id
        LEFT JOIN bookings b
            ON e.event_id = b.event_id
        WHERE e.event_id = %s
        GROUP BY
            e.event_id,
            e.title,
            e.description,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            e.status,
            c.category_name
    """, (event_id,))

    event = cursor.fetchone()

    cursor.close()
    connection.close()

    if event is None:
        return "Event not found.", 404

    event["tickets_remaining"] = (
        int(event["capacity"]) - int(event["tickets_sold"])
    )

    return render_template("event_details.html", event=event)


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not full_name or not email or not password:
            flash("All fields are required.")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.")
            return render_template("register.html")

        connection = get_db_connection()

        if connection is None:
            return "Database connection failed.", 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT user_id FROM users WHERE email = %s",
            (email,)
        )

        existing_user = cursor.fetchone()

        if existing_user:
            cursor.close()
            connection.close()

            flash("An account already exists with that email.")
            return render_template("register.html")

        password_hash = generate_password_hash(password)

        cursor.execute("""
            INSERT INTO users (
                full_name,
                email,
                password_hash,
                role
            )
            VALUES (%s, %s, %s, 'user')
        """, (
            full_name,
            email,
            password_hash
        ))

        connection.commit()

        cursor.close()
        connection.close()

        flash("Registration successful. Please log in.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.")
            return render_template("login.html")

        connection = get_db_connection()

        if connection is None:
            return "Database connection failed.", 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                user_id,
                full_name,
                email,
                password_hash,
                role
            FROM users
            WHERE email = %s
        """, (email,))

        user = cursor.fetchone()

        cursor.close()
        connection.close()

        if user and check_password_hash(
            user["password_hash"],
            password
        ):
            session.clear()
            session["user_id"] = user["user_id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]

            flash("Login successful.")
            return redirect(url_for("home"))

        flash("Invalid email or password.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("home"))


@app.route("/book/<int:event_id>", methods=["GET", "POST"])
def book_event(event_id):
    if "user_id" not in session:
        flash("Please log in before booking tickets.")
        return redirect(url_for("login"))

    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            e.event_id,
            e.title,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            e.status,
            COALESCE(
                SUM(
                    CASE
                        WHEN b.booking_status = 'confirmed'
                        THEN b.quantity
                        ELSE 0
                    END
                ),
                0
            ) AS tickets_sold
        FROM events e
        LEFT JOIN bookings b
            ON e.event_id = b.event_id
        WHERE e.event_id = %s
        GROUP BY
            e.event_id,
            e.title,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            e.status
    """, (event_id,))

    event = cursor.fetchone()

    if event is None:
        cursor.close()
        connection.close()
        return "Event not found.", 404

    if event["status"] != "active":
        cursor.close()
        connection.close()
        flash("This event is not available.")
        return redirect(url_for("event_details", event_id=event_id))

    tickets_remaining = (
        int(event["capacity"]) - int(event["tickets_sold"])
    )

    if request.method == "POST":
        try:
            quantity = int(request.form.get("quantity", "0"))
        except ValueError:
            quantity = 0

        attendee_name = request.form.get(
            "attendee_name",
            ""
        ).strip()

        attendee_email = request.form.get(
            "attendee_email",
            ""
        ).strip().lower()

        if quantity < 1:
            flash("Please select at least one ticket.")

            cursor.close()
            connection.close()

            return render_template(
                "booking.html",
                event=event,
                tickets_remaining=tickets_remaining
            )

        if quantity > tickets_remaining:
            flash("There are not enough tickets remaining.")

            cursor.close()
            connection.close()

            return render_template(
                "booking.html",
                event=event,
                tickets_remaining=tickets_remaining
            )

        if not attendee_name or not attendee_email:
            flash("Attendee name and email are required.")

            cursor.close()
            connection.close()

            return render_template(
                "booking.html",
                event=event,
                tickets_remaining=tickets_remaining
            )

        unit_price = event["ticket_price"]
        discount_amount = 0
        total_amount = unit_price * quantity

        booking_reference = (
            "BCE-" + uuid.uuid4().hex[:8].upper()
        )

        transaction_reference = (
            "PAY-" + uuid.uuid4().hex[:10].upper()
        )

        try:
            cursor.execute("""
                INSERT INTO bookings (
                    booking_reference,
                    user_id,
                    event_id,
                    quantity,
                    unit_price,
                    discount_amount,
                    total_amount,
                    booking_status
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'confirmed'
                )
            """, (
                booking_reference,
                session["user_id"],
                event_id,
                quantity,
                unit_price,
                discount_amount,
                total_amount
            ))

            booking_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO attendees (
                    booking_id,
                    full_name,
                    email
                )
                VALUES (%s, %s, %s)
            """, (
                booking_id,
                attendee_name,
                attendee_email
            ))

            cursor.execute("""
                INSERT INTO payments (
                    booking_id,
                    amount,
                    payment_method,
                    payment_status,
                    transaction_reference
                )
                VALUES (
                    %s,
                    %s,
                    'Test card',
                    'paid',
                    %s
                )
            """, (
                booking_id,
                total_amount,
                transaction_reference
            ))

            connection.commit()

        except Error as error:
            connection.rollback()

            cursor.close()
            connection.close()

            print(f"Booking error: {error}")
            return "The booking could not be completed.", 500

        cursor.close()
        connection.close()

        return redirect(
            url_for(
                "booking_confirmation",
                booking_id=booking_id
            )
        )

    cursor.close()
    connection.close()

    return render_template(
        "booking.html",
        event=event,
        tickets_remaining=tickets_remaining
    )


@app.route("/booking/<int:booking_id>/confirmation")
def booking_confirmation(booking_id):
    if "user_id" not in session:
        flash("Please log in.")
        return redirect(url_for("login"))

    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            b.booking_id,
            b.booking_reference,
            b.quantity,
            b.unit_price,
            b.discount_amount,
            b.total_amount,
            b.booking_status,
            b.booking_date,
            e.title,
            e.event_date,
            e.start_time,
            e.venue,
            p.payment_status,
            p.transaction_reference,
            p.payment_method,
            a.full_name AS attendee_name,
            a.email AS attendee_email
        FROM bookings b
        JOIN events e
            ON b.event_id = e.event_id
        LEFT JOIN payments p
            ON b.booking_id = p.booking_id
        LEFT JOIN attendees a
            ON b.booking_id = a.booking_id
        WHERE b.booking_id = %s
          AND b.user_id = %s
    """, (
        booking_id,
        session["user_id"]
    ))

    booking = cursor.fetchone()

    cursor.close()
    connection.close()

    if booking is None:
        return "Booking not found.", 404

    return render_template(
        "booking_confirmation.html",
        booking=booking
    )


@app.route("/my-bookings")
def my_bookings():
    if "user_id" not in session:
        flash("Please log in to view your bookings.")
        return redirect(url_for("login"))

    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            b.booking_id,
            b.booking_reference,
            b.quantity,
            b.total_amount,
            b.booking_status,
            b.booking_date,
            e.title,
            e.event_date,
            e.start_time,
            e.venue,
            p.payment_status
        FROM bookings b
        JOIN events e
            ON b.event_id = e.event_id
        LEFT JOIN payments p
            ON b.booking_id = p.booking_id
        WHERE b.user_id = %s
        ORDER BY b.booking_date DESC
    """, (session["user_id"],))

    bookings = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "my_bookings.html",
        bookings=bookings
    )


@app.route("/admin")
@admin_required
def admin_dashboard():
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            COUNT(*) AS total_events
        FROM events
    """)
    event_summary = cursor.fetchone()

    cursor.execute("""
        SELECT
            COUNT(*) AS total_bookings,
            COALESCE(SUM(quantity), 0) AS tickets_sold,
            COALESCE(SUM(total_amount), 0) AS total_revenue
        FROM bookings
        WHERE booking_status = 'confirmed'
    """)
    booking_summary = cursor.fetchone()

    cursor.execute("""
        SELECT
            e.event_id,
            e.title,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            e.status,
            c.category_name,
            COALESCE(
                SUM(
                    CASE
                        WHEN b.booking_status = 'confirmed'
                        THEN b.quantity
                        ELSE 0
                    END
                ),
                0
            ) AS tickets_sold,
            COALESCE(
                SUM(
                    CASE
                        WHEN b.booking_status = 'confirmed'
                        THEN b.total_amount
                        ELSE 0
                    END
                ),
                0
            ) AS revenue
        FROM events e
        JOIN categories c
            ON e.category_id = c.category_id
        LEFT JOIN bookings b
            ON e.event_id = b.event_id
        GROUP BY
            e.event_id,
            e.title,
            e.event_date,
            e.start_time,
            e.venue,
            e.capacity,
            e.ticket_price,
            e.status,
            c.category_name
        ORDER BY e.event_date, e.start_time
    """)

    events = cursor.fetchall()

    cursor.close()
    connection.close()

    for event in events:
        event["tickets_remaining"] = (
            int(event["capacity"]) - int(event["tickets_sold"])
        )

    return render_template(
        "admin_dashboard.html",
        event_summary=event_summary,
        booking_summary=booking_summary,
        events=events
    )


@app.route("/admin/events/create", methods=["GET", "POST"])
@admin_required
def admin_create_event():
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT category_id, category_name
        FROM categories
        ORDER BY category_name
    """)
    categories = cursor.fetchall()

    if request.method == "POST":
        category_id = request.form.get("category_id", "")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        event_date = request.form.get("event_date", "")
        start_time = request.form.get("start_time", "")
        venue = request.form.get("venue", "").strip()
        capacity = request.form.get("capacity", "")
        ticket_price = request.form.get("ticket_price", "")

        if not all([
            category_id,
            title,
            description,
            event_date,
            start_time,
            venue,
            capacity,
            ticket_price
        ]):
            flash("All event fields are required.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=None,
                page_title="Create Event"
            )

        try:
            capacity = int(capacity)
            ticket_price = float(ticket_price)
        except ValueError:
            flash("Capacity and price must contain valid numbers.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=None,
                page_title="Create Event"
            )

        if capacity < 1 or ticket_price < 0:
            flash("Capacity must be at least 1 and price cannot be negative.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=None,
                page_title="Create Event"
            )

        if event_date < str(date.today()):
            flash("The event date cannot be in the past.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=None,
                page_title="Create Event"
            )

        cursor.execute("""
            INSERT INTO events (
                category_id,
                title,
                description,
                event_date,
                start_time,
                venue,
                capacity,
                ticket_price,
                status
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'active'
            )
        """, (
            category_id,
            title,
            description,
            event_date,
            start_time,
            venue,
            capacity,
            ticket_price
        ))

        connection.commit()

        cursor.close()
        connection.close()

        flash("Event created successfully.")
        return redirect(url_for("admin_dashboard"))

    cursor.close()
    connection.close()

    return render_template(
        "admin_event_form.html",
        categories=categories,
        event=None,
        page_title="Create Event"
    )


@app.route("/admin/events/<int:event_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_event(event_id):
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT category_id, category_name
        FROM categories
        ORDER BY category_name
    """)
    categories = cursor.fetchall()

    cursor.execute("""
        SELECT *
        FROM events
        WHERE event_id = %s
    """, (event_id,))
    event = cursor.fetchone()

    if event is None:
        cursor.close()
        connection.close()
        return "Event not found.", 404

    if request.method == "POST":
        category_id = request.form.get("category_id", "")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        event_date = request.form.get("event_date", "")
        start_time = request.form.get("start_time", "")
        venue = request.form.get("venue", "").strip()
        capacity = request.form.get("capacity", "")
        ticket_price = request.form.get("ticket_price", "")
        status = request.form.get("status", "active")

        try:
            capacity = int(capacity)
            ticket_price = float(ticket_price)
        except ValueError:
            flash("Capacity and price must contain valid numbers.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=event,
                page_title="Edit Event"
            )

        if not all([
            category_id,
            title,
            description,
            event_date,
            start_time,
            venue
        ]):
            flash("All event fields are required.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=event,
                page_title="Edit Event"
            )

        if capacity < 1 or ticket_price < 0:
            flash("Capacity must be at least 1 and price cannot be negative.")

            cursor.close()
            connection.close()

            return render_template(
                "admin_event_form.html",
                categories=categories,
                event=event,
                page_title="Edit Event"
            )

        cursor.execute("""
            UPDATE events
            SET
                category_id = %s,
                title = %s,
                description = %s,
                event_date = %s,
                start_time = %s,
                venue = %s,
                capacity = %s,
                ticket_price = %s,
                status = %s
            WHERE event_id = %s
        """, (
            category_id,
            title,
            description,
            event_date,
            start_time,
            venue,
            capacity,
            ticket_price,
            status,
            event_id
        ))

        connection.commit()

        cursor.close()
        connection.close()

        flash("Event updated successfully.")
        return redirect(url_for("admin_dashboard"))

    cursor.close()
    connection.close()

    return render_template(
        "admin_event_form.html",
        categories=categories,
        event=event,
        page_title="Edit Event"
    )


@app.route("/admin/events/<int:event_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_event(event_id):
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor()

    cursor.execute("""
        UPDATE events
        SET status = 'cancelled'
        WHERE event_id = %s
    """, (event_id,))

    connection.commit()

    cursor.close()
    connection.close()

    flash("Event cancelled successfully.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/events/<int:event_id>/attendees")
@admin_required
def admin_attendees(event_id):
    connection = get_db_connection()

    if connection is None:
        return "Database connection failed.", 500

    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            event_id,
            title,
            event_date,
            start_time,
            venue
        FROM events
        WHERE event_id = %s
    """, (event_id,))
    event = cursor.fetchone()

    if event is None:
        cursor.close()
        connection.close()
        return "Event not found.", 404

    cursor.execute("""
        SELECT
            b.booking_reference,
            b.quantity,
            b.total_amount,
            b.booking_status,
            b.booking_date,
            u.full_name AS customer_name,
            u.email AS customer_email,
            a.full_name AS attendee_name,
            a.email AS attendee_email,
            p.payment_status,
            p.transaction_reference
        FROM bookings b
        JOIN users u
            ON b.user_id = u.user_id
        LEFT JOIN attendees a
            ON b.booking_id = a.booking_id
        LEFT JOIN payments p
            ON b.booking_id = p.booking_id
        WHERE b.event_id = %s
        ORDER BY b.booking_date DESC
    """, (event_id,))

    attendees = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "admin_attendees.html",
        event=event,
        attendees=attendees
    )


@app.errorhandler(403)
def forbidden(error):
    return render_template("403.html"), 403


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")