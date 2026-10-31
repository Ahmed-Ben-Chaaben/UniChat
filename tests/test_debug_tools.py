"""
Test des outils de vérification (export schéma + inspection retrieval).
Valide qu'ils produisent bien les fichiers attendus, avec le bon contenu.
"""
import tests.test_logic  # stubs bge-m3/qdrant
from pathlib import Path
import tempfile

from scripts.export_schema import export_schema
from scripts.inspect_retrieval import inspect


def test_export_schema():
    with tempfile.TemporaryDirectory() as d:
        txt, js = export_schema(Path(d))
        assert txt.exists() and js.exists()
        content = txt.read_text(encoding="utf-8")
        # Le schéma complet doit contenir la table InvoiceLine et ses colonnes clés
        assert "InvoiceLine" in content
        assert "UnitPrice" in content and "Quantity" in content
        # Les FK doivent apparaître
        assert "FK " in content
        import json
        data = json.loads(js.read_text(encoding="utf-8"))
        assert data["table_count"] == 11, f"11 tables attendues, {data['table_count']}"
        print(f"  ✓ Export schéma : 11 tables, fichiers txt+json générés")


def test_inspect_retrieval():
    with tempfile.TemporaryDirectory() as d:
        out = inspect("chiffre affaires par pays", Path(d))
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "PILE ORDONNÉE" in content
        assert "TABLES comprises" in content
        assert "COLONNES comprises" in content
        print(f"  ✓ Inspection retrieval : rapport généré avec pile ordonnée")


if __name__ == "__main__":
    print("Test export schéma :")
    test_export_schema()
    print("Test inspection retrieval :")
    test_inspect_retrieval()
    print("\n✓ Outils de vérification validés")


def test_trace_retrieval():
    """Le traçage complet doit produire les 6 étapes."""
    import tempfile
    from scripts.trace_retrieval import trace
    with tempfile.TemporaryDirectory() as d:
        out = trace("chiffre affaires par pays", Path(d))
        c = out.read_text(encoding="utf-8")
        for etape in ["MOTS-CLÉS EXTRAITS", "RECHERCHE VECTORIELLE",
                      "RECHERCHE LEXICALE", "FUSION", "MULTI-HOP", "CLASSEMENT FINAL"]:
            assert etape in c, f"étape manquante : {etape}"
        print("  ✓ Trace retrieval : 6 étapes présentes")
