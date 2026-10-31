"""
SQL Generation two-step via SIR (papier SiriusBI §3.4.4).

Modifications vs version MSSQL :
  - Dialecte par défaut : "mysql"
  - Guide MySQL ajouté dans _DIALECT_GUIDE
  - Import sql_postprocess rendu optionnel (module MSSQL, non requis en MySQL)
  - to_sql() et fix_sql() : post-processeur MSSQL uniquement si dialecte mssql
"""
from __future__ import annotations
from dataclasses import dataclass
import re

from core.LLM_cloud import LLMClient
from knowledge.builder import KBEntry

# Import optionnel — sql_postprocess n'est utile qu'en dialecte MSSQL
try:
    from core.sql_postprocess import enforce_mssql, is_mssql as _is_mssql
    _POSTPROCESS_AVAILABLE = True
except ImportError:
    _POSTPROCESS_AVAILABLE = False

    def enforce_mssql(sql: str) -> str:  # type: ignore[misc]
        return sql

    def _is_mssql(dialect: str) -> bool:  # type: ignore[misc]
        return False


@dataclass
class SIRResult:
    rewritten_query: str
    sir_json: dict


# ── Étape 1 : SIR ────────────────────────────────────────────────────────────
_SIR_SYSTEM = (
    "Tu es un expert en analyse sémantique de requêtes BI. Tu transformes une "
    "question en langage naturel en une représentation intermédiaire structurée, "
    "en raisonnant étape par étape (chain-of-thought). Réponds UNIQUEMENT en JSON."
)

_SIR_PROMPT = """\
Connaissance récupérée (colonnes, termes, exemples) :
{knowledge}

Exemples de démonstration (few-shot) :
{demos}

Question : "{question}"

Raisonne étape par étape puis renvoie un JSON avec ces clés :
{{
  "Key Components": {{
     "Metric": "<colonne/agrégat de la mesure>",
     "Dimensions": ["<axes d'analyse>"],
     "Time Range": "<période ou null>",
     "Filters": ["<filtres explicites>"]
  }},
  "Knowledge Mapping": ["<terme utilisateur -> colonne réelle>"],
  "Query Understanding": "<intention en une phrase>",
  "Rewritten Query": "<question canonique, complète, autonome, sans ambiguïté>"
}}
"""

