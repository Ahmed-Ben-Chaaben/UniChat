# Patch pour scripts/trace_retrieval.py — remplacer le bloc ÉTAPE 6
# Chercher le texte "ÉTAPE 6" dans votre trace_retrieval.py et remplacer
# le bloc d'affichage du tableau par ce code.

# ── ÉTAPE 6 — SCORE PAPIER (bloc à remplacer dans trace_retrieval.py) ───────

# Remplacer la ligne d'en-tête et la boucle d'affichage par :

print("\n── ÉTAPE 6 — SCORE PAPIER : Score(t) = Sim(t) + DomBoost(t) + α·Embed(t) + β·Heat(t) ──")
print("  (calculé pour TOUS les candidats fusionnés)")
print()
print(f"   {'rg':>3}  {'entrée':<34} {'Sim(t)':>7} {'DomBoost':>9} {'Embed':>7} "
      f"{'α·Embed':>8} {'Heat':>6} {'β·Heat':>7} {'Score':>8}")
print("  " + "-" * 97)

# qvec doit être disponible dans le scope (déjà calculé à l'étape 2)
rows = store.score_paper_breakdown(cand_entries, vec_scores, question, qvec=qvec)

for rank, r in enumerate(rows, 1):
    e = r["entry"]
    label_short = e.label[:4] if e.label else "    "
    name_short = e.name[:32] if len(e.name) > 32 else e.name
    print(
        f"  {rank:>3}  [{label_short:<6}] {name_short:<26} "
        f"{r['sim']:>7.4f} {r['domain_boost']:>9.4f} {r['embed']:>7.3f} "
        f"{r['alpha_embed']:>8.4f} {r['heat']:>6.3f} {r['beta_heat']:>7.4f} "
        f"{r['score']:>8.4f}"
    )

# ── IMPORTANT : mettre à jour l'appel dans trace_retrieval.py ───────────────
# Ancienne signature : store.score_paper_breakdown(cand_entries, vec_scores, question)
# Nouvelle signature : store.score_paper_breakdown(cand_entries, vec_scores, question, qvec=qvec)
# → qvec est le vecteur question déjà calculé à l'étape 2 (EmbeddingModel.encode([question])[0])
