"""
Test de régression : PAS de boucle de clarification infinie.

Reproduit le bug "le modèle devient fou" :
  - une question complète doit aboutir directement (intent=2), sans clarifier ;
  - même si le LLM insiste pour clarifier en boucle, le garde-fou anti-boucle
    force l'aboutissement après une seule clarification.
"""
import tests.test_logic  # stubs

from config import settings
settings.FUSED_ANALYSIS = True  # les stubs répondent au prompt fusionné

from core.db import DatabaseConnector
from core.llm import LLMClient
from core.mrdq import DialogueState
from core.pipeline import BIPipeline, Outcome
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb


def _sql_reply():
    return ("```sql\nSELECT ar.Name, SUM(il.UnitPrice*il.Quantity) AS CA "
            "FROM InvoiceLine il JOIN Track t ON t.TrackId=il.TrackId "
            "JOIN Album al ON al.AlbumId=t.AlbumId "
            "JOIN Artist ar ON ar.ArtistId=al.ArtistId "
            "JOIN Invoice i ON i.InvoiceId=il.InvoiceId "
            "WHERE strftime('%Y',i.InvoiceDate)='2022' "
            "GROUP BY ar.Name ORDER BY CA DESC LIMIT 5\n```")


class WellBehavedLLM(LLMClient):
    """LLM qui classe correctement une question complète en intent=2."""
    def __init__(self): pass
    def chat(self, system, user, temperature=0.0):
        s = system.lower()
        if "analyste bi expert" in s:
            return ('{"intent":2,"completed_query":"Top 5 artistes CA 2022",'
                    '"clarification":null,'
                    '"rewritten_query":"Top 5 artistes par CA en 2022","reason":"complète"}')
        if "expert sql" in s:
            return _sql_reply()
        return "{}"


class StubbornLLM(LLMClient):
    """LLM 'fou' qui veut TOUJOURS clarifier (reproduit le bug)."""
    def __init__(self): pass
    def chat(self, system, user, temperature=0.0):
        s = system.lower()
        if "analyste bi expert" in s:
            return ('{"intent":1,"completed_query":"Top 5 artistes CA 2022",'
                    '"clarification":"par pays ou par genre ?",'
                    '"rewritten_query":null,"reason":"veut clarifier encore"}')
        if "expert sql" in s:
            return _sql_reply()
        return "{}"


def make(llm):
    db = DatabaseConnector(); store = KnowledgeStore(); store.index(build_full_kb(db))
    return BIPipeline(db, store, llm=llm)


def test_complete_question_no_clarification():
    p = make(WellBehavedLLM())
    r = p.ask("Quels sont les 5 artistes avec le plus de CA en 2022 ?")
    print(f"  Question complète -> {r.outcome.value}")
    assert r.outcome == Outcome.SUCCESS, "une question complète ne doit PAS clarifier"
    print(f"      ✓ aboutit directement, {len(r.data)} lignes")


def test_no_infinite_loop():
    p = make(StubbornLLM())
    state = DialogueState()
    # Tour 1 : le LLM 'fou' demande une clarification -> autorisé UNE fois
    r1 = p.ask("Quels sont les 5 artistes avec le plus de CA en 2022 ?", state)
    print(f"  Tour 1 (LLM têtu) -> {r1.outcome.value}")
    assert r1.outcome == Outcome.NEEDS_CLARIFICATION
    # Tour 2 : même si le LLM reveut clarifier, le garde-fou force l'aboutissement
    r2 = p.ask("par CA !", state)
    print(f"  Tour 2 -> {r2.outcome.value} (garde-fou anti-boucle)")
    assert r2.outcome == Outcome.SUCCESS, "le garde-fou aurait dû forcer l'aboutissement"
    print(f"      ✓ pas de boucle : aboutit au 2e tour, {len(r2.data)} lignes")


if __name__ == "__main__":
    print("Test question complète (pas de clarification parasite) :")
    test_complete_question_no_clarification()
    print("Test anti-boucle (LLM qui veut clarifier sans fin) :")
    test_no_infinite_loop()
    print("\n✓ Boucle de clarification éliminée")
