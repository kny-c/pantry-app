import os
from functools import wraps
import re
from flask import Flask, render_template, request, redirect, session, flash
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta
from flask_wtf.csrf import CSRFProtect
import psycopg2
import psycopg2.extras

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
csrf = CSRFProtect(app)
DATABASE_URL = os.getenv("DATABASE_URL")
UNIT_OPTIONS = ["cup", "tbsp", "tsp", "fl oz", "ml", "l", "oz", "lb", "g", "kg", "count", "can", "package", "clove"]
# ----------------------------------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

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
    connection = get_db_connection()
    cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM items WHERE user_id = %s", (user_id,))
    items = cursor.fetchall()
    connection.close()

    today = date.today().isoformat()
    soon_cutoff = (date.today() + timedelta(days=5)).isoformat()

    result = []
    for item in items:
        item_dict = dict(item)
        exp = item_dict.get("expiration_date")
        item_dict["is_expired"] = bool(exp) and exp < today
        item_dict["expiring_soon"] = bool(exp) and today <= exp <= soon_cutoff
        result.append(item_dict)
    return result

def get_all_recipes(user_id):
    connection = get_db_connection()
    cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM recipes WHERE user_id = %s", (user_id,))
    recipes = cursor.fetchall()

    # Convert each recipe row into a plain dict so we can attach
    # its ingredient list to it -- sqlite3.Row itself doesn't allow
    # adding new keys, but a regular dict does.
    result = []
    for recipe in recipes:
        recipe_dict = dict(recipe)
        # For each recipe, fetch its ingredients from the recipe_ingredients table by recipe id
        cursor.execute(
            "SELECT * FROM recipe_ingredients WHERE recipe_id = %s",
            (recipe["id"],)
        )
        ingredients = cursor.fetchall()
        recipe_dict["ingredients"] = ingredients
        recipe_dict["missing"] = check_recipe_availability(ingredients, cursor, user_id)
        result.append(recipe_dict)

    connection.close()
    return result

def normalize_unit(unit):
    # Makes "cup", "Cups", "CUPS" all compare as equal, since they
    # all describe the same actual unit -- just typed differently.
    return unit.strip().lower().rstrip("s")

def check_recipe_availability(recipe_ingredients, cursor, user_id):
    """
    For a given recipe's ingredient list, check what's missing or
    insufficient in the current pantry. Returns a list of missing
    ingredient descriptions (empty list = fully ready to cook).
    """
    issues = []

    # For each ingredient in the recipe, check if we have enough in the pantry.
    for ingredient in recipe_ingredients:
        # Each ingredient is a row from recipe_ingredients, which has
        # the columns: id, recipe_id, ingredient_name, quantity_needed, unit
        # We only care about the name, quantity_needed, and unit for our check.
        needed_name = ingredient["ingredient_name"] 
        needed_quantity = ingredient["quantity_needed"]
        needed_unit = ingredient["unit"]

        cursor.execute(
            "SELECT * FROM items WHERE name ~* %s AND user_id = %s",
            (r'(^|\s)' + re.escape(needed_name) + r'(\s|$)', user_id)
        )
        matching_items = cursor.fetchall()

        unit_matches = [
            item for item in matching_items
            if normalize_unit(item["unit"]) == normalize_unit(needed_unit)
        ]
        total_available = sum(item["quantity"] for item in unit_matches)

        if total_available >= needed_quantity:
            continue  # We have enough of this ingredient, so no issue to report.

        mismatched = [item for item in matching_items if item["id"] not in {u["id"] for u in unit_matches}]

        if not matching_items:
            issues.append({
                "ingredient_name": needed_name,
                "status": "missing",
                "detail": f"need {needed_quantity} {needed_unit}, have 0",
            })
        elif mismatched:
            other_units = sorted(set(normalize_unit(item["unit"]) for item in mismatched))
            issues.append({
                "ingredient_name": needed_name,
                "status": "unsynced_unit",
                "detail": f"need {needed_quantity} {needed_unit}, "
                          f"but some stored as {', '.join(other_units)}",
            })
        else:
            issues.append({
                "ingredient_name": needed_name,
                "status": "missing",
                "detail": f"need {needed_quantity} {needed_unit}, have {total_available}",
            })
    return issues

def compute_deduction_plan(ingredient_name, needed_quantity, unit, cursor, user_id):
    cursor.execute(
        "SELECT * FROM items WHERE name ~* %s AND user_id = %s ORDER BY id ASC",
        (r'(^|\s)' + re.escape(ingredient_name) + r'(\s|$)', user_id)
    )
    all_name_matches = cursor.fetchall()

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

