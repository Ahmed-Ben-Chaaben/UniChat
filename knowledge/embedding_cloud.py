# knowledge/embedding.py
from ollama import Client
from config import settings

class EmbeddingModel:
    _client = None
    _model = settings.COHERE_EMBEDDING_MODEL  # ou settings.EMBEDDING_MODEL si vous passez à un modèle Ollama

    @classmethod
    def _get_client(cls):
        if cls._client is None:
            # Si vous utilisez le cloud Ollama pour les embeddings
            client_kwargs = {}
            if settings.OLLAMA_CLOUD_HOST:
                client_kwargs['host'] = settings.OLLAMA_CLOUD_HOST
            if settings.OLLAMA_CLOUD_API_KEY:
                client_kwargs['headers'] = {'Authorization': f'Bearer {settings.OLLAMA_CLOUD_API_KEY}'}
            cls._client = Client(**client_kwargs)
        return cls._client

    @classmethod
    def encode(cls, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = cls._get_client()
        # La méthode embed accepte une liste de textes
        response = client.embed(model=cls._model, input=texts)
        # La réponse contient un champ 'embeddings'
        return response['embeddings']