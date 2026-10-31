"""
core/LLM_cloud.py
Client LLM via Ollama Cloud (qwen3.5:397b) ou local.
"""

from __future__ import annotations
import json
import re
import os
from typing import Optional
from config import settings

try:
    from ollama import Client, ResponseError
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


class LLMClient:
    """
    Client LLM via Ollama Cloud ou local.
    """
    
    # === MODÈLES CLOUD DISPONIBLES ===
    CLOUD_MODELS = {
        "qwen3.5": "qwen3.5:397b",
        "qwen3.5-small": "qwen3.5",
        "deepseek-v4": "deepseek-v4-flash",
        "gemma4": "gemma4:31b",
    }
    
    DEFAULT_MODEL = "gemma4:31b"
    DEFAULT_CLOUD_HOST = "https://ollama.com"
    
    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        api_key: str | None = None,
        use_cloud: bool = True
    ):
        self.use_cloud = use_cloud
        
        model_key = model or self.DEFAULT_MODEL
        self.model_name = model_key
        self.model_tag = self.CLOUD_MODELS.get(model_key, model_key)
        
        if use_cloud:
            self.host = host or os.getenv("OLLAMA_HOST", self.DEFAULT_CLOUD_HOST)
            self.api_key = api_key or settings.OLLAMA_CLOUD_API_KEY or os.getenv("OLLAMA_CLOUD_API_KEY")
            
            if not self.api_key:
                raise ValueError(
                    "Clé API Ollama manquante !\n"
                    "1. Va sur https://ollama.com/settings/keys\n"
                    "2. Crée un compte\n"
                    "3. Génère une clé API\n"
                    "4. export OLLAMA_API_KEY='sk-...'"
                )
            
            self._client = Client(
                host=self.host,
                headers={'Authorization': f'Bearer {self.api_key}'}
            )
            print(f"LLMClient [Ollama Cloud] -> {self.model_name} ({self.model_tag})")
            
        else:
            self.host = host or "http://localhost:11434"
            if not OLLAMA_AVAILABLE:
                raise ImportError("pip install ollama")
            self._client = Client(host=self.host)
            print(f"LLMClient [Ollama Local] -> {self.model_tag}")

    def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]
        options = {
            'num_ctx': 128000,
            'num_predict': 4096,
            'temperature': temperature,
        }
        
        try:
            response = self._client.chat(
                model=self.model_tag,
                messages=messages,
                options=options,
                stream=False,
            )
            return response['message']['content']
            
        except ResponseError as e:
            print(f"Erreur : {e.error} (code {e.status_code})")
            if self.use_cloud and "not found" in e.error.lower():
                print(f"Le modèle '{self.model_tag}' n'est pas disponible sur Ollama Cloud.")
                print(f"Modèles cloud disponibles : {list(self.CLOUD_MODELS.keys())}")
            raise

    @staticmethod
    def extract_json(text: str) -> dict:
        print("=== RAW LLM RESPONSE ===")
        print(repr(text))
        print("=========================")
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        start = cleaned.find("{")
        if start == -1:
            raise ValueError(f"Pas de JSON trouvé dans : {text[:200]}")
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(cleaned[start:i + 1])
        raise ValueError(f"JSON non équilibré dans : {text[:200]}")

    @staticmethod
    def extract_sql(text: str) -> str:
        m = re.search(r"```sql\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        m = re.search(r"\b(SELECT|WITH)\b.*", text, re.IGNORECASE | re.DOTALL)
        return m.group(0).strip() if m else text.strip()


class LLMFactory:
    """Factory pour créer des clients préconfigurés."""
    
    DEFAULT_MODEL = "qwen3.5"
    
    @staticmethod
    def cloud(model: str = DEFAULT_MODEL, api_key: str | None = None) -> LLMClient:
        """Crée un client cloud avec le modèle spécifié."""
        return LLMClient(model=model, use_cloud=True, api_key=api_key)
    
    @staticmethod
    def qwen35(api_key: str | None = None) -> LLMClient:
        """Qwen3.5:397b — le meilleur pour le code sur Ollama Cloud."""
        return LLMClient(model="qwen3.5", use_cloud=True, api_key=api_key)
    
    @staticmethod
    def deepseek(api_key: str | None = None) -> LLMClient:
        """DeepSeek-V4-Flash — alternative code."""
        return LLMClient(model="deepseek-v4", use_cloud=True, api_key=api_key)
    
    @staticmethod
    def local(model_tag: str = "qwen3-coder:7b") -> LLMClient:
        """Ollama Local (tous les modèles disponibles)."""
        return LLMClient(model=model_tag, use_cloud=False)