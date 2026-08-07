CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    expiration_date TEXT,
    user_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS recipes (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id SERIAL PRIMARY KEY,
    recipe_id INTEGER NOT NULL,
    ingredient_name TEXT NOT NULL,
    quantity_needed REAL NOT NULL,
    unit TEXT NOT NULL,
    FOREIGN KEY (recipe_id) REFERENCES recipes(id)
);