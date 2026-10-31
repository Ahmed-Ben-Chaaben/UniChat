# -*- coding: utf-8 -*-
"""
config.py — Configuration centralisée depuis les variables d'environnement.

Deux bases de données :
  DBConfig    → TestDB_DataWarehouse (SQLAlchemy, compte bi_nl2sql)
  DBSecConfig → bi_securite (pymysql natif, compte bi_app)

Variables requises dans .env :
  DB_URL              mysql+pymysql://bi_nl2sql:mdp@localhost:3306/TestDB_DataWarehouse
  BI_SEC_URL          mysql+pymysql://bi_app:mdp@localhost:3306/bi_securite
  KEYCLOAK_SERVER_URL
  KEYCLOAK_REALM
  KEYCLOAK_CLIENT_ID
  KEYCLOAK_ADMIN_LOGIN
  KEYCLOAK_ADMIN_PASSWORD
  KEYCLOAK_CLIENT_SECRET  (optionnel)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _requis(nom: str) -> str:
    valeur = os.environ.get(nom)
    if not valeur:
        raise RuntimeError(f"Variable d'environnement manquante : {nom}")
    return valeur


# ── Base métier (TestDB_DataWarehouse, compte bi_nl2sql) ──────────────────────

@dataclass
class DBConfig:
    """URL SQLAlchemy pour le pipeline NL→SQL."""
    connection_string: str

    @classmethod
    def depuis_env(cls) -> "DBConfig":
        return cls(connection_string=_requis("DB_URL"))


# ── Base sécurité (bi_securite, compte bi_app) ───────────────────────────────

@dataclass
class DBSecConfig:
    """URL mysql+pymysql pour bi_securite.

    Utilise BI_SEC_URL (priorité) sinon construit depuis BI_SEC_* individuels.
    Compatible avec pymysqlSecConnection._resolve_connection_string().
    """
    connection_string: str

    @classmethod
    def depuis_env(cls) -> "DBSecConfig":
        # Priorité 1 : URL complète
        url = os.environ.get("BI_SEC_URL")
        if url:
            return cls(connection_string=url)

        # Priorité 2 : paramètres individuels → construire l'URL
        host     = os.environ.get("BI_SEC_HOST", "localhost")
        port     = os.environ.get("BI_SEC_PORT", "3306")
        user     = _requis("BI_SEC_USER")
        password = _requis("BI_SEC_PASSWORD")
        db       = os.environ.get("BI_SEC_DB", "bi_securite")
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"
        return cls(connection_string=url)

    def creer_connexion(self) -> "pymysqlSecConnection":
        from security.db_sec import pymysqlSecConnection
        return pymysqlSecConnection(self.connection_string)


# ── Keycloak (authentification utilisateur) ──────────────────────────────────

@dataclass
class KeycloakConfig:
    server_url: str
    realm: str
    client_id: str
    client_secret: str | None

    @classmethod
    def depuis_env(cls) -> "KeycloakConfig":
        return cls(
            server_url=_requis("KEYCLOAK_SERVER_URL"),
            realm=_requis("KEYCLOAK_REALM"),
            client_id=_requis("KEYCLOAK_CLIENT_ID"),
            client_secret=os.environ.get("KEYCLOAK_CLIENT_SECRET"),
        )


# ── Keycloak Admin (gestion des comptes depuis l'interface admin) ─────────────

@dataclass
class KeycloakAdminClientdata:
    server_url: str
    realm: str
    admin_login: str
    admin_password: str

    @classmethod
    def depuis_env(cls) -> "KeycloakAdminClientdata":
        return cls(
            server_url=_requis("KEYCLOAK_SERVER_URL"),
            realm=_requis("KEYCLOAK_REALM"),
            admin_login=_requis("KEYCLOAK_ADMIN_LOGIN"),
            admin_password=_requis("KEYCLOAK_ADMIN_PASSWORD"),
        )


# ── LLM ──────────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    modele: str

    @classmethod
    def depuis_env(cls) -> "LLMConfig":
        return cls(modele=os.environ.get("LLM_MODEL_CLOUD", "gemma4:31b"))