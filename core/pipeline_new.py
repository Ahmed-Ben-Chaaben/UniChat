# """
# Orchestrateur du pipeline complet.

# Modifications v2 (sécurité) :
#   - ask() accepte un paramètre optionnel `session: Session`
#   - Si session présente :
#       1. schéma filtré par rôle avant envoi au LLM (1re ligne de défense)
#       2. SQL validé contre les tables autorisées (2e ligne de défense)
#       3. interaction loguée dans AUDIT_Interaction (succès, erreur, refus)
#   - Si session absente (mode dev/test) : comportement inchangé.
# """
# from __future__ import annotations

# import time
# from dataclasses import dataclass, field
# from enum import Enum
# from typing import Optional

# import pandas as pd

# from core.db import DatabaseConnector, UnsafeQueryError
# from core.LLM_cloud import LLMClient
# from core.mrdq import MRDQ, DialogueState, AnalysisResult
# from core.sir import SQLGenerator, SIRResult
# from config import settings
# from knowledge.store import KnowledgeStore
# from knowledge.builder import KBEntry

# # Imports sécurité — optionnels pour ne pas casser le mode dev sans Keycloak
# try:
#     from security.models import Session, Interaction
#     from security.authorization import AuthorizationService, SQLValidator
#     from security.audit import AuditLogger
#     _SECURITY_AVAILABLE = True
# except ImportError:
#     _SECURITY_AVAILABLE = False
#     Session = None  # type: ignore


# class Outcome(str, Enum):
#     DECLINED = "declined"
#     NEEDS_CLARIFICATION = "clarify"
#     SUCCESS = "success"
#     ERROR = "error"


# @dataclass
# class PipelineResult:
#     outcome: Outcome
#     message: str = ""
#     analysis: Optional[AnalysisResult] = None
#     retrieved: list[KBEntry] = field(default_factory=list)
#     sir: Optional[SIRResult] = None
#     sql: str = ""
#     data: Optional[pd.DataFrame] = None
#     context_inherited: list[str] = field(default_factory=list)
#     # Champs sécurité (remplis si session présente)
#     tables_demandees: list[str] = field(default_factory=list)
#     acces_refuse: bool = False


# class BIPipeline:
#     def __init__(
#         self,
#         db: DatabaseConnector,
#         store: KnowledgeStore,
#         llm: LLMClient | None = None,
#         # Services sécurité — None en mode dev
#         authz: Optional["AuthorizationService"] = None,
#         validator: Optional["SQLValidator"] = None,
#         audit: Optional["AuditLogger"] = None,
#     ):
#         self.db = db
#         self.store = store
#         self.llm = llm or LLMClient()
#         self.mrdq = MRDQ(self.llm)
#         self.sqlgen = SQLGenerator(self.llm)
#         self._authz = authz
#         self._validator = validator
#         self._audit = audit

#     def _domain_context(self, retrieved: list[KBEntry]) -> str:
#         terms = [e for e in retrieved if e.kb_type == "domain_knowledge"]
#         return "\n".join(f"- {e.name}: {e.description}" for e in terms)

#     def _build_effective_question(self, question: str,
#                                    state: DialogueState) -> str:
#         if state.pending_question:
#             return (
#                 f"{state.pending_question} "
#                 f"(précision apportée par l'utilisateur : {question})"
#             )
#         return question

#     def _context_inherited_labels(self, state: DialogueState) -> list[str]:
#         ctx = state.active_context
#         labels = []
#         if ctx.dimensions:
#             labels.append(f"Dimensions : {', '.join(ctx.dimensions)}")
#         if ctx.filters:
#             labels.append(f"Filtres : {', '.join(ctx.filters)}")
#         if ctx.metric:
#             labels.append(f"Métrique : {ctx.metric}")
#         if ctx.all_periods:
#             labels.append("Périodes complètes (mois vides inclus)")
#         return labels

#     # ── Schéma filtré par rôle (1re ligne de défense) ────────────────────────

#     def _get_schema(self, session: Optional["Session"]) -> str:
#         full_schema = self.db.schema_ddl()
#         print(f"Schéma complet : {full_schema}")  # DEBUG
#         if session is None or self._authz is None:
#             return full_schema

#         # Construire {table: [colonnes]} depuis le DDL pour le filtrage
#         schema_dict = self._schema_ddl_to_dict(full_schema)
#         schema_filtre = self._authz.filtrer_schema(session.role_actif, schema_dict)
#         print(f"Schema filtré pour rôle {session.role_actif} : {schema_filtre}")  # DEBUG

