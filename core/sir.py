# """
# SQL Generation two-step via SIR (papier SiriusBI §3.4.4).
# """
# from __future__ import annotations
# from dataclasses import dataclass

# from core.LLM_cloud import LLMClient
# from core.sql_postprocess import enforce_mssql, is_mssql
# from knowledge.builder import KBEntry


# @dataclass
# class SIRResult:
#     rewritten_query: str
#     sir_json: dict


# # ── Étape 1 : SIR ────────────────────────────────────────────────────────────
# _SIR_SYSTEM = (
#     "Tu es un expert en analyse sémantique de requêtes BI. Tu transformes une "
#     "question en langage naturel en une représentation intermédiaire structurée, "
#     "en raisonnant étape par étape (chain-of-thought). Réponds UNIQUEMENT en JSON."
# )

# _SIR_PROMPT = """\
# Connaissance récupérée (colonnes, termes, exemples) :
# {knowledge}

# Exemples de démonstration (few-shot) :
# {demos}

# Question : "{question}"

# Raisonne étape par étape puis renvoie un JSON avec ces clés :
# {{
#   "Key Components": {{
#      "Metric": "<colonne/agrégat de la mesure>",
#      "Dimensions": ["<axes d'analyse>"],
#      "Time Range": "<période ou null>",
#      "Filters": ["<filtres explicites>"]
#   }},
#   "Knowledge Mapping": ["<terme utilisateur -> colonne réelle>"],
#   "Query Understanding": "<intention en une phrase>",
#   "Rewritten Query": "<question canonique, complète, autonome, sans ambiguïté>"
# }}
# """

# # ── Table de référence dialecte ───────────────────────────────────────────────
# _DIALECT_GUIDE = {
#     "mssql": """\
# Règles syntaxiques MSSQL / SQL Server — STRICTEMENT OBLIGATOIRES :

#   ╔══ INTERDIT — ne jamais utiliser ══════════════════════════════╗
#   ║  LIMIT N       → n'existe pas en SQL Server                   ║
#   ║  NOW()         → utiliser GETDATE()                           ║
#   ║  IFNULL()      → utiliser ISNULL()                            ║
#   ║  EXTRACT(...)  → utiliser DATEPART(partie, colonne)           ║
#   ╚═══════════════════════════════════════════════════════════════╝

#   ╔══ OBLIGATOIRE ════════════════════════════════════════════════╗
#   ║  Limiter N lignes   : SELECT TOP N ...  (dans le SELECT)      ║
#   ║  Extraire l'année   : YEAR(colonne) = 2024                    ║
#   ║  Extraire le mois   : MONTH(colonne)                          ║
#   ║  Extraire trimestre : DATEPART(quarter, colonne)              ║
#   ║  Date courante      : GETDATE()                               ║
#   ║  Diff de dates      : DATEDIFF(day, date_debut, date_fin)     ║
#   ║  Valeur nulle       : ISNULL(col, valeur_defaut)              ║
#   ╚═══════════════════════════════════════════════════════════════╝

#   EXEMPLES VALIDES :

#   -- 100 derniers jours de vente distincts
#   SELECT DISTINCT TOP 100 CAST(Date_Facture AS DATE) AS Jour_Vente
#   FROM FACT_Vente
#   ORDER BY Jour_Vente DESC;

#   -- Trimestre avec le plus de retards de paiement
#   SELECT TOP 1
#       DATEPART(quarter, Date_Paiement) AS Trimestre,
#       COUNT(*) AS Nb_Retards
#   FROM FACT_Recouvrement_Client
#   WHERE YEAR(Date_Paiement) = 2024
#     AND Date_Paiement > Date_Echeance
#   GROUP BY DATEPART(quarter, Date_Paiement)
#   ORDER BY COUNT(*) DESC;

#   -- CA mensuel 2025
#   SELECT MONTH(Date_Facture) AS Mois, SUM(CA_TND) AS CA
#   FROM FACT_Vente
#   WHERE YEAR(Date_Facture) = 2025
#   GROUP BY MONTH(Date_Facture)
#   ORDER BY Mois;""",
# }

# _DIALECT_GUIDE_DEFAULT = """\
# Règles syntaxiques génériques :
#   - Limiter les lignes : LIMIT N
#   - Extraire l'année   : YEAR(colonne)
#   - Date courante      : CURRENT_DATE"""


# def _get_dialect_guide(dialect: str) -> str:
#     # Normalise : "mssql+pyodbc" → clé "mssql"
#     key = "mssql" if is_mssql(dialect) else dialect.lower().strip()
#     return _DIALECT_GUIDE.get(key, _DIALECT_GUIDE_DEFAULT)


