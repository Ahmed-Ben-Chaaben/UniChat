# """
# MRD-Q : Multi-Round Dialogue with Querying (papier SiriusBI §3.3).

# Deux composants :
#   1. Dialogue Analysis (§3.3.1) :
#        - intégrité sémantique : la question contient-elle métrique ET dimension ?
#        - si incomplète, complétion via le dernier tour historique qui les contient.
#   2. Intention Querying (§3.3.2) :
#        - classification I(Q) ∈ {0,1,2} :
#             0 = hors périmètre BI  -> on décline poliment
#             1 = incomplète         -> on pose une question de clarification
#             2 = complète           -> on continue le pipeline
#        - clarification guidée par la connaissance domaine retrouvée.

# Contrairement au papier, on n'entraîne aucun PLM : on s'appuie sur le
# raisonnement du LLM (cf. §3.3.1, "we leverage the robust reasoning of LLMs").
# """
# from __future__ import annotations
# from dataclasses import dataclass, field
# from typing import Literal

# from core.LLM_cloud import LLMClient

# IntentCode = Literal[0, 1, 2]


# @dataclass
# class DialogueState:
#     """Contexte multi-tours."""
#     history: list[str] = field(default_factory=list)  # questions précédentes (NL)
#     pending_question: str | None = None  # question en attente d'une clarification

#     def add(self, question: str) -> None:
#         self.history.append(question)


# @dataclass
# class AnalysisResult:
#     intent: IntentCode
#     completed_query: str        # question complétée (sémantiquement complète)
#     clarification: str | None   # question à poser si intent == 1
#     reason: str                 # justification (debug / rapport)
#     rewritten_query: str | None = None  # réécriture SIR (mode fusionné, si intent=2)


# _SYSTEM = (
#     "Tu es un analyste BI. Tu analyses une question utilisateur en français "
#     "pour un assistant de Business Intelligence qui interroge une base de données. "
#     "Tu raisonnes sur la présence d'une MÉTRIQUE (mesure quantitative, ex: chiffre "
#     "d'affaires, nombre de ventes) et d'une DIMENSION (axe d'analyse, ex: par pays, "
#     "le mois dernier). Réponds UNIQUEMENT en JSON."
# )

# _PROMPT = """\
# Historique des questions précédentes (du plus ancien au plus récent) :
# {history}

# Question actuelle : "{question}"

# Connaissance métier disponible :
# {domain}

# Analyse la question actuelle en t'aidant de l'historique pour combler les
# informations omises (ex: une période mentionnée dans un tour précédent).

# Renvoie un JSON avec EXACTEMENT ces clés :
# {{
#   "intent": 0 | 1 | 2,
#   "completed_query": "<question reformulée, complète et autonome>",
#   "clarification": "<question à poser si intent=1, sinon null>",
#   "reason": "<courte justification>"
# }}

# Règles :
# - intent=0 si la question n'a aucun rapport avec une analyse de données.
# - intent=1 si, même avec l'historique, il manque une métrique OU une dimension
#   OU si un terme est ambigu : pose alors UNE question de clarification.
# - intent=2 si la question est exploitable pour générer une requête SQL.
# """


# class MRDQ:
#     def __init__(self, llm: LLMClient | None = None):
#         self.llm = llm or LLMClient()

#     def analyze(self, question: str, state: DialogueState,
#                 domain_knowledge: str = "") -> AnalysisResult:
#         hist = "\n".join(f"- {h}" for h in state.history) or "(aucune)"
#         prompt = _PROMPT.format(
#             history=hist, question=question,
#             domain=domain_knowledge or "(aucune)",
#         )
#         raw = self.llm.chat(_SYSTEM, prompt)
#         data = LLMClient.extract_json(raw)
#         intent = int(data.get("intent", 2))
#         clar = data.get("clarification")
#         if clar in ("null", "", "None"):
#             clar = None
#         return AnalysisResult(
#             intent=intent,  # type: ignore
#             completed_query=data.get("completed_query", question),
#             clarification=clar,
#             reason=data.get("reason", ""),
#         )

