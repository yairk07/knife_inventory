import json


class KnifeFinderService:
    QUESTIONS = [
        {
            "id": "use",
            "title_key": "finder_q1_title",
            "options": [
                {
                    "id": "edc",
                    "label_key": "finder_q1_edc",
                    "image": "https://picsum.photos/seed/finder_edc/720/480",
                },
                {
                    "id": "outdoor",
                    "label_key": "finder_q1_outdoor",
                    "image": "https://picsum.photos/seed/finder_outdoor/720/480",
                },
                {
                    "id": "work",
                    "label_key": "finder_q1_work",
                    "image": "https://picsum.photos/seed/finder_work/720/480",
                },
                {
                    "id": "display",
                    "label_key": "finder_q1_display",
                    "image": "https://picsum.photos/seed/finder_display/720/480",
                },
            ],
        },
        {
            "id": "blade",
            "title_key": "finder_q2_title",
            "options": [
                {
                    "id": "folding",
                    "label_key": "finder_q2_folding",
                    "image": "https://picsum.photos/seed/finder_fold/720/480",
                },
                {
                    "id": "fixed",
                    "label_key": "finder_q2_fixed",
                    "image": "https://picsum.photos/seed/finder_fixedblade/720/480",
                },
                {
                    "id": "either_blade",
                    "label_key": "finder_q2_either",
                    "image": "https://picsum.photos/seed/finder_eitherblade/720/480",
                },
            ],
        },
        {
            "id": "budget",
            "title_key": "finder_q3_title",
            "options": [
                {
                    "id": "budget_low",
                    "label_key": "finder_q3_low",
                    "image": "https://picsum.photos/seed/finder_budgetlow/720/480",
                },
                {
                    "id": "budget_mid",
                    "label_key": "finder_q3_mid",
                    "image": "https://picsum.photos/seed/finder_budgetmid/720/480",
                },
                {
                    "id": "budget_high",
                    "label_key": "finder_q3_high",
                    "image": "https://picsum.photos/seed/finder_budgethigh/720/480",
                },
            ],
        },
        {
            "id": "steel",
            "title_key": "finder_q4_title",
            "options": [
                {
                    "id": "prefer_stainless",
                    "label_key": "finder_q4_stainless",
                    "image": "https://picsum.photos/seed/finder_stainless/720/480",
                },
                {
                    "id": "prefer_edge",
                    "label_key": "finder_q4_edge",
                    "image": "https://picsum.photos/seed/finder_edgehold/720/480",
                },
                {
                    "id": "prefer_balanced",
                    "label_key": "finder_q4_balanced",
                    "image": "https://picsum.photos/seed/finder_balanced/720/480",
                },
            ],
        },
        {
            "id": "size",
            "title_key": "finder_q5_title",
            "options": [
                {
                    "id": "compact",
                    "label_key": "finder_q5_compact",
                    "image": "https://picsum.photos/seed/finder_compact/720/480",
                },
                {
                    "id": "full_size",
                    "label_key": "finder_q5_full",
                    "image": "https://picsum.photos/seed/finder_fullsize/720/480",
                },
                {
                    "id": "size_either",
                    "label_key": "finder_q5_either",
                    "image": "https://picsum.photos/seed/finder_sizeeither/720/480",
                },
            ],
        },
    ]

    _USE_TAGS = {
        "edc": ("edc", "everyday", "pocket", "folder", "folding", "carry", "urban", "office", "city"),
        "outdoor": ("outdoor", "bush", "camp", "hik", "nature", "wood", "survival", "hunt", "trail", "forest"),
        "work": ("work", "tool", "utility", "trade", "job", "professional", "shop", "field"),
        "display": ("collector", "display", "premium", "limited", "show", "gift", "luxury"),
    }

    _FOLD_HINTS = ("fold", "flip", "pocket", "clasp", "frame lock", "liner lock", "axis", "edc", "slipjoint", "slip joint")
    _FIXED_HINTS = ("fixed", "full tang", "hunting", "bushcraft", "neck knife", "belt knife", "straight", "chef kitchen")

    _CORR_STEELS = (
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
    )
    _EDGE_STEELS = ("1095", "52100", "m4", "cpm-3v", "3v", "4v", "d2", "a2", "o1", "carbon", "tool steel")

    _COMPACT_HINTS = ("mini", "small", "compact", "lite", "slim", "micro", "sub", "little")
    _FULL_HINTS = ("large", "full size", "full-size", "heavy", "xl", "big", "long")

    @classmethod
    def allowed_answers(cls):
        out = {}
        for q in cls.QUESTIONS:
            out[q["id"]] = {o["id"] for o in q["options"]}
        return out

    @classmethod
    def normalize_answers(cls, raw):
        allowed = cls.allowed_answers()
        clean = {}
        for qid, opts in allowed.items():
            v = raw.get(qid)
            if isinstance(v, str) and v in opts:
                clean[qid] = v
        return clean

    @classmethod
    def _blob(cls, row):
        parts = [
            row["category"] or "",
            row["description"] or "",
            row["model"] or "",
            row["brand"] or "",
        ]
        return " ".join(parts).lower()

    @classmethod
    def _effective_price(cls, row):
        sale = row["sale_price"] or 0
        msrp = row["msrp_new_price"] or 0
        try:
            s = float(sale)
        except (TypeError, ValueError):
            s = 0.0
        try:
            m = float(msrp)
        except (TypeError, ValueError):
            m = 0.0
        if s > 0:
            return s
        if m > 0:
            return m
        return None

    @classmethod
    def _metals_blob(cls, row):
        raw = row["blade_metals"] if "blade_metals" in row.keys() else "[]"
        if not raw:
            raw = "[]"
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return str(raw).lower()
        if isinstance(data, list):
            return " ".join(str(x).lower() for x in data)
        return str(raw).lower()

    @classmethod
    def _score_use(cls, blob, choice):
        if not choice:
            return 0
        tags = cls._USE_TAGS.get(choice, ())
        return 28 if any(t in blob for t in tags) else 4

    @classmethod
    def _score_blade(cls, blob, choice):
        if not choice or choice == "either_blade":
            return 14
        has_fold = any(h in blob for h in cls._FOLD_HINTS)
        has_fixed = any(h in blob for h in cls._FIXED_HINTS)
        if choice == "folding":
            if has_fold and not has_fixed:
                return 26
            if has_fold:
                return 18
            if has_fixed:
                return 6
            return 12
        if choice == "fixed":
            if has_fixed and not has_fold:
                return 26
            if has_fixed:
                return 18
            if has_fold:
                return 6
            return 12
        return 0

    @classmethod
    def _score_budget(cls, price, choice):
        if not choice:
            return 0
        if price is None or price <= 0:
            return 10
        bands = {
            "budget_low": (0, 900),
            "budget_mid": (900, 2800),
            "budget_high": (2800, 1e12),
        }
        lo, hi = bands.get(choice, (0, 1e12))
        if lo <= price < hi:
            return 26
        mid = (lo + hi) / 2
        dist = abs(price - mid) / max(mid, 1)
        if dist < 0.35:
            return 14
        return 6

    @classmethod
    def _score_steel(cls, metals, blob, choice):
        if not choice:
            return 0
        m = metals + " " + blob
        if choice == "prefer_balanced":
            return 16
        if choice == "prefer_stainless":
            hit = any(s in m for s in cls._CORR_STEELS)
            return 24 if hit else 8
        if choice == "prefer_edge":
            hit = any(s in m for s in cls._EDGE_STEELS)
            return 24 if hit else 8
        return 0

    @classmethod
    def _score_size(cls, blob, choice):
        if not choice or choice == "size_either":
            return 14
        c = any(h in blob for h in cls._COMPACT_HINTS)
        f = any(h in blob for h in cls._FULL_HINTS)
        if choice == "compact":
            if c and not f:
                return 22
            if c:
                return 16
            return 8
        if choice == "full_size":
            if f and not c:
                return 22
            if f:
                return 16
            return 8
        return 0

    @classmethod
    def score_row(cls, row, answers):
        blob = cls._blob(row)
        metals = cls._metals_blob(row)
        price = cls._effective_price(row)
        s = 0.0
        s += cls._score_use(blob, answers.get("use"))
        s += cls._score_blade(blob, answers.get("blade"))
        s += cls._score_budget(price, answers.get("budget"))
        s += cls._score_steel(metals, blob, answers.get("steel"))
        s += cls._score_size(blob, answers.get("size"))
        if row["is_featured"]:
            s += 6
        st = (row["status"] or "").lower()
        if st == "home":
            s += 5
        try:
            q = int(row["quantity"] or 0)
        except (TypeError, ValueError):
            q = 0
        if q > 0:
            s += 3
        return s

    @classmethod
    def recommend(cls, rows, answers, limit=3):
        answers = cls.normalize_answers(answers)
        scored = []
        for row in rows:
            if (row["status"] or "").lower() == "sold":
                continue
            scored.append((cls.score_row(row, answers), row))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return []
        top_score = scored[0][0]
        if top_score < 8:
            featured = [r for _, r in scored if r["is_featured"]]
            if featured:
                return featured[:limit]
        out = [r for _, r in scored[:limit]]
        return out if out else []