# # ── Prompts SQL ───────────────────────────────────────────────────────────────
# _SQL_SYSTEM = (
#     "Tu es un expert SQL. Tu génères UNE requête SQL SELECT valide et lisible "
#     "à partir d'une question canonique et du schéma fourni. Tu n'inventes JAMAIS "
#     "de table ou de colonne hors schéma : utilise STRICTEMENT les noms listés. "
#     "Tu respectes IMPÉRATIVEMENT la syntaxe du SGBD cible indiqué. "
#     "Réponds avec la requête SQL uniquement, dans un bloc ```sql ... ```."
# )

# _SQL_PROMPT = """\
# {dialect_guide}

# Schéma disponible (tables et colonnes EXACTES — n'utilise rien d'autre) :
# {schema}

# Question canonique : "{rewritten_query}"

# Indications issues de l'analyse sémantique (À RESPECTER IMPÉRATIVEMENT) :
# {sir_hints}

# Contraintes générales :
# - Une seule requête SELECT (lecture seule).
# - Utilise UNIQUEMENT les noms de tables/colonnes du schéma ci-dessus.
# - Si une MÉTRIQUE est indiquée, utilise-la telle quelle.
# - Ajoute les jointures nécessaires d'après les clés étrangères du schéma.
# - Respecte STRICTEMENT la syntaxe du SGBD indiquée ci-dessus.

# Renvoie uniquement la requête SQL dans un bloc ```sql ... ```.
# """

# _SQL_FIX_PROMPT = """\
# {dialect_guide}

# Schéma disponible (tables et colonnes EXACTES — n'utilise rien d'autre) :
# {schema}

# Question canonique : "{rewritten_query}"

# La requête suivante a ÉCHOUÉ à l'exécution :
# ```sql
# {bad_sql}
# ```
# Erreur renvoyée par la base : {error}

# Corrige la requête en respectant STRICTEMENT les règles syntaxiques ci-dessus.
# Renvoie uniquement la requête SQL corrigée dans un bloc ```sql ... ```.
# """


# # ── Helpers de formatage ──────────────────────────────────────────────────────

# def _format_knowledge(entries: list[KBEntry]) -> str:
#     return "\n".join(f"- [{e.label}] {e.name} : {e.description}"
#                      for e in entries) or "(aucune)"


# def _format_demos(entries: list[KBEntry]) -> str:
#     demos = [e for e in entries if e.kb_type == "task_material"]
#     if not demos:
#         return "(aucune)"
#     return "\n".join(
#         f'- Q: "{e.payload.get("question")}" -> SQL: {e.payload.get("sql")}'
#         for e in demos
#     )


# def _format_schema(entries: list[KBEntry]) -> str:
#     tables: dict[str, list[str]] = {}
#     fks: list[str] = []
#     for e in entries:
#         if e.label == "Column":
#             t = e.payload.get("table")
#             tables.setdefault(t, []).append(e.payload.get("column"))
#         elif e.label == "Table":
#             for fk in e.payload.get("foreign_keys", []):
#                 fks.append(
#                     f"{e.name}.{','.join(fk['constrained_columns'])} -> "
#                     f"{fk['referred_table']}.{','.join(fk['referred_columns'])}"
#                 )
#             tables.setdefault(e.name, tables.get(e.name, []))
#     lines = []
#     for t, cols in tables.items():
#         col_str = ", ".join(c for c in cols if c) or "(colonnes non précisées)"
#         lines.append(f"Table {t} : {col_str}")
#     if fks:
#         lines.append("Clés étrangères : " + " ; ".join(fks))
#     return "\n".join(lines) or "(schéma vide)"


# def _format_sir_hints(sir: "SIRResult") -> str:
#     data = sir.sir_json or {}
#     kc = data.get("Key Components", {}) if isinstance(data, dict) else {}
#     lines = []
#     if metric := kc.get("Metric"):
#         lines.append(f"- MÉTRIQUE à utiliser : {metric}")
#     if dims := kc.get("Dimensions"):
#         lines.append(f"- Dimensions : {dims}")
#     if tr := kc.get("Time Range"):
#         lines.append(f"- Période : {tr}")
#     if filters := kc.get("Filters"):
#         lines.append(f"- Filtres : {filters}")
#     if mapping := (data.get("Knowledge Mapping") if isinstance(data, dict) else None):
#         lines.append(f"- Mapping des termes : {mapping}")
#     return "\n".join(lines) or "(aucune indication particulière)"


