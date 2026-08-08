#!/bin/sh
# Levanta el tunel de salida hacia la DIAN (si esta configurado) y despues el servidor.
#
# ═══ POR QUE HACE FALTA UN TUNEL ═══
#
# La DIAN usa dos hosts. `muisca.dian.gov.co` responde desde cualquier parte; `api.dian.gov.co`
# acepta el TCP y CORTA EL HANDSHAKE TLS si la peticion no sale de Colombia. Medido: desde Railway
# (Virginia) falla, desde un VPS en Bogota responde 200. Sin ese host se pierden tres documentos
# —la declaracion del ano pasado, la presentada y el borrador que la DIAN precarga—.
#
# Este script abre un SOCKS5 local contra un servidor en Colombia. Quien lo usa es solo el cliente
# de `api.dian.gov.co`; el de muisca sigue saliendo directo (ver `rest/connector.py`).
#
# ═══ POR QUE UN BUCLE Y NO autossh ═══
#
# `autossh` haria lo mismo, pero es un paquete mas que instalar en la imagen. El bucle de abajo
# cabe en cuatro lineas, no agrega dependencias, y hace lo unico que se necesita: si el tunel se
# cae, volver a levantarlo. `ServerAliveInterval` detecta la caida en ~45s en vez de esperar a que
# el TCP expire solo, que puede tardar minutos.
#
# ═══ POR QUE NO SE ABORTA SI EL TUNEL NO LEVANTA ═══
#
# Porque tumbaria el servicio ENTERO por un problema que solo afecta a tres documentos de seis. Un
# backend arriba sin tunel sigue conciliando, calculando y generando el 210 — que es el nucleo. Se
# deja constancia en el log, y las peticiones a la API de la DIAN fallan con un mensaje que NOMBRA
# al tunel, para que quien opere no se ponga a revisar el portal de la DIAN.
#
# ═══ SIN CONFIGURAR, ESTO NO HACE NADA ═══
#
# Sin las dos variables el script arranca uvicorn y ya. Es el comportamiento de siempre, y el
# correcto en local o en cualquier despliegue que ya alcance la DIAN: un tunel es una dependencia
# mas que se puede caer.
set -e

if [ -n "$DECLARAS_DIAN_TUNEL_DESTINO" ] && [ -n "$DECLARAS_DIAN_TUNEL_LLAVE" ]; then
    LLAVE=/tmp/tunel_dian
    # La llave llega por variable de entorno porque en Railway no hay donde montar un archivo.
    # `printf '%s\n'` y no `echo`: OpenSSH exige el salto de linea final y `echo` no siempre lo
    # pone. Los permisos 600 no son cosmetica — ssh se niega a usar una llave que otros puedan leer.
    printf '%s\n' "$DECLARAS_DIAN_TUNEL_LLAVE" > "$LLAVE"
    chmod 600 "$LLAVE"

    PUERTO="${DECLARAS_DIAN_TUNEL_PUERTO:-1080}"
    # PUERTO SSH NO ESTANDAR, Y NO ES PARANOIA DE SEGURIDAD.
    #
    # Medido: desde fuera de Colombia el proveedor del VPS BLOQUEA el 22 —muy comun, por abuso de
    # escaneo— mientras el 2222 y el 443 pasan sin problema. Con el 22 el tunel daba "Connection
    # timed out" en bucle desde Railway, y el sintoma que llegaba a la pantalla era "no se pudo
    # conectar con la API de la DIAN".
    SSH_PUERTO="${DECLARAS_DIAN_TUNEL_SSH_PUERTO:-2222}"
    echo "tunel.dian: levantando SOCKS5 en 127.0.0.1:$PUERTO hacia $DECLARAS_DIAN_TUNEL_DESTINO:$SSH_PUERTO"

    # 127.0.0.1 y no 0.0.0.0: el SOCKS es para ESTE proceso. Escuchando en todas las interfaces
    # seria un proxy abierto para cualquiera que alcance el contenedor.
    while true; do
        ssh -N -D "127.0.0.1:$PUERTO" \
            -p "$SSH_PUERTO" \
            -i "$LLAVE" \
            -o StrictHostKeyChecking=accept-new \
            -o ExitOnForwardFailure=yes \
            -o ServerAliveInterval=15 \
            -o ServerAliveCountMax=3 \
            -o ConnectTimeout=15 \
            "$DECLARAS_DIAN_TUNEL_DESTINO" || echo "tunel.dian: se cayo, reintentando en 5s"
        sleep 5
    done &

    # SONDA DE ARRANQUE. Sin esto, "el tunel esta arriba" era una suposicion: el bucle solo habla
    # cuando ssh SALE, asi que un ssh colgado sin llegar a escuchar se ve igual que uno sano. Y el
    # sintoma llegaba disfrazado tres capas mas arriba, como "no se pudo consultar la DIAN".
    #
    # Comprueba lo unico que importa —que alguien escuche en el puerto SOCKS— y lo deja escrito.
    # Se usa python y no curl porque python seguro esta en la imagen y curl no necesariamente.
    (
        sleep 10
        python3 - "$PUERTO" <<'SONDA'
import socket, sys
puerto = int(sys.argv[1])
try:
    with socket.create_connection(("127.0.0.1", puerto), timeout=5):
        print(f"tunel.dian: OK, alguien escucha en 127.0.0.1:{puerto}", flush=True)
except OSError as e:
    print(f"tunel.dian: NADIE ESCUCHA en 127.0.0.1:{puerto} ({e}). "
          "Las consultas a api.dian.gov.co van a fallar.", flush=True)
SONDA
    ) &
fi

# `exec` para que uvicorn REEMPLACE a este shell y sea el proceso 1: asi recibe las senales de
# apagado de Railway directamente. Sin esto, un despliegue nuevo mataria el shell y dejaria a
# uvicorn terminando requests a medias.
exec uv run uvicorn declaras.api.app:app --host 0.0.0.0 --port "$PORT"
