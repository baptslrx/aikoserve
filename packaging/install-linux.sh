#!/bin/bash
# Installe AikoServe pour l'utilisateur courant (pas besoin de sudo)
set -e
cd "$(dirname "$0")"
DEST="$HOME/.local/share/aikoserve"
mkdir -p "$DEST" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
pkill -x AikoServe 2>/dev/null || true
rm -rf "$DEST/app"
cp -r AikoServe "$DEST/app"
cp AikoServe/_internal/assets/icon-256.png "$HOME/.local/share/icons/hicolor/256x256/apps/aikoserve.png" 2>/dev/null || true
cat > "$HOME/.local/share/applications/aikoserve.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=AikoServe
Comment=Serveur local avec QR code
Exec=$DEST/app/AikoServe
Icon=aikoserve
Categories=Utility;
DESK
echo "✓ AikoServe installé. Lance-le depuis ton menu d'applications (ou : $DEST/app/AikoServe)"
if ! ldconfig -p 2>/dev/null | grep -q libxcb-cursor; then
  echo "ℹ Si l'appli ne démarre pas : sudo apt install libxcb-cursor0"
fi
nohup "$DEST/app/AikoServe" >/dev/null 2>&1 &
