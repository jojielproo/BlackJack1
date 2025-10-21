# servidor.py
"""
Servidor de Blackjack (banca = servidor, 1–4 clientes) sobre sockets TCP.

Protocolo (JSON por línea):
- Cliente -> Servidor:
    {"tipo":"CONFIGURAR_NOMBRE","nombre": str}
    {"tipo":"INICIAR"}
    {"tipo":"APOSTAR","monto": int}
    {"tipo":"CANCELAR_APUESTA"}
    {"tipo":"SUBIR"}        # pedir carta
    {"tipo":"QUEDARSE"}     # plantarse
    {"tipo":"DOBLAR"}       # doblar (solo con 2 cartas y saldo suficiente)
    {"tipo":"DIVIDIR"}      # dividir (par, saldo >= apuesta)
    {"tipo":"SALIR"}

- Servidor -> Cliente:
    {"tipo":"UNIDO","nombre": str}
    {"tipo":"RENOMBRADO","antes": str,"ahora": str}
    {"tipo":"APUESTAS_ABIERTAS"}
    {"tipo":"PREGUNTAR_APUESTA","nombre": str}
    {"tipo":"APUESTA_OK","nombre": str,"monto": int}
    {"tipo":"RONDA_INICIADA"}
    {"tipo":"ESTADO", ...}                 # ver Mesa.estado_json()
    {"tipo":"RESULTADOS","banca":[(r,p),...],
     "total_banca":int,"detalle":[(nombre, mano_idx, "gana|empata|pierde", delta),...]}
    {"tipo":"INFO","mensaje": str}
    {"tipo":"ERROR","mensaje": str}
    {"tipo":"SALIO","nombre": str}

Reglas implementadas:
- Mazo de 6 barajas; se rebaraja automáticamente si quedan < 52 cartas.
- Valor de As = 11 o 1 (se ajusta para no pasarse).
- La banca roba hasta alcanzar 17 o más (se planta en 17+).
- Pagos: gana 1:1, empate devuelve la apuesta, pierde 0.
- Durante la ronda, la banca muestra solo la 1ª carta (la 2ª oculta).
- Soporte de dividir (par) y doblar (con exactamente 2 cartas).
- Tolerante a desconexiones en plena ronda: avanza turno si el actual se va.
"""

from __future__ import annotations

import json
import random
import socket
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# -------------------- Configuración de red --------------------

HOST = "0.0.0.0"
PORT = 35001
MAX_JUGADORES = 4

# -------------------- Cartas y utilidades --------------------

VALORES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11,
}
PALOS = ["♠", "♥", "♦", "♣"]
RANGOS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
Carta = Tuple[str, str]  # (rango, palo)


def nuevo_mazo() -> List[Carta]:
    """Crea un mazo de 6 barajas mezclado."""
    mazo = [(r, p) for r in RANGOS for p in PALOS] * 6
    random.shuffle(mazo)
    return mazo


def valor_mano(mano: List[Carta]) -> int:
    """Calcula el valor de una mano ajustando ases (11->1) si es necesario."""
    total, ases = 0, 0
    for r, _ in mano:
        total += VALORES[r]
        if r == "A":
            ases += 1
    while total > 21 and ases:
        total -= 10
        ases -= 1
    return total


# -------------------- Modelo de juego --------------------

@dataclass
class Jugador:
    """Estado de un jugador en la mesa."""
    conn: socket.socket
    nombre: str
    saldo: int = 500
    manos: List[List[Carta]] = field(default_factory=list)
    apuestas: List[int] = field(default_factory=list)
    espera_apuesta: bool = False  # True: el servidor espera su decisión de apostar/cancelar


