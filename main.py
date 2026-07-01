#!/usr/bin/env python3
"""
Casap Visit Slot Watcher
=========================

Surveille la page publique de liste d'attente Casap (ImmoAgenda) avec un
vrai navigateur (Playwright), intercepte les réponses réseau vers l'API
GraphQL AppSync et détecte l'apparition de créneaux de visite disponibles
(GetVisitSlots -> slots non vide). Envoie une notification Telegram dès
qu'un créneau apparaît.

Contrairement à un appel direct à l'API GraphQL, ce script ne manipule ni
ne fabrique aucun jeton d'authentification : c'est le navigateur (et le
JS du site book.casap.com) qui gère nativement sa propre session, comme
le ferait un visiteur humain. On se contente d'observer le trafic réseau
généré par la page.

Variables d'environnement :
    TELEGRAM_TOKEN          Token du bot Telegram (obligatoire)
    CHAT_ID                 ID du chat Telegram destinataire (obligatoire)
    ESTATE_ID               ID du bien à surveiller (par défaut : le bien fourni)
    HEADLESS                "true"/"false" (par défaut "true")
    MIN_SLEEP_SECONDS       borne basse du délai aléatoire (défaut 22)
    MAX_SLEEP_SECONDS       borne haute du délai aléatoire (défaut 38)
    MAX_CONSECUTIVE_ERRORS  nb d'erreurs avant pause longue + redémarrage navigateur (défaut 10)
    LONG_BACKOFF_SECONDS    durée de la pause longue en cas d'erreurs répétées (défaut 300)
"""

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone

# --- DEBUG TEMPORAIRE : liste toutes les clés d'environnement visibles ---
# (noms uniquement, jamais les valeurs) pour vérifier ce que le conteneur
# reçoit réellement, avant même d'importer Playwright/requests.
print("=" * 60, flush=True)
print("DEBUG - Clés d'environnement visibles dans ce conteneur :", flush=True)
for _k in sorted(os.environ.keys()):
    print(f"  - {_k}", flush=True)
print("=" * 60, flush=True)
# --- fin debug temporaire ---

import requests
from playwright.sync_api import (
    Playwright,
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

# --------------------------------------------------------------------------
# Configuration (variables d'environnement)
# --------------------------------------------------------------------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
ESTATE_ID = os.environ.get("ESTATE_ID", "b1a30cd7-54a1-4b83-ba3c-9d55345962e7").strip()

PAGE_URL = f"https://book.casap.com/{ESTATE_ID}/waitinglist?waitingListItemHash=&waitingListItemID="

HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"
MIN_SLEEP_SECONDS = float(os.environ.get("MIN_SLEEP_SECONDS", "22"))
MAX_SLEEP_SECONDS = float(os.environ.get("MAX_SLEEP_SECONDS", "38"))
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "10"))
LONG_BACKOFF_SECONDS = float(os.environ.get("LONG_BACKOFF_SECONDS", "300"))

