import os
import sqlite3
import uuid
import base64
import urllib.request
import ssl
from contextlib import closing
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv

load_dotenv(override=True)

app = Flask(__name__)
# SECRET_KEY from env; falls back to a random key (NOT persistent across restarts)
app.secret_key = os.getenv('SECRET_KEY') or os.urandom(32)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
DB_NAME = "knives.db"
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['GOOGLE_CLIENT_ID'] = os.getenv("GOOGLE_CLIENT_ID", "")
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET", "")

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_unique_filename(ext):
    """Generate a unique filename with the given extension."""
    return f"img_{uuid.uuid4().hex[:12]}.{ext}"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(get_db_connection()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand TEXT NOT NULL,
                model TEXT NOT NULL,
                category TEXT DEFAULT '',
                status TEXT NOT NULL,
                buy_price REAL NOT NULL DEFAULT 0,
                estimated_value REAL NOT NULL DEFAULT 0,
                quantity INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT '',
                image TEXT DEFAULT '',
                description TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                image_source_url TEXT DEFAULT '',
                price_source_url TEXT DEFAULT '',
                data_confidence TEXT DEFAULT 'low',
                msrp_new_price REAL NOT NULL DEFAULT 0,
                cost_price REAL NOT NULL DEFAULT 0,
                sale_price REAL NOT NULL DEFAULT 0,
                price_confidence TEXT DEFAULT 'low'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                google_id TEXT,
                is_admin INTEGER DEFAULT 0
            )
        """)
        conn.commit()

init_db()

# -----------------------------------------------------
# AUTHENTICATION
# -----------------------------------------------------

class User(UserMixin):
    def __init__(self, id, email, password_hash, google_id, is_admin):
        self.id = str(id)
        self.email = email
        self.password_hash = password_hash
        self.google_id = google_id
        self.is_admin = bool(is_admin)

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if user_row:
        return User(user_row['id'], user_row['email'], user_row['password_hash'], user_row['google_id'], user_row['is_admin'])
    return None

ADMIN_EMAIL = 'yairk07@gmail.com'

def is_safe_url(target):
    """Ensure redirect target is on the same host to prevent open redirects."""
    from urllib.parse import urlparse, urljoin
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access this page.", "error")
            return redirect(url_for('login', next=request.url))
        if not current_user.is_admin and current_user.email != ADMIN_EMAIL:
            flash("You do not have administrative privileges.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        
        if not email or not password:
            flash("Email and password are required.", "error")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("register"))
            
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        is_admin = 1 if email == ADMIN_EMAIL else 0
        
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
                         (email, hashed, is_admin))
            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already registered.", "error")
        finally:
            conn.close()
            
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        
        conn = get_db_connection()
        user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        
        if user_row and user_row['password_hash'] and bcrypt.check_password_hash(user_row['password_hash'], password):
            user = User(user_row['id'], user_row['email'], user_row['password_hash'], user_row['google_id'], user_row['is_admin'])
            login_user(user, remember=remember)
            next_url = request.form.get('next') or request.args.get('next')
            if next_url and is_safe_url(next_url):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard") if user.email == ADMIN_EMAIL or user.is_admin else url_for("index"))
        else:
            flash("Invalid email or password.", "error")
            
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

@app.route("/login/google")
def login_google():
    if not app.config.get('GOOGLE_CLIENT_ID') or not app.config.get('GOOGLE_CLIENT_SECRET'):
        flash("Google Login is not fully configured (missing credentials).", "error")
        return redirect(url_for('login'))
        
    redirect_uri = url_for('authorize_google', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/authorize/google")
def authorize_google():
    # Handle user cancelling the Google consent screen
    error = request.args.get('error')
    if error:
        flash("Google login was cancelled or failed.", "error")
        return redirect(url_for('login'))

    try:
        token = google.authorize_access_token()
    except Exception as e:
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for('login'))

    user_info = token.get('userinfo')
    
    if not user_info or not user_info.get('email_verified', False):
        flash("Could not verify your Google account email.", "error")
        return redirect(url_for('login'))

    email = user_info['email'].lower()
    google_id = user_info['sub']
    is_admin = 1 if email == ADMIN_EMAIL else 0
    
    conn = get_db_connection()
    user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    if user_row:
        # Update google_id and promote to admin if applicable
        conn.execute(
            "UPDATE users SET google_id = ?, is_admin = MAX(is_admin, ?) WHERE id = ?",
            (google_id, is_admin, user_row['id'])
        )
        conn.commit()
        user = User(user_row['id'], email, user_row['password_hash'], google_id,
                    max(user_row['is_admin'], is_admin))
    else:
        cur = conn.execute(
            "INSERT INTO users (email, google_id, is_admin) VALUES (?, ?, ?)",
            (email, google_id, is_admin)
        )
        conn.commit()
        user = User(cur.lastrowid, email, None, google_id, is_admin)
        
    conn.close()
    login_user(user, remember=True)  # Auto remember on Google login
    
    if user.is_admin or email == ADMIN_EMAIL:
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('index'))

# -----------------------------------------------------
# PUBLIC STOREFRONT
# -----------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    conn = get_db_connection()
    featured = conn.execute("SELECT * FROM knives ORDER BY id DESC LIMIT 4").fetchall()
    conn.close()
    return render_template("index.html", featured_knives=featured)


@app.route("/collection", methods=["GET"])
def collection():
    category_filter = request.args.get("category", "").strip()
    brand_filter = request.args.get("brand", "").strip()
    search_query = request.args.get("q", "").strip()

    query = "SELECT * FROM knives WHERE 1=1"
    params = []

    if category_filter:
        query += " AND category = ?"
        params.append(category_filter)
    if brand_filter:
        query += " AND brand = ?"
        params.append(brand_filter)
    if search_query:
        query += " AND (brand LIKE ? OR model LIKE ? OR description LIKE ?)"
        like_q = f"%{search_query}%"
        params.extend([like_q, like_q, like_q])

    query += " ORDER BY id DESC"

    conn = get_db_connection()
    knives = conn.execute(query, params).fetchall()
    
    cats_db = conn.execute("SELECT DISTINCT category FROM knives WHERE category != ''").fetchall()
    brands_db = conn.execute("SELECT DISTINCT brand FROM knives WHERE brand != ''").fetchall()
    conn.close()

    categories = [row['category'] for row in cats_db]
    brands = [row['brand'] for row in brands_db]

    return render_template(
        "collection.html", 
        knives=knives, 
        categories=categories, 
        brands=brands,
        category_filter=category_filter,
        brand_filter=brand_filter,
        search_query=search_query
    )


@app.route("/knife/<int:knife_id>", methods=["GET"])
def product(knife_id):
    conn = get_db_connection()
    knife = conn.execute("SELECT * FROM knives WHERE id = ?", (knife_id,)).fetchone()
    conn.close()
    if knife is None:
        return redirect(url_for("collection"))
    return render_template("product.html", knife=knife)


# -----------------------------------------------------
# ADMIN DASHBOARD
# -----------------------------------------------------

@app.route("/admin", methods=["GET"])
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    totals = conn.execute("""
        SELECT
            COUNT(*) as total_items,
            COALESCE(SUM(quantity), 0) as total_quantity,
            0 as total_cost,
            COALESCE(SUM(msrp_new_price * quantity), 0) as total_msrp,
            COALESCE(SUM(sale_price * quantity), 0) as total_sale,
            0 as total_profit
        FROM knives
        WHERE sale_price > 0 OR msrp_new_price > 0
    """).fetchone()

    counts = conn.execute("""
        SELECT
            COUNT(*) as total_items,
            COALESCE(SUM(quantity), 0) as total_quantity
        FROM knives
    """).fetchone()

    status_counts = conn.execute("""
        SELECT status, COUNT(*) as count
        FROM knives
        GROUP BY status
    """).fetchall()

    pricing_stats = conn.execute("""
        SELECT
            SUM(CASE WHEN msrp_new_price > 0 THEN 1 ELSE 0 END) as has_msrp,
            SUM(CASE WHEN sale_price > 0 THEN 1 ELSE 0 END) as has_sale
        FROM knives
    """).fetchone()

    conn.close()

    return render_template(
        "admin_dashboard.html",
        totals=totals,
        counts=counts,
        status_counts=status_counts,
        pricing_stats=pricing_stats
    )


@app.route("/admin/knives", methods=["GET"])
@admin_required
def admin_knives():
    status_filter = request.args.get("status", "").strip()
    search_query = request.args.get("q", "").strip()

    query = "SELECT * FROM knives WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if search_query:
        query += " AND (brand LIKE ? OR model LIKE ? OR notes LIKE ?)"
        like_q = f"%{search_query}%"
        params.extend([like_q, like_q, like_q])
    
    query += " ORDER BY id DESC"
    
    conn = get_db_connection()
    knives = conn.execute(query, params).fetchall()
    conn.close()

    return render_template("admin_knives.html", knives=knives, status_filter=status_filter, search_query=search_query)


@app.route("/admin/knives/add", methods=["GET", "POST"])
@admin_required
def admin_add_knife():
    if request.method == "POST":
        return _handle_admin_save(request, knife_id=None)
    return render_template("admin_edit.html", knife=None)


@app.route("/admin/knives/<int:knife_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_knife(knife_id):
    conn = get_db_connection()
    knife = conn.execute("SELECT * FROM knives WHERE id = ?", (knife_id,)).fetchone()
    conn.close()

    if knife is None:
        return redirect(url_for("admin_knives"))

    if request.method == "POST":
        return _handle_admin_save(request, knife_id=knife_id, existing_knife=knife)
    
    return render_template("admin_edit.html", knife=knife)


@app.route("/admin/knives/<int:knife_id>/delete", methods=["POST"])
@admin_required
def admin_delete_knife(knife_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM knives WHERE id = ?", (knife_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_knives"))


# -----------------------------------------------------
# INVENTORY MANAGEMENT
# -----------------------------------------------------

STATUSES = [
    {"key": "home",          "label": "In Stock",       "color": "#10b981"},
    {"key": "sold",          "label": "Sold",            "color": "#ef4444"},
    {"key": "need_to_order", "label": "Need to Order",  "color": "#f97316"},
    {"key": "ordered",       "label": "Ordered",        "color": "#8b5cf6"},
    {"key": "on_the_way",    "label": "On the Way",     "color": "#3b82f6"},
    {"key": "cart",          "label": "Cart",           "color": "#eab308"},
]

@app.route("/admin/inventory", methods=["GET"])
@admin_required
def admin_inventory():
    brand_filter = request.args.get("brand", "").strip()
    status_filter = request.args.get("status", "").strip()
    search_q = request.args.get("q", "").strip()

    conn = get_db_connection()
    brands_db = conn.execute("SELECT DISTINCT brand FROM knives WHERE brand != '' ORDER BY brand").fetchall()
    brands = [r['brand'] for r in brands_db]

    query = "SELECT * FROM knives WHERE 1=1"
    params = []
    if brand_filter:
        query += " AND brand = ?"
        params.append(brand_filter)
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if search_q:
        query += " AND (brand LIKE ? OR model LIKE ?)"
        lq = f"%{search_q}%"
        params.extend([lq, lq])

    query += " ORDER BY brand, model"
    knives = conn.execute(query, params).fetchall()
    conn.close()

    return render_template("admin_inventory.html",
                           knives=knives,
                           brands=brands,
                           statuses=STATUSES,
                           brand_filter=brand_filter,
                           status_filter=status_filter,
                           search_q=search_q)


@app.route("/admin/inventory/update-status", methods=["POST"])
@admin_required
def admin_update_status():
    data = request.get_json()
    knife_id = data.get("id")
    new_status = data.get("status")
    valid_statuses = [s["key"] for s in STATUSES]
    if not knife_id or new_status not in valid_statuses:
        return jsonify({"error": "Invalid"}), 400
    conn = get_db_connection()
    conn.execute("UPDATE knives SET status = ? WHERE id = ?", (new_status, knife_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": knife_id, "status": new_status})


@app.route("/admin/inventory/update-quantity", methods=["POST"])
@admin_required
def admin_update_quantity():
    data = request.get_json()
    knife_id = data.get("id")
    quantity = data.get("quantity")
    try:
        quantity = max(1, int(quantity))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid quantity"}), 400
    conn = get_db_connection()
    conn.execute("UPDATE knives SET quantity = ? WHERE id = ?", (quantity, knife_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": knife_id, "quantity": quantity})


# -----------------------------------------------------

# -----------------------------------------------------
# IMAGE API ENDPOINTS
# -----------------------------------------------------

@app.route("/admin/api/upload-clipboard", methods=["POST"])
@admin_required
def api_upload_clipboard():
    """Handle pasted image from clipboard (base64 data)."""
    data = request.get_json()
    if not data or 'image_data' not in data:
        return jsonify({"error": "No image data"}), 400

    image_data = data['image_data']
    # Expect format: data:image/png;base64,iVBOR...
    try:
        header, encoded = image_data.split(',', 1)
        # Extract mime type
        mime = header.split(':')[1].split(';')[0]  # image/png
        ext = mime.split('/')[1]
        if ext == 'jpeg': ext = 'jpg'
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({"error": f"Invalid image type: {ext}"}), 400

        img_bytes = base64.b64decode(encoded)
        if len(img_bytes) < 100:
            return jsonify({"error": "Image too small"}), 400

        filename = generate_unique_filename(ext)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(filepath, 'wb') as f:
            f.write(img_bytes)

        return jsonify({
            "success": True,
            "filename": filename,
            "url": url_for('static', filename=f'uploads/{filename}')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/upload-url", methods=["POST"])
@admin_required
def api_upload_url():
    """Download an image from a URL and store it locally."""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "No URL provided"}), 400

    image_url = data['url'].strip()
    if not image_url.startswith(('http://', 'https://')):
        return jsonify({"error": "Invalid URL"}), 400

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(image_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            content_type = resp.headers.get('Content-Type', '')
            img_bytes = resp.read()

        if len(img_bytes) < 500:
            return jsonify({"error": "Downloaded file too small, likely not an image"}), 400

        # Determine extension
        ext = 'jpg'
        if 'png' in content_type: ext = 'png'
        elif 'webp' in content_type: ext = 'webp'
        elif 'gif' in content_type: ext = 'gif'
        elif '.png' in image_url.lower(): ext = 'png'
        elif '.webp' in image_url.lower(): ext = 'webp'
        elif '.gif' in image_url.lower(): ext = 'gif'

        filename = generate_unique_filename(ext)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(filepath, 'wb') as f:
            f.write(img_bytes)

        return jsonify({
            "success": True,
            "filename": filename,
            "url": url_for('static', filename=f'uploads/{filename}'),
            "source_url": image_url
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------
# BULK PRICING TOOL
# -----------------------------------------------------

@app.route("/admin/bulk-pricing", methods=["GET"])
@admin_required
def admin_bulk_pricing():
    conn = get_db_connection()
    knives = conn.execute("SELECT id, brand, model, msrp_new_price, cost_price, sale_price, quantity FROM knives ORDER BY brand, model").fetchall()
    conn.close()
    return render_template("admin_bulk_pricing.html", knives=knives)


@app.route("/admin/bulk-pricing/apply", methods=["POST"])
@admin_required
def admin_bulk_pricing_apply():
    mode = request.form.get("mode", "")
    amount = request.form.get("amount", "0")
    knife_ids = request.form.getlist("knife_ids")

    try:
        amount = float(amount)
    except ValueError:
        flash("Invalid amount value.")
        return redirect(url_for("admin_bulk_pricing"))

    if not knife_ids:
        flash("No knives selected.")
        return redirect(url_for("admin_bulk_pricing"))

    conn = get_db_connection()
    updated = 0

    for kid in knife_ids:
        kid = int(kid)
        knife = conn.execute("SELECT msrp_new_price, cost_price FROM knives WHERE id = ?", (kid,)).fetchone()
        if not knife:
            continue

        new_sale = 0.0

        if mode == "msrp_fixed":
            if knife['msrp_new_price'] > 0:
                new_sale = knife['msrp_new_price'] + amount
            else:
                continue
        elif mode == "msrp_percent":
            if knife['msrp_new_price'] > 0:
                new_sale = knife['msrp_new_price'] * (1 + amount / 100.0)
            else:
                continue
        else:
            continue

        new_sale = round(new_sale, 2)
        if new_sale > 0:
            conn.execute("UPDATE knives SET sale_price = ? WHERE id = ?", (new_sale, kid))
            updated += 1

    conn.commit()
    conn.close()
    flash(f"Updated sale price for {updated} knives.")
    return redirect(url_for("admin_bulk_pricing"))


# -----------------------------------------------------
# PRICE EXPORT TOOL
# -----------------------------------------------------

@app.route("/admin/export", methods=["GET"])
@admin_required
def admin_export():
    brand_filter = request.args.get("brand", "").strip()
    category_filter = request.args.get("category", "").strip()

    conn = get_db_connection()
    
    cats_db = conn.execute("SELECT DISTINCT category FROM knives WHERE category != ''").fetchall()
    brands_db = conn.execute("SELECT DISTINCT brand FROM knives WHERE brand != ''").fetchall()
    categories = [row['category'] for row in cats_db]
    brands = [row['brand'] for row in brands_db]

    query = "SELECT brand, model, sale_price, msrp_new_price, quantity FROM knives WHERE status != 'sold'"
    params = []

    if brand_filter:
        query += " AND brand = ?"
        params.append(brand_filter)
    if category_filter:
        query += " AND category = ?"
        params.append(category_filter)

    query += " ORDER BY brand, model"
    
    knives = conn.execute(query, params).fetchall()
    conn.close()

    export_lines = []
    if knives:
        for k in knives:
            price = k['sale_price'] if k['sale_price'] and k['sale_price'] > 0 else k['msrp_new_price']
            if price and price > 0:
                brand = k['brand'].strip() if k['brand'] else ''
                if not brand or brand.lower() in ('no brand', 'unknown', 'none'):
                    line = f"{k['model']}: ₪{price:,.2f}"
                else:
                    line = f"{brand} - {k['model']}: ₪{price:,.2f}"
                export_lines.append(line)
        
    export_text = "\n".join(export_lines) if export_lines else "No knives match the filter or have pricing data."

    return render_template("admin_export.html", 
                           export_text=export_text,
                           brands=brands,
                           categories=categories,
                           brand_filter=brand_filter,
                           category_filter=category_filter)


# -----------------------------------------------------
# SAVE HELPER
# -----------------------------------------------------

def _handle_admin_save(req, knife_id=None, existing_knife=None):
    brand = req.form.get("brand", "").strip()
    model = req.form.get("model", "").strip()
    category = req.form.get("category", "").strip()
    status = req.form.get("status", "home").strip()
    description = req.form.get("description", "").strip()
    notes = req.form.get("notes", "").strip()

    image_source_url = req.form.get("image_source_url", "").strip()
    price_source_url = req.form.get("price_source_url", "").strip()
    data_confidence = req.form.get("data_confidence", "low").strip()
    price_confidence = req.form.get("price_confidence", "low").strip()

    def safe_float(field, default=0.0):
        try: return float(req.form.get(field, str(default)).strip())
        except ValueError: return default

    msrp_new_price = safe_float("msrp_new_price")
    cost_price = safe_float("cost_price")
    sale_price = safe_float("sale_price")
    
    currency = req.form.get("currency", "ILS").strip()
    if currency == "USD":
        # Convert to ILS (Approximate rate: 3.2)
        msrp_new_price = round(msrp_new_price * 3.2, 2)
        cost_price = round(cost_price * 3.2, 2)
        sale_price = round(sale_price * 3.2, 2)
    buy_price = cost_price
    estimated_value = msrp_new_price

    try: 
        quantity = int(req.form.get("quantity", "1").strip())
        if quantity < 1: quantity = 1
    except ValueError: quantity = 1

    # ── Image Handling ──
    # Priority: remove > new upload > hidden (from clipboard/URL JS) > existing
    remove_image = req.form.get("remove_image", "") == "1"
    filename = existing_knife['image'] if existing_knife else ''
    image_url = ''  # We always store locally now

    if remove_image:
        filename = ''
    elif 'image_upload' in req.files:
        file = req.files['image_upload']
        if file and file.filename != '' and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            new_name = generate_unique_filename(ext)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], new_name))
            filename = new_name

    # If JS set a filename via clipboard/URL download
    js_filename = req.form.get("image_filename", "").strip()
    if js_filename and not remove_image:
        # Only use if the file actually exists
        if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], js_filename)):
            filename = js_filename

    conn = get_db_connection()
    if knife_id is None:
        conn.execute("""
            INSERT INTO knives (brand, model, category, status, buy_price, estimated_value, quantity, notes, image, description,
                image_url, image_source_url, price_source_url, data_confidence,
                msrp_new_price, cost_price, sale_price, price_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (brand, model, category, status, buy_price, estimated_value, quantity, notes, filename, description,
              image_url, image_source_url, price_source_url, data_confidence,
              msrp_new_price, cost_price, sale_price, price_confidence))
    else:
        conn.execute("""
            UPDATE knives
            SET brand=?, model=?, category=?, status=?, buy_price=?, estimated_value=?, quantity=?, notes=?, image=?, description=?,
                image_url=?, image_source_url=?, price_source_url=?, data_confidence=?,
                msrp_new_price=?, cost_price=?, sale_price=?, price_confidence=?
            WHERE id = ?
        """, (brand, model, category, status, buy_price, estimated_value, quantity, notes, filename, description,
              image_url, image_source_url, price_source_url, data_confidence,
              msrp_new_price, cost_price, sale_price, price_confidence, knife_id))
    conn.commit()
    conn.close()

    return redirect(url_for("admin_knives"))


if __name__ == "__main__":
    app.run(debug=True)
