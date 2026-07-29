import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, session, flash
from flask.cli import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta

app = Flask(__name__)
load_dotenv()  # Load environment variables from .env file
app.secret_key = os.getenv("SECRET_KEY")

def login_required(original_function):
    @wraps(original_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return original_function(*args, **kwargs)
    return wrapper

def parse_float(value, field_name):
    """
    Tries to convert a submitted form value into a float.
    Returns (number, error_message) -- error_message is None if it worked.

    """
    try:
        return float(value), None
    except (ValueError, TypeError):
        return None, f"'{value}' isn't a valid number for {field_name}."

# ----------------------------------------------------
def get_all_items(user_id):
    connection = sqlite3.connect("pantry.db")
    # By default, sqlite3 returns each row as a plain tuple, like (1, "Eggs", 12.0, ...).
    # Setting row_factory like this makes rows behave like dictionaries instead,
    # so in Jinja we can keep writing item.name instead of item[1].
    connection.row_factory = sqlite3.Row
    cursor = connection.execute("SELECT * FROM items WHERE user_id = ?", (user_id,))
    items = cursor.fetchall()
    connection.close()

    today = date.today().isoformat()
    soon_cutoff = (date.today() + timedelta(days=3)).isoformat()

    result = []
    for item in items:
        item_dict = dict(item)
        exp = item_dict.get("expiration_date")
        item_dict["is_expired"] = bool(exp) and exp < today
        item_dict["expiring_soon"] = bool(exp) and today <= exp <= soon_cutoff
        result.append(item_dict)
    return result

def get_all_recipes(user_id):
    connection = sqlite3.connect("pantry.db")
    connection.row_factory = sqlite3.Row
    recipes = connection.execute("SELECT * FROM recipes WHERE user_id = ?", (user_id,)).fetchall()

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
        recipe_dict["missing"] = check_recipe_availability(ingredients, connection, user_id)
        result.append(recipe_dict)

    connection.close()
    return result

def normalize_unit(unit):
    # Makes "cup", "Cups", "CUPS" all compare as equal, since they
    # all describe the same actual unit -- just typed differently.
    return unit.strip().lower().rstrip("s")

def check_recipe_availability(recipe_ingredients, connection, user_id):
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
            "SELECT * FROM items WHERE name LIKE ? AND user_id = ?",
            (f"%{needed_name}%", user_id)
        ).fetchall()

        total_available = sum(item["quantity"] for item in matching_items
                              if normalize_unit(item["unit"]) == normalize_unit(needed_unit)
                              )

        if total_available < needed_quantity:
            missing.append(f"{needed_name} (need {needed_quantity} {needed_unit}, have {total_available})")

    return missing

def compute_deduction_plan(ingredient_name, needed_quantity, unit, connection, user_id):
    all_name_matches = connection.execute(
        "SELECT * FROM items WHERE name LIKE ? AND user_id = ? ORDER BY id ASC",
        (f"%{ingredient_name}%", user_id)
    ).fetchall()

    matching_items = [
        item for item in all_name_matches
        if normalize_unit(item["unit"]) == normalize_unit(unit)
    ]

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
    items = get_all_items(session["user_id"])

    expired_items = [item for item in items if item["is_expired"]]
    expiring_items = [item for item in items if item["expiring_soon"] and not item["is_expired"]]
    normal_items = [item for item in items if not item["is_expired"] and not item["expiring_soon"]]
    return render_template("index.html", 
                           expired_items=expired_items,
                           expiring_items=expiring_items,
                           normal_items=normal_items,
                           username=session["username"])

