#!/bin/bash

# Ensure the script exits if any command fails
set -e

# Define the name of the executable and the main Python script
EXECUTABLE_NAME="FreeScribe"
MAIN_SCRIPT="src/FreeScribe.client/client.py"
ICON_PATH="src/FreeScribe.client/assets/logo.ico"

# Run PyInstaller to create the standalone executable
pyinstaller client-mac.spec --noconfirm

# Refuse to ship an app that cannot open its window: Tcl's and Tk's scripts must be inside.
for script in _tcl_data/init.tcl _tk_data/tk.tcl; do
    if [ -z "$(find "dist/$EXECUTABLE_NAME.app/Contents" -path "*/$script" -print -quit)" ]; then
        echo "Build check failed: $script is missing from dist/$EXECUTABLE_NAME.app" >&2
        exit 1
    fi
done
echo "Build check: Tcl and Tk scripts are inside the app."

# Print a message indicating that the build is complete
echo "Build complete. Executable created: $DIST_PATH/$EXECUTABLE_NAME.app"