#         if not schema_filtre:
#             raise PermissionError(
#                 "Aucune table autorisée pour votre rôle : impossible de "
#                 "générer une requête."
#             )
#         # Reconstruire le DDL depuis le dict filtré
#         return self._dict_to_schema_ddl(schema_filtre)

#     @staticmethod
#     def _schema_ddl_to_dict(ddl: str) -> dict[str, list[str]]:
#         """Extrait {table: [colonnes]} depuis le DDL texte de schema_ddl()."""
#         schema: dict[str, list[str]] = {}
#         current_table: str | None = None
#         for line in ddl.splitlines():
#             line = line.strip()
#             if line.upper().startswith("CREATE TABLE"):
#                 # ex: CREATE TABLE [FACT_Vente] (
#                 import re
#                 m = re.search(r"\[?dbo\]?\.\[?([A-Za-z_][A-Za-z0-9_]*)\]?", line, re.I)
#                 if not m:
#                     m = re.search(r"TABLE\s+\[?([A-Za-z_][A-Za-z0-9_]*)\]?", line, re.I)
#                 if m:
#                     current_table = m.group(1)
#                     schema[current_table] = []
#             elif current_table and line and not line.startswith((")", "CONSTRAINT", "GO", "/*", "--")):
#                 # ex: [CA_TND] DECIMAL(18,4) NULL,
#                 import re
#                 m = re.match(r"\[?([A-Za-z_][A-Za-z0-9_]*)\]?\s+", line)
#                 if m:
#                     schema[current_table].append(m.group(1))
#         return schema

#     @staticmethod
#     def _dict_to_schema_ddl(schema: dict[str, list[str]]) -> str:
#         """Reconstruit un DDL textuel depuis {table: [colonnes]}."""
#         lines = []
#         for table, cols in schema.items():
#             lines.append(f"Table {table} ({', '.join(cols)})")
#         return "\n".join(lines)

#     # ── Validation SQL (2e ligne de défense) ─────────────────────────────────

#     def _valider_sql(self, sql: str,
#                      session: Optional["Session"]) -> tuple[bool, list[str]]:
#         """Retourne (autorisé, tables_refusées)."""
#         if session is None or self._validator is None or self._authz is None:
#             return True, []
#         tables = self._validator.extraire_tables(sql)
#         decision = self._validator.valider(
#             sql, self._authz.tables_autorisees(session.role_actif)
#         )
#         return decision.autorise, decision.tables_refusees

#     # ── Audit ─────────────────────────────────────────────────────────────────

#     def _logger(self, session: Optional["Session"], question: str,
#                 sql: str, tables: list[str], statut: str,
#                 message_erreur: str | None, nb_lignes: int | None,
#                 duree_exec_ms: int | None, duree_totale_ms: int) -> None:
#         if session is None or self._audit is None:
#             return
#         interaction = Interaction(
#             session=session,
#             question=question,
#             sql_genere=sql or None,
#             tables_demandees=tables,
#             statut_execution=statut,
#             message_erreur=message_erreur,
#             nb_lignes_retournees=nb_lignes,
#             duree_execution_ms=duree_exec_ms,
#             duree_totale_ms=duree_totale_ms,
#             modele_llm=getattr(settings, "LLM_MODEL", None),
#         )
#         interaction_id = self._audit.logger_interaction(interaction)
#         if tables:
#             statut_acces = "Autorise" if statut == "Succes" else "Refuse"
#             self._audit.logger_acces_donnees(interaction_id, tables, statut_acces)

#     # ── Point d'entrée principal ──────────────────────────────────────────────

#     def ask(self, question: str,
#             state: DialogueState | None = None,
#             session: Optional["Session"] = None) -> PipelineResult:
#         t0 = time.perf_counter()
#         state = state or DialogueState()
#         effective_question = self._build_effective_question(question, state)
#         db_meta = DatabaseConnector()   # utilise settings.DB_URL
#         tables = db_meta.get_schema()
#         print(f"🔍 {len(tables)} tables trouvées dans la base métier :")
#         for t in tables:
#             print(f"   - {t.name}")

#         # Retrieval préliminaire
#         retrieved = self.store.retrieve(effective_question)
#         domain = self._domain_context(retrieved)

#         # 1. MRD-Q
#         if settings.FUSED_ANALYSIS:
#             analysis = self.mrdq.analyze_fused(effective_question, state, domain)
#         else:
#             analysis = self.mrdq.analyze(effective_question, state, domain)

