"""
OUTIL DE VÉRIFICATION #1 — Export du schéma indexé.

Génère deux fichiers téléchargeables décrivant EXACTEMENT ce que le système
a compris de la base au moment de l'indexation :

  - schema_export.txt   : lisible (tables, colonnes+types, clés étrangères,
                          + résumé des entrées de la Knowledge Base)
  - schema_export.json  : structuré (exploitable par un autre outil)

Usage :
    python -m scripts.export_schema
    python -m scripts.export_schema --out mon_dossier

Ne dépend PAS d'Ollama ni de Qdrant : lit seulement la base + construit la KB.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

from core.db import DatabaseConnector
from knowledge.builder import build_full_kb


def export_schema(out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = DatabaseConnector()
    schema = db.get_schema()
    kb = build_full_kb(db)

    # ── Structure JSON ──────────────────────────────────────────────
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_url": db.db_url,
        "dialect": db.dialect,
        "table_count": len(schema),
        "tables": [
            {
                "name": t.name,
                "columns": [
                    {"name": c.name, "type": c.type,
                     "nullable": c.nullable, "primary_key": c.primary_key}
                    for c in t.columns
                ],
                "foreign_keys": t.foreign_keys,
            }
            for t in schema
        ],
        "knowledge_base": {
            "entry_count": len(kb),
            "by_type": _count_by(kb, "kb_type"),
            "by_label": _count_by(kb, "label"),
            "entries": [
                {"name": e.name, "label": e.label, "kb_type": e.kb_type,
                 "anc_name": e.anc_name, "anc_label": e.anc_label,
                 "description": e.description}
                for e in kb
            ],
        },
    }
    json_path = out_dir / "schema_export.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Version texte lisible ───────────────────────────────────────
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("EXPORT DU SCHÉMA INDEXÉ — vérification de ce que le système a compris")
    lines.append("=" * 70)
    lines.append(f"Généré le      : {data['generated_at']}")
    lines.append(f"Base           : {db.db_url}")
    lines.append(f"SGBD (dialect) : {db.dialect}")
    lines.append(f"Nb de tables   : {len(schema)}")
    lines.append("")
    lines.append("── SCHÉMA COMPLET (ce qui est passé au LLM pour générer le SQL) ──")
    lines.append("")
    lines.append(db.schema_ddl())
    lines.append("")
    lines.append("── DÉTAIL PAR TABLE ──")
    for t in schema:
        lines.append("")
        lines.append(f"TABLE {t.name}")
        for c in t.columns:
            tags = []
            if c.primary_key: tags.append("PK")
            if not c.nullable: tags.append("NOT NULL")
            tag = f"  [{', '.join(tags)}]" if tags else ""
            lines.append(f"    - {c.name} : {c.type}{tag}")
        for fk in t.foreign_keys:
            lines.append(f"    FK {','.join(fk['constrained_columns'])} -> "
                         f"{fk['referred_table']}.{','.join(fk['referred_columns'])}")
    lines.append("")
    lines.append("── KNOWLEDGE BASE (entrées indexées) ──")
    lines.append(f"Total : {len(kb)} entrées")
    lines.append(f"Par type  : {data['knowledge_base']['by_type']}")
    lines.append(f"Par label : {data['knowledge_base']['by_label']}")
    lines.append("")
    for e in kb:
        anc = f"  (parent: {e.anc_label} {e.anc_name})" if e.anc_name else ""
        lines.append(f"  [{e.label:8}] {e.name}{anc}")
    txt_path = out_dir / "schema_export.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return txt_path, json_path


def _count_by(kb, attr: str) -> dict:
    out: dict[str, int] = {}
    for e in kb:
        k = getattr(e, attr)
        out[k] = out.get(k, 0) + 1
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="exports", help="dossier de sortie")
    args = ap.parse_args()
    txt, js = export_schema(Path(args.out))
    print(f"✓ Export texte : {txt}")
    print(f"✓ Export JSON  : {js}")
    print("\nOuvre schema_export.txt pour vérifier le schéma et les entrées KB.")
