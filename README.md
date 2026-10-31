# BI Conversationnelle — Prototype (test Chinook)

Assistant NL→SQL local et open-source, adapté de l'architecture **SiriusBI**
(arXiv 2411.06102v3). Étapes 1 à 4 du plan : infra, base métier, KB, retrieval.

## Architecture découplée

| Monde | Rôle | Techno |
|-------|------|--------|
| Base **métier** | données à interroger | n'importe quel SGBD via **SQLAlchemy** (Chinook en test → entrepôt Unimed en prod) |
| Base **KB / vectorielle** | dictionnaire sémantique + embeddings | **Qdrant** |

Le LLM **n'écrit jamais** en base : il lit la KB (Qdrant) pour comprendre le
schéma, génère un SQL exécuté en **lecture seule** sur la base métier.

## Mise en route

```bash
# 1. Dépendances
pip install -r requirements.txt

# 2. Base de test Chinook (SQLite)
bash scripts/01_download_chinook.sh

# 3. Qdrant (Docker)
bash scripts/02_start_qdrant.sh

# 4. LLM local (Ollama installé au préalable)
bash scripts/03_pull_llm.sh

# 5. Vérifier le socle (sans LLM ni gros modèles)
python -m tests.test_logic
```

## Passage de Chinook à l'entrepôt Unimed

Une seule variable change — `DB_URL` dans `config/settings.py` (ou en .env) :

```bash
export DB_URL="postgresql+psycopg2://user:pwd@host:5432/dwh"   # exemple
```

…et installer le driver correspondant (cf. requirements.txt).

## Structure

```
config/settings.py    # config centralisée (DB_URL, Qdrant, modèles)
core/db.py            # connecteur métier agnostique + garde-fous SQL
knowledge/builder.py  # schéma -> entrées KB (Anc.Name/Anc.Label)
knowledge/store.py    # indexation Qdrant+BM25, retrieval 2 phases + multi-hop
tests/test_logic.py   # tests logique (garde-fous, multi-hop, retrieval)
scripts/              # download Chinook, start Qdrant, pull LLM
```

## Prochaines étapes (5-6)

- E5 : pipeline MRD-Q + SIR + SQL Generation (branchement Ollama)
- E6 : interface Streamlit/Plotly + évaluation UEX sur questions types

---

## Étapes 5-6 (pipeline LLM + interface) — IMPLÉMENTÉES

### Pipeline two-step (core/)
- `core/llm.py`     : client Ollama + parsing JSON/SQL robuste
- `core/mrdq.py`    : MRD-Q (dialogue analysis + intention querying I(Q)∈{0,1,2})
- `core/sir.py`     : SIR (réécriture CoT) + SQL Generation
- `core/pipeline.py`: orchestrateur question -> résultat

Flux : question -> MRD-Q -> retrieval -> SIR -> SQL -> validation lecture seule -> exécution.

### Interface
```bash
streamlit run app.py
```
Chat multi-tours, SQL généré affiché (transparence), tableau + graphe Plotly auto.

### Évaluation (UEX)
```bash
python -m tests.test_pipeline   # logique validée avec un LLM stubbé (sans Ollama)
```
Pour l'évaluation réelle : ajouter vos paires (question, golden SQL) dans
`compute_uex()` de `tests/test_pipeline.py` et lancer avec Ollama actif.

### Checklist avant de lancer avec le vrai LLM
1. `bash scripts/02_start_qdrant.sh`  (Qdrant sur :6333)
2. `bash scripts/03_pull_llm.sh`      (modèle Ollama)
3. `python -m knowledge.store`        (vérifie le vrai retrieval bge-m3)
4. `streamlit run app.py`
# UniChat