#         if analysis.intent == 0:
#             state.pending_question = None
#             duree = int((time.perf_counter() - t0) * 1000)
#             self._logger(session, question, "", [], "Refuse",
#                          "Hors périmètre BI", None, None, duree)
#             return PipelineResult(
#                 Outcome.DECLINED, analysis=analysis,
#                 message="Cette question sort du périmètre BI. "
#                         "Posez une question sur les indicateurs métier.",
#             )

#         if analysis.intent == 1:
#             if state.pending_question is not None:
#                 analysis.intent = 2  # type: ignore
#                 if not analysis.rewritten_query:
#                     analysis.rewritten_query = (
#                         analysis.completed_query or effective_question
#                     )
#             else:
#                 state.pending_question = analysis.completed_query or effective_question
#                 return PipelineResult(
#                     Outcome.NEEDS_CLARIFICATION, analysis=analysis,
#                     message=analysis.clarification
#                             or "Pouvez-vous préciser votre question ?",
#                 )

#         # intent == 2 : on génère le SQL
#         state.pending_question = None
#         # ---- DÉBOGAGE : inspecter DialogueState.add ----
#         import inspect
#         print("=== DÉBOGAGE state.add ===")
#         print(f"type(state) = {type(state)}")
#         print(f"state = {state}")
#         print(f"state.__dict__ = {state.__dict__ if hasattr(state, '__dict__') else 'N/A'}")
#         if hasattr(state, 'add'):
#             print(f"Signature de state.add : {inspect.signature(state.add)}")
#         else:
#             print("❌ state n'a PAS de méthode 'add' !")
#         print(f"Arguments à passer : question={question}, completed={analysis.completed_query}, intent={analysis.intent}")
#         # ---- FIN DÉBOGAGE ----
#         state.add(original=question, completed=analysis.completed_query, intent=analysis.intent)  # Ajout de l'entrée dans l'historique

#         retrieved = self.store.retrieve(analysis.completed_query)

#         # 2. SIR
#         if settings.FUSED_ANALYSIS and analysis.rewritten_query:
#             sir = SIRResult(rewritten_query=analysis.rewritten_query, sir_json={})
#         else:
#             sir_question = analysis.completed_query
#             if state.active_context.all_periods:
#                 if "y compris les mois sans données" not in sir_question.lower():
#                     sir_question += (
#                         " — inclure TOUS les mois (même sans données)"
#                     )
#             sir = self.sqlgen.to_sir(sir_question, retrieved)

#         # 3. Schéma filtré par rôle (1re ligne de défense)
#         try:
#             schema = self._get_schema(session)
#         except PermissionError as exc:
#             duree = int((time.perf_counter() - t0) * 1000)
#             self._logger(session, question, "", [], "Refuse",
#                          str(exc), None, None, duree)
#             return PipelineResult(
#                 Outcome.ERROR, analysis=analysis,
#                 message=str(exc), acces_refuse=True,
#             )

#         # 4. Génération SQL
#         sql = self.sqlgen.to_sql(
#             sir, retrieved,
#             dialect=self.db.dialect,
#             schema=schema,
#         )

#         # 5. Validation SQL (2e ligne de défense)
#         tables = self._validator.extraire_tables(sql) if self._validator else []
#         autorise, refusees = self._valider_sql(sql, session)
#         if not autorise:
#             msg = f"Accès refusé aux tables : {', '.join(refusees)}"
#             duree = int((time.perf_counter() - t0) * 1000)
#             self._logger(session, question, sql, tables, "Refuse",
#                          msg, None, None, duree)
#             return PipelineResult(
#                 Outcome.ERROR, analysis=analysis, sir=sir, sql=sql,
#                 tables_demandees=tables, acces_refuse=True,
#                 message="Je n'ai pas les autorisations nécessaires pour "
#                         "répondre à cette question avec votre rôle actuel.",
#             )

