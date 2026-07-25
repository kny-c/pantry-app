import sqlite3

connection = sqlite3.connect("pantry.db")

connection.execute("ALTER TABLE items ADD COLUMN user_id INTEGER")
connection.execute("ALTER TABLE recipes ADD COLUMN user_id INTEGER")

user = connection.execute(
    "SELECT id FROM users WHERE username = ?", ("knycao",)
).fetchone()

if user is None:
    print("No user found with that username -- check the spelling and try again.")
else:
    user_id = user[0]

    connection.execute("UPDATE items SET user_id = ? WHERE user_id IS NULL", (user_id,))
    connection.execute("UPDATE recipes SET user_id = ? WHERE user_id IS NULL", (user_id,))
    connection.commit()

    print(f"Done. All existing items and recipes assigned to user_id {user_id}.")

connection.close()