# AikoServe

Glisse le dossier de ton site : il est servi sur ton réseau local avec un **QR code**. Scanne-le avec ton téléphone (connecté au même Wi-Fi) et ta page s'ouvre. Ensuite, **chaque modification recharge la page toute seule**, sur l'ordinateur comme sur le téléphone.

Le thème s'adapte au système : fenêtre **noire** ou **blanche** avec l'accent **bleu clair**, comme le reste de la suite Aiko.

- **Rechargement intelligent** : si seul un CSS change, les styles sont remplacés sans recharger la page (tu gardes ton scroll et l'état de tes formulaires). Les autres fichiers rechargent la page.
- **Mode application** (React, Vue, Svelte…) : toutes les adresses mènent à `index.html`.
- **Compteur d'appareils connectés**, bouton **Copier l'adresse**, et **choix de l'adresse réseau** si l'ordinateur en a plusieurs (Wi-Fi + Ethernet…).
- `node_modules`, `.git` et les fichiers temporaires sont ignorés. Aucun accès possible en dehors du dossier du site.
- **Pas de `index.html`** : la liste des fichiers s'affiche à la place.

**Premier lancement** : Windows et macOS demandent d'autoriser les connexions entrantes. Accepte-les (réseau privé), sinon le téléphone ne pourra pas se connecter.

**Bon à savoir** : le site est servi en `http` (pas `https`). Sur téléphone, certaines fonctions comme la caméra, la géolocalisation ou les service workers exigent `https` et ne marcheront pas dans ce mode. Tout le reste fonctionne (mise en page, responsive, JavaScript…).

## Téléchargements

Chaque envoi sur `main` compile les trois versions. Pour publier : **Actions → Publier une version** (numéro de la compilation + version).

| Système | Fichier |
|---|---|
| Windows 10/11 | `AikoServe-Windows-Setup.exe` (ou la version portable) |
| Mac (Apple Silicon) | `AikoServe-Mac.zip` — au premier lancement : Réglages Système → Confidentialité et sécurité → Ouvrir quand même |
| Linux x86_64 | `AikoServe-Linux-x86_64.tar.gz` → `./installer.sh` |

## Code

`app/` (Python + Qt ; le serveur n'utilise que la bibliothèque standard) — `python -m aikoserve [dossier]` · `app/tests/smoke.py` : test complet (vraies requêtes HTTP, rechargement après modification).
