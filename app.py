import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import numpy as np
import pandas as pd

# If using optional weather integration:
from weather_api_helper import get_weather_forecast

app = Flask(__name__)
app.secret_key = "SUPER_SECRET_KEY_CHANGE_THIS"

# -----------------------------
# DATABASE CONFIG
# -----------------------------
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, "app.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -----------------------------
# MODELS
# -----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin' or 'shelter'

    # These two fields were previously added:
    phone_number = db.Column(db.String(30), default="")
    org_email = db.Column(db.String(100), default="")

class ProduceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Fields from shelter
    num_people = db.Column(db.Integer, default=0)
    volume_goal = db.Column(db.Float, default=0.0)
    calorie_goal = db.Column(db.Float, default=0.0)
    additional_needs = db.Column(db.String(200), default="")
    shelter_notes = db.Column(db.Text, default="")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="new")

    # Relationship to the user
    user = db.relationship("User", backref=db.backref("requests", lazy=True))

    # NEW CODE: Urgency field
    urgency = db.Column(db.Integer, default=1)  # Shelter can rank 1=low, 5=high, etc.


# NEW CODE: Model for Gardens
class Garden(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    garden_name = db.Column(db.String(100), nullable=False)
    area_sq_ft = db.Column(db.Float, default=0.0)
    # You could add more fields if you like, such as location, soil type, etc.


# NEW CODE: Model for Crops currently growing in a Garden
class GardenCrop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    garden_id = db.Column(db.Integer, db.ForeignKey("garden.id"), nullable=False)
    crop_name = db.Column(db.String(100), nullable=False)
    weeks_growing = db.Column(db.Integer, default=0)
    space_used = db.Column(db.Float, default=0.0)

    # Relationship to Garden
    garden = db.relationship("Garden", backref=db.backref("crops", lazy=True))

# -----------------------------
# DATABASE SEEDING (ADMIN)
# -----------------------------
def seed_admin():
    from werkzeug.security import generate_password_hash
    existing_admin = User.query.filter_by(username="admin").first()
    if not existing_admin:
        admin = User(
            username="admin",
            password="admin123",  # plain text here; in production, store hashed
            role="admin"
        )
        db.session.add(admin)
        db.session.commit()

# -----------------------------
# APP CONTEXT & DB CREATION
# -----------------------------
# This will create tables if they don't exist, each time the app starts.

# -----------------------------
# AUTH HELPERS
# -----------------------------
def is_logged_in():
    return "user_id" in session

def current_user():
    if not is_logged_in():
        return None
    return User.query.get(session["user_id"])

def requires_login(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            flash("Please log in first.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def requires_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = current_user()
        if not user or user.role != "admin":
            flash("Admin access only.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# -----------------------------
# RECOMMENDATION / SCHEDULING LOGIC
# -----------------------------
def parse_existing_crops(existing_crops_str):
    if not existing_crops_str.strip():
        return []
    crops_data = []
    items = existing_crops_str.split(";")
    for item in items:
        parts = item.split(":")
        if len(parts) == 3:
            crop_name = parts[0].strip()
            try:
                space = float(parts[1].strip())
                weeks = float(parts[2].strip())
            except ValueError:
                space = 0
                weeks = 0
            crops_data.append({
                "name": crop_name,
                "space": space,
                "weeks_grown": weeks
            })
    return crops_data

def recommend_crops(num_people, volume_goal, calorie_goal, additional_needs, free_space):
    crop_space_requirements = {
        "Potatoes": 10,
        "Corn": 12,
        "Zucchini": 8,
        "Tomatoes": 6,
        "Bell Peppers": 6,
        "Lettuce": 4,
        "Kale": 4,
        "Carrots": 5,
        "Onions": 5,
    }
    recommended = []

    if calorie_goal > 2000:
        recommended.append("Potatoes")
        recommended.append("Corn")
    if volume_goal > 10:
        recommended.append("Zucchini")
        recommended.append("Tomatoes")
        recommended.append("Bell Peppers")
    if "leafy greens" in additional_needs.lower():
        recommended.append("Lettuce")
        recommended.append("Kale")
    if not recommended:
        recommended = ["Tomatoes", "Carrots", "Onions"]

    final_crops = []
    space_left = free_space
    for crop in recommended:
        req = crop_space_requirements.get(crop, 5)
        if req <= space_left:
            final_crops.append(crop)
            space_left -= req

    return final_crops

def generate_planting_diagram(existing_crops, recommended_crops, garden_size):
    diagram = "Garden Layout Diagram\n\n"
    diagram += f"Total garden size: {garden_size} sq ft\n\n"
    diagram += "Existing Crops:\n"
    row_number = 1
    for ecrop in existing_crops:
        diagram += f"  Row {row_number} (existing): {ecrop['name']} occupying {ecrop['space']} sq ft\n"
        row_number += 1

    if recommended_crops:
        diagram += "\nNew Crops:\n"
        for crop in recommended_crops:
            diagram += f"  Row {row_number} (new): {crop}\n"
            row_number += 1
    else:
        diagram += "\nNo space left for new crops.\n"

    return diagram

def generate_schedule(existing_crops, recommended_crops, lat=None, lon=None, api_key=None):
    schedule = []
    # existing
    for ecrop in existing_crops:
        total_cycle = 12
        weeks_done = ecrop["weeks_grown"]
        weeks_left = max(0, total_cycle - weeks_done)
        schedule.append({
            "crop": ecrop["name"] + " (existing)",
            "planting_date": f"~{int(weeks_done)} weeks ago",
            "watering_instructions": "Continue watering as normal.",
            "weeding_instructions": "Weed weekly.",
            "harvest_info": f"Ready in about {weeks_left} more weeks",
            "weather_note": ""
        })

    # new
    base_planting_date = pd.Timestamp("2025-03-01")
    for i, crop in enumerate(recommended_crops):
        plant_date = base_planting_date + pd.Timedelta(weeks=i)
        harvest_date = plant_date + pd.Timedelta(weeks=10)
        schedule.append({
            "crop": crop + " (new)",
            "planting_date": str(plant_date.date()),
            "watering_instructions": "Water 1 inch/week (adjust if rainy).",
            "weeding_instructions": "Weed once/week.",
            "harvest_info": f"Estimated harvest around {harvest_date.date()}",
            "weather_note": ""
        })

    # optionally factor in weather
    if api_key and lat is not None and lon is not None:
        forecast = get_weather_forecast(api_key, lat, lon)
        forecast_dict = {day["date"]: day for day in forecast}
        for entry in schedule:
            pd_date = entry["planting_date"]
            if pd_date in forecast_dict:
                desc = forecast_dict[pd_date]["weather"]
                entry["weather_note"] = f"Forecast: {desc}"
    return schedule


# -----------------------------
# NEW: HOME PAGE ROUTE
# -----------------------------
@app.route("/")
def home():
    return render_template("home.html")

# -----------------------------
# AUTH ROUTES
# -----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            session["user_id"] = user.id
            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            else:
                return redirect(url_for("shelter_dashboard"))
        else:
            flash("Invalid credentials.")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        phone_number = request.form.get("phone_number", "")
        org_email = request.form.get("org_email", "")

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("Username already exists.")
            return redirect(url_for("register"))

        # default role = shelter
        shelter_user = User(
            username=username,
            password=password,
            role="shelter",
            phone_number=phone_number,
            org_email=org_email
        )
        db.session.add(shelter_user)
        db.session.commit()
        flash("Account created! Please log in.")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# -----------------------------
# SHELTER PORTAL
# -----------------------------
@app.route("/shelter/dashboard")
@requires_login
def shelter_dashboard():
    user = current_user()
    if user.role != "shelter":
        flash("Shelter access only.")
        return redirect(url_for("login"))

    my_requests = ProduceRequest.query.filter_by(user_id=user.id).all()
    return render_template("shelter_dashboard.html", user=user, requests=my_requests)

@app.route("/shelter/request", methods=["GET", "POST"])
@requires_login
def new_request():
    user = current_user()
    if user.role != "shelter":
        flash("Shelter access only.")
        return redirect(url_for("login"))

    if request.method == "POST":
        num_people = int(request.form.get("num_people", 0))
        volume_goal = float(request.form.get("volume_goal", 0))
        calorie_goal = float(request.form.get("calorie_goal", 0))
        additional_needs = request.form.get("additional_needs", "")
        shelter_notes = request.form.get("shelter_notes", "")

        # NEW CODE: urgency
        urgency = int(request.form.get("urgency", 1))

        new_req = ProduceRequest(
            user_id=user.id,
            num_people=num_people,
            volume_goal=volume_goal,
            calorie_goal=calorie_goal,
            additional_needs=additional_needs,
            shelter_notes=shelter_notes,
            urgency=urgency
        )
        db.session.add(new_req)
        db.session.commit()
        flash("Request submitted!")
        return redirect(url_for("shelter_dashboard"))

    return render_template("request_form.html")

# -----------------------------
# ADMIN PORTAL
# -----------------------------
@app.route("/admin/dashboard")
@requires_admin
def admin_dashboard():
    shelters = User.query.filter_by(role="shelter").all()
    return render_template("admin_dashboard.html", shelters=shelters)

@app.route("/admin/shelter/<int:shelter_id>")
@requires_admin
def view_shelter_requests(shelter_id):
    user = User.query.get_or_404(shelter_id)
    if user.role != "shelter":
        flash("Not a shelter user.")
        return redirect(url_for("admin_dashboard"))

    requests_ = ProduceRequest.query.filter_by(user_id=user.id).all()
    return render_template("admin_dashboard.html", selected_shelter=user, requests_=requests_)

@app.route("/admin/generate_schedule/<int:request_id>", methods=["GET", "POST"])
@requires_admin
def generate_schedule_for_request(request_id):
    produce_request = ProduceRequest.query.get_or_404(request_id)

    if request.method == "POST":
        garden_size = float(request.form.get("garden_size", 0))
        existing_crops_str = request.form.get("existing_crops", "")
        lat = float(request.form.get("latitude", 0))
        lon = float(request.form.get("longitude", 0))
        api_key = request.form.get("api_key", None)

        existing_crops = parse_existing_crops(existing_crops_str)
        used_space = sum(ec["space"] for ec in existing_crops)
        free_space = max(0, garden_size - used_space)

        recommended_crops = recommend_crops(
            produce_request.num_people,
            produce_request.volume_goal,
            produce_request.calorie_goal,
            produce_request.additional_needs,
            free_space
        )

        diagram = generate_planting_diagram(existing_crops, recommended_crops, garden_size)
        schedule = generate_schedule(existing_crops, recommended_crops, lat, lon, api_key)

        return render_template(
            "schedule_view.html",
            produce_request=produce_request,
            diagram=diagram,
            schedule=schedule
        )

    return render_template("schedule_form.html", produce_request=produce_request)


@app.route("/shelter/about_creators")
@requires_login
def about_creators():
    user = current_user()
    if user.role != "shelter":
        flash("Shelter access only.")
        return redirect(url_for("login"))
    return render_template("about_creators.html")


# NEW CODE: Admin "Presets" tab for gardens and crops
@app.route("/admin/presets", methods=["GET", "POST"])
@requires_admin
def admin_presets():
    """
    Allows admin to add/delete gardens, set each garden's area,
    and add/delete existing crops with their weeks grown and space used.
    Also can remove a garden, which should remove the dropdown option for that garden.
    """
    # We'll handle all form submissions in a single route for simplicity

    # 1) Handle addition of a new Garden
    if request.method == "POST" and request.form.get("action") == "add_garden":
        g_name = request.form.get("garden_name", "").strip()
        g_area = float(request.form.get("garden_area", 0))
        if g_name:
            new_garden = Garden(garden_name=g_name, area_sq_ft=g_area)
            db.session.add(new_garden)
            db.session.commit()
            flash("New garden added.")

    # 2) Handle deletion of a Garden
    if request.method == "POST" and request.form.get("action") == "delete_garden":
        garden_id = request.form.get("garden_id_to_delete")
        if garden_id:
            garden_obj = Garden.query.get(int(garden_id))
            if garden_obj:
                # Deleting garden also deletes associated crops via cascade or manual
                # If we want manual, do:
                for c in garden_obj.crops:
                    db.session.delete(c)
                db.session.delete(garden_obj)
                db.session.commit()
                flash("Garden deleted.")

    # 3) Handle addition of a new GardenCrop
    if request.method == "POST" and request.form.get("action") == "add_garden_crop":
        selected_garden = request.form.get("selected_garden")
        crop_name = request.form.get("crop_name", "").strip()
        weeks = int(request.form.get("weeks_growing", 0))
        space_used = float(request.form.get("crop_space", 0))
        if selected_garden and crop_name:
            g_obj = Garden.query.get(int(selected_garden))
            if g_obj:
                new_crop = GardenCrop(
                    garden_id=g_obj.id,
                    crop_name=crop_name,
                    weeks_growing=weeks,
                    space_used=space_used
                )
                db.session.add(new_crop)
                db.session.commit()
                flash("New crop added to the garden.")

    # 4) Handle deletion of a GardenCrop
    if request.method == "POST" and request.form.get("action") == "delete_garden_crop":
        crop_id = request.form.get("crop_id_to_delete")
        if crop_id:
            crop_obj = GardenCrop.query.get(int(crop_id))
            if crop_obj:
                db.session.delete(crop_obj)
                db.session.commit()
                flash("Crop deleted.")

    # After handling forms, we fetch updated data
    all_gardens = Garden.query.all()
    all_crops = GardenCrop.query.all()
    return render_template("presets.html", gardens=all_gardens, crops=all_crops)


# -----------------------------
# MAIN
# -----------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_admin()

    app.run(debug=True)