class Mesa:
    """Orquesta el estado de la partida y la comunicación con los clientes."""

    def __init__(self) -> None:
        self.jugadores: List[Jugador] = []
        self.banca: List[Carta] = []
        self.mazo: List[Carta] = nuevo_mazo()

        self.en_apuestas: bool = False
        self.en_ronda: bool = False
        self.turno_idx: Optional[int] = None
        self.mano_turno_idx: Optional[int] = None

    # ---------- Envío / estado ----------

    @staticmethod
    def _safe_send(conn: socket.socket, obj: dict) -> None:
        """Envía JSON por la conexión; ignora errores de red."""
        try:
            conn.sendall((json.dumps(obj) + "\n").encode())
        except Exception:
            pass

    def broadcast(self, obj: dict) -> None:
        """Envía un mensaje a todos los jugadores conectados."""
        for j in list(self.jugadores):
            self._safe_send(j.conn, obj)

    def broadcast_estado(self) -> None:
        """Envía el snapshot de estado a todos los jugadores."""
        self.broadcast(self.estado_json())

    # ---------- Motor de cartas ----------

    def barajar(self) -> None:
        """Rebaraja cuando el mazo se queda bajo (umbral = 52)."""
        if len(self.mazo) < 52:
            self.mazo = nuevo_mazo()

    # ---------- Vistas / serialización ----------

    def vista_banca(self) -> List[Carta]:
        """
        Representación de la banca para clientes.
        - En ronda: 1ª carta visible, 2ª oculta.
        - Fuera de ronda: todas las cartas descubiertas.
        """
        if self.en_ronda and len(self.banca) >= 2:
            return [self.banca[0], ("?", "?")]
        return list(self.banca)

    def estado_json(self) -> dict:
        """Estructura de estado que consumen los clientes."""
        turno = (
            self.jugadores[self.turno_idx].nombre
            if (self.turno_idx is not None and 0 <= self.turno_idx < len(self.jugadores))
            else None
        )
        return {
            "tipo": "ESTADO",
            "banca": self.vista_banca(),
            "jugadores": [
                {
                    "nombre": j.nombre,
                    "saldo": j.saldo,
                    "manos": [{"cartas": m} for m in j.manos],
                    "apuestas": j.apuestas,
                }
                for j in self.jugadores
            ],
            "turno": turno,
            "mano_turno_idx": self.mano_turno_idx,
        }

    # ---------- Gestión de turnos ----------

    def _primer_idx_activo(self) -> Optional[int]:
        """Índice del primer jugador con apuesta y mano viva."""
        for i, j in enumerate(self.jugadores):
            if j.apuestas and j.manos:
                return i
        return None

    def _siguiente_idx_activo(self, idx_actual: int) -> Optional[int]:
        """Índice del siguiente jugador activo a partir de idx_actual."""
        for i in range(idx_actual + 1, len(self.jugadores)):
            j = self.jugadores[i]
            if j.apuestas and j.manos:
                return i
        return None

    def siguiente_turno(self) -> None:
        """
        Avanza el turno:
        - Siguiente mano del mismo jugador (si dividió), o
        - Siguiente jugador activo; si no queda nadie, resuelve la banca.
        """
        if not self.en_ronda or self.turno_idx is None:
            return

        actual = (
            self.jugadores[self.turno_idx]
            if 0 <= self.turno_idx < len(self.jugadores)
            else None
        )

        # Si el jugador actual ya no tiene manos, saltar al siguiente.
        if not actual or not actual.apuestas or not actual.manos:
            nxt = self._siguiente_idx_activo(self.turno_idx)
            if nxt is None:
                self.turno_idx = None
                self.mano_turno_idx = None
                self.jugar_banca()
            else:
                self.turno_idx = nxt
                self.mano_turno_idx = 0
                self.broadcast_estado()
            return

        mi = self.mano_turno_idx or 0

        # Siguiente mano del mismo jugador (tras dividir).
        if mi + 1 < len(actual.manos):
            self.mano_turno_idx = mi + 1
            self.broadcast_estado()
            return

        # Siguiente jugador activo.
        nxt = self._siguiente_idx_activo(self.turno_idx)
        if nxt is None:
            self.turno_idx = None
            self.mano_turno_idx = None
            self.jugar_banca()
        else:
            self.turno_idx = nxt
            self.mano_turno_idx = 0
            self.broadcast_estado()

    # ---------- Resolución de banca y pagos ----------

    def jugar_banca(self) -> None:
        """La banca roba hasta 17+ y luego se liquidan todas las manos."""
        while valor_mano(self.banca) < 17:
            self.barajar()
            self.banca.append(self.mazo.pop())

        total_banca = valor_mano(self.banca)
        resultados: List[Tuple[str, int, str, int]] = []

        for j in self.jugadores:
            if not j.manos:
                continue
            # Seguridad: alinear longitudes apuestas/manos
            while len(j.apuestas) < len(j.manos):
                j.apuestas.append(0)

            for idx, mano in enumerate(j.manos):
                ap = j.apuestas[idx]
                total = valor_mano(mano)

                if total > 21:
                    resultados.append((j.nombre, idx, "pierde", -ap))
                elif total_banca > 21 or total > total_banca:
                    j.saldo += ap * 2
                    resultados.append((j.nombre, idx, "gana", ap))
                elif total == total_banca:
                    j.saldo += ap
                    resultados.append((j.nombre, idx, "empata", 0))
                else:
                    resultados.append((j.nombre, idx, "pierde", -ap))

        # Log de consola (útil en clase / demo)
        try:
            print(f"[RESULTADOS] Banca={total_banca} | Detalle={resultados}")
        except Exception:
            pass

        self.broadcast(
            {
                "tipo": "RESULTADOS",
                "banca": self.banca,
                "total_banca": total_banca,
                "detalle": resultados,
            }
        )

        # Reset de flags y manos para la siguiente ronda
        self.en_ronda = False
        self.en_apuestas = False
        for j in self.jugadores:
            j.manos = []
            j.apuestas = []
            j.espera_apuesta = False

    # ---------- Ciclo de apuestas ----------

    def abrir_apuestas(self) -> None:
        """Abre la fase de apuestas y pide apuesta a todos los conectados."""
        self.en_apuestas = True
        self.en_ronda = False
        self.banca = []

        for j in self.jugadores:
            j.manos = []
            j.apuestas = []
            j.espera_apuesta = True

        try:
            print("[SERVIDOR] Apuestas abiertas.")
        except Exception:
            pass

        self.broadcast({"tipo": "APUESTAS_ABIERTAS"})
        for j in self.jugadores:
            self._safe_send(j.conn, {"tipo": "PREGUNTAR_APUESTA", "nombre": j.nombre})

    def evaluar_inicio_ronda(self) -> None:
        """
        Si todos los conectados respondieron (apostar o cancelar) y hay
        al menos una apuesta, reparte y comienza la ronda; si nadie apostó,
        cancela la fase de apuestas.
        """
        if not self.en_apuestas:
            return

        conectados = [j for j in self.jugadores if j.conn]
        if not conectados:
            return

        todos_respondieron = all(not j.espera_apuesta for j in conectados)
        hay_apuestas = any(j.apuestas for j in conectados)
        if not todos_respondieron:
            return

        if not hay_apuestas:
            self.en_apuestas = False
            try:
                print("[SERVIDOR] Nadie apostó. Se cancelan apuestas.")
            except Exception:
                pass
            self.broadcast({"tipo": "INFO", "mensaje": "Nadie apostó. Apuestas canceladas."})
            self.broadcast_estado()
            return

        # Arranca ronda
        self.en_apuestas = False
        self.en_ronda = True
        self.banca = []
        self.barajar()
        self.banca = [self.mazo.pop(), self.mazo.pop()]

        for j in self.jugadores:
            j.manos = [[self.mazo.pop(), self.mazo.pop()]] if j.apuestas else []

        self.turno_idx = self._primer_idx_activo()
        self.mano_turno_idx = 0 if self.turno_idx is not None else None

        try:
            print("[SERVIDOR] Ronda iniciada.")
        except Exception:
            pass

        self.broadcast({"tipo": "RONDA_INICIADA"})
        self.broadcast_estado()


