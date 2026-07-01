FROM python:3.11-slim

# Dépendances système nécessaires à Chromium (Playwright)
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installe Chromium + toutes les dépendances système requises
RUN playwright install --with-deps chromium

COPY main.py .

# Le script tourne en boucle infinie : pas de CMD "one-shot"
CMD ["python", "main.py"]