#!/bin/sh
# volume 是空的（或掛的是 bind mount）時補齊子目錄，再執行指令。
set -e
for d in reports data state logs; do
  mkdir -p "/var/lib/newsletter/$d"
done
exec "$@"
