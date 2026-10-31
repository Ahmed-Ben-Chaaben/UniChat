"""
Test de régression : la réponse à une clarification doit être fusionnée
avec la question en attente, et NON traitée hors contexte.

Reproduit le bug signalé :
  "Quels sont les 10 meilleurs artistes ?" -> clarification
  "nombre de chansons"                     -> doit ABOUTIR (pas décliner)
"""
import tests.test_logic  # installe les stubs FlagEmbedding/qdrant

from core.db import DatabaseConnector
from core.llm import LLMClient
from core.mrdq import DialogueState
from core.pipeline import BIPipeline, Outcome
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb


class StubLLM(LLMClient):
    """LLM stub reproduisant le comportement réel observé."""
    def __init__(self): pass

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        s = system.lower()
        if "analyste bi" in s:
            return self._analyze(user)
        if "analyse sémantique" in s:
            return self._sir(user)
        if "expert sql" in s:
            return self._sql(user)
        return "{}"

    def _analyze(self, user: str) -> str:
        u = user.lower()
        # Cas 1 : "10 meilleurs artistes" seul -> métrique ambiguë -> clarifier
        if "meilleurs artistes" in u and "précision apportée" not in u:
            return ('{"intent":1,'
                    '"completed_query":"Quels sont les 10 meilleurs artistes ?",'
                    '"clarification":"Quelle métrique pour \'meilleurs\' '
                    '(nombre de chansons, revenus) ?",'
                    '"reason":"métrique ambiguë"}')
        # Cas 2 : la question fusionnée contient la précision -> complète
        if "précision apportée" in u and "chansons" in u:
            return ('{"intent":2,'
                    '"completed_query":"Top 10 des artistes par nombre de chansons",'
                    '"clarification":null,"reason":"métrique=nombre de chansons"}')
        # AVANT LE FIX : "nombre de chansons" seul aurait été vu comme hors-BI.
        if u.strip() in ("nombre de chansons", "nombre de chansons."):
            return ('{"intent":0,"completed_query":"","clarification":null,'
                    '"reason":"phrase hors contexte"}')
        return ('{"intent":2,"completed_query":"' + user + '",'
                '"clarification":null,"reason":"ok"}')

    def _sir(self, user: str) -> str:
        return ('{"Key Components":{"Metric":"COUNT(Track.TrackId)",'
                '"Dimensions":["Artist.Name"],"Time Range":null,"Filters":[]},'
                '"Knowledge Mapping":["meilleurs -> COUNT des chansons"],'
                '"Query Understanding":"Top 10 artistes par nb de chansons",'
                '"Rewritten Query":"Top 10 des artistes par nombre de chansons"}')

    def _sql(self, user: str) -> str:
        return ("```sql\n"
                "SELECT ar.Name, COUNT(t.TrackId) AS nb_chansons "
                "FROM Artist ar "
                "JOIN Album al ON al.ArtistId = ar.ArtistId "
                "JOIN Track t ON t.AlbumId = al.AlbumId "
                "GROUP BY ar.Name ORDER BY nb_chansons DESC LIMIT 10\n```")


def make_pipeline():
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    return BIPipeline(db, store, llm=StubLLM())


def test_clarification_followup():
    p = make_pipeline()
    state = DialogueState()

    # Tour 1
    r1 = p.ask("Quels sont les 10 meilleurs artistes ?", state)
    print(f"  Tour 1 -> {r1.outcome.value} : {r1.message}")
    assert r1.outcome == Outcome.NEEDS_CLARIFICATION
    assert state.pending_question is not None, "la question doit être mise en attente"

    # Tour 2 : la réponse seule, comme tapée par l'utilisateur
    r2 = p.ask("nombre de chansons", state)
    print(f"  Tour 2 -> {r2.outcome.value} : {r2.message}")
    assert r2.outcome == Outcome.SUCCESS, f"attendu SUCCESS, obtenu {r2.outcome.value}"
    assert r2.data is not None and len(r2.data) > 0
    print(f"      SQL : {r2.sql.strip()}")
    print(f"      Top : {r2.data.iloc[0].to_dict()}")
    assert state.pending_question is None, "l'attente doit être vidée après succès"


if __name__ == "__main__":
    print("Test régression clarification multi-tours :")
    test_clarification_followup()
    print("\n✓ Bug corrigé : la réponse à la clarification est bien fusionnée")