#     # ── Mode FUSIONNÉ : analyse + réécriture SIR en UN seul appel ────
#     def analyze_fused(self, question: str, state: DialogueState,
#                       domain_knowledge: str = "") -> AnalysisResult:
#         """Combine MRD-Q et SIR : si l'intention est complète (2), produit
#         aussi la 'rewritten_query'. Économise un appel LLM par question."""
#         hist = "\n".join(f"- {h}" for h in state.history) or "(aucune)"
#         prompt = _FUSED_PROMPT.format(
#             history=hist, question=question,
#             domain=domain_knowledge or "(aucune)",
#         )
#         raw = self.llm.chat(_FUSED_SYSTEM, prompt)
#         data = LLMClient.extract_json(raw)
#         intent = int(data.get("intent", 2))
#         clar = data.get("clarification")
#         if clar in ("null", "", "None"):
#             clar = None
#         rq = data.get("rewritten_query") or data.get("Rewritten Query")
#         if rq in ("null", "", "None"):
#             rq = None
#         return AnalysisResult(
#             intent=intent,  # type: ignore
#             completed_query=data.get("completed_query", question),
#             clarification=clar,
#             reason=data.get("reason", ""),
#             rewritten_query=rq,
#         )


# _FUSED_SYSTEM = (
#     "Tu es un analyste BI expert. Ton rôle est de transformer une question en "
#     "requête exploitable, PAS de multiplier les questions. Tu ne demandes une "
#     "précision QUE si c'est absolument indispensable pour écrire le SQL. "
#     "En cas de doute, tu génères la requête (intent=2). "
#     "Réponds UNIQUEMENT en JSON."
# )

# _FUSED_PROMPT = """\
# Historique des échanges précédents (questions ET clarifications déjà posées) :
# {history}

# Question actuelle : "{question}"

# Connaissance métier disponible :
# {domain}

# Analyse la question en t'aidant de l'historique, puis renvoie un JSON avec
# EXACTEMENT ces clés :
# {{
#   "intent": 0 | 1 | 2,
#   "completed_query": "<question reformulée, complète et autonome>",
#   "clarification": "<UNE question courte si intent=1, sinon null>",
#   "rewritten_query": "<si intent=2 : requête canonique prête pour le SQL ; sinon null>",
#   "reason": "<courte justification>"
# }}

# RÈGLES STRICTES (dans l'ordre) :
# 1. Par défaut, choisis intent=2. Ne demande une clarification que si c'est
#    VRAIMENT impossible de faire autrement.
# 2. intent=0 UNIQUEMENT si la question n'a aucun rapport avec des données.
# 3. intent=1 SEULEMENT si une MÉTRIQUE (mesure : CA, nombre, total...) OU une
#    DIMENSION (axe : par artiste, par pays...) est totalement ABSENTE et
#    introuvable dans l'historique. Une question qui a déjà les deux est intent=2.
# 4. INTERDIT de redemander une information déjà présente dans la question ou
#    l'historique (période, métrique, axe déjà donnés). Si l'utilisateur a déjà
#    répondu à une clarification, tu DOIS passer à intent=2.
# 5. INTERDIT de poser plus d'une clarification au total. Si l'historique montre
#    qu'une clarification a déjà été posée, force intent=2 en utilisant les
#    réponses déjà fournies.
# 6. N'invente jamais un axe non demandé (ne propose pas "par pays ou par genre"
#    si l'utilisateur veut simplement le classement des artistes).

# Exemple : "Top 5 artistes par CA en 2022" contient métrique (CA), axe (artiste),
# période (2022), limite (5) -> intent=2, rewritten_query fournie, PAS de clarification."""
# """MRD-Q : Multi-Round Dialogue with Querying (papier SiriusBI §3.3).

# Deux composants :
#   1. Dialogue Analysis (§3.3.1) :
#        - intégrité sémantique : la question contient-elle métrique ET dimension ?
#        - si incomplète, complétion via le dernier tour historique qui les contient.
#   2. Intention Querying (§3.3.2) :
#        - classification I(Q) ∈ {0,1,2} :
#             0 = hors périmètre BI  -> on décline poliment
#             1 = incomplète         -> on pose une question de clarification
#             2 = complète           -> on continue le pipeline
#        - clarification guidée par la connaissance domaine retrouvée.

# Contrairement au papier, on n'entraîne aucun PLM : on s'appuie sur le
# raisonnement du LLM (cf. §3.3.1, "we leverage the robust reasoning of LLMs").
# """

################################################
################################################
################################################

# from __future__ import annotations
# from dataclasses import dataclass, field
# from typing import Literal

# from core.LLM_cloud import LLMClient

# IntentCode = Literal[0, 1, 2]


# @dataclass
# class DialogueState:
#     """Contexte multi-tours."""
#     history: list[str] = field(default_factory=list)  # questions précédentes (NL)
#     pending_question: str | None = None  # question en attente d'une clarification

#     def add(self, question: str) -> None:
#         self.history.append(question)


