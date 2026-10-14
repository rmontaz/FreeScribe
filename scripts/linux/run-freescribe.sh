#!/bin/bash
# Starts FreeScribe. The first time, offers to download the built-in AI model (about 2.8 GB).
cd "$(dirname "$(readlink -f "$0")")" || exit 1
MODEL="models/gemma-2-2b-it-Q8_0.gguf"
URL="https://huggingface.co/lmstudio-community/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q8_0.gguf?download=true"
if [ ! -f "$MODEL" ] && [ -t 0 ]; then
    echo "FreeScribe's built-in AI model is not downloaded yet (about 2.8 GB)."
    read -r -p "Download it now? [y/N] " answer
    if [[ "$answer" =~ ^[Yy] ]]; then
        mkdir -p models
        if curl -L --fail -o "$MODEL.part" "$URL"; then
            mv "$MODEL.part" "$MODEL"
        else
            rm -f "$MODEL.part"
            echo "The download failed. You can still use an AI server (Settings > AI Settings)."
        fi
    fi
fi
exec ./FreeScribe "$@"
