# """
# OUTIL DE VÉRIFICATION #4 — Traçage COMPLET du retrieval, étape par étape.

# Ouvre la boîte noire de retrieve() et montre CHAQUE sous-étape :

#   ÉTAPE 1 — Tokenisation : les mots-clés extraits de la question.
#   ÉTAPE 2 — Recherche VECTORIELLE (bge-m3 -> Qdrant) : tables/colonnes par
#             similarité sémantique, avec leur score.
#   ÉTAPE 3 — Recherche LEXICALE (BM25) : tables/colonnes par correspondance de
#             mots, avec leur score.
#   ÉTAPE 4 — Fusion : union des candidats vectoriels + lexicaux.
#   ÉTAPE 5 — Multi-hop : ancêtres ajoutés par traversée anc_name.
#   ÉTAPE 6 — SCORE PAPIER (Sim + α·Embed + β·Heat) : détail par candidat,
#             pour comprendre et régler α (ALPHA) et β (BETA).
#   ÉTAPE 7 — Rerank final (méthode active dans settings.SCORING_METHOD).

# Affiche à l'écran ET exporte un fichier téléchargeable.

# Usage :
#     python -m scripts.trace_retrieval "Les noms des 5 artistes avec le plus de CA en 2022"
#     python -m scripts.trace_retrieval "CA par pays" --out exports

# Prérequis : Qdrant lancé + modèle bge-m3 (comme l'app).
# """
# from __future__ import annotations
# import argparse
# import re
# from pathlib import Path

# from config import settings
# from core.db import DatabaseConnector
# from knowledge.store import KnowledgeStore, EmbeddingModel, Reranker, _tokenize
# from knowledge.builder import build_full_kb, KBEntry


# def _slug(text: str) -> str:
#     return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "question"


# def _kind(entry: KBEntry) -> str:
#     """Résumé court de la nature d'une entrée."""
#     if entry.label == "Column":
#         return f"colonne {entry.name} ({entry.payload.get('type','?')})"
#     if entry.label == "Table":
#         return f"TABLE {entry.name}"
#     return f"{entry.label} {entry.name}"


# def trace(question: str, out_dir: Path) -> Path:
#     out_dir.mkdir(parents=True, exist_ok=True)
#     db = DatabaseConnector()
#     store = KnowledgeStore()
#     store.index(build_full_kb(db))
#     store.warmup()

#     coarse_k = settings.COARSE_TOP_K
#     fine_k = settings.FINE_TOP_K

#     L: list[str] = []
#     def out(s=""): L.append(s); print(s)

#     out("=" * 72)
#     out("TRAÇAGE DU RETRIEVAL — comment le programme extrait les données")
#     out("=" * 72)
#     out(f"Question : {question}")
#     out(f"Paramètres : coarse_k={coarse_k} (candidats/méthode), fine_k={fine_k} (gardés)")
#     out(f"Reranker actif : {settings.USE_RERANKER}")
#     out(f"Méthode de scoring active : {settings.SCORING_METHOD}")
#     out(f"  (ALPHA={settings.ALPHA}, BETA={settings.BETA} pour la méthode 'paper')")
#     out("")

#     # ─ ÉTAPE 1 : Tokenisation ───────────────────────────────────────
#     tokens = _tokenize(question)
#     out("── ÉTAPE 1 — MOTS-CLÉS EXTRAITS (tokenisation) ──")
#     out(f"  {tokens}")
#     out(f"  ({len(tokens)} tokens : minuscules, '.' et '|' remplacés par espaces)")
#     out("")

#     # ─ ÉTAPE 2 : Recherche vectorielle (sémantique) ─────────────────
#     out("── ÉTAPE 2 — RECHERCHE VECTORIELLE (similarité sémantique bge-m3) ──")
#     qvec = EmbeddingModel.encode([question])[0]
#     out(f"  Vecteur question : dimension {len(qvec)} (bge-m3)")
#     vec_hits = store.client.query_points(store.collection, query=qvec, limit=coarse_k).points
#     out(f"  Top {min(coarse_k, len(vec_hits))} par similarité sémantique :")
#     vec_names = []
#     vec_scores: dict[str, float] = {}   # {name: score} — réutilisé à l'ÉTAPE 6
#     for rank, h in enumerate(vec_hits, 1):
#         name = h.payload["name"]
#         vec_names.append(name)
#         score = getattr(h, "score", None)
#         if isinstance(score, (int, float)):
#             vec_scores[name] = float(score)
#         score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "?"
#         lbl = h.payload.get("label", "?")
#         out(f"    {rank:2}. [{lbl:7}] {name}   (score={score_str})")
#     out("")