# @dataclass
# class AnalysisResult:
#     intent: IntentCode
#     completed_query: str        # question complétée (sémantiquement complète)
#     clarification: str | None   # question à poser si intent == 1
#     reason: str                 # justification (debug / rapport)
#     rewritten_query: str | None = None  # réécriture SIR (mode fusionné, si intent=2)


# # ── Prompts mode standard ────────────────────────────────────────────────────

# _SYSTEM = """\
# Tu es un analyste BI expert pour une entreprise industrielle et pharmaceutique.
# Tu analyses des questions en français pour un assistant Business Intelligence
# qui interroge un data warehouse (dialecte SQL : MSSQL / SQL Server).

# PÉRIMÈTRE MÉTIER — les sujets suivants sont TOUS dans le domaine BI :
# - Ventes, chiffre d'affaires (CA), facturation, factures, lignes de vente
# - Commandes clients, livraisons, retours, bons de livraison
# - Recouvrement, créances, impayés, retards de paiement, délais de règlement
# - Achats, fournisseurs, ordres d'achat, réceptions
# - Production, ordres de fabrication (OF), déclarations, consommation, stocks
# - Articles, produits, familles, coûts, prix, marges
# - Clients, régions, marchés, segments
# - Temps, périodes, mois, trimestres, semestres, années
# - Finances, devises, taux de change

# RÈGLE ABSOLUE : Toute question portant sur ces sujets métier est BI
# (intent 1 ou 2), JAMAIS intent=0.
# intent=0 UNIQUEMENT pour : météo, cuisine, blagues, politique, sport, etc.
# — tout ce qui est clairement hors entreprise.

# Réponds UNIQUEMENT en JSON valide.
# """

# _PROMPT = """\
# Historique des questions précédentes (du plus ancien au plus récent) :
# {history}

# Question actuelle : "{question}"

# Connaissance métier disponible :
# {domain}

# Analyse la question actuelle. Rappel des règles d'intent :
# - intent=2 : la question contient UNE MÉTRIQUE (mesure : nombre, montant,
#   taux, délai, total, moyenne, max, min...) ET UNE DIMENSION (axe : par client,
#   par trimestre, par région, par article...). → GÉNÉRER le SQL.
# - intent=1 : il manque vraiment une métrique OU une dimension, et l'historique
#   ne permet pas de compléter. Pose UNE question de clarification.
# - intent=0 : sujet totalement hors domaine métier (voir SYSTÈME).

# EXEMPLES de classification correcte :
#   "Quel trimestre 2024 a eu le plus de retards de paiement ?"
#     → métrique=COUNT(retards), dimension=trimestre 2024 → intent=2
#   "Quel est le CA par client en 2025 ?"
#     → métrique=CA, dimension=client/2025 → intent=2
#   "Montre-moi les ventes"
#     → métrique=ventes (implicite : montant ou quantité), dimension manquante
#     → intent=1 (demander la période ou l'axe)
#   "Raconte-moi une blague"
#     → hors domaine → intent=0

# Renvoie un JSON avec EXACTEMENT ces clés :
# {{
#   "intent": 0 | 1 | 2,
#   "completed_query": "<question reformulée, complète et autonome, ou la question originale>",
#   "clarification": "<UNE question courte si intent=1, sinon null>",
#   "reason": "<courte justification en français>"
# }}
# """


# class MRDQ:
#     def __init__(self, llm: LLMClient | None = None):
#         self.llm = llm or LLMClient()

#     def analyze(self, question: str, state: DialogueState,
#                 domain_knowledge: str = "") -> AnalysisResult:
#         hist = "\n".join(f"- {h}" for h in state.history) or "(aucune)"
#         prompt = _PROMPT.format(
#             history=hist, question=question,
#             domain=domain_knowledge or "(aucune)",
#         )
#         raw = self.llm.chat(_SYSTEM, prompt)
#         data = LLMClient.extract_json(raw)
#         intent = int(data.get("intent", 2))
#         clar = data.get("clarification")
#         if clar in ("null", "", "None", None):
#             clar = None

#         # ── Garde-fou post-LLM ──────────────────────────────────────────────
#         # Si le LLM classe intent=0 une question qui contient un mot-clé métier
#         # connu, on force intent=1 (au minimum) pour éviter un rejet silencieux.
#         if intent == 0 and _looks_like_bi(question):
#             intent = 1
#             clar = clar or "Pourriez-vous préciser la métrique ou la période souhaitée ?"

#         return AnalysisResult(
#             intent=intent,  # type: ignore
#             completed_query=data.get("completed_query") or question,
#             clarification=clar,
#             reason=data.get("reason", ""),
#         )

