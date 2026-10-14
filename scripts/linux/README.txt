FreeScribe for Linux (x86_64)

Start FreeScribe with:   ./run-freescribe.sh

The first time, it offers to download the built-in AI model (about 2.8 GB)
into the models/ folder next to this file. Without it, FreeScribe can use an
AI server instead (Settings > AI Settings).

Needs: a desktop session (X11 or Wayland via XWayland), PortAudio and ffmpeg:
    sudo apt install libportaudio2 ffmpeg        (Debian, Ubuntu)
    sudo dnf install portaudio ffmpeg            (Fedora)

Speech-to-text and the AI model run on the CPU in this build.
