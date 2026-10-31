"""
Construction de la Knowledge Base à partir du schéma de la base métier.

On reproduit la hiérarchie de la Figure 4 du papier SiriusBI :
    Database -> Table -> Column -> Value
avec, pour chaque entrée, deux champs relationnels (Figure 5) :
    anc_label : le LABEL du parent (ex. "Table")
    anc_name  : le NOM concret du parent (ex. "Invoice")

Ce graphe léger permet la traversée multi-hop sans base de graphe dédiée.

Types de KB (4 du papier) :
  1. db_metadata          -> généré ici automatiquement depuis le schéma
  2. domain_knowledge     -> termes métier (à enrichir : pharma chez Unimed)
  3. task_material         -> exemples question -> SQL (few-shot)
  4. conversation_history  -> rempli au runtime (non couvert ici)

NOUVEAUTÉS v2 :
  - Chaque entrée domain_knowledge porte maintenant deux champs dans payload :
      tables_ref : list[str]  — tables directement référencées par ce terme
      keywords   : list[str]  — mots-clés métier que l'utilisateur peut taper
    Ces champs sont exploités par KnowledgeStore._score_paper() pour propager
    un bonus de score ("domain boost") vers les tables et colonnes concernées
    quand le Term matche fortement la question.
  - QueryCache : cache pré-pipeline par similarité sémantique (cosine > seuil)
    → si une question identique/proche a déjà été résolue, on renvoie le SQL
    directement sans refaire le retrieval + génération.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

from core.db import DatabaseConnector, TableInfo

KBLabel = Literal["Database", "Table", "Column", "Term", "Task"]


@dataclass
class KBEntry:
    """Une entrée de la Knowledge Base."""
    name: str                       # nom canonique (ex. "Invoice.Total")
    label: KBLabel                  # type d'entrée
    description: str                # texte décrivant l'entrée (indexé)
    anc_name: str | None = None     # nom du parent
    anc_label: str | None = None    # label du parent
    kb_type: str = "db_metadata"    # 1 des 4 types
    payload: dict = field(default_factory=dict)  # infos additionnelles

    def text_for_embedding(self) -> str:
        """Texte concaténé utilisé pour l'embedding et le BM25.
        Pour les Domain entries, on ajoute les keywords pour enrichir
        l'espace sémantique BM25 sans polluer la description affichée."""
        parts = [self.name, self.label, self.description]
        if self.anc_name:
            parts.append(f"appartient à {self.anc_label} {self.anc_name}")
        # Enrichissement BM25 : les keywords sont ajoutés en clair
        kws = self.payload.get("keywords")
        if kws:
            parts.append(" ".join(kws))
        return " | ".join(p for p in parts if p)


def build_metadata_entries(schema: list[TableInfo], db_name: str = "TestDB_DataWarehouse") -> list[KBEntry]:
    """Transforme le schéma introspecté en entrées KB hiérarchiques."""
    entries: list[KBEntry] = []

    # Niveau Database (racine, sans parent)
    entries.append(KBEntry(
        name=db_name, label="Database",
        description=f"Base de données métier {db_name}.",
        kb_type="db_metadata",
    ))

    for t in schema:
        # Niveau Table -> parent = Database
        fk_desc = ""
        if t.foreign_keys:
            refs = ", ".join(
                f"{','.join(fk['constrained_columns'])}→{fk['referred_table']}"
                for fk in t.foreign_keys
            )
            fk_desc = f" Clés étrangères : {refs}."
        entries.append(KBEntry(
            name=t.name, label="Table",
            description=f"Table {t.name} ({len(t.columns)} colonnes).{fk_desc}",
            anc_name=db_name, anc_label="Database",
            kb_type="db_metadata",
            payload={"foreign_keys": t.foreign_keys,
                     "columns": [c.name for c in t.columns]},
        ))

        # Niveau Column -> parent = Table
        for c in t.columns:
            tags = []
            if c.primary_key:
                tags.append("clé primaire")
            if not c.nullable:
                tags.append("non nul")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            entries.append(KBEntry(
                name=f"{t.name}.{c.name}", label="Column",
                description=f"Colonne {c.name} de type {c.type} dans la table {t.name}{tag_str}.",
                anc_name=t.name, anc_label="Table",
                kb_type="db_metadata",
                payload={"table": t.name, "column": c.name, "type": c.type},
            ))
    return entries


