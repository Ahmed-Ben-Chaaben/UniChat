# -*- coding: utf-8 -*-
"""
core/viz_engine.py — Moteur de visualisation automatique.

Choisit et génère le visuel le plus adapté en fonction de :
  - la SIR (Key Components : Has_Ratio, Has_Rank, Time Range, Dimensions)
  - la structure du DataFrame résultat (nb colonnes, types, cardinalité)

Catalogue des 9 visuels standards BI implémentés :
  1. KPI Card        — 1 valeur scalaire (SUM/COUNT sans GROUP BY)
  2. Bar Chart       — comparaison catégorielle (≤ 20 catégories)
  3. Horizontal Bar  — comparaison catégorielle (> 20 catégories ou labels longs)
  4. Line Chart      — série temporelle (Time Range présent ou colonne date/mois)
  5. Area Chart      — série temporelle cumulée
  6. Pie / Donut     — répartition (Has_Ratio=true, ≤ 8 catégories)
  7. Treemap         — répartition hiérarchique (Has_Ratio=true, > 8 catégories)
  8. Ranked Table    — classement (Has_Rank=true)
  9. Waterfall       — évolution positive/négative (écarts, variances)

Bibliothèques : Plotly Express + Plotly Graph Objects (déjà dans le projet).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ── Palette Unimed ────────────────────────────────────────────────────────────
_PALETTE = px.colors.qualitative.Set2
_COLOR_PRIMARY = "#1D9E75"
_COLOR_ACCENT = "#185FA5"
_COLOR_WARNING = "#BA7517"
_LAYOUT = dict(
    font_family="Inter, sans-serif",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=40, r=20, t=50, b=40),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)


# ── Détection de la structure du DataFrame ────────────────────────────────────

@dataclass
class DFProfile:
    n_rows: int
    n_cols: int
    num_cols: list[str]      # colonnes numériques
    cat_cols: list[str]      # colonnes catégorielles
    date_cols: list[str]     # colonnes temporelles (date/mois/année)
    is_scalar: bool          # 1 ligne × 1 colonne numérique
    has_rank_col: bool       # colonne "Rang" détectée
    has_pct_col: bool        # colonne avec % ou Pct


def _profile(df: pd.DataFrame) -> DFProfile:
    num = df.select_dtypes("number").columns.tolist()
    cat = [c for c in df.columns if c not in num]

    # Colonnes temporelles : noms contenant date/mois/année/trimestre/semestre
    date_pat = re.compile(r"mois|month|date|annee|year|trim|semestre|periode|jour",
                          re.I)
    date_cols = [c for c in df.columns if date_pat.search(c)]

    # Rang
    rank_pat = re.compile(r"rang|rank|classement|position", re.I)
    has_rank = any(rank_pat.search(c) for c in df.columns)

    # Pourcentage
    pct_pat = re.compile(r"pct|pourcent|ratio|taux|part|%", re.I)
    has_pct = any(pct_pat.search(c) for c in df.columns)

    return DFProfile(
        n_rows=len(df),
        n_cols=len(df.columns),
        num_cols=num,
        cat_cols=cat,
        date_cols=date_cols,
        is_scalar=(len(df) == 1 and len(num) >= 1 and len(cat) == 0),
        has_rank_col=has_rank,
        has_pct_col=has_pct,
    )


# ── Routeur principal ─────────────────────────────────────────────────────────

def _choose_chart(sir: dict, prof: DFProfile) -> str:
    """Retourne le type de graphique à générer."""
    kc = sir.get("Key Components", {}) if sir else {}
    has_ratio = str(kc.get("Has_Ratio", "false")).lower() == "true"
    has_rank = str(kc.get("Has_Rank", "false")).lower() == "true"
    time_range = kc.get("Time Range")
    dims = kc.get("Dimensions", []) or []

    # 1. Scalaire → KPI Card
    if prof.is_scalar:
        return "kpi"



    # 3. Série temporelle → Line ou Area
    if prof.date_cols or time_range:
        if prof.n_rows > 0 and prof.num_cols:
            # Area si une seule série et valeurs toutes positives
            if len(prof.cat_cols) <= 1:
                vals = prof.num_cols[0]
                try:
                    if pd.to_numeric(prof.cat_cols[0] if prof.cat_cols else
                                     prof.num_cols[0]).min() >= 0:
                        pass
                except Exception:
                    pass
                return "area"
            return "line"

    # 4. Répartition (ratio/pourcentage)
    if has_ratio or prof.has_pct_col:
        if prof.n_rows <= 8:
            return "donut"
        return "treemap"

    # 5. Catégoriel
    if prof.cat_cols and prof.num_cols:
        # Labels longs ou beaucoup de catégories → horizontal
        max_label_len = df_global_ref[prof.cat_cols[0]].astype(str).str.len().max() \
            if prof.cat_cols else 0
        if prof.n_rows > 20 or max_label_len > 15:
            return "hbar"
        return "bar"

    return "table"


# ── Générateurs de visuels ────────────────────────────────────────────────────

def _kpi_card(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    """Affiche 1 à 4 métriques scalaires en cartes KPI."""
    cols = st.columns(len(prof.num_cols))
    for i, col_name in enumerate(prof.num_cols):
        val = df[col_name].iloc[0]
        if isinstance(val, float):
            formatted = f"{val:,.2f}"
        else:
            formatted = f"{val:,}"
        cols[i].metric(label=col_name.replace("_", " ").title(),
                       value=formatted)


def _bar_chart(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    x = prof.cat_cols[0] if prof.cat_cols else df.columns[0]
    y = prof.num_cols[0] if prof.num_cols else df.columns[-1]
    color = prof.cat_cols[1] if len(prof.cat_cols) > 1 else None
    fig = px.bar(df, x=x, y=y, color=color,
                 title=title, color_discrete_sequence=_PALETTE,
                 text_auto=".2s")
    fig.update_layout(**_LAYOUT)
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


def _hbar_chart(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    x = prof.num_cols[0] if prof.num_cols else df.columns[-1]
    y = prof.cat_cols[0] if prof.cat_cols else df.columns[0]
    fig = px.bar(df.sort_values(x, ascending=True), x=x, y=y,
                 orientation="h", title=title,
                 color_discrete_sequence=[_COLOR_PRIMARY],
                 text_auto=".2s")
    fig.update_layout(**_LAYOUT, height=max(350, prof.n_rows * 28))
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)


def _line_chart(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    x = prof.date_cols[0] if prof.date_cols else \
        (prof.cat_cols[0] if prof.cat_cols else df.columns[0])
    y_cols = prof.num_cols if prof.num_cols else [df.columns[-1]]
    fig = px.line(df, x=x, y=y_cols, title=title,
                  markers=True, color_discrete_sequence=_PALETTE)
    fig.update_layout(**_LAYOUT)
    st.plotly_chart(fig, use_container_width=True)


def _area_chart(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    x = prof.date_cols[0] if prof.date_cols else \
        (prof.cat_cols[0] if prof.cat_cols else df.columns[0])
    y = prof.num_cols[0] if prof.num_cols else df.columns[-1]
    fig = px.area(df, x=x, y=y, title=title,
                  color_discrete_sequence=[_COLOR_PRIMARY])
    fig.update_layout(**_LAYOUT)
    st.plotly_chart(fig, use_container_width=True)


def _donut_chart(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    labels = prof.cat_cols[0] if prof.cat_cols else df.columns[0]
    values = prof.num_cols[0] if prof.num_cols else df.columns[-1]
    fig = px.pie(df, names=labels, values=values, title=title,
                 hole=0.45, color_discrete_sequence=_PALETTE)
    fig.update_traces(textinfo="label+percent", textposition="outside")
    fig.update_layout(**_LAYOUT)
    st.plotly_chart(fig, use_container_width=True)


def _treemap(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    labels = prof.cat_cols[0] if prof.cat_cols else df.columns[0]
    values = prof.num_cols[0] if prof.num_cols else df.columns[-1]
    fig = px.treemap(df, path=[labels], values=values, title=title,
                     color_discrete_sequence=_PALETTE)
    fig.update_layout(**_LAYOUT)
    st.plotly_chart(fig, use_container_width=True)





def _waterfall(df: pd.DataFrame, prof: DFProfile, title: str) -> None:
    """Waterfall pour les colonnes d'écart/variance."""
    x = prof.cat_cols[0] if prof.cat_cols else df.columns[0]
    y = prof.num_cols[0] if prof.num_cols else df.columns[-1]
    vals = df[y].tolist()
    measure = ["relative"] * len(vals)
    if len(vals) > 1:
        measure[-1] = "total"
    fig = go.Figure(go.Waterfall(
        name=y, measure=measure,
        x=df[x].tolist(), y=vals,
        connector={"line": {"color": "rgb(63, 63, 63)"}},
        increasing={"marker": {"color": _COLOR_PRIMARY}},
        decreasing={"marker": {"color": "#E05C5C"}},
        totals={"marker": {"color": _COLOR_ACCENT}},
    ))
    fig.update_layout(title=title, **_LAYOUT)
    st.plotly_chart(fig, use_container_width=True)


