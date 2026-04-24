import os
import socket
import sqlite3
import uuid
import base64
import secrets
import re
import json
import concurrent.futures
import time
import threading
import urllib.request
import ssl
from datetime import datetime, timezone
from contextlib import closing
from functools import wraps
from collections import defaultdict, deque
from urllib.parse import urlparse, parse_qs, unquote
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, make_response
from werkzeug.exceptions import HTTPException
from werkzeug.routing.exceptions import RoutingException
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from storefront_locale import storefront_locale_service
from knife_finder_service import KnifeFinderService
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
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv("SESSION_COOKIE_SECURE", "0").strip() == "1"
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = os.getenv("REMEMBER_COOKIE_SECURE", "0").strip() == "1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "knives.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_BACKUP_DIR = os.path.join(BASE_DIR, "db_backups")
os.makedirs(DB_BACKUP_DIR, exist_ok=True)


class FlaskListenPortResolver:
    def resolve(self, host, preferred_port, span=48):
        """First bindable port starting at preferred_port (avoids silent clashes on FLASK_PORT)."""
        bind_host = host or "127.0.0.1"
        end = preferred_port + span
        for port in range(preferred_port, end):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind((bind_host, port))
                    return port
                except OSError:
                    continue
        raise RuntimeError(f"No free TCP port on {bind_host!r} from {preferred_port} to {end - 1}")


flask_listen_port_resolver = FlaskListenPortResolver()

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


def ensure_sale_price_history_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sale_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knife_id INTEGER NOT NULL,
            old_sale REAL NOT NULL,
            new_sale REAL NOT NULL,
            source TEXT NOT NULL,
            user_email TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )


def get_db_connection(timeout=30):
    conn = sqlite3.connect(DB_NAME, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(max(1, timeout) * 1000)}")
    ensure_sale_price_history_schema(conn)
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

    def parse_signed_decimal(self, value, fallback=0.0):
        try:
            return round(float(str(value).strip()), 2)
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


class NumberFormatService:
    def format_int(self, value):
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "0"

    def format_money(self, value):
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return "0.00"


number_format_service = NumberFormatService()


