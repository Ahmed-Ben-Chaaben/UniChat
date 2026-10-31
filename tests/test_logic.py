"""
Tests de la LOGIQUE PURE (sans LLM ni gros modèles) :
  - garde-fous SQL (lecture seule, LIMIT auto)
  - traversée multi-hop sur anc_name
  - construction KB hiérarchique

On stub les modèles lourds pour valider la logique sans télécharger les poids.
"""
import sys, types

# ── Stubs des dépendances lourdes (avant import de store) ───────────
# Stub FlagEmbedding
flag = types.ModuleType("FlagEmbedding")
class _FakeBGE:
    def __init__(self, *a, **k): pass
    def encode(self, texts, **k):
        # vecteur factice déterministe (longueur 1024)
        return {"dense_vecs": [[float(len(t) % 7)] * 1024 for t in texts]}
class _FakeReranker:
    def __init__(self, *a, **k): pass
    def compute_score(self, pairs):
        # score = chevauchement de mots query/doc
        return [len(set(q.lower().split()) & set(d.lower().split())) for q, d in pairs]
flag.BGEM3FlagModel = _FakeBGE
flag.FlagReranker = _FakeReranker
sys.modules["FlagEmbedding"] = flag

# Stub qdrant_client minimal
qd = types.ModuleType("qdrant_client")
qmodels = types.ModuleType("qdrant_client.models")
class _Hit:
    def __init__(self, payload): self.payload = payload
class _FakeQdrant:
    def __init__(self, *a, **k): self._pts = []
    def collection_exists(self, c): return False
    def delete_collection(self, c): pass
    def create_collection(self, c, **k): pass
    def upsert(self, c, points): self._pts = points
    def search(self, c, query_vector, limit):
        return [_Hit(p.payload) for p in self._pts[:limit]]
class _PointStruct:
    def __init__(self, id, vector, payload): self.id=id; self.vector=vector; self.payload=payload
qd.QdrantClient = _FakeQdrant
qmodels.Distance = types.SimpleNamespace(COSINE="cosine")
qmodels.VectorParams = lambda **k: None
qmodels.PointStruct = _PointStruct
sys.modules["qdrant_client"] = qd
sys.modules["qdrant_client.models"] = qmodels

# ── Tests ───────────────────────────────────────────────────────────
from core.db import DatabaseConnector, UnsafeQueryError
from knowledge.builder import build_full_kb
from knowledge.store import KnowledgeStore


def test_guardrails():
    db = DatabaseConnector()
    # SELECT valide -> LIMIT auto ajouté
    df = db.run("SELECT Name FROM Artist")
    assert len(df) <= 1000
    print(f"  ✓ SELECT exécuté, LIMIT auto ({len(df)} lignes)")

    # Modifications interdites
    for bad in ["DROP TABLE Artist", "DELETE FROM Invoice", "UPDATE Track SET Name='x'",
                "INSERT INTO Genre VALUES (99,'x')"]:
        try:
            db.run(bad); assert False, f"aurait dû refuser : {bad}"
        except UnsafeQueryError:
            pass
    print("  ✓ INSERT/UPDATE/DELETE/DROP correctement refusés")

    # Requêtes multiples interdites
    try:
        db.run("SELECT 1; SELECT 2"); assert False
    except UnsafeQueryError:
        print("  ✓ Requêtes multiples refusées")


def test_multihop():
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    # Partir d'une colonne -> doit remonter Table puis Database
    anc = store._expand_ancestors(["Invoice.Total"])
    assert "Invoice" in anc, "doit remonter à la table Invoice"
    assert "Chinook" in anc, "doit remonter à la Database"
    print(f"  ✓ Multi-hop : Invoice.Total -> {sorted(anc)}")


def test_retrieve():
    db = DatabaseConnector()
    store = KnowledgeStore()
    store.index(build_full_kb(db))
    res = store.retrieve("chiffre d'affaires par pays", coarse_k=40, fine_k=8)
    names = [e.name for e in res]
    print(f"  ✓ Retrieval renvoie {len(res)} entrées : {names[:5]}...")
    assert len(res) > 0


if __name__ == "__main__":
    print("Test garde-fous SQL :");      test_guardrails()
    print("Test multi-hop :");            test_multihop()
    print("Test retrieval (stubs) :");    test_retrieve()
    print("\nTous les tests de logique passent ✓")
