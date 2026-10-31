"""
Test du mode fusionné (FUSED_ANALYSIS) : MRD-Q + SIR en un seul appel LLM.

Vérifie :
  - le pipeline aboutit (SUCCESS) en mode fusionné ;
  - il ne fait que 2 appels LLM (analyse fusionnée + SQL) au lieu de 3.
"""
import tests.test_logic  # stubs FlagEmbedding/qdrant

from config import settings
settings.FUSED_ANALYSIS = True  # ce test valide spécifiquement le mode fusionné

from core.db import DatabaseConnector
from core.llm import LLMClient
from core.pipeline import BIPipeline, Outcome
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb
from config import settings


class CountingLLM(LLMClient):
    def __init__(self): self.roles = []

    def chat(self, system, user, temperature=0.0):
        s = system.lower()
        # analyse fusionnée : produit intent + rewritten_query d'un coup
        if "analyste bi expert" in s:
            self.roles.append("FUSED")
            return ('{"intent":2,'
                    '"completed_query":"Top 5 artistes par CA en 2022",'
                    '"clarification":null,'
                    '"rewritten_query":"Somme de InvoiceLine.UnitPrice*Quantity par '
                    'artiste, filtré sur 2022, top 5",'
                    '"reason":"metrique=CA dimension=artiste"}')
        if "expert sql" in s:
            self.roles.append("SQL")
            return ("```sql\nSELECT ar.Name, SUM(il.UnitPrice*il.Quantity) AS CA "
                    "FROM InvoiceLine il JOIN Track t ON t.TrackId=il.TrackId "
                    "JOIN Album al ON al.AlbumId=t.AlbumId "
                    "JOIN Artist ar ON ar.ArtistId=al.ArtistId "
                    "JOIN Invoice i ON i.InvoiceId=il.InvoiceId "
                    "WHERE strftime('%Y',i.InvoiceDate)='2022' "
                    "GROUP BY ar.Name ORDER BY CA DESC LIMIT 5\n```")
        # Si le SIR séparé était appelé, on le verrait ici :
        if "analyse sémantique" in s:
            self.roles.append("SIR")
            return '{"Rewritten Query":"x"}'
        return "{}"


def test_fused_saves_one_call():
    assert settings.FUSED_ANALYSIS, "FUSED_ANALYSIS doit être actif (=1) pour ce test"
    db = DatabaseConnector()
    store = KnowledgeStore(); store.index(build_full_kb(db))
    llm = CountingLLM()
    p = BIPipeline(db, store, llm=llm)

    r = p.ask("Quels sont les 5 artistes avec le plus de CA en 2022 ?")
    print(f"  Outcome : {r.outcome.value} — {r.message}")
    print(f"  Appels LLM : {llm.roles}")
    assert r.outcome == Outcome.SUCCESS, r.message
    assert "SIR" not in llm.roles, "l'appel SIR séparé aurait dû être sauté"
    assert llm.roles == ["FUSED", "SQL"], f"attendu 2 appels, obtenu {llm.roles}"
    print(f"  ✓ 2 appels LLM au lieu de 3 (SIR fusionné dans l'analyse)")
    print(f"  Top : {r.data.iloc[0].to_dict()}")


if __name__ == "__main__":
    print("Test fusion MRD-Q + SIR :")
    test_fused_saves_one_call()
    print("\n✓ Fusion validée : 1 appel LLM économisé par question (-33%)")
