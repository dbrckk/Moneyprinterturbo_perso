#!/usr/bin/env bash
# One-command launcher for MoneyPrinter's private, loopback-only Android studio.
# Runs from a checked-out repository. Never fetches or executes remote scripts,
# accesses the Android gallery, elevates privileges, or enables LAN access.
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
MoneyPrinterTurbo Perso — démarrage Android sans PC

  bash scripts/termux-start.sh             Vérifier puis ouvrir le studio local
  bash scripts/termux-start.sh --doctor    Diagnostic rapide sans serveur
  bash scripts/termux-start.sh --doctor-full
                                          Vrai test MP4 synthétique puis sortie
  bash scripts/termux-start.sh --help      Afficher cette aide

Le studio écoute uniquement sur http://127.0.0.1:8765.
Les fichiers de la galerie restent sur le téléphone.
Aucune connexion GitHub, clé payante ou téléchargement IA n'est requis.
USAGE
}

mode="start"
case "\${1:-}" in
  "") ;;
  --doctor) mode="doctor" ;;
  --doctor-full) mode="doctor-full" ;;
  --help|-h) usage; exit 0 ;;
  *) printf 'Option inconnue : %s\n' "$1" >&2; usage >&2; exit 2 ;;
esac
if (($# > 1)); then
  echo "Une seule option est acceptée." >&2
  exit 2
fi

if [[ "\${PREFIX:-}" != */com.termux/files/usr ]] || ! command -v pkg >/dev/null 2>&1; then
  echo "Ce lanceur nécessite Termux officiel sur Android (et non un PC ou GitHub Actions)." >&2
  echo "Guide : https://github.com/dbrckk/Moneyprinterturbo_perso/blob/main/docs/ANDROID_LOCAL_MEDIA.md" >&2
  exit 2
fi

repo="$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$repo/studio_server.py" || ! -f "$repo/studio_doctor.py" ]]; then
  echo "Dépôt incomplet : studio_server.py et studio_doctor.py sont requis." >&2
  exit 2
fi
cd -- "$repo"

# Install only the missing Termux packages. Do not update or overwrite source
# files automatically: users may have local changes and mobile data limits.
packages=()
if ! command -v python3 >/dev/null 2>&1; then
  packages+=(python)
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  packages+=(ffmpeg)
fi
if ! command -v espeak >/dev/null 2>&1; then
  packages+=(espeak)
fi
if ((${#packages[@]} > 0)); then
  printf 'Installation des outils libres manquants : %s\n' "\${packages[*]}"
  pkg install -y "\${packages[@]}"
fi
for executable in python3 ffmpeg ffprobe espeak; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    printf 'Outil requis absent après installation : %s\n' "$executable" >&2
    echo "Dans Termux, essayer : pkg update -y && pkg install -y python ffmpeg espeak" >&2
    exit 1
  fi
done

if [[ "$mode" == "doctor-full" ]]; then
  echo "Contrôle complet : deux scènes synthétiques, voix et MP4 H.264/AAC."
  exec python3 studio_server.py --doctor-full
fi

# Run a real, short offline H.264/AAC + French voice check before attempting
# to launch the HTTP server. If it fails, leave existing files untouched.
echo "Contrôle initial de FFmpeg, de la voix française et du stockage…"
if ! python3 studio_server.py --doctor; then
  echo "Diagnostic non validé : le serveur n'a pas été démarré." >&2
  echo "Lire les erreurs ci-dessus puis essayer pkg update -y." >&2
  exit 1
fi
if [[ "$mode" == "doctor" ]]; then
  echo "Diagnostic terminé : le serveur n'a pas été démarré."
  exit 0
fi

printf '\n%s\n' \
  'Studio prêt : ouvrir http://127.0.0.1:8765 dans le navigateur de ce téléphone.' \
  'Sélectionner ses photos/vidéos dans la galerie depuis le site local.' \
  'Garder Termux actif pendant le rendu. Ctrl+C pour arrêter.' \
  'Les fichiers importés ne sont pas transférés sur GitHub.'
# Explicit loopback and empty local token: never expose the private video
# importer on Wi-Fi or the public Internet.
exec python3 studio_server.py --host 127.0.0.1 --port 8765 --token ""