#         # 6. Exécution + self-correction
#         last_exc = None
#         duree_exec_ms: int | None = None
#         for attempt in range(2):
#             t_exec = time.perf_counter()
#             try:
#                 data = self.db.run(sql)
#                 duree_exec_ms = int((time.perf_counter() - t_exec) * 1000)
#                 duree = int((time.perf_counter() - t0) * 1000)
#                 self._logger(session, question, sql, tables, "Succes",
#                              None, len(data), duree_exec_ms, duree)
#                 return PipelineResult(
#                     Outcome.SUCCESS,
#                     analysis=analysis, retrieved=retrieved,
#                     sir=sir, sql=sql, data=data,
#                     tables_demandees=tables,
#                     context_inherited=self._context_inherited_labels(state),
#                     message=(
#                         f"{len(data)} ligne(s) retournée(s)."
#                         + (" (corrigée automatiquement)" if attempt else "")
#                     ),
#                 )
#             except UnsafeQueryError as exc:
#                 duree = int((time.perf_counter() - t0) * 1000)
#                 self._logger(session, question, sql, tables, "Refuse",
#                              str(exc), None, None, duree)
#                 return PipelineResult(
#                     Outcome.ERROR, analysis=analysis, sir=sir, sql=sql,
#                     tables_demandees=tables,
#                     message=f"Requête refusée par les garde-fous : {exc}",
#                 )
#             except Exception as exc:  # noqa: BLE001
#                 last_exc = exc
#                 if attempt == 0:
#                     sql = self.sqlgen.fix_sql(
#                         sir, retrieved, sql, str(exc),
#                         dialect=self.db.dialect,
#                         schema=schema,
#                     )
#                     tables = (self._validator.extraire_tables(sql)
#                               if self._validator else [])

#         duree = int((time.perf_counter() - t0) * 1000)
#         self._logger(session, question, sql, tables, "Erreur",
#                      str(last_exc), None, None, duree)
#         return PipelineResult(
#             Outcome.ERROR, analysis=analysis, retrieved=retrieved,
#             sir=sir, sql=sql, tables_demandees=tables,
#             message=f"La requête générée n'a pas pu être exécutée : {last_exc}",
#         )

