import pymysql

print("hello")

try:
    # TA CHAÎNE ORIGINALE transformée en paramètres
    conn = pymysql.connect(
        host="localhost",
        user="root",
        password="benchahmed123A!",
        database="testdb_datawarehouse",
        port=3306
    )
    print("✅ Connexion réussie à MySQL !")
    
    # Petit test pour vérifier que ça marche vraiment
    cursor = conn.cursor()
    cursor.execute("SELECT VERSION()")
    version = cursor.fetchone()
    print(f"📦 Version MySQL : {version[0]}")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print("❌ Erreur :", e)