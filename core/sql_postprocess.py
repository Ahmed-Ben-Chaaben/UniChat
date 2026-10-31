"""
sql_postprocess.py — Post-processeur déterministe du SQL généré par le LLM.

Problème récurrent : le LLM 7b génère du SQL avec des constructions
MySQL/PG même quand le dialecte cible est MSSQL. Ce module corrige
cela de façon déterministe, sans appel LLM supplémentaire.

Point d'entrée : enforce_mssql(sql) — appelé par sir.py après to_sql()
et fix_sql(), systématiquement pour tout dialecte MSSQL.
"""
from __future__ import annotations
import re

# ── Détection dialecte ────────────────────────────────────────────────────────

_MSSQL_VARIANTS = frozenset({
    "mssql", "sqlserver", "sql server", "sql_server",
    "mssql+pyodbc", "mssql+pymssql",
})


def is_mssql(dialect: str) -> bool:
    """Retourne True pour tout variant de dialecte MSSQL/SQL Server.
    Gère : 'mssql', 'mssql+pyodbc', 'mssql+pymssql', 'sqlserver', etc.
    """
    d = dialect.lower().strip()
    return d in _MSSQL_VARIANTS or d.startswith("mssql")


# ── Regex ─────────────────────────────────────────────────────────────────────

# LIMIT N [OFFSET M] en fin de requête (ou avant ';')
_LIMIT_RE = re.compile(
    r"\bLIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*;?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Capture juste le N de LIMIT N
_LIMIT_N_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
# SELECT [DISTINCT] TOP N déjà présent
_TOP_IN_SELECT_RE = re.compile(r"\bSELECT\s+(DISTINCT\s+)?TOP\s+\d+\b", re.IGNORECASE)

# Fonctions MySQL/PG → MSSQL
_IFNULL_RE      = re.compile(r"\bIFNULL\s*\(", re.IGNORECASE)
_NOW_RE         = re.compile(r"\bNOW\s*\(\s*\)", re.IGNORECASE)
_DATE_FORMAT_RE = re.compile(r"\bDATE_FORMAT\s*\(", re.IGNORECASE)
_EXTRACT_RE     = re.compile(
    r"\bEXTRACT\s*\(\s*(\w+)\s+FROM\s+([^)]+)\s*\)", re.IGNORECASE
)
_DATE_TRUNC_RE  = re.compile(
    r"\bDATE_TRUNC\s*\(\s*'?(\w+)'?\s*,\s*([^)]+)\s*\)", re.IGNORECASE
)
# DATEDIFF à 2 args MySQL (sans unité) — ne pas toucher aux DATEDIFF MSSQL à 3 args
_DATEDIFF2_RE   = re.compile(
    r"\bDATEDIFF\s*\(\s*"
    r"(?!year\b|month\b|day\b|hour\b|minute\b|second\b|week\b|quarter\b)"
    r"([^,]+?)\s*,\s*([^)]+?)\s*\)",
    re.IGNORECASE,
)


# ── Corrections ───────────────────────────────────────────────────────────────

def _fix_limit(sql: str) -> str:
    """Supprime LIMIT [OFFSET] et, si TOP manque, l'injecte dans le SELECT."""
    m = _LIMIT_N_RE.search(sql)
    if not m:
        return sql

    n = m.group(1)
    # Supprimer le LIMIT (et l'OFFSET éventuel)
    sql = _LIMIT_RE.sub("", sql).rstrip().rstrip(";").rstrip()

    if not _TOP_IN_SELECT_RE.search(sql):
        # Pas encore de TOP → injecter SELECT TOP N [DISTINCT] ou SELECT DISTINCT TOP N
        sql = re.sub(
            r"\bSELECT(\s+DISTINCT)?\b",
            lambda mo: f"SELECT{mo.group(1) or ''} TOP {n}",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )

    return sql.rstrip(";").rstrip() + ";"


def _fix_functions(sql: str) -> str:
    """Corrige les fonctions MySQL/PG vers leurs équivalents MSSQL."""
    sql = _IFNULL_RE.sub("ISNULL(", sql)
    sql = _NOW_RE.sub("GETDATE()", sql)
    sql = _DATE_FORMAT_RE.sub("FORMAT(", sql)
    sql = _EXTRACT_RE.sub(
        lambda m: f"DATEPART({m.group(1)}, {m.group(2).strip()})", sql
    )
    sql = _DATE_TRUNC_RE.sub(
        lambda m: (
            f"DATEADD({m.group(1)}, "
            f"DATEDIFF({m.group(1)}, 0, {m.group(2).strip()}), 0)"
        ),
        sql,
    )
    sql = _DATEDIFF2_RE.sub(
        lambda m: f"DATEDIFF(day, {m.group(2).strip()}, {m.group(1).strip()})",
        sql,
    )
    return sql


def enforce_mssql(sql: str) -> str:
    """Applique toutes les corrections MSSQL.
    Idempotent — safe d'appeler deux fois.
    """
    sql = _fix_functions(sql)
    sql = _fix_limit(sql)
    sql = re.sub(r";\s*;", ";", sql).strip()
    if not sql.endswith(";"):
        sql += ";"
    return sql


# ── Tests ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        # Bug exact observé (TOP + LIMIT) — les deux fois
        ("SELECT TOP 100 Date_Facture FROM FACT_Vente ORDER BY Date_Facture DESC LIMIT 1000",
         "TOP conservé, LIMIT supprimé"),
        ("SELECT TOP 1 DATEPART(quarter, Date_Paiement) AS T FROM FACT_Recouvrement_Client "
         "WHERE YEAR(Date_Paiement) = 2024 AND Date_Paiement > Date_Echeance "
         "GROUP BY DATEPART(quarter, Date_Paiement) ORDER BY COUNT(*) DESC LIMIT 1000",
         "TOP 1 conservé, LIMIT 1000 supprimé"),
        # LIMIT sans TOP → converti en TOP
        ("SELECT Code_Client, SUM(CA_TND) AS CA FROM FACT_Vente "
         "GROUP BY Code_Client ORDER BY CA DESC LIMIT 10",
         "TOP 10 injecté"),
        # SELECT DISTINCT TOP déjà là
        ("SELECT DISTINCT TOP 100 CAST(Date_Facture AS DATE) AS Jour "
         "FROM FACT_Vente ORDER BY Jour DESC LIMIT 1000",
         "DISTINCT TOP 100 conservé, LIMIT supprimé"),
        # Fonctions MySQL
        ("SELECT IFNULL(Montant, 0) FROM FACT_Vente WHERE Date_Facture < NOW()",
         "ISNULL + GETDATE"),
        # EXTRACT
        ("SELECT EXTRACT(quarter FROM Date_Paiement) AS Q FROM FACT_Recouvrement_Client",
         "DATEPART(quarter, ...)"),
    ]

    all_ok = True
    for sql_in, label in cases:
        sql_out = enforce_mssql(sql_in)
        bad = bool(re.search(r"\bLIMIT\b", sql_out, re.IGNORECASE))
        status = "❌ LIMIT encore présent!" if bad else "✅"
        print(f"{status} [{label}]")
        print(f"  IN  : {sql_in[:80]}")
        print(f"  OUT : {sql_out[:80]}\n")
        if bad:
            all_ok = False

    # Test is_mssql
    print("=== is_mssql ===")
    for d, exp in [("mssql", True), ("mssql+pyodbc", True), ("mssql+pymssql", True),
                   ("sqlserver", True), ("sqlite", False), ("postgresql", False)]:
        r = is_mssql(d)
        print(f"  {'✅' if r == exp else '❌'}  {d!r:20} → {r}")

    print("\n=== TOUS OK ===" if all_ok else "\n=== ÉCHECS ===")