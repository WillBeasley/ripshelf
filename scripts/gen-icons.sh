#!/bin/sh
# Render the PNG app icons in web/static/brand/ from app-icon-dark.svg. Needs rsvg-convert (librsvg).
set -eu
cd "$(dirname "$0")/../web/static/brand"
bg='#0f172a'
# "any" icons keep the rounded corners transparent.
rsvg-convert -w 192 -h 192 app-icon-dark.svg -o icon-192.png
rsvg-convert -w 512 -h 512 app-icon-dark.svg -o icon-512.png
# iOS rounds the corners itself and turns transparency black, so fill them.
rsvg-convert -w 180 -h 180 -b "$bg" app-icon-dark.svg -o apple-touch-icon.png
# Maskable, with a full-bleed background with the icon inside the 80% safe zone.
rsvg-convert -w 410 -h 410 --page-width 512 --page-height 512 --left 51 --top 51 -b "$bg" \
	app-icon-dark.svg -o icon-maskable-512.png
