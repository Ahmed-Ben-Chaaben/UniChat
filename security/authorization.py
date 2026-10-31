# # -*- coding: utf-8 -*-
# """
# Couche autorisation — deux lignes de défense indépendantes.

# 1. AuthorizationService.filtrer_schema()
#    → retire du schéma envoyé au LLM les tables/colonnes interdites pour le
#      rôle. Le LLM ne voit même pas ce qu'il n'a pas le droit d'utiliser.

# 2. SQLValidator.valider()
#    → après génération, vérifie qu'aucune table hors périmètre n'a été
#      référencée. Protection contre les hallucinations LLM.

# Les règles sont chargées depuis SEC_Role_Table_Acces et
# SEC_Role_Colonne_Masquee (déjà en base via RBAC_Audit_Schema.sql).
# """
# from __future__ import annotations

# import re
# from dataclasses import dataclass

# from .db_sec import PyodbcSecConnection
# from .models import Role

# ACCES_TOTAL = "*"   # Admin / Directeur_General → accès illimité


# @dataclass
# class DecisionAcces:
#     autorise: bool
#     tables_refusees: list[str]


# class AuthorizationService:
#     """Résout les tables autorisées et les colonnes masquées pour un rôle.

#     Le cache en mémoire est valide pour la durée de vie du service — appeler
#     recharger() après une modification des tables SEC_Role_*.
#     """

#     def __init__(self, db: PyodbcSecConnection) -> None:
#         self._db = db
#         self._tables_par_role: dict[int, set[str]] = {}
#         self._colonnes_masquees_par_role: dict[int, dict[str, set[str]]] = {}
#         self.recharger()

#     def recharger(self) -> None:
#         lignes = self._db.executer_lecture(
#             "SELECT RoleId, NomTable FROM dbo.SEC_Role_Table_Acces "
#             "WHERE NiveauAcces = 'Lecture';"
#         )
#         tables: dict[int, set[str]] = {}
#         for l in lignes:
#             tables.setdefault(l["RoleId"], set()).add(l["NomTable"])
#         self._tables_par_role = tables

#         lignes = self._db.executer_lecture(
#             "SELECT RoleId, NomTable, NomColonne "
#             "FROM dbo.SEC_Role_Colonne_Masquee;"
#         )
#         colonnes: dict[int, dict[str, set[str]]] = {}
#         for l in lignes:
#             par_table = colonnes.setdefault(l["RoleId"], {})
#             par_table.setdefault(l["NomTable"], set()).add(l["NomColonne"])
#         self._colonnes_masquees_par_role = colonnes

#     # ── Accesseurs ────────────────────────────────────────────────────────────

#     def tables_autorisees(self, role: Role) -> set[str]:
#         return self._tables_par_role.get(role.role_id, set())

#     def table_est_autorisee(self, role: Role, nom_table: str) -> bool:
#         autorisees = self.tables_autorisees(role)
#         return ACCES_TOTAL in autorisees or nom_table in autorisees

#     def colonnes_masquees(self, role: Role, nom_table: str) -> set[str]:
#         return (self._colonnes_masquees_par_role
#                 .get(role.role_id, {})
#                 .get(nom_table, set()))

#     # ── 1re ligne de défense : filtrage schéma avant envoi au LLM ────────────

#     def filtrer_schema(self, role: Role,
#                        schema: dict[str, list[str]]) -> dict[str, list[str]]:
#         """schema : {nom_table: [colonnes]} → version filtrée pour le rôle.

#         Retire les tables non autorisées, puis les colonnes masquées des
#         tables restantes. Le LLM reçoit uniquement ce résultat.
#         """
#         autorisees = self.tables_autorisees(role)
#         print(f"Tables autorisées pour rôle {role.nom_role} : {autorisees}")  # DEBUG
#         acces_total = ACCES_TOTAL in autorisees

#         schema_filtre: dict[str, list[str]] = {}
#         for nom_table, colonnes in schema.items():
#             if not (acces_total or nom_table in autorisees):
#                 continue
#             masquees = self.colonnes_masquees(role, nom_table)
#             print(f"Colonnes masquées pour rôle {role.nom_role}, table {nom_table} : {masquees}")  # DEBUG
#             schema_filtre[nom_table] = [c for c in colonnes if c not in masquees]
#             print(f"Table {nom_table} après filtrage : {schema_filtre[nom_table]}")  # DEBUG
#         return schema_filtre


# class SQLValidator:
#     """2e ligne de défense : parse le SQL généré et vérifie les tables
#     référencées, indépendamment du filtrage de schéma fait en amont."""

#     _RE_TABLE = re.compile(
#         r"\b(?:FROM|JOIN)\s+\[?(?:dbo\]?\.\[?)?([A-Za-z_][A-Za-z0-9_]*)\]?",
#         re.IGNORECASE,
#     )

#     def extraire_tables(self, sql: str) -> list[str]:
#         return sorted(set(self._RE_TABLE.findall(sql)))

#     def valider(self, sql: str,
#                 tables_autorisees: set[str]) -> DecisionAcces:
#         if ACCES_TOTAL in tables_autorisees:
#             return DecisionAcces(autorise=True, tables_refusees=[])
#         tables_demandees = self.extraire_tables(sql)
#         refusees = [t for t in tables_demandees if t not in tables_autorisees]
#         return DecisionAcces(autorise=(len(refusees) == 0),
#                              tables_refusees=refusees)


