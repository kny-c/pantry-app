import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "dev-secret-change-this-later"

def login_required(original_function):
    @wraps(original_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return original_function(*args, **kwargs)
    return wrapper

# ----------------------------------------------------
def get_all_items():
    # Open a connection to the database file each time we need it.
    connection = sqlite3.connect("pantry.db")

    # By default, sqlite3 returns each row as a plain tuple, like (1, "Eggs", 12.0, ...).
    # Setting row_factory like this makes rows behave like dictionaries instead,
    # so in Jinja we can keep writing item.name instead of item[1].
    connection.row_factory = sqlite3.Row

    cursor = connection.execute("SELECT * FROM items")
    items = cursor.fetchall()

    connection.close()
    return items

def get_all_recipes():
    connection = sqlite3.connect("pantry.db")
    connection.row_factory = sqlite3.Row

    recipes = connection.execute("SELECT * FROM recipes").fetchall()

    # Convert each recipe row into a plain dict so we can attach
    # its ingredient list to it -- sqlite3.Row itself doesn't allow
    # adding new keys, but a regular dict does.
    result = []
    for recipe in recipes:
        recipe_dict = dict(recipe)
        # For each recipe, fetch its ingredients from the recipe_ingredients table by recipe id
        ingredients = connection.execute(
            "SELECT * FROM recipe_ingredients WHERE recipe_id = ?",
            (recipe["id"],)
        ).fetchall() 
        recipe_dict["ingredients"] = ingredients
        recipe_dict["missing"] = check_recipe_availability(ingredients, connection)
        result.append(recipe_dict)

    connection.close()
    return result

def check_recipe_availability(recipe_ingredients, connection):
    """
    For a given recipe's ingredient list, check what's missing or
    insufficient in the current pantry. Returns a list of missing
    ingredient descriptions (empty list = fully ready to cook).
    """
    missing = []

    # For each ingredient in the recipe, check if we have enough in the pantry.
    for ingredient in recipe_ingredients:
        # Each ingredient is a row from recipe_ingredients, which has
        # the columns: id, recipe_id, ingredient_name, quantity_needed, unit
        # We only care about the name, quantity_needed, and unit for our check.
        needed_name = ingredient["ingredient_name"] 
        needed_quantity = ingredient["quantity_needed"]
        needed_unit = ingredient["unit"]

        matching_items = connection.execute(
            "SELECT * FROM items WHERE name LIKE ? AND unit = ?",
            (f"%{needed_name}%", needed_unit)
        ).fetchall()

        total_available = sum(item["quantity"] for item in matching_items)

        if total_available < needed_quantity:
            missing.append(f"{needed_name} (need {needed_quantity} {needed_unit}, have {total_available})")

    return missing

def compute_deduction_plan(ingredient_name, needed_quantity, unit, connection):
    """
    Figures out exactly which pantry items to deduct from, and how much,
    to cover 'needed_quantity' of an ingredient -- oldest items (lowest id) first.
    """
    matching_items = connection.execute(
        "SELECT * FROM items WHERE name LIKE ? AND unit = ? ORDER BY id ASC",
        (f"%{ingredient_name}%", unit)
    ).fetchall()

    plan = []
    remaining_needed = needed_quantity

    for item in matching_items:
        if remaining_needed <= 0:
            break

        deduct_amount = min(item["quantity"], remaining_needed)
        new_quantity = item["quantity"] - deduct_amount

        plan.append({
            "item_id": item["id"],
            "item_name": item["name"],
            "old_quantity": item["quantity"],
            "deduct_amount": deduct_amount,
            "new_quantity": new_quantity,
        })

        remaining_needed -= deduct_amount

    return plan

@app.route("/", methods=["GET"])
@login_required
def show_pantry():
    items = get_all_items()
    return render_template("index.html", items=items, username=session["username"])

@app.route("/add", methods=["POST"])
@login_required
def add_item():
    # request.form is a dictionary-like object holding whatever the
    # submitted form sent. Each key matches an input's "name" attribute.
    name = request.form["name"]
    quantity = request.form["quantity"]
    unit = request.form["unit"]
    expiration_date = request.form["expiration_date"]

    connection = sqlite3.connect("pantry.db")
    connection.execute(
        "INSERT INTO items (name, quantity, unit, expiration_date) VALUES (?, ?, ?, ?)",
        (name, quantity, unit, expiration_date)
    )
    connection.commit()
    connection.close()

    # After adding, send the browser back to the homepage so it sees
    # the updated list -- this is a redirect, a fresh new GET request.
    return redirect("/")

@app.route("/delete/<int:item_id>", methods=["POST"])
@login_required
def delete_item(item_id):
    connection = sqlite3.connect("pantry.db")
    connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    connection.commit()
    connection.close()
    return redirect("/")

@app.route("/edit/<int:item_id>", methods=["POST"])
@login_required
def edit_item(item_id):
    name = request.form["name"]
    quantity = request.form["quantity"]
    unit = request.form["unit"]
    expiration_date = request.form["expiration_date"]

    connection = sqlite3.connect("pantry.db")
    connection.execute(
        "UPDATE items SET name = ?, quantity = ?, unit = ?, expiration_date = ? WHERE id = ?",
        (name, quantity, unit, expiration_date, item_id)
    )
    connection.commit()
    connection.close()
    return redirect("/")

#----------------------------------------------------
# User Authentication Routes
@app.route("/signup", methods=["GET"])
def show_signup():
    return render_template("signup.html", error=None)

@app.route("/signup", methods=["POST"])
def signup():
    username = request.form["username"]
    password = request.form["password"]
    password_hash = generate_password_hash(password)

    connection = sqlite3.connect("pantry.db")
    try:
        connection.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash)
        )
        connection.commit()
    except sqlite3.IntegrityError:
        # This fires if the UNIQUE constraint on username is violated
        connection.close()
        return render_template("signup.html", error="That username is already taken.")
    connection.close()

    return redirect("/login")