#     # ── Mode FUSIONNÉ : analyse + réécriture SIR en UN seul appel ────────────
#     def analyze_fused(self, question: str, state: DialogueState,
#                       domain_knowledge: str = "") -> AnalysisResult:
#         """Combine MRD-Q et SIR : si l'intention est complète (2), produit
#         aussi la 'rewritten_query'. Économise un appel LLM par question."""
#         hist = "\n".join(f"- {h}" for h in state.history) or "(aucune)"
#         prompt = _FUSED_PROMPT.format(
#             history=hist, question=question,
#             domain=domain_knowledge or "(aucune)",
#         )
#         raw = self.llm.chat(_FUSED_SYSTEM, prompt)
#         data = LLMClient.extract_json(raw)
#         intent = int(data.get("intent", 2))
#         clar = data.get("clarification")
#         if clar in ("null", "", "None", None):
#             clar = None
#         rq = data.get("rewritten_query") or data.get("Rewritten Query")
#         if rq in ("null", "", "None", None):
#             rq = None

#         # ── Garde-fou post-LLM ──────────────────────────────────────────────
#         if intent == 0 and _looks_like_bi(question):
#             intent = 1
#             clar = clar or "Pourriez-vous préciser la métrique ou la période souhaitée ?"

#         return AnalysisResult(
#             intent=intent,  # type: ignore
#             completed_query=data.get("completed_query") or question,
#             clarification=clar,
#             reason=data.get("reason", ""),
#             rewritten_query=rq,
#         )


# # ── Garde-fou lexical : détecte les questions BI mal classées intent=0 ───────

# # Mots-clés métier qui signalent qu'une question est BI, même si le LLM
# # l'a classée intent=0. La liste est volontairement large et accentuée.
# _BI_KEYWORDS: frozenset[str] = frozenset({
#     # Vente / CA
#     "ca", "chiffre", "affaires", "vente", "ventes", "facture", "factures",
#     "facturation", "montant", "prix", "marge", "marges", "recette",
#     "revenu", "revenus",
#     # Commande / livraison
#     "commande", "commandes", "livraison", "livraisons", "bl",
#     "bon", "livrer", "expedier",
#     # Recouvrement / paiement
#     "paiement", "paiements", "retard", "retards", "impaye", "impayes",
#     "creance", "creances", "recouvrement", "reglement", "echeance",
#     "encaissement",
#     # Achat / fournisseur
#     "achat", "achats", "fournisseur", "fournisseurs", "commande fournisseur",
#     "reception", "receptions",
#     # Production / stock
#     "production", "fabrication", "of", "stock", "stocks", "lot", "lots",
#     "peremption", "consommation", "arret", "arrets", "declaration",
#     # Finance
#     "devise", "taux", "change", "budget",
#     # Clients / articles
#     "client", "clients", "article", "articles", "produit", "produits",
#     "famille", "region", "regions",
#     # Temps
#     "mois", "trimestre", "semestre", "annee", "annees", "periode", "date",
#     "2024", "2025", "2026",
#     # Agrégats
#     "total", "somme", "moyenne", "nombre", "count", "sum", "avg",
#     "max", "min", "cumul",
# })


# def _looks_like_bi(question: str) -> bool:
#     """Retourne True si la question contient au moins un mot-clé métier connu.
#     Utilisé comme garde-fou quand le LLM rend un intent=0 suspect."""
#     tokens = set(
#         question.lower()
#         .replace("'", " ").replace("é", "e").replace("è", "e")
#         .replace("ê", "e").replace("à", "a").replace("ô", "o")
#         .replace("û", "u").replace("ç", "c").replace("î", "i")
#         .split()
#     )
#     return bool(tokens & _BI_KEYWORDS)


# # ── Prompts mode FUSIONNÉ ────────────────────────────────────────────────────

# _FUSED_SYSTEM = """\
# Tu es un analyste BI expert pour une entreprise industrielle et pharmaceutique.
# Tu transformes des questions en requêtes exploitables pour un data warehouse
# (dialecte SQL : MSSQL / SQL Server).

# PÉRIMÈTRE MÉTIER — les sujets suivants sont TOUS dans le domaine BI :
# - Ventes, chiffre d'affaires (CA), facturation, factures, lignes de vente
# - Commandes clients, livraisons, retours, bons de livraison
# - Recouvrement, créances, impayés, retards de paiement, délais de règlement
# - Achats, fournisseurs, ordres d'achat, réceptions
# - Production, ordres de fabrication (OF), déclarations, consommation, stocks
# - Articles, produits, familles, coûts, prix, marges
# - Clients, régions, marchés, segments
# - Temps, périodes, mois, trimestres, semestres, années
# - Finances, devises, taux de change

