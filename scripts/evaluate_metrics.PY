# -*- coding: utf-8 -*-
"""
Script d'evaluation des metriques de performance du pipeline BI conversationnel.

Calcule, sur un jeu de questions "golden" :
  - Temps de reponse moyen (bout en bout, execution SQL incluse)
  - UEX (Usage Execution Accuracy)   : le resultat retourne par le pipeline
    correspond-il (apres normalisation) au resultat du SQL de reference ?
  - KR Recall (Knowledge Retrieval)  : les entrees KB pertinentes sont-elles
    bien remontees par le retrieval ?
  - SL Recall (Schema Linking)       : les tables necessaires sont-elles bien
    identifiees dans le SQL genere (reutilise SQLValidator.extraire_tables,
    deja calcule par le pipeline lui-meme) ?

Usage :
    python -m scripts.evaluate_metrics --golden tests/golden_questions_unimed.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core.db import DatabaseConnector
from core.mrdq import DialogueState
from core.pipeline_new import BIPipeline, Outcome
from knowledge.builder import build_full_kb
from knowledge.store import KnowledgeStore
from security.authorization import SQLValidator


def get_db() -> DatabaseConnector:
    # Utilise DB_URL depuis l'environnement (comme app_new.py::_init_services)
    return DatabaseConnector()


def build_pipeline(db: DatabaseConnector) -> BIPipeline:
    store = KnowledgeStore()
    kb = build_full_kb(db)
    store.index(kb)
    # validator seul (sans authz/audit) -> le pipeline tourne en mode "dev"
    # (pas de filtrage RBAC, session=None) mais extrait quand meme les
    # tables du SQL genere pour le SL Recall.
    return BIPipeline(db=db, store=store, validator=SQLValidator())


def normalize_rows(df: pd.DataFrame | None) -> frozenset[tuple[str, ...]]:
    """Normalise un DataFrame pour comparaison (ordre lignes/colonnes
    insensible, valeurs converties en str)."""
    if df is None or df.empty:
        return frozenset()
    records = df.to_dict(orient="records")
    normalized = set()
    for row in records:
        values = tuple(str(v) for _, v in sorted(row.items()))
        normalized.add(values)
    return frozenset(normalized)


def compute_uex(db: DatabaseConnector, generated_df: pd.DataFrame | None,
                 golden_sql: str) -> bool:
    try:
        golden_result = db.run(golden_sql)
    except Exception:
        return False
    return normalize_rows(generated_df) == normalize_rows(golden_result)


def compute_sl_recall(found_tables: list[str], required_tables: list[str]) -> float:
    found = {t.lower() for t in found_tables}
    required = {t.lower() for t in required_tables}
    if not required:
        return 1.0
    return len(found & required) / len(required)


def compute_kr_recall(retrieved_entries: list[Any], relevant_entries: list[str]) -> float:
    retrieved_names = {e.name.lower() for e in retrieved_entries}
    relevant = {r.lower() for r in relevant_entries}
    if not relevant:
        return 1.0
    return len(retrieved_names & relevant) / len(relevant)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default="tests/golden_questions_unimed.json")
    parser.add_argument("--out", default="exports/eval_results.json")
    args = parser.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    db = get_db()
    pipeline = build_pipeline(db)

    per_question = []
    for item in golden:
        state = DialogueState()
        t0 = time.perf_counter()
        result = pipeline.ask(question=item["question"], state=state, session=None)
        elapsed = time.perf_counter() - t0

        generated_df = result.data if result.outcome == Outcome.SUCCESS else None
        uex = compute_uex(db, generated_df, item["golden_sql"])
        sl_recall = compute_sl_recall(result.tables_demandees, item["required_tables"])
        kr_recall = compute_kr_recall(result.retrieved, item["relevant_kb_entries"])

        per_question.append({
            "id": item["id"],
            "question": item["question"],
            "outcome": result.outcome.value,
            "response_time_s": round(elapsed, 2),
            "uex": uex,
            "sl_recall": round(sl_recall, 3),
            "kr_recall": round(kr_recall, 3),
            "generated_sql": result.sql,
        })
        print(f"[{item['id']}] {result.outcome.value} | {elapsed:.2f}s | "
              f"UEX={uex} | SL={sl_recall:.2f} | KR={kr_recall:.2f}")

    summary = {
        "avg_response_time_s": round(statistics.mean(r["response_time_s"] for r in per_question), 2),
        "uex_pct": round(100 * statistics.mean(r["uex"] for r in per_question), 1),
        "kr_recall_pct": round(100 * statistics.mean(r["kr_recall"] for r in per_question), 1),
        "sl_recall_pct": round(100 * statistics.mean(r["sl_recall"] for r in per_question), 1),
        "n_questions": len(per_question),
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps({"summary": summary, "details": per_question}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    print("\n=== LaTeX row ===")
    print(
        f"{summary['avg_response_time_s']}\\,s & "
        f"{summary['uex_pct']}\\% & "
        f"{summary['kr_recall_pct']}\\% & "
        f"{summary['sl_recall_pct']}\\% \\\\"
    )


if __name__ == "__main__":
    main()