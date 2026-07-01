# Casap Visit Slot Watcher

Surveille la page publique de liste d'attente Casap (ImmoAgenda) pour un bien
donné, et envoie une notification Telegram dès qu'un créneau de visite
devient disponible.

Le script utilise **Playwright** (navigateur headless) pour charger la page
comme le ferait un visiteur humain, puis intercepte en arrière-plan la
réponse de l'appel `GetVisitSlots` vers l'API GraphQL. Aucun jeton
d'authentification n'est manipulé manuellement : c'est le site lui-même qui
gère sa session, exactement comme dans un navigateur classique.

⚠️ **Bon usage** : gardez un intervalle de vérification raisonnable (22-38s
par défaut) pour rester léger sur les serveurs de Casap, et n'utilisez ce
script que pour un usage personnel de surveillance d'un bien qui vous
intéresse réellement.

---

## 1. Fichiers du projet

- `main.py` — le script principal
- `requirements.txt` — dépendances Python
- `Dockerfile` — nécessaire pour installer le navigateur Chromium sur
  Railway/Render (Playwright a besoin de binaires navigateur, pas seulement
  des paquets pip)

---

## 2. Créer le bot Telegram

1. Ouvrez Telegram et cherchez **@BotFather**.
2. Envoyez la commande `/newbot`.
3. Donnez un nom (ex. `Casap Watcher`) puis un identifiant unique se
   terminant par `bot` (ex. `casap_watcher_bot`).
4. BotFather vous renvoie un **token** du type :
   ```
   123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw
   ```
   → c'est votre `TELEGRAM_TOKEN`.

## 3. Récupérer le CHAT_ID

1. Démarrez une conversation avec votre bot (cherchez son
   `@casap_watcher_bot` et cliquez sur **Démarrer** / envoyez `/start`).
2. Envoyez-lui n'importe quel message (ex. `salut`).
3. Dans un navigateur, allez sur :
   ```
   https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates
   ```
   (remplacez `<VOTRE_TOKEN>` par votre token, en gardant le `bot` devant).
4. Dans la réponse JSON, cherchez :
   ```json
   "chat": { "id": 123456789, ... }
   ```
   → cette valeur `id` est votre `CHAT_ID`.

   Astuce : si vous voulez recevoir les notifications dans un groupe,
   ajoutez le bot au groupe, envoyez un message dans le groupe, puis
   refaites la même requête `getUpdates` : le `chat.id` sera négatif
   (ex. `-1001234567890`), c'est normal pour un groupe.

---

## 4. Tester en local (optionnel)

```bash
python -m venv venv
source venv/bin/activate      # Windows : venv\Scripts\activate

pip install -r requirements.txt
playwright install --with-deps chromium

export TELEGRAM_TOKEN="123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
export CHAT_ID="123456789"
export ESTATE_ID="b1a30cd7-54a1-4b83-ba3c-9d55345962e7"   # optionnel, valeur par défaut déjà correcte
export HEADLESS="false"   # mettre "false" pour voir le navigateur pendant les tests

python main.py
```

Vous devriez voir des logs comme :
```
2026-07-01 09:00:00 | INFO     | Démarrage du watcher Casap.
2026-07-01 09:00:00 | INFO     | Bien surveillé : b1a30cd7-54a1-4b83-ba3c-9d55345962e7
2026-07-01 09:00:01 | INFO     | Chargement de la page (déclenche l'appel GetVisitSlots)...
2026-07-01 09:00:03 | INFO     | Requête GetVisitSlots interceptée (status=200).
2026-07-01 09:00:03 | INFO     | Slots trouvés dans cette réponse : 0
2026-07-01 09:00:03 | INFO     | Pause de 27.4s avant la prochaine vérification...
```

---

## 5. Déploiement sur Railway (recommandé)

Railway détecte automatiquement le `Dockerfile` et s'en sert pour construire
l'image (nécessaire pour installer Chromium correctement).

