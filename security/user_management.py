# -*- coding: utf-8 -*-
"""
security/user_management.py v3 — Adaptation MySQL
  Adaptations MSSQL → MySQL :
  1. OUTPUT INSERTED.UserId → LAST_INSERT_ID()
  2. CONVERT(NVARCHAR(19), ..., 120) → DATE_FORMAT(..., '%Y-%m-%d %H:%i:%s')
  3. Placeholders %s conservés (compatibles pymysql)
  4. backticks pour les identifiants si besoin
  5. DISTINCT + ORDER BY compatibles MySQL
"""
from __future__ import annotations

import hashlib
import os
import requests
from dataclasses import dataclass
from typing import Optional

from .db_sec import pymysqlSecConnection


@dataclass
class UserRow:
    user_id: int
    login: str
    nom_complet: Optional[str]
    email: Optional[str]
    role_id: int
    nom_role: str
    code_site: Optional[str]
    code_region: Optional[str]
    actif: bool
    derniere_connexion: Optional[str]


def _hash_mdp(mdp: str) -> str:
    """Hash SHA-256 simple du mot de passe temporaire.
    En production, utiliser bcrypt : pip install bcrypt.
    """
    try:
        import bcrypt
        return bcrypt.hashpw(mdp.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        return hashlib.sha256(mdp.encode()).hexdigest()


class KeycloakAdminClient:
    """Appels à l'API Keycloak Admin REST (inchangé)."""

    def __init__(self, server_url: str, realm: str,
                 admin_login: str, admin_password: str,
                 client_id: str = "admin-cli") -> None:
        base = server_url.rstrip("/")
        self._token_url = f"{base}/realms/master/protocol/openid-connect/token"
        self._users_base = f"{base}/admin/realms/{realm}/users"
        self._client_id = client_id
        self._admin_login = admin_login
        self._admin_password = admin_password

    def _get_admin_token(self) -> str:
        r = requests.post(self._token_url, data={
            "grant_type": "password",
            "client_id": self._client_id,
            "username": self._admin_login,
            "password": self._admin_password,
        }, timeout=10)
        if not r.ok:
            raise PermissionError(
                f"Keycloak admin auth échouée ({r.status_code}) : {r.text[:200]}"
            )
        return r.json()["access_token"]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_admin_token()}",
            "Content-Type": "application/json",
        }

    def creer_utilisateur_keycloak(self, login: str, email: str,
                           nom: str, mdp_temporaire: str) -> str:
        parts = nom.strip().split(" ", 1)
        payload = {
            "username": login,
            "email": email or "",
            "firstName": parts[0] if parts else "",
            "lastName": parts[1] if len(parts) > 1 else "",
            "enabled": True,
            "emailVerified": True,
            "credentials": [{
                "type": "password",
                "value": mdp_temporaire,
                "temporary": True,
            }],
        }
        r = requests.post(self._users_base, json=payload,
                          headers=self._headers(), timeout=10)
        if r.status_code == 409:
            raise ValueError(f"Login '{login}' déjà utilisé dans Keycloak.")
        if not r.ok:
            raise RuntimeError(
                f"Keycloak create user échoué ({r.status_code}) : {r.text[:300]}"
            )
        location = r.headers.get("Location", "")
        return location.rstrip("/").split("/")[-1]

    def _get_kc_id(self, login: str) -> Optional[str]:
        r = requests.get(self._users_base,
                          params={"username": login, "exact": "true"},
                          headers=self._headers(), timeout=10)
        r.raise_for_status()
        users = r.json()
        return users[0]["id"] if users else None

    def reset_mot_de_passe(self, login: str, nouveau_mdp: str,
                            temporaire: bool = True) -> None:
        kc_id = self._get_kc_id(login)
        if not kc_id:
            raise ValueError(f"Utilisateur '{login}' introuvable dans Keycloak.")
        r = requests.put(
            f"{self._users_base}/{kc_id}/reset-password",
            json={"type": "password", "value": nouveau_mdp,
                  "temporary": temporaire},
            headers=self._headers(), timeout=10,
        )
        r.raise_for_status()

    def supprimer_utilisateur(self, login: str) -> None:
        kc_id = self._get_kc_id(login)
        if kc_id:
            requests.delete(f"{self._users_base}/{kc_id}",
                            headers=self._headers(), timeout=10).raise_for_status()


