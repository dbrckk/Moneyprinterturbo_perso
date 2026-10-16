# Alignement acoustique des sous-titres (CPU, facultatif)

Les sous-titres progressifs ont désormais deux modes, **sans API payante**.

- `--word-alignment estimated` (**défaut**) : répartit la durée réelle de chaque phrase entre ses mots selon leur longueur. Rapide et déterministe, mais **pas un alignement phonétique**.
- `--word-alignment forced` : utilise [stable-ts 2.19.1](https://pypi.org/project/stable-ts/2.19.1/) et le modèle Whisper `tiny` sur CPU pour **analyser l'audio produit et retrouver le début de chaque mot du scénario fourni**. Aucune transcription inventée ne remplace la narration. Un script incohérent avec les mots renvoyés par le modèle est rejeté scène par scène. Les scènes non validées utilisent explicitement l'estimation et des avertissements sont écrits dans le JSON. Si aucune scène n'est correctement alignée, le rendu échoue plutôt que de se faire passer pour « aligné ».

Le code de stable-ts (MIT) n'est **pas copié** dans ce dépôt : c'est une dépendance optionnelle. Son dépôt [jianfch/stable-ts](https://github.com/jianfch/stable-ts) a été **archivé le 30 mai 2026** ; cette intégration est isolée pour faciliter son remplacement par une solution activement maintenue si nécessaire. Le modèle Whisper `tiny` est téléchargé une première fois par stable-ts. Les dépendances PyTorch, le téléchargement du modèle et les calculs CPU supplémentaires allongent le premier rendu, notamment sur GitHub Actions.

## Utilisation

```bash
sudo apt-get install -y ffmpeg espeak
python -m pip install 'stable-ts==2.19.1'
python free_studio.py --topic 'Un paysage marin' --script 'La mer est magnifique. Le vent caresse la côte.' \
  --lang fr --resolution 360 --caption-style kinetic --word-alignment forced \
  --output outputs/mer.mp4
```

Depuis Android, sélectionner « Sous-titres progressifs » et « Alignement audio Whisper (CPU) ». Le workflow GitHub n'installe la dépendance lourde que si cette option est sélectionnée.

Les livrables comprennent `video.mp4`, `video.srt` (modifiable), `video.ass` (effets de surlignage), `video.alignment.json` (horodatages et indicateurs de fiabilité du mode sélectionné), et le rapport de contrôle qualité.

## Limites et contrôles

L'alignement est **acoustique**, pas une garantie d'exactitude mot à mot. Les modèles peuvent se tromper sur les homophones, noms propres, voix de synthèse et pauses. Le contrôleur vérifie les mots attendus, leur ordre et les bornes de chaque scène ; les résultats douteux sont reclassés en estimation. Aucun fichier audio n'est envoyé à un service tiers, mais les modèles sont téléchargés au premier usage. Les voix Piper/eSpeak et les fichiers de sous-titres sont inchangés en mode par défaut.