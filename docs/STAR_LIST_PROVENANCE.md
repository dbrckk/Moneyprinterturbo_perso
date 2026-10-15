# MoneyPrinterTurbo Perso — inventaire des réutilisations et sélection `star-list`

État vérifié le 15 octobre 2026 dans [`dbrckk/star-list`](https://github.com/dbrckk/star-list/blob/main/catalog.json) (725 entrées) et le dépôt Moneyprinterturbo_perso.

**Vocabulaire :** « importé » signifie des fichiers sources effectivement présents dans ce dépôt ; « intégré » signifie une interface ou une commande vers un outil externe ; « référence » signifie que son code n'est pas utilisé. Les scores ci-dessous sont ceux du catalogue `star-list`, non une mesure de compatibilité certifiée.

## Déjà dans le produit

| Projet ou service | État réel | Où | Licence / contrainte |
| --- | --- | --- | --- |
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | **Importé** dans `upstream/` : code source, WebUI, API, scripts ; Free Studio n'utilise pas automatiquement tous ses composants | `upstream/`, `upstream/LICENSE` | MIT ; chansons et polices de droits incertains exclues |
| [ollama/ollama](https://github.com/ollama/ollama) (star-list : 9,8) | **Intégré par API** : scénario IA locale en CLI, serveur local et maintenant workflow GitHub Actions CPU, modèle Qwen2.5 1.5B téléchargé au lancement | `free_studio.py:ollama_script`, `studio_server.py`, `free-video.yml` | Logiciel MIT ; droits du modèle à vérifier séparément ; aucun code Ollama copié |
| Piper (outil externe ; non identifié dans la version consultée de star-list) | **Intégré par commande** : fonctionnement contrôlé avec un exécutable factice ; qualité vocale réelle pas encore vérifiée | `free_studio.py:make_audio` | Requiert modèle ONNX local et droits du modèle ; aucun code Piper copié |
| FFmpeg, eSpeak (outils système) | **Dépendances de production** : assemblage MP4, voix TTS de secours, sous-titres | `free_studio.py`, CI | Logiciels système, non importés comme sources GitHub |
| Wikimedia Commons (service, pas un dépôt incorporé) | **Source média conditionnelle** | `media_assets.py` | Seules licences reconnues domaine public/CC0 retenues ; droits tiers et pertinence à contrôler |
| GitHub Actions + ShipStatic | **Infrastructure** : calcul gratuit sur runner standard de dépôt public et publication HTTPS statique | `free-video.yml`, `remote/` | Limites d'utilisation et de stockage ; ShipStatic ne fait pas le rendu |

## Candidats intéressants de `star-list` à court terme

| Priorité | Dépôt | Note `star-list` | Utilité / décision |
| --- | --- | ---: | --- |
| 1 | [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM) | 9,4 | Explorer une TTS française plus naturelle. VoxCPM2 annonce le français parmi 30 langues et des poids/code Apache-2.0 ; la vitesse CPU et la qualité réelle restent à mesurer sur nos runners. |
| 2 | [motion-canvas/motion-canvas](https://github.com/motion-canvas/motion-canvas) | 9,5 | Graphismes animés modernes, titres et transitions programmatiques ; exporter des éléments compatibles avec le pipeline FFmpeg, sans réécrire le moteur. MIT. |
| 3 | [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) | 8,2 | La branche principale d'OpenCut est en réécriture : ne pas l'intégrer comme éditeur prêt à l'emploi. Comparer avec opencut-classic (version précédente) avant une intégration ; vérifier compatibilité et licences. |
| 4 | [RayVentura/ShortGPT](https://github.com/RayVentura/ShortGPT) | 8,0 | Inspirations de séquençage et sélection des médias ; comparaison fonctionnelle avant adaptation. MIT. |
| 5 | [microsoft/playwright](https://github.com/microsoft/playwright) | 9,8 | Tests automatiques d'interface sur Android/desktop en CI ; ne nécessite aucune intégration en production. Apache-2.0. |
| 6 | [huggingface/transformers](https://github.com/huggingface/transformers) | 9,9 | Exécution locale d'autres petits modèles si Ollama devient bloquant ; actuellement inutile de doubler les dépendances. Apache-2.0. |

## Candidats GPU, non prioritaires pour un service gratuit sur runner CPU

- [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) — 9,7 : images IA avancées, mais GPU préférable, et GPL-3.0 à vérifier avant redistribution de code.
- [huggingface/diffusers](https://github.com/huggingface/diffusers) — 8,2 : moteurs de diffusion multi-modèles, gourmand en calcul ; Apache-2.0 du dépôt ne couvre pas nécessairement les poids des modèles.
- [Wan-Video/Wan2.1](https://github.com/Wan-Video/Wan2.1) — 8,2 : génération vidéo avancée mais gourmand en VRAM ; modèle et restrictions de poids à vérifier.
- [Tencent-Hunyuan/HunyuanVideo](https://github.com/Tencent-Hunyuan/HunyuanVideo) — 9,3 : recherche vidéo GPU lourde, pas adaptée au rendu standard GitHub gratuit.

## À éviter comme intégration directe pour l'instant

- [FujiwaraChoki/MoneyPrinterV2](https://github.com/FujiwaraChoki/MoneyPrinterV2) — 8,1 : idées utiles ; code AGPL-3.0 avec obligations de redistribution et de service réseau. Ne pas mélanger sans revue juridique.
- Les bibliothèques d'automatisation sociale qui demandent des comptes, autorisations ou contournements de plateformes : publication uniquement via autorisations explicites et API officielles quand elles existent.

## Prochaines étapes hiérarchisées

1. Valider dans un vrai workflow `script_mode=local-ai` : téléchargement du modèle, temps CPU, scénario réellement généré, sortie H.264 + SRT. **L'intégration est testée statiquement et en interface, mais pas encore exécutée avec un vrai modèle GitHub Actions.**
2. Ajouter une voix neuronale réellement testée en français sur un runner gratuit, avec repli eSpeak.
3. Raffiner sélection visuelle et animation des scènes avant d'ajouter une timeline complète.
4. Ajouter Playwright dans la CI pour valider le site HTTPS après publication.

**Pas de garantie “100 % gratuit illimité”** : les runners standards des dépôts publics sont gratuits selon GitHub, mais soumis à politiques d'utilisation, stockage, réseau, limites et restrictions ; les GPU et services tiers peuvent avoir un coût.
