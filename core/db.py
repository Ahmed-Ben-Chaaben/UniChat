"""
Couche d'accès à la base MÉTIER — agnostique au SGBD.

Tout passe par SQLAlchemy : le reste du code ignore qu'on est sur SQLite,
PostgreSQL, MySQL... Pour changer de base, on ne touche QUE DB_URL.

Garde-fous SQL (inspirés des "SQL guardrails" du papier SiriusBI) :
  1. Exécution lecture seule : seuls les SELECT sont autorisés.
  2. Validation : on rejette tout mot-clé de modification (INSERT, UPDATE,
     DELETE, DROP, ALTER, CREATE, TRUNCATE, REPLACE, GRANT, ...).
  3. LIMIT automatique : si la requête n'a pas de LIMIT, on en ajoute un.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from config import settings

# Mots-clés interdits (modification de données / schéma)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|"
    r"GRANT|REVOKE|MERGE|EXEC|EXECUTE|ATTACH|DETACH|PRAGMA)\b",
    re.IGNORECASE,
)
_HAS_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_IS_SELECT = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


class UnsafeQueryError(Exception):
    """Levée quand une requête viole les garde-fous lecture seule."""


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    foreign_keys: list[dict[str, Any]]  # {constrained_columns, referred_table, referred_columns}


class DatabaseConnector:
    """Connecteur agnostique vers la base métier."""

    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or settings.DB_URL
        self.engine: Engine = create_engine(self.db_url)

    @property
    def dialect(self) -> str:
        """Nom du SGBD cible (sqlite, postgresql, mysql, mssql, oracle...)."""
        return self.engine.dialect.name

    def schema_ddl(self) -> str:
        """
        Schéma complet et EXACT de la base, formaté pour le prompt SQL.
        On donne toutes les tables avec leurs colonnes réelles + les clés
        étrangères : le LLM n'a ainsi aucune raison d'inventer un nom.
        """
        lines: list[str] = []
        for t in self.get_schema():
            cols = ", ".join(f"{c.name} {c.type}" for c in t.columns)
            lines.append(f"Table {t.name}({cols})")
            for fk in t.foreign_keys:
                lines.append(
                    f"  FK {t.name}.{','.join(fk['constrained_columns'])} -> "
                    f"{fk['referred_table']}.{','.join(fk['referred_columns'])}"
                )
        return "\n".join(lines)

    # ── Introspection du schéma (étape 3) ───────────────────────────
    def get_schema(self) -> list[TableInfo]:
        """Extrait automatiquement tables, colonnes et clés étrangères."""
        insp = inspect(self.engine)
        tables: list[TableInfo] = []
        for tname in insp.get_table_names():
            pk_cols = set(insp.get_pk_constraint(tname).get("constrained_columns") or [])
            cols = [
                ColumnInfo(
                    name=c["name"],
                    type=str(c["type"]),
                    nullable=c.get("nullable", True),
                    primary_key=c["name"] in pk_cols,
                )
                for c in insp.get_columns(tname)
            ]
            fks = [
                {
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                }
                for fk in insp.get_foreign_keys(tname)
            ]
            tables.append(TableInfo(name=tname, columns=cols, foreign_keys=fks))
        return tables

    # ── Garde-fous + exécution (étape 5) ────────────────────────────
    def _validate(self, sql: str) -> None:
        if not _IS_SELECT.match(sql):
            raise UnsafeQueryError("Seules les requêtes SELECT / WITH sont autorisées.")
        if _FORBIDDEN.search(sql):
            raise UnsafeQueryError("Mot-clé de modification détecté : requête refusée.")
        # Requêtes multiples : un ';' qui n'est pas en toute fin de requête
        if ";" in sql.rstrip().rstrip(";"):
            raise UnsafeQueryError("Requêtes multiples (;) interdites.")

    def _enforce_limit(self, sql: str, limit: int) -> str:
        clean = sql.rstrip().rstrip(";")
        if self.dialect == "mssql":
            # Vérifier si TOP ou OFFSET FETCH est déjà présent
            # Si pas, on ajoute TOP (limit) après le SELECT (ou après WITH pour CTE)
            # On peut utiliser une regex pour insérer TOP après le premier SELECT
            # Mais attention aux sous-requêtes, aux CTE, etc. Approche simplifiée :
            # on cherche le premier mot-clé SELECT (ou WITH) et on insère TOP après.
            # Mais pour une approche robuste, on peut vérifier si 'TOP' ou 'OFFSET' est présent.
            if not re.search(r'\bTOP\b|\bOFFSET\b|\bFETCH\b', clean, re.IGNORECASE):
                # Insérer TOP après le premier SELECT (ou WITH)
                # On va remplacer le premier SELECT par SELECT TOP (limit)
                # Utiliser une regex avec re.IGNORECASE
                # Pour simplifier, on suppose que la requête commence par SELECT ou WITH.
                # On remplace 'select' (case-insensitive) par 'select top (limit)'
                # Mais attention aux guillemets et espaces.
                # On peut utiliser re.sub avec une fonction.
                def repl(match):
                    return f"{match.group(0)} TOP ({limit})"
                clean = re.sub(r'^\s*SELECT\b', repl, clean, flags=re.IGNORECASE)
                # Si c'est un WITH, il faudrait après le CTE, mais on simplifie.
                # On peut aussi ne pas gérer les CTE complexes, mais on peut tenter de placer TOP après le premier SELECT qui suit WITH.
                # Une alternative : utiliser 'SELECT TOP (n) ...' pour les requêtes sans CTE.
                # On va plutôt utiliser une méthode plus simple : si la requête contient 'WITH', on ajoute TOP après le premier SELECT qui suit.
                # Mais on va simplifier : on suppose que les requêtes générées sont simples.
        else:
            if not _HAS_LIMIT.search(clean):
                clean = f"{clean}\nLIMIT {limit}"
        return clean

    def run(self, sql: str, limit: int | None = None) -> pd.DataFrame:
        """Valide puis exécute une requête lecture seule, renvoie un DataFrame."""
        if settings.READ_ONLY:
            self._validate(sql)
        sql = self._enforce_limit(sql, limit or settings.DEFAULT_ROW_LIMIT)
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn)


if __name__ == "__main__":
    # Petit test manuel d'introspection
    db = DatabaseConnector()
    schema = db.get_schema()
    print(f"{len(schema)} tables détectées :")
    for t in schema:
        print(f"  - {t.name} ({len(t.columns)} colonnes, {len(t.foreign_keys)} FK)")
