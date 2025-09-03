#!/bin/bash
set -e

APP_NAME="dnsjumper"
VERSION="1.0-1"
BUILD_DIR="pkg_${APP_NAME}"

# تمیز کردن خروجی قبلی
rm -rf "$BUILD_DIR" "${APP_NAME}_${VERSION}.deb"

# ساختار پکیج
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/usr/local/bin"
mkdir -p "$BUILD_DIR/usr/share/applications"
mkdir -p "$BUILD_DIR/usr/share/icons/hicolor/64x64/apps"

# فایل کنترل
cat > "$BUILD_DIR/DEBIAN/control" <<EOF
Package: $APP_NAME
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Maintainer: MetiDev <mehdicode3@example.com>
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, python3-dnspython, network-manager, libcanberra-gtk3-module | libcanberra-gtk-module
Description: DNS Jumper for Linux
 A polished GUI tool to test and set DNS servers using NetworkManager.
 Features:
  - Add and save multiple DNS profiles
  - Test latency of DNS servers
  - Apply DNS instantly with one pkexec
  - Plays success sound on apply
EOF

# کپی سورس
cp main.py "$BUILD_DIR/usr/local/bin/$APP_NAME"
chmod +x "$BUILD_DIR/usr/local/bin/$APP_NAME"

# فایل desktop
cat > "$BUILD_DIR/usr/share/applications/$APP_NAME.desktop" <<EOF
[Desktop Entry]
Name=DNS Jumper
Comment=Change and test DNS profiles easily
Exec=$APP_NAME
Icon=$APP_NAME
Terminal=false
Type=Application
Categories=Network;Settings;
EOF

# آیکون (باید تو پروژه داشته باشی مثلا dnsjumper.png)
if [ -f "dnsjumper.png" ]; then
  cp dnsjumper.png "$BUILD_DIR/usr/share/icons/hicolor/64x64/apps/$APP_NAME.png"
fi

# ساخت پکیج deb
dpkg-deb --build "$BUILD_DIR" "${APP_NAME}_${VERSION}.deb"

echo "✅ پکیج ساخته شد: ${APP_NAME}_${VERSION}.deb"
echo "برای نصب: sudo apt install ./\${APP_NAME}_\${VERSION}.deb"
