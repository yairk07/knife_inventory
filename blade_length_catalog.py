import os
import sqlite3
from typing import Optional, Tuple


class BladeLengthCatalogService:
    _RULES: Tuple[Tuple[str, Tuple[str, ...], float], ...] = (
        ("benchmade", ("socp", "179"), 17.80),
        ("benchmade", ("socp", "176"), 8.89),
        ("benchmade", ("4010bk",), 20.0),
        ("benchmade", ("710",), 10.16),
        ("benchmade", ("9400",), 8.64),
        ("benchmade", ("adamas", "275"), 9.70),
        ("benchmade", ("adira",), 9.86),
        ("benchmade", ("bugout", "535"), 8.23),
        ("benchmade", ("bushcrafter",), 11.43),
        ("benchmade", ("4300",), 8.71),
        ("benchmade", ("casbah",), 8.38),
        ("benchmade", ("claymore", "9070"), 8.89),
        ("benchmade", ("griptilian", "551"), 8.74),
        ("benchmade", ("intersect",), 6.48),
        ("benchmade", ("narrows",), 8.74),
        ("benchmade", ("psk",), 6.73),
        ("benchmade", ("593bk",), 6.73),
        ("benchmade", ("pagan",), 9.90),
        ("benchmade", ("saddle",), 10.40),
        ("benchmade", ("shootout",), 8.38),
        ("benchmade", ("freek",), 9.14),
        ("benchmade", ("560bk",), 9.14),
        ("cold steel", ("code",), 8.89),
        ("cold steel", ("espada",), 14.0),
        ("cold steel", ("magnum",), 19.05),
        ("cold steel", ("recon",), 10.16),
        ("cold steel", ("srk",), 15.24),
        ("cold steel", ("survivalist",), 20.32),
        ("kizer", ("supreme",), 10.0),
        ("microtech", ("socom",), 10.16),
        ("microtech", ("utx",), 8.10),
        ("sog", ("pentagon", "fx"), 11.43),
        ("sog", ("seal", "xr"), 9.90),
        ("sog", ("pentagon", "xr"), 8.89),
    )

    def resolve(self, brand: str, model: str) -> Optional[float]:
        b = (brand or "").strip().lower()
        m = (model or "").strip().lower()
        for rule_brand, subs, length_cm in self._RULES:
            if b != rule_brand:
                continue
            if all(sub in m for sub in subs):
                return float(length_cm)
        return None

    def apply_matches_to_connection(self, conn) -> int:
        rows = conn.execute("SELECT id, brand, model FROM knives").fetchall()
        updated = 0
        for r in rows:
            val = self.resolve(r["brand"], r["model"])
            if val is None:
                continue
            conn.execute("UPDATE knives SET blade_length_cm = ? WHERE id = ?", (val, r["id"]))
            updated += 1
        return updated


blade_length_catalog_service = BladeLengthCatalogService()


def apply_blade_lengths_cli():
    root = os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(root, "knives.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    n = blade_length_catalog_service.apply_matches_to_connection(conn)
    conn.commit()
    conn.close()
    return n


if __name__ == "__main__":
    print("updated_rows", apply_blade_lengths_cli())
