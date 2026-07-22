import sqlite3

connection = sqlite3.connect("pantry.db")
connection.execute("""
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL
)
""")
connection.execute("""
CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL,
    ingredient_name TEXT NOT NULL,
    quantity_needed REAL NOT NULL,
    unit TEXT NOT NULL,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
)
""")
connection.commit()
connection.close()
print("recipes tables created")
