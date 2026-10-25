#!/usr/bin/env bash
# Install the ROBOT jar that app/owl_format.py runs on every publish, at the release the data
# repo pins (KrishnaTO/ARI tools/robot.version). Both must name the same release, or every
# publish fails the data repo's OWL format check. Needs Java 11+ on PATH (DEPLOY.md).
#
#   deploy/install-robot.sh [dir]      default dir: /opt/ari/robot  ->  <dir>/robot.jar
set -euo pipefail

ROBOT_VERSION=1.9.11
ROBOT_SHA256=53cd275c8e4c0dad1fc2c1c34174876795cd7d6b6926e1444adcbb0cb3c7ab26
DIR="${1:-/opt/ari/robot}"

mkdir -p "$DIR"
curl -fsSL -o "$DIR/robot.jar.part" \
  "https://github.com/ontodev/robot/releases/download/v$ROBOT_VERSION/robot.jar"
echo "$ROBOT_SHA256  $DIR/robot.jar.part" | sha256sum -c --quiet
chmod 0644 "$DIR/robot.jar.part"
mv -f "$DIR/robot.jar.part" "$DIR/robot.jar"
java -jar "$DIR/robot.jar" --version