# ── Table de référence dialecte ───────────────────────────────────────────────
_DIALECT_GUIDE = {
    "mysql": """\
Règles syntaxiques MYSQL 8+ — STRICTEMENT OBLIGATOIRES :

  ╔══ INTERDIT ════════════════════════════════════════════════════╗
  ║  TOP N      → INTERDIT. N'existe pas en MySQL.                ║
  ║  GETDATE()  → INTERDIT. Utiliser CURDATE() ou NOW()           ║
  ║  ISNULL()   → INTERDIT. Utiliser IFNULL()                     ║
  ║  DATEADD()  → INTERDIT. Utiliser DATE_ADD(col, INTERVAL N unit)║
  ╚════════════════════════════════════════════════════════════════╝

  ╔══ OBLIGATOIRE ══════════════════════════════════════════════════╗
  ║  Limiter N lignes  : ... ORDER BY ... LIMIT N  (en fin)        ║
  ║  Extraire l'année  : YEAR(colonne) = 2024                      ║
  ║  Extraire le mois  : MONTH(colonne)                            ║
  ║  Extraire trimestre: QUARTER(colonne)                          ║
  ║  Date courante     : CURDATE()                                 ║
  ║  Diff de dates     : DATEDIFF(date_fin, date_debut)  ← ordre ! ║
  ║  Valeur nulle      : IFNULL(col, valeur_defaut)               ║
  ║  Colonnes spéciales: entourer de backticks `col accentuée`     ║
  ╚════════════════════════════════════════════════════════════════╝

  EXEMPLES VALIDES :

  -- Top 5 articles par CA
  SELECT Code_Article, SUM(CA_TND) AS CA
  FROM FACT_Vente
  GROUP BY Code_Article
  ORDER BY CA DESC
  LIMIT 5;

  -- Trimestre avec le plus de retards de paiement
  SELECT QUARTER(Date_Paiement) AS Trimestre, COUNT(*) AS Nb_Retards
  FROM FACT_Recouvrement_Client
  WHERE YEAR(Date_Paiement) = 2024
    AND Date_Paiement > Date_Echeance
  GROUP BY QUARTER(Date_Paiement)
  ORDER BY Nb_Retards DESC
  LIMIT 1;

  -- CA par mois 2025
  SELECT MONTH(Date_Facture) AS Mois, SUM(CA_TND) AS CA
  FROM FACT_Vente
  WHERE YEAR(Date_Facture) = 2025
  GROUP BY MONTH(Date_Facture)
  ORDER BY Mois;

  -- Lots expirant dans 30 jours
  SELECT Article, Num_Lot, Date_Exp
  FROM Fact_Prod_Stocks
  WHERE Date_Exp <= DATE_ADD(CURDATE(), INTERVAL 30 DAY);

  -- Délai moyen de livraison fournisseur (DATEDIFF = fin, debut)
  SELECT AVG(DATEDIFF(r.Date_Reception, oa.Date_Commande)) AS Delai_Moyen
  FROM FACT_Ordre_Achat oa
  JOIN FACT_Assoc_Reception r ON r.Num_Commande = oa.Num_Commande;""",

    "sqlite": """\
Règles syntaxiques SQLITE :
  - Limiter les lignes    : LIMIT N  (jamais TOP N)
  - Extraire l'année      : strftime('%Y', colonne) = '2024'
  - Extraire le mois      : strftime('%m', colonne)
  - Extraire le trimestre : CAST((strftime('%m', colonne)+2)/3 AS INT)
  - Date courante         : DATE('now')
  - Différence de dates   : julianday(d1) - julianday(d2)""",

    "postgresql": """\
Règles syntaxiques POSTGRESQL :
  - Limiter les lignes    : LIMIT N  (jamais TOP N)
  - Extraire l'année      : EXTRACT(YEAR FROM colonne) = 2024
  - Extraire le mois      : EXTRACT(MONTH FROM colonne)
  - Extraire le trimestre : EXTRACT(QUARTER FROM colonne)
  - Date courante         : CURRENT_DATE
  - Différence de dates   : colonne::date - colonne2::date""",

    "mssql": """\
Règles syntaxiques MSSQL / SQL Server — STRICTEMENT OBLIGATOIRES :

  ╔══ INTERDIT ════════════════════════════════════════════════════╗
  ║  LIMIT N    → INTERDIT. N'existe pas en SQL Server.           ║
  ║  NOW()      → INTERDIT. Utiliser GETDATE()                    ║
  ║  IFNULL()   → INTERDIT. Utiliser ISNULL()                     ║
  ╚════════════════════════════════════════════════════════════════╝

  ╔══ OBLIGATOIRE ══════════════════════════════════════════════════╗
  ║  Limiter N lignes  : SELECT TOP N ...  (dans le SELECT)        ║
  ║  Extraire l'année  : YEAR(colonne) = 2024                      ║
  ║  Extraire le mois  : MONTH(colonne)                            ║
  ║  Extraire trimestre: DATEPART(quarter, colonne)                 ║
  ║  Date courante     : GETDATE()                                 ║
  ║  Diff de dates     : DATEDIFF(day, date_debut, date_fin)       ║
  ║  Valeur nulle      : ISNULL(col, valeur_defaut)                ║
  ╚════════════════════════════════════════════════════════════════╝""",
}

_DIALECT_GUIDE_DEFAULT = """\
Règles syntaxiques génériques :
  - Limiter les lignes : LIMIT N
  - Extraire l'année   : YEAR(colonne)
  - Date courante      : CURRENT_DATE"""


def _get_dialect_guide(dialect: str) -> str:
    return _DIALECT_GUIDE.get(dialect.lower().strip(), _DIALECT_GUIDE_DEFAULT)


def _needs_mssql_fix(dialect: str) -> bool:
    """Retourne True si le dialecte est MSSQL et que le post-processeur est dispo."""
    return _POSTPROCESS_AVAILABLE and _is_mssql(dialect)


# ── Prompts SQL ───────────────────────────────────────────────────────────────
_SQL_SYSTEM = (
    "Tu es un expert SQL. Tu génères UNE requête SQL SELECT valide et lisible "
    "à partir d'une question canonique et du schéma fourni. Tu n'inventes JAMAIS "
    "de table ou de colonne hors schéma : utilise STRICTEMENT les noms listés. "
    "Tu respectes IMPÉRATIVEMENT la syntaxe du SGBD cible indiqué. "
    "Réponds avec la requête SQL uniquement, dans un bloc ```sql ... ```."
)

_SQL_PROMPT = """\
{dialect_guide}

Schéma disponible (tables et colonnes EXACTES — n'utilise rien d'autre) :
{schema}

Question canonique : "{rewritten_query}"

Indications issues de l'analyse sémantique (À RESPECTER IMPÉRATIVEMENT) :
{sir_hints}

Contraintes générales :
- Une seule requête SELECT (lecture seule).
- Utilise UNIQUEMENT les noms de tables/colonnes du schéma ci-dessus.
- Si une MÉTRIQUE est indiquée, utilise-la telle quelle.
- Ajoute les jointures nécessaires d'après les clés étrangères.
- Respecte STRICTEMENT la syntaxe du SGBD indiquée ci-dessus.

Renvoie uniquement la requête SQL dans un bloc ```sql ... ```.
"""

