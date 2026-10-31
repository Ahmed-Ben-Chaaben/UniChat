# Jeu de test NL2SQL — Base `TestDB_DataWarehouse`

Ce document rassemble des paires **question en langage naturel → requête SQL Server
attendue**, construites à partir de votre schéma réel (46 tables DIM_/FACT_/AGG_),
pour évaluer/entraîner un système NL2SQL sur les pièges les plus fréquents.

Chaque section correspond à un type de piège. Pour chaque exemple : la question,
la requête attendue, et **pourquoi c'est piégeux** (ce qu'un NL2SQL naïf a tendance
à rater).

> ⚠️ Hypothèse de travail : le terme **"jointure RH"** que vous avez utilisé n'a pas
> de correspondance directe dans ce schéma (pas de module Ressources Humaines).
> Je l'ai interprété comme **"jointure à risque d'Homonymie"** — le cas très présent
> dans votre base où une même colonne (`Code_Motif`, `Code_Client`...) sert de clé
> dans **plusieurs tables différentes**, et où un NL2SQL naïf choisit la mauvaise
> cible de jointure. Dites-moi si vous vouliez autre chose (ex: auto-jointure
> hiérarchique) et j'ajuste cette section.

---

## 1. Piège du grain (grain trap)

Le grain d'une table = ce que représente une ligne. Ici, `FACT_Vente` est au grain
"ligne de vente", `FACT_Commande_Client` est au grain "ligne de commande" (avec des
colonnes déjà agrégées comme `Total_Montant_Facture`), et `AGG_Commande` est déjà
pré-agrégé au grain "client × article". Un NL2SQL naïf mélange ces grains et
double-compte.

### 1.1
**Q :** « Quel est le chiffre d'affaires total de l'entreprise ? »
**Piège :** utiliser `AGG_Commande` (déjà agrégé) ET `FACT_Vente` (grain fin) dans
la même requête sans s'en apercevoir double-compte le CA.
```sql
SELECT SUM(CA_TND) AS CA_Total
FROM [FACT_Vente];
```

### 1.2
**Q :** « Combien de commandes le client CLI0010 a-t-il passées ? »
**Piège :** `FACT_Commande_Client` est au grain **ligne de commande**, pas
**commande**. Compter `COUNT(*)` compte les lignes, pas les commandes distinctes.
```sql
SELECT COUNT(DISTINCT Num_Commande) AS Nb_Commandes
FROM [FACT_Commande_Client]
WHERE Code_Client = 'CLI0010';
```

### 1.3
**Q :** « Quelle est la quantité totale livrée pour la commande CMD0005 ? »
**Piège :** joindre `FACT_Commande_Client` (grain ligne de commande) avec
`FACT_Assoc_Bon_Livraison` (grain ligne de bon de livraison) sur `Num_Commande`
seul crée un produit cartésien partiel (fan-out) : chaque ligne de commande est
démultipliée par chaque BL associé. Il faut agréger **avant** de joindre, ou
agréger après en acceptant le fan-out contrôlé.
```sql
SELECT
    bl.Num_Commande,
    SUM(bl.[Quantité_Livrée]) AS Quantite_Livree_Totale
FROM [FACT_Assoc_Bon_Livraison] bl
WHERE bl.Num_Commande = 'CMD0005'
GROUP BY bl.Num_Commande;
```

### 1.4
**Q :** « Quel est le CA moyen par ligne de vente et par facture ? »
**Piège :** confondre "moyenne par ligne" (`AVG` direct sur `FACT_Vente`) et
"moyenne par facture" (il faut d'abord `SUM ... GROUP BY Num_Facture`, puis
`AVG` sur ce résultat — deux grains, deux réponses différentes).
```sql
-- CA moyen par FACTURE (et non par ligne)
SELECT AVG(CA_Facture) AS CA_Moyen_Par_Facture
FROM (
    SELECT Num_Facture, SUM(CA_TND) AS CA_Facture
    FROM [FACT_Vente]
    GROUP BY Num_Facture
) AS Factures;
```

