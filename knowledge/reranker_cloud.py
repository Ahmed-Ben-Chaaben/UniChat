# knowledge/reranker_cloud.py

import requests
from config import settings

class Reranker:
    """Wrapper pour Cohere Rerank API."""
    _api_key = settings.COHERE_API_KEY
    _model = settings.COHERE_RERANK_MODEL

    @classmethod
    def score(cls, query: str, docs: list[str]) -> list[float]:
        url = "https://api.cohere.ai/v1/rerank"
        headers = {
            "Authorization": f"Bearer {cls._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "documents": docs,
            "model": cls._model,
            "top_n": len(docs),  # on veut tous les scores
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # Les résultats sont triés par score, mais on veut les scores dans l'ordre original.
        # On construit un dictionnaire {index: score}
        scores = [0.0] * len(docs)
        for item in data["results"]:
            idx = item["index"]
            scores[idx] = item["relevance_score"]
        return scores