#!/bin/sh
set -eu
cd "$(dirname "$0")"
temporary=$(mktemp libgeometry.XXXXXX.so)
trap 'rm -f "$temporary"' 0 HUP INT TERM
"${CXX:-c++}" -O3 -std=c++17 -shared -fPIC -fno-fast-math -ffp-contract=off -fopenmp geometry.cpp -o "$temporary"
chmod 644 "$temporary"
mv "$temporary" libgeometry.so