# # ── Classe principale ─────────────────────────────────────────────────────────

# class SQLGenerator:
#     def __init__(self, llm: LLMClient | None = None):
#         self.llm = llm or LLMClient()

#     def to_sir(self, question: str, knowledge: list[KBEntry]) -> SIRResult:
#         prompt = _SIR_PROMPT.format(
#             knowledge=_format_knowledge(knowledge),
#             demos=_format_demos(knowledge),
#             question=question,
#         )
#         raw = self.llm.chat(_SIR_SYSTEM, prompt)
#         data = LLMClient.extract_json(raw)
#         return SIRResult(
#             rewritten_query=data.get("Rewritten Query", question),
#             sir_json=data,
#         )

#     def to_sql(self, sir: SIRResult, knowledge: list[KBEntry],
#                dialect: str = "mssql", schema: str | None = None) -> str:
#         prompt = _SQL_PROMPT.format(
#             dialect_guide=_get_dialect_guide(dialect),
#             schema=schema or _format_schema(knowledge),
#             rewritten_query=sir.rewritten_query,
#             sir_hints=_format_sir_hints(sir),
#         )
#         raw = self.llm.chat(_SQL_SYSTEM, prompt)
#         sql = LLMClient.extract_sql(raw)
#         # Post-processeur déterministe — actif pour tout variant MSSQL.
#         # Corrige LIMIT→TOP, IFNULL→ISNULL, NOW()→GETDATE(), etc.
#         if is_mssql(dialect):
#             sql = enforce_mssql(sql)
#         return sql

#     def fix_sql(self, sir: SIRResult, knowledge: list[KBEntry],
#                 bad_sql: str, error: str, dialect: str = "mssql",
#                 schema: str | None = None) -> str:
#         """Re-génère le SQL en fournissant au LLM la requête fautive + l'erreur."""
#         prompt = _SQL_FIX_PROMPT.format(
#             dialect_guide=_get_dialect_guide(dialect),
#             schema=schema or _format_schema(knowledge),
#             rewritten_query=sir.rewritten_query,
#             bad_sql=bad_sql,
#             error=error,
#         )
#         raw = self.llm.chat(_SQL_SYSTEM, prompt)
#         sql = LLMClient.extract_sql(raw)
#         # Post-processeur aussi sur le fix — le LLM peut récidiver
#         if is_mssql(dialect):
#             sql = enforce_mssql(sql)
#         return sql

####################################################################


"""
SQL Generation two-step via SIR (papier SiriusBI §3.4.4).
"""
from __future__ import annotations
from dataclasses import dataclass

from core.LLM_cloud import LLMClient
from knowledge.builder import KBEntry


@dataclass
class SIRResult:
    rewritten_query: str
    sir_json: dict


_SIR_SYSTEM = """\
Tu es un expert en analyse sémantique de requêtes BI sur un data warehouse
industriel/pharmaceutique (SQL Server / MSSQL).

RÈGLES DE COMPRÉHENSION SÉMANTIQUE :

1. ANTI-JOINTURES — quand la question contient "jamais", "aucun", "sans",
   "n'ont pas", "n'existe pas" :
   → utilise TOUJOURS NOT EXISTS (jamais NOT IN, car NOT IN échoue sur NULL)
   → pattern : WHERE NOT EXISTS (SELECT 1 FROM ... WHERE ...)

2. RATIO / POURCENTAGE — quand la question contient "proportion", "part",
   "pourcentage", "%", "taux", "ratio", "par rapport" :
   → la métrique principale EST un ratio : CAST(... AS FLOAT) / NULLIF(total, 0)
   → mentionner dans Rewritten Query "en pourcentage"

3. CLASSEMENT / RANG — quand la question contient "rang", "classement",
   "le plus", "les N premiers", "top", "ordre" :
   → ajouter ROW_NUMBER() OVER (ORDER BY ...) AS Rang dans le SELECT
   → ou ORDER BY ... ASC/DESC explicite avec "du plus grand au plus petit"

4. VALEURS DE FILTRE — quand la question filtre sur une valeur
   (ex: motif='MOR0005', année=2024, région='Tunis') :
   → inclure la valeur du filtre dans le SELECT comme colonne constante
   → ex: 'MOR0005' AS Motif_Filtre, 2024 AS Annee_Filtre

5. AMBIGUÏTÉ FK/PK — quand une table a une FK vers une autre :
   → bien distinguer "compter les X par Y" :
      si Y est une FK dans la table → GROUP BY Y, COUNT(DISTINCT PK)
      ex: DIM_Region.Code_Client est FK vers DIM_Client
          "nombre de régions par client" → GROUP BY Code_Client, COUNT(Code_Region)
          "nombre de clients par région" → GROUP BY Code_Region, COUNT(DISTINCT Code_Client)

6. DIMENSION vs MESURE — ne jamais confondre une colonne FK avec une mesure.

Réponds UNIQUEMENT en JSON.
"""