GRAPHQL_HOST_FRAGMENT = "appsync-api.eu-west-1.amazonaws.com/graphql"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("casap-watcher")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def send_telegram_message(text: str) -> bool:
    """Envoie un message Telegram. Retourne True si succès."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_TOKEN ou CHAT_ID manquant : notification annulée.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info("Notification Telegram envoyée avec succès.")
            return True
        logger.error(
            "Échec envoi Telegram (status=%s) : %s", resp.status_code, resp.text[:300]
        )
        return False
    except requests.RequestException as exc:
        logger.error("Erreur réseau lors de l'envoi Telegram : %s", exc)
        return False


def notify_slots_available(slots: list) -> None:
    nb = len(slots)
    apercu = ""
    for s in slots[:5]:
        frm = s.get("from", "?")
        to = s.get("to", "?")
        apercu += f"\n• {frm} → {to}"
    if nb > 5:
        apercu += f"\n… et {nb - 5} autre(s)"

    message = (
        "🟢 <b>Créneau(x) de visite disponible(s) !</b>\n\n"
        f"Bien : <code>{ESTATE_ID}</code>\n"
        f"Nombre de créneaux : <b>{nb}</b>{apercu}\n\n"
        f"🔗 <a href=\"{PAGE_URL}\">Réserver maintenant</a>\n\n"
        f"Détecté le {utcnow_iso()}"
    )
    send_telegram_message(message)


# --------------------------------------------------------------------------
# Interception réseau
# --------------------------------------------------------------------------

class SlotWatcherState:
    """Garde en mémoire si des slots étaient déjà disponibles, pour éviter
    de spammer Telegram à chaque cycle tant que la disponibilité persiste."""

    def __init__(self) -> None:
        self.currently_available = False
        self.total_checks_seen = 0


def make_response_handler(state: SlotWatcherState):
    def handle_response(response):
        try:
            request = response.request
            if request.method != "POST":
                return
            if GRAPHQL_HOST_FRAGMENT not in request.url:
                return

            post_data = request.post_data or ""
            if "GetVisitSlots" not in post_data and "getVisitSlots" not in post_data:
                return

            logger.info("Requête GetVisitSlots interceptée (status=%s).", response.status)

            if response.status != 200:
                logger.warning("Réponse GetVisitSlots non-200 (status=%s), ignorée.", response.status)
                return

            try:
                body = response.json()
            except Exception as exc:
                logger.warning("Impossible de parser le JSON de la réponse : %s", exc)
                return

            errors = body.get("errors")
            if errors:
                logger.warning("La réponse GraphQL contient des erreurs : %s", errors)

            result = (body.get("data") or {}).get("getVisitSlots") or {}
            slots = result.get("slots") or []

            state.total_checks_seen += 1
            logger.info("Slots trouvés dans cette réponse : %d", len(slots))

            if slots:
                logger.info("Contenu des slots : %s", json.dumps(slots, ensure_ascii=False))
                if not state.currently_available:
                    logger.info("Nouvelle disponibilité détectée -> envoi Telegram.")
                    notify_slots_available(slots)
                    state.currently_available = True
                else:
                    logger.info("Créneaux toujours disponibles, notification déjà envoyée précédemment.")
            else:
                if state.currently_available:
                    logger.info("Les créneaux ne sont plus disponibles (réinitialisation de l'état).")
                state.currently_available = False

        except Exception as exc:
            logger.error("Erreur dans le gestionnaire de réponse réseau : %s", exc, exc_info=True)

    return handle_response


# --------------------------------------------------------------------------
# Boucle principale
# --------------------------------------------------------------------------

def create_browser_context(playwright: Playwright):
    browser = playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="fr-FR",
        viewport={"width": 1366, "height": 850},
    )
    page = context.new_page()
    return browser, context, page


def run() -> None:
    # --- DEBUG TEMPORAIRE : affiche la forme exacte des variables lues ---
    # (longueur + repr() pour repérer espaces, guillemets ou caractères
    # invisibles, sans exposer la valeur complète du token)
    token_preview = (
        f"len={len(TELEGRAM_TOKEN)} repr={TELEGRAM_TOKEN[:6]!r}...{TELEGRAM_TOKEN[-4:]!r}"
        if TELEGRAM_TOKEN
        else "VIDE"
    )
    chat_preview = f"len={len(CHAT_ID)} repr={CHAT_ID!r}" if CHAT_ID else "VIDE"
    logger.info("DEBUG TELEGRAM_TOKEN -> %s", token_preview)
    logger.info("DEBUG CHAT_ID -> %s", chat_preview)
    # --- fin debug temporaire ---

    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.critical(
            "TELEGRAM_TOKEN et/ou CHAT_ID ne sont pas définis. "
            "Configurez les variables d'environnement avant de lancer le script."
        )
        sys.exit(1)

    logger.info("Démarrage du watcher Casap.")
    logger.info("Bien surveillé : %s", ESTATE_ID)
    logger.info("Page cible     : %s", PAGE_URL)
    logger.info("Intervalle     : %.0f-%.0f secondes", MIN_SLEEP_SECONDS, MAX_SLEEP_SECONDS)
    logger.info("Mode headless  : %s", HEADLESS)

    state = SlotWatcherState()
    handler = make_response_handler(state)

    with sync_playwright() as playwright:
        browser, context, page = create_browser_context(playwright)
        page.on("response", handler)

        consecutive_errors = 0

        try:
            while True:
                try:
                    logger.info("Chargement de la page (déclenche l'appel GetVisitSlots)...")
                    page.goto(PAGE_URL, wait_until="networkidle", timeout=30000)
                    logger.info("Page chargée avec succès.")
                    consecutive_errors = 0

                except PlaywrightTimeoutError:
                    consecutive_errors += 1
                    logger.warning(
                        "Timeout lors du chargement de la page (%d/%d erreurs consécutives).",
                        consecutive_errors,
                        MAX_CONSECUTIVE_ERRORS,
                    )

                except Exception as exc:
                    consecutive_errors += 1
                    logger.error(
                        "Erreur lors du chargement de la page (%d/%d) : %s",
                        consecutive_errors,
                        MAX_CONSECUTIVE_ERRORS,
                        exc,
                    )

                # Protection contre les boucles d'erreurs infinies :
                # après N échecs consécutifs, on fait une pause longue puis
                # on redémarre complètement le navigateur (session/contexte propres).
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical(
                        "Trop d'erreurs consécutives (%d). Pause de %.0fs puis redémarrage du navigateur.",
                        consecutive_errors,
                        LONG_BACKOFF_SECONDS,
                    )
                    try:
                        context.close()
                        browser.close()
                    except Exception:
                        pass

                    time.sleep(LONG_BACKOFF_SECONDS)

                    browser, context, page = create_browser_context(playwright)
                    page.on("response", handler)
                    consecutive_errors = 0
                    continue

                sleep_time = random.uniform(MIN_SLEEP_SECONDS, MAX_SLEEP_SECONDS)
                logger.info("Pause de %.1fs avant la prochaine vérification...", sleep_time)
                time.sleep(sleep_time)

        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


def main() -> None:
    """Point d'entrée avec protection globale : si Playwright plante
    entièrement (crash du navigateur, etc.), on relance proprement après
    une pause plutôt que de terminer le process."""
    while True:
        try:
            run()
        except KeyboardInterrupt:
            logger.info("Arrêt demandé par l'utilisateur (Ctrl+C).")
            sys.exit(0)
        except Exception as exc:
            logger.critical("Crash global du watcher : %s", exc, exc_info=True)
            logger.info("Redémarrage complet dans 60 secondes...")
            time.sleep(60)


if __name__ == "__main__":
    main()