# servidor.py
import socket
import threading
import json
import random

HOST = "0.0.0.0"
PORT = 35001
MAX_JUGADORES = 4

VALORES = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":10,"Q":10,"K":10,"A":11}
PALOS = ["♠","♥","♦","♣"]
RANGOS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

def nuevo_mazo():
    mazo = [(r,p) for r in RANGOS for p in PALOS] * 6
    random.shuffle(mazo)
    return mazo

def valor_mano(mano):
    total, ases = 0, 0
    for r, _ in mano:
        total += VALORES[r]
        if r == "A": ases += 1
    while total > 21 and ases:
        total -= 10; ases -= 1
    return total

class Jugador:
    def __init__(self, conn, nombre):
        self.conn = conn
        self.nombre = nombre
        self.saldo = 500
        self.manos = []
        self.apuestas = []
        self.espera_apuesta = False

class Mesa:
    def __init__(self):
        self.jugadores = []
        self.banca = []
        self.mazo = nuevo_mazo()
        self.en_apuestas = False
        self.en_ronda = False
        self.turno_idx = None
        self.mano_turno_idx = None

    def barajar(self):
        if len(self.mazo) < 52:
            self.mazo = nuevo_mazo()

    def vista_banca(self):
        if self.en_ronda and len(self.banca) >= 2:
            return [self.banca[0], ("?","?")]
        return list(self.banca)

    def estado_json(self):
        return {
            "tipo": "ESTADO",
            "banca": self.vista_banca(),
            "jugadores": [{
                "nombre": j.nombre,
                "saldo": j.saldo,
                "manos": [{"cartas": m} for m in j.manos],
                "apuestas": j.apuestas
            } for j in self.jugadores],
            "turno": (self.jugadores[self.turno_idx].nombre
                      if (self.turno_idx is not None and 0 <= self.turno_idx < len(self.jugadores))
                      else None),
            "mano_turno_idx": self.mano_turno_idx
        }

    def broadcast(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        for j in list(self.jugadores):
            try: j.conn.sendall(data)
            except: pass

    def broadcast_estado(self):
        self.broadcast(self.estado_json())

    def _primer_idx_activo(self):
        for i, j in enumerate(self.jugadores):
            if j.apuestas and j.manos: return i
        return None

    def _siguiente_idx_activo(self, idx_actual):
        for i in range(idx_actual+1, len(self.jugadores)):
            j = self.jugadores[i]
            if j.apuestas and j.manos: return i
        return None

    def siguiente_turno(self):
        if not self.en_ronda or self.turno_idx is None: return

        j = self.jugadores[self.turno_idx] if 0 <= self.turno_idx < len(self.jugadores) else None
        if not j or not j.apuestas or not j.manos:
            nxt = self._siguiente_idx_activo(self.turno_idx)
            if nxt is None:
                self.turno_idx = None; self.mano_turno_idx = None
                self.jugar_banca()
            else:
                self.turno_idx = nxt; self.mano_turno_idx = 0
                self.broadcast_estado()
            return

        mi = self.mano_turno_idx or 0
        if mi + 1 < len(j.manos):
            self.mano_turno_idx = mi + 1
            self.broadcast_estado()
            return

        nxt = self._siguiente_idx_activo(self.turno_idx)
        if nxt is None:
            self.turno_idx = None; self.mano_turno_idx = None
            self.jugar_banca()
        else:
            self.turno_idx = nxt; self.mano_turno_idx = 0
            self.broadcast_estado()

    def jugar_banca(self):
        while valor_mano(self.banca) < 17:
            self.barajar(); self.banca.append(self.mazo.pop())
        total_banca = valor_mano(self.banca)

        resultados = []
        for j in self.jugadores:
            if not j.manos: continue
            while len(j.apuestas) < len(j.manos):
                j.apuestas.append(0)
            for idx, mano in enumerate(j.manos):
                ap = j.apuestas[idx]
                total = valor_mano(mano)
                if total > 21:
                    resultados.append((j.nombre, idx, "pierde", -ap))
                elif total_banca > 21 or total > total_banca:
                    j.saldo += ap * 2; resultados.append((j.nombre, idx, "gana", ap))
                elif total == total_banca:
                    j.saldo += ap; resultados.append((j.nombre, idx, "empata", 0))
                else:
                    resultados.append((j.nombre, idx, "pierde", -ap))

        self.broadcast({"tipo": "RESULTADOS",
                        "banca": self.banca,
                        "total_banca": total_banca,
                        "detalle": resultados})

        self.en_ronda = False
        self.en_apuestas = False
        for j in self.jugadores:
            j.manos = []; j.apuestas = []; j.espera_apuesta = False

    def abrir_apuestas(self):
        self.en_apuestas = True; self.en_ronda = False; self.banca = []
        for j in self.jugadores:
            j.manos = []; j.apuestas = []; j.espera_apuesta = True
        self.broadcast({"tipo": "APUESTAS_ABIERTAS"})
        for j in self.jugadores:
            try: j.conn.sendall((json.dumps({"tipo":"PREGUNTAR_APUESTA","nombre":j.nombre})+"\n").encode())
            except: pass

    def evaluar_inicio_ronda(self):
        if not self.en_apuestas: return
        conectados = [j for j in self.jugadores if j.conn]
        if not conectados: return
        todos_respondieron = all(not j.espera_apuesta for j in conectados)
        hay_apuestas = any(j.apuestas for j in conectados)
        if not todos_respondieron: return

        if not hay_apuestas:
            self.en_apuestas = False
            self.broadcast({"tipo":"INFO","mensaje":"Nadie apostó. Apuestas canceladas."})
            self.broadcast_estado()
            return

        self.en_apuestas = False; self.en_ronda = True
        self.banca = []; self.barajar()
        self.banca = [self.mazo.pop(), self.mazo.pop()]
        for j in self.jugadores:
            j.manos = [[self.mazo.pop(), self.mazo.pop()]] if j.apuestas else []
        self.turno_idx = self._primer_idx_activo()
        self.mano_turno_idx = 0 if self.turno_idx is not None else None
        self.broadcast({"tipo":"RONDA_INICIADA"})
        self.broadcast_estado()

mesa = Mesa()
lock = threading.Lock()

def manejar_cliente(conn, addr):
    global mesa
    with lock:
        if len(mesa.jugadores) >= MAX_JUGADORES:
            conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"Mesa llena (máx 4)."})+"\n").encode())
            conn.close(); return

    jugador = Jugador(conn, f"Jugador{addr[1]}")
    with lock:
        mesa.jugadores.append(jugador)
    mesa.broadcast({"tipo":"UNIDO","nombre":jugador.nombre})

    try:
        for line in conn.makefile("rb"):
            try: msg = json.loads(line.decode().strip())
            except: continue
            tipo = msg.get("tipo")

            with lock:
                if tipo == "CONFIGURAR_NOMBRE":
                    antes = jugador.nombre
                    jugador.nombre = msg.get("nombre", jugador.nombre)
                    mesa.broadcast({"tipo":"RENOMBRADO","antes":antes,"ahora":jugador.nombre})
                    continue

                if tipo == "INICIAR":
                    if not mesa.en_ronda and not mesa.en_apuestas:
                        mesa.abrir_apuestas()
                    else:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"Ya hay una ronda o apuestas abiertas."})+"\n").encode())
                    continue

                if tipo == "APOSTAR":
                    try: monto = int(msg.get("monto", 0))
                    except: monto = 0
                    if monto <= 0:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"Monto inválido."})+"\n").encode()); continue
                    if jugador.saldo < monto:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"Fondos insuficientes."})+"\n").encode()); continue
                    jugador.saldo -= monto
                    jugador.apuestas = [monto]
                    jugador.manos = []
                    jugador.espera_apuesta = False
                    mesa.broadcast({"tipo":"APUESTA_OK","nombre":jugador.nombre,"monto":monto})
                    mesa.evaluar_inicio_ronda()
                    continue

                if tipo == "CANCELAR_APUESTA":
                    jugador.apuestas = []
                    jugador.manos = []
                    jugador.espera_apuesta = False
                    mesa.broadcast({"tipo":"INFO","mensaje":f"{jugador.nombre} canceló su apuesta."})
                    mesa.broadcast_estado()
                    mesa.evaluar_inicio_ronda()
                    continue

                if tipo == "SUBIR":
                    if not mesa.en_ronda or mesa.turno_idx is None or mesa.jugadores[mesa.turno_idx] is not jugador:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No es tu turno."})+"\n").encode())
                    else:
                        j = mesa.jugadores[mesa.turno_idx]
                        mi = mesa.mano_turno_idx or 0
                        if mi < 0 or mi >= len(j.manos):
                            jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No hay mano activa."})+"\n").encode())
                        else:
                            mesa.barajar()
                            carta = mesa.mazo.pop()
                            j.manos[mi].append(carta)
                            total = valor_mano(j.manos[mi])
                            mesa.broadcast({"tipo":"INFO","mensaje": f"{j.nombre} pidió: {carta[0]}{carta[1]} (total {total})."})
                            mesa.broadcast_estado()
                            if total > 21: mesa.siguiente_turno()
                    continue

                if tipo == "QUEDARSE":
                    if not mesa.en_ronda or mesa.turno_idx is None or mesa.jugadores[mesa.turno_idx] is not jugador:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No es tu turno."})+"\n").encode())
                    else:
                        mesa.broadcast({"tipo":"INFO","mensaje": f"{jugador.nombre} se planta."})
                        mesa.siguiente_turno()
                    continue

                if tipo == "DOBLAR":
                    if not mesa.en_ronda or mesa.turno_idx is None or mesa.jugadores[mesa.turno_idx] is not jugador:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No es tu turno."})+"\n").encode())
                    else:
                        j = mesa.jugadores[mesa.turno_idx]
                        mi = mesa.mano_turno_idx or 0
                        if mi < 0 or mi >= len(j.manos) or mi >= len(j.apuestas):
                            jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No hay mano activa para doblar."})+"\n").encode())
                        else:
                            mano = j.manos[mi]; ap = j.apuestas[mi]
                            if len(mano) != 2:
                                jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"Solo puedes doblar con 2 cartas."})+"\n").encode())
                            elif j.saldo < ap:
                                jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"Fondos insuficientes para doblar."})+"\n").encode())
                            else:
                                j.saldo -= ap; j.apuestas[mi] += ap
                                mesa.barajar(); carta = mesa.mazo.pop()
                                mano.append(carta)
                                total = valor_mano(mano)
                                mesa.broadcast({"tipo":"INFO","mensaje": f"{j.nombre} dobló; carta {carta[0]}{carta[1]} (total {total})."})
                                mesa.broadcast_estado()
                                mesa.siguiente_turno()
                    continue

                if tipo == "DIVIDIR":
                    if not mesa.en_ronda or mesa.turno_idx is None or mesa.jugadores[mesa.turno_idx] is not jugador:
                        jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No es tu turno."})+"\n").encode())
                    else:
                        j = mesa.jugadores[mesa.turno_idx]
                        mi = mesa.mano_turno_idx or 0
                        if mi < 0 or mi >= len(j.manos) or mi >= len(j.apuestas):
                            jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No hay mano activa para dividir."})+"\n").encode())
                        else:
                            mano = j.manos[mi]; ap = j.apuestas[mi]
                            if len(mano) == 2 and mano[0][0] == mano[1][0] and j.saldo >= ap:
                                j.saldo -= ap
                                c1, c2 = mano[0], mano[1]
                                mesa.barajar()
                                j.manos[mi] = [c1, mesa.mazo.pop()]
                                j.manos.insert(mi+1, [c2, mesa.mazo.pop()])
                                j.apuestas.insert(mi+1, ap)
                                mesa.broadcast({"tipo":"INFO","mensaje": f"{j.nombre} dividió su mano."})
                                mesa.broadcast_estado()
                            else:
                                jugador.conn.sendall((json.dumps({"tipo":"ERROR","mensaje":"No puedes dividir esta mano."})+"\n").encode())
                    continue

                if tipo == "SALIR":
                    break

    except Exception as e:
        try: conn.sendall((json.dumps({"tipo":"ERROR","mensaje":f"Conexión: {e}"})+"\n").encode())
        except: pass
    finally:
        with lock:
            if jugador in mesa.jugadores:
                idx = mesa.jugadores.index(jugador)
                mesa.jugadores.remove(jugador)
                mesa.broadcast({"tipo":"SALIO","nombre":jugador.nombre})

                if mesa.en_ronda:
                    if mesa.turno_idx is not None:
                        if idx == mesa.turno_idx:
                            mesa.siguiente_turno()
                        elif idx < mesa.turno_idx:
                            mesa.turno_idx -= 1
                elif mesa.en_apuestas:
                    # si se va alguien que aún no respondía, re-evaluar
                    mesa.evaluar_inicio_ronda()

                if mesa.jugadores: mesa.broadcast_estado()

        try: conn.close()
        except: pass

def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen()
        print(f"Servidor Blackjack en {HOST}:{PORT}")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=manejar_cliente, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()
