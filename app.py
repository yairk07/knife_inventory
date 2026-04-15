import os
import sqlite3
import uuid
import base64
import secrets
import re
import json
import concurrent.futures
import time
import urllib.request
import ssl
from datetime import datetime, timezone
from contextlib import closing
from functools import wraps
from collections import defaultdict, deque
from urllib.parse import urlparse, parse_qs, unquote
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

load_dotenv(override=True)

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY", "").strip()
if not secret_key:
    raise RuntimeError("Missing SECRET_KEY in environment. Please set it in .env.")
app.secret_key = secret_key
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv("SESSION_COOKIE_SECURE", "0").strip() == "1"
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = os.getenv("REMEMBER_COOKIE_SECURE", "0").strip() == "1"
DB_NAME = "knives.db"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['GOOGLE_CLIENT_ID'] = os.getenv("GOOGLE_CLIENT_ID", "")
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv("GOOGLE_CLIENT_SECRET", "")
app.config['GOOGLE_REDIRECT_URI'] = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

if os.getenv("TRUST_PROXY", "0").strip() == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

oauth = OAuth(app)


class GoogleOAuthService:
    def __init__(self, oauth_backend, flask_app):
        self._oauth = oauth_backend
        self._app = flask_app
        self._client = None

    def is_configured(self):
        c = self._app.config
        return bool((c.get('GOOGLE_CLIENT_ID') or '').strip() and (c.get('GOOGLE_CLIENT_SECRET') or '').strip())

    def get_client(self):
        if not self.is_configured():
            return None
        if self._client is None:
            self._client = self._oauth.register(
                name='google',
                client_id=self._app.config['GOOGLE_CLIENT_ID'],
                client_secret=self._app.config['GOOGLE_CLIENT_SECRET'],
                server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                client_kwargs={'scope': 'openid email profile'}
            )
        return self._client


google_oauth_service = GoogleOAuthService(oauth, app)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
TRUSTED_STATUSES = {"home", "sold", "need_to_order", "ordered", "on_the_way", "cart"}
TRUSTED_CONFIDENCE = {"low", "medium", "high"}


class LoginRateLimiter:
    def __init__(self, max_attempts=5, window_seconds=900):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts = defaultdict(deque)

    def _prune_old_attempts(self, ip):
        now = time.time()
        bucket = self.attempts[ip]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if not bucket:
            self.attempts.pop(ip, None)

    def is_allowed(self, ip):
        self._prune_old_attempts(ip)
        return len(self.attempts.get(ip, ())) < self.max_attempts

    def register_failure(self, ip):
        self._prune_old_attempts(ip)
        self.attempts[ip].append(time.time())

    def clear_failures(self, ip):
        self.attempts.pop(ip, None)


