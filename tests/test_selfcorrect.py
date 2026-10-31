"""
Test de la boucle de self-correction SQL.

Reproduit l'erreur réelle :
  1er essai -> table inexistante 'InvoiceLineItem' + fonction YEAR() (hors sqlite)
  2e essai  -> SQL corrigé (InvoiceLine + strftime) qui s'exécute.
"""
import tests.test_logic  # stubs FlagEmbedding/qdrant

from core.db import DatabaseConnector
from core.llm import LLMClient
from core.pipeline import BIPipeline, Outcome
from knowledge.store import KnowledgeStore
from knowledge.builder import build_full_kb


class StubLLM(LLMClient):
    def __init__(self): self.sql_calls = 0

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        s = system.lower()
        if "analyste bi" in s:
            return ('{"intent":2,"completed_query":"Top 5 artistes par CA en 2023",'
                    '"clarification":null,"reason":"ok"}')
        if "analyse sémantique" in s:
            return ('{"Key Components":{"Metric":"SUM(Invoice.Total)",'
                    '"Dimensions":["Artist.Name"],"Time Range":"2023","Filters":[]},'
                    '"Knowledge Mapping":[],"Query Understanding":"top 5 artistes CA 2023",'
                    '"Rewritten Query":"Top 5 des artistes par CA en 2023"}')
        if "expert sql" in s:
            # 1er appel = requête fautive ; appel de correction = requête valide
            is_fix = "a ÉCHOUÉ" in user or "Corrige" in user
            self.sql_calls += 1
            if not is_fix:
                return ("```sql\nSELECT Artist.Name, SUM(Invoice.Total) AS TotalSales "
                        "FROM Artist JOIN Album ON Artist.ArtistId=Album.ArtistId "
                        "JOIN Track ON Album.AlbumId=Track.AlbumId "
                        "JOIN InvoiceLineItem ON Track.TrackId=InvoiceLineItem.TrackId "
                        "JOIN Invoice ON InvoiceLineItem.InvoiceId=Invoice.InvoiceId "
                        "WHERE YEAR(Invoice.InvoiceDate)=2023 "
                        "GROUP BY Artist.Name ORDER BY TotalSales DESC LIMIT 5\n```")
            # Version corrigée : InvoiceLine + strftime
            return ("```sql\nSELECT Artist.Name, SUM(InvoiceLine.UnitPrice*InvoiceLine.Quantity) "
                    "AS TotalSales FROM Artist JOIN Album ON Artist.ArtistId=Album.ArtistId "
                    "JOIN Track ON Album.AlbumId=Track.AlbumId "
                    "JOIN InvoiceLine ON Track.TrackId=InvoiceLine.TrackId "
                    "JOIN Invoice ON InvoiceLine.InvoiceId=Invoice.InvoiceId "
                    "WHERE strftime('%Y',Invoice.InvoiceDate)='2023' "
                    "GROUP BY Artist.Name ORDER BY TotalSales DESC LIMIT 5\n```")
        return "{}"


def test_self_correction():
    db = DatabaseConnector()
    print(f"  Dialecte détecté : {db.dialect}")
    store = KnowledgeStore(); store.index(build_full_kb(db))
    llm = StubLLM()
    p = BIPipeline(db, store, llm=llm)

    r = p.ask("Quels sont les 5 artistes qui ont généré le plus de CA en 2023 ?")
    print(f"  Résultat : {r.outcome.value} — {r.message}")
    print(f"  Appels SQL au LLM : {llm.sql_calls} (1 fautif + 1 correction)")
    assert r.outcome == Outcome.SUCCESS, f"attendu SUCCESS, obtenu {r.outcome.value}"
    assert llm.sql_calls == 2, "la self-correction aurait dû déclencher 1 réessai"
    print(f"  SQL final : {r.sql.strip()[:80]}...")
    print(f"  Lignes : {len(r.data)} ; aperçu : {r.data.head(2).to_dict('records')}")


if __name__ == "__main__":
    print("Test self-correction SQL (InvoiceLineItem + YEAR -> corrigé) :")
    test_self_correction()
    print("\n✓ La requête fautive est corrigée automatiquement et s'exécute")
