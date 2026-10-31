# -*- coding: utf-8 -*-
"""
Modèles de données — reflètent fidèlement les tables SEC_* et AUDIT_*.
Aucune logique métier : simples porteurs de données.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4


@dataclass
class Role:
    """Reflète SEC_Role."""
    role_id: int
    nom_role: str
    description: Optional[str] = None


@dataclass
class Utilisateur:
    """Reflète SEC_Utilisateur. Un seul rôle par utilisateur."""
    user_id: int
    login: str
    role: Role
    nom_complet: Optional[str] = None
    email: Optional[str] = None
    code_site: Optional[str] = None
    code_region: Optional[str] = None
    actif: bool = True


@dataclass
class Session:
    """Reflète AUDIT_Session. Le rôle est figé à l'ouverture."""
    utilisateur: Utilisateur
    role_actif: Role
    session_id: UUID = field(default_factory=uuid4)
    date_debut: datetime = field(default_factory=datetime.utcnow)
    date_fin: Optional[datetime] = None
    canal: Optional[str] = None
    adresse_ip: Optional[str] = None

    def fermer(self) -> None:
        self.date_fin = datetime.utcnow()


@dataclass
class Interaction:
    """Reflète AUDIT_Interaction."""
    session: Session
    question: str
    interaction_id: Optional[int] = None
    sql_genere: Optional[str] = None
    tables_demandees: list[str] = field(default_factory=list)
    statut_execution: str = "Refuse"       # 'Succes' | 'Erreur' | 'Refuse'
    message_erreur: Optional[str] = None
    reponse_assistant: Optional[str] = None
    nb_lignes_retournees: Optional[int] = None
    duree_execution_ms: Optional[int] = None
    duree_totale_ms: Optional[int] = None
    modele_llm: Optional[str] = None
    feedback: Optional[int] = None
    commentaire_feedback: Optional[str] = None
    date_heure: datetime = field(default_factory=datetime.utcnow)
