"""
Test du pipeline complet (étape 6) avec un LLM STUBBÉ déterministe.

Objectif : valider l'orchestration MRD-Q -> SIR -> SQL -> exécution SANS Ollama.
Le stub renvoie des réponses JSON/SQL plausibles selon la question.

Inclut aussi un mini harnais UEX (Useful Execution Accuracy) : une question
est "utile" si le résultat exécuté correspond (intention) au résultat de
la requête de référence.
"""
import sys, types

# ── Réutilise les stubs embeddings/Qdrant de test_logic ─────────────
import tests.test_logic  # noqa: F401  (installe les stubs FlagEmbedding/qdrant)

from core.db import DatabaseConnector
from core.llm import LLMClient
from core.mrdq import DialogueState
from core.pipeline import BIPipeline, Outcome
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb


# ── LLM stub déterministe ───────────────────────────────────────────
class StubLLM(LLMClient):
    """Renvoie des réponses scriptées selon le rôle système détecté."""
    def __init__(self): pass  # pas de connexion

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        s = system.lower()
        if "analyste bi" in s:           # MRD-Q
            return self._analyze(user)
        if "analyse sémantique" in s:    # SIR
            return self._sir(user)
        if "expert sql" in s:            # SQL gen
            return self._sql(user)
        return "{}"

    def _analyze(self, user: str) -> str:
        q = user.lower()
        if "météo" in q or "blague" in q:
            return '{"intent":0,"completed_query":"","clarification":null,"reason":"hors BI"}'
        if "et los angeles" in q or "et la france" in q:
            # incomplet : pas de métrique explicite
            return ('{"intent":1,"completed_query":"",'
                    '"clarification":"Quelle mesure souhaitez-vous (CA, nombre de ventes) ?",'
                    '"reason":"métrique manquante"}')
        # complet
        return ('{"intent":2,"completed_query":"Chiffre d\'affaires total par pays",'
                '"clarification":null,"reason":"métrique=CA, dimension=pays"}')

    def _sir(self, user: str) -> str:
        return ('{"Key Components":{"Metric":"SUM(Invoice.Total)",'
                '"Dimensions":["BillingCountry"],"Time Range":null,"Filters":[]},'
                '"Knowledge Mapping":["CA -> SUM(Invoice.Total)"],'
                '"Query Understanding":"Revenu total par pays",'
                '"Rewritten Query":"Somme de Invoice.Total groupée par BillingCountry"}')

    def _sql(self, user: str) -> str:
        return ("```sql\nSELECT BillingCountry, SUM(Total) AS revenu "
                "FROM Invoice GROUP BY BillingCountry ORDER BY revenu DESC\n```")


def make_pipeline():
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    return BIPipeline(db, store, llm=StubLLM())


def test_decline():
    p = make_pipeline()
    r = p.ask("Raconte-moi une blague")
    assert r.outcome == Outcome.DECLINED, r.outcome
    print(f"  ✓ Hors-BI -> {r.outcome.value}")


def test_clarify():
    p = make_pipeline()
    state = DialogueState()
    state.add("Quel est le chiffre d'affaires par pays ?")
    r = p.ask("Et Los Angeles ?", state)
    assert r.outcome == Outcome.NEEDS_CLARIFICATION, r.outcome
    print(f"  ✓ Incomplet -> clarification : « {r.message} »")


def test_success():
    p = make_pipeline()
    r = p.ask("Quel est le chiffre d'affaires par pays ?")
    assert r.outcome == Outcome.SUCCESS, (r.outcome, r.message)
    assert r.data is not None and len(r.data) > 0
    print(f"  ✓ Succès -> SQL exécuté, {len(r.data)} lignes")
    print(f"      SQL : {r.sql}")
    print(f"      Top : {r.data.iloc[0].to_dict()}")


# ── Harnais UEX ─────────────────────────────────────────────────────
def compute_uex():
    """
    UEX : proportion de questions dont le résultat exécuté correspond
    (en intention) à la requête de référence (golden SQL).
    Comparaison robuste : mêmes lignes après tri, indépendamment de l'ordre.
    """
    p = make_pipeline()
    cases = [
        ("Quel est le chiffre d'affaires par pays ?",
         "SELECT BillingCountry, SUM(Total) AS revenu FROM Invoice GROUP BY BillingCountry"),
    ]
    ok = 0
    for question, golden_sql in cases:
        r = p.ask(question)
        if r.outcome != Outcome.SUCCESS:
            print(f"      ✗ {question} -> {r.outcome.value}")
            continue
        gold = p.db.run(golden_sql).sort_index(axis=1)
        got = r.data.sort_index(axis=1)
        # compare l'ensemble des valeurs (intention), pas l'ordre des lignes
        gold_set = set(map(tuple, gold.round(2).values.tolist()))
        got_set = set(map(tuple, got.round(2).values.tolist()))
        if gold_set == got_set:
            ok += 1
            print(f"      ✓ {question}")
        else:
            print(f"      ✗ {question} (résultats différents)")
    uex = ok / len(cases) if cases else 0.0
    print(f"  UEX = {uex:.0%} ({ok}/{len(cases)})")
    return uex


if __name__ == "__main__":
    print("Test déclin (hors BI) :");        test_decline()
    print("Test clarification :");           test_clarify()
    print("Test succès (bout en bout) :");   test_success()
    print("Harnais UEX :");                  compute_uex()
    print("\nPipeline E5-6 validé ✓")
