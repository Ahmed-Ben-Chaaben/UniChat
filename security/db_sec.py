# -*- coding: utf-8 -*-
"""
security/db_sec.py — Connexion pymysql pour bi_securite (SEC_* + AUDIT_*).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

import pymysql
import pymysql.cursors


def _resolve_connection_string(url: str) -> dict:
    """Convertit mysql+pymysql://user:pass@host:port/db en dict pymysql.connect().

    unquote_plus() gère les caractères spéciaux dans le mot de passe
    (ex: Mdp@2024! encodé en Mdp%402024%21 dans l'URL).
    """
    parsed = urlparse(url)
    # urlparse ne décode pas automatiquement password — on le fait manuellement
    password = unquote_plus(parsed.password) if parsed.password else None
    user     = unquote_plus(parsed.username) if parsed.username else None

    config = {
        "host":        parsed.hostname or "localhost",
        "port":        parsed.port or 3306,
        "user":        user,
        "password":    password,
        "database":    parsed.path.lstrip("/") or None,
        "charset":     "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }
    # Paramètres additionnels dans la query string
    for k, v in parse_qs(parsed.query).items():
        if k not in ("cursorclass", "charset"):
            config[k] = v[0]
    return config


class pymysqlSecConnection:
    """Connexion à bi_securite via pymysql.

    Accepte une URL mysql+pymysql://user:pass@host:port/db
    Les caractères spéciaux dans le mot de passe doivent être encodés URL
    (urllib.parse.quote_plus) ou l'URL doit être construite par DBSecConfig.
    """

    def __init__(self, connection_string: str) -> None:
        if not connection_string.startswith("mysql"):
            raise ValueError(
                "URL MySQL attendue : mysql+pymysql://user:pass@host:port/db"
            )
        self._config = _resolve_connection_string(connection_string)

    def _connect(self):
        return pymysql.connect(**self._config)

    def executer_lecture(self, sql: str,
                          params: tuple = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

    def executer_ecriture(self, sql: str, params: tuple = ()) -> int | None:
        """INSERT AUTO_INCREMENT → retourne lastrowid."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                identite = cur.lastrowid or None
            conn.commit()
            return identite

    def executer_ecriture_simple(self, sql: str,
                                  params: tuple = ()) -> None:
        """UPDATE / DELETE / INSERT sans retour de valeur."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
    def executer_insert_avec_lastrowid(
        self,
        sql: str,
        params: tuple = (),
    ) -> int:
        """
        Exécute un INSERT MySQL et retourne
        l'identifiant AUTO_INCREMENT généré.
        """
        connection = self.connection  # Adapt to your actual attribute name

        cursor = connection.cursor()

        try:
            cursor.execute(sql, params)
            insert_id = cursor.lastrowid
            connection.commit()

            if not insert_id:
                raise RuntimeError(
                    "Aucun identifiant AUTO_INCREMENT généré."
                )

            return int(insert_id)

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()