_SQL_FIX_PROMPT = """\
{dialect_guide}

Schéma disponible (tables et colonnes EXACTES — n'utilise rien d'autre) :
{schema}

Question canonique : "{rewritten_query}"

La requête suivante a ÉCHOUÉ à l'exécution :
```sql
{bad_sql}
```
Erreur renvoyée par la base : {error}

Corrige la requête en respectant STRICTEMENT les règles syntaxiques ci-dessus.
Renvoie uniquement la requête SQL corrigée dans un bloc ```sql ... ```.
"""


# ── Helpers de formatage ──────────────────────────────────────────────────────

def _format_knowledge(entries: list[KBEntry]) -> str:
    return "\n".join(f"- [{e.label}] {e.name} : {e.description}"
                     for e in entries) or "(aucune)"


def _format_demos(entries: list[KBEntry]) -> str:
    demos = [e for e in entries if e.kb_type == "task_material"]
    if not demos:
        return "(aucune)"
    return "\n".join(
        f'- Q: "{e.payload.get("question")}" -> SQL: {e.payload.get("sql")}'
        for e in demos
    )


def _format_schema(entries: list[KBEntry]) -> str:
    tables: dict[str, list[str]] = {}
    fks: list[str] = []
    for e in entries:
        if e.label == "Column":
            t = e.payload.get("table")
            tables.setdefault(t, []).append(e.payload.get("column"))
        elif e.label == "Table":
            for fk in e.payload.get("foreign_keys", []):
                fks.append(
                    f"{e.name}.{','.join(fk['constrained_columns'])} -> "
                    f"{fk['referred_table']}.{','.join(fk['referred_columns'])}"
                )
            tables.setdefault(e.name, tables.get(e.name, []))
    lines = []
    for t, cols in tables.items():
        col_str = ", ".join(c for c in cols if c) or "(colonnes non précisées)"
        lines.append(f"Table {t} : {col_str}")
    if fks:
        lines.append("Clés étrangères : " + " ; ".join(fks))
    return "\n".join(lines) or "(schéma vide)"


def _format_sir_hints(sir: "SIRResult") -> str:
    data = sir.sir_json or {}
    kc = data.get("Key Components", {}) if isinstance(data, dict) else {}
    lines = []
    if metric := kc.get("Metric"):
        lines.append(f"- MÉTRIQUE à utiliser : {metric}")
    if dims := kc.get("Dimensions"):
        lines.append(f"- Dimensions : {dims}")
    if tr := kc.get("Time Range"):
        lines.append(f"- Période : {tr}")
    if filters := kc.get("Filters"):
        lines.append(f"- Filtres : {filters}")
    if mapping := (data.get("Knowledge Mapping") if isinstance(data, dict) else None):
        lines.append(f"- Mapping des termes : {mapping}")
    return "\n".join(lines) or "(aucune indication particulière)"


# ── Classe principale ─────────────────────────────────────────────────────────

class SQLGenerator:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def to_sir(self, question: str, knowledge: list[KBEntry]) -> SIRResult:
        prompt = _SIR_PROMPT.format(
            knowledge=_format_knowledge(knowledge),
            demos=_format_demos(knowledge),
            question=question,
        )
        raw = self.llm.chat(_SIR_SYSTEM, prompt)
        data = LLMClient.extract_json(raw)
        return SIRResult(
            rewritten_query=data.get("Rewritten Query", question),
            sir_json=data,
        )

    def to_sql(self, sir: SIRResult, knowledge: list[KBEntry],
               dialect: str = "mysql", schema: str | None = None) -> str:
        prompt = _SQL_PROMPT.format(
            dialect_guide=_get_dialect_guide(dialect),
            schema=schema or _format_schema(knowledge),
            rewritten_query=sir.rewritten_query,
            sir_hints=_format_sir_hints(sir),
        )
        raw = self.llm.chat(_SQL_SYSTEM, prompt)
        sql = LLMClient.extract_sql(raw)
        # Post-processeur déterministe MSSQL (optionnel, non actif en MySQL)
        if _needs_mssql_fix(dialect):
            sql = enforce_mssql(sql)
        return sql

    def fix_sql(self, sir: SIRResult, knowledge: list[KBEntry],
                bad_sql: str, error: str, dialect: str = "mysql",
                schema: str | None = None) -> str:
        prompt = _SQL_FIX_PROMPT.format(
            dialect_guide=_get_dialect_guide(dialect),
            schema=schema or _format_schema(knowledge),
            rewritten_query=sir.rewritten_query,
            bad_sql=bad_sql,
            error=error,
        )
        raw = self.llm.chat(_SQL_SYSTEM, prompt)
        sql = LLMClient.extract_sql(raw)
        if _needs_mssql_fix(dialect):
            sql = enforce_mssql(sql)
        return sql  