def _fallback_table(df: pd.DataFrame, title: str) -> None:
    st.markdown(f"**{title}**")
    st.dataframe(df, use_container_width=True, hide_index=True)


# ── Variable globale temporaire pour le routeur ───────────────────────────────
# (nécessaire pour accéder au df dans _choose_chart sans le passer en paramètre)
df_global_ref: pd.DataFrame = pd.DataFrame()


# ── Point d'entrée public ─────────────────────────────────────────────────────

def render_chart(df: pd.DataFrame, sir: dict | None,
                 query_understanding: str = "") -> None:
    """Génère le visuel adapté et l'affiche dans Streamlit.

    Args:
        df                : DataFrame résultat de la requête SQL
        sir               : dict sir_json complet (contient Key Components)
        query_understanding: phrase de compréhension (titre du graphique)
    """
    global df_global_ref
    df_global_ref = df

    if df is None or df.empty:
        return

    prof = _profile(df)
    title = query_understanding or "Résultat"

    # Déterminer le type de graphique
    chart_type = _choose_chart(sir or {}, prof)

    # Détecter si colonne d'écart/variance → waterfall
    variance_pat = re.compile(r"ecart|variance|diff|delta|variation", re.I)
    if any(variance_pat.search(c) for c in df.columns) and prof.num_cols:
        chart_type = "waterfall"

    # Rendu
    dispatch = {
        "kpi":          lambda: _kpi_card(df, prof, title),
        "bar":          lambda: _bar_chart(df, prof, title),
        "hbar":         lambda: _hbar_chart(df, prof, title),
        "line":         lambda: _line_chart(df, prof, title),
        "area":         lambda: _area_chart(df, prof, title),
        "donut":        lambda: _donut_chart(df, prof, title),
        "treemap":      lambda: _treemap(df, prof, title),
        "waterfall":    lambda: _waterfall(df, prof, title),
        "table":        lambda: _fallback_table(df, title),
    }
    try:
        dispatch.get(chart_type, lambda: _fallback_table(df, title))()
    except Exception:
        # Visuel en échec → fallback tableau silencieux
        _fallback_table(df, title)
