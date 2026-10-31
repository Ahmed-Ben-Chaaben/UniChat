"""
Interface conversationnelle — Streamlit + Plotly (étape 6).

Lancement :  streamlit run app.py
Prérequis  :  Qdrant lancé, Ollama lancé + modèle pull, KB indexée.

Affiche : conversation multi-tours, SQL généré (transparence), tableau de
résultats, et une visualisation Plotly auto-choisie selon la forme des données.
"""
from __future__ import annotations
import streamlit as st
import plotly.express as px
import pandas as pd

from core.db import DatabaseConnector
from core.LLM_cloud import LLMClient
from core.mrdq import DialogueState
from core.pipeline import BIPipeline, Outcome
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb

st.set_page_config(page_title="BI Conversationnelle — Unimed", layout="wide")


@st.cache_resource
def get_pipeline():
    """Initialise une seule fois DB + KB + pipeline (coûteux)."""
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    store.warmup()  # charge embeddings (+ reranker si activé) une seule fois ici,
                    # pour que la 1re question ne paie pas ce coût.
    return BIPipeline(db, store, llm=LLMClient())


def auto_chart(df: pd.DataFrame):
    """Choisit une visualisation simple selon la forme du résultat."""
    if df.empty:
        return None
    num_cols = df.select_dtypes("number").columns.tolist()
    cat_cols = [c for c in df.columns if c not in num_cols]
    if len(df) == 1 and len(num_cols) == 1:
        return None  # une seule valeur : pas de graphe, on affiche le chiffre
    if cat_cols and num_cols:
        x, y = cat_cols[0], num_cols[0]
        if len(df) <= 30:
            return px.bar(df.head(30), x=x, y=y, title=f"{y} par {x}")
    if len(num_cols) >= 2:
        return px.scatter(df, x=num_cols[0], y=num_cols[1])
    return None


st.title("💬 Assistant BI conversationnel")
st.caption("Architecture SiriusBI two-step (SIR) — 100% local · base de test : Chinook")

if "state" not in st.session_state:
    st.session_state.state = DialogueState()
if "messages" not in st.session_state:
    st.session_state.messages = []

# Réaffiche l'historique
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("df") is not None:
            st.dataframe(m["df"], use_container_width=True)
            fig = auto_chart(m["df"])
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)

question = st.chat_input("Posez une question sur vos données…")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Analyse, génération SQL et exécution…"):
            pipeline = get_pipeline()
            res = pipeline.ask(question, st.session_state.state)

        if res.outcome == Outcome.SUCCESS:
            st.markdown(res.message)
            with st.expander("Voir le SQL généré et la SIR"):
                st.code(res.sql, language="sql")
                if res.sir:
                    st.json(res.sir.sir_json)
            st.dataframe(res.data, use_container_width=True)
            fig = auto_chart(res.data)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)
            st.session_state.messages.append(
                {"role": "assistant", "content": res.message, "df": res.data}
            )
        else:
            st.markdown(f"**{res.message}**")
            st.session_state.messages.append(
                {"role": "assistant", "content": res.message}
            )