def build_domain_entries() -> list[KBEntry]:
    """
    Connaissance domaine (type 2).

    Format étendu : (mot_cle, alternative, description, tables_ref, keywords)
      - mot_cle    : libellé principal du terme (nom de l'entrée KB)
      - alternative: synonymes séparés par virgule
      - description: description complète (tables, colonnes, pièges)
      - tables_ref : liste des noms de tables directement ciblées par ce terme
                     → utilisée par KnowledgeStore pour propager un bonus de
                     score aux tables concernées quand ce Term matche
      - keywords   : mots-clés métier supplémentaires pour le BM25
                     → injectés dans text_for_embedding()
    """
    domain = [
        # ── Client ──────────────────────────────────────────────────────────
        ("Client", "acheteur, tiers client, compte client",
         "DIM_Client contient l'identite et la segmentation de chaque client : "
         "Code_Client (PK), Nom_Client, Classe_Client, Code_Marche, "
         "Code_Type_Client, Status_Client, Region, Code_Devise.",
         ["DIM_Client"],
         ["client", "acheteur", "tiers", "compte", "nom client", "code client"]),

        ("Classe client", "segment client, categorie de clientele",
         "DIM_Classe_Client liste les classes de clients (Code_classe_client, "
         "Description) ; DIM_Client.Classe_Client contient la valeur mais sans "
         "contrainte FK declaree vers cette table.",
         ["DIM_Classe_Client", "DIM_Client"],
         ["classe", "segment", "categorie", "clientele"]),

        ("Type de client", "categorie de client",
         "DIM_Type_Client liste les types de client (Code_Type_Client, "
         "Description), reference par DIM_Client.Code_Type_Client.",
         ["DIM_Type_Client", "DIM_Client"],
         ["type client", "categorie client"]),

        ("Marche / segment de vente", "canal de vente, marche cible",
         "DIM_Type_Marche liste les types de marche (Code_Type_Marche, "
         "Description), reference par DIM_Client.Code_Marche.",
         ["DIM_Type_Marche", "DIM_Client"],
         ["marche", "canal", "segment", "export", "local"]),

        ("Region / adresse client", "zone geographique, secteur, territoire, ville, pays",
         "DIM_Region contient les adresses des clients : Code_Region (PK), "
         "Code_Client (FK vers DIM_Client), Adresse, Ville, Pays, Type_Adresse "
         "(Facturation/Livraison/Siege).",
         ["DIM_Region", "DIM_Client"],
         ["region", "ville", "pays", "adresse", "zone", "secteur", "territoire"]),

        # ── Article / produit ────────────────────────────────────────────────
        ("Article / produit", "reference, SKU, produit fini",
         "DIM_Article decrit chaque article : Code_Article (PK), Description, "
         "Famille_Article, Type_Article, Prix_Vente, Unite_Mesure, AMM "
         "(autorisation de mise sur le marche), Ligne_Produit, Status.",
         ["DIM_Article"],
         ["article", "produit", "reference", "sku", "medicament", "prix vente", "AMM"]),

        ("Famille d'article", "categorie produit, gamme de produits",
         "DIM_Famille_Article liste les familles d'articles (Code_Famille, "
         "Description) ; DIM_Article.Famille_Article est une colonne texte "
         "libre, sans FK declaree vers cette table.",
         ["DIM_Famille_Article", "DIM_Article"],
         ["famille", "gamme", "categorie produit"]),

        ("Type d'article", "nature de l'article (fini/semi-fini/matiere premiere)",
         "DIM_Type_Article liste les types d'article (Code_Type_Article, "
         "Description).",
         ["DIM_Type_Article", "DIM_Article"],
         ["type article", "fini", "semi-fini", "matiere premiere", "nature"]),

        ("Unite de mesure", "unite, UM (kg, litre, piece...)",
         "DIM_Unite_Mesure liste les unites de mesure standard "
         "(Code_Unite_Mesure, Description).",
         ["DIM_Unite_Mesure"],
         ["unite", "mesure", "kg", "litre", "piece", "UM"]),

        ("Unite de production", "UP, ligne de production",
         "Dim_Unite_Production liste les unites de production (Code_Unite, "
         "Description).",
         ["Dim_Unite_Production"],
         ["unite production", "UP", "ligne production"]),

        ("Validite d'une unite de production", "periode de validite UP pour un article",
         "Dim_Validite_UP donne, pour un article donne, la periode de validite "
         "(Date_Debut/Date_Fin) d'une unite de production (Unite_Production).",
         ["Dim_Validite_UP"],
         ["validite", "periode", "date debut", "date fin", "UP article"]),

        ("Cout de revient d'un article", "cout matiere/MO/FGV/FGF, cout standard",
         "DIM_Cout_Article detaille le cout de revient par article ET par "
         "groupe de cout (Groupe_Cout : MP/MO/FGV/FGF/ST), avec une date de "
         "changement (Date_Changement) : Cout_Total, Cout_Matiere, Cout_MO, "
         "Cout_FGV, Cout_FGF, Cout_ST (+ variantes _NI).",
         ["DIM_Cout_Article"],
         ["cout", "revient", "standard", "matiere", "MO", "FGV", "FGF", "cout article"]),

        # ── Fournisseur / achat ──────────────────────────────────────────────
        ("Fournisseur", "vendeur, tiers fournisseur",
         "DIM_Fournisseur contient l'identite des fournisseurs : "
         "Code_Fournisseur (PK), Nom_Fournisseur, Pays, Ville, Devise, Type "
         "(Local/Import), Etat.",
         ["DIM_Fournisseur"],
         ["fournisseur", "vendeur", "tiers", "import", "local"]),

        ("Type de fournisseur", "categorie de fournisseur",
         "DIM_Type_Fournisseur liste les types de fournisseur "
         "(Code_Type_Fournisseur, Description).",
         ["DIM_Type_Fournisseur"],
         ["type fournisseur", "categorie fournisseur"]),

        ("Ordre d'achat", "commande fournisseur, OA, demande d'achat (DA)",
         "FACT_Ordre_Achat enregistre les commandes passees aux fournisseurs : "
         "Num_DA, Num_Commande, Code_Fournisseur, Code_Article, Qte_Commandee, "
         "Cout_Achat, Statut_Ordre, Date_Commande, Credit_Terms.",
         ["FACT_Ordre_Achat"],
         ["ordre achat", "commande fournisseur", "OA", "DA", "demande achat",
          "quantite commandee", "cout achat"]),

        ("Reception fournisseur", "reception de marchandise, bon de reception (BR)",
         "FACT_Assoc_Reception enregistre les receptions de marchandises "
         "fournisseurs : Num_Reception, Num_Commande, Code_Fournisseur, "
         "Code_Article, Qte_reçue, Date_Reception, Cout_Fact_TND.",
         ["FACT_Assoc_Reception"],
         ["reception", "BR", "bon reception", "marchandise", "quantite recue"]),

        # ── Devise / finance transverse ──────────────────────────────────────
        ("Devise", "monnaie, currency",
         "DIM_Devise liste les devises utilisees (Code_Devise, Description, "
         "Taux_Change, Date_Fin).",
         ["DIM_Devise"],
         ["devise", "monnaie", "currency", "EUR", "USD", "TND"]),

        ("Taux de change", "conversion devise vers TND",
         "Colonne Taux_Change presente dans plusieurs tables de faits "
         "(FACT_Vente, FACT_Commande_Client, FACT_Assoc_Retour, "
         "FACT_Ordre_Achat, FACT_Recouvrement_Client, FACT_Assoc_Reception) "
         "pour convertir un montant en devise d'origine vers le TND.",
         ["FACT_Vente", "FACT_Commande_Client", "FACT_Ordre_Achat",
          "FACT_Recouvrement_Client", "DIM_Devise"],
         ["taux change", "conversion", "devise", "TND", "euro", "dollar"]),

        ("Chiffre d'affaires (CA)", "revenu, ventes, recette",
         "FACT_Vente.CA_TND est le CA deja converti en dinars tunisiens (a "
         "utiliser pour toute somme multi-devises) ; CA_Devise_Ope est le CA "
         "en devise d'origine (ne jamais sommer directement entre lignes de "
         "devises differentes).",
         ["FACT_Vente"],
         ["CA", "chiffre affaires", "revenu", "ventes", "recette", "CA_TND",
          "montant", "total vente"]),

        # ── Site / organisation ──────────────────────────────────────────────
        ("Site", "depot, usine, entrepot",
         "DIM_Site liste les sites de l'entreprise (Code_Site, Description, "
         "Status).",
         ["DIM_Site"],
         ["site", "depot", "usine", "entrepot", "localisation"]),

        ("Departement (production)", "atelier, unite organisationnelle de production",
         "DIM_Departement liste les departements de production (Code_Dpt, "
         "Description, Capacite_Mch, Unite_Prod).",
         ["DIM_Departement"],
         ["departement", "atelier", "unite organisationnelle", "capacite"]),

        ("Domaine commercial", "marche geographique, local vs export",
         "DIM_Domaine liste les domaines commerciaux (Code_Domaine, "
         "Description, Type_Domaine).",
         ["DIM_Domaine"],
         ["domaine", "commercial", "marche geographique", "export", "local"]),

        # ── Production / industriel ──────────────────────────────────────────
        ("Centre de charge", "atelier, poste de charge",
         "DIM_Centre_Charge decrit les centres de charge de production : "
         "Code_centre_charge (PK), Machine, Departement, et une serie de taux "
         "horaires (Taux_MO, Taux_Rendement, Taux_Utilisation, Taux_Reglage...).",
         ["DIM_Centre_Charge"],
         ["centre charge", "atelier", "poste charge", "machine", "taux utilisation",
          "taux rendement", "MO"]),

        ("Gamme de fabrication", "gamme operatoire, routing, sequence d'operations",
         "DIM_Gamme decrit les operations d'une gamme de fabrication : "
         "Code_Gamme, Operation, Machine, Temps_Reglage, Temps_Execution, "
         "Cadence, Centre_Charge.",
         ["DIM_Gamme"],
         ["gamme", "fabrication", "routing", "operation", "temps execution",
          "cadence", "reglage"]),

        ("Operation (referentiel production)", "poste operatoire, etape de gamme",
         "DIM_Operations liste les operations de production (N_Op (PK), "
         "Operation, Machine, Centre_Charge, Rendement theorique).",
         ["DIM_Operations"],
         ["operation", "poste", "etape", "rendement", "theorique"]),

        ("Nomenclature", "BOM, recette de fabrication, composants",
         "DIM_Nomenclature decrit la composition d'un article : "
         "Code_Nomenclature + Formule (cle composite), Compose, Composant, "
         "Quantite_par, Rebut.",
         ["DIM_Nomenclature"],
         ["nomenclature", "BOM", "recette", "composant", "composition",
          "quantite par", "rebut"]),

        ("Ordre de fabrication (OF)", "OF, lot de production, ordre de fab",
         "L'OF (colonne N_OF) traverse plusieurs tables de faits : "
         "FACT_PROD_Consommation (consommation matiere), "
         "FACT_PROD_Declaration (declarations d'execution), "
         "FACT_PROD_Valorisation (valorisation cout reel vs standard), "
         "FACT_PROD_Arret (arrets survenus pendant l'OF).",
         ["FACT_PROD_Consommation", "FACT_PROD_Declaration",
          "FACT_PROD_Valorisation", "FACT_PROD_Arret"],
         ["OF", "ordre fabrication", "lot production", "N_OF"]),

        ("Consommation matiere (production)", "consommation OF, sortie de stock matiere",
         "FACT_PROD_Consommation detaille la consommation de matiere par OF : "
         "N_OF, Code_Article_OF, Code_Nomenclature, Qte_Theorique, "
         "Qte_Terminee, Qte_prelevee, Cout_Matiere.",
         ["FACT_PROD_Consommation"],
         ["consommation", "sortie stock", "matiere", "qte theorique",
          "qte terminee", "qte prelevee"]),

        ("Declaration de production", "temps de production, rendement, rebut declare",
         "FACT_PROD_Declaration enregistre les declarations d'execution par "
         "operation : N_OF, N_ID (PK), N_OP, Qte_Terminee, Qte_Rejetee, "
         "[%Rendement], Temps_Exec_Reel, Cout_MO_Reel.",
         ["FACT_PROD_Declaration"],
         ["declaration", "temps production", "rendement", "rebut", "rejet",
          "Qte_Rejetee", "Cout_MO_Reel"]),

        ("Arret de production", "panne, incident, temps d'arret machine",
         "FACT_PROD_Arret enregistre les arrets de production : N_Operation "
         "(PK), N_OF, Code_Motif (motif d'arret), Duree, Date_Debut, "
         "Date_Fin, Machine, Departement.",
         ["FACT_PROD_Arret"],
         ["arret", "panne", "incident", "duree arret", "machine", "motif arret"]),

        ("Motif d'arret de production", "cause de panne, incident production",
         "DIM_Motif_Arret_Prod liste les motifs d'arret de production "
         "(Code_Motif, Description), reference par FACT_PROD_Arret.Code_Motif.",
         ["DIM_Motif_Arret_Prod", "FACT_PROD_Arret"],
         ["motif", "arret", "panne", "cause", "incident"]),

        ("Valorisation de production", "cout de production reel vs standard",
         "FACT_PROD_Valorisation valorise chaque OF : N_OF (PK), Valeur_Cons, "
         "Valeur_MO, Valeur_FGV, Cout_Std, Cout_Std_MO, Cout_Std_Matiere.",
         ["FACT_PROD_Valorisation"],
         ["valorisation", "cout reel", "cout standard", "Valeur_Cons",
          "Valeur_MO", "Valeur_FGV", "ecart cout"]),

        ("Rendement de production", "efficacite, taux de rendement",
         "Le rendement apparait a plusieurs niveaux : DIM_Operations.Rendement "
         "(theorique par operation), FACT_PROD_Declaration.[%Rendement] "
         "(reel declare), FACT_PROD_Consommation.Rendement (rendement de "
         "l'OF) — bien preciser lequel est demande.",
         ["DIM_Operations", "FACT_PROD_Declaration", "FACT_PROD_Consommation"],
         ["rendement", "efficacite", "taux rendement", "theorique", "reel"]),

        ("Stock", "inventaire, quantite en stock, peremption",
         "Fact_Prod_Stocks donne le stock disponible par article/lot/site : "
         "Article, Num_Lot, Site, In_Qty_Oh (quantite en stock), Date_Exp "
         "(date de peremption), Emplacement.",
         ["Fact_Prod_Stocks"],
         ["stock", "inventaire", "lot", "peremption", "disponible",
          "quantite stock", "Date_Exp", "emplacement"]),

        # ── Vente / commande / logistique ────────────────────────────────────
        ("Vente / facturation", "chiffre d'affaires, CA, facture client, ligne de vente",
         "FACT_Vente est la table de faits au grain LIGNE DE VENTE : "
         "Client, Article, Facture, Quantite, CA_TND, CA_Devise_Ope, "
         "Marge, Prix_Unitaire.",
         ["FACT_Vente"],
         ["vente", "facture", "facturation", "CA", "chiffre affaires",
          "ligne vente", "CA_TND", "prix unitaire", "marge", "quantite vendue",
          "facture client", "montant facture", "moyen facture", "average"]),

        ("Commande client", "ordre de vente, ligne de commande",
         "FACT_Commande_Client est au grain LIGNE DE COMMANDE (Ligne_Num) : "
         "Num_Commande, Code_Client, Code_Article, Quantite_Commandee, "
         "Total_Quantite_Livree, Total_Montant_Facture, Statut, "
         "Date_Commande, Date_Echeance.",
         ["FACT_Commande_Client"],
         ["commande", "ordre vente", "ligne commande", "quantite commandee",
          "montant facture", "statut commande", "livraison"]),

        ("Statut de commande", "etat de la commande",
         "DIM_Statut_Commande liste les statuts possibles d'une commande "
         "(Statut, Description_Statut).",
         ["DIM_Statut_Commande"],
         ["statut", "etat", "commande", "en cours", "cloture"]),

        ("Bon de livraison", "BL, expedition, livraison client",
         "FACT_Assoc_Bon_Livraison associe une ligne de commande a sa "
         "livraison : Num_Commande, Num_BL, Quantite_Livree, "
         "Date_Livraison_BL, ID_Livraison (PK), Code_Site.",
         ["FACT_Assoc_Bon_Livraison"],
         ["bon livraison", "BL", "expedition", "livraison", "quantite livree",
          "date livraison"]),

        ("Retour client", "avoir, retour de marchandise, SAV",
         "FACT_Assoc_Retour enregistre les retours clients : Num_Commande, "
         "Code_Client, Code_Article, Quantite_Retournee, Montant_Retour, "
         "Motif_Retour, Date_Retour.",
         ["FACT_Assoc_Retour"],
         ["retour", "avoir", "SAV", "retour marchandise", "motif retour",
          "montant retour"]),

        ("Motif de retour (vente)", "cause de retour client",
         "DIM_Motif_Retour liste les motifs de retour client (Code_Motif, "
         "Description_Motif).",
         ["DIM_Motif_Retour"],
         ["motif retour", "cause retour", "client"]),

        ("Motif de retour (achat)", "cause de retour fournisseur",
         "DIM_Motif_Retour_Achat liste les motifs de retour vers un "
         "fournisseur (Code_Motif, Description).",
         ["DIM_Motif_Retour_Achat"],
         ["motif retour achat", "cause retour fournisseur"]),

        ("Motif de retard", "cause de retard de commande",
         "DIM_Motif_Retard liste les motifs de retard (Code_Motif, "
         "Description_Motif) ; FACT_Commande_Client.Code_Motif_Retard n'a pas "
         "de contrainte FK declaree en base (a relier par convention de nom).",
         ["DIM_Motif_Retard", "FACT_Commande_Client"],
         ["retard", "motif retard", "delai", "cause retard"]),

        ("Numero de serie / lot vendu", "tracabilite vente, lot facture",
         "FACT_Serial trace le lot vendu par facture : Code_Client, "
         "Num_Facture, Date_Facture, Code_Article, Lot_Article.",
         ["FACT_Serial"],
         ["serie", "lot", "tracabilite", "Num_Facture", "Lot_Article",
          "lot vendu", "numero serie"]),

        # ── Finance / recouvrement ────────────────────────────────────────────
        ("Recouvrement client / creance", "impaye, encaissement, reglement",
         "FACT_Recouvrement_Client suit les documents de recouvrement client : "
         "Num_Document (PK), Code_Client, Montant_Total, Montant_Regle, "
         "Montant_Ouvert, Ouvert (bit : 1 = encore ouvert), Date_Echeance, "
         "Date_Paiement.",
         ["FACT_Recouvrement_Client"],
         ["recouvrement", "creance", "impaye", "encaissement", "reglement",
          "montant ouvert", "echeance", "paiement"]),

        ("Agregat de recouvrement", "creance mensuelle par client, en-cours",
         "AGG_Recouvrement_Client est un agregat mensuel deja calcule par "
         "client : Annee, Mois, Code_Client, Creance_Client, "
         "Total_Recouvrement, En_Cours_Client, Total_Impaye, CA_TTC — ne pas "
         "re-sommer comme si c'etait du detail (piege du grain).",
         ["AGG_Recouvrement_Client"],
         ["agregat", "recouvrement", "mensuel", "creance", "en-cours",
          "impaye", "CA_TTC"]),

        ("Agregat de commande", "commande agregee par client/article",
         "AGG_Commande agrege les commandes par couple client-article (cle "
         "COMPOSITE Code_Client + Code_Article) : Quantite_Commandee, "
         "Quantite_livree, Montant_Commande, Montant_facturee.",
         ["AGG_Commande"],
         ["agregat", "commande", "client article", "quantite commandee",
          "montant commande", "montant facture"]),

        ("Methode de paiement", "moyen de paiement",
         "DIM_Methode_Paiement liste les methodes de paiement "
         "(Code_Methode_Paiement, Description_Methode_Paiement).",
         ["DIM_Methode_Paiement"],
         ["methode paiement", "moyen paiement", "virement", "cheque", "especes"]),

        ("Modalite de paiement", "conditions de paiement, delai de paiement",
         "DIM_Modalite_Paiement liste les modalites de paiement "
         "(Code_Modalite, Description_Modalite, Delais).",
         ["DIM_Modalite_Paiement"],
         ["modalite", "conditions paiement", "delai paiement"]),

        ("Type de transaction financiere", "nature de l'operation de recouvrement",
         "DIM_Type_Transaction liste les types de transaction financiere "
         "(Code_Type_Transaction, Description_Type_Transaction).",
         ["DIM_Type_Transaction"],
         ["type transaction", "nature operation", "financiere"]),

        # ── Temporel / transverse ────────────────────────────────────────────
        ("Calendrier / date", "temps, periode, jour/mois/annee",
         "DIM_Date fournit un referentiel calendaire (Jour, Mois, Trimestre, "
         "Semestre, Annee) mais sa cle declaree (Num_Semaine) est anormale et "
         "aucune table de faits n'a de FK fiable vers cette dimension : "
         "preferer extraire l'annee/le mois directement des colonnes date des "
         "tables de faits (YEAR(...), MONTH(...)).",
         ["DIM_Date"],
         ["calendrier", "date", "mois", "annee", "trimestre", "semestre",
          "periode", "jour", "temps"]),

        ("Grain de la donnee", "niveau de detail, granularite",
         "FACT_Vente et FACT_Commande_Client sont au grain LIGNE ; "
         "AGG_Commande et AGG_Recouvrement_Client sont deja PRE-AGREGES "
         "(client x article, ou client x mois) — ne jamais re-sommer un "
         "agregat comme si c'etait du detail, risque de double-comptage.",
         ["FACT_Vente", "FACT_Commande_Client",
          "AGG_Commande", "AGG_Recouvrement_Client"],
         ["grain", "granularite", "detail", "agrege", "double comptage",
          "ligne", "pre-agrege"]),

        ("Homonymie de colonnes cles", "piege de jointure, code motif ambigu",
         "Plusieurs tables partagent le meme nom de colonne PK "
         "(Code_Motif dans DIM_Motif_Arret_Prod / DIM_Motif_Retard / "
         "DIM_Motif_Retour / DIM_Motif_Retour_Achat ; N_ID dans "
         "FACT_PROD_Consommation / FACT_PROD_Declaration) : bien choisir la "
         "table cible selon le contexte metier de la question.",
         ["DIM_Motif_Arret_Prod", "DIM_Motif_Retard",
          "DIM_Motif_Retour", "DIM_Motif_Retour_Achat",
          "FACT_PROD_Consommation", "FACT_PROD_Declaration"],
         ["homonymie", "piege", "jointure", "ambiguite", "Code_Motif", "N_ID"]),
        
    ]

    return [
        KBEntry(
            name=mot_cle, label="Term",
            description=f"{description} (aussi appele : {alternative})",
            kb_type="domain_knowledge",
            payload={
                "alternative": alternative,
                "description": description,
                "tables_ref": tables_ref,   # ← NOUVEAU : tables référencées
                "keywords": keywords,       # ← NOUVEAU : mots-clés BM25
            },
        )
        for mot_cle, alternative, description, tables_ref, keywords in domain
    ]


