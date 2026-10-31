"""
OUTIL DE VÉRIFICATION #2 — Inspecteur de retrieval.

Pour une question donnée, montre la PILE des entrées que le retrieval a
remontées (ce que le modèle "comprend" comme contexte), en distinguant
tables et colonnes, dans l'ordre de pertinence.

Affiche à l'écran ET exporte un fichier téléchargeable :
  - retrieval_<slug>.txt

Usage :
    python -m scripts.inspect_retrieval "Quels sont les 5 artistes par CA en 2022 ?"
    python -m scripts.inspect_retrieval "CA par pays" --out exports

Prérequis (chez vous) : Qdrant lancé + modèle bge-m3 (comme pour l'app).
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

from core.db import DatabaseConnector
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb, KBEntry


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "question"


def inspect(question: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    store.warmup()  # précharge les modèles (embeddings + reranker si activé)

    retrieved = store.retrieve(question)

    # Regroupe par nature
    tables = [e for e in retrieved if e.label == "Table"]
    columns = [e for e in retrieved if e.label == "Column"]
    terms = [e for e in retrieved if e.label == "Term"]
    tasks = [e for e in retrieved if e.label == "Task"]

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("INSPECTION DU RETRIEVAL — ce que le modèle a remonté comme contexte")
    lines.append("=" * 70)
    lines.append(f"Question : {question}")
    lines.append(f"Entrées remontées : {len(retrieved)} (ordre = pertinence décroissante)")
    lines.append("")

    lines.append(f"── PILE ORDONNÉE ({len(retrieved)}) ──")
    for rank, e in enumerate(retrieved, 1):
        anc = f"  ⟵ {e.anc_label} {e.anc_name}" if e.anc_name else ""
        lines.append(f"  {rank:2}. [{e.label:8}] {e.name}{anc}")

    lines.append("")
    lines.append(f"── TABLES comprises ({len(tables)}) ──")
    for e in tables:
        cols = e.payload.get("columns", [])
        lines.append(f"  • {e.name} : {', '.join(cols) if cols else '(colonnes non listées)'}")

    lines.append("")
    lines.append(f"── COLONNES comprises ({len(columns)}) ──")
    for e in columns:
        lines.append(f"  • {e.name}  ({e.payload.get('type','?')})")

    if terms:
        lines.append("")
        lines.append(f"── TERMES MÉTIER mobilisés ({len(terms)}) ──")
        for e in terms:
            lines.append(f"  • {e.name} : {e.description}")

    if tasks:
        lines.append("")
        lines.append(f"── EXEMPLES (démonstrations) mobilisés ({len(tasks)}) ──")
        for e in tasks:
            lines.append(f"  • {e.payload.get('question','?')} -> {e.payload.get('sql','?')}")

    report = "\n".join(lines)
    print(report)

    out_path = out_dir / f"retrieval_{_slug(question)}.txt"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✓ Rapport exporté : {out_path}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question", help="la question à inspecter (entre guillemets)")
    ap.add_argument("--out", default="exports", help="dossier de sortie")
    args = ap.parse_args()
    inspect(args.question, Path(args.out))
