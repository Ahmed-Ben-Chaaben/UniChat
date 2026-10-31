# knowledge/store.py
from __future__ import annotations
from typing import Iterable
import requests
import json
import numpy as np
import math

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import settings
from knowledge.builder import KBEntry


# ─── Utilitaires ───────────────────────────────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    return text.lower().replace(".", " ").replace("|", " ").split()


def _entry_fields(entry: "KBEntry") -> list[str]:
    if entry.kb_type == "table":
        cols = entry.payload.get("columns")
        if cols:
            fields = []
            for c in cols:
                if isinstance(c, dict):
                    fields.append(" ".join(
                        str(v) for v in (c.get("name"), c.get("description")) if v
                    ))
                else:
                    fields.append(str(c))
            if fields:
                return fields
    return [entry.text_for_embedding()]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


# ─── Session HTTP avec retry ──────────────────────────────────────────────────
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _get_retry_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


# ─── Modèles d'embedding et reranker (cloud Cohere) ─────────────────────────
class EmbeddingModel:
    _api_key = getattr(settings, "COHERE_API_KEY", None)
    _model = getattr(settings, "COHERE_EMBEDDING_MODEL", "embed-multilingual-v3.0")
    _batch_size = 96
    _session = _get_retry_session()

    @classmethod
    def encode(cls, texts: list[str], input_type: str = "search_document") -> list[list[float]]:
        if not texts:
            return []
        if not cls._api_key:
            raise ValueError("❌ COHERE_API_KEY non définie dans la configuration")
        all_embeddings = []
        for i in range(0, len(texts), cls._batch_size):
            batch = texts[i:i + cls._batch_size]
            url = "https://api.cohere.ai/v1/embed"
            headers = {
                "Authorization": f"Bearer {cls._api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "texts": batch,
                "model": cls._model,
                "input_type": input_type,
            }
            try:
                resp = cls._session.post(url, json=payload, headers=headers, timeout=120)
                resp.raise_for_status()
                data = resp.json()
                if "embeddings" not in data:
                    raise ValueError(f"Réponse inattendue : {data.keys()}")
                all_embeddings.extend(data["embeddings"])
            except Exception as e:
                print(f"❌ Erreur Cohere Embed (batch {i//cls._batch_size + 1}) : {e}")
                raise
        return all_embeddings