1. Poussez ce dossier sur un dépôt GitHub (public ou privé).
2. Allez sur [railway.app](https://railway.app) et connectez-vous.
3. Cliquez sur **New Project** → **Deploy from GitHub repo**.
4. Sélectionnez votre dépôt. Railway détecte le `Dockerfile` et build
   automatiquement.
5. Allez dans l'onglet **Variables** du service et ajoutez :
   | Variable | Valeur |
   |---|---|
   | `TELEGRAM_TOKEN` | votre token de bot |
   | `CHAT_ID` | votre chat id |
   | `ESTATE_ID` | `b1a30cd7-54a1-4b83-ba3c-9d55345962e7` (optionnel) |
   | `HEADLESS` | `true` |
6. Dans **Settings**, vérifiez que le service est bien de type **Worker**
   (pas de port HTTP à exposer — le script tourne en continu, il n'écoute
   aucun port). Si Railway insiste pour un port, vous pouvez ignorer
   l'avertissement : ce n'est pas un service web.
7. Déployez. Consultez l'onglet **Deployments → Logs** pour vérifier que le
   script tourne et affiche bien ses logs.
8. Railway garde le service actif en continu tant que le plan choisi le
   permet (vérifiez les quotas d'heures selon votre formule).

---

## 6. Déploiement sur Render (alternative)

1. Poussez le projet sur GitHub comme ci-dessus.
2. Sur [render.com](https://render.com), cliquez sur **New +** →
   **Background Worker** (et non "Web Service", puisque ce script n'expose
   aucun port HTTP).
3. Connectez votre dépôt GitHub.
4. Render détecte le `Dockerfile` automatiquement ; laissez **Environment =
   Docker**.
5. Choisissez un plan (le plan gratuit met le worker en veille après
   inactivité si c'est un Web Service — préférez un plan payant pour un
   Background Worker qui doit tourner 24/7).
6. Dans **Environment**, ajoutez les mêmes variables que pour Railway :
   `TELEGRAM_TOKEN`, `CHAT_ID`, `ESTATE_ID` (optionnel), `HEADLESS=true`.
7. Déployez et surveillez les logs dans l'onglet **Logs**.

---

## 7. Variables d'environnement disponibles

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | — | Token du bot Telegram |
| `CHAT_ID` | ✅ | — | ID du chat/groupe destinataire |
| `ESTATE_ID` | ❌ | `b1a30cd7-54a1-4b83-ba3c-9d55345962e7` | ID du bien à surveiller |
| `HEADLESS` | ❌ | `true` | `false` pour voir le navigateur (debug local uniquement) |
| `MIN_SLEEP_SECONDS` | ❌ | `22` | Borne basse du délai aléatoire entre vérifications |
| `MAX_SLEEP_SECONDS` | ❌ | `38` | Borne haute du délai aléatoire |
| `MAX_CONSECUTIVE_ERRORS` | ❌ | `10` | Nombre d'échecs avant pause longue + redémarrage navigateur |
| `LONG_BACKOFF_SECONDS` | ❌ | `300` | Durée de la pause longue en cas d'erreurs répétées |

---

## 8. Comportement en cas d'erreur

- Chaque échec de chargement de page (timeout, erreur réseau, etc.) est
  loggé et compté.
- Après `MAX_CONSECUTIVE_ERRORS` échecs consécutifs, le script fait une
  pause de `LONG_BACKOFF_SECONDS` puis redémarre complètement le navigateur
  (nouveau contexte, nouvelle session) avant de reprendre.
- Si Playwright plante entièrement (crash du process navigateur), le script
  se relance automatiquement après 60 secondes plutôt que de s'arrêter.
- Le script ne notifie qu'au **passage** de "aucun créneau" à "au moins un
  créneau" — il ne spamme pas Telegram à chaque vérification tant que la
  disponibilité reste inchangée. Si les créneaux disparaissent puis
  réapparaissent, une nouvelle notification sera envoyée.