_SIR_PROMPT = """\
Connaissance récupérée (colonnes, termes, exemples) :
{knowledge}

Exemples de démonstration (few-shot) :
{demos}

Question : "{question}"

Applique les règles sémantiques (anti-jointure, ratio, rang, filtre, FK/PK)
puis renvoie un JSON avec ces clés :
{{
  "Key Components": {{
     "Metric": "<agrégat exact, ex: COUNT(DISTINCT Code_Client) ou ratio en %>",
     "Dimensions": ["<axes d'analyse>"],
     "Time Range": "<période ou null>",
     "Filters": ["<filtre=valeur, ex: Motif=MOR0005>"],
     "Anti_Join": "<table cible si NOT EXISTS, sinon null>",
     "Has_Ratio": "<true si pourcentage demandé, sinon false>",
     "Has_Rank": "<true si classement demandé, sinon false>",
     "Filter_Values": ["<valeurs de filtres à afficher dans le résultat>"]
  }},
  "Knowledge Mapping": ["<terme utilisateur -> colonne réelle>"],
  "Query Understanding": "<intention précise en une phrase>",
  "Rewritten Query": "<question canonique, complète, sans ambiguïté>"
}}
"""

_SQL_SYSTEM = """\
Tu es un expert SQL Server (MSSQL). Tu génères des requêtes SQL Server valides
et lisibles.

━━━ RÈGLES MSSQL ABSOLUES ━━━
  • TOP N lignes      → SELECT TOP N ...  ou  SELECT DISTINCT TOP N ...
                        (LIMIT n'existe pas en SQL Server)
  • DISTINCT + TOP    → SELECT DISTINCT TOP N col  (DISTINCT avant TOP est invalide)
                        Syntaxe correcte  : SELECT DISTINCT TOP 10 col FROM t
                        Syntaxe invalide  : SELECT TOP 10 DISTINCT col FROM t
                        Syntaxe invalide  : SELECT TOP (1000) DISTINCT col FROM t
  • Année             → YEAR(colonne)
  • Mois              → MONTH(colonne)
  • Trimestre         → DATEPART(quarter, colonne)
  • Date courante     → GETDATE()
  • Valeur nulle      → ISNULL(col, val)
  • Diff de dates     → DATEDIFF(day, date_debut, date_fin)

━━━ ANTI-JOINTURES ━━━
  Utilise TOUJOURS NOT EXISTS, jamais NOT IN (NOT IN échoue si la sous-requête
  contient des NULL).
  Pattern correct :
    SELECT col FROM TableA a
    WHERE NOT EXISTS (
        SELECT 1 FROM TableB b WHERE b.fk = a.pk
    );

━━━ RATIO / POURCENTAGE ━━━
  Si la question demande une proportion ou un pourcentage :
    CAST(partie AS FLOAT) * 100.0 / NULLIF(total, 0) AS Pourcentage
  Afficher le symbole % dans l'alias ou arrondir : ROUND(..., 2)

━━━ CLASSEMENT ━━━
  Si classement demandé (rang, top, le plus...) :
    ROW_NUMBER() OVER (ORDER BY metrique DESC) AS Rang
  ou ORDER BY metrique DESC avec TOP N.
  Toujours afficher le rang dans le SELECT.

━━━ VALEURS DE FILTRES ━━━
  Si la requête filtre sur une valeur littérale (ex: Code_Motif = 'MOR0005') :
  inclure cette valeur comme colonne dans le SELECT pour la visibilité :
    SELECT 'MOR0005' AS Motif_Filtre, COUNT(*) AS Nb ...
    WHERE Code_Motif = 'MOR0005'

━━━ FK vs PK — COMPTER CORRECTEMENT ━━━
  Quand une table a une FK, bien identifier ce qu'on compte :
  • DIM_Region : PK = Code_Region, FK = Code_Client
    - "nb de régions/adresses par client"  → GROUP BY Code_Client,
                                              COUNT(Code_Region) AS Nb_Adresses
    - "nb de clients par région"           → GROUP BY Code_Region,
                                              COUNT(DISTINCT Code_Client)
  • Règle générale : COUNT(DISTINCT PK) pour compter des entités uniques.

━━━ MOIS VIDES ━━━
  Si "tous les mois y compris sans données" → CTE de série + LEFT JOIN :
  WITH Mois AS (
      SELECT 1 AS M UNION SELECT 2 UNION ... UNION SELECT 12
  )
  SELECT m.M, ISNULL(SUM(v.CA_TND), 0) AS CA
  FROM Mois m
  LEFT JOIN FACT_Vente v ON MONTH(v.Date_Facture) = m.M AND YEAR(v.Date_Facture) = 2025
  GROUP BY m.M ORDER BY m.M;

Tu n'inventes JAMAIS de table ou de colonne absente du schéma fourni.
Réponds avec la requête SQL uniquement, dans un bloc ```sql … ```.
"""

