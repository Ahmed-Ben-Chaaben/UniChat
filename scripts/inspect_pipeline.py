"""
OUTIL DE VÉRIFICATION #3 — Inspecteur de pipeline COMPLET.

Pour une question, montre TOUTE la chaîne de décision :
  1. mode (fusionné ou non)
  2. analyse MRD-Q (intent, completed_query)
  3. SIR (Key Components, métrique retenue, rewritten_query)
  4. indications transmises au générateur SQL
  5. SQL généré
  6. résultat exécuté

C'est l'outil pour comprendre POURQUOI un résultat est incorrect : on voit
si la métrique de la SIR est bien celle utilisée dans le SQL, ou si le SQL
a retombé sur Invoice.Total (piège du grain).

Usage :
    python -m scripts.inspect_pipeline "Les noms des 5 artistes avec le plus de CA en 2022"

Prérequis : Qdrant + Ollama actifs (comme l'app).
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

from config import settings
from core.db import DatabaseConnector
from core.llm import LLMClient
from core.mrdq import MRDQ, DialogueState
from core.sir import SQLGenerator, SIRResult, _format_sir_hints
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "question"


def inspect_pipeline(question: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = DatabaseConnector()
    store = KnowledgeStore(); store.index(build_full_kb(db)); store.warmup()
    llm = LLMClient()
    mrdq = MRDQ(llm); sqlgen = SQLGenerator(llm)

    L: list[str] = []
    def out(s=""): L.append(s); print(s)

    out("=" * 70)
    out("INSPECTION PIPELINE COMPLET")
    out("=" * 70)
    out(f"Question : {question}")
    out(f"Mode fusionné (FUSED_ANALYSIS) : {settings.FUSED_ANALYSIS}")
    out(f"Reranker (USE_RERANKER)        : {settings.USE_RERANKER}")
    out(f"Modèle LLM                     : {settings.LLM_MODEL}")
    out("")

    state = DialogueState()
    retrieved = store.retrieve(question)
    domain = "\n".join(f"- {e.name}: {e.description}"
                       for e in retrieved if e.kb_type == "domain_knowledge")

    # 1. Analyse
    out("── 1. ANALYSE MRD-Q ──")
    if settings.FUSED_ANALYSIS:
        analysis = mrdq.analyze_fused(question, state, domain)
        out("  (mode fusionné : SIR produite dans le même appel)")
    else:
        analysis = mrdq.analyze(question, state, domain)
    out(f"  intent          : {analysis.intent}")
    out(f"  completed_query : {analysis.completed_query}")
    out(f"  clarification   : {analysis.clarification}")
    out(f"  rewritten_query : {analysis.rewritten_query}")
    out("")

    if analysis.intent != 2:
        out(f"  -> Le pipeline s'arrête ici (intent={analysis.intent}).")
        return _save(L, out_dir, question)

    # 2. SIR
    out("── 2. SIR (Semantic Intermediate Representation) ──")
    if settings.FUSED_ANALYSIS and analysis.rewritten_query:
        sir = SIRResult(rewritten_query=analysis.rewritten_query, sir_json={})
        out("  ⚠ mode fusionné : sir_json VIDE -> AUCUNE métrique transmise au SQL !")
        out("     (c'est la cause probable si le SQL retombe sur Invoice.Total)")
    else:
        retrieved = store.retrieve(analysis.completed_query)
        sir = sqlgen.to_sir(analysis.completed_query, retrieved)
        import json
        out("  Key Components :")
        for k, v in (sir.sir_json.get("Key Components", {}) or {}).items():
            out(f"     {k} : {v}")
    out(f"  rewritten_query : {sir.rewritten_query}")
    out("")

    # 3. Indications transmises au SQL
    out("── 3. INDICATIONS TRANSMISES AU GÉNÉRATEUR SQL ──")
    out(_format_sir_hints(sir))
    out("")

    # 4. SQL généré
    out("── 4. SQL GÉNÉRÉ ──")
    full_schema = db.schema_ddl()
    sql = sqlgen.to_sql(sir, retrieved, dialect=db.dialect, schema=full_schema)
    out(sql)
    out("")

    # Diagnostic automatique du piège du grain
    out("── 5. DIAGNOSTIC AUTOMATIQUE ──")
    if "Invoice.Total" in sql or "SUM(Total)" in sql.replace(" ", ""):
        if "InvoiceLine" in sql:
            out("  ⚠ ALERTE PIÈGE DU GRAIN : le SQL joint InvoiceLine MAIS somme")
            out("    Invoice.Total -> montants gonflés (facture comptée plusieurs fois).")
            out("    La bonne mesure serait SUM(InvoiceLine.UnitPrice * Quantity).")
        else:
            out("  ℹ Le SQL utilise Invoice.Total (correct SI la question est au")
            out("    niveau facture/client/pays, incorrect au niveau artiste/track).")
    elif "UnitPrice" in sql and "Quantity" in sql:
        out("  ✓ Le SQL utilise SUM(UnitPrice * Quantity) : mesure correcte au")
        out("    niveau produit/artiste.")
    out("")

    # 6. Exécution
    out("── 6. RÉSULTAT EXÉCUTÉ ──")
    try:
        data = db.run(sql)
        out(f"  {len(data)} ligne(s) :")
        out(data.to_string(index=False))
    except Exception as e:
        out(f"  ERREUR : {e}")

    return _save(L, out_dir, question)


def _save(lines, out_dir, question):
    path = out_dir / f"pipeline_{_slug(question)}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ Rapport exporté : {path}")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--out", default="exports")
    args = ap.parse_args()
    inspect_pipeline(args.question, Path(args.out))
