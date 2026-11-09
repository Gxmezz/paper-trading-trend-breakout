#!/usr/bin/env bash
# Bucle del paper trader dentro de GitHub Actions:
#  - ejecuta el motor cada minuto durante ~5 h 40 min (límite de un job: 6 h)
#  - publica dashboard + estado en la rama gh-pages cada ~7 min (GitHub Pages admite ~10 publicaciones/hora)
#  - al terminar, el workflow se relanza a sí mismo (paso siguiente del workflow)
set -u
REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
LOOP_SECONDS=$(( ${LOOP_MINUTES:-340} * 60 ))
PUBLISH_EVERY=420
mkdir -p site

# Cargar el último estado publicado (gh-pages); si no existe, el de la rama antigua "state"
if git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
  git fetch -q --depth=1 origin gh-pages && git show origin/gh-pages:state.json > state.json 2>/dev/null \
    && echo "Estado cargado de gh-pages ($(wc -c < state.json) bytes)"
elif git ls-remote --exit-code --heads origin state >/dev/null 2>&1; then
  git fetch -q --depth=1 origin state && git show origin/state:state.json > state.json \
    && echo "Estado migrado de la rama state ($(wc -c < state.json) bytes)"
else
  echo "Sin estado previo: se empieza de cero"
fi

publish() {
  [ -f site/index.html ] && [ -f state.json ] || return 0
  tmp=$(mktemp -d)
  cp site/index.html state.json "$tmp/" && touch "$tmp/.nojekyll"
  ( cd "$tmp" && git init -q -b gh-pages && git add -A \
    && git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
         commit -qm "dashboard $(date -u +%FT%TZ)" \
    && git push -qf "$REPO_URL" gh-pages ) && echo "$(date -u +%T) publicado" || echo "$(date -u +%T) fallo al publicar"
  rm -rf "$tmp"
}

start=$(date +%s); last_pub=0
while [ $(( $(date +%s) - start )) -lt "$LOOP_SECONDS" ]; do
  python paper_trader.py --once 2>&1 | grep -vE "Paper trader iniciado" || true
  now=$(date +%s)
  if [ $(( now - last_pub )) -ge "$PUBLISH_EVERY" ]; then publish; last_pub=$now; fi
  sleep 60
done
publish