# -*- coding: utf-8 -*-
"""
Couche autorisation — deux lignes de défense indépendantes.

1. AuthorizationService.filtrer_schema(role, schema_dict)
   → reçoit {table: [colonnes]} directement (pas de DDL à parser)
   → retire les tables/colonnes interdites pour le rôle
   → le LLM ne voit que le périmètre autorisé

2. SQLValidator.valider(sql, tables_autorisees)
   → après génération SQL, vérifie qu'aucune table hors périmètre
     n'a été référencée (protection contre hallucinations LLM)

Les règles sont chargées depuis SEC_Role_Table_Acces et
SEC_Role_Colonne_Masquee (RBAC_Audit_Schema.sql déjà exécuté).

Admin / Directeur_General : NomTable = '*' → accès total,
filtrer_schema() retourne le schéma entier sans modification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .db_sec import pymysqlSecConnection
from .models import Role

ACCES_TOTAL = "*"


@dataclass
class DecisionAcces:
    autorise: bool
    tables_refusees: list[str]


class AuthorizationService:
    """Résout les tables autorisées et colonnes masquées pour un rôle.

    Appeler recharger() après toute modification de SEC_Role_Table_Acces
    ou SEC_Role_Colonne_Masquee pour invalider le cache mémoire.
    """

    def __init__(self, db: pymysqlSecConnection) -> None:
        self._db = db
        self._tables_par_role: dict[int, set[str]] = {}
        self._colonnes_masquees_par_role: dict[int, dict[str, set[str]]] = {}
        self.recharger()

    def recharger(self) -> None:
        # Toutes les entrées Lecture (inclut '*' pour Admin/DG)
        lignes = self._db.executer_lecture(
            "SELECT RoleId, NomTable FROM sec_role_table_acces "
            "WHERE NiveauAcces = 'Lecture';"
        )
        tables: dict[int, set[str]] = {}
        for l in lignes:
            tables.setdefault(int(l["RoleId"]), set()).add(l["NomTable"])
        self._tables_par_role = tables

        lignes = self._db.executer_lecture(
            "SELECT RoleId, NomTable, NomColonne "
            "FROM sec_role_colonne_masquee;"
        )
        colonnes: dict[int, dict[str, set[str]]] = {}
        for l in lignes:
            par_table = colonnes.setdefault(int(l["RoleId"]), {})
            par_table.setdefault(l["NomTable"], set()).add(l["NomColonne"])
        self._colonnes_masquees_par_role = colonnes

    # ── Accesseurs ────────────────────────────────────────────────────────────

    def tables_autorisees(self, role: Role) -> set[str]:
        return self._tables_par_role.get(int(role.role_id), set())

    def a_acces_total(self, role: Role) -> bool:
        return ACCES_TOTAL in self.tables_autorisees(role)

    def table_est_autorisee(self, role: Role, nom_table: str) -> bool:
        autorisees = self.tables_autorisees(role)
        return ACCES_TOTAL in autorisees or nom_table in autorisees

    def colonnes_masquees(self, role: Role, nom_table: str) -> set[str]:
        return (self._colonnes_masquees_par_role
                .get(int(role.role_id), {})
                .get(nom_table, set()))

    # ── 1re ligne de défense ──────────────────────────────────────────────────

    def filtrer_schema(self, role: Role,
                       schema: dict[str, list[str]]) -> dict[str, list[str]]:
        """Filtre le schéma {table: [colonnes]} selon le rôle.

        Admin / Directeur_General (NomTable='*') → retourne le schéma complet
        sans modification, sauf les colonnes explicitement masquées.

        Autres rôles → ne conserve que les tables listées dans
        SEC_Role_Table_Acces, en retirant les colonnes masquées.
        """
        autorisees = self.tables_autorisees(role)

        if not autorisees:
            # Aucune règle en base pour ce rôle → accès refusé
            return {}

        acces_total = ACCES_TOTAL in autorisees

        schema_filtre: dict[str, list[str]] = {}
        for nom_table, colonnes in schema.items():
            if not acces_total and nom_table not in autorisees:
                continue  # table hors périmètre
            masquees = self.colonnes_masquees(role, nom_table)
            cols_visibles = [c for c in colonnes if c not in masquees]
            schema_filtre[nom_table] = cols_visibles

        return schema_filtre


class SQLValidator:
    """2e ligne de défense : vérifie le SQL généré indépendamment du filtrage."""

    _RE_TABLE = re.compile(
        r"\b(?:FROM|JOIN)\s+\[?(?:dbo\]?\.\[?)?([A-Za-z_][A-Za-z0-9_]*)\]?",
        re.IGNORECASE,
    )

    def extraire_tables(self, sql: str) -> list[str]:
        return sorted(set(self._RE_TABLE.findall(sql)))

    def valider(self, sql: str,
                tables_autorisees: set[str]) -> DecisionAcces:
        if ACCES_TOTAL in tables_autorisees:
            return DecisionAcces(autorise=True, tables_refusees=[])
        tables_demandees = self.extraire_tables(sql)
        refusees = [t for t in tables_demandees if t not in tables_autorisees]
        return DecisionAcces(autorise=(len(refusees) == 0),
                             tables_refusees=refusees)
