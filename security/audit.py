# -*- coding: utf-8 -*-
"""
Journalisation — appelé systématiquement après chaque question,
succès, erreur ou refus. Toutes les requêtes sont paramétrées.
"""
from __future__ import annotations

import json

from .db_sec import pymysqlSecConnection
from .models import Interaction, Role, Session, Utilisateur


class AuditLogger:
    def __init__(self, db: pymysqlSecConnection) -> None:
        self._db = db

    def ouvrir_session(
        self,
        utilisateur: Utilisateur,
        role_actif: Role,
        canal: str | None = None,
        adresse_ip: str | None = None,
    ) -> Session:
        session = Session(
            utilisateur=utilisateur,
            role_actif=role_actif,
            canal=canal,
            adresse_ip=adresse_ip,
        )
        self._db.executer_ecriture(
            "INSERT INTO AUDIT_Session "
            "(SessionId, UserId, RoleId, DateDebut, Canal, AdresseIP) "
            "VALUES (%s, %s, %s, %s, %s, %s);",
            (str(session.session_id), utilisateur.user_id, role_actif.role_id,
             session.date_debut, canal, adresse_ip),
        )
        return session

    def fermer_session(self, session: Session) -> None:
        session.fermer()
        self._db.executer_ecriture(
            "UPDATE AUDIT_Session SET DateFin = %s WHERE SessionId = %s;",
            (session.date_fin, str(session.session_id)),
        )

    def logger_interaction(self, interaction: Interaction) -> int:
        sql = """
            INSERT INTO AUDIT_Interaction (
                SessionId, UserId, RoleId, DateHeure, Question, SqlGenere,
                TablesDemandees, StatutExecution, MessageErreur,
                ReponseAssistant, NbLignesRetournees,
                DureeExecutionMs, DureeTotaleMs, ModeleLLM
            )
            OUTPUT INSERTED.InteractionId   -- <-- ici
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """
        params = (
            str(interaction.session.session_id),
            interaction.session.utilisateur.user_id,
            interaction.session.role_actif.role_id,
            interaction.date_heure,
            interaction.question,
            interaction.sql_genere,
            json.dumps(interaction.tables_demandees, ensure_ascii=False),
            interaction.statut_execution,
            interaction.message_erreur,
            interaction.reponse_assistant,
            interaction.nb_lignes_retournees,
            interaction.duree_execution_ms,
            interaction.duree_totale_ms,
            interaction.modele_llm,
        )
        # ICI : remplacer executer_ecriture par executer_insert_avec_output
        interaction_id = self._db.executer_ecriture(sql, params)
        interaction.interaction_id = interaction_id
        return interaction_id

    def logger_acces_donnees(self, interaction_id: int,
                              tables: list[str], statut: str) -> None:
        """statut : 'Autorise' ou 'Refuse'."""
        for table in tables:
            self._db.executer_ecriture(
                "INSERT INTO AUDIT_Acces_Donnees "
                "(InteractionId, NomTable, StatutAcces) VALUES (%s, %s, %s);",
                (interaction_id, table, statut),
            )

    def enregistrer_feedback(self, interaction_id: int,
                              feedback: int,
                              commentaire: str | None = None) -> None:
        self._db.executer_ecriture(
            "UPDATE AUDIT_Interaction "
            "SET Feedback = %s, CommentaireFeedback = %s "
            "WHERE InteractionId = %s;",
            (feedback, commentaire, interaction_id),
        )
