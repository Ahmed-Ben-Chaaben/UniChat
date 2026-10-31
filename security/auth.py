# -*- coding: utf-8 -*-
"""
Authentification Keycloak.

Flux :
    1. login(username, password)        → access_token JWT via Direct Access Grant
    2. verifier_token(access_token)     → valide signature JWKS + expiration
    3. resoudre_utilisateur(claims, db) → récupère rôle réel depuis SEC_Utilisateur
    4. authentifier(u, p, db)           → enchaîne les 3 étapes
"""
from __future__ import annotations

import requests
import jwt
from jwt import PyJWKClient

from .db_sec import pymysqlSecConnection
from .models import Role, Utilisateur


class KeycloakAuthenticator:
    def __init__(self, server_url: str, realm: str,
                 client_id: str, client_secret: str | None = None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        base = f"{server_url.rstrip('/')}/realms/{realm}/protocol/openid-connect"
        self._token_endpoint = f"{base}/token"
        self._issuer = f"{server_url.rstrip('/')}/realms/{realm}"
        self._jwk_client = PyJWKClient(f"{base}/certs")

    def login(self, username: str, password: str) -> str:
        """Retourne l'access_token JWT. Lève PermissionError si invalide."""
        data = {
            "grant_type": "password",
            "client_id": self._client_id,
            "username": username,
            "password": password,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret

        r = requests.post(self._token_endpoint, data=data, timeout=10)
        if r.status_code != 200:
            raise PermissionError("Identifiants invalides ou compte Keycloak inconnu.")
        return r.json()["access_token"]

    def verifier_token(self, access_token: str) -> dict:
        """Valide la signature JWKS, l'émetteur et l'expiration."""
        try:
            cle = self._jwk_client.get_signing_key_from_jwt(access_token)
            return jwt.decode(
                access_token,
                cle.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={"verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise PermissionError("Token expiré, reconnectez-vous.") from exc
        except jwt.PyJWTError as exc:
            raise PermissionError(f"Token invalide : {exc}") from exc

    def resoudre_utilisateur(self, claims: dict,
                              db: pymysqlSecConnection) -> Utilisateur:
        """Le rôle autoritaire vient de SEC_Utilisateur, pas des claims Keycloak."""
        login = claims.get("preferred_username")
        if not login:
            raise PermissionError("Token sans 'preferred_username'.")

        lignes = db.executer_lecture(
            """
            SELECT u.UserId, u.Login, u.NomComplet, u.Email,
                   u.CodeSite, u.CodeRegion, u.Actif,
                   r.RoleId, r.NomRole, r.Description
            FROM SEC_Utilisateur u
            JOIN SEC_Role r ON r.RoleId = u.RoleId
            WHERE u.Login = %s;
            """,
            (login,),
        )
        if not lignes:
            raise PermissionError(
                f"'{login}' est authentifié sur Keycloak mais absent de "
                f"SEC_Utilisateur — créez-le d'abord."
            )
        ligne = lignes[0]
        if not ligne["Actif"]:
            raise PermissionError(f"Compte '{login}' désactivé.")

        role = Role(
            role_id=ligne["RoleId"],
            nom_role=ligne["NomRole"],
            description=ligne["Description"],
        )
        return Utilisateur(
            user_id=ligne["UserId"],
            login=ligne["Login"],
            role=role,
            nom_complet=ligne["NomComplet"],
            email=ligne["Email"],
            code_site=ligne["CodeSite"],
            code_region=ligne["CodeRegion"],
            actif=bool(ligne["Actif"]),
        )

    def authentifier(self, username: str, password: str,
                     db: pymysqlSecConnection) -> Utilisateur:
        """login → verifier_token → resoudre_utilisateur en une seule étape."""
        token = self.login(username, password)
        claims = self.verifier_token(token)
        return self.resoudre_utilisateur(claims, db)
