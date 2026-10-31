# -*- coding: utf-8 -*-
"""
pages/admin.py v3 — Adaptation MySQL
  Adaptations MSSQL → MySQL :
  1. SELECT TOP 100 → LIMIT 100 (placé en fin de requête)
  2. CONVERT(NVARCHAR(19), ..., 120) → DATE_FORMAT(..., '%%Y-%%m-%%d %%H:%%i:%%s')
  3. Alias [Durée ms] → `Durée ms` (backticks MySQL)
  4. LEFT() → LEFT() (compatible MySQL, inchangé)
"""
from __future__ import annotations

import streamlit as st
import pandas as pd

from security.user_management import UserManagementService, KeycloakAdminClient
from security.authorization import AuthorizationService


def _badge_role(nom_role: str) -> str:
    icons = {
        "Admin": "🟣", "Directeur_General": "🔵",
        "Commercial": "🔷", "Responsable_Magasin": "⚪",
        "Responsable_Production": "🟢", "PRT": "🩷", "RH": "🟡",
    }
    return f"{icons.get(nom_role, '⚫')} {nom_role}"


def _ok(msg: str) -> None:
    st.toast(msg, icon="✅")


def _err(msg: str) -> None:
    st.toast(msg, icon="❌")


# ── Onglet 1 : Utilisateurs ───────────────────────────────────────────────────

def _tab_utilisateurs(svc: UserManagementService) -> None:
    roles = svc.lister_roles()
    role_map = {r["NomRole"]: r["RoleId"] for r in roles}
    role_names = [r["NomRole"] for r in roles]

    sites = svc.lister_sites()
    site_opts = ["(aucun)"] + [f"{s['Code_Site']} — {s['Description']}" for s in sites]
    site_code_map = {"(aucun)": None,
                     **{f"{s['Code_Site']} — {s['Description']}": s['Code_Site']
                        for s in sites}}

    regions = svc.lister_regions()
    region_opts = ["(aucune)"] + [f"{r['Code']} — {r['Description']}" for r in regions]
    region_code_map = {"(aucune)": None,
                       **{f"{r['Code']} — {r['Description']}": r['Code']
                          for r in regions}}

    # ── Formulaire création ────────────────────────────────────────────────
    with st.expander("➕ Nouvel utilisateur", expanded=False):
        with st.form("form_creer_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            login      = c1.text_input("Login *", placeholder="prenom.nom")
            nom        = c2.text_input("Nom complet *", placeholder="Prénom Nom")
            email      = c1.text_input("Email", placeholder="nom@unimed.tn")
            role_sel   = c2.selectbox("Rôle *", role_names)
            site_sel   = c1.selectbox("Site", site_opts)
            region_sel = c2.selectbox("Région", region_opts)
            mdp        = c1.text_input("Mot de passe temporaire *", type="password")
            mdp2       = c2.text_input("Confirmer *", type="password")

            if st.form_submit_button("Créer l'utilisateur", type="primary"):
                errs = []
                if not login: errs.append("Login requis.")
                if not nom:   errs.append("Nom complet requis.")
                if not mdp:   errs.append("Mot de passe requis.")
                if mdp and mdp != mdp2: errs.append("Mots de passe différents.")
                if mdp and len(mdp) < 8: errs.append("Minimum 8 caractères.")
                if errs:
                    for e in errs: st.error(e)
                else:
                    try:
                        svc.creer_utilisateur(
                            login=login.strip(),
                            nom_complet=nom.strip(),
                            email=email.strip(),
                            role_id=role_map[role_sel],
                            mdp_temporaire=mdp,
                            code_site=site_code_map.get(site_sel),
                            code_region=region_code_map.get(region_sel),
                        )
                        _ok(f"Utilisateur '{login}' créé.")
                        st.rerun()
                    except Exception as exc:
                        _err(str(exc))

    # ── Liste ──────────────────────────────────────────────────────────────
    users = svc.lister_utilisateurs()
    filtre = st.radio("", ["Tous", "Actifs", "Inactifs"],
                      horizontal=True, label_visibility="collapsed")
    if filtre == "Actifs":   users = [u for u in users if u.actif]
    elif filtre == "Inactifs": users = [u for u in users if not u.actif]

    current_login = st.session_state["utilisateur"].login

    for u in users:
        statut = "🟢 Actif" if u.actif else "🔴 Inactif"
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2, 2.5, 2, 1.5])
            c1.markdown(f"**{u.login}**  \n{u.nom_complet or '—'}")
            c2.markdown(
                f"{_badge_role(u.nom_role)}  \n"
                f"{u.email or '—'}  \n"
                f"Site: `{u.code_site or '—'}` · Région: `{u.code_region or '—'}`"
            )
            c3.markdown(f"{statut}  \n{u.derniere_connexion or 'Jamais connecté'}")

            with c4:
                # Modifier
                with st.popover("✏️"):
                    with st.form(f"edit_{u.user_id}"):
                        new_nom    = st.text_input("Nom complet", value=u.nom_complet or "")
                        new_email  = st.text_input("Email", value=u.email or "")
                        new_role   = st.selectbox("Rôle", role_names,
                            index=next((i for i, r in enumerate(role_names)
                                        if r == u.nom_role), 0))
                        new_site   = st.selectbox("Site", site_opts,
                            index=next((i for i, s in enumerate(site_opts)
                                        if site_code_map.get(s) == u.code_site), 0))
                        new_region = st.selectbox("Région", region_opts,
                            index=next((i for i, r in enumerate(region_opts)
                                        if region_code_map.get(r) == u.code_region), 0))
                        if st.form_submit_button("Enregistrer", type="primary"):
                            try:
                                svc.modifier_utilisateur(
                                    u.user_id, new_nom, new_email,
                                    role_map[new_role],
                                    code_site=site_code_map.get(new_site),
                                    code_region=region_code_map.get(new_region),
                                )
                                _ok("Modifications enregistrées.")
                                st.rerun()
                            except Exception as exc:
                                _err(str(exc))

                # Reset mot de passe
                with st.popover("🔑"):
                    with st.form(f"mdp_{u.user_id}"):
                        p1 = st.text_input("Nouveau mot de passe", type="password")
                        p2 = st.text_input("Confirmer", type="password")
                        if st.form_submit_button("Appliquer", type="primary"):
                            if p1 != p2:
                                st.error("Mots de passe différents.")
                            elif len(p1) < 8:
                                st.error("Minimum 8 caractères.")
                            else:
                                try:
                                    svc.reset_mot_de_passe(u.login, p1)
                                    _ok(f"Mot de passe réinitialisé pour '{u.login}'.")
                                except Exception as exc:
                                    _err(str(exc))

                # Supprimer (sauf soi-même)
                if u.login != current_login:
                    key_conf = f"confirm_del_{u.user_id}"
                    if st.button("🗑️", key=f"del_{u.user_id}",
                                 help="Supprimer définitivement"):
                        if st.session_state.get(key_conf):
                            try:
                                svc.supprimer_utilisateur(u.user_id, u.login)
                                st.session_state.pop(key_conf, None)
                                _ok(f"'{u.login}' supprimé.")
                                st.rerun()
                            except Exception as exc:
                                _err(str(exc))
                        else:
                            st.session_state[key_conf] = True
                            st.warning(
                                f"Confirmer la suppression de **{u.login}** "
                                f"(toutes ses sessions d'audit seront effacées). "
                                f"Cliquez à nouveau sur 🗑️."
                            )


