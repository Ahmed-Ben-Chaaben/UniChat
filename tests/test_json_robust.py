"""
Test de robustesse de LLMClient.extract_json.

Reproduit le crash réel : "ValueError: Impossible de trouver la fin de l'objet JSON"
causé par un JSON TRONQUÉ (modèle coupé par num_predict trop court).

Vérifie :
  1. JSON normal -> parsé ;
  2. JSON dans un bloc markdown -> parsé ;
  3. JSON TRONQUÉ en plein objet -> réparé (pas d'exception) ;
  4. JSON tronqué en pleine chaîne -> réparé ;
  5. JSON tronqué dans un tableau -> réparé ;
  6. accolades DANS une chaîne -> ne faussent pas le comptage ;
  7. texte sans JSON -> erreur explicite.
"""
from core.llm import LLMClient


def test_normal():
    d = LLMClient.extract_json('{"intent": 2, "x": {"a": 1}}')
    assert d["intent"] == 2 and d["x"]["a"] == 1
    print("  ✓ JSON normal")


def test_markdown():
    d = LLMClient.extract_json('Voici :\n```json\n{"intent": 1}\n```\nfin')
    assert d["intent"] == 1
    print("  ✓ JSON dans bloc markdown")


def test_truncated_object():
    """Cas réel : la SIR est coupée au milieu."""
    raw = '{"Key Components": {"Metric": "SUM(InvoiceLine.UnitPrice)", "Dimensions": ["Artist"]'
    d = LLMClient.extract_json(raw)
    assert d["Key Components"]["Metric"].startswith("SUM("), d
    assert d["Key Components"]["Dimensions"] == ["Artist"], d
    print(f"  ✓ Objet tronqué réparé : {list(d.keys())}")


def test_truncated_mid_string():
    """Coupé en plein milieu d'une valeur texte."""
    raw = '{"Query Understanding": "L\'utilisateur veut les 5 artis'
    d = LLMClient.extract_json(raw)
    assert "Query Understanding" in d
    print(f"  ✓ Chaîne tronquée réparée : {d}")


def test_truncated_trailing_comma():
    """Coupé juste après une virgule / un deux-points."""
    raw = '{"Metric": "SUM(x)", "Dimensions": ["Artist"], "Time Range":'
    d = LLMClient.extract_json(raw)
    assert d["Metric"] == "SUM(x)"
    print(f"  ✓ Virgule/':' orphelin nettoyé : {list(d.keys())}")


def test_braces_inside_string():
    """Des accolades DANS une chaîne ne doivent pas fausser le comptage."""
    raw = '{"desc": "un objet {imbriqué} ici", "ok": true}'
    d = LLMClient.extract_json(raw)
    assert d["ok"] is True and "{imbriqué}" in d["desc"], d
    print("  ✓ Accolades dans une chaîne ignorées")


def test_no_json():
    try:
        LLMClient.extract_json("aucun json ici")
        assert False, "aurait dû lever"
    except ValueError:
        print("  ✓ Absence de JSON -> erreur explicite")


if __name__ == "__main__":
    print("Tests de parsing JSON robuste :")
    test_normal()
    test_markdown()
    test_truncated_object()
    test_truncated_mid_string()
    test_truncated_trailing_comma()
    test_braces_inside_string()
    test_no_json()
    print("\n✓ extract_json résiste aux JSON tronqués (plus de crash)")