# RÈGLE ABSOLUE : Toute question portant sur ces sujets métier est BI
# (intent 1 ou 2), JAMAIS intent=0.
# intent=0 UNIQUEMENT pour : météo, cuisine, blagues, politique, sport, etc.

# Ton rôle est de générer (intent=2), PAS de multiplier les clarifications.
# Tu ne demandes une précision QUE si c'est absolument indispensable.
# Réponds UNIQUEMENT en JSON valide.
# """

# _FUSED_PROMPT = """\
# Historique des échanges précédents (questions ET clarifications déjà posées) :
# {history}

# Question actuelle : "{question}"

# Connaissance métier disponible :
# {domain}

# Analyse la question, puis renvoie un JSON avec EXACTEMENT ces clés :
# {{
#   "intent": 0 | 1 | 2,
#   "completed_query": "<question reformulée, complète et autonome>",
#   "clarification": "<UNE question courte si intent=1, sinon null>",
#   "rewritten_query": "<si intent=2 : requête canonique prête pour le SQL ; sinon null>",
#   "reason": "<courte justification en français>"
# }}

# RÈGLES STRICTES (dans l'ordre de priorité) :
# 1. Par défaut, choisis intent=2. Ne demande une clarification que si c'est
#    VRAIMENT impossible de faire autrement.
# 2. intent=0 UNIQUEMENT si le sujet est clairement hors domaine métier
#    (météo, sport, politique, blagues...). JAMAIS pour un sujet BI.
# 3. intent=1 SEULEMENT si une MÉTRIQUE (mesure quantitative : CA, nombre,
#    montant, taux, délai, total, moyenne...) OU une DIMENSION (axe : par
#    client, par trimestre, par région...) est totalement absente ET
#    introuvable dans l'historique.
# 4. INTERDIT de redemander une information déjà présente dans la question
#    ou l'historique. Si l'utilisateur a répondu à une clarification → intent=2.
# 5. INTERDIT de poser plus d'une clarification au total par tour.
#    Si l'historique montre qu'une clarification a déjà été posée → force intent=2.
# 6. N'invente jamais un axe non demandé.

# EXEMPLES de classification correcte :
#   "Quel trimestre 2024 a eu le plus de retards de paiement clients ?"
#     → métrique=COUNT(retards/impayés), dimension=trimestre+année+client
#     → intent=2, rewritten_query fournie, clarification=null

#   "Quel est le CA par région en 2025 ?"
#     → intent=2

#   "Montre-moi les ventes"
#     → dimension manquante → intent=1, demander la période

#   "Quelle est la météo à Paris ?"
#     → hors domaine → intent=0
# """


"""
MRD-Q : Multi-Round Dialogue with Querying (papier SiriusBI §3.3).

NOUVEAUTÉS v3 — DialogueState comme moteur de contexte :

  1. CONTEXTE ACTIF : l'historique n'est pas juste affiché au LLM — il est
     analysé pour extraire les dimensions/filtres déjà établis (région, période,
     article...) et les injecter automatiquement dans la question courante.

  2. MOIS VIDES : "par mois" signifie TOUS les mois, y compris ceux sans données.
     Le LLM est instruit de produire une rewritten_query qui le mentionne
     explicitement → le générateur SQL saura qu'il faut un LEFT JOIN ou CTE.

  3. CLARIFICATION CIBLÉE : si une dimension est ambiguë (ex: "les ventes" sans
     savoir si c'est CA_TND ou Quantite), le LLM pose UNE question précise,
     mémorisée dans pending_question, et la réponse est fusionnée au tour suivant.

  4. RÉPONSE ENRICHIE : completed_query porte les éléments de contexte hérités
     de l'historique explicitement formulés (ex: "...par région comme précédemment").
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

from core.llm import LLMClient

IntentCode = Literal[0, 1, 2]


# ════════════════════════════════════════════════════════════════════════════
#  DialogueState — contexte actif multi-tour
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ActiveContext:
    """Éléments de contexte extraits des tours précédents et réutilisables."""
    dimensions: list[str] = field(default_factory=list)   # ex: ["région", "mois"]
    filters: list[str] = field(default_factory=list)      # ex: ["année=2025"]
    metric: str | None = None                              # ex: "CA_TND"
    groupby: list[str] = field(default_factory=list)      # ex: ["Code_Region"]
    all_periods: bool = False                              # "par mois" → inclure les vides


@dataclass
class DialogueState:
    """Contexte multi-tours enrichi.

    Attributs :
      history          — liste des (question_originale, completed_query, intent)
      pending_question — question en attente de clarification
      active_context   — dimensions/filtres hérités des tours précédents
    """
    history: list[dict] = field(default_factory=list)
    pending_question: str | None = None
    active_context: ActiveContext = field(default_factory=ActiveContext)

    def add(self, original: str, completed: str, intent: int) -> None:
        self.history.append({
            "original": original,
            "completed": completed,
            "intent": intent,
        })

    def history_text(self) -> str:
        """Formate l'historique pour injection dans les prompts LLM."""
        if not self.history:
            return "(aucun)"
        lines = []
        for i, h in enumerate(self.history, 1):
            lines.append(f"Tour {i} : {h['completed']}")
        return "\n".join(lines)

    def context_summary(self) -> str:
        """Résumé du contexte actif pour injection dans les prompts."""
        ctx = self.active_context
        parts = []
        if ctx.dimensions:
            parts.append(f"Dimensions actives : {', '.join(ctx.dimensions)}")
        if ctx.filters:
            parts.append(f"Filtres actifs : {', '.join(ctx.filters)}")
        if ctx.metric:
            parts.append(f"Métrique active : {ctx.metric}")
        if ctx.groupby:
            parts.append(f"Group-by actif : {', '.join(ctx.groupby)}")
        if ctx.all_periods:
            parts.append("Périodes : TOUS les mois/trimestres, y compris les vides")
        return "\n".join(parts) if parts else "(aucun contexte hérité)"