# ── Onglet 2 : Matrice d'accès ────────────────────────────────────────────────

def _tab_matrice(svc: UserManagementService,
                  authz: AuthorizationService) -> None:
    roles = svc.lister_roles()
    roles_acces_total = svc.roles_avec_acces_total()
    roles_editables = [r for r in roles if r["RoleId"] not in roles_acces_total]
    roles_total     = [r for r in roles if r["RoleId"] in roles_acces_total]
    tables  = sorted(svc.lister_tables_connues())
    matrice = svc.matrice_acces()

    with st.expander("➕ Ajouter une table dans la matrice"):
        with st.form("form_add_table"):
            new_table  = st.text_input("Nom exact de la table/vue", placeholder="FACT_Vente")
            roles_init = st.multiselect("Rôles autorisés (hors Admin/DG)",
                                        [r["NomRole"] for r in roles_editables])
            if st.form_submit_button("Ajouter", type="primary"):
                if new_table:
                    rid_set = {r["RoleId"] for r in roles_editables
                               if r["NomRole"] in roles_init}
                    svc.ajouter_table(new_table, rid_set)
                    authz.recharger()
                    _ok(f"Table '{new_table}' ajoutée.")
                    st.rerun()

    noms_total = ", ".join(r["NomRole"] for r in roles_total)
    st.info(f"**{noms_total}** ont accès total (★) — non modifiables ici.")

    changes: dict[str, set[int]] = {}
    header_cols = st.columns([2] + [1]*len(roles_editables) + [1]*len(roles_total))
    header_cols[0].markdown("**Table**")
    for i, r in enumerate(roles_editables):
        label = r["NomRole"].replace("Responsable_", "R_")
        header_cols[i+1].markdown(f"**{label}**", help=r["NomRole"])
    for j, r in enumerate(roles_total):
        header_cols[len(roles_editables)+j+1].markdown("**★**", help=r["NomRole"])

    for table in tables:
        rids_actuels = matrice.get(table, set())
        cols = st.columns([2] + [1]*len(roles_editables) + [1]*len(roles_total))
        cols[0].code(table)
        new_rids: set[int] = set()
        for i, role in enumerate(roles_editables):
            checked = cols[i+1].checkbox(
                " ", value=(role["RoleId"] in rids_actuels),
                key=f"mat_{table}_{role['RoleId']}",
                help=role["NomRole"],
            )
            if checked:
                new_rids.add(role["RoleId"])
        for j in range(len(roles_total)):
            cols[len(roles_editables)+j+1].markdown("★")
        changes[table] = new_rids

    if st.button("💾 Enregistrer la matrice", type="primary"):
        try:
            for table, rids in changes.items():
                svc.enregistrer_matrice(table, rids, roles_acces_total)
            authz.recharger()
            _ok("Matrice d'accès enregistrée.")
            st.rerun()
        except Exception as exc:
            _err(str(exc))


