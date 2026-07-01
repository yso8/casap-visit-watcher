# Image officielle Playwright : Chromium + toutes les dépendances système
# sont déjà préinstallées et testées ensemble pour cette version exacte.
# Évite les incompatibilités de paquets rencontrées avec python:3.11-slim.
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Le script tourne en boucle infinie : pas de CMD "one-shot"
CMD ["python", "main.py"]