---

## 2. Agrégation temporelle

`DIM_Date` a une anomalie connue (sa clé primaire déclarée est `Num_Semaine`, pas
`Date`) et **aucune table de faits n'a de FK résolue vers `DIM_Date`** — un NL2SQL
naïf va essayer de joindre sur `DIM_Date` alors qu'il faut extraire l'année/le mois
directement depuis les colonnes date des tables de faits.

### 2.1
**Q :** « Quel est le chiffre d'affaires par mois pour l'année 2025 ? »
**Piège :** tenter une jointure vers `DIM_Date` (inexistante/non fiable) au lieu
d'extraire directement l'année/le mois de `Date_Facture`.
```sql
SELECT
    YEAR(Date_Facture)  AS Annee,
    MONTH(Date_Facture) AS Mois,
    SUM(CA_TND)          AS CA_Mensuel
FROM [FACT_Vente]
WHERE YEAR(Date_Facture) = 2025
GROUP BY YEAR(Date_Facture), MONTH(Date_Facture)
ORDER BY Mois;
```

### 2.2
**Q :** « Quelle est l'évolution du CA d'un mois sur l'autre (variation en %) ? »
**Piège :** calcul de variation nécessite un accès à la ligne précédente —
tentation d'un auto-JOIN compliqué et fragile au lieu d'une fonction fenêtre
`LAG()` (voir aussi section 6).
```sql
WITH CA_Mensuel AS (
    SELECT
        YEAR(Date_Facture)  AS Annee,
        MONTH(Date_Facture) AS Mois,
        SUM(CA_TND)          AS CA
    FROM [FACT_Vente]
    GROUP BY YEAR(Date_Facture), MONTH(Date_Facture)
)
SELECT
    Annee, Mois, CA,
    LAG(CA) OVER (ORDER BY Annee, Mois) AS CA_Mois_Precedent,
    CAST(100.0 * (CA - LAG(CA) OVER (ORDER BY Annee, Mois))
         / NULLIF(LAG(CA) OVER (ORDER BY Annee, Mois), 0) AS DECIMAL(10,2)) AS Variation_Pct
FROM CA_Mensuel
ORDER BY Annee, Mois;
```

### 2.3
**Q :** « Quel trimestre 2024 a eu le plus de retards de paiement clients ? »
**Piège :** confondre `Date_Echeance` (date prévue) et `Date_Paiement` (date
réelle) dans `FACT_Recouvrement_Client` — le NL2SQL doit comparer les deux
colonnes, pas filtrer sur une seule.
```sql
SELECT
    DATEPART(QUARTER, Date_Echeance) AS Trimestre,
    COUNT(*) AS Nb_Retards
FROM [FACT_Recouvrement_Client]
WHERE YEAR(Date_Echeance) = 2024
  AND Date_Paiement > Date_Echeance
GROUP BY DATEPART(QUARTER, Date_Echeance)
ORDER BY Nb_Retards DESC;
```

### 2.4
**Q :** « Quels sont les 100 derniers jours de vente enregistrés ? »
**Piège :** `TOP N` sans `ORDER BY` explicite sur la date renvoie un résultat non
déterministe ; il faut aussi dédupliquer les dates avant de les compter.
```sql
SELECT DISTINCT TOP 100 CAST(Date_Facture AS DATE) AS Jour_Vente
FROM [FACT_Vente]
ORDER BY Jour_Vente DESC;
```

---

## 3. Jointure + concaténation

### 3.1
**Q :** « Donne-moi la fiche client : nom, ville, pays, sur une seule ligne
texte. »
**Piège :** `DIM_Client` ne contient pas la ville/le pays (ils sont dans
`DIM_Region`, reliée via `Code_Client`, **pas** via une PK directe de
`DIM_Client`) ; il faut aussi gérer les `NULL` dans la concaténation (sinon tout
le résultat devient `NULL`).
```sql
SELECT
    c.Code_Client,
    CONCAT(c.Nom_Client, ' - ', ISNULL(r.Ville, 'Ville inconnue'),
           ', ', ISNULL(r.Pays, 'Pays inconnu')) AS Fiche_Client
FROM [DIM_Client] c
LEFT JOIN [DIM_Region] r
    ON r.Code_Client = c.Code_Client;
```