_SQL_PROMPT = """\
Schéma disponible (utilise UNIQUEMENT ces tables et colonnes) :
{schema}

Question canonique : "{rewritten_query}"

Indications sémantiques (respecte-les impérativement) :
{sir_hints}

Renvoie uniquement la requête SQL dans un bloc ```sql … ```.
"""

_SQL_FIX_PROMPT = """\
Schéma disponible (utilise UNIQUEMENT ces tables et colonnes) :
{schema}

Question canonique : "{rewritten_query}"

Cette requête a échoué :
```sql
{bad_sql}
```
Erreur : {error}

Corrige-la en vérifiant :
  • SELECT DISTINCT TOP N  (pas SELECT TOP N DISTINCT ni SELECT TOP (N) DISTINCT)
  • NOT EXISTS à la place de NOT IN pour les anti-jointures
  • Uniquement les colonnes présentes dans le schéma ci-dessus
  • Jamais LIMIT — toujours SELECT TOP N

Renvoie uniquement la requête SQL corrigée dans un bloc ```sql … ```.
"""


def _format_knowledge(entries: list[KBEntry]) -> str:
    return "\n".join(f"- [{e.label}] {e.name} : {e.description}"
                     for e in entries) or "(aucune)"


def _format_demos(entries: list[KBEntry]) -> str:
    demos = [e for e in entries if e.kb_type == "task_material"]
    if not demos:
        return "(aucune)"
    return "\n".join(
        f'- Q: "{e.payload.get("question")}" → SQL: {e.payload.get("sql")}'
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
                    f"{e.name}.{','.join(fk['constrained_columns'])} → "
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
        lines.append(f"- MÉTRIQUE : {metric}")
    if dims := kc.get("Dimensions"):
        lines.append(f"- Dimensions : {dims}")
    if tr := kc.get("Time Range"):
        lines.append(f"- Période : {tr}")
    if filters := kc.get("Filters"):
        lines.append(f"- Filtres : {filters}")
    if aj := kc.get("Anti_Join"):
        lines.append(f"- ANTI-JOINTURE (NOT EXISTS) vers : {aj}")
    if kc.get("Has_Ratio") == "true":
        lines.append("- RATIO : calculer en pourcentage (CAST/NULLIF)")
    if kc.get("Has_Rank") == "true":
        lines.append("- CLASSEMENT : inclure ROW_NUMBER() AS Rang")
    if fv := kc.get("Filter_Values"):
        lines.append(f"- VALEURS DE FILTRE à afficher dans le SELECT : {fv}")
    if mapping := (data.get("Knowledge Mapping") if isinstance(data, dict) else None):
        lines.append(f"- Mapping : {mapping}")
    return "\n".join(lines) or "(aucune)"


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
               dialect: str = "mssql", schema: str | None = None) -> str:
        prompt = _SQL_PROMPT.format(
            schema=schema or _format_schema(knowledge),
            rewritten_query=sir.rewritten_query,
            sir_hints=_format_sir_hints(sir),
        )
        raw = self.llm.chat(_SQL_SYSTEM, prompt)
        return LLMClient.extract_sql(raw)

    def fix_sql(self, sir: SIRResult, knowledge: list[KBEntry],
                bad_sql: str, error: str,
                dialect: str = "mssql", schema: str | None = None) -> str:
        prompt = _SQL_FIX_PROMPT.format(
            schema=schema or _format_schema(knowledge),
            rewritten_query=sir.rewritten_query,
            bad_sql=bad_sql,
            error=error,
        )
        raw = self.llm.chat(_SQL_SYSTEM, prompt)
        return LLMClient.extract_sql(raw)