class UserManagementService:
    """CRUD SEC_Utilisateur + matrice d'accès + colonnes masquées (MySQL)."""

    def __init__(self, db: pymysqlSecConnection,
                 kc: KeycloakAdminClient) -> None:
        self._db = db
        self._kc = kc

    # ── Lister les sites et régions ──────────────────────────────────────────

    def lister_sites(self) -> list[dict]:
        try:
            return self._db.executer_lecture(
                "SELECT Code_Site, Description FROM DIM_Site "
                "WHERE Status='Actif' ORDER BY Code_Site;"
            )
        except Exception:
            return []

    def lister_regions(self) -> list[dict]:
        try:
            return self._db.executer_lecture(
                "SELECT DISTINCT Code_Region AS Code, Ville AS Description "
                "FROM DIM_Region ORDER BY Ville;"
            )
        except Exception:
            return []

    # ── Utilisateurs ──────────────────────────────────────────────────────────

    def lister_utilisateurs(self) -> list[UserRow]:
        # MySQL : DATE_FORMAT au lieu de CONVERT(NVARCHAR...,120)
        lignes = self._db.executer_lecture("""
            SELECT u.UserId, u.Login, u.NomComplet, u.Email,
                   u.RoleId, r.NomRole,
                   u.CodeSite, u.CodeRegion,
                   u.Actif,
                   DATE_FORMAT(u.DateDerniereConnexion, '%%Y-%%m-%%d %%H:%%i:%%s')
                       AS DateDerniereConnexion
            FROM SEC_Utilisateur u
            JOIN SEC_Role r ON r.RoleId = u.RoleId
            ORDER BY u.Actif DESC, u.NomComplet;
        """)
        return [UserRow(
            user_id=int(l["UserId"]),
            login=l["Login"],
            nom_complet=l["NomComplet"],
            email=l["Email"],
            role_id=int(l["RoleId"]),
            nom_role=l["NomRole"],
            code_site=l["CodeSite"],
            code_region=l["CodeRegion"],
            actif=bool(l["Actif"]),
            derniere_connexion=l["DateDerniereConnexion"],
        ) for l in lignes]

    def creer_utilisateur(self, login: str, nom_complet: str,
                           email: str, role_id: int,
                           mdp_temporaire: str,
                           code_site: Optional[str] = None,
                           code_region: Optional[str] = None) -> int:
        """
        1. Crée dans Keycloak (si configuré)
        2. Insère dans SEC_Utilisateur avec MotDePasseHash
        """
        if self._kc:
            try:
                self._kc.creer_utilisateur_keycloak(login, email, nom_complet, mdp_temporaire)
            except Exception as exc:
                raise RuntimeError(f"Echec création Keycloak : {exc}") from exc

        mdp_hash = _hash_mdp(mdp_temporaire)
        # MySQL : INSERT puis LAST_INSERT_ID() (pas de OUTPUT INSERTED)
        self._db.executer_ecriture_simple("""
            INSERT INTO SEC_Utilisateur
                (Login, NomComplet, Email, RoleId,
                 CodeSite, CodeRegion, MotDePasseHash, Actif)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1);
        """, (login.strip(), nom_complet.strip(),
              email.strip() or None,
              role_id,
              code_site or None,
              code_region or None,
              mdp_hash))
        # Récupération de l'ID auto-généré
        rows = self._db.executer_lecture("SELECT LAST_INSERT_ID() AS UserId;")
        return int(rows[0]["UserId"])

    def modifier_utilisateur(self, user_id: int, nom_complet: str,
                              email: str, role_id: int,
                              code_site: Optional[str] = None,
                              code_region: Optional[str] = None) -> None:
        self._db.executer_ecriture_simple("""
            UPDATE SEC_Utilisateur
            SET NomComplet=%s, Email=%s, RoleId=%s,
                CodeSite=%s, CodeRegion=%s
            WHERE UserId=%s;
        """, (nom_complet, email or None, role_id,
              code_site or None, code_region or None,
              user_id))

    def reset_mot_de_passe(self, login: str, nouveau_mdp: str) -> None:
        if self._kc:
            self._kc.reset_mot_de_passe(login, nouveau_mdp, temporaire=True)
        mdp_hash = _hash_mdp(nouveau_mdp)
        self._db.executer_ecriture_simple(
            "UPDATE SEC_Utilisateur SET MotDePasseHash=%s WHERE Login=%s;",
            (mdp_hash, login),
        )

    def supprimer_utilisateur(self, user_id: int, login: str) -> None:
        """Suppression physique dans le bon ordre FK (MySQL)."""
        self._db.executer_ecriture_simple("""
            DELETE FROM AUDIT_Acces_Donnees
            WHERE InteractionId IN (
                SELECT InteractionId FROM AUDIT_Interaction WHERE UserId = %s
            );
        """, (user_id,))

        self._db.executer_ecriture_simple(
            "DELETE FROM AUDIT_Interaction WHERE UserId = %s;", (user_id,)
        )

        self._db.executer_ecriture_simple(
            "DELETE FROM AUDIT_Session WHERE UserId = %s;", (user_id,)
        )

        self._db.executer_ecriture_simple(
            "DELETE FROM SEC_Utilisateur WHERE UserId = %s;", (user_id,)
        )

        if self._kc:
            try:
                self._kc.supprimer_utilisateur(login)
            except Exception:
                pass

    # ── Rôles ─────────────────────────────────────────────────────────────────

    def lister_roles(self) -> list[dict]:
        return self._db.executer_lecture(
            "SELECT RoleId, NomRole, Description FROM SEC_Role ORDER BY RoleId;"
        )

    # ── Matrice d'accès ───────────────────────────────────────────────────────

    def lister_tables_connues(self) -> list[str]:
        lignes = self._db.executer_lecture("""
            SELECT DISTINCT NomTable FROM SEC_Role_Table_Acces
            WHERE NomTable <> '*'
            UNION
            SELECT DISTINCT NomTable FROM SEC_Role_Colonne_Masquee
            ORDER BY NomTable;
        """)
        return [l["NomTable"] for l in lignes]

    def matrice_acces(self) -> dict[str, set[int]]:
        lignes = self._db.executer_lecture("""
            SELECT NomTable, RoleId FROM SEC_Role_Table_Acces
            WHERE NomTable <> '*' AND NiveauAcces = 'Lecture';
        """)
        result: dict[str, set[int]] = {}
        for l in lignes:
            result.setdefault(l["NomTable"], set()).add(int(l["RoleId"]))
        return result

    def roles_avec_acces_total(self) -> set[int]:
        lignes = self._db.executer_lecture(
            "SELECT RoleId FROM SEC_Role_Table_Acces WHERE NomTable='*';"
        )
        return {int(l["RoleId"]) for l in lignes}

    def enregistrer_matrice(self, table: str, role_ids_actifs: set[int],
                             roles_acces_total: set[int]) -> None:
        lignes_actuelles = self._db.executer_lecture(
            "SELECT RoleId FROM SEC_Role_Table_Acces "
            "WHERE NomTable=%s AND NomTable<>'*';", (table,)
        )
        ids_actuels = {int(l["RoleId"]) for l in lignes_actuelles}
        a_ajouter = role_ids_actifs - ids_actuels - roles_acces_total
        a_supprimer = (ids_actuels - role_ids_actifs) - roles_acces_total
        for rid in a_ajouter:
            self._db.executer_ecriture_simple(
                "INSERT INTO SEC_Role_Table_Acces (RoleId, NomTable, NiveauAcces) "
                "VALUES (%s, %s, 'Lecture');", (rid, table)
            )
        for rid in a_supprimer:
            self._db.executer_ecriture_simple(
                "DELETE FROM SEC_Role_Table_Acces WHERE RoleId=%s AND NomTable=%s;",
                (rid, table)
            )

    def ajouter_table(self, nom_table: str, role_ids: set[int]) -> None:
        for rid in role_ids:
            self._db.executer_ecriture_simple(
                "INSERT INTO SEC_Role_Table_Acces (RoleId, NomTable, NiveauAcces) "
                "VALUES (%s, %s, 'Lecture');", (rid, nom_table)
            )

    # ── Colonnes masquées ─────────────────────────────────────────────────────

    def colonnes_masquees(self) -> list[dict]:
        return self._db.executer_lecture("""
            SELECT cm.RoleId, r.NomRole, cm.NomTable, cm.NomColonne
            FROM SEC_Role_Colonne_Masquee cm
            JOIN SEC_Role r ON r.RoleId = cm.RoleId
            ORDER BY cm.NomTable, r.NomRole, cm.NomColonne;
        """)

    def ajouter_colonne_masquee(self, role_id: int,
                                 nom_table: str, nom_colonne: str) -> None:
        self._db.executer_ecriture_simple(
            "INSERT INTO SEC_Role_Colonne_Masquee "
            "(RoleId, NomTable, NomColonne) VALUES (%s,%s,%s);",
            (role_id, nom_table, nom_colonne),
        )

    def supprimer_colonne_masquee(self, role_id: int,
                                   nom_table: str, nom_colonne: str) -> None:
        self._db.executer_ecriture_simple(
            "DELETE FROM SEC_Role_Colonne_Masquee "
            "WHERE RoleId=%s AND NomTable=%s AND NomColonne=%s;",
            (role_id, nom_table, nom_colonne),
        )