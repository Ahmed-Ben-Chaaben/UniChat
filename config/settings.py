"""
Configuration centralisée du projet.

POINT CLÉ DE L'ARCHITECTURE DÉCOUPLÉE :
  - DB_URL  -> base MÉTIER (données à interroger). C'est la SEULE chose à changer
              pour passer de Chinook (test) à l'entrepôt Unimed (prod).
  - QDRANT_* -> base VECTORIELLE / Knowledge Base (séparée de la base métier).

Aucune autre partie du code ne doit connaître le type de SGBD métier :
tout passe par SQLAlchemy via DB_URL.
"""
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# Racine du projet
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────
#  BASE MÉTIER (agnostique — SQLAlchemy)
# ─────────────────────────────────────────────────────────────────────
# TEST  : Chinook en SQLite (aucun serveur requis)
# PROD  : remplacer par la chaîne de l'entrepôt Unimed, ex. :
#   postgresql+psycopg2://user:pwd@host:5432/dwh
#   mysql+pymysql://user:pwd@host:3306/dwh
#   mssql+pyodbc://user:pwd@host/dwh?driver=ODBC+Driver+17+for+SQL+Server
#   oracle+oracledb://user:pwd@host:1521/?service_name=dwh
DB_URL = os.getenv(
    "DB_URL",
    "mysql+pymysql://root:benchahmed123A!@localhost:3306/testdb_datawarehouse"
    # "mssql+pyodbc:///?odbc_connect=Driver%3DODBC+Driver+17+for+SQL+Server%3BServer%3DPORTABLE-HP-21%5CSQLEXPRESS%3BDatabase%3DTestDB_DataWarehouse%3BTrusted_Connection%3Dyes%3BTrustServerCertificate%3Dyes%3B",
)

# Sécurité : on force la lecture seule au niveau applicatif (cf. core/db.py)
READ_ONLY = True
# LIMIT injecté automatiquement si la requête n'en a pas
DEFAULT_ROW_LIMIT = 1000

# ─────────────────────────────────────────────────────────────────────
#  QDRANT (Knowledge Base vectorielle)
# ─────────────────────────────────────────────────────────────────────
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "7000"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "unimed_kb")

# ─────────────────────────────────────────────────────────────────────
#  MODÈLES LOCAUX
# ─────────────────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = 1024  # dimension de bge-m3
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")



***REMOVED***
COHERE_EMBEDDING_MODEL="embed-multilingual-v3.0"
COHERE_RERANK_MODEL="rerank-multilingual-v3.0"

LLM_MODEL_CLOUD="gemma4:31b"
OLLAMA_CLOUD_HOST="https://ollama.com"
OLLAMA_CLOUD_API_KEY="***REMOVED***"

# LLM via Ollama
# OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")

# Réglages performance LLM (cruciaux sur CPU / RAM limitée)
LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "30m")   # garde le modèle chargé
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "4096"))   # fenêtre de contexte bornée
LLM_NUM_PREDICT = int(os.getenv("LLM_NUM_PREDICT", "1024"))  # longueur de sortie max

# ─────────────────────────────────────────────────────────────────────
#  RETRIEVAL
# ─────────────────────────────────────────────────────────────────────
COARSE_TOP_K = 30   # candidats remontés en phase coarse (BM25 + vecteur)
FINE_TOP_K = 16      # candidats gardés après re-ranking BGE

# ─────────────────────────────────────────────────────────────────────
#  MÉTHODE DE CLASSEMENT (fine ranking)
# ─────────────────────────────────────────────────────────────────────
# Choix de la méthode de classement final des candidats :
#   "paper"    -> fonction du papier SiriusBI : Score = Sim + α·Embed + β·Heat
#   "rrf"      -> Reciprocal Rank Fusion (rapide, robuste, sans hyperparamètre)
#   "reranker" -> bge-reranker-v2-m3 (neuronal, lent sur CPU, peu fiable court)
SCORING_METHOD = os.getenv("SCORING_METHOD", "paper")

# Hyperparamètres de la fonction du papier (Score = Sim + α·Embed + β·Heat).
# Tous les termes sont normalisés dans [0,1] avant combinaison.
#   ALPHA : poids de la similarité sémantique (Embed). ↑ = plus de sémantique.
#   BETA  : poids de la popularité (Heat). 0 par défaut (pas de logs d'usage).
# Ajuster selon votre base : sur schéma technique, ALPHA modéré (~1.0) évite
# que la sémantique domine le lexical ; augmenter BETA si vous alimentez 'heat'.
ALPHA = float(os.getenv("ALPHA", "1.0"))
BETA = float(os.getenv("BETA", "0.0"))

# Constante RRF (utilisée si SCORING_METHOD="rrf"). 60 = valeur standard.
RRF_K = int(os.getenv("RRF_K", "60"))

# Reranker neuronal : activé seulement si USE_RERANKER=1 OU SCORING_METHOD="reranker".
# Lent sur CPU et peu fiable sur chaînes courtes -> désactivé par défaut.
USE_RERANKER = os.getenv("USE_RERANKER", "0") == "1"

# Fusionne MRD-Q et SIR en UN seul appel LLM (2 appels/question au lieu de 3).
# À ACTIVER (1) sur petit modèle lent (ex: 1.5b sur CPU) pour gagner du temps.
# À DÉSACTIVER (0) sur modèle capable (ex: 7b) : la SIR complète (Key Components,
# métrique exacte) est alors produite séparément et transmise au générateur SQL,
# ce qui évite le "piège du grain" (SQL retombant sur Invoice.Total).
FUSED_ANALYSIS = os.getenv("FUSED_ANALYSIS", "0") == "1"


 
# ─────────────────────────────────────────────────────────────────────
#  DEBUG / FIABILITÉ JSON
# ─────────────────────────────────────────────────────────────────────
# Sauvegarde la dernière réponse brute du LLM dans exports/last_llm_response.txt
# (utile pour inspecter ce que le modèle a répondu avant une erreur de parsing).
DEBUG_DUMP_LLM = os.getenv("DEBUG_DUMP_LLM", "1") == "1"
 
# Force le mode JSON strict d'Ollama (format="json") sur les appels qui
# attendent du JSON (MRD-Q, SIR). Corrige à la racine les JSON malformés.
LLM_FORCE_JSON = os.getenv("LLM_FORCE_JSON", "1") == "1"

BI_SEC_CONNECTION_STRING="DRIVER=ODBC Driver 17 for SQL Server;Server=PORTABLE-HP-21\SQLEXPRESS;Database=TestDB_DataWarehouse;Trusted_Connection=yes;TrustServerCertificate=yes;"


KEYCLOAK_SERVER_URL="http://localhost:8095"
KEYCLOAK_REALM="bi-assistant"
KEYCLOAK_CLIENT_ID="bi-assistant-app"
KEYCLOAK_CLIENT_SECRET="JwbJZ4BH3txPztWGib3dBOT8rDHdBJh2"