# ════════════════════════════════════════════════════════════════════════════
#  Prompts
# ════════════════════════════════════════════════════════════════════════════

_SYSTEM = """\
Tu es un analyste BI expert pour une entreprise industrielle et pharmaceutique.
Tu analyses des questions en français pour un assistant BI qui interroge un
data warehouse SQL Server (MSSQL).

P�RIMÈTRE MÉTIER — ces sujets sont TOUS dans le domaine BI :
  Ventes, CA, facturation · Commandes, livraisons, retours
  Recouvrement, créances, retards de paiement · Achats, fournisseurs
  Production, OF, stocks · Articles, coûts, marges · Clients, régions
  Temps (mois, trimestre, semestre, année) · Devises, finances

RÈGLE ABSOLUE : intent=0 UNIQUEMENT pour météo, sport, blagues, cuisine.
Tout sujet métier ci-dessus → intent 1 ou 2.

Réponds UNIQUEMENT en JSON valide.
"""

_PROMPT = """\
═══ HISTORIQUE DES ÉCHANGES ═══
{history}

═══ CONTEXTE ACTIF HÉRITÉ ═══
{context}

═══ QUESTION ACTUELLE ═══
"{question}"

═══ CONNAISSANCE MÉTIER DISPONIBLE ═══
{domain}

━━━ INSTRUCTIONS D'ANALYSE ━━━

ÉTAPE 1 — Hériter du contexte
  • Si la question actuelle ne mentionne pas de dimension (région, client,
    article...) mais que le contexte actif en a une → hérite-la.
  • Si la question actuelle ne mentionne pas de métrique mais que le contexte
    actif en a une → hérite-la, sauf si la nouvelle question en implique
    clairement une autre.
  • Formule la completed_query en citant EXPLICITEMENT les éléments hérités
    (ex: "...par région comme dans les échanges précédents").

ÉTAPE 2 — Détecter les patterns temporels
  • "par mois" / "mois par mois" / "mensuel" → ALL_PERIODS=true :
    la requête SQL devra retourner TOUS les mois (y compris ceux sans données).
    Mentionne-le dans completed_query : "...pour tous les mois, y compris
    les mois sans données".
  • "par trimestre" / "par semestre" → même logique.

ÉTAPE 3 — Détecter les ambiguïtés
  • Si un terme est ambigu ET que l'ambiguïté change radicalement le SQL
    (ex: "ventes" = CA_TND ou Quantite_Vendue ?), pose UNE clarification.
  • Si l'ambiguïté est résolvable par le contexte ou la connaissance métier
    disponible → résous-la toi-même, ne demande pas.

ÉTAPE 4 — Classifier l'intent
  • intent=2 si la question (+ contexte hérité) contient métrique + dimension.
  • intent=1 si une clarification est vraiment nécessaire.
  • intent=0 si hors domaine métier.

Renvoie un JSON avec EXACTEMENT ces clés :
{{
  "intent": 0 | 1 | 2,
  "completed_query": "<question complète, autonome, avec contexte hérité explicite>",
  "clarification": "<UNE question courte si intent=1, sinon null>",
  "reason": "<justification courte>",
  "context_update": {{
    "dimensions": ["<dimensions détectées ou héritées>"],
    "filters": ["<filtres détectés ou hérités>"],
    "metric": "<métrique principale ou null>",
    "groupby": ["<colonnes SQL de group-by>"],
    "all_periods": true | false
  }}
}}
"""

