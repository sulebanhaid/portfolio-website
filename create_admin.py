"""Create an administrator account for Bristol Community Events.

Usage:
    python create_admin.py

You will be asked for a name, email and password. The password is hashed
with Werkzeug before it is stored, in the same way as normal registrations.
"""

from getpass import getpass

from werkzeug.security import generate_password_hash

from app import get_db_connection


def main():
    full_name = input("Full name: ").strip()
    email = input("Email: ").strip().lower()
    password = getpass("Password (min 6 characters): ")

    if not full_name or not email or len(password) < 6:
        print("Name and email are required, and the password needs 6+ characters.")
        return

    connection = get_db_connection()
    if connection is None:
        print("Could not connect to the database. Is MySQL running?")
        return

    cursor = connection.cursor()
    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))

    if cursor.fetchone():
        cursor.execute(
            "UPDATE users SET role = 'admin' WHERE email = %s",
            (email,)
        )
        print(f"Existing account {email} is now an administrator.")
    else:
        cursor.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role)
            VALUES (%s, %s, %s, 'admin')
            """,
            (full_name, email, generate_password_hash(password))
        )
        print(f"Administrator account created for {email}.")

    connection.commit()
    cursor.close()
    connection.close()


if __name__ == "__main__":
    main()