@app.route("/login", methods=["GET"])
def show_login():
    return render_template("login.html", error=None)

@app.route("/login", methods=["POST"])
def login():
    username = request.form["username"]
    password = request.form["password"]

    connection = sqlite3.connect("pantry.db")
    connection.row_factory = sqlite3.Row
    user = connection.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    connection.close()

    # fetchone() returns None if no row matched -- i.e. username doesn't exist
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Incorrect username or password.")

    # Storing user id in the session -- this is what "remembers" you're logged in
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect("/")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")
# ----------------------------------------------------

@app.route("/recipes", methods=["GET"])
@login_required
def show_recipes():
    recipes = get_all_recipes()
    return render_template("recipes.html", recipes=recipes, username=session["username"])

@app.route("/recipes/add", methods=["POST"])
@login_required
def add_recipe():
    title = request.form["title"]
    instructions = request.form["instructions"]
    ingredients_text = request.form["ingredients"]

    connection = sqlite3.connect("pantry.db")
    cursor = connection.execute(
        "INSERT INTO recipes (title, instructions) VALUES (?, ?)",
        (title, instructions)
    )
    # lastrowid gives us the id SQLite just auto-assigned to the row
    # we inserted above -- we need it to link ingredients to this recipe.
    new_recipe_id = cursor.lastrowid

    # Parse the textarea: one ingredient per line, formatted "name, quantity, unit"
    for line in ingredients_text.strip().split("\n"):
        parts = line.split(",")
        if len(parts) != 3:
            continue  # skip malformed lines instead of crashing
        name = parts[0].strip()
        quantity = parts[1].strip()
        unit = parts[2].strip()

        connection.execute(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_name, quantity_needed, unit) VALUES (?, ?, ?, ?)",
            (new_recipe_id, name, quantity, unit)
        )

    connection.commit()
    connection.close()
    return redirect("/recipes")

@app.route("/recipes/delete/<int:recipe_id>", methods=["POST"])
@login_required
def delete_recipe(recipe_id):
    connection = sqlite3.connect("pantry.db")
    # Delete ingredients first -- they reference the recipe via foreign key
    connection.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    connection.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    connection.commit()
    connection.close()
    return redirect("/recipes")

@app.route("/recipes/<int:recipe_id>/cook", methods=["GET"])
@login_required
def cook_preview(recipe_id):
    connection = sqlite3.connect("pantry.db")
    connection.row_factory = sqlite3.Row

    recipe = connection.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    recipe_ingredients = connection.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
    ).fetchall()

    full_plan = []
    for ingredient in recipe_ingredients:
        steps = compute_deduction_plan(
            ingredient["ingredient_name"],
            ingredient["quantity_needed"],
            ingredient["unit"],
            connection
        )
        full_plan.extend(steps)

    connection.close()
    return render_template(
        "cook_preview.html",
        recipe=recipe,
        deduction_plan=full_plan,
        username=session["username"]
    )

@app.route("/recipes/<int:recipe_id>/cook", methods=["POST"])
@login_required
def cook_confirm(recipe_id):
    connection = sqlite3.connect("pantry.db")
    connection.row_factory = sqlite3.Row

    recipe_ingredients = connection.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
    ).fetchall()

    for ingredient in recipe_ingredients:
        steps = compute_deduction_plan(
            ingredient["ingredient_name"],
            ingredient["quantity_needed"],
            ingredient["unit"],
            connection
        )
        for step in steps:
            connection.execute(
                "UPDATE items SET quantity = ? WHERE id = ?",
                (step["new_quantity"], step["item_id"])
            )

    connection.commit()
    connection.close()
    return redirect("/recipes")

# ----------------------------------------------------
# Start Server
if __name__ == "__main__":
    app.run(debug=True, port=5050)
