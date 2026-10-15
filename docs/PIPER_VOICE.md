# Optional neural French narration (Piper)

From the Android Remote Studio, select **Neurale · Piper SIWIS (français)**. This
installs the standalone `piper-tts==1.8.0` command on a GitHub-hosted CPU runner
and downloads `fr_FR-siwis-medium.onnx` and its companion `.onnx.json` from the
immutable Hugging Face revision `d5de91c972ba5a13cea0e77a63fda1623faac2c1`.
The model must match SHA-256
`641d1ab097da2b81128c076810edb052b385decc8be3381814802a64a73baf99`.
Incorrect bytes **fail closed**; the video is not silently narrated by eSpeak.

Source/model card: https://huggingface.co/rhasspy/piper-voices/blob/d5de91c972ba5a13cea0e77a63fda1623faac2c1/fr/fr_FR/siwis/medium/MODEL_CARD

The SIWIS dataset is listed as **CC BY 4.0** by the model card. Attribution and
license link are included in the generated `video.credits.txt` when the
reviewed model's SHA-256 matches. This should be retained when publishing.
The Piper executable is GPL-3.0-or-later, executed as an external program and
not copied into the repository. Voice/model rights are distinct from the
program's MIT upstream license.

With a different user-provided Piper model, `video.credits.txt` explicitly
marks its license as unverified. Verify such models before distributing output.

The default remains eSpeak for all six supported languages. The SIWIS option
is French-only, needs network access to install/download, and its computational
cost/quality must be checked via actual GitHub rendering; it cannot guarantee
human-like naturalness or unrestricted free usage.