_FUSED_SYSTEM = """\
Tu es un analyste BI expert pour une entreprise industrielle et pharmaceutique.
Tu transformes des questions en requêtes BI exploitables pour SQL Server (MSSQL).

P�RIMÈTRE MÉTIER — ces sujets sont TOUS dans le domaine BI :
  Ventes, CA, facturation · Commandes, livraisons, retours
  Recouvrement, créances, retards de paiement · Achats, fournisseurs
  Production, OF, stocks · Articles, coûts, marges · Clients, régions
  Temps (mois, trimestre, semestre, année) · Devises, finances

RÈGLE ABSOLUE : intent=0 UNIQUEMENT pour météo, sport, blagues, cuisine.
Par défaut → intent=2. Clarifie UNIQUEMENT si c'est indispensable.
Réponds UNIQUEMENT en JSON valide.
"""

_FUSED_PROMPT = """\
═══ HISTORIQUE DES ÉCHANGES ═══
{history}

═══ CONTEXTE ACTIF HÉRITÉ ═══
{context}

═══ QUESTION ACTUELLE ═══
"{question}"

═══ CONNAISSANCE MÉTIER DISPONIBLE ═══
{domain}

━━━ INSTRUCTIONS ━━━

HÉRITER DU CONTEXTE :
  • Dimensions/filtres/métriques actifs → les inclure dans completed_query
    et rewritten_query si la question courante ne les redéfinit pas.
  • Citer explicitement ce qui est hérité dans la formulation.

PATTERNS TEMPORELS :
  • "par mois" / "mensuel" / "mois par mois" → ALL_PERIODS=true
    → inclure TOUS les mois, même sans données (SQL : LEFT JOIN ou CTE de dates)
    → mentionner dans completed_query "y compris les mois sans données"
  • Même logique pour "par trimestre", "par semestre".

CLARIFICATION :
  • UNIQUEMENT si une ambiguïté bloque le SQL ET ne peut pas être résolue
    par le contexte ou la connaissance métier.
  • Interdit de redemander ce qui est déjà dans l'historique.
  • Maximum 1 clarification par tour. Si déjà posée → force intent=2.

Renvoie un JSON avec EXACTEMENT ces clés :
{{
  "intent": 0 | 1 | 2,
  "completed_query": "<question complète avec contexte hérité explicite>",
  "clarification": "<UNE question courte si intent=1, sinon null>",
  "rewritten_query": "<si intent=2 : requête canonique SQL-ready ; sinon null>",
  "reason": "<justification courte>",
  "context_update": {{
    "dimensions": ["<dimensions détectées ou héritées>"],
    "filters": ["<filtres détectés ou hérités>"],
    "metric": "<métrique principale ou null>",
    "groupby": ["<colonnes SQL de group-by>"],
    "all_periods": true | false
  }}
}}

RÈGLES STRICTES :
1. Par défaut intent=2.
2. intent=0 uniquement si clairement hors domaine.
3. intent=1 uniquement si métrique OU dimension totalement absente
   ET non résolvable par le contexte.
4. Interdit de poser une clarification déjà posée dans l'historique.
5. Ne pas inventer d'axe non demandé.

EXEMPLES :
  Tour 1 : "CA par région en 2025" → intent=2, groupby=[Code_Region], metric=CA_TND
  Tour 2 : "Et par mois ?"
    → hérite région + 2025, ajoute mois, ALL_PERIODS=true
    → completed_query = "CA par région ET par mois en 2025,
       y compris les mois sans données, comme dans les échanges précédents"
    → intent=2

  Tour 1 : "Quelles régions ont le plus de retards ?"
    → intent=2, groupby=[Code_Region], metric=COUNT(retards)
  Tour 2 : "Et en 2024 ?"
    → hérite région + métrique retards, filtre année=2024
    → completed_query = "Régions avec le plus de retards de paiement en 2024"
    → intent=2
"""


# ════════════════════════════════════════════════════════════════════════════
#  Garde-fou lexical (inchangé)
# ════════════════════════════════════════════════════════════════════════════