#     # ─ ÉTAPE 3 : Recherche lexicale (BM25) ──────────────────────────
#     out("── ÉTAPE 3 — RECHERCHE LEXICALE (BM25, correspondance de mots) ──")
#     scores = store._bm25.get_scores(tokens)
#     scores_normalisé = store._norm(scores)
#     top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:coarse_k]
#     bm25_names = []
#     out(f"  Top {coarse_k} par score BM25 :")
#     for rank, i in enumerate(top_idx, 1):
#         e = store.entries[i]
#         bm25_names.append(e.name)
#         out(f"    {rank:2}. [{e.label:7}] {e.name}   (bm25={scores[i]:.3f}) score_norm={scores_normalisé[i]:.3f}")
#     out("")

#     # ─ ÉTAPE 4 : Fusion ─────────────────────────────────────────────
#     out("── ÉTAPE 4 — FUSION (union vectoriel + lexical) ──")
#     fused = set(vec_names) | set(bm25_names)
#     only_vec = set(vec_names) - set(bm25_names)
#     only_bm = set(bm25_names) - set(vec_names)
#     both = set(vec_names) & set(bm25_names)
#     out(f"  {len(fused)} candidats uniques après fusion")
#     out(f"  - présents dans LES DEUX méthodes : {sorted(both)}")
#     out(f"  - vectoriel seulement : {sorted(only_vec)}")
#     out(f"  - BM25 seulement      : {sorted(only_bm)}")
#     out("")

#     # ─ ÉTAPE 5 : Multi-hop ──────────────────────────────────────────
#     out("── ÉTAPE 5 — MULTI-HOP (traversée des ancêtres via anc_name) ──")
#     expanded = store._expand_ancestors(fused)
#     added = expanded - fused
#     out(f"  {len(expanded)} candidats après multi-hop (+{len(added)} ancêtres)")
#     if added:
#         for n in sorted(added):
#             e = store._by_name.get(n)
#             anc = f" ⟵ {e.anc_label} {e.anc_name}" if e and e.anc_name else ""
#             out(f"    + {n}{anc}")
#     else:
#         out("    (aucun ancêtre supplémentaire)")
#     out("")

#     # Patch pour scripts/trace_retrieval.py — remplacer le bloc ÉTAPE 6
#     # Chercher le texte "ÉTAPE 6" dans votre trace_retrieval.py et remplacer
#     # le bloc d'affichage du tableau par ce code.

#     # ── ÉTAPE 6 — SCORE PAPIER (bloc à remplacer dans trace_retrieval.py) ───────

#     # Remplacer la ligne d'en-tête et la boucle d'affichage par :

#     print("\n── ÉTAPE 6 — SCORE PAPIER : Score(t) = Sim(t) + DomBoost(t) + α·Embed(t) + β·Heat(t) ──")
#     print("  (calculé pour TOUS les candidats fusionnés)")
#     print()
#     print(f"   {'rg':>3}  {'entrée':<34} {'Sim(t)':>7} {'DomBoost':>9} {'Embed':>7} "
#         f"{'α·Embed':>8} {'Heat':>6} {'β·Heat':>7} {'Score':>8}")
#     print("  " + "-" * 97)

#     # qvec doit être disponible dans le scope (déjà calculé à l'étape 2)
#     rows = store.score_paper_breakdown(cand_entries=fused, vec_scores=vec_scores, question=question, qvec=qvec)

#     for rank, r in enumerate(rows, 1):
#         e = r["entry"]
#         label_short = e.label[:4] if e.label else "    "
#         name_short = e.name[:32] if len(e.name) > 32 else e.name
#         print(
#             f"  {rank:>3}  [{label_short:<6}] {name_short:<26} "
#             f"{r['sim']:>7.4f} {r['domain_boost']:>9.4f} {r['embed']:>7.3f} "
#             f"{r['alpha_embed']:>8.4f} {r['heat']:>6.3f} {r['beta_heat']:>7.4f} "
#             f"{r['score']:>8.4f}"
#         )

#         # ── IMPORTANT : mettre à jour l'appel dans trace_retrieval.py ───────────────
#         # Ancienne signature : store.score_paper_breakdown(cand_entries, vec_scores, question)
#         # Nouvelle signature : store.score_paper_breakdown(cand_entries, vec_scores, question, qvec=qvec)
#         # → qvec est le vecteur question déjà calculé à l'étape 2 (EmbeddingModel.encode([question])[0])

