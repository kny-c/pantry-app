import sqlite3

# This creates (or opens, if it already exists) a file called pantry.db
connection = sqlite3.connect("pantry.db")

# Run the CREATE TABLE statement from schema.sql
with open("schema.sql") as f:
    connection.executescript(f.read())

# Insert our sample items -- same data we had hardcoded before,
# but now it's actually going into the database file.
sample_items = [
    ("Eggs", 12, "count", "2026-07-10"),
    ("Milk", 1, "gallon", "2026-07-08"),
    ("Flour", 500, "g", "2026-09-01"),
]

connection.executemany(
    "INSERT INTO items (name, quantity, unit, expiration_date) VALUES (?, ?, ?, ?)",
    sample_items
)

connection.commit()
connection.close()

print("Database created and seeded. See pantry.db")