_BI_KEYWORDS: frozenset[str] = frozenset({
    "ca", "chiffre", "affaires", "vente", "ventes", "facture", "factures",
    "facturation", "montant", "prix", "marge", "marges", "recette",
    "revenu", "revenus", "commande", "commandes", "livraison", "livraisons",
    "bl", "bon", "livrer", "paiement", "paiements", "retard", "retards",
    "impaye", "impayes", "creance", "creances", "recouvrement", "reglement",
    "echeance", "encaissement", "achat", "achats", "fournisseur", "fournisseurs",
    "reception", "receptions", "production", "fabrication", "of", "stock",
    "stocks", "lot", "lots", "peremption", "consommation", "arret", "arrets",
    "declaration", "devise", "taux", "change", "budget", "client", "clients",
    "article", "articles", "produit", "produits", "famille", "region", "regions",
    "mois", "trimestre", "semestre", "annee", "annees", "periode", "date",
    "2023", "2024", "2025", "2026",
    "total", "somme", "moyenne", "nombre", "count", "sum", "avg", "max", "min", "cumul",
})


def _looks_like_bi(question: str) -> bool:
    tokens = set(
        question.lower()
        .replace("'", " ").replace("é", "e").replace("è", "e")
        .replace("ê", "e").replace("à", "a").replace("ô", "o")
        .replace("û", "u").replace("ç", "c").replace("î", "i")
        .split()
    )
    return bool(tokens & _BI_KEYWORDS)


# ════════════════════════════════════════════════════════════════════════════
#  AnalysisResult
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class AnalysisResult:
    intent: IntentCode
    completed_query: str
    clarification: str | None
    reason: str
    rewritten_query: str | None = None
    context_update: dict | None = None   # ← NOUVEAU : mise à jour du contexte


# ════════════════════════════════════════════════════════════════════════════
#  MRDQ
# ════════════════════════════════════════════════════════════════════════════

class MRDQ:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def _apply_context_update(self, state: DialogueState,
                               update: dict | None) -> None:
        """Met à jour le contexte actif depuis le context_update du LLM."""
        if not update:
            return
        ctx = state.active_context
        # Fusionner (ne pas écraser si le LLM renvoie une liste vide)
        if update.get("dimensions"):
            ctx.dimensions = update["dimensions"]
        if update.get("filters"):
            ctx.filters = update["filters"]
        if update.get("metric") not in (None, "null", ""):
            ctx.metric = update["metric"]
        if update.get("groupby"):
            ctx.groupby = update["groupby"]
        if "all_periods" in update:
            ctx.all_periods = bool(update["all_periods"])

    def analyze(self, question: str, state: DialogueState,
                domain_knowledge: str = "") -> AnalysisResult:
        prompt = _PROMPT.format(
            history=state.history_text(),
            context=state.context_summary(),
            question=question,
            domain=domain_knowledge or "(aucune)",
        )
        raw = self.llm.chat(_SYSTEM, prompt)
        data = LLMClient.extract_json(raw)

        intent = int(data.get("intent", 2))
        clar = data.get("clarification")
        if clar in ("null", "", "None", None):
            clar = None

        # Garde-fou
        if intent == 0 and _looks_like_bi(question):
            intent = 1
            clar = clar or "Pourriez-vous préciser la métrique ou la période souhaitée ?"

        result = AnalysisResult(
            intent=intent,  # type: ignore
            completed_query=data.get("completed_query") or question,
            clarification=clar,
            reason=data.get("reason", ""),
            context_update=data.get("context_update"),
        )

        # Mettre à jour le contexte actif dès maintenant (même pour intent=1,
        # pour que la clarification suivante hérite du contexte partiel)
        self._apply_context_update(state, result.context_update)
        return result

    def analyze_fused(self, question: str, state: DialogueState,
                      domain_knowledge: str = "") -> AnalysisResult:
        prompt = _FUSED_PROMPT.format(
            history=state.history_text(),
            context=state.context_summary(),
            question=question,
            domain=domain_knowledge or "(aucune)",
        )
        raw = self.llm.chat(_FUSED_SYSTEM, prompt)
        data = LLMClient.extract_json(raw)

        intent = int(data.get("intent", 2))
        clar = data.get("clarification")
        if clar in ("null", "", "None", None):
            clar = None
        rq = data.get("rewritten_query") or data.get("Rewritten Query")
        if rq in ("null", "", "None", None):
            rq = None

        # Garde-fou
        if intent == 0 and _looks_like_bi(question):
            intent = 1
            clar = clar or "Pourriez-vous préciser la métrique ou la période souhaitée ?"

        result = AnalysisResult(
            intent=intent,  # type: ignore
            completed_query=data.get("completed_query") or question,
            clarification=clar,
            reason=data.get("reason", ""),
            rewritten_query=rq,
            context_update=data.get("context_update"),
        )

        self._apply_context_update(state, result.context_update)
        return result