#     # ─ ÉTAPE 7 : Rerank final ───────────────────────────────────────
#     out("── ÉTAPE 7 — CLASSEMENT FINAL (méthode active) ──")
#     final = store.retrieve(question)  # applique rerank ou fallback selon config
#     out(f"")
#     out(f"  {len(final)} entrées retenues (fine_k={fine_k}) :")
#     for rank, e in enumerate(final, 1):
#         out(f"    {rank:2}. {_kind(e)}")
#     out("")

#     # ─ SYNTHÈSE tables/colonnes ─────────────────────────────────────
#     out("── SYNTHÈSE : tables & colonnes comprises au final ──")
#     tables = sorted({e.name for e in final if e.label == "Table"})
#     cols = sorted({e.name for e in final if e.label == "Column"})
#     out(f"  Tables  : {tables or '(aucune)'}")
#     out(f"  Colonnes: {cols or '(aucune)'}")

#     path = out_dir / f"trace_{_slug(question)}.txt"
#     path.write_text("\n".join(L), encoding="utf-8")
#     print(f"\n✓ Trace exportée : {path}")
#     return path


# if __name__ == "__main__":
#     ap = argparse.ArgumentParser()
#     ap.add_argument("question")
#     ap.add_argument("--out", default="exports")
#     args = ap.parse_args()
#     trace(args.question, Path(args.out))




