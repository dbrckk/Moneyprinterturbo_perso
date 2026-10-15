# Analyse des ressources de `dbrckk/star-list`

Source examinée : [catalogue star-list](https://github.com/dbrckk/star-list/blob/main/catalog.json) (725 entrées au moment de cette analyse). Les scores sont des scores du catalogue, **pas** des garanties de compatibilité ou de fonctionnement dans notre application.

| Dépôt | Score | Licence recensée | Décision | Motif |
|---|---:|---|---|---|
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 8,2 | MIT | **Import effectué** sous `upstream/` | Préserve un moteur mature (WebUI, API, montage, audio). |
| [ollama/ollama](https://github.com/ollama/ollama) | 9,8 | MIT | **Intégration disponible** via `--ollama-model` | Script réellement produit par un LLM local, sans service facturé. |
| [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM) | 9,4 | Apache-2.0 | Candidat | Voix plus naturelle hors ligne ; vérifier coûts CPU/GPU, licences des modèles et qualité multilingue. |
| [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) | 8,2 | MIT | Candidat | Interface de montage web à étudier ; ne pas copier sans évaluer l'architecture. |
| [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) | 9,7 | GPL-3.0 | Option séparée | Images créées localement sur GPU ; pas une dépendance obligatoire. |
| [Tencent-Hunyuan/HunyuanVideo](https://github.com/Tencent-Hunyuan/HunyuanVideo) | 9,3 | Apache-2.0 | Option GPU avancée | Génération vidéo locale, trop gourmande pour la voie gratuite légère sur Android/GitHub Actions. |
| [RayVentura/ShortGPT](https://github.com/RayVentura/ShortGPT) | 8,0 | MIT | Référence technique | Idées pour la composition de courtes vidéos ; pas importé. |
| [FujiwaraChoki/MoneyPrinterV2](https://github.com/FujiwaraChoki/MoneyPrinterV2) | 8,1 | AGPL-3.0 | Analyse seulement | Obligations de copyleft réseau à étudier avant réutilisation de code. |

## Priorités vérifiables

1. **Terminé** : reproduire le workflow `script → narration → sous-titres → MP4` avec FFmpeg + eSpeak, en mode sans API, testable en CI.
2. **Terminé** : importer l'amont MIT avec licence, séparément du moteur gratuit.
3. **Implémenté dans Free Studio** : photos et vidéos locales, recherche Wikimedia Commons (CC0/domaine public), journal de crédits, repli automatique. **À améliorer** : sélection sémantique, transitions et performance des rendus.
4. **À réaliser** : brancher l'édition de projet et la prévisualisation web sur le moteur existant, plutôt que réimplémenter toute l'interface.
5. **À réaliser** : voix locales plus naturelles, cache, traitement en lot, suivi d'erreurs et tests de rendu multi-plateforme.

## Choix de sécurité / conformité

- Aucun jeton cloud payant n'est exigé dans `free_studio.py`.
- Utiliser les moteurs lourds comme **extensions facultatives**, jamais dans la chaîne gratuite minimale.
- Conserver les licences amont. Éviter les musiques et polices sans licence individuelle explicite.
- Ne pas confondre « logiciel libre/gratuit » et « génération vidéo haute qualité sans calcul matériel ».
- Les notes du catalogue peuvent vieillir : revérifier les licences et versions avant toute intégration.