class AppSecurityService:
    def __init__(self, login_rate_limiter):
        self.login_rate_limiter = login_rate_limiter

    def client_ip(self):
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.remote_addr or "unknown"

    def add_security_headers(self, response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


login_rate_limiter = LoginRateLimiter()
app_security_service = AppSecurityService(login_rate_limiter)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_unique_filename(ext):
    """Generate a unique filename with the given extension."""
    return f"img_{uuid.uuid4().hex[:12]}.{ext}"


STORED_IMAGE_NAME_RE = re.compile(
    r"^img_[0-9a-f]{12}\.(?:png|jpg|jpeg|webp|gif)$", re.IGNORECASE
)


def is_server_stored_image_name(name):
    return bool(name and STORED_IMAGE_NAME_RE.fullmatch(name))


def get_db_connection(timeout=30):
    conn = sqlite3.connect(DB_NAME, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(max(1, timeout) * 1000)}")
    return conn


class KnifeInputService:
    def clean_text(self, value, fallback=""):
        return (value or fallback).strip()

    def clean_float(self, value, fallback=0.0):
        try:
            cleaned = float(str(value).strip())
            return round(max(0.0, cleaned), 2)
        except (TypeError, ValueError):
            return fallback

    def clean_int(self, value, fallback=1, minimum=1):
        try:
            parsed = int(str(value).strip())
            return max(minimum, parsed)
        except (TypeError, ValueError):
            return fallback

    def clean_url(self, value):
        url = self.clean_text(value)
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            return url
        return ""

    def normalize_status(self, value):
        status = self.clean_text(value, "home")
        return status if status in TRUSTED_STATUSES else "home"

    def normalize_confidence(self, value):
        conf = self.clean_text(value, "low")
        return conf if conf in TRUSTED_CONFIDENCE else "low"


knife_input_service = KnifeInputService()


class KnifeAutoLookupService:
    _cache = {}
    _cache_ttl_seconds = 600
    _blocked_domains = {"youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com"}
    _preferred_domains = {
        "bladehq.com": 40,
        "knifecenter.com": 38,
        "smkw.com": 34,
        "dltradingco.com": 20,
        "whitemountainknives.com": 30,
        "dlttrading.com": 24,
        "lamnia.com": 22,
        "knivesplus.com": 20,
        "amazon.com": 12,
        "ebay.com": 8,
    }

    def _from_cache(self, cache_key):
        item = self._cache.get(cache_key)
        if not item:
            return None
        age = (datetime.now(timezone.utc) - item["created_at"]).total_seconds()
        if age > self._cache_ttl_seconds:
            self._cache.pop(cache_key, None)
            return None
        return item["results"]

    def _save_cache(self, cache_key, results):
        self._cache[cache_key] = {"created_at": datetime.now(timezone.utc), "results": results}

    def _extract_price(self, text):
        if not text:
            return None, None
        if "€" in text:
            euro_match = re.search(r"[€]\s*([\d,]+(?:\.\d{1,2})?)", text)
            if euro_match:
                return round(float(euro_match.group(1).replace(",", "")), 2), "EUR"
        shekel_match = re.search(r"[₪]\s*([\d,]+(?:\.\d{1,2})?)", text)
        if shekel_match:
            return round(float(shekel_match.group(1).replace(",", "")), 2), "ILS"
        dollar_match = re.search(r"[$]\s*([\d,]+(?:\.\d{1,2})?)", text)
        if dollar_match:
            return round(float(dollar_match.group(1).replace(",", "")), 2), "USD"
        return None, None

    def _quick_price_hints(self, brand, model):
        hints = []
        q = f"{brand} {model} knife price".strip()
        for row in self._search_bing(q, max_results=10):
            body = row.get("snippet", "") or ""
            title = row.get("title", "") or ""
            href = row.get("href", "") or ""
            price, currency = self._extract_price(f"{title} {body}")
            if not price:
                continue
            domain = self._normalize_domain(href)
            hints.append({
                "domain": domain,
                "price": price,
                "currency": currency,
                "source_url": href
            })
        return hints

    def _normalize_domain(self, url):
        raw = urlparse(url).netloc.lower()
        return raw[4:] if raw.startswith("www.") else raw

    def _search_bing(self, query, max_results=20):
        links = []
        try:
            resp = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "en-US"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            if resp.status_code >= 400:
                return links
            html = resp.text
            blocks = re.findall(r'<li class="b_algo".*?</li>', html, flags=re.IGNORECASE | re.DOTALL)
            for block in blocks:
                href_match = re.search(r'<a href="([^"]+)"', block, flags=re.IGNORECASE)
                title_match = re.search(r"<h2.*?>(.*?)</h2>", block, flags=re.IGNORECASE | re.DOTALL)
                snippet_match = re.search(r'<p>(.*?)</p>', block, flags=re.IGNORECASE | re.DOTALL)
                if not href_match:
                    continue
                href = href_match.group(1).strip()
                title = re.sub(r"<[^>]+>", " ", title_match.group(1)).strip() if title_match else ""
                snippet = re.sub(r"<[^>]+>", " ", snippet_match.group(1)).strip() if snippet_match else ""
                links.append({"href": href, "title": title, "snippet": snippet})
                if len(links) >= max_results:
                    break
        except Exception:
            return links
        return links

    def _run_with_timeout(self, func, timeout_seconds=8):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                return future.result(timeout=timeout_seconds)
            except Exception:
                return []

    def _search_bing_images(self, query, max_results=14):
        results = []
        try:
            resp = requests.get(
                "https://www.bing.com/images/search",
                params={"q": query, "form": "HDRSC3"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            if resp.status_code >= 400:
                return results
            html = resp.text
            blobs = re.findall(r'm="(\{.*?\})"', html, flags=re.IGNORECASE)
            for blob in blobs:
                try:
                    decoded = blob.replace("&quot;", '"')
                    data = json.loads(decoded)
                except Exception:
                    continue
                image_url = (data.get("murl") or "").strip()
                page_url = (data.get("purl") or "").strip()
                title = (data.get("t") or "").strip()
                if not image_url or not page_url:
                    continue
                results.append({"image_url": image_url, "page_url": page_url, "title": title})
                if len(results) >= max_results:
                    break
        except Exception:
            return results
        return results

    def _search_links(self, query, max_results=16):
        links = self._search_bing(query, max_results=max_results)
        if links:
            return links

        ddg_html_url = "https://duckduckgo.com/html/"
        params = {"q": query}
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = requests.get(ddg_html_url, params=params, headers=headers, timeout=6)
            if resp.status_code < 400:
                html = resp.text
                for match in re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE):
                    if "duckduckgo.com/l/?" in match and "uddg=" in match:
                        parsed = urlparse(match)
                        q = parse_qs(parsed.query)
                        target = unquote((q.get("uddg") or [""])[0]).strip()
                        if target.startswith(("http://", "https://")):
                            links.append({"href": target, "title": "", "snippet": ""})
                    elif match.startswith(("http://", "https://")) and "duckduckgo.com" not in match:
                        links.append({"href": match, "title": "", "snippet": ""})
                    if len(links) >= max_results:
                        break
        except Exception:
            pass

        if not links and DDGS is not None:
            def fetch_ddgs_rows():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, backend="lite", max_results=max_results))

            rows = self._run_with_timeout(fetch_ddgs_rows, timeout_seconds=5)
            for row in rows:
                href = (row.get("href") or "").strip()
                title = (row.get("title") or "").strip()
                snippet = (row.get("body") or "").strip()
                if href:
                    links.append({"href": href, "title": title, "snippet": snippet})
        return links

    def _extract_jsonld_product(self, html):
        script_matches = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for raw in script_matches:
            try:
                cleaned = raw.strip()
                if not cleaned:
                    continue
                data = json.loads(cleaned)
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for node in candidates:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("@type", "")).lower()
                if "product" not in node_type:
                    continue
                image = node.get("image")
                if isinstance(image, list):
                    image = image[0] if image else ""
                offers = node.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = offers.get("price") if isinstance(offers, dict) else None
                currency = offers.get("priceCurrency") if isinstance(offers, dict) else None
                try:
                    price = round(float(str(price).replace(",", "")), 2) if price is not None else None
                except Exception:
                    price = None
                return {
                    "title": (node.get("name") or "")[:180],
                    "image_url": image or "",
                    "price": price,
                    "currency": currency
                }
        return None

    def _fetch_og_image(self, page_url):
        if not page_url.startswith(("http://", "https://")):
            return ""
        try:
            resp = requests.get(
                page_url,
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                return ""
            html = resp.text[:250000]
            patterns = [
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, flags=re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        except Exception:
            return ""
        return ""

    def _extract_title(self, html):
        match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()

    def _extract_price_from_html(self, html):
        patterns = [
            r'property=["\']product:price:amount["\']\s+content=["\']([^"\']+)["\']',
            r'"price"\s*:\s*"([\d,]+(?:\.\d{1,2})?)"',
            r'"price"\s*:\s*([\d,]+(?:\.\d{1,2})?)',
        ]
        currency_patterns = [
            r'property=["\']product:price:currency["\']\s+content=["\']([^"\']+)["\']',
            r'"priceCurrency"\s*:\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                raw_price = match.group(1).replace(",", "").strip()
                try:
                    price = round(float(raw_price), 2)
                except Exception:
                    continue
                currency = None
                for cp in currency_patterns:
                    cm = re.search(cp, html, flags=re.IGNORECASE)
                    if cm:
                        currency = cm.group(1).upper().strip()
                        break
                return price, currency
        return None, None

    def _score_candidate(self, candidate, brand, model, attributes):
        haystack = f"{candidate.get('title', '')} {candidate.get('page_url', '')}".lower()
        tokens = [t for t in re.split(r"[\s,\-_]+", f"{brand} {model} {attributes}".lower()) if len(t) > 1]
        score = 0
        for token in tokens:
            if token in haystack:
                score += 4
        domain = self._normalize_domain(candidate.get("page_url", ""))
        score += self._preferred_domains.get(domain, 0)
        if candidate.get("price") is not None:
            score += 10
        if candidate.get("image_url", "").lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".avif")):
            score += 4
        return score

    def _bing_image_candidates(self, query, price_hints, seen_images, max_add):
        out = []
        for item in self._search_bing_images(query, max_results=14):
            image_url = item.get("image_url", "")
            page_url = item.get("page_url", "")
            if not image_url or not page_url or image_url in seen_images:
                continue
            domain = self._normalize_domain(page_url)
            if any(blocked in domain for blocked in self._blocked_domains):
                continue
            seen_images.add(image_url)
            price = None
            currency = None
            source_url = page_url
            for hint in price_hints:
                if hint["domain"] and hint["domain"] == domain:
                    price = hint["price"]
                    currency = hint["currency"]
                    source_url = hint["source_url"] or page_url
                    break
            out.append({
                "title": (item.get("title") or "")[:180],
                "image_url": image_url,
                "page_url": page_url,
                "source_url": source_url,
                "price": price,
                "currency": currency
            })
            if len(out) >= max_add:
                break
        return out

    def search(self, brand, model, attributes):
        base_query = f"{brand} {model} knife".strip()
        full_query = f"{base_query} {attributes}".strip()
        cache_key = f"{brand.lower()}|{model.lower()}|{attributes.lower()}"
        cached = self._from_cache(cache_key)
        if cached is not None:
            return cached

        deadline = time.monotonic() + 18.0
        price_hints = self._quick_price_hints(brand, model)
        candidates = []
        seen_images = set()

        for q in (full_query, base_query):
            if time.monotonic() > deadline or len(candidates) >= 8:
                break
            candidates.extend(self._bing_image_candidates(q, price_hints, seen_images, 8 - len(candidates)))

        if len(candidates) < 6 and time.monotonic() < deadline:
            rows = self._search_links(full_query, max_results=14)
            for row in rows[:10]:
                if time.monotonic() > deadline:
                    break
                page_url = (row.get("href") or "").strip()
                if not page_url.startswith(("http://", "https://")):
                    continue
                domain = self._normalize_domain(page_url)
                if any(blocked in domain for blocked in self._blocked_domains):
                    continue
                try:
                    resp = requests.get(page_url, timeout=4, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
                    if resp.status_code >= 400:
                        continue
                    html = resp.text[:200000]
                except Exception:
                    continue
                product = self._extract_jsonld_product(html)
                title = (row.get("title") or self._extract_title(html))
                image_url = ""
                price = None
                currency = None
                if product:
                    title = product.get("title") or title
                    image_url = product.get("image_url") or ""
                    price = product.get("price")
                    currency = product.get("currency")
                if not image_url:
                    image_url = self._fetch_og_image(page_url)
                if not image_url or image_url in seen_images:
                    continue
                seen_images.add(image_url)
                source_url = page_url
                if price is None:
                    page_price, page_currency = self._extract_price_from_html(html)
                    if page_price is not None:
                        price = page_price
                        currency = page_currency
                if price is None:
                    for hint in price_hints:
                        if hint["domain"] and hint["domain"] == domain:
                            price = hint["price"]
                            currency = hint["currency"]
                            source_url = hint["source_url"] or page_url
                            break
                candidates.append({
                    "title": (title or "")[:180],
                    "image_url": image_url,
                    "page_url": page_url,
                    "source_url": source_url,
                    "price": price,
                    "currency": currency
                })
                if len(candidates) >= 12:
                    break

        if not candidates:
            raise RuntimeError("No good candidates found. Try different attributes (e.g. blade shape or handle material).")

        for c in candidates:
            c["score"] = self._score_candidate(c, brand, model, attributes)
        candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)[:8]
        for c in candidates:
            c.pop("score", None)
        self._save_cache(cache_key, candidates)
        return candidates


knife_auto_lookup_service = KnifeAutoLookupService()

def init_db():
    for attempt in range(30):
        try:
            with closing(get_db_connection(timeout=2)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
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
                        price_confidence TEXT DEFAULT 'low',
                        is_featured INTEGER DEFAULT 0
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
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_email TEXT NOT NULL,
                        action TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        entity_id INTEGER,
                        details TEXT DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('landing_page_enabled', '1')")
                try:
                    conn.execute("ALTER TABLE knives ADD COLUMN is_featured INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                conn.commit()
            return
        except sqlite3.OperationalError as err:
            if "locked" not in str(err).lower() or attempt >= 29:
                raise
            time.sleep(0.15)

def get_setting(key, default=None):
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else default

def set_setting(key, value):
    with closing(get_db_connection()) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def create_csrf_token():
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = create_csrf_token()
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token()}


def verify_csrf():
    should_verify = request.path.startswith("/admin/") or request.is_json
    if should_verify and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token")
        if not expected or not submitted or not secrets.compare_digest(submitted, expected):
            return False
    return True


@app.before_request
def csrf_protect():
    if not verify_csrf():
        if request.path.startswith("/admin/") or request.is_json:
            return jsonify({"error": "CSRF validation failed"}), 400
        flash("Security validation failed. Please retry.", "error")
        return redirect(request.referrer or url_for("index"))


@app.after_request
def apply_security_headers(response):
    return app_security_service.add_security_headers(response)


def write_audit_log(action, entity_type, entity_id=None, details=""):
    actor = (current_user.email if current_user.is_authenticated else None) or "anonymous"
    now = datetime.utcnow().isoformat(timespec="seconds")
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (user_email, action, entity_type, entity_id, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor, action, entity_type, entity_id, details, now),
        )
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
        email = knife_input_service.clean_text(request.form.get("email", "")).lower()
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
        client_ip = app_security_service.client_ip()
        if not login_rate_limiter.is_allowed(client_ip):
            flash("Too many login attempts. Please wait a few minutes and try again.", "error")
            return redirect(url_for("login"))

        email = knife_input_service.clean_text(request.form.get("email", "")).lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        
        conn = get_db_connection()
        user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        
        if user_row and user_row['password_hash'] and bcrypt.check_password_hash(user_row['password_hash'], password):
            login_rate_limiter.clear_failures(client_ip)
            user = User(user_row['id'], user_row['email'], user_row['password_hash'], user_row['google_id'], user_row['is_admin'])
            login_user(user, remember=remember)
            next_url = request.form.get('next') or request.args.get('next')
            if next_url and is_safe_url(next_url):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard") if user.email == ADMIN_EMAIL or user.is_admin else url_for("index"))
        else:
            login_rate_limiter.register_failure(client_ip)
            flash("Invalid email or password.", "error")
            
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

@app.route("/login/google")
def login_google():
    client = google_oauth_service.get_client()
    if not client:
        flash("Google Login is not fully configured (missing credentials).", "error")
        return redirect(url_for('login'))

    redirect_uri = app.config.get('GOOGLE_REDIRECT_URI') or url_for('authorize_google', _external=True)
    return client.authorize_redirect(redirect_uri)

@app.route("/authorize/google")
def authorize_google():
    client = google_oauth_service.get_client()
    if not client:
        flash("Google Login is not fully configured (missing credentials).", "error")
        return redirect(url_for('login'))

    # Handle user cancelling the Google consent screen
    error = request.args.get('error')
    if error:
        flash("Google login was cancelled or failed.", "error")
        return redirect(url_for('login'))

    try:
        token = client.authorize_access_token()
    except Exception as e:
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for('login'))

    user_info = token.get('userinfo')
    if not user_info:
        try:
            user_info = client.userinfo()
        except Exception:
            user_info = None
    if not user_info:
        try:
            user_info = client.parse_id_token(token)
        except Exception:
            user_info = None
    
    email_verified = bool(user_info and user_info.get('email_verified', False))
    if not user_info or not user_info.get('email') or not email_verified:
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
    login_rate_limiter.clear_failures(app_security_service.client_ip())
    
    if user.is_admin or email == ADMIN_EMAIL:
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('index'))


