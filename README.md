<div align="center">

# 🗡️ Knife Inventory

**A full-stack inventory management system for knife collectors and dealers.**  
Built with Flask · SQLite · Google OAuth · Modern Admin Dashboard

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?style=flat&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## ✨ Features

### 🛍️ Public Storefront
- **Collection browser** with live search, category & brand filters
- **Product detail pages** with full knife specs and imagery
- Featured knives highlight on the landing page

### 🔐 Authentication
- **Email / password** registration & login with bcrypt hashing
- **Google OAuth 2.0** single sign-on
- Role-based access: standard users vs. admins
- Secure session handling (HTTPONLY + SameSite cookies)

### 🛠️ Admin Dashboard
- At-a-glance stats: total items, quantities, MSRP & sale totals
- Status breakdown (In Stock / Sold / Ordered / On the Way / Cart)
- Pricing coverage indicators

### 📦 Inventory Management
- **Inline status updates** — click to cycle through statuses with color badges
- **Inline quantity editing** — no page reload required
- Filter by brand, status, or search keyword

### 💰 Pricing Tools
- Per-knife MSRP, cost price, and sale price fields
- **Bulk pricing tool** — apply fixed or percentage markups across selected knives
- **Export tool** — generate clean price lists (₪) filtered by brand/category

### 🖼️ Image Management
- File upload from disk
- Paste image directly from clipboard (base64)
- Fetch image from any URL and store locally
- Auto-generates unique filenames to prevent collisions

---

## 🚀 Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/YOUR_USERNAME/knife_inventory.git
cd knife_inventory
```

### 2. Create a virtual environment
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
```
Edit `.env` and fill in your values:

| Variable | Description |
|---|---|
| `SECRET_KEY` | Random secret for Flask sessions — run `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` | From [Google Cloud Console](https://console.cloud.google.com/) |
| `GOOGLE_CLIENT_SECRET` | From [Google Cloud Console](https://console.cloud.google.com/) |

> **Note:** Google OAuth is optional. The app works with email/password login if no Google credentials are provided.

### 5. Run the app
```bash
python app.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

The database (`knives.db`) is created automatically on first run.

---

## 🗂️ Project Structure

```
knife_inventory/
├── app.py                  # Main Flask application (routes, auth, API)
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── seed_db.py              # Seed the DB with sample data
├── seed_data.json          # Sample knife data
│
├── templates/              # Jinja2 HTML templates
│   ├── base.html           # Public layout base
│   ├── index.html          # Landing page
│   ├── collection.html     # Browse all knives
│   ├── product.html        # Knife detail page
│   ├── login.html          # Login page
│   ├── register.html       # Register page
│   ├── admin_base.html     # Admin layout base
│   ├── admin_dashboard.html
│   ├── admin_knives.html   # Knife list & management
│   ├── admin_edit.html     # Add / edit knife form
│   ├── admin_inventory.html # Inline status & qty editor
│   ├── admin_bulk_pricing.html
│   └── admin_export.html
│
└── static/
    ├── style.css           # Global stylesheet
    └── uploads/            # Locally stored knife images (git-ignored)
```

---

## ⚙️ Admin Setup

The admin account is tied to the email defined in `app.py`:

```python
ADMIN_EMAIL = 'your-admin@example.com'
```

Change this to your email before running. The first time you register or sign in with that email, you'll automatically get admin privileges.

### Admin Routes

| Route | Description |
|---|---|
| `/admin` | Dashboard with stats |
| `/admin/knives` | Full knife list |
| `/admin/knives/add` | Add a new knife |
| `/admin/knives/<id>/edit` | Edit a knife |
| `/admin/inventory` | Inline status & quantity management |
| `/admin/bulk-pricing` | Apply bulk price rules |
| `/admin/export` | Generate price list export |

---

## 🗃️ Database Schema

### `knives`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `brand` | TEXT | Knife brand |
| `model` | TEXT | Model name |
| `category` | TEXT | e.g. Folding, Fixed Blade |
| `status` | TEXT | home / sold / need_to_order / ordered / on_the_way / cart |
| `quantity` | INTEGER | Stock count |
| `msrp_new_price` | REAL | Manufacturer suggested retail price (₪) |
| `cost_price` | REAL | What you paid for it (₪) |
| `sale_price` | REAL | Your selling price (₪) |
| `image` | TEXT | Filename of locally stored image |
| `description` | TEXT | Long-form description |
| `notes` | TEXT | Internal notes |

### `users`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | |
| `email` | TEXT UNIQUE | |
| `password_hash` | TEXT | bcrypt hash |
| `google_id` | TEXT | Google OAuth sub |
| `is_admin` | INTEGER | 0 or 1 |

---

## 🔧 Useful Scripts

| Script | Purpose |
|---|---|
| `seed_db.py` | Load `seed_data.json` into the database |
| `migrate_pricing.py` | Add pricing columns to existing DBs |
| `audit_images.py` | Find knives with missing images |

---

## 🔒 Security Notes

- Never commit your `.env` file (it's in `.gitignore`)
- Rotate `SECRET_KEY` in production
- For production, use a WSGI server (e.g. Gunicorn) and HTTPS
- Image uploads are validated by extension and size

---

## 📄 License

MIT © [Your Name](https://github.com/YOUR_USERNAME)
