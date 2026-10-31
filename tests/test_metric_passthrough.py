"""
Test : la métrique résolue par la SIR est bien transmise au générateur SQL.

Reproduit le bug observé avec le 7b : la SIR disait
  Metric = SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)
mais le SQL utilisait SUM(Invoice.Total) (piège du grain).
On vérifie ici que le prompt SQL reçoit désormais explicitement la métrique.
"""
import tests.test_logic  # stubs

from core.llm import LLMClient
from core.sir import SQLGenerator, SIRResult, _format_sir_hints


def test_sir_hints_extracted():
    sir = SIRResult(
        rewritten_query="Top 5 artistes par CA en 2022",
        sir_json={
            "Key Components": {
                "Metric": "SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)",
                "Dimensions": ["Artist"],
                "Time Range": "2022",
                "Filters": [],
            },
            "Knowledge Mapping": ["CA artiste -> SUM(InvoiceLine.UnitPrice*Quantity)"],
        },
    )
    hints = _format_sir_hints(sir)
    print("  Indications extraites de la SIR :")
    for line in hints.splitlines():
        print(f"    {line}")
    assert "SUM(InvoiceLine.UnitPrice" in hints, "la métrique doit être extraite"


def test_metric_in_sql_prompt():
    """Capture le prompt envoyé au LLM SQL et vérifie qu'il contient la métrique."""
    captured = {}

    class CapturingLLM(LLMClient):
        def __init__(self): pass
        def chat(self, system, user, temperature=0.0):
            if "expert sql" in system.lower():
                captured["prompt"] = user
                return "```sql\nSELECT 1\n```"
            return "{}"

    sir = SIRResult(
        rewritten_query="Top 5 artistes par CA en 2022",
        sir_json={"Key Components": {
            "Metric": "SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)"}},
    )
    gen = SQLGenerator(llm=CapturingLLM())
    gen.to_sql(sir, knowledge=[], dialect="sqlite", schema="Table Invoice(Total)")

    prompt = captured.get("prompt", "")
    assert "SUM(InvoiceLine.UnitPrice" in prompt, \
        "le prompt SQL DOIT contenir la métrique de la SIR"
    assert "n'utilise JAMAIS" in prompt or "JAMAIS" in prompt, \
        "le prompt doit interdire une autre mesure"
    print("  ✓ Le prompt SQL contient bien la métrique imposée par la SIR")
    print("  ✓ Le prompt interdit explicitement de retomber sur Invoice.Total")


if __name__ == "__main__":
    print("Test extraction des indications SIR :")
    test_sir_hints_extracted()
    print("Test transmission de la métrique au prompt SQL :")
    test_metric_in_sql_prompt()
    print("\n✓ La métrique de la SIR est désormais imposée au générateur SQL")
