#!/usr/bin/env bash
# Prueba manual del conector DIAN contra la API local.
#
#   ./scripts/probar.sh [puerto]
#
# Usa cedulas aleatorias en cada corrida para que el contador anti bloqueo no
# arrastre estado entre ejecuciones.
set -uo pipefail

PORT="${1:-8000}"
API="http://localhost:${PORT}"
KEY="${DECLARAS_API_KEY:-dev-key-cambiar}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CC_MAIN="10$(printf '%08d' $((RANDOM * RANDOM % 100000000)))"
CC_BAD="20$(printf '%08d' $((RANDOM * RANDOM % 100000000)))"

azul() { printf "\n\033[1;36m%s\033[0m\n" "$1"; }
ok()   { printf "  \033[0;32m✓\033[0m %s\n" "$1"; }
info() { printf "  \033[0;90m%s\033[0m\n" "$1"; }

pedir() {  # pedir <cedula> <clave> [json_extra]
  local cc="$1" clave="$2" extra="${3:-}"
  curl -s -X POST "${API}/v1/extractions" -H "X-API-Key: ${KEY}" -H "Content-Type: application/json" \
    -d "{\"id_number\":\"${cc}\",\"dian_password\":\"${clave}\",\"tax_year\":2025${extra:+,$extra}}"
}
estado()  { curl -s "${API}/v1/extractions/$1" -H "X-API-Key: ${KEY}"; }
id_de()   { python3 -c "import sys,json;print(json.load(sys.stdin).get('job_id',''))"; }
resumen() { python3 "${HERE}/_resumen.py"; }

esperar() {  # esperar <job_id> -> json final
  local id="$1" json st
  for _ in $(seq 1 60); do
    json=$(estado "$id")
    st=$(echo "$json" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
    case "$st" in SUCCEEDED|FAILED|CANCELLED|AWAITING_CHALLENGE) echo "$json"; return 0 ;; esac
    sleep 0.4
  done
  estado "$id"
}

correr() {  # correr <cedula> <clave> [extra] -> encola, espera y resume
  local id; id=$(pedir "$1" "$2" "${3:-}" | id_de)
  if [ -z "$id" ]; then echo "    (la API rechazo la peticion de entrada)"; return 1; fi
  esperar "$id" | resumen
}

azul "0. Salud del servicio"
curl -s "${API}/health" | python3 -m json.tool | sed 's/^/  /'
info "cedula de esta corrida: ${CC_MAIN}"

azul "1. Extraccion exitosa: los 5 documentos"
job=$(pedir "$CC_MAIN" "clave-buena" | id_de)
final=$(esperar "$job"); echo "$final" | resumen

azul "2. Descargar un documento"
url=$(echo "$final" | python3 -c "import sys,json;print(json.load(sys.stdin)['documents'][0]['download_url'])")
info "GET ${url:0:60}..."
curl -s "${API}${url}" -H "X-API-Key: ${KEY}" | head -c 55; echo ""
ok "bytes recibidos"

azul "3. Pedir solo RUT y EXOGENA"
correr "$CC_MAIN" "ok" '"doc_types":["RUT","EXOGENA"]'

azul "4. Exito parcial: la DIAN no publico la exogena"
correr "$CC_MAIN" "clave-noexo"

azul "5. Portal caido (reintentable)"
correr "$CC_MAIN" "clave-down"

azul "6. Verificacion de identidad: patron relevo completo"
job=$(pedir "$CC_MAIN" "clave-challenge" | id_de)
esperar "$job" | resumen
info "respondo mal (0000):"
curl -s -o /dev/null -w "    HTTP %{http_code}  (esperado 401)\n" -X POST "${API}/v1/extractions/${job}/challenge" \
  -H "X-API-Key: ${KEY}" -H "Content-Type: application/json" -d '{"answers":["0000"]}'
info "respondo bien (1234):"
curl -s -o /dev/null -w "    HTTP %{http_code}  (esperado 200)\n" -X POST "${API}/v1/extractions/${job}/challenge" \
  -H "X-API-Key: ${KEY}" -H "Content-Type: application/json" -d '{"answers":["1234"]}'
esperar "$job" | resumen

azul "7. Clave mala: consume intentos y frena antes de bloquear la cuenta"
info "cedula dedicada: ${CC_BAD}"
for n in 1 2; do
  info "intento $n:"
  correr "$CC_BAD" "clave-bad"
done
info "tercer intento (la API debe rechazar de entrada):"
curl -s -D /tmp/declaras-h.txt -X POST "${API}/v1/extractions" -H "X-API-Key: ${KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"id_number\":\"${CC_BAD}\",\"dian_password\":\"clave-bad\",\"tax_year\":2025}" \
  | python3 -m json.tool | sed 's/^/    /'
grep -i "x-retryable" /tmp/declaras-h.txt | sed 's/^/    /'

azul "8. Seguridad: llave de API invalida"
curl -s -o /dev/null -w "    HTTP %{http_code}  (esperado 401)\n" -X POST "${API}/v1/extractions" \
  -H "X-API-Key: llave-mala" -H "Content-Type: application/json" -d '{}'

azul "9. La clave nunca aparece en los logs"
if grep -q "clave-buena\|clave-bad\|clave-challenge\|clave-noexo" /tmp/declaras-api.log 2>/dev/null; then
  printf "    \033[0;31m✗ FUGA DE SECRETO EN LOGS\033[0m\n"
else
  ok "0 apariciones de claves en /tmp/declaras-api.log"
fi

azul "10. Documentos en disco"
find var/documents -type f 2>/dev/null | sed "s|var/documents/|    |" | head -14

printf "\n\033[1;32mListo. Swagger interactivo: %s/docs\033[0m\n\n" "$API"
