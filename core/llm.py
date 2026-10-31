"""
Client LLM local (Ollama).

Isolé derrière une interface simple pour pouvoir être stubbé dans les tests
(comme les embeddings en étapes 1-4). Aucune dépendance cloud.

Prérequis runtime : Ollama lancé + modèle pull (cf. scripts/03_pull_llm.sh).
"""
from __future__ import annotations
import json
import re
from config import settings


class LLMClient:
    """Wrapper minimal autour d'Ollama (chat completions)."""

    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or settings.LLM_MODEL
        self.host = host or settings.OLLAMA_HOST

    # Dernière réponse brute du modèle (pour debug/inspection après une erreur).
    last_raw_response: str = ""

    def chat(self, system: str, user: str, temperature: float = 0.0,
             force_json: bool = False) -> str:
        """Un tour de chat. Renvoie le texte brut du modèle.

        force_json=True active le mode JSON strict d'Ollama (format="json"),
        qui contraint le modèle à produire du JSON valide -> corrige à la
        RACINE les erreurs de parsing (au lieu de réparer après coup).
        """
        import ollama
        client = ollama.Client(host=self.host)
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={
                "temperature": temperature,
                "num_ctx": settings.LLM_NUM_CTX,
                "num_predict": settings.LLM_NUM_PREDICT,
            },
            keep_alive=settings.LLM_KEEP_ALIVE,
        )
        if force_json:
            kwargs["format"] = "json"  # Ollama garantit un JSON syntaxiquement valide
        resp = client.chat(**kwargs)
        content = resp["message"]["content"]

        # Capture pour inspection (accessible via LLMClient.last_raw_response
        # ou dans le fichier exports/last_llm_response.txt).
        LLMClient.last_raw_response = content
        if settings.DEBUG_DUMP_LLM:
            try:
                from pathlib import Path
                p = Path(settings.DATA_DIR).parent / "exports"
                p.mkdir(exist_ok=True)
                (p / "last_llm_response.txt").write_text(content, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        return content

    # ── Utilitaires de parsing robustes ─────────────────────────────
    @staticmethod
    def extract_json(text: str) -> dict:
        """Extrait un objet JSON d'une réponse LLM, de façon TRÈS robuste.

        Stratégie en cascade (ne plante jamais si le moindre champ est récupérable) :
          1. parse direct (cas idéal) ;
          2. json_repair : bibliothèque qui corrige la plupart des corruptions
             produites par les LLM (virgules finales, quotes, tokens parasites,
             troncature...) ;
          3. réparation maison (troncature / erreurs de syntaxe simples) ;
          4. fallback regex : on extrait au moins les champs critiques (intent...).
        """
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        start = cleaned.find("{")
        if start == -1:
            raise ValueError(f"Pas de JSON trouvé dans : {text[:200]}")
        candidate = cleaned[start:]

        # 1) Parse direct
        try:
            return json.loads(LLMClient._first_balanced(candidate))
        except Exception:  # noqa: BLE001
            pass

        # 2) json_repair (si disponible)
        try:
            from json_repair import repair_json
            fixed = repair_json(candidate)
            obj = json.loads(fixed)
            if isinstance(obj, dict) and obj:
                return obj
        except Exception:  # noqa: BLE001
            pass

        # 3) Réparation maison (troncature + erreurs simples)
        try:
            return LLMClient._loads_tolerant(LLMClient._first_balanced(candidate))
        except Exception:  # noqa: BLE001
            pass
        try:
            return LLMClient._repair_truncated_json(candidate, text)
        except Exception:  # noqa: BLE001
            pass

        # 4) Dernier recours : extraction regex des champs critiques.
        fallback = LLMClient._regex_fallback(candidate)
        if fallback:
            return fallback

        raise ValueError(
            "Impossible d'extraire un JSON exploitable de la réponse du modèle. "
            f"Début : {text[:200]}"
        )

    @staticmethod
    def _first_balanced(s: str) -> str:
        """Renvoie le 1er objet {…} équilibré (en ignorant les accolades en chaîne)."""
        depth = 0; in_str = False; esc = False
        for i, ch in enumerate(s):
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
                continue
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[:i + 1]
        return s  # pas équilibré -> renvoyé tel quel (géré en amont)

    @staticmethod
    def _regex_fallback(s: str) -> dict:
        """Extrait les champs clés par regex quand tout le reste a échoué.
        Garantit que le pipeline peut continuer (au pire, intent=2)."""
        out: dict = {}
        m = re.search(r'"intent"\s*:\s*([0-2])', s)
        out["intent"] = int(m.group(1)) if m else 2
        for key in ("completed_query", "clarification", "rewritten_query", "reason"):
            m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', s)
            if m:
                out[key] = m.group(1)
            elif re.search(rf'"{key}"\s*:\s*null', s):
                out[key] = None
        return out

    @staticmethod
    def _loads_tolerant(s: str) -> dict:
        """json.loads tolérant aux erreurs de syntaxe fréquentes des LLM :
        virgules finales, guillemets simples, None/True/False Python."""
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        repaired = LLMClient._fix_common_json_errors(s)
        return json.loads(repaired)

    @staticmethod
    def _fix_common_json_errors(s: str) -> str:
        """Corrige les fautes JSON typiques des petits modèles."""
        # Retire les virgules finales avant } ou ]  ->  {"a":1,}  ->  {"a":1}
        s = re.sub(r",\s*([}\]])", r"\1", s)
        # Remplace les littéraux Python par leurs équivalents JSON (hors chaînes).
        s = re.sub(r"\bNone\b", "null", s)
        s = re.sub(r"\bTrue\b", "true", s)
        s = re.sub(r"\bFalse\b", "false", s)
        # Guillemets simples -> doubles UNIQUEMENT si le texte n'a aucun double
        # (sinon on risque de casser des apostrophes dans les valeurs).
        if "'" in s and '"' not in s:
            s = s.replace("'", '"')
        return s

    @staticmethod
    def _repair_truncated_json(fragment: str, original: str) -> dict:
        """Ferme les structures ouvertes d'un JSON coupé en plein milieu."""
        buf = fragment.rstrip()

        # Si on est au milieu d'une chaîne, on la ferme.
        in_str = False
        esc = False
        depth_obj = 0
        depth_arr = 0
        for ch in buf:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth_obj += 1
            elif ch == "}":
                depth_obj -= 1
            elif ch == "[":
                depth_arr += 1
            elif ch == "]":
                depth_arr -= 1

        if in_str:
            buf += '"'

        buf = buf.rstrip()
        # Retire une virgule finale orpheline.
        while buf and buf[-1] == ",":
            buf = buf[:-1].rstrip()
        # Cas "clé": <coupé> -> la clé n'a pas de valeur : on retire la clé entière
        # (et la virgule qui la précède), sinon le JSON reste invalide.
        if buf.endswith(":"):
            buf = buf[:-1].rstrip()          # enlève le ':'
            if buf.endswith('"'):            # enlève la clé "..."
                q = buf.rfind('"', 0, len(buf) - 1)
                if q != -1:
                    buf = buf[:q].rstrip()
            while buf and buf[-1] == ",":    # et la virgule qui la précédait
                buf = buf[:-1].rstrip()

        # Ferme les tableaux puis les objets restés ouverts.
        buf += "]" * max(0, depth_arr)
        buf += "}" * max(0, depth_obj)

        try:
            return LLMClient._loads_tolerant(buf)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "JSON incomplet et irréparable (le modèle a probablement été "
                f"tronqué ; augmentez LLM_NUM_PREDICT). Détail : {exc}. "
                f"Début de la réponse : {original[:200]}"
            ) from exc

    @staticmethod
    def extract_sql(text: str) -> str:
        """Extrait une requête SQL d'une réponse (tolère ```sql ... ```)."""
        m = re.search(r"```sql\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Sinon : à partir du premier SELECT/WITH
        m = re.search(r"\b(SELECT|WITH)\b.*", text, re.IGNORECASE | re.DOTALL)
        return m.group(0).strip() if m else text.strip()