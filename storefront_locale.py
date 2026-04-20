from flask import request


class StorefrontLocaleService:
    COOKIE = "site_lang"
    COOKIE_MAX_AGE = 34560000

    STRINGS = {
        "en": {
            "site_title": "BLADE & STEEL | Premium Knives",
            "nav_home": "Home",
            "nav_collection": "Collection",
            "nav_finder": "Find a knife",
            "nav_admin": "Admin",
            "nav_logout": "Logout",
            "nav_login": "Login",
            "lang_en": "English",
            "lang_he": "עברית",
            "footer_rights": "© 2026 Blade & Steel Premium Gear. All rights reserved.",
            "hero_title": "Precision Engineered",
            "hero_subtitle": "Explore our curated collection of premium folding, fixed blade, and tactical tools.",
            "hero_cta": "Shop Collection",
            "section_featured": "Featured Gear",
            "no_featured": "No featured items right now.",
            "no_image": "NO IMAGE",
            "collection_title": "The Collection",
            "search_ph": "Search...",
            "opt_all_brands": "All Brands",
            "opt_all_categories": "All Categories",
            "btn_filter": "Filter",
            "link_clear": "Clear",
            "no_results": "No knives found matching criteria.",
            "badge_sold": "SOLD",
            "price_on_request": "Price on request",
            "no_description": "No description available for this model.",
            "blade_steel": "Blade steel",
            "blade_length": "Blade length",
            "cm_abbr": "cm",
            "category": "Category",
            "status_label": "Status",
            "availability": "Availability",
            "out_of_stock": "Out of Stock",
            "in_stock": "In Stock",
            "na": "N/A",
            "contact_purchase": "Contact to Purchase",
            "sold_out": "Sold Out",
            "status_home": "Home",
            "status_sold": "Sold",
            "status_cart": "Cart",
            "status_on_the_way": "On the way",
            "status_need_to_order": "Need to order",
            "status_ordered": "Ordered",
            "login_title": "Welcome Back",
            "email": "Email",
            "password": "Password",
            "show_password": "Show password",
            "remember_me": "Remember me on this device",
            "sign_in": "Sign In",
            "or_divider": "OR",
            "google_sign_in": "Sign in with Google",
            "login_no_account": "Don't have an account?",
            "login_register_link": "Register here",
            "register_title": "Create Account",
            "register_email": "Email Address",
            "register_submit": "Register",
            "register_has_account": "Already have an account?",
            "register_signin_link": "Sign in here",
            "finder_kicker": "Guided match",
            "finder_title": "Find your knife",
            "finder_lead": "Answer five quick questions with image choices. We rank inventory that fits your needs.",
            "finder_back": "Back",
            "finder_results_title": "Your top matches",
            "finder_results_sub": "Based on your answers and how each listing is described in the catalog.",
            "finder_browse_collection": "Browse full collection",
            "finder_restart": "Start over",
            "finder_disclaimer": "Recommendations use catalog text and specs only—not a substitute for handling laws or professional advice.",
            "finder_loading": "Finding matches…",
            "finder_done": "Done",
            "finder_no_match": "No open inventory matched strongly. Try again or browse the collection.",
            "finder_error": "Could not load matches. Check your connection and try again.",
            "finder_q1_title": "Where will you use it most?",
            "finder_q1_edc": "Everyday & carry",
            "finder_q1_outdoor": "Outdoor & adventure",
            "finder_q1_work": "Work & utility",
            "finder_q1_display": "Collection & display",
            "finder_q2_title": "Blade format",
            "finder_q2_folding": "Folding",
            "finder_q2_fixed": "Fixed blade",
            "finder_q2_either": "No preference",
            "finder_q3_title": "Budget level (guide)",
            "finder_q3_low": "Entry",
            "finder_q3_mid": "Mid-range",
            "finder_q3_high": "Premium",
            "finder_q4_title": "Steel priority",
            "finder_q4_stainless": "Corrosion resistance",
            "finder_q4_edge": "Edge holding / toughness",
            "finder_q4_balanced": "Balanced",
            "finder_q5_title": "Carry size",
            "finder_q5_compact": "Compact / light",
            "finder_q5_full": "Full size",
            "finder_q5_either": "Either is fine",
        },
        "he": {
            "site_title": "BLADE & STEEL | סכינים פרימיום",
            "nav_home": "בית",
            "nav_collection": "קולקציה",
            "nav_finder": "מצא סכין",
            "nav_admin": "ניהול",
            "nav_logout": "התנתקות",
            "nav_login": "התחברות",
            "lang_en": "English",
            "lang_he": "עברית",
            "footer_rights": "© 2026 Blade & Steel. כל הזכויות שמורות.",
            "hero_title": "הנדסת דיוק",
            "hero_subtitle": "קולקציה נבחרת של סכיני הטבעה, מתקפלים וכלים טקטיים.",
            "hero_cta": "לקולקציה",
            "section_featured": "מובילים",
            "no_featured": "אין פריטים מובילים כרגע.",
            "no_image": "ללא תמונה",
            "collection_title": "הקולקציה",
            "search_ph": "חיפוש...",
            "opt_all_brands": "כל המותגים",
            "opt_all_categories": "כל הקטגוריות",
            "btn_filter": "סינון",
            "link_clear": "נקה",
            "no_results": "לא נמצאו פריטים התואמים לחיפוש.",
            "badge_sold": "נמכר",
            "price_on_request": "מחיר לפי בקשה",
            "no_description": "אין תיאור זמין לדגם זה.",
            "blade_steel": "מתכת להב",
            "blade_length": "אורך להב",
            "cm_abbr": "ס״מ",
            "category": "קטגוריה",
            "status_label": "סטטוס",
            "availability": "זמינות",
            "out_of_stock": "אזל מהמלאי",
            "in_stock": "במלאי",
            "na": "לא זמין",
            "contact_purchase": "צור קשר לרכישה",
            "sold_out": "אזל",
            "status_home": "זמין",
            "status_sold": "נמכר",
            "status_cart": "בעגלה",
            "status_on_the_way": "בדרך",
            "status_need_to_order": "להזמנה",
            "status_ordered": "הוזמן",
            "login_title": "ברוך שובך",
            "email": "אימייל",
            "password": "סיסמה",
            "show_password": "הצג סיסמה",
            "remember_me": "זכור אותי במכשיר זה",
            "sign_in": "התחבר",
            "or_divider": "או",
            "google_sign_in": "התחברות עם Google",
            "login_no_account": "אין לך חשבון?",
            "login_register_link": "הרשמה כאן",
            "register_title": "יצירת חשבון",
            "register_email": "כתובת אימייל",
            "register_submit": "הרשמה",
            "register_has_account": "כבר יש לך חשבון?",
            "register_signin_link": "התחבר כאן",
            "finder_kicker": "התאמה מודרכת",
            "finder_title": "מצא את הסכין בשבילך",
            "finder_lead": "חמש שאלות קצרות עם בחירות בתמונה. נדרג מהמלאי לפי התאמה לצרכים שבחרת.",
            "finder_back": "חזרה",
            "finder_results_title": "ההתאמות המובילות שלך",
            "finder_results_sub": "לפי התשובות שלך ולפי איך שכל פריט מתואר בקטלוג.",
            "finder_browse_collection": "לכל הקולקציה",
            "finder_restart": "התחל מחדש",
            "finder_disclaimer": "ההמלצות מבוססות על טקסט ומפרטים בקטלוג בלבד—לא תחליף לחוקים או ייעוץ מקצועי.",
            "finder_loading": "מחפש התאמות…",
            "finder_done": "סיום",
            "finder_no_match": "לא נמצאה התאמה חזקה במלאי הפתוח. נסה שוב או עיין בקולקציה.",
            "finder_error": "טעינת ההתאמות נכשלה. בדוק חיבור ונסה שוב.",
            "finder_q1_title": "איפה תשתמש בה הכי הרבה?",
            "finder_q1_edc": "יומיומי ונשיאה",
            "finder_q1_outdoor": "חוץ ובטבע",
            "finder_q1_work": "עבודה ושימושים",
            "finder_q1_display": "אוסף ותצוגה",
            "finder_q2_title": "סוג להב",
            "finder_q2_folding": "מתקפל",
            "finder_q2_fixed": "להב קבוע",
            "finder_q2_either": "אין העדפה",
            "finder_q3_title": "רמת תקציב (הכוונה)",
            "finder_q3_low": "כניסה",
            "finder_q3_mid": "בינוני",
            "finder_q3_high": "פרימיום",
            "finder_q4_title": "עדיפות פלדה",
            "finder_q4_stainless": "עמידות בקורוזיה",
            "finder_q4_edge": "אחיזת חד / קשיחות",
            "finder_q4_balanced": "מאוזן",
            "finder_q5_title": "גודל נשיאה",
            "finder_q5_compact": "קומפקטי / קל",
            "finder_q5_full": "מלא",
            "finder_q5_either": "גמיש",
        },
    }

    def get_lang(self):
        try:
            raw = (request.cookies.get(self.COOKIE) or "en").strip().lower()
        except RuntimeError:
            return "en"
        return raw if raw in ("en", "he") else "en"

    def translate(self, key, lang=None):
        lang = lang or self.get_lang()
        row = self.STRINGS.get(lang) or self.STRINGS["en"]
        base = self.STRINGS["en"]
        val = row.get(key)
        if val is None:
            val = base.get(key)
        if val is not None:
            return val
        if isinstance(key, str) and key.startswith("status_"):
            return key[7:].replace("_", " ").title()
        return key

    def blade_length_parts(self, cm_value, lang=None):
        lang = lang or self.get_lang()
        try:
            n = float(cm_value)
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        num_txt = f"{n:.2f}".rstrip("0").rstrip(".") or "0"
        label = self.translate("blade_length", lang)
        unit = self.translate("cm_abbr", lang)
        return {"label": label, "value": f"{num_txt} {unit}"}

    def blade_length_card_line(self, cm_value, lang=None):
        parts = self.blade_length_parts(cm_value, lang)
        if not parts:
            return ""
        return f"{parts['label']}: {parts['value']}"

    @staticmethod
    def safe_internal_path(candidate):
        if not candidate or not isinstance(candidate, str):
            return None
        c = candidate.strip()
        if not c.startswith("/") or "\n" in c or "\r" in c or "://" in c:
            return None
        return c


storefront_locale_service = StorefrontLocaleService()