@app.route("/health")
def health():
    return jsonify({"ok": True}), 200


# -----------------------------------------------------
# PUBLIC STOREFRONT
# -----------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    if get_setting('landing_page_enabled') == '0':
        return redirect(url_for('collection'))
        
    conn = get_db_connection()
    featured = conn.execute("SELECT * FROM knives WHERE is_featured = 1 ORDER BY id DESC").fetchall()
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

    landing_page_enabled = get_setting('landing_page_enabled', '1') == '1'

    return render_template(
        "admin_dashboard.html",
        totals=totals,
        counts=counts,
        status_counts=status_counts,
        pricing_stats=pricing_stats,
        landing_page_enabled=landing_page_enabled
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
    write_audit_log("delete_attempt", "knife", knife_id)
    conn = get_db_connection()
    conn.execute("DELETE FROM knives WHERE id = ?", (knife_id,))
    conn.commit()
    conn.close()
    write_audit_log("delete", "knife", knife_id)
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
    data = request.get_json() or {}
    knife_id = knife_input_service.clean_int(data.get("id"), fallback=0, minimum=1)
    new_status = knife_input_service.normalize_status(data.get("status"))
    valid_statuses = [s["key"] for s in STATUSES]
    if not knife_id or new_status not in valid_statuses:
        return jsonify({"error": "Invalid"}), 400
    conn = get_db_connection()
    conn.execute("UPDATE knives SET status = ? WHERE id = ?", (new_status, knife_id))
    conn.commit()
    conn.close()
    write_audit_log("status_update", "knife", knife_id, f"status={new_status}")
    return jsonify({"ok": True, "id": knife_id, "status": new_status})


@app.route("/admin/inventory/update-quantity", methods=["POST"])
@admin_required
def admin_update_quantity():
    data = request.get_json() or {}
    knife_id = knife_input_service.clean_int(data.get("id"), fallback=0, minimum=1)
    quantity = knife_input_service.clean_int(data.get("quantity"), fallback=0, minimum=1)
    if not knife_id or not quantity:
        return jsonify({"error": "Invalid quantity"}), 400
    conn = get_db_connection()
    conn.execute("UPDATE knives SET quantity = ? WHERE id = ?", (quantity, knife_id))
    conn.commit()
    conn.close()
    write_audit_log("quantity_update", "knife", knife_id, f"quantity={quantity}")
    return jsonify({"ok": True, "id": knife_id, "quantity": quantity})


@app.route("/admin/settings/toggle-landing", methods=["POST"])
@admin_required
def admin_toggle_landing():
    current = get_setting('landing_page_enabled', '1')
    new_val = '0' if current == '1' else '1'
    set_setting('landing_page_enabled', new_val)
    write_audit_log("landing_toggle", "settings", None, f"landing_page_enabled={new_val}")
    return jsonify({"ok": True, "enabled": new_val == '1'})


@app.route("/admin/knives/toggle-featured", methods=["POST"])
@admin_required
def admin_toggle_featured():
    data = request.get_json() or {}
    knife_id = knife_input_service.clean_int(data.get("id"), fallback=0, minimum=1)
    if not knife_id:
        return jsonify({"error": "Missing ID"}), 400
        
    conn = get_db_connection()
    knife = conn.execute("SELECT is_featured FROM knives WHERE id = ?", (knife_id,)).fetchone()
    if not knife:
        conn.close()
        return jsonify({"error": "Not found"}), 404
        
    new_status = 1 if not knife['is_featured'] else 0
    conn.execute("UPDATE knives SET is_featured = ? WHERE id = ?", (new_status, knife_id))
    conn.commit()
    conn.close()
    write_audit_log("featured_toggle", "knife", knife_id, f"is_featured={new_status}")
    return jsonify({"ok": True, "id": knife_id, "is_featured": bool(new_status)})


# -----------------------------------------------------

# -----------------------------------------------------
# IMAGE API ENDPOINTS
# -----------------------------------------------------

@app.route("/admin/api/upload-clipboard", methods=["POST"])
@admin_required
def api_upload_clipboard():
    """Handle pasted image from clipboard (base64 data)."""
    data = request.get_json() or {}
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

        write_audit_log("image_upload_clipboard", "media", None, f"filename={filename}")
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
    data = request.get_json() or {}
    if not data or 'url' not in data:
        return jsonify({"error": "No URL provided"}), 400

    image_url = knife_input_service.clean_url(data.get("url"))
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

        write_audit_log("image_upload_url", "media", None, f"filename={filename}")
        return jsonify({
            "success": True,
            "filename": filename,
            "url": url_for('static', filename=f'uploads/{filename}'),
            "source_url": image_url
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/auto-search", methods=["POST"])
@admin_required
def api_auto_search():
    data = request.get_json() or {}
    brand = knife_input_service.clean_text(data.get("brand", ""))
    model = knife_input_service.clean_text(data.get("model", ""))
    attributes = knife_input_service.clean_text(data.get("attributes", ""))
    if not brand or not model:
        return jsonify({"error": "Brand and model are required"}), 400
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(knife_auto_lookup_service.search, brand, model, attributes)
            try:
                candidates = fut.result(timeout=22)
            except concurrent.futures.TimeoutError:
                return jsonify({"ok": False, "error": "Search timed out. Try again or use shorter keywords."}), 504
        write_audit_log("auto_search", "knife", None, f"query={brand} {model} {attributes};count={len(candidates)}")
        return jsonify({"ok": True, "candidates": candidates})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
    mode = knife_input_service.clean_text(request.form.get("mode", ""))
    amount = request.form.get("amount", "0")
    knife_ids = request.form.getlist("knife_ids")

    try:
        amount = knife_input_service.clean_float(amount)
    except Exception:
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
    write_audit_log("bulk_pricing", "knife", None, f"mode={mode};amount={amount};updated={updated}")
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
    brand = knife_input_service.clean_text(req.form.get("brand", ""))
    model = knife_input_service.clean_text(req.form.get("model", ""))
    category = knife_input_service.clean_text(req.form.get("category", ""))
    status = knife_input_service.normalize_status(req.form.get("status", "home"))
    description = knife_input_service.clean_text(req.form.get("description", ""))
    notes = knife_input_service.clean_text(req.form.get("notes", ""))
    is_featured = 1 if req.form.get("is_featured") == "on" else 0

    image_source_url = knife_input_service.clean_url(req.form.get("image_source_url", ""))
    price_source_url = knife_input_service.clean_url(req.form.get("price_source_url", ""))
    data_confidence = knife_input_service.normalize_confidence(req.form.get("data_confidence", "low"))
    price_confidence = knife_input_service.normalize_confidence(req.form.get("price_confidence", "low"))

    msrp_new_price = knife_input_service.clean_float(req.form.get("msrp_new_price", 0))
    cost_price = knife_input_service.clean_float(req.form.get("cost_price", 0))
    sale_price = knife_input_service.clean_float(req.form.get("sale_price", 0))
    
    currency = knife_input_service.clean_text(req.form.get("currency", "ILS"))
    if currency == "USD":
        # Convert to ILS (Approximate rate: 3.2)
        msrp_new_price = round(msrp_new_price * 3.2, 2)
        cost_price = round(cost_price * 3.2, 2)
        sale_price = round(sale_price * 3.2, 2)
    buy_price = cost_price
    estimated_value = msrp_new_price

    quantity = knife_input_service.clean_int(req.form.get("quantity", 1), fallback=1, minimum=1)

    if not brand or not model:
        flash("Brand and model are required.", "error")
        return redirect(request.url)

    # ── Image Handling ──
    # Priority: remove > new upload > hidden (from clipboard/URL JS) > existing
    remove_image = req.form.get("remove_image", "") == "1"
    filename = (existing_knife["image"] or "") if existing_knife else ""
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
    if js_filename and not remove_image and is_server_stored_image_name(js_filename):
        js_path = os.path.join(app.config["UPLOAD_FOLDER"], js_filename)
        if os.path.isfile(js_path):
            filename = js_filename

    conn = get_db_connection()
    audit_action = ""
    audit_entity_id = knife_id
    audit_details = f"{brand} {model}"
    if knife_id is None:
        cur = conn.execute("""
            INSERT INTO knives (brand, model, category, status, buy_price, estimated_value, quantity, notes, image, description,
                image_url, image_source_url, price_source_url, data_confidence,
                msrp_new_price, cost_price, sale_price, price_confidence, is_featured)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (brand, model, category, status, buy_price, estimated_value, quantity, notes, filename, description,
              image_url, image_source_url, price_source_url, data_confidence,
              msrp_new_price, cost_price, sale_price, price_confidence, is_featured))
        audit_action = "create"
        audit_entity_id = cur.lastrowid
    else:
        conn.execute("""
            UPDATE knives
            SET brand=?, model=?, category=?, status=?, buy_price=?, estimated_value=?, quantity=?, notes=?, image=?, description=?,
                image_url=?, image_source_url=?, price_source_url=?, data_confidence=?,
                msrp_new_price=?, cost_price=?, sale_price=?, price_confidence=?, is_featured=?
            WHERE id = ?
        """, (brand, model, category, status, buy_price, estimated_value, quantity, notes, filename, description,
              image_url, image_source_url, price_source_url, data_confidence,
              msrp_new_price, cost_price, sale_price, price_confidence, is_featured, knife_id))
        audit_action = "update"
    conn.commit()
    conn.close()
    if audit_action:
        write_audit_log(audit_action, "knife", audit_entity_id, audit_details)

    return redirect(url_for("admin_knives"))


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0").strip() == "1",
    )