@app.route("/add", methods=["POST"])
@login_required
def add_item():
    # request.form is a dictionary-like object holding whatever the
    # submitted form sent. Each key matches an input's "name" attribute.
    name = request.form.get("name", "").strip()
    quantity_raw = request.form.get("quantity", "").strip()
    unit = request.form.get("unit", "").strip()
    expiration_date = request.form.get("expiration_date", "").strip()

    # Validate that name and unit are not empty, and that quantity is a valid float.
    if not name or not unit:
        flash("Name and unit are required fields.")
        return redirect("/")
    # Validate that quantity is a valid float
    quantity, error = parse_float(quantity_raw, "quantity")
    if error:
        flash(error)
        return redirect("/")

    connection = sqlite3.connect("pantry.db")
    connection.execute(
        "INSERT INTO items (name, quantity, unit, expiration_date, user_id) VALUES (?, ?, ?, ?, ?)",
        (name, quantity, unit, expiration_date, session["user_id"])
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
    # Delete the item only if it belongs to the currently logged-in user (Safeguard)
    connection.execute("DELETE FROM items WHERE id = ? AND user_id = ?", (item_id, session["user_id"]))
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
        "UPDATE items SET name = ?, quantity = ?, unit = ?, expiration_date = ? WHERE id = ? AND user_id = ?",
        (name, quantity, unit, expiration_date, item_id, session["user_id"])
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
    recipes = get_all_recipes(session["user_id"])
    return render_template("recipes.html", recipes=recipes, username=session["username"])

@app.route("/recipes/add", methods=["POST"])
@login_required
def add_recipe():
    title = request.form.get("title", "").strip()
    instructions = request.form.get("instructions", "").strip()
    ingredients_text = request.form.get("ingredients", "").strip()

    if not title or not instructions or not ingredients_text:
        flash("Title, instructions, and ingredients are required fields.")
        return redirect("/recipes")

    parsed_ingredients = []
    skipped_lines = []
    for line in ingredients_text.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) != 3:
            skipped_lines.append(line)
            continue

        name = parts[0].strip()
        quantity, error = parse_float(parts[1].strip(), "ingredient quantity")

        if error or not name or not unit:
            skipped_lines.append(line)
            continue
        parsed_ingredients.append((name, quantity, unit))

    if skipped_lines:
        flash(f"Skipped {len(skipped_lines)} ingredient line(s) that weren't formatted as 'name, quantity, unit'.")

    connection = sqlite3.connect("pantry.db")
    cursor = connection.execute(
        "INSERT INTO recipes (title, instructions, user_id) VALUES (?, ?, ?)",
        (title, instructions, session["user_id"])
    )
    # lastrowid gives us the id SQLite just auto-assigned to the row
    # we inserted above -- we need it to link ingredients to this recipe.
    new_recipe_id = cursor.lastrowid

    # Parse the textarea: one ingredient per line, formatted "name, quantity, unit"
    for name, quantity, unit in parsed_ingredients:
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
    recipe = connection.execute(
        "SELECT * FROM recipes WHERE id = ? AND user_id = ?", (recipe_id, session["user_id"])
    ).fetchone()
    if recipe is not None:
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

    recipe = connection.execute("SELECT * FROM recipes WHERE id = ? AND user_id = ?", (recipe_id, session["user_id"])).fetchone()

    if recipe is None:
        connection.close()
        return redirect("/recipes")

    recipe_ingredients = connection.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
    ).fetchall()

    full_plan = []
    for ingredient in recipe_ingredients:
        steps = compute_deduction_plan(
            ingredient["ingredient_name"],
            ingredient["quantity_needed"],
            ingredient["unit"],
            connection,
            session["user_id"]
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

    recipe = connection.execute("SELECT * FROM recipes WHERE id = ? AND user_id = ?", (recipe_id, session["user_id"])).fetchone()
    if recipe is None:
        connection.close()
        return redirect("/recipes")

    recipe_ingredients = connection.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
    ).fetchall()

    for ingredient in recipe_ingredients:
        steps = compute_deduction_plan(
            ingredient["ingredient_name"],
            ingredient["quantity_needed"],
            ingredient["unit"],
            connection,
            session["user_id"]
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
