"""
Interface Streamlit de l'assistant BI conversationnel.

Flux de sécurité :
    1. Page login  → KeycloakAuthenticator.authentifier()
    2. Ouverture de session AUDIT via AuditLogger.ouvrir_session()
    3. Chaque question → pipeline.ask(..., session=session)
       → schéma filtré par rôle (1re défense)
       → SQL validé (2e défense)
       → résultat loggué dans AUDIT_Interaction
    4. Fermeture de session à la déconnexion / fermeture de l'onglet
"""
from __future__ import annotations
import os

from dotenv import load_dotenv
import streamlit as st
import plotly.express as px
import pandas as pd

from config.config import DBSecConfig, KeycloakConfig, KeycloakAdminClientdata
from core.db import DatabaseConnector
from core.pipeline_new import BIPipeline, Outcome
from core.mrdq import DialogueState
from core.viz_engine import render_chart
from knowledge.builder import build_full_kb
from knowledge.store import KnowledgeStore
from security import (
    KeycloakAuthenticator,
    AuthorizationService,
    SQLValidator,
    pymysqlSecConnection,
)
from security.audit_mysql import  AuditLogger

from security.user_management import UserManagementService, KeycloakAdminClient
from pages.admin import render_admin_page
from config import settings





@st.cache_resource
def _init_services():
    load_dotenv() 
    db = DatabaseConnector()
    store = KnowledgeStore()
    kb = build_full_kb(db)
    store.index(kb)


    # sec_cs = DBSecConfig.depuis_env().connection_string

    sec_cs = os.getenv("MYSQL_SEC_CS")
    if sec_cs is None:
        raise ValueError("MYSQL_SEC_CS non définie dans l'environnement")
    db_sec = DBSecConfig.depuis_env().creer_connexion()
    print(f"Connexion aux tables SEC_*/AUDIT_* : {sec_cs}")
    authz = AuthorizationService(db_sec)
    validator = SQLValidator()
    audit = AuditLogger(db_sec)
    


    kc_cfg = KeycloakConfig.depuis_env()
    authenticator = KeycloakAuthenticator(
        server_url=kc_cfg.server_url,
        realm=kc_cfg.realm,
        client_id=kc_cfg.client_id,
        client_secret=kc_cfg.client_secret,
    )
    kc_ad = KeycloakAdminClientdata.depuis_env()
    kc_admin = KeycloakAdminClient(
    server_url=kc_ad.server_url,
    realm=kc_ad.realm,
    admin_login=kc_ad.admin_login,
    admin_password=kc_ad.admin_password,
)
    svc = UserManagementService(db_sec, kc_admin)
    
    pipeline = BIPipeline(
        db=db, store=store,
        authz=authz, validator=validator, audit=audit,
    )
    return pipeline, authenticator, db_sec, audit, authz, svc   # ← ajouter svc



# ── Page login ────────────────────────────────────────────────────────────────

def _page_login(authenticator, db_sec, audit) -> None:
    st.title("Assistant BI — Connexion")
    with st.form("login_form"):
        username = st.text_input("Identifiant")
        password = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se connecter", type="primary")
    if submitted:
        if not username or not password:
            st.error("Identifiant et mot de passe requis.")
            return
        try:
            utilisateur = authenticator.authentifier(username, password, db_sec)
            session = audit.ouvrir_session(
                utilisateur, utilisateur.role, canal="Streamlit",
            )
            st.session_state["utilisateur"] = utilisateur
            st.session_state["session"] = session
            st.session_state["dialogue_state"] = DialogueState()
            st.session_state["messages"] = []
            st.session_state["page"] = "chat"
            st.rerun()
        except PermissionError as exc:
            st.error(str(exc))


# ── Page chat ─────────────────────────────────────────────────────────────────

def _page_chat(pipeline, audit, authz) -> None:
    utilisateur = st.session_state["utilisateur"]
    session = st.session_state["session"]
    state: DialogueState = st.session_state["dialogue_state"]
    est_admin = utilisateur.role.nom_role == "Admin"

    # En-tête
    col_title, col_admin, col_logout = st.columns([5, 1, 1])
    with col_title:
        st.title("Assistant BI — Unimed")
        st.caption(
            f"Connecté : **{utilisateur.nom_complet or utilisateur.login}** "
            f"· Rôle : **{utilisateur.role.nom_role}**"
        )
    with col_admin:
        # Bouton visible uniquement pour l'Admin
        if est_admin:
            if st.button("⚙️ Admin", use_container_width=True,
                         help="Gestion des utilisateurs et des accès"):
                st.session_state["page"] = "admin"
                st.rerun()
    with col_logout:
        if st.button("Déconnexion", use_container_width=True):
            audit.fermer_session(session)
            for k in ["utilisateur", "session", "dialogue_state",
                       "messages", "page"]:
                st.session_state.pop(k, None)
            st.rerun()

    st.divider()

    # Historique
    for msg in st.session_state.get("messages", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sql"):
                with st.expander("SQL généré", expanded=False):
                    st.code(msg["sql"], language="sql")
            if msg.get("data") is not None:
                None

    # Saisie
    question = st.chat_input("Posez votre question BI...")
    if not question:
        return

    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Analyse en cours..."):
            result = pipeline.ask(question=question, state=state, session=session)

        if result.outcome == Outcome.DECLINED:
            content = f"⚠️ {result.message}"
            st.warning(content)
        elif result.outcome == Outcome.NEEDS_CLARIFICATION:
            content = f"❓ {result.message}"
            st.info(content)
        elif result.outcome == Outcome.SUCCESS:
            lines = [f"✅ {result.message}"]
            if result.context_inherited:
                lines.append("_Contexte : " + " · ".join(result.context_inherited) + "_")
            content = "\n\n".join(lines)
            st.markdown(content)

            if result.sql:
                with st.expander("SQL généré", expanded=False):
                    st.code(result.sql, language="sql")

            if result.data is not None and not result.data.empty:
                st.dataframe(result.data, use_container_width=True)

                # ── Visualisation automatique ──────────────────────────────
                sir_json = result.sir.sir_json if result.sir else {}
                query_und = (sir_json.get("Query Understanding", "")
                                if isinstance(sir_json, dict) else "")
                with st.expander("📊 Visualisation", expanded=True):
                    render_chart(result.data, sir_json, query_und)
        else:
            content = f"{'🔒' if result.acces_refuse else '❌'} {result.message}"
            st.error(content)
            if result.sql and not result.acces_refuse:
                with st.expander("SQL (erreur)", expanded=False):
                    st.code(result.sql, language="sql")

        st.session_state["messages"].append({
            "role": "assistant", "content": content,
            "sql": result.sql if result.outcome == Outcome.SUCCESS else None,
            "data": result.data if result.outcome == Outcome.SUCCESS else None,
        })




# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(page_title="Assistant BI — Unimed",
                       page_icon="🏭", layout="wide")

    pipeline, authenticator, db_sec, audit, authz, svc = _init_services()   # ← recevoir svc

    if "utilisateur" not in st.session_state:
        _page_login(authenticator, db_sec, audit)
        return

    page = st.session_state.get("page", "chat")
    role = st.session_state["utilisateur"].role.nom_role

    if page == "admin" and role == "Admin":
        render_admin_page(db_sec, authz, svc)   # ← passer svc
    else:
        _page_chat(pipeline, audit, authz)

if __name__ == "__main__":
    main()