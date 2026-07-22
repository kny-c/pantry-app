import sqlite3

connection = sqlite3.connect("pantry.db")
connection.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
)
""")
connection.commit()
connection.close()
print("users table created (or already existed)")