# -------------------- Concurrencia --------------------

mesa = Mesa()
lock = threading.Lock()


# -------------------- Manejo por cliente --------------------

def manejar_cliente(conn: socket.socket, addr: Tuple[str, int]) -> None:
    """
    Hilo de atención por cliente. Lee mensajes JSON (una línea por mensaje)
    y aplica la lógica en una sección crítica protegida por `lock`.
    """
    global mesa

    # Cupo de la mesa
    with lock:
        if len(mesa.jugadores) >= MAX_JUGADORES:
            Mesa._safe_send(conn, {"tipo": "ERROR", "mensaje": "Mesa llena (máx 4)."})
            conn.close()
            return

    jugador = Jugador(conn=conn, nombre=f"Jugador{addr[1]}")

    with lock:
        mesa.jugadores.append(jugador)
    mesa.broadcast({"tipo": "UNIDO", "nombre": jugador.nombre})
    try:
        print(f"[SERVIDOR] Se conectó {jugador.nombre} desde {addr}")
    except Exception:
        pass

    try:
        for line in conn.makefile("rb"):
            try:
                msg = json.loads(line.decode().strip())
            except Exception:
                continue

            tipo = msg.get("tipo")

            with lock:
                # ---------- Identidad ----------
                if tipo == "CONFIGURAR_NOMBRE":
                    antes = jugador.nombre
                    jugador.nombre = msg.get("nombre", jugador.nombre)
                    mesa.broadcast({"tipo": "RENOMBRADO", "antes": antes, "ahora": jugador.nombre})
                    try:
                        print(f"[SERVIDOR] {antes} ahora es {jugador.nombre}")
                    except Exception:
                        pass
                    continue

                # ---------- Inicio de apuestas ----------
                if tipo == "INICIAR":
                    if not mesa.en_ronda and not mesa.en_apuestas:
                        mesa.abrir_apuestas()
                    else:
                        Mesa._safe_send(
                            jugador.conn,
                            {"tipo": "ERROR", "mensaje": "Ya hay una ronda o apuestas abiertas."},
                        )
                    continue

                # ---------- Apostar / cancelar ----------
                if tipo == "APOSTAR":
                    try:
                        monto = int(msg.get("monto", 0))
                    except Exception:
                        monto = 0

                    if monto <= 0:
                        Mesa._safe_send(jugador.conn, {"tipo": "ERROR", "mensaje": "Monto inválido."})
                        continue

                    if jugador.saldo < monto:
                        Mesa._safe_send(
                            jugador.conn, {"tipo": "ERROR", "mensaje": "Fondos insuficientes."}
                        )
                        continue

                    jugador.saldo -= monto
                    jugador.apuestas = [monto]
                    jugador.manos = []  # se reparten cuando inicie la ronda
                    jugador.espera_apuesta = False

                    mesa.broadcast({"tipo": "APUESTA_OK", "nombre": jugador.nombre, "monto": monto})
                    try:
                        print(f"[APUESTA] {jugador.nombre} apostó ${monto}. Saldo={jugador.saldo}")
                    except Exception:
                        pass

                    mesa.evaluar_inicio_ronda()
                    continue

                if tipo == "CANCELAR_APUESTA":
                    jugador.apuestas = []
                    jugador.manos = []
                    jugador.espera_apuesta = False
                    mesa.broadcast({"tipo": "INFO", "mensaje": f"{jugador.nombre} canceló su apuesta."})
                    try:
                        print(f"[APUESTA] {jugador.nombre} canceló su apuesta.")
                    except Exception:
                        pass
                    mesa.broadcast_estado()
                    mesa.evaluar_inicio_ronda()
                    continue

                # ---------- Acciones en ronda ----------
                if tipo == "SUBIR":
                    if (
                        not mesa.en_ronda
                        or mesa.turno_idx is None
                        or mesa.jugadores[mesa.turno_idx] is not jugador
                    ):
                        Mesa._safe_send(jugador.conn, {"tipo": "ERROR", "mensaje": "No es tu turno."})
                    else:
                        j = mesa.jugadores[mesa.turno_idx]
                        mi = mesa.mano_turno_idx or 0

                        if mi < 0 or mi >= len(j.manos):
                            Mesa._safe_send(jugador.conn, {"tipo": "ERROR", "mensaje": "No hay mano activa."})
                        else:
                            mesa.barajar()
                            carta = mesa.mazo.pop()
                            j.manos[mi].append(carta)
                            total = valor_mano(j.manos[mi])

                            mesa.broadcast(
                                {
                                    "tipo": "INFO",
                                    "mensaje": f"{j.nombre} pidió: {carta[0]}{carta[1]} (total {total}).",
                                }
                            )
                            mesa.broadcast_estado()

                            if total > 21:
                                try:
                                    print(f"[TURNO] {j.nombre} se pasó ({total}).")
                                except Exception:
                                    pass
                                mesa.siguiente_turno()
                                if mesa.en_ronda:
                                    mesa.broadcast_estado()
                    continue

                if tipo == "QUEDARSE":
                    if (
                        not mesa.en_ronda
                        or mesa.turno_idx is None
                        or mesa.jugadores[mesa.turno_idx] is not jugador
                    ):
                        Mesa._safe_send(jugador.conn, {"tipo": "ERROR", "mensaje": "No es tu turno."})
                    else:
                        mesa.broadcast({"tipo": "INFO", "mensaje": f"{jugador.nombre} se planta."})
                        try:
                            print(f"[TURNO] {jugador.nombre} se planta.")
                        except Exception:
                            pass
                        mesa.siguiente_turno()
                        if mesa.en_ronda:
                            mesa.broadcast_estado()
                    continue

                if tipo == "DOBLAR":
                    if (
                        not mesa.en_ronda
                        or mesa.turno_idx is None
                        or mesa.jugadores[mesa.turno_idx] is not jugador
                    ):
                        Mesa._safe_send(jugador.conn, {"tipo": "ERROR", "mensaje": "No es tu turno."})
                    else:
                        j = mesa.jugadores[mesa.turno_idx]
                        mi = mesa.mano_turno_idx or 0

                        if mi < 0 or mi >= len(j.manos) or mi >= len(j.apuestas):
                            Mesa._safe_send(
                                jugador.conn, {"tipo": "ERROR", "mensaje": "No hay mano activa para doblar."}
                            )
                        else:
                            mano = j.manos[mi]
                            ap = j.apuestas[mi]

                            if len(mano) != 2:
                                Mesa._safe_send(
                                    jugador.conn,
                                    {"tipo": "ERROR", "mensaje": "Solo puedes doblar con 2 cartas."},
                                )
                            elif j.saldo < ap:
                                Mesa._safe_send(
                                    jugador.conn,
                                    {"tipo": "ERROR", "mensaje": "Fondos insuficientes para doblar."},
                                )
                            else:
                                j.saldo -= ap
                                j.apuestas[mi] += ap
                                mesa.barajar()
                                carta = mesa.mazo.pop()
                                mano.append(carta)
                                total = valor_mano(mano)

                                mesa.broadcast(
                                    {
                                        "tipo": "INFO",
                                        "mensaje": f"{j.nombre} dobló; carta {carta[0]}{carta[1]} (total {total}).",
                                    }
                                )
                                mesa.broadcast_estado()
                                mesa.siguiente_turno()
                                if mesa.en_ronda:
                                    mesa.broadcast_estado()
                    continue

                if tipo == "DIVIDIR":
                    if (
                        not mesa.en_ronda
                        or mesa.turno_idx is None
                        or mesa.jugadores[mesa.turno_idx] is not jugador
                    ):
                        Mesa._safe_send(jugador.conn, {"tipo": "ERROR", "mensaje": "No es tu turno."})
                    else:
                        j = mesa.jugadores[mesa.turno_idx]
                        mi = mesa.mano_turno_idx or 0

                        if mi < 0 or mi >= len(j.manos) or mi >= len(j.apuestas):
                            Mesa._safe_send(
                                jugador.conn, {"tipo": "ERROR", "mensaje": "No hay mano activa para dividir."}
                            )
                        else:
                            mano = j.manos[mi]
                            ap = j.apuestas[mi]

                            # Mismas figuras/rango y saldo >= ap
                            if len(mano) == 2 and mano[0][0] == mano[1][0] and j.saldo >= ap:
                                j.saldo -= ap
                                c1, c2 = mano[0], mano[1]
                                mesa.barajar()

                                j.manos[mi] = [c1, mesa.mazo.pop()]
                                j.manos.insert(mi + 1, [c2, mesa.mazo.pop()])
                                j.apuestas.insert(mi + 1, ap)

                                mesa.broadcast({"tipo": "INFO", "mensaje": f"{j.nombre} dividió su mano."})
                                mesa.broadcast_estado()
                            else:
                                Mesa._safe_send(
                                    jugador.conn, {"tipo": "ERROR", "mensaje": "No puedes dividir esta mano."}
                                )
                    continue

                if tipo == "SALIR":
                    break

    except Exception as e:
        Mesa._safe_send(conn, {"tipo": "ERROR", "mensaje": f"Conexión: {e}"})

    finally:
        # Limpieza y ajuste de turnos si el usuario se desconecta.
        with lock:
            if jugador in mesa.jugadores:
                idx = mesa.jugadores.index(jugador)
                mesa.jugadores.remove(jugador)
                mesa.broadcast({"tipo": "SALIO", "nombre": jugador.nombre})
                try:
                    print(f"[SERVIDOR] {jugador.nombre} se desconectó.")
                except Exception:
                    pass

                if mesa.en_ronda:
                    if mesa.turno_idx is not None:
                        if idx == mesa.turno_idx:
                            mesa.siguiente_turno()
                            if mesa.en_ronda:
                                mesa.broadcast_estado()
                        elif idx < mesa.turno_idx:
                            mesa.turno_idx -= 1
                elif mesa.en_apuestas:
                    # Si se va alguien que aún no respondía, re-evaluar.
                    mesa.evaluar_inicio_ronda()

                if mesa.jugadores:
                    mesa.broadcast_estado()

        try:
            conn.close()
        except Exception:
            pass


# -------------------- Arranque del servidor --------------------

def main() -> None:
    """Crea el socket de escucha y atiende conexiones entrantes con hilos."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen()
        print(f"Servidor Blackjack en {HOST}:{PORT}")

        while True:
            conn, addr = s.accept()
            threading.Thread(
                target=manejar_cliente, args=(conn, addr), daemon=True
            ).start()


if __name__ == "__main__":
    main()