class Reranker:
    _api_key = getattr(settings, "COHERE_API_KEY", None)
    _model = getattr(settings, "COHERE_RERANK_MODEL", "rerank-multilingual-v3.0")
    _session = _get_retry_session()

    @classmethod
    def score(cls, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        if not cls._api_key:
            raise ValueError("❌ COHERE_API_KEY non définie pour le reranker")
        url = "https://api.cohere.ai/v1/rerank"
        headers = {
            "Authorization": f"Bearer {cls._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "documents": docs,
            "model": cls._model,
            "top_n": len(docs),
        }
        try:
            resp = cls._session.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            scores = [0.0] * len(docs)
            for item in data["results"]:
                scores[item["index"]] = item["relevance_score"]
            return scores
        except Exception as e:
            print(f"❌ Erreur Cohere Rerank : {e}")
            raise


# ─── Similarité lexicale (TF‑IDF) ────────────────────────────────────────────
def _keyword_table_similarity(keywords: list[str],
                               cand_entries: list["KBEntry"]) -> dict[str, float]:
    if not keywords or not cand_entries:
        return {e.name: 0.0 for e in cand_entries}

    fields_per_entry = {e.name: _entry_fields(e) for e in cand_entries}
    all_fields: list[str] = []
    field_range: dict[str, tuple[int, int]] = {}
    for e in cand_entries:
        start = len(all_fields)
        all_fields.extend(fields_per_entry[e.name])
        field_range[e.name] = (start, len(all_fields))

    if not all_fields:
        return {e.name: 0.0 for e in cand_entries}

    corpus = all_fields + keywords
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
    try:
        tfidf = vectorizer.fit_transform(corpus)
    except ValueError:
        return {e.name: 0.0 for e in cand_entries}

    n_fields = len(all_fields)
    field_vecs = tfidf[:n_fields]
    keyword_vecs = tfidf[n_fields:]
    sim_matrix = cosine_similarity(keyword_vecs, field_vecs)

    sim_scores: dict[str, float] = {}
    for e in cand_entries:
        start, end = field_range[e.name]
        cols_sim = sim_matrix[:, start:end]
        per_keyword_max = cols_sim.max(axis=1)
        sim_scores[e.name] = float(per_keyword_max.sum())
    return sim_scores


# ─── Domain Boost ─────────────────────────────────────────────────────────────
DOMAIN_BOOST_THRESHOLD = getattr(settings, "DOMAIN_BOOST_THRESHOLD", 0.45)
DOMAIN_BOOST_WEIGHT = getattr(settings, "DOMAIN_BOOST_WEIGHT", 1.5)

def _compute_domain_boost(
    query_vec: list[float],
    cand_entries: list[KBEntry],
) -> dict[str, float]:
    col_by_table: dict[str, list[str]] = {}
    for e in cand_entries:
        if e.label == "Column":
            tname = e.payload.get("table", "")
            col_by_table.setdefault(tname, []).append(e.name)

    boost: dict[str, float] = {}

    term_entries = [e for e in cand_entries
                    if e.kb_type == "domain_knowledge" and e.label == "Term"]
    if not term_entries:
        return boost

    term_texts = [e.text_for_embedding() for e in term_entries]
    term_vecs = EmbeddingModel.encode(term_texts)

    for entry, tvec in zip(term_entries, term_vecs):
        sim = _cosine(query_vec, tvec)
        if sim < DOMAIN_BOOST_THRESHOLD:
            continue

        tables_ref: list[str] = entry.payload.get("tables_ref", [])
        for tname in tables_ref:
            boost[tname] = boost.get(tname, 0.0) + sim * DOMAIN_BOOST_WEIGHT
            for col_name in col_by_table.get(tname, []):
                boost[col_name] = boost.get(col_name, 0.0) + sim * DOMAIN_BOOST_WEIGHT * 0.5

    return boost


# ─── KnowledgeStore ───────────────────────────────────────────────────────────
class KnowledgeStore:
    def __init__(self):
        self.client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
        self.collection = settings.QDRANT_COLLECTION
        self.entries: list[KBEntry] = []
        self._bm25: BM25Okapi | None = None
        self._by_name: dict[str, KBEntry] = {}

    def load_from_qdrant(self) -> None:
        """Charge les entrées depuis Qdrant pour reconstruire le cache local."""
        if not self.client.collection_exists(self.collection):
            raise ValueError(f"Collection {self.collection} n'existe pas")

        points = []
        offset = None
        while True:
            scroll_result = self.client.scroll(
                collection_name=self.collection,
                offset=offset,
                limit=10000,
                with_payload=True,
                with_vectors=False,
            )
            points.extend(scroll_result[0])
            offset = scroll_result[1]
            if offset is None:
                break

        if not points:
            raise ValueError(f"La collection {self.collection} est vide")

        self.entries = []
        self._by_name = {}
        for point in points:
            payload = point.payload
            entry = KBEntry(
                name=payload["name"],
                label=payload.get("label", ""),
                description=payload.get("description", ""),
                anc_name=payload.get("anc_name"),
                anc_label=payload.get("anc_label"),
                kb_type=payload.get("kb_type", ""),
                payload={k: v for k, v in payload.items()
                         if k not in {"name", "label", "description", "anc_name", "anc_label", "kb_type"}}
            )
            self.entries.append(entry)
            self._by_name[entry.name] = entry

        corpus = [_tokenize(e.text_for_embedding()) for e in self.entries]
        self._bm25 = BM25Okapi(corpus)
        print(f"✅ Chargé {len(self.entries)} entrées depuis Qdrant (collection {self.collection})")

    def index(self, entries: list[KBEntry]) -> None:
        seen: set[str] = set()
        unique: list[KBEntry] = []
        for e in entries:
            if e.name not in seen:
                seen.add(e.name)
                unique.append(e)
        entries = unique

        self.entries = entries
        self._by_name = {e.name: e for e in entries}

        corpus = [_tokenize(e.text_for_embedding()) for e in entries]
        self._bm25 = BM25Okapi(corpus)

        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection,
            vectors_config=VectorParams(size=settings.EMBEDDING_DIM, distance=Distance.COSINE),
        )
        vectors = EmbeddingModel.encode([e.text_for_embedding() for e in entries])
        points = [
            PointStruct(id=i, vector=vectors[i], payload={
                "name": e.name, "label": e.label, "description": e.description,
                "anc_name": e.anc_name, "anc_label": e.anc_label,
                "kb_type": e.kb_type, **e.payload,
            })
            for i, e in enumerate(entries)
        ]
        self.client.upsert(self.collection, points=points)

    # ─── Normalisation min‑max ──────────────────────────────────────────────
    def _norm(self, d):
        if d is None:
            return {} if isinstance(d, dict) else np.array([])

        if isinstance(d, dict):
            if not d:
                return {}
            lo, hi = min(d.values()), max(d.values())
            if hi - lo < 1e-9:
                return {k: 0.0 for k in d}
            return {k: (v - lo) / (hi - lo) for k, v in d.items()}
        else:
            arr = np.asarray(d)
            if arr.size == 0:
                return arr
            lo, hi = arr.min(), arr.max()
            if hi - lo < 1e-9:
                return np.zeros_like(arr, dtype=float)
            return (arr - lo) / (hi - lo)

    # ─── Multi‑hop ────────────────────────────────────────────────────────────
    def _expand_ancestors(self, names: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        stack = list(names)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            entry = self._by_name.get(n)
            if entry and entry.anc_name and entry.anc_name not in seen:
                stack.append(entry.anc_name)
        return seen

    # ─── Retrieval principal ──────────────────────────────────────────────────
    def retrieve(self, query: str,
                 coarse_k: int | None = None,
                 fine_k: int | None = None) -> list[KBEntry]:
        coarse_k = coarse_k or settings.COARSE_TOP_K
        fine_k = fine_k or settings.FINE_TOP_K

        # Embedding de la requête
        try:
            qvec = EmbeddingModel.encode([query], input_type="search_query")[0]
        except Exception as e:
            print(f"⚠️ Erreur lors de l’embedding de la requête : {e}")
            return []

        # Recherche vectorielle
        vec_hits = self.client.query_points(self.collection, query=qvec, limit=coarse_k).points
        vec_names = [h.payload["name"] for h in vec_hits]
        vec_scores = {h.payload["name"]: float(getattr(h, "score", 0.0)) for h in vec_hits}

        # Recherche lexicale (BM25)
        bm_all = self._bm25.get_scores(_tokenize(query))
        top_idx = sorted(range(len(bm_all)), key=lambda i: bm_all[i], reverse=True)[:coarse_k]
        bm25_names = [self.entries[i].name for i in top_idx]

        # Fusion + multi‑hop
        candidates = set(vec_names) | set(bm25_names)
        candidates = self._expand_ancestors(candidates)
        cand_entries = [self._by_name[n] for n in candidates if n in self._by_name]
        if not cand_entries:
            return []

        # Classement fin
        method = settings.SCORING_METHOD.lower()

        if method == "reranker" or settings.USE_RERANKER:
            docs = [e.text_for_embedding() for e in cand_entries]
            rerank_scores = Reranker.score(query, docs)
            ranked = sorted(zip(cand_entries, rerank_scores),
                            key=lambda x: x[1], reverse=True)
            return [e for e, _ in ranked[:fine_k]]

        if method == "paper":
            scored = self._score_paper(cand_entries, vec_scores, query)
            return [e for e, _ in scored[:fine_k]]

        # défaut : RRF
        scored = self._score_rrf(cand_entries, vec_names, bm25_names)
        return [e for e, _ in scored[:fine_k]]

    # ─── Scoring "paper" détaillé ────────────────────────────────────────────
    def score_paper_breakdown(self, cand_entries, vec_scores, query: str,
                               qvec: list[float] | None = None) -> list[dict]:
        alpha = settings.ALPHA
        beta = settings.BETA

        if qvec is None:
            qvec = EmbeddingModel.encode([query])[0]

        keywords = _tokenize(query)
        sim_scores = _keyword_table_similarity(keywords, cand_entries)
        domain_boost = _compute_domain_boost(qvec, cand_entries)

        emb_n = self._norm(vec_scores)
        heat_raw = {e.name: float(e.payload.get("heat", 0.0)) for e in cand_entries}
        heat_n = self._norm(heat_raw)

        rows = []
        for e in cand_entries:
            sim = sim_scores.get(e.name, 0.0)
            db = domain_boost.get(e.name, 0.0)
            emb = emb_n.get(e.name, 0.0)
            heat = heat_n.get(e.name, 0.0)
            rows.append({
                "entry": e,
                "sim": sim,
                "domain_boost": db,
                "embed": emb,
                "heat": heat,
                "alpha_embed": alpha * emb,
                "beta_heat": beta * heat,
                "score": sim + db + alpha * emb + beta * heat,
            })
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows

    def _score_paper(self, cand_entries, vec_scores, query: str):
        rows = self.score_paper_breakdown(cand_entries, vec_scores, query, qvec=None)
        return [(r["entry"], r["score"]) for r in rows]

    # ─── RRF ──────────────────────────────────────────────────────────────────
    def _score_rrf(self, cand_entries, vec_names, bm25_names):
        k = settings.RRF_K
        vr = {n: i for i, n in enumerate(vec_names)}
        br = {n: i for i, n in enumerate(bm25_names)}
        scored = []
        for e in cand_entries:
            s = 0.0
            if e.name in vr: s += 1.0 / (k + vr[e.name])
            if e.name in br: s += 1.0 / (k + br[e.name])
            scored.append((e, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ─── Warmup ──────────────────────────────────────────────────────────────
    def warmup(self) -> None:
        EmbeddingModel.encode(["warmup"])
        if settings.USE_RERANKER:
            Reranker.score("warmup", ["warmup"])