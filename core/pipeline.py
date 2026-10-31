"""
Orchestrateur du pipeline complet (étape 5).

Question NL
  -> MRD-Q (analyse dialogue + intention querying)
       intent 0 -> déclin poli
       intent 1 -> clarification renvoyée à l'utilisateur
       intent 2 -> on continue
  -> Knowledge Retrieval (2 phases + multi-hop)
  -> SIR (réécriture CoT)
  -> SQL Generation
  -> validation lecture seule + LIMIT auto + exécution
  -> résultat (DataFrame)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from core.db import DatabaseConnector, UnsafeQueryError
from core.LLM_cloud import LLMClient
from core.mrdq import MRDQ, DialogueState, AnalysisResult
from core.sir import SQLGenerator, SIRResult
from config import settings
from knowledge.store import KnowledgeStore
from knowledge.builder import KBEntry


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





class BIPipeline:
    def __init__(self, db: DatabaseConnector, store: KnowledgeStore,
                 llm: LLMClient | None = None):
        self.db = db
        self.store = store
        self.llm = llm or LLMClient()
        self.mrdq = MRDQ(self.llm)
        self.sqlgen = SQLGenerator(self.llm)
        # Dialecte normalisé une seule fois à l'initialisation

    def _domain_context(self, retrieved: list[KBEntry]) -> str:
        terms = [e for e in retrieved if e.kb_type == "domain_knowledge"]
        return "\n".join(f"- {e.name}: {e.description}" for e in terms)

    def ask(self, question: str, state: DialogueState | None = None) -> PipelineResult:
        state = state or DialogueState()

        # Si une question était en attente de clarification, on fusionne la
        # réponse de l'utilisateur avec cette question avant toute analyse.
        if state.pending_question:
            effective_question = (
                f"{state.pending_question} "
                f"(précision apportée par l'utilisateur : {question})"
            )
        else:
            effective_question = question

        # Retrieval préliminaire pour nourrir l'analyse en connaissance domaine
        retrieved = self.store.retrieve(effective_question)
        domain = self._domain_context(retrieved)

        # 1. MRD-Q
        if settings.FUSED_ANALYSIS:
            analysis = self.mrdq.analyze_fused(effective_question, state, domain)
        else:
            analysis = self.mrdq.analyze(effective_question, state, domain)

        if analysis.intent == 0:
            state.pending_question = None
            return PipelineResult(
                Outcome.DECLINED, analysis=analysis,
                message="Cette question sort du périmètre d'analyse de données. "
                        "Posez une question sur les indicateurs métier.",
            )

        if analysis.intent == 1:
            # GARDE-FOU ANTI-BOUCLE : une seule clarification autorisée.
            if state.pending_question is not None:
                analysis.intent = 2  # type: ignore
                if not analysis.rewritten_query:
                    analysis.rewritten_query = analysis.completed_query or effective_question
            else:
                state.pending_question = analysis.completed_query or effective_question
                return PipelineResult(
                    Outcome.NEEDS_CLARIFICATION, analysis=analysis,
                    message=analysis.clarification or "Pouvez-vous préciser votre question ?",
                )

        # Question complète
        state.pending_question = None
        state.add(analysis.completed_query)

        # Re-retrieval sur la question complétée
        retrieved = self.store.retrieve(analysis.completed_query)

        # 2. SIR
        if settings.FUSED_ANALYSIS and analysis.rewritten_query:
            sir = SIRResult(rewritten_query=analysis.rewritten_query, sir_json={})
        else:
            sir = self.sqlgen.to_sir(analysis.completed_query, retrieved)

        # Schéma COMPLET (pas filtré par le retrieval)
        full_schema = self.db.schema_ddl()

        # 3. SQL Generation — dialecte normalisé transmis
        sql = self.sqlgen.to_sql(
            sir, retrieved,
            dialect="mssql",   # ← toujours "mssql" pour SQL Server
            schema=full_schema,
        )

        # 4. Exécution avec self-correction (max 1 réessai)
        last_exc = None
        for attempt in range(2):
            try:
                data = self.db.run(sql)
                return PipelineResult(
                    Outcome.SUCCESS, analysis=analysis, retrieved=retrieved,
                    sir=sir, sql=sql, data=data,
                    message=f"{len(data)} ligne(s) retournée(s)."
                            + (" (corrigée automatiquement)" if attempt else ""),
                )
            except UnsafeQueryError as exc:
                return PipelineResult(
                    Outcome.ERROR, analysis=analysis, retrieved=retrieved,
                    sir=sir, sql=sql,
                    message=f"Requête refusée par les garde-fous : {exc}",
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0:
                    sql = self.sqlgen.fix_sql(
                        sir, retrieved, sql, str(exc),
                        dialect="mssql",   # ← normalisé ici aussi
                        schema=full_schema,
                    )

        return PipelineResult(
            Outcome.ERROR, analysis=analysis, retrieved=retrieved,
            sir=sir, sql=sql,
            message=f"La requête générée n'a pas pu être exécutée : {last_exc}",
        )