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

NOTE : tous les exemples SQL task_material utilisent le dialecte MySQL.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
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
        """Texte concaténé utilisé pour l'embedding et le BM25."""
        parts = [self.name, self.label, self.description]
        if self.anc_name:
            parts.append(f"appartient à {self.anc_label} {self.anc_name}")
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
    Connaissance domaine (type 2), generee en parcourant les 46 tables
    DIM_/FACT_/AGG_ du data warehouse (pharma/industrie, Tunisie).
    """
    domain = [
        # --- Client ---
        ("Client", "acheteur, tiers client, compte client",
         "DIM_Client contient l'identite et la segmentation de chaque client : "
         "Code_Client (PK), Nom_Client, Classe_Client, Code_Marche, "
         "Code_Type_Client, Status_Client, Region, Code_Devise."),
        ("Classe client", "segment client, categorie de clientele",
         "DIM_Classe_Client liste les classes de clients (Code_classe_client, "
         "Description) ; DIM_Client.Classe_Client contient la valeur mais sans "
         "contrainte FK declaree vers cette table."),
        ("Type de client", "categorie de client",
         "DIM_Type_Client liste les types de client (Code_Type_Client, "
         "Description), reference par DIM_Client.Code_Type_Client."),
        ("Marche / segment de vente", "canal de vente, marche cible",
         "DIM_Type_Marche liste les types de marche (Code_Type_Marche, "
         "Description), reference par DIM_Client.Code_Marche."),
        ("Region / adresse client", "zone geographique, secteur, territoire, ville, pays",
         "DIM_Region contient les adresses des clients : Code_Region (PK), "
         "Code_Client (FK vers DIM_Client), Adresse, Ville, Pays, Type_Adresse "
         "(Facturation/Livraison/Siege)."),

        # --- Article / produit ---
        ("Article / produit", "reference, SKU, produit fini",
         "DIM_Article decrit chaque article : Code_Article (PK), Description, "
         "Famille_Article, Type_Article, Prix_Vente, Unite_Mesure, AMM "
         "(autorisation de mise sur le marche), Ligne_Produit, Status."),
        ("Famille d'article", "categorie produit, gamme de produits",
         "DIM_Famille_Article liste les familles d'articles (Code_Famille, "
         "Description) ; DIM_Article.Famille_Article est une colonne texte "
         "libre, sans FK declaree vers cette table."),
        ("Type d'article", "nature de l'article (fini/semi-fini/matiere premiere)",
         "DIM_Type_Article liste les types d'article (Code_Type_Article, "
         "Description)."),
        ("Unite de mesure", "unite, UM (kg, litre, piece...)",
         "DIM_Unite_Mesure liste les unites de mesure standard "
         "(Code_Unite_Mesure, Description)."),
        ("Unite de production", "UP, ligne de production",
         "Dim_Unite_Production liste les unites de production (Code_Unite, "
         "Description)."),
        ("Validite d'une unite de production", "periode de validite UP pour un article",
         "Dim_Validite_UP donne, pour un article donne, la periode de validite "
         "(Date_Debut/Date_Fin) d'une unite de production (Unite_Production)."),
        ("Cout de revient d'un article", "cout matiere/MO/FGV/FGF, cout standard",
         "DIM_Cout_Article detaille le cout de revient par article ET par "
         "groupe de cout (Groupe_Cout : MP/MO/FGV/FGF/ST), avec une date de "
         "changement (Date_Changement) : Cout_Total, Cout_Matiere, Cout_MO, "
         "Cout_FGV, Cout_FGF, Cout_ST (+ variantes _NI)."),

        # --- Fournisseur / achat ---
        ("Fournisseur", "vendeur, tiers fournisseur",
         "DIM_Fournisseur contient l'identite des fournisseurs : "
         "Code_Fournisseur (PK), Nom_Fournisseur, Pays, Ville, Devise, Type "
         "(Local/Import), Etat."),
        ("Type de fournisseur", "categorie de fournisseur",
         "DIM_Type_Fournisseur liste les types de fournisseur "
         "(Code_Type_Fournisseur, Description)."),
        ("Ordre d'achat", "commande fournisseur, OA, demande d'achat (DA)",
         "FACT_Ordre_Achat enregistre les commandes passees aux fournisseurs : "
         "Num_DA, Num_Commande, Code_Fournisseur, Code_Article, Qte_Commandee, "
         "Cout_Achat, Statut_Ordre, Date_Commande, Credit_Terms."),
        ("Reception fournisseur", "reception de marchandise, bon de reception (BR)",
         "FACT_Assoc_Reception enregistre les receptions de marchandises "
         "fournisseurs : Num_Reception, Num_Commande, Code_Fournisseur, "
         "Code_Article, `Qte_reçue`, Date_Reception, Cout_Fact_TND."),

        # --- Devise / finance transverse ---
        ("Devise", "monnaie, currency",
         "DIM_Devise liste les devises utilisees (Code_Devise, Description, "
         "Taux_Change, Date_Fin)."),
        ("Taux de change", "conversion devise vers TND",
         "Colonne Taux_Change presente dans plusieurs tables de faits "
         "(FACT_Vente, FACT_Commande_Client, FACT_Assoc_Retour, "
         "FACT_Ordre_Achat, FACT_Recouvrement_Client, FACT_Assoc_Reception) "
         "pour convertir un montant en devise d'origine vers le TND."),
        ("Chiffre d'affaires (CA)", "revenu, ventes, recette",
         "FACT_Vente.CA_TND est le CA deja converti en dinars tunisiens (a "
         "utiliser pour toute somme multi-devises) ; CA_Devise_Ope est le CA "
         "en devise d'origine (ne jamais sommer directement entre lignes de "
         "devises differentes)."),

        # --- Site / organisation ---
        ("Site", "depot, usine, entrepot",
         "DIM_Site liste les sites de l'entreprise (Code_Site, Description, "
         "Status)."),
        ("Departement (production)", "atelier, unite organisationnelle de production",
         "DIM_Departement liste les departements de production (Code_Dpt, "
         "Description, Capacite_Mch, Unite_Prod)."),
        ("Domaine commercial", "marche geographique, local vs export",
         "DIM_Domaine liste les domaines commerciaux (Code_Domaine, "
         "Description, Type_Domaine)."),

        # --- Production / industriel ---
        ("Centre de charge", "atelier, poste de charge",
         "DIM_Centre_Charge decrit les centres de charge de production : "
         "Code_centre_charge (PK), Machine, Departement, et une serie de taux "
         "horaires (Taux_MO, Taux_Rendement, Taux_Utilisation, "
         "Taux_Reglage...)."),
        ("Gamme de fabrication", "gamme operatoire, routing, sequence d'operations",
         "DIM_Gamme decrit les operations d'une gamme de fabrication : "
         "Code_Gamme, Operation, Machine, Temps_Reglage, Temps_Execution, "
         "Cadence, Centre_Charge."),
        ("Operation (referentiel production)", "poste operatoire, etape de gamme",
         "DIM_Operations liste les operations de production (N_Op (PK), "
         "Operation, Machine, Centre_Charge, Rendement theorique)."),
        ("Nomenclature", "BOM, recette de fabrication, composants",
         "DIM_Nomenclature decrit la composition d'un article : "
         "Code_Nomenclature + Formule (cle composite), Compose, Composant, "
         "Quantite_par, Rebut."),
        ("Ordre de fabrication (OF)", "OF, lot de production, ordre de fab",
         "L'OF (colonne N_OF) traverse plusieurs tables de faits : "
         "FACT_PROD_Consommation (consommation matiere), "
         "FACT_PROD_Declaration (declarations d'execution), "
         "FACT_PROD_Valorisation (valorisation cout reel vs standard), "
         "FACT_PROD_Arret (arrets survenus pendant l'OF)."),
        ("Consommation matiere (production)", "consommation OF, sortie de stock matiere",
         "FACT_PROD_Consommation detaille la consommation de matiere par OF : "
         "N_OF, Code_Article_OF, Code_Nomenclature, Qte_Theorique, "
         "Qte_Terminee, Qte_prelevee, Cout_Matiere."),
        ("Declaration de production", "temps de production, rendement, rebut declare",
         "FACT_PROD_Declaration enregistre les declarations d'execution par "
         "operation : N_OF, N_ID (PK), N_OP, Qte_Terminee, Qte_Rejetee, "
         "`%Rendement`, Temps_Exec_Reel, Cout_MO_Reel."),
        ("Arret de production", "panne, incident, temps d'arret machine",
         "FACT_PROD_Arret enregistre les arrets de production : N_Operation "
         "(PK), N_OF, Code_Motif (motif d'arret), Duree, Date_Debut, "
         "Date_Fin, Machine, Departement."),
        ("Motif d'arret de production", "cause de panne, incident production",
         "DIM_Motif_Arret_Prod liste les motifs d'arret de production "
         "(Code_Motif, Description), reference par FACT_PROD_Arret.Code_Motif."),
        ("Valorisation de production", "cout de production reel vs standard",
         "FACT_PROD_Valorisation valorise chaque OF : N_OF (PK), Valeur_Cons, "
         "Valeur_MO, Valeur_FGV, Cout_Std, Cout_Std_MO, Cout_Std_Matiere."),
        ("Rendement de production", "efficacite, taux de rendement",
         "Le rendement apparait a plusieurs niveaux : DIM_Operations.Rendement "
         "(theorique par operation), FACT_PROD_Declaration.`%Rendement` "
         "(reel declare), FACT_PROD_Consommation.Rendement (rendement de "
         "l'OF) — bien preciser lequel est demande."),
        ("Stock", "inventaire, quantite en stock, peremption",
         "Fact_Prod_Stocks donne le stock disponible par article/lot/site : "
         "Article, Num_Lot, Site, In_Qty_Oh (quantite en stock), Date_Exp "
         "(date de peremption), Emplacement."),

        # --- Vente / commande / logistique ---
        ("Vente / facturation", "chiffre d'affaires, CA, facture client, ligne de vente",
         "FACT_Vente est la table de faits au grain LIGNE DE VENTE : "
         "Client, Article, Facture, Facture, `Quantité`, "
         "CA_TND, CA_Devise_Ope, Marge, Prix_Unitaire"),
        ("Commande client", "ordre de vente, ligne de commande",
         "FACT_Commande_Client est au grain LIGNE DE COMMANDE (Ligne_Num) : "
         "Num_Commande, Code_Client, Code_Article, Quantite_Commandee, "
         "Total_Quantite_Livree, Total_Montant_Facture, Statut, "
         "Date_Commande, Date_Echeance."),
        ("Statut de commande", "etat de la commande",
         "DIM_Statut_Commande liste les statuts possibles d'une commande "
         "(Statut, Description_Statut)."),
        ("Bon de livraison", "BL, expedition, livraison client",
         "FACT_Assoc_Bon_Livraison associe une ligne de commande a sa "
         "livraison : Num_Commande, Num_BL, `Quantité_Livrée`, "
         "Date_Livraison_BL, ID_Livraison (PK), Code_Site."),
        ("Retour client", "avoir, retour de marchandise, SAV",
         "FACT_Assoc_Retour enregistre les retours clients : Num_Commande, "
         "Code_Client, Code_Article, `Quantité_Retournée`, Montant_Retour, "
         "Motif_Retour, Date_Retour."),
        ("Motif de retour (vente)", "cause de retour client",
         "DIM_Motif_Retour liste les motifs de retour client (Code_Motif, "
         "Description_Motif)."),
        ("Motif de retour (achat)", "cause de retour fournisseur",
         "DIM_Motif_Retour_Achat liste les motifs de retour vers un "
         "fournisseur (Code_Motif, Description)."),
        ("Motif de retard", "cause de retard de commande",
         "DIM_Motif_Retard liste les motifs de retard (Code_Motif, "
         "Description_Motif) ; FACT_Commande_Client.Code_Motif_Retard n'a pas "
         "de contrainte FK declaree en base (a relier par convention de nom)."),
        ("Numero de serie / lot vendu", "tracabilite vente, lot facture",
         "FACT_Serial trace le lot vendu par facture : Code_Client, "
         "Num_Facture, Date_Facture, Code_Article, Lot_Article."),

        # --- Finance / recouvrement ---
        ("Recouvrement client / creance", "impaye, encaissement, reglement",
         "FACT_Recouvrement_Client suit les documents de recouvrement client : "
         "Num_Document (PK), Code_Client, Montant_Total, Montant_Regle, "
         "Montant_Ouvert, Ouvert (bit : 1 = encore ouvert), Date_Echeance, "
         "Date_Paiement."),
        ("Agregat de recouvrement", "creance mensuelle par client, en-cours",
         "AGG_Recouvrement_Client est un agregat mensuel deja calcule par "
         "client : Annee, Mois, Code_Client, Creance_Client, "
         "Total_Recouvrement, En_Cours_Client, Total_Impaye, CA_TTC — ne pas "
         "re-sommer comme si c'etait du detail (piege du grain)."),
        ("Agregat de commande", "commande agregee par client/article",
         "AGG_Commande agrege les commandes par couple client-article (cle "
         "COMPOSITE Code_Client + Code_Article) : Quantite_Commandee, "
         "Quantite_livree, Montant_Commande, Montant_facturee."),
        ("Methode de paiement", "moyen de paiement",
         "DIM_Methode_Paiement liste les methodes de paiement "
         "(Code_Methode_Paiement, Description_Methode_Paiement)."),
        ("Modalite de paiement", "conditions de paiement, delai de paiement",
         "DIM_Modalite_Paiement liste les modalites de paiement "
         "(Code_Modalite, Description_Modalite, Delais)."),
        ("Type de transaction financiere", "nature de l'operation de recouvrement",
         "DIM_Type_Transaction liste les types de transaction financiere "
         "(Code_Type_Transaction, Description_Type_Transaction)."),

        # --- Temporel / transverse ---
        ("Calendrier / date", "temps, periode, jour/mois/annee",
         "DIM_Date fournit un referentiel calendaire (Jour, Mois, Trimestre, "
         "Semestre, Annee) mais sa cle declaree (Num_Semaine) est anormale et "
         "aucune table de faits n'a de FK fiable vers cette dimension : "
         "preferer extraire l'annee/le mois directement des colonnes date des "
         "tables de faits (YEAR(...), MONTH(...))."),
        ("Grain de la donnee", "niveau de detail, granularite",
         "FACT_Vente et FACT_Commande_Client sont au grain LIGNE ; "
         "AGG_Commande et AGG_Recouvrement_Client sont deja PRE-AGREGES "
         "(client x article, ou client x mois) — ne jamais re-sommer un "
         "agregat comme si c'etait du detail, risque de double-comptage."),
        ("Homonymie de colonnes cles", "piege de jointure, code motif ambigu",
         "Plusieurs tables partagent le meme nom de colonne PK "
         "(Code_Motif dans DIM_Motif_Arret_Prod / DIM_Motif_Retard / "
         "DIM_Motif_Retour / DIM_Motif_Retour_Achat ; N_ID dans "
         "FACT_PROD_Consommation / FACT_PROD_Declaration) : bien choisir la "
         "table cible selon le contexte metier de la question."),
    ]
    return [
        KBEntry(
            name=mot_cle, label="Term",
            description=f"{description} (aussi appele : {alternative})",
            kb_type="domain_knowledge",
            payload={"alternative": alternative, "description": description},
        )
        for mot_cle, alternative, description in domain
    ]


def build_task_entries() -> list[KBEntry]:
    """
    Materiels de tache (type 3) : demonstrations question -> SQL pour le
    few-shot SIR, couvrant les 46 tables du data warehouse.

    Format : (question, sql)
    Dialecte : MySQL 8+ (LIMIT, YEAR/MONTH/QUARTER, DATE_ADD, backticks pour
    colonnes spéciales, DATEDIFF(fin, debut) = arg order inversé vs MSSQL).
    """
    tasks = [
        # --- Ventes / CA ---
        ("Quel est le chiffre d'affaires total en TND ?",
         "SELECT SUM(CA_TND) AS CA_Total FROM FACT_Vente;"),
        ("Quel est le CA total par client ?",
         "SELECT Code_Client, SUM(CA_TND) AS CA FROM FACT_Vente "
         "GROUP BY Code_Client ORDER BY CA DESC;"),
        # TOP 5 → LIMIT 5 en MySQL
        ("Quels sont les 5 articles les plus vendus en chiffre d'affaires ?",
         "SELECT Code_Article, SUM(CA_TND) AS CA FROM FACT_Vente "
         "GROUP BY Code_Article ORDER BY CA DESC LIMIT 5;"),
        ("Quel est le chiffre d'affaires par mois pour l'annee 2025 ?",
         "SELECT MONTH(Date_Facture) AS Mois, SUM(CA_TND) AS CA "
         "FROM FACT_Vente WHERE YEAR(Date_Facture) = 2025 "
         "GROUP BY MONTH(Date_Facture) ORDER BY Mois;"),
        ("Quel est le cumul du chiffre d'affaires mois apres mois en 2025 ?",
         "WITH CA_Mensuel AS ("
         "SELECT MONTH(Date_Facture) AS Mois, SUM(CA_TND) AS CA "
         "FROM FACT_Vente WHERE YEAR(Date_Facture) = 2025 "
         "GROUP BY MONTH(Date_Facture)) "
         "SELECT Mois, CA, SUM(CA) OVER (ORDER BY Mois "
         "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS CA_Cumule "
         "FROM CA_Mensuel ORDER BY Mois;"),
        ("Quel est l'article le plus vendu en CA dans chaque region ?",
         "WITH Classement AS ("
         "SELECT Code_Region, Code_Article, SUM(CA_TND) AS CA, "
         "ROW_NUMBER() OVER (PARTITION BY Code_Region ORDER BY SUM(CA_TND) DESC) AS rn "
         "FROM FACT_Vente GROUP BY Code_Region, Code_Article) "
         "SELECT Code_Region, Code_Article, CA FROM Classement WHERE rn = 1;"),
        ("Quelle est la repartition du chiffre d'affaires par devise d'origine (sans conversion) ?",
         "SELECT Code_Devise, SUM(CA_Devise_Ope) AS CA_Devise "
         "FROM FACT_Vente GROUP BY Code_Devise;"),
        ("Quel est le lot vendu pour la facture 'FAC0010' ?",
         "SELECT Code_Article, Lot_Article FROM FACT_Serial "
         "WHERE Num_Facture = 'FAC0010';"),

        # --- Client / region ---
        ("Combien de clients sont enregistres dans la base ?",
         "SELECT COUNT(*) AS Nb_Clients FROM DIM_Client;"),
        ("Quels clients appartiennent a la classe 'VIP' ?",
         "SELECT Code_Client, Nom_Client FROM DIM_Client "
         "WHERE Classe_Client = 'VIP';"),
        ("Quelle est la ville et le pays de chaque client ?",
         "SELECT c.Code_Client, r.Ville, r.Pays FROM DIM_Client c "
         "LEFT JOIN DIM_Region r ON r.Code_Client = c.Code_Client;"),
        ("Quels sont les types de client existants ?",
         "SELECT Code_Type_Client, Description FROM DIM_Type_Client;"),
        ("Quelles sont les classes de client existantes ?",
         "SELECT Code_classe_client, Description FROM DIM_Classe_Client;"),
        ("Quels sont les types de marche existants ?",
         "SELECT Code_Type_Marche, Description FROM DIM_Type_Marche;"),
        # NOT EXISTS — valide MySQL
        ("Quels clients n'ont jamais passe de commande ?",
         "SELECT c.Code_Client, c.Nom_Client FROM DIM_Client c "
         "WHERE NOT EXISTS (SELECT 1 FROM FACT_Commande_Client fc "
         "WHERE fc.Code_Client = c.Code_Client);"),
        # NOT EXISTS sur DIM_Region
        ("Quels clients n'ont aucune adresse enregistree dans DIM_Region ?",
         "SELECT c.Code_Client, c.Nom_Client FROM DIM_Client c "
         "WHERE NOT EXISTS (SELECT 1 FROM DIM_Region r "
         "WHERE r.Code_Client = c.Code_Client);"),

        # --- Article ---
        ("Combien d'articles existent par famille ?",
         "SELECT Famille_Article, COUNT(*) AS Nb_Articles FROM DIM_Article "
         "GROUP BY Famille_Article;"),
        ("Quel est le prix de vente moyen par type d'article ?",
         "SELECT Type_Article, AVG(Prix_Vente) AS Prix_Moyen FROM DIM_Article "
         "GROUP BY Type_Article;"),
        ("Quelles sont les familles d'article referencees ?",
         "SELECT Code_Famille, Description FROM DIM_Famille_Article;"),
        ("Quels sont les types d'article existants ?",
         "SELECT Code_Type_Article, Description FROM DIM_Type_Article;"),
        ("Quelles sont les unites de mesure disponibles ?",
         "SELECT Code_Unite_Mesure, Description FROM DIM_Unite_Mesure;"),
        ("Quelles sont les unites de production disponibles ?",
         "SELECT Code_Unite, Description FROM Dim_Unite_Production;"),
        ("Quelle est la periode de validite de chaque unite de production par article ?",
         "SELECT Code_Article, Unite_Production, Date_Debut, Date_Fin "
         "FROM Dim_Validite_UP;"),
        # ROW_NUMBER() valide MySQL 8+
        ("Quel est le cout matiere le plus recent de chaque article ?",
         "SELECT Code_Article, Cout_Matiere, Date_Changement FROM ("
         "SELECT *, ROW_NUMBER() OVER (PARTITION BY Code_Article "
         "ORDER BY Date_Changement DESC) AS rn FROM DIM_Cout_Article) t "
         "WHERE rn = 1;"),
        ("Quels articles n'ont jamais ete vendus ?",
         "SELECT da.Code_Article, da.Description FROM DIM_Article da "
         "WHERE NOT EXISTS (SELECT 1 FROM FACT_Vente fv "
         "WHERE fv.Code_Article = da.Code_Article);"),

        # --- Fournisseur / achat ---
        ("Quels fournisseurs sont situes en Tunisie ?",
         "SELECT Code_Fournisseur, Nom_Fournisseur FROM DIM_Fournisseur "
         "WHERE Pays = 'Tunisie';"),
        ("Quels sont les types de fournisseur existants ?",
         "SELECT Code_Type_Fournisseur, Description FROM DIM_Type_Fournisseur;"),
        ("Quels ordres d'achat sont encore ouverts (quantite ouverte > 0) ?",
         "SELECT Num_DA, Code_Fournisseur, Qte_Ouverte FROM FACT_Ordre_Achat "
         "WHERE Qte_Ouverte > 0;"),
        # Backtick pour colonne accentuée
        ("Quel est le nombre de receptions par fournisseur et leur quantite totale recue ?",
         "SELECT Code_Fournisseur, COUNT(*) AS Nb_Receptions, "
         "SUM(`Qte_reçue`) AS Qte_Totale FROM FACT_Assoc_Reception "
         "GROUP BY Code_Fournisseur;"),
        # DATEDIFF MySQL : DATEDIFF(fin, debut) — ordre inverse de MSSQL
        ("Quel est le delai moyen en jours entre commande et reception fournisseur ?",
         "SELECT AVG(DATEDIFF(r.Date_Reception, oa.Date_Commande)) "
         "AS Delai_Moyen_Jours FROM FACT_Ordre_Achat oa "
         "JOIN FACT_Assoc_Reception r ON r.Num_Commande = oa.Num_Commande "
         "AND r.Code_Article = oa.Code_Article;"),
        # NULLIF valide MySQL ; backtick pour Qte_reçue
        ("Quels fournisseurs ont livre en moyenne moins de 90% des quantites commandees, sur plus de 5 commandes ?",
         "SELECT Code_Fournisseur, COUNT(*) AS Nb_Lignes, "
         "AVG(1.0 * `Qte_reçue` / NULLIF(Qte_Commandee, 0)) AS Taux_Livraison "
         "FROM FACT_Ordre_Achat GROUP BY Code_Fournisseur "
         "HAVING COUNT(*) > 5 "
         "AND AVG(1.0 * `Qte_reçue` / NULLIF(Qte_Commandee, 0)) < 0.90;"),

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
        # TOP 1 → LIMIT 1 en MySQL
        ("Quel est le motif d'arret de production le plus frequent ?",
         "SELECT m.Code_Motif, m.Description, COUNT(*) AS Nb "
         "FROM FACT_PROD_Arret a JOIN DIM_Motif_Arret_Prod m "
         "ON m.Code_Motif = a.Code_Motif "
         "GROUP BY m.Code_Motif, m.Description ORDER BY Nb DESC LIMIT 1;"),
        ("Quelle est la duree totale d'arret de production par machine ?",
         "SELECT Machine, SUM(Duree) AS Duree_Totale FROM FACT_PROD_Arret "
         "GROUP BY Machine ORDER BY Duree_Totale DESC;"),
        ("Quelle est la quantite theorique et la quantite terminee par ordre de fabrication ?",
         "SELECT N_OF, Qte_Theorique, Qte_Terminee FROM FACT_PROD_Consommation;"),
        # CAST DECIMAL valide MySQL ; backtick pour %Rendement
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
        # DATEADD(DAY,30,GETDATE()) → DATE_ADD(CURDATE(), INTERVAL 30 DAY) MySQL
        ("Quels lots arrivent a peremption dans les 30 prochains jours ?",
         "SELECT Article, Num_Lot, Date_Exp FROM Fact_Prod_Stocks "
         "WHERE Date_Exp <= DATE_ADD(CURDATE(), INTERVAL 30 DAY);"),
        # Rendement % avec backtick
        ("Quel est le rendement reel moyen par OF en declaration de production ?",
         "SELECT N_OF, AVG(`%Rendement`) AS Rendement_Moyen "
         "FROM FACT_PROD_Declaration GROUP BY N_OF ORDER BY Rendement_Moyen DESC;"),

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
        # SUM() OVER() — valide MySQL 8+
        ("Quel est le ratio du CA de chaque client par rapport au CA total ?",
         "SELECT Code_Client, SUM(CA_TND) AS CA_Client, "
         "CAST(100.0 * SUM(CA_TND) / SUM(SUM(CA_TND)) OVER () AS DECIMAL(5,2)) AS Pct_CA "
         "FROM FACT_Vente GROUP BY Code_Client ORDER BY CA_Client DESC;"),
        # TOP 5 → LIMIT 5 + RANK OVER
        ("Quels sont les 5 clients avec le plus grand nombre de retours ?",
         "SELECT Code_Client, COUNT(*) AS Nb_Retours "
         "FROM FACT_Assoc_Retour GROUP BY Code_Client "
         "ORDER BY Nb_Retours DESC LIMIT 5;"),

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
        ("Quels clients ont un montant moyen de facture superieur a 5000 TND ?",
         "SELECT Code_Client, AVG(CA_TND) AS CA_Moyen_Facture FROM FACT_Vente "
         "GROUP BY Code_Client HAVING AVG(CA_TND) > 5000 "
         "ORDER BY CA_Moyen_Facture DESC;"),
        # Trimestre MSSQL → QUARTER() MySQL
        ("Quel trimestre a enregistre le plus de retards de paiement en 2024 ?",
         "SELECT QUARTER(Date_Paiement) AS Trimestre, COUNT(*) AS Nb_Retards "
         "FROM FACT_Recouvrement_Client "
         "WHERE YEAR(Date_Paiement) = 2024 "
         "AND Date_Paiement > Date_Echeance "
         "GROUP BY QUARTER(Date_Paiement) "
         "ORDER BY Nb_Retards DESC LIMIT 1;"),
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