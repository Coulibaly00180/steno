# Nemotron 3 Diarization

Sténo identifie les intervenants avec **Nemotron 3 Diarization** de NVIDIA, intégré à l'image Docker.

- Modèle d'origine : [nvidia/Nemotron-3-Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization), révision `f667ed73aee57d40cc39428eb768b4fd87a0a29e`, par NVIDIA.
- Fichiers utilisés : l'export ONNX quantifié en int8 de [onnx-community/Nemotron-3-Diarization-ONNX](https://huggingface.co/onnx-community/Nemotron-3-Diarization-ONNX), révision `353b6f8ad2cac3580e982d7fbdf0a010786b0406` : `onnx/model_quantized.onnx` et `onnx/model_quantized.onnx_data`. Ils ne sont pas modifiés.
- Licence : **OpenMDW 1.1** (texte complet dans `LICENSE`, à côté de ce fichier). Elle permet l'usage commercial et la redistribution, à condition de joindre la licence et les mentions d'origine.
- Le traitement autour du réseau (spectrogramme, découpage en blocs, mémoire des voix) est écrit dans Sténo (`backend/app/nemotron.py`). Il suit l'implémentation de Hugging Face transformers (Apache 2.0).

Le modèle ne sort rien du poste : il tourne localement, sans compte ni connexion au moment de l'analyse.
