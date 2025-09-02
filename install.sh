#!/bin/bash
set -e

PKG_NAME="dnsjumper"
PKG_VERSION="1.0-1"
DEB_FILE="${PKG_NAME}_${PKG_VERSION}.deb"

BUILD_DIR=$(mktemp -d)

echo "[*] Preparing package build directory..."
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR/usr/share/applications"
mkdir -p "$BUILD_DIR/usr/share/icons/hicolor/64x64/apps"

cp main.py "$BUILD_DIR/usr/bin/dnsjumper"
chmod +x "$BUILD_DIR/usr/bin/dnsjumper"

cat > "$BUILD_DIR/usr/share/applications/dnsjumper.desktop" <<EOF
[Desktop Entry]
Name=DNS Jumper
Exec=dnsjumper
Icon=dnsjumper
Type=Application
Categories=Network;Utility;
EOF

if [ -f "icon.png" ]; then
    cp icon.png "$BUILD_DIR/usr/share/icons/hicolor/64x64/apps/dnsjumper.png"
fi

cat > "$BUILD_DIR/DEBIAN/control" <<EOF
Package: $PKG_NAME
Version: $PKG_VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: MetiDev <mehdicode3@gmail.com>
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, python3-dnspython, network-manager, libcanberra-gtk3-module | libcanberra-gtk-module
Description: DNS Jumper for Linux
 A polished GUI tool to test and set DNS servers using NetworkManager.
EOF

echo "[*] Building .deb package..."
dpkg-deb --build "$BUILD_DIR" "$DEB_FILE"

echo "[*] Installing package..."
sudo dpkg -i "$DEB_FILE" || sudo apt-get -f install -y

echo "[+] Done! Run with: dnsjumper"