"""
Orchestrateur du pipeline complet.

Modification sécurité :
  - ask() accepte session: Session (optionnel — None = mode dev)
  - Filtrage schéma via AuthorizationService.filtrer_schema(role, schema_dict)
    → utilise get_schema() (objets structurés) au lieu de parser schema_ddl()
  - Validation SQL post-génération (SQLValidator)
  - Audit systématique (AuditLogger)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from core.db import DatabaseConnector, UnsafeQueryError
from core.LLM_cloud import LLMClient
from core.mrdq import MRDQ, DialogueState, AnalysisResult
from core.sir_mysql import SQLGenerator, SIRResult
from config import settings
from knowledge.store import KnowledgeStore
from knowledge.builder import KBEntry

try:
    from security.models import Session, Interaction
    from security.authorization import AuthorizationService, SQLValidator
    from security.audit_mysql import AuditLogger
    _SECURITY_AVAILABLE = True
except ImportError:
    _SECURITY_AVAILABLE = False
    Session = None  # type: ignore


class Outcome(str, Enum):
    DECLINED = "declined"
    NEEDS_CLARIFICATION = "clarify"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class PipelineResult:
    outcome: Outcome
    message: str = ""
    analysis: Optional[AnalysisResult] = None
    retrieved: list[KBEntry] = field(default_factory=list)
    sir: Optional[SIRResult] = None
    sql: str = ""
    data: Optional[pd.DataFrame] = None
    context_inherited: list[str] = field(default_factory=list)
    tables_demandees: list[str] = field(default_factory=list)
    acces_refuse: bool = False


class BIPipeline:
    def __init__(
        self,
        db: DatabaseConnector,
        store: KnowledgeStore,
        llm: LLMClient | None = None,
        authz: Optional["AuthorizationService"] = None,
        validator: Optional["SQLValidator"] = None,
        audit: Optional["AuditLogger"] = None,
    ):
        self.db = db
        self.store = store
        self.llm = llm or LLMClient()
        self.mrdq = MRDQ(self.llm)
        self.sqlgen = SQLGenerator(self.llm)
        self._authz = authz
        self._validator = validator
        self._audit = audit

    def _domain_context(self, retrieved: list[KBEntry]) -> str:
        terms = [e for e in retrieved if e.kb_type == "domain_knowledge"]
        return "\n".join(f"- {e.name}: {e.description}" for e in terms)

    def _build_effective_question(self, question: str,
                                   state: DialogueState) -> str:
        if state.pending_question:
            return (
                f"{state.pending_question} "
                f"(précision apportée par l'utilisateur : {question})"
            )
        return question

    def _context_inherited_labels(self, state: DialogueState) -> list[str]:
        ctx = state.active_context
        labels = []
        if ctx.dimensions:
            labels.append(f"Dimensions : {', '.join(ctx.dimensions)}")
        if ctx.filters:
            labels.append(f"Filtres : {', '.join(ctx.filters)}")
        if ctx.metric:
            labels.append(f"Métrique : {ctx.metric}")
        if ctx.all_periods:
            labels.append("Périodes complètes (mois vides inclus)")
        return labels

    # ── Schéma filtré ─────────────────────────────────────────────────────────

    def _get_schema(self, session) -> tuple[str, dict[str, list[str]]]:
        """Retourne (ddl_str, schema_dict) filtrés selon le rôle.

        Utilise get_schema() (objets structurés TableInfo) — jamais de parsing
        DDL texte qui échoue selon le format retourné par le connecteur.

        Admin / Directeur_General ('*') → schéma complet sans restriction.
        """
        raw_schema = self.db.get_schema()
        schema_dict: dict[str, list[str]] = {
            t.name: [c.name for c in t.columns]
            for t in raw_schema
        }

        # Mode dev sans sécurité
        if session is None or self._authz is None:
            return self.db.schema_ddl(), schema_dict

        # Filtrage RBAC
        schema_filtre = self._authz.filtrer_schema(session.role_actif, schema_dict)

        if not schema_filtre:
            raise PermissionError(
                "Votre rôle ne donne accès à aucune table du data warehouse. "
                "Contactez l'administrateur."
            )

        ddl_filtre = self._schema_dict_to_ddl(schema_filtre, raw_schema)
        return ddl_filtre, schema_filtre

    @staticmethod
    def _schema_dict_to_ddl(schema_filtre: dict[str, list[str]],
                              raw_schema) -> str:
        fk_by_table: dict[str, list[str]] = {}
        for t in raw_schema:
            if t.foreign_keys:
                fk_by_table[t.name] = [
                    f"{','.join(fk['constrained_columns'])} → {fk['referred_table']}"
                    for fk in t.foreign_keys
                ]
        lines = []
        for nom_table, colonnes in schema_filtre.items():
            col_str = ", ".join(colonnes) if colonnes else "(aucune colonne visible)"
            fk_str = ""
            if fks := fk_by_table.get(nom_table):
                fk_str = f" | FK: {'; '.join(fks)}"
            lines.append(f"Table {nom_table} ({col_str}){fk_str}")
        return "\n".join(lines)

    # ── Validation SQL ────────────────────────────────────────────────────────

    def _valider_sql(self, sql: str, session) -> tuple[bool, list[str]]:
        if session is None or self._validator is None or self._authz is None:
            return True, []
        decision = self._validator.valider(
            sql, self._authz.tables_autorisees(session.role_actif)
        )
        return decision.autorise, decision.tables_refusees

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _logger(self, session, question: str, sql: str, tables: list[str],
                statut: str, message_erreur, nb_lignes, duree_exec_ms,
                duree_totale_ms: int) -> None:
        if session is None or self._audit is None:
            return
        interaction = Interaction(
            session=session,
            question=question,
            sql_genere=sql or None,
            tables_demandees=tables,
            statut_execution=statut,
            message_erreur=message_erreur,
            nb_lignes_retournees=nb_lignes,
            duree_execution_ms=duree_exec_ms,
            duree_totale_ms=duree_totale_ms,
            modele_llm=getattr(settings, "LLM_MODEL", None),
        )
        interaction_id = self._audit.logger_interaction(interaction)
        if tables:
            statut_acces = "Autorise" if statut == "Succes" else "Refuse"
            self._audit.logger_acces_donnees(interaction_id, tables, statut_acces)

    # ── Point d'entrée ────────────────────────────────────────────────────────

    def ask(self, question: str,
            state: DialogueState | None = None,
            session=None) -> PipelineResult:
        t0 = time.perf_counter()
        state = state or DialogueState()
        effective_question = self._build_effective_question(question, state)
        print(f"effective_question: {effective_question}")  # DEBUG

        retrieved = self.store.retrieve(effective_question)
        domain = self._domain_context(retrieved)

        # 1. MRD-Q
        if settings.FUSED_ANALYSIS:
            analysis = self.mrdq.analyze_fused(effective_question, state, domain)
        else:
            analysis = self.mrdq.analyze(effective_question, state, domain)

        if analysis.intent == 0:
            state.pending_question = None
            duree = int((time.perf_counter() - t0) * 1000)
            self._logger(session, question, "", [], "Refuse",
                         "Hors périmètre BI", None, None, duree)
            return PipelineResult(
                Outcome.DECLINED, analysis=analysis,
                message="Cette question sort du périmètre BI. "
                        "Posez une question sur les indicateurs métier.",
            )

        if analysis.intent == 1:
            if state.pending_question is not None:
                analysis.intent = 2  # type: ignore
                if not analysis.rewritten_query:
                    analysis.rewritten_query = (
                        analysis.completed_query or effective_question
                    )
            else:
                state.pending_question = analysis.completed_query or effective_question
                return PipelineResult(
                    Outcome.NEEDS_CLARIFICATION, analysis=analysis,
                    message=analysis.clarification
                            or "Pouvez-vous préciser votre question ?",
                )

        state.pending_question = None
        state.add(
            original=question,
            completed=analysis.completed_query,
            intent=analysis.intent,
        )
        retrieved = self.store.retrieve(analysis.completed_query)

        # 2. SIR
        if settings.FUSED_ANALYSIS and analysis.rewritten_query:
            sir = SIRResult(rewritten_query=analysis.rewritten_query, sir_json={})
        else:
            sir_question = analysis.completed_query
            if state.active_context.all_periods:
                if "y compris les mois sans données" not in sir_question.lower():
                    sir_question += " — inclure TOUS les mois (même sans données)"
            sir = self.sqlgen.to_sir(sir_question, retrieved)

        # 3. Schéma filtré (1re ligne de défense)
        try:
            schema_ddl, schema_dict = self._get_schema(session)
        except PermissionError as exc:
            duree = int((time.perf_counter() - t0) * 1000)
            self._logger(session, question, "", [], "Refuse",
                         str(exc), None, None, duree)
            return PipelineResult(
                Outcome.ERROR, analysis=analysis,
                message=str(exc), acces_refuse=True,
            )

        # 4. Génération SQL
        sql = self.sqlgen.to_sql(
            sir, retrieved,
            dialect=self.db.dialect,
            schema=schema_ddl,
        )

        # 5. Validation SQL (2e ligne de défense)
        tables = self._validator.extraire_tables(sql) if self._validator else []
        autorise, refusees = self._valider_sql(sql, session)
        if not autorise:
            msg = f"Accès refusé aux tables : {', '.join(refusees)}"
            duree = int((time.perf_counter() - t0) * 1000)
            self._logger(session, question, sql, tables, "Refuse",
                         msg, None, None, duree)
            return PipelineResult(
                Outcome.ERROR, analysis=analysis, sir=sir, sql=sql,
                tables_demandees=tables, acces_refuse=True,
                message="Je n'ai pas les autorisations nécessaires pour "
                        "répondre à cette question avec votre rôle actuel.",
            )

        # 6. Exécution + self-correction
        last_exc = None
        duree_exec_ms = None
        for attempt in range(2):
            t_exec = time.perf_counter()
            try:
                data = self.db.run(sql)
                duree_exec_ms = int((time.perf_counter() - t_exec) * 1000)
                duree = int((time.perf_counter() - t0) * 1000)
                self._logger(session, question, sql, tables, "Succes",
                             None, len(data), duree_exec_ms, duree)
                return PipelineResult(
                    Outcome.SUCCESS,
                    analysis=analysis, retrieved=retrieved,
                    sir=sir, sql=sql, data=data,
                    tables_demandees=tables,
                    context_inherited=self._context_inherited_labels(state),
                    message=(
                        f"{len(data)} ligne(s) retournée(s)."
                        + (" (corrigée automatiquement)" if attempt else "")
                    ),
                )
            except UnsafeQueryError as exc:
                duree = int((time.perf_counter() - t0) * 1000)
                self._logger(session, question, sql, tables, "Refuse",
                             str(exc), None, None, duree)
                return PipelineResult(
                    Outcome.ERROR, analysis=analysis, sir=sir, sql=sql,
                    tables_demandees=tables,
                    message=f"Requête refusée par les garde-fous : {exc}",
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0:
                    sql = self.sqlgen.fix_sql(
                        sir, retrieved, sql, str(exc),
                        dialect=self.db.dialect,
                        schema=schema_ddl,
                    )
                    tables = (self._validator.extraire_tables(sql)
                              if self._validator else [])

        duree = int((time.perf_counter() - t0) * 1000)
        self._logger(session, question, sql, tables, "Erreur",
                     str(last_exc), None, None, duree)
        return PipelineResult(
            Outcome.ERROR, analysis=analysis, retrieved=retrieved,
            sir=sir, sql=sql, tables_demandees=tables,
            message=f"La requête générée n'a pas pu être exécutée : {last_exc}",
        )