### 3.2
**Q :** « Liste les articles avec leur libellé complet : famille + description +
unité de mesure. »
**Piège :** `DIM_Article` n'a pas de colonne "famille" à jour reliée par FK
déclarée (`Famille_Article` est une colonne texte libre, pas une FK vers
`DIM_Famille_Article`) — un NL2SQL peut hallucinez une jointure inexistante.
```sql
SELECT
    Code_Article,
    CONCAT(Famille_Article, ' | ', Description, ' (', Unite_Mesure, ')') AS Libelle_Complet
FROM [DIM_Article];
```

### 3.3
**Q :** « Pour chaque bon de livraison, donne le client, l'article et la date,
sous forme d'une seule chaîne récapitulative. »
**Piège :** `FACT_Assoc_Bon_Livraison` n'a pas de `Code_Client` direct — il faut
remonter par `Num_Commande` jusqu'à `FACT_Commande_Client` pour l'obtenir (jointure
à deux sauts, facilement oubliée).
```sql
SELECT
    bl.ID_Livraison,
    CONCAT('Client ', fc.Code_Client, ' - Article ', bl.Code_Article,
           ' - livré le ', CONVERT(varchar, bl.Date_Livraison_BL, 23)) AS Recap
FROM [FACT_Assoc_Bon_Livraison] bl
LEFT JOIN [FACT_Commande_Client] fc
    ON fc.Num_Commande = bl.Num_Commande;
```

---

## 4. Agrégation sur quantité (vs comptage)

Piège classique : confondre "nombre de lignes" (`COUNT(*)`), "nombre de valeurs
distinctes" (`COUNT(DISTINCT ...)`) et "somme d'une quantité" (`SUM(Quantite)`).

### 4.1
**Q :** « Combien d'articles différents ont été vendus, et quelle quantité totale
cela représente-t-il ? »
```sql
SELECT
    COUNT(DISTINCT Code_Article) AS Nb_Articles_Distincts,
    SUM([Quantité])              AS Quantite_Totale_Vendue
FROM [FACT_Vente];
```

### 4.2
**Q :** « Quel client a commandé le plus grand nombre d'articles différents (pas
la plus grande quantité) ? »
**Piège :** confondre "plus grand nombre d'articles différents"
(`COUNT(DISTINCT Code_Article)`) avec "plus grande quantité totale"
(`SUM(Quantite_Commandee)`) — ce sont deux classements différents.
```sql
    SELECT TOP 1
        Code_Client,
        COUNT(DISTINCT Code_Article) AS Nb_Articles_Differents
    FROM [FACT_Commande_Client]
    GROUP BY Code_Client
    ORDER BY Nb_Articles_Differents DESC;
```

### 4.3
**Q :** « Quelle est la quantité rejetée par rapport à la quantité terminée en
production, par centre de charge ? »
**Piège :** `FACT_PROD_Declaration.Centre_Charge` est un champ texte libre (pas
une FK vers `DIM_Centre_Charge`) — bien vérifier qu'on agrège sur cette colonne
texte et non sur un code inexistant.
```sql
SELECT
    Centre_Charge,
    SUM(Qte_Terminee) AS Qte_Terminee_Totale,
    SUM(Qte_Rejetee)  AS Qte_Rejetee_Totale,
    CAST(100.0 * SUM(Qte_Rejetee) / NULLIF(SUM(Qte_Terminee) + SUM(Qte_Rejetee), 0)
         AS DECIMAL(5,2)) AS Taux_Rejet_Pct
FROM [FACT_PROD_Declaration]
GROUP BY Centre_Charge
ORDER BY Taux_Rejet_Pct DESC;
```

---

## 5. AVG + HAVING