class SalePriceHistoryService:
    @staticmethod
    def backup_catalog():
        os.makedirs(DB_BACKUP_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"knives_{stamp}.db"
        dest = os.path.join(DB_BACKUP_DIR, fname)
        try:
            src = sqlite3.connect(DB_NAME, timeout=120)
            try:
                dst = sqlite3.connect(dest)
                try:
                    src.backup(dst)
                    dst.commit()
                finally:
                    dst.close()
            finally:
                src.close()
        except (OSError, sqlite3.Error):
            return None
        return fname

    @staticmethod
    def record(conn, knife_id, old_sale, new_sale, source, user_email):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO sale_price_history (knife_id, old_sale, new_sale, source, user_email, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (knife_id, float(old_sale), float(new_sale), source, (user_email or "").strip(), now),
        )


class BulkPricingService:
    @staticmethod
    def msrp_base(row):
        msrp = float(row["msrp_new_price"] or 0)
        if msrp > 0:
            return msrp
        return float(row["estimated_value"] or 0)

    @staticmethod
    def current_sale(row):
        s = float(row["sale_price"] or 0)
        return s if s > 0 else 0.0

    @staticmethod
    def compute_new_sale(mode, row, amount):
        base = BulkPricingService.msrp_base(row)
        sale = BulkPricingService.current_sale(row)
        if mode == "msrp_fixed":
            if base <= 0:
                return None
            return base + amount
        if mode == "msrp_percent":
            if base <= 0:
                return None
            return base * (1 + amount / 100.0)
        if mode == "sale_percent":
            ref = sale if sale > 0 else base
            if ref <= 0:
                return None
            return ref * (1 + amount / 100.0)
        return None

    @staticmethod
    def snap_to_fifty_grid(value):
        if value is None or value <= 0:
            return 0.0
        return float(int(value / 50.0 + 0.5) * 50)


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

    def _clean_query_text(self, text):
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        return re.sub(r"[\"'`]+", "", normalized)

    def _build_queries(self, brand, model, attributes):
        brand_clean = self._clean_query_text(brand)
        model_clean = self._clean_query_text(model)
        attributes_clean = self._clean_query_text(attributes)
        base = f"{brand_clean} {model_clean}".strip()
        full = f"{base} {attributes_clean}".strip()
        queries = [
            f"{full} knife price".strip(),
            f"{base} knife".strip(),
            f"{base} price".strip(),
            full,
            base,
        ]
        seen = set()
        unique = []
        for q in queries:
            q = re.sub(r"\s+", " ", q).strip()
            if not q:
                continue
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(q)
        return unique

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
            if not links:
                for href in re.findall(r'<a href="(https?://[^"]+)"', html, flags=re.IGNORECASE):
                    clean_href = href.strip()
                    if not clean_href or "bing.com" in clean_href:
                        continue
                    links.append({"href": clean_href, "title": "", "snippet": ""})
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
                page_url = (data.get("purl") or data.get("surl") or "").strip()
                title = (data.get("t") or "").strip()
                if not image_url:
                    continue
                results.append({"image_url": image_url, "page_url": page_url, "title": title})
                if len(results) >= max_results:
                    break
            if not results:
                raw_image_urls = re.findall(
                    r'https?://[^"\']+\.(?:jpg|jpeg|png|webp|avif)(?:\?[^"\']*)?',
                    html,
                    flags=re.IGNORECASE,
                )
                seen = set()
                for image_url in raw_image_urls:
                    key = image_url.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({"image_url": image_url, "page_url": "", "title": ""})
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
            if not image_url or image_url in seen_images:
                continue
            domain = self._normalize_domain(page_url) if page_url else ""
            if domain and any(blocked in domain for blocked in self._blocked_domains):
                continue
            seen_images.add(image_url)
            price = None
            currency = None
            source_url = page_url or image_url
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

    def _collect_candidates_from_query(self, query, seen_images, price_hints, cap):
        out = []
        if cap <= 0:
            return out
        out.extend(self._bing_image_candidates(query, price_hints, seen_images, cap))
        if len(out) >= cap:
            return out[:cap]
        rows = self._search_links(query, max_results=20)
        for row in rows[:16]:
            if len(out) >= cap:
                break
            page_url = (row.get("href") or "").strip()
            if not page_url.startswith(("http://", "https://")):
                continue
            domain = self._normalize_domain(page_url)
            if any(blocked in domain for blocked in self._blocked_domains):
                continue
            try:
                resp = requests.get(page_url, timeout=5, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
                if resp.status_code >= 400:
                    continue
                html = resp.text[:260000]
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
            out.append({
                "title": (title or "")[:180],
                "image_url": image_url,
                "page_url": page_url,
                "source_url": source_url,
                "price": price,
                "currency": currency
            })
        return out

    def search(self, brand, model, attributes):
        queries = self._build_queries(brand, model, attributes)
        cache_key = f"{brand.lower()}|{model.lower()}|{attributes.lower()}"
        cached = self._from_cache(cache_key)
        if cached is not None:
            return cached

        deadline = time.monotonic() + 24.0
        price_hints = self._quick_price_hints(brand, model)
        candidates = []
        seen_images = set()

        for q in queries:
            if time.monotonic() > deadline or len(candidates) >= 16:
                break
            cap = 16 - len(candidates)
            candidates.extend(self._collect_candidates_from_query(q, seen_images, price_hints, cap))

        if not candidates and queries:
            broad = self._clean_query_text(f"{brand} {model}")
            candidates.extend(self._collect_candidates_from_query(broad, seen_images, price_hints, 8))

        for c in candidates:
            c["score"] = self._score_candidate(c, brand, model, attributes)
        candidates = sorted(candidates, key=lambda c: c["score"], reverse=True)[:8]
        for c in candidates:
            c.pop("score", None)
        self._save_cache(cache_key, candidates)
        return candidates


knife_auto_lookup_service = KnifeAutoLookupService()


class ShipmentTrackingService:
    def __init__(self):
        self._endpoint = "https://apimftprd.israelpost.co.il/MyPost-itemtrace/items/{item_code}/heb"
        self._subscription_key = os.getenv("ISRAEL_POST_SUBSCRIPTION_KEY", "5ccb5b137e7444d885be752eda7f767a").strip()

    def normalize_item_code(self, value):
        code = re.sub(r"[^A-Za-z0-9]", "", (value or "").strip().upper())
        return code

    def fetch_tracking_data(self, item_code):
        clean_code = self.normalize_item_code(item_code)
        if not clean_code:
            raise ValueError("Item code is required.")
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7,ar;q=0.6",
            "authorization": "Bearer null",
            "cache-control": "no-cache",
            "ocp-apim-subscription-key": self._subscription_key,
            "origin": "https://doar.israelpost.co.il",
            "pragma": "no-cache",
            "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
        }
        response = requests.get(
            self._endpoint.format(item_code=clean_code),
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected tracking response format.")
        return payload

    def parse_summary(self, payload):
        maslul = payload.get("Maslul") or []
        latest_event = maslul[-1] if isinstance(maslul, list) and maslul else {}
        status_for_display = (payload.get("StatusForDisplay") or "").strip()
        if not status_for_display and isinstance(latest_event, dict):
            status_for_display = (latest_event.get("Status") or latest_event.get("StatusDesc") or "").strip()
        return {
            "item_code": (payload.get("ItemCode") or "").strip(),
            "category_name": (payload.get("CategoryName") or "").strip(),
            "category_icon": (payload.get("CategoryIcon") or "").strip(),
            "status_for_display": status_for_display,
            "sender_name": (payload.get("SenderName") or "").strip(),
            "delivery_type_desc": (payload.get("DeliveryTypeDesc") or "").strip(),
            "delivery_type_icon": (payload.get("DeliveryTypeIcon") or "").strip(),
            "last_event_desc": (latest_event.get("StatusDesc") or latest_event.get("Status") or "").strip() if isinstance(latest_event, dict) else "",
            "last_event_branch": (latest_event.get("BranchName") or "").strip() if isinstance(latest_event, dict) else "",
            "last_event_city": (latest_event.get("City") or "").strip() if isinstance(latest_event, dict) else "",
            "last_event_icon": (latest_event.get("CategoryIcon") or "").strip() if isinstance(latest_event, dict) else "",
        }

    def parse_timeline(self, payload):
        events = []
        maslul = payload.get("Maslul") or []
        if not isinstance(maslul, list):
            return events
        for event in maslul:
            if not isinstance(event, dict):
                continue
            events.append(
                {
                    "date": (event.get("StatusDate") or event.get("EventDate") or event.get("Date") or "").strip(),
                    "category": (event.get("CategoryName") or "").strip(),
                    "icon": (event.get("CategoryIcon") or "").strip(),
                    "status": (event.get("StatusDesc") or event.get("Status") or "").strip(),
                    "branch": (event.get("BranchName") or "").strip(),
                    "city": (event.get("City") or "").strip(),
                }
            )
        return events


shipment_tracking_service = ShipmentTrackingService()
shipment_refresh_lock = threading.Lock()


def refresh_all_shipments():
    refreshed = 0
    failed = 0
    with shipment_refresh_lock:
        conn = get_db_connection()
        rows = conn.execute("SELECT id, item_code FROM shipment_tracking ORDER BY id").fetchall()
        conn.close()
        now = datetime.utcnow().isoformat(timespec="seconds")
        for row in rows:
            shipment_id = row["id"]
            item_code = (row["item_code"] or "").strip()
            if not item_code:
                failed += 1
                continue
            try:
                payload = shipment_tracking_service.fetch_tracking_data(item_code)
                summary = shipment_tracking_service.parse_summary(payload)
                conn = get_db_connection()
                conn.execute(
                    """
                    UPDATE shipment_tracking
                    SET category_name = ?, status_for_display = ?, sender_name = ?, delivery_type_desc = ?,
                        last_event_desc = ?, last_event_branch = ?, last_event_city = ?, raw_payload = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        summary["category_name"],
                        summary["status_for_display"],
                        summary["sender_name"],
                        summary["delivery_type_desc"],
                        summary["last_event_desc"],
                        summary["last_event_branch"],
                        summary["last_event_city"],
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        shipment_id,
                    ),
                )
                conn.commit()
                conn.close()
                refreshed += 1
            except Exception:
                failed += 1
    return refreshed, failed


class BladeMetalSuggestionService:
    _STEELS = tuple(
        sorted(
            {
                "m390",
                "s90v",
                "s30v",
                "s35vn",
                "vg10",
                "154cm",
                "14c28n",
                "n690",
                "lc200n",
                "magnacut",
                "bd1n",
                "20cv",
                "elmax",
                "1095",
                "52100",
                "m4",
                "cpm-3v",
                "3v",
                "4v",
                "d2",
                "a2",
                "o1",
            }
        )
    )

    @staticmethod
    def suggest(texts, limit=12):
        blob = " ".join((t or "") for t in (texts or [])).lower()
        hits = []
        for steel in BladeMetalSuggestionService._STEELS:
            pat = r"\b" + re.escape(steel) + r"\b"
            c = len(re.findall(pat, blob, flags=re.IGNORECASE))
            if c:
                hits.append((c, steel))
        hits.sort(key=lambda x: (-x[0], x[1]))
        out = [{"steel": s.upper() if s in ("m4", "d2", "a2", "o1") else (s[0].upper() + s[1:]), "hits": c} for c, s in hits[:limit]]
        return out


class AdminImageImportService:
    @staticmethod
    def download_to_uploads(image_url):
        image_url = knife_input_service.clean_url(image_url)
        if not image_url.startswith(("http://", "https://")):
            raise ValueError("Invalid URL")

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            content_type = resp.headers.get("Content-Type", "")
            img_bytes = resp.read()

        if len(img_bytes) < 500:
            raise ValueError("Downloaded file too small, likely not an image")

        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"
        elif "gif" in content_type:
            ext = "gif"
        elif ".png" in image_url.lower():
            ext = "png"
        elif ".webp" in image_url.lower():
            ext = "webp"
        elif ".gif" in image_url.lower():
            ext = "gif"

        filename = generate_unique_filename(ext)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        with open(filepath, "wb") as f:
            f.write(img_bytes)

        return {
            "filename": filename,
            "url": url_for("static", filename=f"uploads/{filename}"),
            "source_url": image_url,
        }


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
                    CREATE TABLE IF NOT EXISTS shipment_tracking (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        item_code TEXT NOT NULL UNIQUE,
                        nickname TEXT DEFAULT '',
                        category_name TEXT DEFAULT '',
                        status_for_display TEXT DEFAULT '',
                        sender_name TEXT DEFAULT '',
                        delivery_type_desc TEXT DEFAULT '',
                        last_event_desc TEXT DEFAULT '',
                        last_event_branch TEXT DEFAULT '',
                        last_event_city TEXT DEFAULT '',
                        raw_payload TEXT DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
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
                try:
                    conn.execute("ALTER TABLE knives ADD COLUMN blade_metals TEXT DEFAULT '[]'")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE knives ADD COLUMN blade_length_cm REAL NOT NULL DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    conn.execute("ALTER TABLE shipment_tracking ADD COLUMN nickname TEXT DEFAULT ''")
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


@app.context_processor
def inject_storefront_i18n():
    lang = storefront_locale_service.get_lang()

    def tr(key):
        return storefront_locale_service.translate(key, lang)

    def _row_blade_cm_positive(row):
        if row is None:
            return None
        try:
            raw = row["blade_length_cm"]
        except (KeyError, TypeError, IndexError):
            return None
        try:
            n = float(raw)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    def blade_length_parts(cm):
        return storefront_locale_service.blade_length_parts(cm, lang)

    def blade_length_card_line(cm):
        return storefront_locale_service.blade_length_card_line(cm, lang)

    def blade_length_parts_knife(row):
        cm = _row_blade_cm_positive(row)
        return storefront_locale_service.blade_length_parts(cm, lang) if cm is not None else None

    def blade_length_card_line_knife(row):
        cm = _row_blade_cm_positive(row)
        return storefront_locale_service.blade_length_card_line(cm, lang) if cm is not None else ""

    return {
        "ui_lang": lang,
        "ui_dir": "rtl" if lang == "he" else "ltr",
        "tr": tr,
        "blade_length_parts": blade_length_parts,
        "blade_length_card_line": blade_length_card_line,
        "blade_length_parts_knife": blade_length_parts_knife,
        "blade_length_card_line_knife": blade_length_card_line_knife,
        "fmt_int": number_format_service.format_int,
        "fmt_money": number_format_service.format_money,
    }


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


def _write_error_log(exc):
    try:
        from flask import has_request_context
        path = "?"
        if has_request_context():
            try:
                path = request.path
            except RuntimeError:
                path = "?"
        log_path = os.path.join(BASE_DIR, "knife_inventory_errors.log")
        import traceback
        tb = getattr(exc, "__traceback__", None)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n--- {datetime.now(timezone.utc).isoformat()} {path!r} ---\n")
            fh.writelines(traceback.format_exception(type(exc), exc, tb))
        app.logger.exception("Error on %s", path, exc_info=exc)
    except Exception:
        pass


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


@app.route("/set-lang/<code>")
def set_site_language(code):
    code = (code or "").strip().lower()
    if code not in ("en", "he"):
        code = "en"
    next_url = request.args.get("next") or url_for("index")
    safe = storefront_locale_service.safe_internal_path(next_url)
    target = safe if safe else url_for("index")
    resp = make_response(redirect(target))
    resp.set_cookie(
        storefront_locale_service.COOKIE,
        code,
        max_age=storefront_locale_service.COOKIE_MAX_AGE,
        httponly=False,
        samesite="Lax",
        path="/",
    )
    return resp


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


def _finder_knife_image_src(row):
    url = (row["image_url"] or "").strip()
    if url:
        return url
    img = (row["image"] or "").strip()
    if img:
        return url_for("static", filename=f"uploads/{img}")
    return ""


@app.route("/find-knife", methods=["GET"])
def find_knife():
    return render_template("finder.html", finder_questions=KnifeFinderService.QUESTIONS)


@app.route("/find-knife/match", methods=["POST"])
def find_knife_match():
    payload = request.get_json(silent=True) or {}
    answers = {k: payload.get(k) for k in ("use", "blade", "budget", "steel", "size")}
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, brand, model, category, description, sale_price, msrp_new_price,
               status, quantity, image, image_url, is_featured, blade_metals
        FROM knives
        """
    ).fetchall()
    conn.close()
    picked = KnifeFinderService.recommend(rows, answers, limit=3)
    cards = []
    for row in picked:
        cards.append(
            {
                "id": row["id"],
                "brand": row["brand"] or "",
                "model": row["model"] or "",
                "image": _finder_knife_image_src(row),
                "url": url_for("product", knife_id=row["id"]),
            }
        )
    return jsonify({"knives": cards})


# -----------------------------------------------------
# ADMIN DASHBOARD
# -----------------------------------------------------

@app.route("/admin", methods=["GET"])
@admin_required
def admin_dashboard():
    auto_refreshed, auto_failed = refresh_all_shipments()

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
    shipment_rows_db = conn.execute(
        """
        SELECT id, item_code, nickname, category_name, status_for_display, sender_name, delivery_type_desc,
               last_event_desc, last_event_branch, last_event_city, raw_payload, updated_at
        FROM shipment_tracking
        ORDER BY updated_at DESC, id DESC
        """
    ).fetchall()

    conn.close()
    shipment_rows = []
    for row in shipment_rows_db:
        row_dict = dict(row)
        payload = {}
        raw_payload = (row_dict.get("raw_payload") or "").strip()
        if raw_payload:
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                payload = {}
        live_summary = shipment_tracking_service.parse_summary(payload) if payload else {}
        if live_summary:
            row_dict["category_name"] = live_summary.get("category_name") or row_dict.get("category_name") or ""
            row_dict["status_for_display"] = live_summary.get("status_for_display") or row_dict.get("status_for_display") or ""
            row_dict["sender_name"] = live_summary.get("sender_name") or row_dict.get("sender_name") or ""
            row_dict["delivery_type_desc"] = live_summary.get("delivery_type_desc") or row_dict.get("delivery_type_desc") or ""
            row_dict["last_event_desc"] = live_summary.get("last_event_desc") or row_dict.get("last_event_desc") or ""
            row_dict["last_event_branch"] = live_summary.get("last_event_branch") or row_dict.get("last_event_branch") or ""
            row_dict["last_event_city"] = live_summary.get("last_event_city") or row_dict.get("last_event_city") or ""
        row_dict["category_icon"] = live_summary.get("category_icon", "") if live_summary else ""
        row_dict["delivery_type_icon"] = live_summary.get("delivery_type_icon", "") if live_summary else ""
        row_dict["last_event_icon"] = live_summary.get("last_event_icon", "") if live_summary else ""
        row_dict["timeline_events"] = shipment_tracking_service.parse_timeline(payload)
        shipment_rows.append(row_dict)

    landing_page_enabled = get_setting('landing_page_enabled', '1') == '1'

    return render_template(
        "admin_dashboard.html",
        totals=totals,
        counts=counts,
        status_counts=status_counts,
        pricing_stats=pricing_stats,
        shipment_rows=shipment_rows,
        landing_page_enabled=landing_page_enabled,
        auto_refreshed=auto_refreshed,
        auto_failed=auto_failed,
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


class AdminInventoryFilterService:
    VALID_MISSING = frozenset({"blade_length", "blade_metals", "image", "sale_price", "description"})

    @classmethod
    def append_missing_sql(cls, query, params, missing_key):
        if not missing_key or missing_key not in cls.VALID_MISSING:
            return query, params
        p = list(params)
        if missing_key == "blade_length":
            query += " AND (COALESCE(blade_length_cm, 0) <= 0)"
        elif missing_key == "blade_metals":
            query += " AND (blade_metals IS NULL OR TRIM(blade_metals) IN ('', '[]'))"
        elif missing_key == "image":
            query += " AND ((TRIM(COALESCE(image, '')) = '') AND (TRIM(COALESCE(image_url, '')) = ''))"
        elif missing_key == "sale_price":
            query += " AND (COALESCE(sale_price, 0) <= 0)"
        elif missing_key == "description":
            query += " AND (description IS NULL OR TRIM(description) = '')"
        return query, p


@app.route("/admin/inventory", methods=["GET"])
@admin_required
def admin_inventory():
    brand_filter = request.args.get("brand", "").strip()
    status_filter = request.args.get("status", "").strip()
    search_q = request.args.get("q", "").strip()
    missing_filter = request.args.get("missing", "").strip()

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
    query, params = AdminInventoryFilterService.append_missing_sql(query, params, missing_filter)

    query += " ORDER BY brand, model"
    knives = conn.execute(query, params).fetchall()
    conn.close()

    return render_template("admin_inventory.html",
                           knives=knives,
                           brands=brands,
                           statuses=STATUSES,
                           brand_filter=brand_filter,
                           status_filter=status_filter,
                           search_q=search_q,
                           missing_filter=missing_filter)


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


@app.route("/admin/shipments/track", methods=["POST"])
@admin_required
def admin_track_shipment():
    item_code = shipment_tracking_service.normalize_item_code(request.form.get("item_code", ""))
    if not item_code:
        flash("Please enter a valid shipment item code.", "error")
        return redirect(url_for("admin_dashboard"))
    try:
        payload = shipment_tracking_service.fetch_tracking_data(item_code)
        summary = shipment_tracking_service.parse_summary(payload)
    except requests.RequestException:
        flash("Could not fetch shipment from Israel Post API right now.", "error")
        return redirect(url_for("admin_dashboard"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_dashboard"))

    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO shipment_tracking (
            item_code, category_name, status_for_display, sender_name, delivery_type_desc,
            last_event_desc, last_event_branch, last_event_city, raw_payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_code) DO UPDATE SET
            category_name=excluded.category_name,
            status_for_display=excluded.status_for_display,
            sender_name=excluded.sender_name,
            delivery_type_desc=excluded.delivery_type_desc,
            last_event_desc=excluded.last_event_desc,
            last_event_branch=excluded.last_event_branch,
            last_event_city=excluded.last_event_city,
            raw_payload=excluded.raw_payload,
            updated_at=excluded.updated_at
        """,
        (
            summary["item_code"] or item_code,
            summary["category_name"],
            summary["status_for_display"],
            summary["sender_name"],
            summary["delivery_type_desc"],
            summary["last_event_desc"],
            summary["last_event_branch"],
            summary["last_event_city"],
            json.dumps(payload, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    write_audit_log("shipment_track", "shipment", None, f"item_code={item_code}")
    flash(f"Shipment {item_code} tracked successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/shipments/<int:shipment_id>/refresh", methods=["POST"])
@admin_required
def admin_refresh_shipment(shipment_id):
    conn = get_db_connection()
    row = conn.execute("SELECT id, item_code FROM shipment_tracking WHERE id = ?", (shipment_id,)).fetchone()
    conn.close()
    if not row:
        flash("Shipment not found.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        payload = shipment_tracking_service.fetch_tracking_data(row["item_code"])
        summary = shipment_tracking_service.parse_summary(payload)
    except requests.RequestException:
        flash("Could not refresh shipment from Israel Post API right now.", "error")
        return redirect(url_for("admin_dashboard"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_dashboard"))

    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = get_db_connection()
    conn.execute(
        """
        UPDATE shipment_tracking
        SET category_name = ?, status_for_display = ?, sender_name = ?, delivery_type_desc = ?,
            last_event_desc = ?, last_event_branch = ?, last_event_city = ?, raw_payload = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            summary["category_name"],
            summary["status_for_display"],
            summary["sender_name"],
            summary["delivery_type_desc"],
            summary["last_event_desc"],
            summary["last_event_branch"],
            summary["last_event_city"],
            json.dumps(payload, ensure_ascii=False),
            now,
            shipment_id,
        ),
    )
    conn.commit()
    conn.close()
    write_audit_log("shipment_refresh", "shipment", shipment_id, f"item_code={row['item_code']}")
    flash(f"Shipment {row['item_code']} refreshed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/shipments/<int:shipment_id>/delete", methods=["POST"])
@admin_required
def admin_delete_shipment(shipment_id):
    conn = get_db_connection()
    row = conn.execute("SELECT item_code FROM shipment_tracking WHERE id = ?", (shipment_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM shipment_tracking WHERE id = ?", (shipment_id,))
        conn.commit()
    conn.close()
    if row:
        write_audit_log("shipment_delete", "shipment", shipment_id, f"item_code={row['item_code']}")
        flash(f"Shipment {row['item_code']} removed.", "success")
    else:
        flash("Shipment not found.", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/shipments/<int:shipment_id>/set-nickname", methods=["POST"])
@admin_required
def admin_set_shipment_nickname(shipment_id):
    nickname = knife_input_service.clean_text(request.form.get("nickname", ""))[:80]
    conn = get_db_connection()
    row = conn.execute("SELECT item_code FROM shipment_tracking WHERE id = ?", (shipment_id,)).fetchone()
    if not row:
        conn.close()
        flash("Shipment not found.", "error")
        return redirect(url_for("admin_dashboard"))
    conn.execute("UPDATE shipment_tracking SET nickname = ? WHERE id = ?", (nickname, shipment_id))
    conn.commit()
    conn.close()
    write_audit_log("shipment_nickname_update", "shipment", shipment_id, f"item_code={row['item_code']};nickname={nickname}")
    flash("Shipment nickname saved.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/shipments/refresh-all", methods=["POST"])
@admin_required
def admin_refresh_all_shipments():
    refreshed, failed = refresh_all_shipments()
    write_audit_log("shipment_refresh_all", "shipment", None, f"refreshed={refreshed};failed={failed}")
    flash(f"Refresh all completed. Updated: {refreshed}, failed: {failed}.", "success")
    return redirect(url_for("admin_dashboard"))


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

    try:
        out = AdminImageImportService.download_to_uploads(data.get("url"))
        write_audit_log("image_upload_url", "media", None, f"filename={out['filename']}")
        return jsonify({
            "success": True,
            "filename": out["filename"],
            "url": out["url"],
            "source_url": out["source_url"],
        })
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/auto-search", methods=["POST"])
@admin_required
def api_auto_search():
    data = request.get_json() or {}
    brand = knife_input_service.clean_text(data.get("brand", ""))
    model = knife_input_service.clean_text(data.get("model", ""))
    attributes = knife_input_service.clean_text(data.get("attributes", ""))
    search_only_metals = bool(data.get("search_only_metals"))
    if not brand or not model:
        return jsonify({"error": "Brand and model are required"}), 400
    try:
        candidates = []
        if not search_only_metals:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(knife_auto_lookup_service.search, brand, model, attributes)
                try:
                    candidates = fut.result(timeout=22)
                except concurrent.futures.TimeoutError:
                    return jsonify({"ok": False, "error": "Search timed out. Try again or use shorter keywords."}), 504
        write_audit_log(
            "auto_search",
            "knife",
            None,
            f"query={brand} {model} {attributes};only_metals={int(search_only_metals)};count={len(candidates)}",
        )
        texts = [attributes] + [c.get("title") or "" for c in (candidates or [])]
        metals = BladeMetalSuggestionService.suggest(texts)
        return jsonify({"ok": True, "candidates": candidates, "metal_candidates": metals})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# -----------------------------------------------------
# BULK CHANGE TOOL
# -----------------------------------------------------

@app.route("/admin/bulk-change", methods=["GET"])
@admin_required
def admin_bulk_change():
    conn = get_db_connection()
    knives = conn.execute(
        """
        SELECT id, brand, model, category, description, status, quantity, image, image_url, blade_metals
        FROM knives
        ORDER BY brand, model
        """
    ).fetchall()
    conn.close()

    rows = []
    for k in knives:
        img = (k["image"] or "").strip()
        metals = (k["blade_metals"] or "").strip()
        missing_image = (not img) and (not (k["image_url"] or "").strip())
        missing_metals = (not metals) or (metals == "[]")
        rows.append(
            {
                "id": k["id"],
                "brand": k["brand"],
                "model": k["model"],
                "category": k["category"] or "",
                "status": k["status"] or "",
                "quantity": k["quantity"] or 0,
                "image": img,
                "image_url": (k["image_url"] or "").strip(),
                "blade_metals": metals if metals else "[]",
                "missing_image": missing_image,
                "missing_blade_metals": missing_metals,
            }
        )

    return render_template("admin_bulk_change.html", knives=rows)


@app.route("/admin/api/bulk-change/apply", methods=["POST"])
@admin_required
def api_bulk_change_apply():
    data = request.get_json() or {}
    knife_id = data.get("knife_id")
    if not knife_id:
        return jsonify({"ok": False, "error": "Missing knife_id"}), 400
    try:
        knife_id = int(knife_id)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid knife_id"}), 400

    apply_image = bool(data.get("apply_image"))
    apply_metals = bool(data.get("apply_metals"))
    image_url = knife_input_service.clean_url(data.get("image_url", ""))
    page_url = knife_input_service.clean_url(data.get("page_url", "")) or image_url
    metals = data.get("blade_metals") or []
    if not isinstance(metals, list):
        return jsonify({"ok": False, "error": "blade_metals must be a list"}), 400
    clean_metals = []
    for m in metals:
        s = knife_input_service.clean_text(str(m))
        if not s:
            continue
        if s.lower() in {x.lower() for x in clean_metals}:
            continue
        clean_metals.append(s)

    conn = get_db_connection()
    row = conn.execute("SELECT id, brand, model, image, blade_metals FROM knives WHERE id = ?", (knife_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "Knife not found"}), 404

    updates = {}
    if apply_image and image_url:
        try:
            out = AdminImageImportService.download_to_uploads(image_url)
        except ValueError as ve:
            conn.close()
            return jsonify({"ok": False, "error": str(ve)}), 400
        updates["image"] = out["filename"]
        updates["image_url"] = ""
        updates["image_source_url"] = page_url or out["source_url"]

    if apply_metals:
        updates["blade_metals"] = json.dumps(clean_metals[:24])

    if updates:
        sets = ", ".join([f"{k} = ?" for k in updates.keys()])
        params = list(updates.values()) + [knife_id]
        conn.execute(f"UPDATE knives SET {sets} WHERE id = ?", params)
        conn.commit()

    conn.close()
    write_audit_log(
        "bulk_change_apply",
        "knife",
        knife_id,
        f"apply_image={int(apply_image)};apply_metals={int(apply_metals)};metals={len(clean_metals)}",
    )
    return jsonify({"ok": True, "updated": list(updates.keys())})


# -----------------------------------------------------
# BULK PRICING TOOL
# -----------------------------------------------------

@app.route("/admin/bulk-pricing", methods=["GET"])
@admin_required
def admin_bulk_pricing():
    conn = get_db_connection()
    knives = conn.execute(
        "SELECT id, brand, model, msrp_new_price, estimated_value, cost_price, sale_price, quantity FROM knives ORDER BY brand, model"
    ).fetchall()
    conn.close()
    return render_template("admin_bulk_pricing.html", knives=knives)


@app.route("/admin/bulk-pricing/apply", methods=["POST"])
@admin_required
def admin_bulk_pricing_apply():
    mode = knife_input_service.clean_text(request.form.get("mode", ""))
    amount = knife_input_service.parse_signed_decimal(request.form.get("amount", "0"))
    apply_all = request.form.get("apply_all") == "1"
    knife_ids = request.form.getlist("knife_ids")
    if apply_all:
        conn_ids = get_db_connection()
        knife_ids = [str(r["id"]) for r in conn_ids.execute("SELECT id FROM knives ORDER BY id").fetchall()]
        conn_ids.close()
    if mode not in {"msrp_fixed", "msrp_percent", "sale_percent"}:
        flash("Select a pricing mode.", "error")
        return redirect(url_for("admin_bulk_pricing"))
    if not knife_ids:
        flash("No knives selected.", "error")
        return redirect(url_for("admin_bulk_pricing"))
    SalePriceHistoryService.backup_catalog()
    actor = (current_user.email if current_user.is_authenticated else "") or ""
    conn = get_db_connection()
    updated = 0
    for kid_raw in knife_ids:
        kid = int(kid_raw)
        row = conn.execute(
            "SELECT id, msrp_new_price, sale_price, estimated_value FROM knives WHERE id = ?",
            (kid,),
        ).fetchone()
        if not row:
            continue
        raw_sale = BulkPricingService.compute_new_sale(mode, row, amount)
        if raw_sale is None:
            continue
        new_sale = round(float(raw_sale), 2)
        if new_sale <= 0:
            continue
        old_sale = float(row["sale_price"] or 0)
        conn.execute("UPDATE knives SET sale_price = ? WHERE id = ?", (new_sale, kid))
        SalePriceHistoryService.record(conn, kid, old_sale, new_sale, f"bulk:{mode}", actor)
        updated += 1
    conn.commit()
    conn.close()
    write_audit_log("bulk_pricing", "knife", None, f"mode={mode};amount={amount};updated={updated};apply_all={int(apply_all)}")
    flash(f"Updated sale price for {updated} knives.")
    return redirect(url_for("admin_bulk_pricing"))


@app.route("/admin/bulk-pricing/reset-to-msrp", methods=["POST"])
@admin_required
def admin_bulk_pricing_reset_to_msrp():
    SalePriceHistoryService.backup_catalog()
    actor = (current_user.email if current_user.is_authenticated else "") or ""
    conn = get_db_connection()
    rows = conn.execute("SELECT id, msrp_new_price, estimated_value, sale_price FROM knives").fetchall()
    updated = 0
    for row in rows:
        base = BulkPricingService.msrp_base(row)
        if base <= 0:
            continue
        new_sale = round(base, 2)
        old_sale = float(row["sale_price"] or 0)
        if round(old_sale, 2) == new_sale:
            continue
        conn.execute("UPDATE knives SET sale_price = ? WHERE id = ?", (new_sale, row["id"]))
        SalePriceHistoryService.record(conn, row["id"], old_sale, new_sale, "reset_msrp", actor)
        updated += 1
    conn.commit()
    conn.close()
    write_audit_log("bulk_pricing_reset_msrp", "knife", None, f"updated={updated}")
    flash(f"Reset sale price to MSRP base for {updated} knives.")
    return redirect(url_for("admin_bulk_pricing"))


@app.route("/admin/sale-price-history", methods=["GET"])
@admin_required
def admin_sale_price_history():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT h.id, h.knife_id, h.old_sale, h.new_sale, h.source, h.user_email, h.created_at,
               k.brand, k.model
        FROM sale_price_history h
        LEFT JOIN knives k ON k.id = h.knife_id
        ORDER BY h.id DESC
        LIMIT 500
        """
    ).fetchall()
    conn.close()
    backup_files = []
    if os.path.isdir(DB_BACKUP_DIR):
        backup_files = sorted(
            [f for f in os.listdir(DB_BACKUP_DIR) if f.endswith(".db")],
            reverse=True,
        )[:40]
    return render_template(
        "admin_sale_price_history.html",
        rows=rows,
        backup_files=backup_files,
        backup_dir=DB_BACKUP_DIR,
    )


@app.route("/admin/snap-prices-to-grid", methods=["POST"])
@admin_required
def admin_snap_prices_to_grid():
    SalePriceHistoryService.backup_catalog()
    actor = (current_user.email if current_user.is_authenticated else "") or ""
    conn = get_db_connection()
    rows = conn.execute("SELECT id, sale_price, msrp_new_price, estimated_value FROM knives").fetchall()
    for row in rows:
        kid = row["id"]
        old_sale = float(row["sale_price"] or 0)
        new_sale = BulkPricingService.snap_to_fifty_grid(old_sale)
        new_msrp = BulkPricingService.snap_to_fifty_grid(float(row["msrp_new_price"] or 0))
        new_est = BulkPricingService.snap_to_fifty_grid(float(row["estimated_value"] or 0))
        conn.execute(
            "UPDATE knives SET sale_price = ?, msrp_new_price = ?, estimated_value = ? WHERE id = ?",
            (new_sale, new_msrp, new_est, kid),
        )
        if round(old_sale, 2) != round(new_sale, 2):
            SalePriceHistoryService.record(conn, kid, old_sale, new_sale, "snap_grid", actor)
    conn.commit()
    conn.close()
    write_audit_log("snap_prices_grid", "knife", None, "all_knives")
    flash("Snapped sale, MSRP, and estimated values to the nearest ₪50.")
    return redirect(request.referrer or url_for("admin_bulk_pricing"))


# -----------------------------------------------------
# PRICE EXPORT TOOL
# -----------------------------------------------------

class AdminExportService:
    NO_BRAND_VALUES = {"", "no brand", "unknown", "none"}

    def clean_mode(self, value):
        mode = knife_input_service.clean_text(value, "price").lower()
        return mode if mode in {"price", "images", "full"} else "price"

    def clean_bool(self, value):
        return str(value).strip() in {"1", "true", "on", "yes"}

    def brand_is_missing(self, brand):
        return knife_input_service.clean_text(brand).lower() in self.NO_BRAND_VALUES

    def resolve_price(self, knife_row):
        sale = knife_row["sale_price"] if knife_row["sale_price"] and knife_row["sale_price"] > 0 else 0
        msrp = knife_row["msrp_new_price"] if knife_row["msrp_new_price"] and knife_row["msrp_new_price"] > 0 else 0
        return sale if sale > 0 else msrp

    def image_for_export(self, knife_row):
        image_url = knife_input_service.clean_url(knife_row["image_url"])
        if image_url:
            return image_url
        image_name = knife_input_service.clean_text(knife_row["image"])
        if image_name:
            safe_name = os.path.basename(image_name)
            ext = safe_name.rsplit(".", 1)[1].lower() if "." in safe_name else ""
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_name)
            if ext in ALLOWED_EXTENSIONS and os.path.isfile(file_path):
                return url_for("static", filename=f"uploads/{safe_name}")
        return ""

    def build_catalog_item(self, knife_row):
        price_value = self.resolve_price(knife_row)
        return {
            "brand": knife_input_service.clean_text(knife_row["brand"]),
            "model": knife_input_service.clean_text(knife_row["model"]),
            "price_value": price_value,
            "price_display": f"₪{price_value:,.2f}" if price_value and price_value > 0 else "",
            "image_url": self.image_for_export(knife_row),
        }

    def filter_items(self, catalog_items, omit_no_brand, omit_no_price):
        filtered = []
        for item in catalog_items:
            if omit_no_brand and self.brand_is_missing(item["brand"]):
                continue
            if omit_no_price and not item["price_display"]:
                continue
            filtered.append(item)
        return filtered

    def build_export_text(self, catalog_items, hide_price, hide_brand):
        lines = []
        for item in catalog_items:
            model = item["model"] or "—"
            brand = item["brand"]
            if hide_brand or self.brand_is_missing(brand):
                base = model
            else:
                base = f"{brand} - {model}"
            if hide_price:
                lines.append(base)
            elif item["price_display"]:
                lines.append(f"{base}: {item['price_display']}")
            else:
                lines.append(f"{base}: No price")
        return "\n".join(lines) if lines else "No knives match the current filters."

    def build_doc_html(self, mode, catalog_items, hide_price, hide_brand):
        title_map = {"price": "Text list", "images": "Image catalog", "full": "Full catalog"}
        header = (
            "<html><head><meta charset='utf-8'></head><body>"
            "<h2>Blade &amp; Steel Catalog</h2>"
            f"<p>{title_map.get(mode, 'Text list')} · {len(catalog_items)} item(s)</p>"
        )
        footer = "</body></html>"
        if mode == "price":
            text = self.build_export_text(catalog_items, hide_price=hide_price, hide_brand=hide_brand)
            return f"{header}<pre>{text}</pre>{footer}"
        rows = []
        for item in catalog_items:
            brand_html = "" if hide_brand else f"<div><strong>{item['brand'] or '—'}</strong></div>"
            model_html = f"<div>{item['model'] or '—'}</div>"
            price_html = ""
            if not hide_price:
                price_html = f"<div>{item['price_display'] or 'No price'}</div>"
            image_html = f"<div>{item['image_url']}</div>" if item["image_url"] else "<div>No photo</div>"
            if mode == "images":
                rows.append(f"<li>{image_html}</li>")
            else:
                rows.append(f"<li>{brand_html}{model_html}{price_html}{image_html}</li>")
        return f"{header}<ul>{''.join(rows)}</ul>{footer}"


admin_export_service = AdminExportService()


@app.route("/admin/export", methods=["GET"])
@admin_required
def admin_export():
    brand_filter = request.args.get("brand", "").strip()
    category_filter = request.args.get("category", "").strip()
    export_mode = admin_export_service.clean_mode(request.args.get("mode", "price"))
    omit_no_brand = admin_export_service.clean_bool(request.args.get("omit_no_brand", "0"))
    omit_no_price = admin_export_service.clean_bool(request.args.get("omit_no_price", "0"))
    hide_price = admin_export_service.clean_bool(request.args.get("hide_price", "0"))
    hide_brand = admin_export_service.clean_bool(request.args.get("hide_brand", "0"))

    conn = get_db_connection()
    
    cats_db = conn.execute("SELECT DISTINCT category FROM knives WHERE category != ''").fetchall()
    brands_db = conn.execute("SELECT DISTINCT brand FROM knives WHERE brand != ''").fetchall()
    categories = [row['category'] for row in cats_db]
    brands = [row['brand'] for row in brands_db]

    query = "SELECT brand, model, sale_price, msrp_new_price, quantity, image, image_url FROM knives WHERE status != 'sold'"
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

    catalog_items = [admin_export_service.build_catalog_item(k) for k in knives]
    catalog_items = admin_export_service.filter_items(
        catalog_items,
        omit_no_brand=omit_no_brand,
        omit_no_price=omit_no_price,
    )
    export_text = admin_export_service.build_export_text(
        catalog_items,
        hide_price=hide_price,
        hide_brand=hide_brand,
    )
    catalog_doc_url = url_for(
        "admin_export_doc",
        mode=export_mode,
        brand=brand_filter,
        category=category_filter,
        omit_no_brand=1 if omit_no_brand else 0,
        omit_no_price=1 if omit_no_price else 0,
        hide_price=1 if hide_price else 0,
        hide_brand=1 if hide_brand else 0,
    )

    return render_template("admin_export.html", 
                           export_mode=export_mode,
                           catalog_items=catalog_items,
                           export_text=export_text,
                           brands=brands,
                           categories=categories,
                           brand_filter=brand_filter,
                           category_filter=category_filter,
                           omit_no_brand=omit_no_brand,
                           omit_no_price=omit_no_price,
                           hide_price=hide_price,
                           hide_brand=hide_brand,
                           catalog_doc_url=catalog_doc_url)


@app.route("/admin/export/catalog.doc", methods=["GET"])
@admin_required
def admin_export_doc():
    brand_filter = request.args.get("brand", "").strip()
    category_filter = request.args.get("category", "").strip()
    export_mode = admin_export_service.clean_mode(request.args.get("mode", "price"))
    omit_no_brand = admin_export_service.clean_bool(request.args.get("omit_no_brand", "0"))
    omit_no_price = admin_export_service.clean_bool(request.args.get("omit_no_price", "0"))
    hide_price = admin_export_service.clean_bool(request.args.get("hide_price", "0"))
    hide_brand = admin_export_service.clean_bool(request.args.get("hide_brand", "0"))

    conn = get_db_connection()
    query = "SELECT brand, model, sale_price, msrp_new_price, quantity, image, image_url FROM knives WHERE status != 'sold'"
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

    catalog_items = [admin_export_service.build_catalog_item(k) for k in knives]
    catalog_items = admin_export_service.filter_items(catalog_items, omit_no_brand=omit_no_brand, omit_no_price=omit_no_price)
    doc_html = admin_export_service.build_doc_html(
        mode=export_mode,
        catalog_items=catalog_items,
        hide_price=hide_price,
        hide_brand=hide_brand,
    )
    response = make_response(doc_html)
    response.headers["Content-Type"] = "application/msword; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=knife-catalog.doc"
    return response


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
    blade_length_cm = knife_input_service.clean_float(req.form.get("blade_length_cm", 0))
    
    currency = knife_input_service.clean_text(req.form.get("currency", "ILS"))
    if currency == "USD":
        # Convert to ILS (Approximate rate: 3)
        msrp_new_price = round(msrp_new_price * 3, 2)
        cost_price = round(cost_price * 3, 2)
        sale_price = round(sale_price * 3, 2)
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
                msrp_new_price, cost_price, sale_price, price_confidence, is_featured, blade_length_cm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (brand, model, category, status, buy_price, estimated_value, quantity, notes, filename, description,
              image_url, image_source_url, price_source_url, data_confidence,
              msrp_new_price, cost_price, sale_price, price_confidence, is_featured, blade_length_cm))
        audit_action = "create"
        audit_entity_id = cur.lastrowid
    else:
        old_sale = float(existing_knife["sale_price"] or 0)
        conn.execute("""
            UPDATE knives
            SET brand=?, model=?, category=?, status=?, buy_price=?, estimated_value=?, quantity=?, notes=?, image=?, description=?,
                image_url=?, image_source_url=?, price_source_url=?, data_confidence=?,
                msrp_new_price=?, cost_price=?, sale_price=?, price_confidence=?, is_featured=?, blade_length_cm=?
            WHERE id = ?
        """, (brand, model, category, status, buy_price, estimated_value, quantity, notes, filename, description,
              image_url, image_source_url, price_source_url, data_confidence,
              msrp_new_price, cost_price, sale_price, price_confidence, is_featured, blade_length_cm, knife_id))
        if round(old_sale, 2) != round(sale_price, 2):
            SalePriceHistoryService.record(
                conn,
                knife_id,
                old_sale,
                sale_price,
                "admin_edit",
                (current_user.email if current_user.is_authenticated else "") or "",
            )
        audit_action = "update"
    conn.commit()
    conn.close()
    if audit_action:
        write_audit_log(audit_action, "knife", audit_entity_id, audit_details)

    return redirect(url_for("admin_knives"))


@app.errorhandler(Exception)
def _fallback_exception_response(e):
    if isinstance(e, RoutingException) and not isinstance(e, HTTPException):
        raise e
    if isinstance(e, HTTPException):
        code = getattr(e, "code", None) or 500
        if code < 500:
            return e
        inner = getattr(e, "original_exception", None)
        to_log = inner if inner is not None else e
        _write_error_log(to_log)
        if os.getenv("FLASK_DEBUG", "0").strip() == "1":
            import traceback
            tb = getattr(to_log, "__traceback__", None)
            body = "".join(traceback.format_exception(type(to_log), to_log, tb))
            return make_response(body, 500, {"Content-Type": "text/plain; charset=utf-8"})
        return make_response(
            "Server error. See knife_inventory_errors.log in the app folder.",
            500,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    _write_error_log(e)
    if os.getenv("FLASK_DEBUG", "0").strip() == "1":
        import traceback
        tb = getattr(e, "__traceback__", None)
        body = "".join(traceback.format_exception(type(e), e, tb))
        return make_response(body, 500, {"Content-Type": "text/plain; charset=utf-8"})
    return make_response(
        "Server error. See knife_inventory_errors.log in the app folder.",
        500,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


if __name__ == "__main__":
    _debug = os.getenv("FLASK_DEBUG", "0").strip() == "1"
    _host = os.getenv("FLASK_HOST", "127.0.0.1")
    _want = int(os.getenv("FLASK_PORT", "5000"))
    _port = flask_listen_port_resolver.resolve(_host, _want)
    if _port != _want:
        print(f"Port {_want} was not available; listening on http://{_host}:{_port}")
    app.run(
        host=_host,
        port=_port,
        debug=_debug,
        use_reloader=_debug and os.getenv("FLASK_USE_RELOADER", "0").strip() == "1",
    )