def build_task_entries() -> list[KBEntry]:
    """
    Materiels de tache (type 3) : demonstrations question -> SQL pour le
    few-shot SIR, couvrant les 46 tables du data warehouse.

    Format : (question, sql)
    """
    tasks = [
        # --- Ventes / CA ---
        ("Quel est le chiffre d'affaires total en TND ?",
         "SELECT SUM(CA_TND) AS CA_Total FROM FACT_Vente;"),
        ("Quel est le CA total par client ?",
         "SELECT Code_Client, SUM(CA_TND) AS CA_Total FROM FACT_Vente "
         "GROUP BY Code_Client ORDER BY CA_Total DESC;"),
        ("Quel est le chiffre d'affaires par mois pour l'annee 2025 ?",
         "SELECT MONTH(Date_Facture) AS Mois, SUM(CA_TND) AS CA_Mensuel "
         "FROM FACT_Vente WHERE YEAR(Date_Facture) = 2025 "
         "GROUP BY MONTH(Date_Facture) ORDER BY Mois;"),
        ("Quel est le cumul du chiffre d'affaires mois apres mois en 2025 ?",
         "SELECT MONTH(Date_Facture) AS Mois, "
         "SUM(SUM(CA_TND)) OVER (ORDER BY MONTH(Date_Facture)) AS CA_Cumule "
         "FROM FACT_Vente WHERE YEAR(Date_Facture) = 2025 "
         "GROUP BY MONTH(Date_Facture);"),
        ("Quel est le CA moyen par ligne de vente et par facture ?",
         "SELECT AVG(CA_TND) AS CA_Moyen_Ligne, "
         "AVG(Total_Facture) AS CA_Moyen_Facture "
         "FROM FACT_Vente;"),
        ("Quels sont les 5 articles les plus vendus en chiffre d'affaires ?",
         "SELECT TOP 5 Code_Article, SUM(CA_TND) AS CA_Total "
         "FROM FACT_Vente GROUP BY Code_Article ORDER BY CA_Total DESC;"),
        ("Quel est l'article le plus vendu en CA dans chaque region ?",
         "SELECT r.Region, v.Code_Article, SUM(v.CA_TND) AS CA "
         "FROM FACT_Vente v JOIN DIM_Client c ON c.Code_Client = v.Code_Client "
         "JOIN DIM_Region r ON r.Code_Client = c.Code_Client "
         "GROUP BY r.Region, v.Code_Article "
         "ORDER BY r.Region, CA DESC;"),
        ("Quel est le prix de vente moyen par type d'article ?",
         "SELECT a.Type_Article, AVG(v.Prix_Unitaire) AS Prix_Moyen "
         "FROM FACT_Vente v JOIN DIM_Article a ON a.Code_Article = v.Code_Article "
         "GROUP BY a.Type_Article;"),
        ("Quelle est la repartition du chiffre d'affaires par devise d'origine (sans conversion) ?",
         "SELECT Code_Devise_Ope, SUM(CA_Devise_Ope) AS CA_Devise "
         "FROM FACT_Vente GROUP BY Code_Devise_Ope;"),
        ("Quels clients ont un montant moyen de facture superieur a 5000 TND ?",
         "SELECT Code_Client, AVG(CA_TND) AS CA_Moyen_Facture FROM FACT_Vente "
         "GROUP BY Code_Client HAVING AVG(CA_TND) > 5000 "
         "ORDER BY CA_Moyen_Facture DESC;"),

        # --- Tracabilite ---
        ("Quel est le lot vendu pour la facture 'FAC0010' ?",
         "SELECT Code_Article, Lot_Article FROM FACT_Serial "
         "WHERE Num_Facture = 'FAC0010';"),

        # --- Commande / livraison / retour ---
        ("Quels sont les statuts de commande possibles ?",
         "SELECT Statut, Description_Statut FROM DIM_Statut_Commande;"),
        ("Quelles commandes sont encore au statut 'En cours' ?",
         "SELECT DISTINCT Num_Commande FROM FACT_Commande_Client "
         "WHERE Statut = 'En cours';"),
        ("Quel est le montant total facture par commande ?",
         "SELECT Num_Commande, SUM(Total_Montant_Facture) AS Montant_Facture "
         "FROM FACT_Commande_Client GROUP BY Num_Commande;"),
        ("Quelles lignes de commande ont un ecart entre quantite commandee et quantite livree ?",
         "SELECT Num_Commande, Code_Article, Quantite_Commandee, "
         "Total_Quantite_Livree FROM FACT_Commande_Client "
         "WHERE Quantite_Commandee <> Total_Quantite_Livree;"),
        ("Quel est le nombre de bons de livraison par site ?",
         "SELECT Code_Site, COUNT(*) AS Nb_BL FROM FACT_Assoc_Bon_Livraison "
         "GROUP BY Code_Site;"),
        ("Quelles commandes n'ont jamais ete livrees ?",
         "SELECT DISTINCT fc.Num_Commande FROM FACT_Commande_Client fc "
         "WHERE NOT EXISTS (SELECT 1 FROM FACT_Assoc_Bon_Livraison bl "
         "WHERE bl.Num_Commande = fc.Num_Commande);"),
        ("Quel est le montant total des retours clients par motif ?",
         "SELECT Motif_Retour, SUM(Montant_Retour) AS Montant_Retour_Total "
         "FROM FACT_Assoc_Retour GROUP BY Motif_Retour;"),
        ("Quels sont les motifs de retour client existants ?",
         "SELECT Code_Motif, Description_Motif FROM DIM_Motif_Retour;"),
        ("Quels sont les motifs de retour fournisseur existants ?",
         "SELECT Code_Motif, Description FROM DIM_Motif_Retour_Achat;"),
        ("Quels sont les motifs de retard existants ?",
         "SELECT Code_Motif, Description_Motif FROM DIM_Motif_Retard;"),

        # --- Recouvrement / paiement ---
        ("Quel est le montant total impaye par client ?",
         "SELECT Code_Client, SUM(Montant_Ouvert) AS Total_Impaye "
         "FROM FACT_Recouvrement_Client WHERE Ouvert = 1 "
         "GROUP BY Code_Client;"),
        ("Quelle est la creance totale par mois d'apres l'agregat de recouvrement ?",
         "SELECT Annee, Mois, SUM(Creance_Client) AS Creance_Totale "
         "FROM AGG_Recouvrement_Client GROUP BY Annee, Mois "
         "ORDER BY Annee, Mois;"),
        ("Quelle est la quantite totale commandee par client d'apres l'agregat de commandes ?",
         "SELECT Code_Client, SUM(Quantite_Commandee) AS Quantite_Totale "
         "FROM AGG_Commande GROUP BY Code_Client "
         "ORDER BY Quantite_Totale DESC;"),
        ("Quels sont les modalites de paiement disponibles et leurs delais ?",
         "SELECT Code_Modalite, Description_Modalite, Delais "
         "FROM DIM_Modalite_Paiement;"),
        ("Quelles sont les methodes de paiement disponibles ?",
         "SELECT Code_Methode_Paiement, Description_Methode_Paiement "
         "FROM DIM_Methode_Paiement;"),
        ("Quels sont les types de transaction financiere existants ?",
         "SELECT Code_Type_Transaction, Description_Type_Transaction "
         "FROM DIM_Type_Transaction;"),

        # --- Achat / fournisseur ---
        ("Quel est le delai moyen en jours entre commande et reception fournisseur ?",
         "SELECT AVG(DATEDIFF(DAY, oa.Date_Commande, r.Date_Reception)) "
         "AS Delai_Moyen_Jours FROM FACT_Ordre_Achat oa "
         "JOIN FACT_Assoc_Reception r ON r.Num_Commande = oa.Num_Commande "
         "AND r.Code_Article = oa.Code_Article;"),
        ("Quels fournisseurs ont livre en moyenne moins de 90% des quantites commandees, sur plus de 5 commandes ?",
         "SELECT Code_Fournisseur, COUNT(*) AS Nb_Lignes, "
         "AVG(1.0 * [Qte_reçue] / NULLIF(Qte_Commandee, 0)) AS Taux_Livraison "
         "FROM FACT_Ordre_Achat GROUP BY Code_Fournisseur "
         "HAVING COUNT(*) > 5 "
         "AND AVG(1.0 * [Qte_reçue] / NULLIF(Qte_Commandee, 0)) < 0.90;"),
        ("Quel est le nombre de receptions par fournisseur et leur quantite totale recue ?",
         "SELECT Code_Fournisseur, COUNT(*) AS Nb_Receptions, "
         "SUM(Qte_reçue) AS Qte_Totale FROM FACT_Assoc_Reception "
         "GROUP BY Code_Fournisseur ORDER BY Qte_Totale DESC;"),

        # --- Devise / site / organisation ---
        ("Quel est le taux de change actuel de l'euro ?",
         "SELECT Taux_Change FROM DIM_Devise WHERE Code_Devise = 'EUR';"),
        ("Quels sont les sites actifs ?",
         "SELECT Code_Site, Description FROM DIM_Site WHERE Status = 'Actif';"),
        ("Quelle est la capacite machine de chaque departement ?",
         "SELECT Code_Dpt, Capacite_Mch FROM DIM_Departement;"),
        ("Quels domaines commerciaux sont de type export ?",
         "SELECT Code_Domaine, Description FROM DIM_Domaine "
         "WHERE Type_Domaine = 'Export';"),
        ("Quelle est la ville et le pays de chaque client ?",
         "SELECT c.Code_Client, c.Nom_Client, r.Ville, r.Pays "
         "FROM DIM_Client c JOIN DIM_Region r ON r.Code_Client = c.Code_Client "
         ),

        # --- Production ---
        ("Quel est le taux d'utilisation moyen par centre de charge ?",
         "SELECT Code_centre_charge, AVG(Taux_Utilisation) AS Taux_Moyen "
         "FROM DIM_Centre_Charge GROUP BY Code_centre_charge;"),
        ("Quelles sont les operations de la gamme 'GAM0001', triees par temps d'execution ?",
         "SELECT Operation, Description_Oper, Temps_Execution FROM DIM_Gamme "
         "WHERE Code_Gamme = 'GAM0001' ORDER BY Temps_Execution;"),
        ("Quel est le rendement theorique de chaque operation de production ?",
         "SELECT N_Op, Operation, Rendement FROM DIM_Operations "
         "ORDER BY Rendement DESC;"),
        ("Quelle est la composition (nomenclature) de l'article 'NOM0001' ?",
         "SELECT Composant, Quantite_par, Rebut FROM DIM_Nomenclature "
         "WHERE Code_Nomenclature = 'NOM0001';"),
        ("Quel est le motif d'arret de production le plus frequent ?",
         "SELECT TOP 1 m.Code_Motif, m.Description, COUNT(*) AS Nb "
         "FROM FACT_PROD_Arret a JOIN DIM_Motif_Arret_Prod m "
         "ON m.Code_Motif = a.Code_Motif "
         "GROUP BY m.Code_Motif, m.Description ORDER BY Nb DESC;"),
        ("Quelle est la duree totale d'arret de production par machine ?",
         "SELECT Machine, SUM(Duree) AS Duree_Totale FROM FACT_PROD_Arret "
         "GROUP BY Machine ORDER BY Duree_Totale DESC;"),
        ("Quelle est la quantite theorique et la quantite terminee par ordre de fabrication ?",
         "SELECT N_OF, Qte_Theorique, Qte_Terminee FROM FACT_PROD_Consommation;"),
        ("Quel est le taux de rejet moyen par centre de charge en declaration de production ?",
         "SELECT Centre_Charge, SUM(Qte_Rejetee) AS Rejete, "
         "SUM(Qte_Terminee) AS Termine, "
         "CAST(100.0 * SUM(Qte_Rejetee) "
         "/ NULLIF(SUM(Qte_Terminee) + SUM(Qte_Rejetee), 0) AS DECIMAL(5,2)) "
         "AS Taux_Rejet_Pct "
         "FROM FACT_PROD_Declaration GROUP BY Centre_Charge "
         "ORDER BY Taux_Rejet_Pct DESC;"),
        ("Quel est l'ecart entre le cout standard et le cout reel de main-d'oeuvre en declaration de production ?",
         "SELECT N_OF, N_OP, Cout_MO_Std, Cout_MO_Reel, "
         "(Cout_MO_Reel - Cout_MO_Std) AS Ecart FROM FACT_PROD_Declaration;"),
        ("Quelle est la valeur de consommation, de main-d'oeuvre et de frais generaux par ordre de fabrication ?",
         "SELECT N_OF, Valeur_Cons, Valeur_MO, Valeur_FGV "
         "FROM FACT_PROD_Valorisation;"),
        ("Quel est le stock disponible total par site ?",
         "SELECT Site, SUM(In_Qty_Oh) AS Stock_Total FROM Fact_Prod_Stocks "
         "GROUP BY Site;"),
        ("Quels lots arrivent a peremption dans les 30 prochains jours ?",
         "SELECT Article, Num_Lot, Date_Exp FROM Fact_Prod_Stocks "
         "WHERE Date_Exp <= DATEADD(DAY, 30, GETDATE());"),
        ("Quelle est la periode de validite de chaque unite de production par article ?",
         "SELECT Code_Article, Unite_Production, Date_Debut, Date_Fin "
         "FROM Dim_Validite_UP ORDER BY Code_Article;"),
        # ── Patch builder.py ─────────────────────────────────────────────────────────
    # Ajouter ces tâches AVANT la ligne "]" finale de build_task_entries()
    ]
        # (avant la ligne "    ]" à la fin de la liste tasks)

    TASKS_TO_ADD = [

        # ── DIM_Region : distinction FK vs PK ────────────────────────────────────
        ("Combien d'adresses (regions) chaque client possede-t-il ?",
        "SELECT Code_Client, COUNT(Code_Region) AS Nb_Adresses "
        "FROM [DIM_Region] "
        "GROUP BY Code_Client "
        "ORDER BY Nb_Adresses DESC;"),

        ("Combien de clients distincts sont references dans DIM_Region ?",
        "SELECT COUNT(DISTINCT Code_Client) AS Nb_Clients_Distincts "
        "FROM [DIM_Region];"),

        ("Quels clients ont plusieurs adresses de type Facturation ?",
        "SELECT Code_Client, COUNT(Code_Region) AS Nb_Adresses_Fact "
        "FROM [DIM_Region] "
        "WHERE Type_Adresse = 'Facturation' "
        "GROUP BY Code_Client "
        "HAVING COUNT(Code_Region) > 1 "
        "ORDER BY Nb_Adresses_Fact DESC;"),

        # ── Anti-jointures avec NOT EXISTS ────────────────────────────────────────
        ("Quels articles n'ont jamais ete vendus ?",
        "SELECT da.Code_Article, da.Description "
        "FROM DIM_Article da "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM FACT_Vente fv "
        "    WHERE fv.Code_Article = da.Code_Article"
        ");"),

        ("Quels clients n'ont jamais passe de commande ?",
        "SELECT c.Code_Client, c.Nom_Client "
        "FROM DIM_Client c "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM FACT_Commande_Client fc "
        "    WHERE fc.Code_Client = c.Code_Client"
        ");"),

        ("Quelles commandes n'ont jamais ete livrees (aucun bon de livraison) ?",
        "SELECT fc.Num_Commande "
        "FROM FACT_Commande_Client fc "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM FACT_Assoc_Bon_Livraison bl "
        "    WHERE bl.Num_Commande = fc.Num_Commande"
        ");"),

        ("Quels fournisseurs n'ont jamais recu de reception de marchandise ?",
        "SELECT f.Code_Fournisseur, f.Nom_Fournisseur "
        "FROM DIM_Fournisseur f "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM FACT_Assoc_Reception r "
        "    WHERE r.Code_Fournisseur = f.Code_Fournisseur"
        ");"),

        ("Dans quelles regions se trouvent les articles qui n'ont jamais ete vendus ?",
        "SELECT DISTINCT r.Region "
        "FROM DIM_Region r "
        "JOIN DIM_Client c ON c.Code_Client = r.Code_Client "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM FACT_Vente v "
        "    WHERE v.Code_Client = c.Code_Client"
        ");"),

        # ── Valeur de filtre visible dans le SELECT ───────────────────────────────
        ("Combien de retards de commande sont dus au motif 'MOR0005' ?",
        "SELECT 'MOR0005' AS Motif_Filtre, COUNT(*) AS Nb_Retards "
        "FROM FACT_Commande_Client "
        "WHERE Code_Motif_Retard = 'MOR0005';"),

        # ── Rang visible ──────────────────────────────────────────────────────────
        ("Quel est le motif d'arret de production le plus frequent avec son rang ?",
        "SELECT TOP 5 "
        "    ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS Rang, "
        "    m.Code_Motif, m.Description, COUNT(*) AS Nb_Arrets "
        "FROM FACT_PROD_Arret a "
        "JOIN DIM_Motif_Arret_Prod m ON m.Code_Motif = a.Code_Motif "
        "GROUP BY m.Code_Motif, m.Description "
        "ORDER BY Nb_Arrets DESC;"),

        # ── Ratio / pourcentage ───────────────────────────────────────────────────
        ("Quelle est la part de chaque famille d'article dans le CA total ?",
        "SELECT "
        "    a.Famille_Article, "
        "    SUM(v.CA_TND) AS CA_Famille, "
        "    ROUND(CAST(SUM(v.CA_TND) AS FLOAT) * 100.0 "
        "          / NULLIF(SUM(SUM(v.CA_TND)) OVER (), 0), 2) AS Part_Pct "
        "FROM FACT_Vente v "
        "JOIN DIM_Article a ON a.Code_Article = v.Code_Article "
        "GROUP BY a.Famille_Article "
        "ORDER BY CA_Famille DESC;"),

        ("Quel pourcentage de commandes n'a jamais ete livre ?",
        "SELECT "
        "    COUNT(*) AS Total_Commandes, "
        "    SUM(CASE WHEN NOT EXISTS ("
        "        SELECT 1 FROM FACT_Assoc_Bon_Livraison bl "
        "        WHERE bl.Num_Commande = fc.Num_Commande"
        "    ) THEN 1 ELSE 0 END) AS Commandes_Non_Livrees, "
        "    ROUND("
        "        CAST(SUM(CASE WHEN NOT EXISTS ("
        "            SELECT 1 FROM FACT_Assoc_Bon_Livraison bl "
        "            WHERE bl.Num_Commande = fc.Num_Commande"
        "        ) THEN 1 ELSE 0 END) AS FLOAT) * 100.0 "
        "        / NULLIF(COUNT(*), 0), 2"
        "    ) AS Pct_Non_Livrees "
        "FROM FACT_Commande_Client fc;"),
    ]
    


    
    return [
        KBEntry(
            name=q, label="Task",
            description=f"Question: {q} | SQL: {sql}",
            kb_type="task_material",
            payload={"question": q, "sql": sql},
        )
        for q, sql in tasks
    ]


def build_full_kb(db: DatabaseConnector, db_name: str = "TestDB_DataWarehouse") -> list[KBEntry]:
    schema = db.get_schema()
    return (
        build_metadata_entries(schema, db_name)
        + build_domain_entries()
        + build_task_entries()
    )


if __name__ == "__main__":
    db = DatabaseConnector()
    kb = build_full_kb(db)
    print(f"{len(kb)} entrées KB générées.")
    by_type: dict[str, int] = {}
    for e in kb:
        by_type[e.kb_type] = by_type.get(e.kb_type, 0) + 1
    for k, v in by_type.items():
        print(f"  {k}: {v}")