# ── Onglet 3 : Colonnes masquées ──────────────────────────────────────────────

def _tab_colonnes(svc: UserManagementService,
                   authz: AuthorizationService) -> None:
    roles  = svc.lister_roles()
    role_map = {r["NomRole"]: r["RoleId"] for r in roles}
    tables = svc.lister_tables_connues()

    with st.expander("➕ Masquer une colonne"):
        with st.form("form_mask_col"):
            c1, c2, c3 = st.columns(3)
            sel_role  = c1.selectbox("Rôle",   [r["NomRole"] for r in roles])
            sel_table = c2.selectbox("Table",  tables)
            sel_col   = c3.text_input("Colonne exacte", placeholder="Cout_Total")
            if st.form_submit_button("Appliquer masquage", type="primary"):
                if sel_col:
                    try:
                        svc.ajouter_colonne_masquee(
                            role_map[sel_role], sel_table, sel_col.strip()
                        )
                        authz.recharger()
                        _ok(f"Colonne '{sel_col}' masquée pour {sel_role}.")
                        st.rerun()
                    except Exception as exc:
                        _err(str(exc))

    lignes = svc.colonnes_masquees()
    if not lignes:
        st.info("Aucune colonne masquée configurée.")
        return

    for l in lignes:
        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
        c1.write(l["NomRole"])
        c2.write(l["NomTable"])
        c3.code(l["NomColonne"])
        if c4.button("🗑️",
                     key=f"dc_{l['RoleId']}_{l['NomTable']}_{l['NomColonne']}"):
            svc.supprimer_colonne_masquee(
                int(l["RoleId"]), l["NomTable"], l["NomColonne"]
            )
            authz.recharger()
            _ok("Règle supprimée.")
            st.rerun()


# ── Onglet 4 : Journal d'audit ────────────────────────────────────────────────

def _tab_audit(db_sec) -> None:
    filtre_statut = st.selectbox(
        "Filtre statut", ["Tous", "Succes", "Erreur", "Refuse"],
        label_visibility="collapsed"
    )
    where = ("" if filtre_statut == "Tous"
             else f"WHERE i.StatutExecution = '{filtre_statut}'")
    # MySQL : LIMIT au lieu de TOP, DATE_FORMAT au lieu de CONVERT, backticks pour alias
    lignes = db_sec.executer_lecture(f"""
        SELECT
            DATE_FORMAT(i.DateHeure, '%%Y-%%m-%%d %%H:%%i:%%s') AS Date,
            u.Login AS Utilisateur, r.NomRole AS Role,
            LEFT(i.Question, 80) AS Question,
            i.StatutExecution AS Statut,
            i.NbLignesRetournees AS Lignes,
            i.DureeTotaleMs AS `Durée ms`
        FROM AUDIT_Interaction i
        JOIN SEC_Utilisateur u ON u.UserId = i.UserId
        JOIN SEC_Role r ON r.RoleId = i.RoleId
        {where}
        ORDER BY i.DateHeure DESC
        LIMIT 100;
    """)
    if not lignes:
        st.info("Aucune interaction enregistrée.")
        return
    st.dataframe(pd.DataFrame(lignes), use_container_width=True, hide_index=True)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def render_admin_page(db_sec, authz: AuthorizationService,
                       svc: UserManagementService) -> None:
    st.title("Administration")
    col_back, _ = st.columns([1, 5])
    if col_back.button("← Retour au chat"):
        st.session_state["page"] = "chat"
        st.rerun()

    tab1, tab2, tab3, tab4 = st.tabs([
        "👥 Utilisateurs", "🔐 Accès tables",
        "🙈 Colonnes masquées", "📋 Journal d'audit",
    ])
    with tab1: _tab_utilisateurs(svc)
    with tab2: _tab_matrice(svc, authz)
    with tab3: _tab_colonnes(svc, authz)
    with tab4: _tab_audit(db_sec)