# ----------------------------------------------------
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
                           username=session["username"],
                           unit_options=UNIT_OPTIONS)

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

    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO items (name, quantity, unit, expiration_date, user_id) VALUES (%s, %s, %s, %s, %s)",
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
    connection = get_db_connection()
    # Delete the item only if it belongs to the currently logged-in user (Safeguard)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM items WHERE id = %s AND user_id = %s", (item_id, session["user_id"]))
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

    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute(
        "UPDATE items SET name = %s, quantity = %s, unit = %s, expiration_date = %s WHERE id = %s AND user_id = %s",
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

    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            (username, password_hash)
        )
        connection.commit()
    except psycopg2.IntegrityError:
        # This fires if the UNIQUE constraint on username is violated
        connection.rollback()
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

    connection = get_db_connection()
    cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        "SELECT * FROM users WHERE username = %s", (username,)
    )
    user = cursor.fetchone()
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
    return render_template("recipes.html", recipes=recipes, username=session["username"], unit_options=UNIT_OPTIONS)

@app.route("/recipes/add", methods=["POST"])
@login_required
def add_recipe():
    title = request.form.get("title", "").strip()
    instructions = request.form.get("instructions", "").strip()

    # Pull every input sharing the same name attribute into a list. This is how we can get all the ingredients from the form.
    # 3 Ingredient fields: name, quantity, unit. Each is a list of values.
    names = request.form.getlist("ingredient_name")
    quantities = request.form.getlist("ingredient_quantity")
    units = request.form.getlist("ingredient_unit")

    if not title or not instructions or not names:
        flash("Title, instructions, and ingredients are required fields.")
        return redirect("/recipes")

    parsed_ingredients = []
    skipped = []
    for name, quantity_raw, unit in zip(names, quantities, units):
        name = name.strip()
        unit = unit.strip()
        quantity, error = parse_float(quantity_raw.strip(), "ingredient quantity")
        if not name or not unit or error:
            skipped.append(name or "(blank)")
            continue
        parsed_ingredients.append((name, quantity, unit))

    if not parsed_ingredients:
        flash("No valid ingredients were provided. Please check your input.")
        return redirect("/recipes")
    if skipped:
        flash(f"Skipped {len(skipped)} ingredient row(s) that were incomplete.")

    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO recipes (title, instructions, user_id) VALUES (%s, %s, %s) RETURNING id",
        (title, instructions, session["user_id"])
    )
    new_recipe_id = cursor.fetchone()[0]
    for name, quantity, unit in parsed_ingredients:
        cursor.execute(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_name, quantity_needed, unit) VALUES (%s, %s, %s, %s)",
            (new_recipe_id, name, quantity, unit)
        )

    connection.commit()
    connection.close()
    return redirect("/recipes")

@app.route("/recipes/delete/<int:recipe_id>", methods=["POST"])
@login_required
def delete_recipe(recipe_id):
    connection = get_db_connection()
    cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        "SELECT * FROM recipes WHERE id = %s AND user_id = %s", (recipe_id, session["user_id"])
    )
    recipe = cursor.fetchone()
    if recipe is not None:
        cursor.execute("DELETE FROM recipe_ingredients WHERE recipe_id = %s", (recipe_id,))
        cursor.execute("DELETE FROM recipes WHERE id = %s", (recipe_id,))
        connection.commit()
    connection.close()
    return redirect("/recipes")

@app.route("/recipes/<int:recipe_id>/cook", methods=["GET"])
@login_required
def cook_preview(recipe_id):
    connection = get_db_connection()
    cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        "SELECT * FROM recipes WHERE id = %s AND user_id = %s", (recipe_id, session["user_id"])
    )
    recipe = cursor.fetchone()

    if recipe is None:
        connection.close()
        return redirect("/recipes")

    cursor.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = %s", (recipe_id,)
    )
    recipe_ingredients = cursor.fetchall()

    full_plan = []
    for ingredient in recipe_ingredients:
        steps = compute_deduction_plan(
            ingredient["ingredient_name"],
            ingredient["quantity_needed"],
            ingredient["unit"],
            cursor,
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
    connection = get_db_connection()
    cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute(
        "SELECT * FROM recipes WHERE id = %s AND user_id = %s", (recipe_id, session["user_id"])
    )
    recipe = cursor.fetchone()
    
    if recipe is None:
        connection.close()
        return redirect("/recipes")

    cursor.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id = %s", (recipe_id,)
    )
    recipe_ingredients = cursor.fetchall()

    for ingredient in recipe_ingredients:
        steps = compute_deduction_plan(
            ingredient["ingredient_name"],
            ingredient["quantity_needed"],
            ingredient["unit"],
            cursor,
            session["user_id"]
        )
        
        for step in steps:
            cursor.execute(
                "UPDATE items SET quantity = %s WHERE id = %s AND user_id = %s",
                (step["new_quantity"], step["item_id"], session["user_id"])
            )

    connection.commit()
    connection.close()
    return redirect("/recipes")

# ----------------------------------------------------
# Start Server
if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(debug=debug_mode, port=5050)
