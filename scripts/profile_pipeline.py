"""
Profilage du pipeline avec le VRAI LLM (Ollama) — à lancer sur votre machine.

Mesure le temps de chaque étape pour localiser les goulots :
  - chargement des modèles (embeddings, première requête Ollama)
  - chaque appel LLM (MRD-Q, SIR, SQL)
  - retrieval, exécution SQL

Lancement :  python -m scripts.profile_pipeline
Prérequis :  Qdrant lancé, Ollama lancé + modèle pull.
"""
import time
from contextlib import contextmanager

from core.db import DatabaseConnector
from core.llm import LLMClient
from core.mrdq import MRDQ, DialogueState
from core.sir import SQLGenerator
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb

TIMINGS: dict[str, float] = {}


@contextmanager
def timer(label: str):
    t = time.perf_counter()
    yield
    dt = time.perf_counter() - t
    TIMINGS[label] = dt
    print(f"  {label:42} {dt*1000:9.1f} ms")


# Wrapper qui chronomètre chaque appel LLM individuellement
class TimedLLM(LLMClient):
    def chat(self, system, user, temperature=0.0):
        s = system.lower()
        role = ("LLM:MRD-Q" if "analyste bi" in s
                else "LLM:SIR" if "analyse sémantique" in s
                else "LLM:SQL" if "expert sql" in s else "LLM:?")
        t = time.perf_counter()
        out = super().chat(system, user, temperature)
        dt = time.perf_counter() - t
        TIMINGS[role] = TIMINGS.get(role, 0) + dt
        print(f"  {role:42} {dt*1000:9.1f} ms  ({len(out)} car.)")
        return out


def main():
    question = "Quels sont les 5 artistes qui ont généré le plus de chiffre d'affaires en 2022 ?"
    print(f"Question : {question}\n")

    print("── Initialisation (coût unique au démarrage) ──")
    with timer("Connexion DB"):
        db = DatabaseConnector()
    with timer("Build KB (entrées)"):
        kb = build_full_kb(db)
    with timer("Index KB (embeddings + Qdrant)  [LOURD]"):
        store = KnowledgeStore(); store.index(kb)
    llm = TimedLLM()
    mrdq = MRDQ(llm); sqlgen = SQLGenerator(llm)

    print("\n── Échauffement LLM (1er appel = chargement du modèle en RAM/VRAM) ──")
    with timer("Warm-up Ollama (1er appel à vide)"):
        llm.chat("Tu es un assistant.", "Réponds OK.")

    print("\n── Pipeline pour 1 question (régime établi) ──")
    state = DialogueState()
    with timer("Retrieval #1 (pour contexte domaine)"):
        retrieved = store.retrieve(question)
    with timer("TOTAL MRD-Q (analyse)"):
        analysis = mrdq.analyze(question, state, "")
    with timer("Retrieval #2 (sur question complétée)"):
        retrieved = store.retrieve(analysis.completed_query)
    with timer("TOTAL SIR"):
        sir = sqlgen.to_sir(analysis.completed_query, retrieved)
    with timer("schema_ddl()"):
        schema = db.schema_ddl()
    with timer("TOTAL SQL gen"):
        sql = sqlgen.to_sql(sir, retrieved, dialect=db.dialect, schema=schema)
    with timer("Exécution SQL"):
        data = db.run(sql)

    print("\n── Synthèse ──")
    llm_total = sum(v for k, v in TIMINGS.items() if k.startswith("LLM:"))
    non_llm = sum(v for k, v in TIMINGS.items()
                  if not k.startswith("LLM:") and k not in
                  ("Index KB (embeddings + Qdrant)  [LOURD]", "Warm-up Ollama (1er appel à vide)"))
    print(f"  Somme appels LLM (hors warm-up) : {llm_total*1000:9.1f} ms")
    print(f"  Somme étapes non-LLM            : {non_llm*1000:9.1f} ms")
    print(f"  -> Le LLM représente {100*llm_total/(llm_total+non_llm):.0f}% du temps de réponse")
    print(f"\n  Lignes retournées : {len(data)}")


if __name__ == "__main__":
    main()