### 5.1
**Q :** « Quels clients ont un montant moyen de facture supérieur à 5000 TND ? »
**Piège :** filtrer avec `WHERE` sur une moyenne (impossible, `WHERE` s'applique
avant l'agrégation) au lieu de `HAVING`.
```sql
SELECT
    Code_Client,
    AVG(CA_TND) AS CA_Moyen_Facture
FROM [FACT_Vente]
GROUP BY Code_Client
HAVING AVG(CA_TND) > 5000
ORDER BY CA_Moyen_Facture DESC;
```

### 5.2
**Q :** « Quels articles ont un prix unitaire moyen commandé supérieur à leur
prix de vente catalogue ? »
**Piège :** double niveau — il faut agréger `FACT_Commande_Client.Prix_Unitaire`
par article, PUIS comparer à `DIM_Article.Prix_Vente` via une jointure, PUIS
filtrer sur cette comparaison (pas un simple `HAVING` sur une seule table).
```sql
SELECT  
    fc.Code_Article,
    AVG(fc.Prix_Unitaire) AS Prix_Moyen_Commande,
    da.Prix_Vente
FROM [FACT_Commande_Client] fc
JOIN [DIM_Article] da
    ON da.Code_Article = fc.Code_Article
GROUP BY fc.Code_Article, da.Prix_Vente
HAVING AVG(fc.Prix_Unitaire) > CAST(da.Prix_Vente AS numeric);
```

### 5.3
**Q :** « Quels fournisseurs ont livré en moyenne moins de 90% des quantités
commandées, sur plus de 5 commandes ? »
**Piège :** deux conditions `HAVING` combinées (moyenne ET nombre de lignes) —
un NL2SQL naïf n'en met souvent qu'une.
```sql
SELECT
    Code_Fournisseur,
    COUNT(*) AS Nb_Lignes_Commande,
    AVG(1.0 * [Qte_reçue] / NULLIF(Qte_Commandee, 0)) AS Taux_Livraison_Moyen
FROM [FACT_Ordre_Achat]
GROUP BY Code_Fournisseur
HAVING COUNT(*) > 5
   AND AVG(1.0 * [Qte_reçue] / NULLIF(Qte_Commandee, 0)) < 0.90
ORDER BY Taux_Livraison_Moyen;
```

---

## 6. Fonction fenêtre (window functions)

### 6.1
**Q :** « Classe les articles par CA décroissant, avec leur rang, sans perdre les
ex-æquo. »
**Piège :** utiliser `ROW_NUMBER()` (qui casse les ex-æquo arbitrairement) au
lieu de `RANK()` ou `DENSE_RANK()`.
```sql
SELECT
    Code_Article,
    SUM(CA_TND) AS CA_Total,
    RANK() OVER (ORDER BY SUM(CA_TND) DESC) AS Rang_CA
FROM [FACT_Vente]
GROUP BY Code_Article;
```

### 6.2
**Q :** « Pour chaque région, quel est l'article le plus vendu en CA ? »
**Piège :** oublier le `PARTITION BY` (classer globalement au lieu de classer
**par région**), ou faire une sous-requête corrélée au lieu d'une fonction
fenêtre plus lisible et performante.
```sql
WITH Classement AS (
    SELECT
        Code_Region,
        Code_Article,
        SUM(CA_TND) AS CA_Article,
        ROW_NUMBER() OVER (PARTITION BY Code_Region ORDER BY SUM(CA_TND) DESC) AS rn
    FROM [FACT_Vente]
    GROUP BY Code_Region, Code_Article
)
SELECT Code_Region, Code_Article, CA_Article
FROM Classement
WHERE rn = 1;
```

### 6.3
**Q :** « Donne le cumul du CA (running total) mois après mois pour 2025. »
**Piège :** confondre somme cumulée (`SUM() OVER (ORDER BY ... ROWS UNBOUNDED
PRECEDING)`) avec une simple somme totale, ou oublier de préciser le cadre de la
fenêtre (`ROWS BETWEEN`).
```sql
WITH CA_Mensuel AS (
    SELECT MONTH(Date_Facture) AS Mois, SUM(CA_TND) AS CA
    FROM [FACT_Vente]
    WHERE YEAR(Date_Facture) = 2025
    GROUP BY MONTH(Date_Facture)
)
SELECT
    Mois, CA,
    SUM(CA) OVER (ORDER BY Mois ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS CA_Cumule
FROM CA_Mensuel
ORDER BY Mois;
```

### 6.4
**Q :** « Pour chaque client, montre sa facture la plus récente et l'écart en
jours avec la précédente. »
**Piège :** nécessite `LAG()` **partitionné par client** ET trié par date — un
oubli de `PARTITION BY Code_Client` mélange les factures de plusieurs clients.
```sql
WITH Factures AS (
    SELECT
        Code_Client, Num_Facture, Date_Facture,
        LAG(Date_Facture) OVER (PARTITION BY Code_Client ORDER BY Date_Facture) AS Date_Facture_Precedente
    FROM [FACT_Vente]
)
SELECT
    Code_Client, Num_Facture, Date_Facture,
    DATEDIFF(DAY, Date_Facture_Precedente, Date_Facture) AS Ecart_Jours
FROM Factures
ORDER BY Code_Client, Date_Facture DESC;
```

---

## 7. Jointure à risque d'homonymie ("jointure RH")

Votre schéma a **plusieurs tables différentes qui partagent exactement le même
nom de colonne PK** (`Code_Motif`) : `DIM_Motif_Arret_Prod`, `DIM_Motif_Retard`,
`DIM_Motif_Retour`, `DIM_Motif_Retour_Achat`. Un NL2SQL qui fait un simple
"matching de nom de colonne" peut joindre sur la mauvaise table.

### 7.1
**Q :** « Quel est le motif d'arrêt de production le plus fréquent ? »
**Piège :** joindre `FACT_PROD_Arret.Code_Motif` avec `DIM_Motif_Retard` ou
`DIM_Motif_Retour` (mauvaise table) au lieu de `DIM_Motif_Arret_Prod` (la seule
sémantiquement correcte pour un **arrêt de production**).
```sql
SELECT TOP 1
    m.Code_Motif,
    m.Description_Motif,
    COUNT(*) AS Nb_Occurrences
FROM [FACT_PROD_Arret] a
JOIN [DIM_Motif_Arret_Prod] m
    ON m.Code_Motif = a.Code_Motif
GROUP BY m.Code_Motif, m.Description_Motif
ORDER BY Nb_Occurrences DESC;
```

### 7.2
**Q :** « Combien de retards de commande sont dus au motif 'MOR0005' ? »
**Piège :** `FACT_Commande_Client.Code_Motif_Retard` n'a **aucune contrainte FK**
dans la base (colonne "orpheline" volontairement laissée sans relation car la
cible n'était pas certaine) — le NL2SQL doit quand même savoir la relier à
`DIM_Motif_Retard` par convention de nommage, sans planter parce qu'il n'y a pas
de contrainte déclarée.
```sql
SELECT COUNT(*) AS Nb_Commandes_Retard
FROM [FACT_Commande_Client] fc
JOIN [DIM_Motif_Retard] m
    ON m.Code_Motif = fc.Code_Motif_Retard
WHERE fc.Code_Motif_Retard = 'MOR0005';
```

### 7.3
**Q :** « Quel est le nombre de clients par code client dans `DIM_Region` ? »
**Piège :** `DIM_Region.Code_Client` est une colonne (FK vers `DIM_Client`), mais
`DIM_Region.Code_Region` est la vraie clé primaire de la table — confondre les
deux fait compter des régions au lieu de clients, ou inversement.
```sql
SELECT
    Code_Client,
    COUNT(DISTINCT Code_Region) AS Nb_Adresses_Region
FROM [DIM_Region]
GROUP BY Code_Client
ORDER BY Nb_Adresses_Region DESC;
```

---

## 8. Anti-jointure (clients/articles "sans")

### 8.1
**Q :** « Quels clients n'ont jamais passé de commande ? »
```sql
SELECT c.Code_Client, c.Nom_Client
FROM [DIM_Client] c
WHERE NOT EXISTS (
    SELECT 1 FROM [FACT_Commande_Client] fc
    WHERE fc.Code_Client = c.Code_Client
);
```

### 8.2
**Q :** « Quels articles n'ont jamais été vendus ? »
**Piège :** utiliser `LEFT JOIN ... WHERE fv.Code_Article IS NULL` en oubliant
que si `FACT_Vente.Code_Article` peut lui-même être `NULL` (ligne orpheline), le
filtre `IS NULL` devient ambigu — préférer `NOT EXISTS`, plus sûr.
```sql
SELECT da.Code_Article, da.Description
FROM [DIM_Article] da
WHERE NOT EXISTS (
    SELECT 1 FROM [FACT_Vente] fv
    WHERE fv.Code_Article = da.Code_Article
);
```

### 8.3
**Q :** « Quelles commandes n'ont jamais été livrées (aucun bon de livraison
associé) ? »
```sql
SELECT DISTINCT fc.Num_Commande
FROM [FACT_Commande_Client] fc
WHERE NOT EXISTS (
    SELECT 1 FROM [FACT_Assoc_Bon_Livraison] bl
    WHERE bl.Num_Commande = fc.Num_Commande
);
```

### 8.4
**Q :** « Quels fournisseurs n'ont livré aucun article de la famille
'Matiere_Premiere' ? »
**Piège :** anti-jointure sur un critère **indirect** (il faut d'abord relier
fournisseur → ordre d'achat → article → famille, puis chercher l'absence de
cette combinaison précise, pas juste l'absence du fournisseur).
```sql
SELECT f.Code_Fournisseur, f.Nom_Fournisseur
FROM [DIM_Fournisseur] f
WHERE NOT EXISTS (
    SELECT 1
    FROM [FACT_Ordre_Achat] oa
    JOIN [DIM_Article] da ON da.Code_Article = oa.Code_Article
    WHERE oa.Code_Fournisseur = f.Code_Fournisseur
      AND da.Type_Article = 'Matiere_Premiere'
);
```

---

## 9. Autres pièges spécifiques à ce schéma

### 9.1 — Devise mélangée (CA en devise d'origine vs CA converti)
**Q :** « Quel est le chiffre d'affaires total ? »
**Piège :** sommer `CA_Devise_Ope` directement mélange des devises différentes
(TND, EUR, USD...) sans conversion — il faut utiliser la colonne déjà convertie
`CA_TND`, pas la colonne en devise d'origine.
```sql
-- CORRECT : la colonne deja convertie
SELECT SUM(CA_TND) AS CA_Total_TND FROM [FACT_Vente];

-- INCORRECT (piege) : melange de devises, resultat non interpretable
-- SELECT SUM(CA_Devise_Ope) FROM [FACT_Vente];
```

### 9.2 — Clé composite mal agrégée
**Q :** « Quelle est la quantité totale commandée par client, tous articles
confondus, d'après les agrégats déjà calculés ? »
**Piège :** `AGG_Commande` a une clé **composite** (`Code_Client`,
`Code_Article`) : sommer sans `GROUP BY Code_Client` seul mélange les grains, et
oublier qu'une ligne = un couple client/article (pas un client).
```sql
SELECT
    Code_Client,
    SUM(Quantite_Commandee) AS Quantite_Totale_Commandee
FROM [AGG_Commande]
GROUP BY Code_Client
ORDER BY Quantite_Totale_Commandee DESC;
```

### 9.3 — Colonne FK sans contrainte déclarée (jointure "à l'aveugle")
**Q :** « Quelle est la nomenclature associée à chaque article consommé en
production ? »
**Piège :** `FACT_PROD_Consommation.Code_Nomenclature` est une colonne FK
**sans contrainte** en base (la cible `DIM_Nomenclature` a une clé composite
`Code_Nomenclature + Formule`, donc la relation simple colonne-à-colonne n'a pas
pu être posée) — un NL2SQL doit soit avertir de l'ambiguïté, soit ne joindre que
sur `Code_Nomenclature` en sachant que ce n'est pas garanti unique côté
`DIM_Nomenclature`.
```sql
SELECT
    c.Code_Article_OF,
    c.Code_Nomenclature,
    n.Formule
FROM [FACT_PROD_Consommation] c
LEFT JOIN [DIM_Nomenclature] n
    ON n.Code_Nomenclature = c.Code_Nomenclature;
-- Attention : si plusieurs Formule existent pour un meme Code_Nomenclature,
-- cette jointure demultiplie les lignes (piege du grain, voir section 1).
```

### 9.4 — Filtre booléen mal interprété (`bit`)
**Q :** « Quels documents de recouvrement sont encore ouverts ? »
**Piège :** `Ouvert` est un `BIT` (0/1) — un NL2SQL peut écrire
`WHERE Ouvert = 'Ouvert'` (comparaison texte hallucinée) au lieu de
`WHERE Ouvert = 1`.
```sql
SELECT Num_Document, Code_Client, Montant_Ouvert
FROM [FACT_Recouvrement_Client]
WHERE Ouvert = 1;
```

### 9.5 — Comparaison de colonnes presque identiques
**Q :** « Quel est le taux de service (livré vs commandé) par commande ? »
**Piège :** confondre les 4 colonnes de quantité de `FACT_Commande_Client`
(`Quantite_Commandee`, `Total_Quantite_Facturee`, `Total_Quantite_Livree`,
`Quantite_Ouverte`) — chacune a un sens métier différent, une seule est
pertinente ici.
```sql
SELECT
    Num_Commande,
    SUM(Quantite_Commandee)      AS Qte_Commandee,
    SUM(Total_Quantite_Livree)   AS Qte_Livree,
    CAST(100.0 * SUM(Total_Quantite_Livree) / NULLIF(SUM(Quantite_Commandee), 0)
         AS DECIMAL(5,2)) AS Taux_Service_Pct
FROM [FACT_Commande_Client]
GROUP BY Num_Commande
ORDER BY Taux_Service_Pct;
```

---

## Récapitulatif des mots-clés / vocabulaire métier extraits

Ce glossaire relie le **vocabulaire métier en français** (tel qu'un utilisateur
le formulerait) aux **tables/colonnes réelles** du schéma. Utile pour renforcer
le "schema linking" (l'étape où un NL2SQL doit deviner quelle table/colonne
correspond à un mot de la question).

| Terme métier (FR)                     | Table.Colonne                                          | Synonymes probables                          |
|----------------------------------------|----------------------------------------------------------|-----------------------------------------------|
| Chiffre d'affaires / CA                | `FACT_Vente.CA_TND`, `FACT_Vente.CA_Devise_Ope`           | ventes, revenu, recette                        |
| Client                                 | `DIM_Client.Code_Client` / `Nom_Client`                   | acheteur, tiers, compte client                 |
| Commande                               | `FACT_Commande_Client.Num_Commande`                       | ordre de vente, OV                             |
| Ligne de commande                      | `FACT_Commande_Client` (grain ligne)                      | position, ligne                                |
| Article / produit                      | `DIM_Article.Code_Article` / `Description`                | produit, référence, SKU                        |
| Famille d'article                      | `DIM_Article.Famille_Article`                             | catégorie, gamme (attention : `DIM_Gamme` existe aussi, à ne pas confondre) |
| Fournisseur                            | `DIM_Fournisseur.Code_Fournisseur` / `Nom_Fournisseur`    | vendeur, achat, tiers fournisseur              |
| Ordre d'achat                          | `FACT_Ordre_Achat.Num_DA`                                 | commande fournisseur, achat, OA                |
| Bon de livraison / BL                  | `FACT_Assoc_Bon_Livraison.ID_Livraison` / `Num_BL`        | livraison, expédition                          |
| Facture                                | `FACT_Vente.Num_Facture`                                  | invoice                                        |
| Recouvrement / créance                 | `FACT_Recouvrement_Client.Montant_Ouvert`                 | impayé, encaissement, règlement                |
| Retard de paiement                     | `FACT_Recouvrement_Client.Date_Echeance` vs `Date_Paiement` | client en retard, arriéré                    |
| Motif de retard                        | `DIM_Motif_Retard.Code_Motif`                             | cause du retard                                |
| Motif de retour (vente)                | `DIM_Motif_Retour.Code_Motif`                             | raison de retour client                        |
| Motif de retour (achat)                | `DIM_Motif_Retour_Achat.Code_Motif`                       | raison de retour fournisseur                   |
| Motif d'arrêt (production)             | `DIM_Motif_Arret_Prod.Code_Motif`                         | panne, incident production                     |
| Région / secteur                       | `DIM_Region.Code_Region`, `DIM_Client.Region`              | zone, territoire (⚠ 2 colonnes différentes)   |
| Site                                   | `DIM_Site.Code_Site`                                      | dépôt, usine, entrepôt                         |
| Devise                                 | `DIM_Devise.Code_Devise`                                  | monnaie                                        |
| Ordre de fabrication (OF)              | `FACT_PROD_Valorisation.N_OF`, `FACT_PROD_Declaration.N_OF` | OF, lot de production                         |
| Opération (gamme de production)        | `DIM_Operations.N_Op`                                     | poste, opération gamme                         |
| Centre de charge                       | `DIM_Centre_Charge.Code_centre_charge`, aussi texte libre dans `FACT_PROD_Declaration.Centre_Charge` | atelier, ligne de production |
| Nomenclature (BOM)                     | `DIM_Nomenclature.Code_Nomenclature` + `Formule`           | BOM, recette de fabrication                    |
| Rendement                              | `FACT_PROD_Declaration.[%Rendement]`, `DIM_Operations.Rendement` | efficacité, taux de rendement            |
| Quantité rejetée                       | `FACT_PROD_Declaration.Qte_Rejetee`                        | rebut, non-conforme                            |
| Prix de vente catalogue                | `DIM_Article.Prix_Vente`                                   | tarif, prix catalogue                          |
| Prix unitaire (commande)               | `FACT_Commande_Client.Prix_Unitaire`                       | prix négocié, prix ligne                       |
| Classe client                          | `DIM_Client.Classe_Client`                                 | segment, catégorie client                      |
| Marché                                 | `DIM_Client.Code_Marche`                                   | canal de vente, marché cible                   |

### Table des colonnes "piégeuses" (nom identique, tables différentes)

| Colonne          | Tables où elle apparaît en PK                                                                 |
|------------------|-------------------------------------------------------------------------------------------------|
| `Code_Motif`     | `DIM_Motif_Arret_Prod`, `DIM_Motif_Retard`, `DIM_Motif_Retour`, `DIM_Motif_Retour_Achat`         |
| `N_ID`           | `FACT_PROD_Consommation`, `FACT_PROD_Declaration`                                               |
| `Code_Article`   | PK unique dans `DIM_Article`, mais aussi partie de clé composite dans `AGG_Commande`, `DIM_Cout_Article` |
| `Code_Client`    | PK unique dans `DIM_Client`, mais aussi partie de clé composite dans `AGG_Commande`               |

---

## Compte final

- **34 paires question/SQL** réparties sur 9 catégories de pièges
- **~28 termes métier** cartographiés vers leurs tables/colonnes réelles
- **4 colonnes homonymes** identifiées comme sources d'ambiguïté de jointure

Dites-moi si vous voulez que je :
1. génère une version **CSV/JSON** de ces paires (pour l'ingérer dans un pipeline
   de test automatisé),
2. ajoute d'autres catégories (ex: sous-requêtes corrélées, `PIVOT`, requêtes
   récursives `CTE`),
3. ou augmente le volume (par ex. 3-5 variantes reformulées par question, pour
   tester la robustesse à la paraphrase).
