# -*- coding: utf-8 -*-
"""
Package sécurité de l'assistant BI.

Exports publics utilisés par pipeline.py et app.py :
    KeycloakAuthenticator  — authentification Keycloak
    AuthorizationService   — RBAC : filtrage schéma + validation SQL
    SQLValidator           — 2e ligne de défense post-génération
    AuditLogger            — journalisation systématique
    pymysqlSecConnection   — connexion dédiée SEC_*/AUDIT_*
    Role, Utilisateur, Session, Interaction  — modèles de données
"""
from .auth import KeycloakAuthenticator
from .authorization import AuthorizationService, SQLValidator
from .audit import AuditLogger
from .db_sec import pymysqlSecConnection
from .models import Role, Utilisateur, Session, Interaction

__all__ = [
    "KeycloakAuthenticator",
    "AuthorizationService",
    "SQLValidator",
    "AuditLogger",
    "pymysqlSecConnection",
    "Role",
    "Utilisateur",
    "Session",
    "Interaction",
]