# scripts/trace_retrieval.py
"""
OUTIL DE VÉRIFICATION #4 — Traçage COMPLET du retrieval, étape par étape.
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

from config import settings
from core.db import DatabaseConnector
from knowledge.store import KnowledgeStore, EmbeddingModel, Reranker, _tokenize
from knowledge.builder import build_full_kb, KBEntry


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "question"


def _kind(entry: KBEntry) -> str:
    if entry.label == "Column":
        return f"colonne {entry.name} ({entry.payload.get('type','?')})"
    if entry.label == "Table":
        return f"TABLE {entry.name}"
    return f"{entry.label} {entry.name}"


def trace(question: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    store.warmup()

    coarse_k = settings.COARSE_TOP_K
    fine_k = settings.FINE_TOP_K

    L: list[str] = []
    def out(s=""): L.append(s); print(s)

    out("=" * 72)
    out("TRAÇAGE DU RETRIEVAL — comment le programme extrait les données")
    out("=" * 72)
    out(f"Question : {question}")
    out(f"Paramètres : coarse_k={coarse_k} (candidats/méthode), fine_k={fine_k} (gardés)")
    out(f"Reranker actif : {settings.USE_RERANKER}")
    out(f"Méthode de scoring active : {settings.SCORING_METHOD}")
    out(f"  (ALPHA={settings.ALPHA}, BETA={settings.BETA} pour la méthode 'paper')")
    out("")

    # ÉTAPE 1
    tokens = _tokenize(question)
    out("── ÉTAPE 1 — MOTS-CLÉS EXTRAITS (tokenisation) ──")
    out(f"  {tokens}")
    out(f"  ({len(tokens)} tokens : minuscules, '.' et '|' remplacés par espaces)")
    out("")

    # ÉTAPE 2
    out("── ÉTAPE 2 — RECHERCHE VECTORIELLE (similarité sémantique bge-m3) ──")
    qvec = EmbeddingModel.encode([question])[0]
    out(f"  Vecteur question : dimension {len(qvec)} (bge-m3)")
    vec_hits = store.client.query_points(store.collection, query=qvec, limit=coarse_k).points
    out(f"  Top {min(coarse_k, len(vec_hits))} par similarité sémantique :")
    vec_names = []
    vec_scores: dict[str, float] = {}
    for rank, h in enumerate(vec_hits, 1):
        name = h.payload["name"]
        vec_names.append(name)
        score = getattr(h, "score", None)
        if isinstance(score, (int, float)):
            vec_scores[name] = float(score)
        score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "?"
        lbl = h.payload.get("label", "?")
        out(f"    {rank:2}. [{lbl:7}] {name}   (score={score_str})")
    out("")

    # ÉTAPE 3
    out("── ÉTAPE 3 — RECHERCHE LEXICALE (BM25, correspondance de mots) ──")
    scores = store._bm25.get_scores(tokens)
    scores_normalisé = store._norm(scores)
    top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:coarse_k]
    bm25_names = []
    out(f"  Top {coarse_k} par score BM25 :")
    for rank, i in enumerate(top_idx, 1):
        e = store.entries[i]
        bm25_names.append(e.name)
        out(f"    {rank:2}. [{e.label:7}] {e.name}   (bm25={scores[i]:.3f}) score_norm={scores_normalisé[i]:.3f}")
    out("")

    # ÉTAPE 4
    out("── ÉTAPE 4 — FUSION (union vectoriel + lexical) ──")
    fused = set(vec_names) | set(bm25_names)
    only_vec = set(vec_names) - set(bm25_names)
    only_bm = set(bm25_names) - set(vec_names)
    both = set(vec_names) & set(bm25_names)
    out(f"  {len(fused)} candidats uniques après fusion")
    out(f"  - présents dans LES DEUX méthodes : {sorted(both)}")
    out(f"  - vectoriel seulement : {sorted(only_vec)}")
    out(f"  - BM25 seulement      : {sorted(only_bm)}")
    out("")

    # ÉTAPE 5
    out("── ÉTAPE 5 — MULTI-HOP (traversée des ancêtres via anc_name) ──")
    expanded = store._expand_ancestors(fused)
    added = expanded - fused
    out(f"  {len(expanded)} candidats après multi-hop (+{len(added)} ancêtres)")
    if added:
        for n in sorted(added):
            e = store._by_name.get(n)
            anc = f" ⟵ {e.anc_label} {e.anc_name}" if e and e.anc_name else ""
            out(f"    + {n}{anc}")
    else:
        out("    (aucun ancêtre supplémentaire)")
    out("")

    # ÉTAPE 6
    out("── ÉTAPE 6 — SCORE PAPIER : Score(t) = Sim(t) + α·Embed(t) + β·Heat(t) ──")
    out("  (calculé pour TOUS les candidats fusionnés, indépendamment de la")
    out("   méthode active — sert à régler ALPHA/BETA dans config.py)")
    out("")
    cand_entries = [store._by_name[n] for n in expanded if n in store._by_name]
    if not cand_entries:
        out("  (aucun candidat)")
    else:
        rows = store.score_paper_breakdown(cand_entries, vec_scores, question)
        header = f"  {'rg':>3}  {'entrée':<32} {'Sim(t)':>8} {'Embed':>7} {'α·Embed':>8} {'Heat':>6} {'β·Heat':>7} {'Score':>8}"
        out(header)
        out("  " + "-" * (len(header) - 2))
        for rank, r in enumerate(rows, 1):
            e = r["entry"]
            out(f"  {rank:>3}  {e.name:<32.32} {r['sim']:>8.4f} {r['embed']:>7.3f} "
                f"{r['alpha_embed']:>8.4f} {r['heat']:>6.3f} {r['beta_heat']:>7.4f} {r['score']:>8.4f}")
        out("")
        out("  Lecture pour régler les hyperparamètres :")
        out("  - Sim(t) domine et écrase les autres termes -> baisser α et/ou β,")
        out("    ou vérifier que payload['columns'] est bien peuplé (sinon Sim(t)")
        out("    retombe sur un repli à 1 seul champ, moins discriminant).")
        out("  - Un candidat pertinent classé bas malgré un bon Embed(t) -> monter α.")
        out("  - Heat(t) fait remonter des tables populaires mais hors-sujet -> baisser β.")
    out("")

    # ÉTAPE 7
    out("── ÉTAPE 7 — CLASSEMENT FINAL (méthode active) ──")
    final = store.retrieve(question)
    out("")
    out(f"  {len(final)} entrées retenues (fine_k={fine_k}) :")
    for rank, e in enumerate(final, 1):
        out(f"    {rank:2}. {_kind(e)}")
    out("")

    # SYNTHÈSE
    out("── SYNTHÈSE : tables & colonnes comprises au final ──")
    tables = sorted({e.name for e in final if e.label == "Table"})
    cols = sorted({e.name for e in final if e.label == "Column"})
    out(f"  Tables  : {tables or '(aucune)'}")
    out(f"  Colonnes: {cols or '(aucune)'}")

    path = out_dir / f"trace_{_slug(question)}.txt"
    path.write_text("\n".join(L), encoding="utf-8")
    print(f"\n✓ Trace exportée : {path}")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--out", default="exports")
    args = ap.parse_args()
    trace(args.question, Path(args.out))