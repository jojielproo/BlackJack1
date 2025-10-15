# Cliente.py
import socket, json, threading, queue, sys, os
import pygame
from pygame.locals import *
from PIL import Image
from copy import deepcopy

# -------- Rutas y config empaquetado --------
def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, *parts)

def cargar_config():
    default_ip = "172.20.10.6"
    default_port = 35001
    cfg = resource_path("config.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                c = json.load(f)
            return c.get("SERVIDOR", default_ip), c.get("PUERTO", default_port)
        except:
            pass
    return default_ip, default_port

SERVIDOR, PUERTO = cargar_config()

# -------- Ventana / estética --------
W, H = 1200, 720
BORDERLESS = False
FPS = 60

# Cartas
PALOS_MAP = {"♠":"S", "♥":"H", "♦":"D", "♣":"C"}

# Colores
COLOR_BG = (22, 26, 32)
COLOR_TEXT = (230, 230, 230)
COLOR_SUB = (200, 200, 200)
COLOR_BTN = (54, 60, 70)
COLOR_BTN_HOVER = (74, 80, 90)
COLOR_BTN_DISABLED = (45, 50, 58)
COLOR_PANEL = (30, 34, 40)

# Layout
MARGEN = 24
BANNER_W, BANNER_H = 420, 52
BTN_GAP = 10
BTN_W, BTN_H = 130, 40
BTN_Y = H - 64
AREA_JUGADORES_X = MARGEN
AREA_JUGADORES_Y = MARGEN + BANNER_H + 40
AREA_JUGADORES_W = int(W*0.58)
AREA_CRUPIER_Y = MARGEN + 2
AREA_CRUPIER_RIGHT = W - MARGEN
CARD_H_DEALER = 120
CARD_H_PLAYER_MIN = 95

def valor_mano_local(cartas):
    VALORES = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":10,"Q":10,"K":10,"A":11}
    total, ases = 0, 0
    for r,_ in cartas:
        total += VALORES[r]
        if r == "A": ases += 1
    while total > 21 and ases:
        total -= 10; ases -= 1
    return total

def carta_filename(rango, palo):
    return f"{rango}{PALOS_MAP.get(palo,'S')}.png"

def load_image(fname, height=130):
    full = resource_path("assets", "cartas", fname)
    if not os.path.exists(full):
        full = resource_path("assets", "cartas", "BACK.png")
    img = Image.open(full).convert("RGBA")
    w, h = img.size
    s = height / h
    img = img.resize((int(w*s), int(h*s)))
    return pygame.image.fromstring(img.tobytes(), img.size, img.mode)

class Button:
    def __init__(self, rect, text, onclick):
        self.rect = pygame.Rect(rect); self.text = text; self.onclick = onclick
        self.hover = False; self.enabled = True
    def set_enabled(self, on): self.enabled = bool(on)
    def draw(self, surf, font):
        col = COLOR_BTN_DISABLED if not self.enabled else (COLOR_BTN_HOVER if self.hover else COLOR_BTN)
        pygame.draw.rect(surf, col, self.rect, border_radius=12)
        label = font.render(self.text, True, (200,200,200) if self.enabled else (150,150,150))
        surf.blit(label, label.get_rect(center=self.rect.center))
    def handle(self, event):
        if event.type == MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos) and self.enabled
        elif event.type == MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos) and self.enabled: self.onclick()

class TextInput:
    def __init__(self, rect, placeholder=""):
        self.rect = pygame.Rect(rect); self.text = ""; self.placeholder = placeholder; self.active = False
    def draw(self, surf, font):
        pygame.draw.rect(surf, (255,255,255), self.rect, 2 if self.active else 1, border_radius=8)
        show = self.text if self.text or self.active else self.placeholder
        col = COLOR_TEXT if (self.text or self.active) else (180,180,180)
        t = font.render(show, True, col)
        surf.blit(t, (self.rect.x+8, self.rect.y+(self.rect.h-t.get_height())//2))
    def handle(self, event):
        if event.type == MOUSEBUTTONDOWN: self.active = self.rect.collidepoint(event.pos)
        if self.active and event.type == KEYDOWN:
            if event.key == K_RETURN:
                txt = self.text.strip(); self.text = ""; return ("enter", txt)
            elif event.key == K_BACKSPACE: self.text = self.text[:-1]
            else:
                if event.unicode and len(self.text) < 20: self.text += event.unicode
        return None

class NumeroInput(TextInput):
    def handle(self, event):
        if event.type == MOUSEBUTTONDOWN: self.active = self.rect.collidepoint(event.pos)
        if self.active and event.type == KEYDOWN:
            if event.key == K_RETURN:
                txt = self.text.strip(); self.text = ""; return ("enter", txt)
            elif event.key == K_BACKSPACE: self.text = self.text[:-1]
            else:
                if event.unicode.isdigit() and len(self.text) < 8: self.text += event.unicode
        return None

class cliente:
    def __init__(self):
        pygame.init()
        flags = pygame.NOFRAME if BORDERLESS else 0
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("Blackjack 😎")
        self.clock = pygame.time.Clock()
        self.font       = pygame.font.SysFont("arial", 20)
        self.font_bold  = pygame.font.SysFont("arial", 24, bold=True)
        self.font_title = pygame.font.SysFont("arial", 28, bold=True)
        self.font_small = pygame.font.SysFont("arial", 16)

        self.title_crupier_surf   = self.font_bold.render("Crupier:", True, COLOR_TEXT)
        self.title_jugadores_surf = self.font_title.render("Jugadores:", True, COLOR_TEXT)

        self.sock = None; self.rx_queue = queue.Queue()
        self.mi_nombre = None; self.conectado = False

        self.en_apuestas = False; self.en_ronda = False
        self.turno_nombre = None
        self.mano_turno_idx = None

        self.saldo_cache = 500

        self.mostrando_resultados = False
        self.detalle_resultados = []
        self.total_banca_final = None
        self.players_snapshot = None

        self.nombre_input = TextInput((MARGEN, MARGEN, 240, 36), "Tu nombre…")
        self.btn_conectar = Button((MARGEN+250, MARGEN, 120, 36), "Conectar", self.conectar)

        x0 = (W - (BTN_W*6 + BTN_GAP*5)) // 2
        self.btn_iniciar  = Button((x0+0*(BTN_W+BTN_GAP), BTN_Y, BTN_W, BTN_H), "Iniciar",  lambda: self.enviar_cmd("INICIAR"))
        self.btn_subir    = Button((x0+1*(BTN_W+BTN_GAP), BTN_Y, BTN_W, BTN_H), "Subir",    lambda: self.enviar_cmd("SUBIR"))
        self.btn_quedar   = Button((x0+2*(BTN_W+BTN_GAP), BTN_Y, BTN_W, BTN_H), "Quedarse", lambda: self.enviar_cmd("QUEDARSE"))
        self.btn_doblar   = Button((x0+3*(BTN_W+BTN_GAP), BTN_Y, BTN_W, BTN_H), "Doblar",   lambda: self.enviar_cmd("DOBLAR"))
        self.btn_dividir  = Button((x0+4*(BTN_W+BTN_GAP), BTN_Y, BTN_W, BTN_H), "Dividir",  lambda: self.enviar_cmd("DIVIDIR"))
        self.btn_salir    = Button((x0+5*(BTN_W+BTN_GAP), BTN_Y, BTN_W, BTN_H), "Salir",    self.cerrar)

        self.bet_input = NumeroInput((W-260, BTN_Y, 160, 36), "Apuesta…")
        self.bet_visible_for_me = False
        self.esperando_apuesta = False     # <- NUEVO
        self.bet_error = ""                # <- NUEVO (mensaje de error visible)

        self.log_msgs = []; self.dealer_cards = []; self.players = []; self.image_cache = {}

        self.show_welcome = True
        self.btn_continuar = Button((0, 0, 170, 44), "Continuar", self.cerrar_welcome)

        self.actualizar_botones()

    # Util
    def centrar_rect(self, w, h): return pygame.Rect((W - w)//2, (H - h)//2, w, h)
    def card_sprite(self, r, p, height=120):
        fname = "BACK.png" if (r=="?" or p=="?") else carta_filename(r,p)
        key = (fname, height)
        if key not in self.image_cache:
            self.image_cache[key] = load_image(fname, height=height)
        return self.image_cache[key]
    def mi_estado(self):
        for j in self.players:
            if j.get("nombre") == self.mi_nombre: return j
        return None

    # Red
    def conectar(self, nombre_override=None):
        if self.sock: return
        nombre = (nombre_override or (self.nombre_input.text or "").strip()) or "Jugador"
        self.mi_nombre = nombre
        try:
            self.sock = socket.create_connection((SERVIDOR, PUERTO))
        except Exception as e:
            self.log(f"ERROR: {e}"); return
        threading.Thread(target=self.receptor, daemon=True).start()
        self.send_json({"tipo":"CONFIGURAR_NOMBRE","nombre":self.mi_nombre})
        self.log(f"Bienvenido, {self.mi_nombre} 👋")
        self.btn_conectar.set_enabled(False)
        self.conectado = True
        self.actualizar_botones()

    def receptor(self):
        f = self.sock.makefile("rb")
        for line in f:
            try: msg = json.loads(line.decode().strip()); self.rx_queue.put(msg)
            except Exception as e:
                self.rx_queue.put({"tipo":"ERROR","mensaje":f"Decodificación: {e}"}); break

    def send_json(self, obj):
        if not self.sock: return
        try: self.sock.sendall((json.dumps(obj)+"\n").encode())
        except Exception as e: self.log(f"ERROR al enviar: {e}")

    def enviar_cmd(self, cmd): self.send_json({"tipo": cmd})

    # UI helper
    def log(self, text):
        self.log_msgs.append(text)
        if len(self.log_msgs) > 12: self.log_msgs = self.log_msgs[-12:]

    def puedo_doblar(self):
        if not (self.en_ronda and self.turno_nombre == self.mi_nombre): return False
        j = self.mi_estado()
        if not j: return False
        mi = self.mano_turno_idx if self.mano_turno_idx is not None else 0
        manos = j.get("manos", []); apuestas = j.get("apuestas", [])
        if mi < 0 or mi >= len(manos) or mi >= len(apuestas): return False
        mano = manos[mi].get("cartas", [])
        apuesta = apuestas[mi]; saldo = j.get("saldo", 0)
        return len(mano) == 2 and saldo >= apuesta

    def puedo_dividir(self):
        if not (self.en_ronda and self.turno_nombre == self.mi_nombre): return False
        j = self.mi_estado()
        if not j: return False
        manos = j.get("manos", []); apuestas = j.get("apuestas", []); saldo = j.get("saldo", 0)
        if len(manos) != 1 or len(apuestas) != 1: return False
        cartas = manos[0].get("cartas", [])
        if len(cartas) != 2: return False
        r1,_ = cartas[0]; r2,_ = cartas[1]
        return (r1 == r2) and (saldo >= apuestas[0])

    def actualizar_botones(self):
        self.btn_salir.set_enabled(True)
        self.btn_conectar.set_enabled(self.sock is None)
        self.btn_iniciar.set_enabled(self.sock is not None and (not self.en_ronda) and (not self.en_apuestas))
        soy_turno = (self.en_ronda and (self.turno_nombre == self.mi_nombre))
        self.btn_subir.set_enabled(soy_turno)
        self.btn_quedar.set_enabled(soy_turno)
        self.btn_doblar.set_enabled(self.puedo_doblar())
        self.btn_dividir.set_enabled(self.puedo_dividir())

    # Mensajes
    def handle_msg(self, msg):
        t = msg.get("tipo")
        if t == "UNIDO":
            self.log(f"<< {msg['nombre']} se unió a la mesa.")
        elif t == "RENOMBRADO":
            self.log(f"<< {msg['antes']} ahora es {msg['ahora']}.")
        elif t == "INFO":
            self.log(f"<< {msg['mensaje']}")
        elif t == "APUESTAS_ABIERTAS":
            self.log("<< Apuestas abiertas.")
            self.en_apuestas = True; self.en_ronda = False
            self.turno_nombre = None; self.mano_turno_idx = None
            self.mostrando_resultados = False
            self.actualizar_botones()
        elif t == "PREGUNTAR_APUESTA":
            if msg.get("nombre") == self.mi_nombre:
                self.bet_visible_for_me = True
                self.esperando_apuesta = False
                self.bet_error = ""  # reset error
                self.bet_input.rect = self.centrar_rect(220, 40); self.bet_input.rect.y += 30
                self.log("Escribe tu apuesta y presiona ENTER. (ESC cancela)")
            else:
                self.log(f"<< {msg['nombre']} está apostando…")
        elif t == "APUESTA_OK":
            self.log(f"<< {msg['nombre']} apostó ${msg['monto']}.")
            if msg.get("nombre") == self.mi_nombre:
                try: self.saldo_cache -= int(msg.get("monto", 0))
                except: pass
                self.esperando_apuesta = False
                self.bet_visible_for_me = False
                self.bet_error = ""
        elif t == "RONDA_INICIADA":
            self.log("<< ¡Ronda iniciada!")
            self.en_apuestas = False; self.en_ronda = True
            self.mostrando_resultados = False
            self.actualizar_botones()
        elif t == "ESTADO":
            if self.mostrando_resultados and not msg.get("turno"):
                for j in msg.get("jugadores", []):
                    if j.get("nombre") == self.mi_nombre and "saldo" in j:
                        self.saldo_cache = j["saldo"]
                return
            if msg.get("turno") is not None:
                self.mostrando_resultados = False
            self.dealer_cards = msg["banca"]
            self.players = msg["jugadores"]
            self.players_snapshot = deepcopy(self.players)
            self.turno_nombre = msg.get("turno")
            self.mano_turno_idx = msg.get("mano_turno_idx")
            me = self.mi_estado()
            if me and "saldo" in me: self.saldo_cache = me["saldo"]
            self.actualizar_botones()
        elif t == "RESULTADOS":
            self.dealer_cards = msg["banca"]
            self.total_banca_final = msg.get("total_banca")
            self.detalle_resultados = msg.get("detalle", [])
            self.mostrando_resultados = True

            if not self.players_snapshot:
                self.players_snapshot = deepcopy(self.players)
            me_snap = None
            for j in (self.players_snapshot or []):
                if j.get("nombre") == self.mi_nombre: me_snap = j; break
            if me_snap:
                abono = 0
                apuestas = me_snap.get("apuestas", [])
                for idx, _mano in enumerate(me_snap.get("manos", [])):
                    ap = apuestas[idx] if idx < len(apuestas) else 0
                    outcome = None
                    for (nom, i, outc, _delta) in self.detalle_resultados:
                        if nom == self.mi_nombre and i == idx: outcome = outc; break
                    if outcome == "gana":   abono += ap * 2
                    elif outcome == "empata": abono += ap
                self.saldo_cache += abono

            self.bet_visible_for_me = False
            self.en_ronda = False
            self.turno_nombre = None
            self.mano_turno_idx = None
            self.actualizar_botones()
            self.log("<< Fin de ronda. Pulsa INICIAR para abrir apuestas.")
        elif t == "SALIO":
            self.log(f"<< {msg['nombre']} salió.")
        elif t == "ERROR":
            self.log(f"<< ERROR: {msg['mensaje']}")
            if self.en_apuestas:
                # Mantén abierto el overlay y muestra error visible
                self.esperando_apuesta = False
                self.bet_error = str(msg.get("mensaje", "Error"))
                if not self.bet_visible_for_me and self.mi_nombre:
                    self.bet_visible_for_me = True
                self.log("Corrige la apuesta o presiona ESC para cancelar.")

    # Cierre
    def cerrar(self):
        try:
            if self.sock: self.sock.close()
        except: pass
        pygame.quit(); sys.exit()

    # Dibujo: Crupier
    def draw_dealer_area(self):
        y0 = AREA_CRUPIER_Y
        right_edge = AREA_CRUPIER_RIGHT

        title_surf = self.title_crupier_surf
        title_rect = title_surf.get_rect()
        title_rect.topright = (right_edge, y0)

        space = 12
        group_left = title_rect.left
        group_right = title_rect.right

        if self.mostrando_resultados and self.total_banca_final is not None:
            total_surf = self.font_title.render(str(self.total_banca_final), True, COLOR_TEXT)
            total_rect = total_surf.get_rect()
            total_rect.midleft = (title_rect.right + space, title_rect.centery)

            overflow = total_rect.right - right_edge
            if overflow > 0:
                title_rect.move_ip(-overflow, 0)
                total_rect.move_ip(-overflow, 0)

            self.screen.blit(total_surf, total_rect)
            group_left = title_rect.left
            group_right = total_rect.right

        self.screen.blit(title_surf, title_rect)

        header_center_x = (group_left + group_right) // 2
        cy = y0 + 30
        for (r, p) in self.dealer_cards:
            spr = self.card_sprite(r, p, height=CARD_H_DEALER)
            cx = header_center_x - spr.get_width() // 2
            self.screen.blit(spr, (cx, cy))
            cy += spr.get_height() + 12

    # Dibujo: Jugadores
    def draw_players_list(self):
        x0 = AREA_JUGADORES_X
        y0 = AREA_JUGADORES_Y

        self.screen.blit(self.title_jugadores_surf, (x0, y0 - 36))

        jugadores_dib = self.players_snapshot if (self.mostrando_resultados and self.players_snapshot) else self.players

        bottom_lim = BTN_Y - 14
        disponible = max(120, bottom_lim - y0)
        max_paneles = 4
        sep = 12
        panel_h = int((disponible - (max_paneles-1)*sep) / max_paneles)
        panel_h = max(140, min(panel_h, 190))

        header_h = 26
        card_h_local = max(CARD_H_PLAYER_MIN, panel_h - header_h - 34)

        py = y0
        panel_radius = 10
        panel_w = AREA_JUGADORES_W - 40

        for j in jugadores_dib[:4]:
            panel_rect = pygame.Rect(x0 - 8, py - 6, panel_w, panel_h)
            pygame.draw.rect(self.screen, COLOR_PANEL, panel_rect, border_radius=panel_radius)

            apuestas_str = ", ".join(map(str, j.get("apuestas", []))) or "0"
            header = f"{j['nombre']} | Apuesta(s): {apuestas_str}"
            hdr_surface = self.font.render(header, True, COLOR_TEXT)
            self.screen.blit(hdr_surface, (x0 + 10, py))

            px = x0 + 10
            py_cards = py + header_h
            spr_h_local = 0

            for mano_idx, mano in enumerate(j.get("manos", [])):
                start_x = px
                for (r, p) in mano.get("cartas", []):
                    spr = self.card_sprite(r, p, height=card_h_local)
                    self.screen.blit(spr, (px, py_cards))
                    spr_h_local = spr.get_height()
                    px += spr.get_width() + 10

                if self.mostrando_resultados and mano.get("cartas"):
                    total = valor_mano_local(mano["cartas"])
                    outcome = None
                    for (nom, idx, outc, _delta) in self.detalle_resultados:
                        if nom == j['nombre'] and idx == mano_idx: outcome = outc; break
                    mapa = {"gana":"Ganaste","empata":"Empate","pierde":"Perdiste"}
                    s = f"{total}  {mapa.get(outcome,'')}".strip()
                    txt = self.font.render(s, True, COLOR_SUB)
                    txt_rect = txt.get_rect()
                    center_y = py_cards + (spr_h_local or card_h_local) // 2
                    txt_rect.midleft = (start_x + (px - start_x) + 14, center_y)
                    self.screen.blit(txt, txt_rect)

                px += 18

            py += panel_h + sep

    # Modal de reglas
    def cerrar_welcome(self): self.show_welcome = False
    def draw_wrapped_text(self, surf, text, font, color, rect, line_gap=4):
        words = text.split(); lines = []; cur = ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.size(t)[0] <= rect.width: cur = t
            else: lines.append(cur); cur = w
        if cur: lines.append(cur)
        y = rect.top
        for ln in lines:
            img = font.render(ln, True, color)
            surf.blit(img, (rect.left, y))
            y += img.get_height() + line_gap
    def draw_welcome_modal(self):
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0,0,0,160))
        self.screen.blit(overlay, (0,0))
        panel = self.centrar_rect(680, 420)
        pygame.draw.rect(self.screen, (40,45,54), panel, border_radius=16)
        title = self.font_title.render("¡Bienvenido al Blackjack!", True, (240,240,240))
        self.screen.blit(title, title.get_rect(center=(panel.centerx, panel.top+50)))
        reglas = (
            "Objetivo: acercarte a 21 sin pasarte. Figuras = 10; As = 11 ó 1.\n\n"
            "Tu turno: SUBIR (pedir), QUEDARSE (plantarse), "
            "DOBLAR (solo con 2 cartas), DIVIDIR (si tienes par).\n\n"
            "Crupier: roba hasta 17 (se planta en 17+).\n"
            "Pagos: victoria 1:1, empate devuelve la apuesta."
        )
        text_rect = pygame.Rect(panel.left+30, panel.top+100, panel.width-60, panel.height-170)
        self.draw_wrapped_text(self.screen, reglas, self.font, (220,220,220), text_rect, line_gap=6)
        self.btn_continuar.rect = pygame.Rect(0,0,170,44)
        self.btn_continuar.rect.centerx = panel.centerx
        self.btn_continuar.rect.bottom  = panel.bottom - 24
        self.btn_continuar.draw(self.screen, self.font)

    # Bucle principal
    def run(self):
        while True:
            for event in pygame.event.get():
                if event.type == QUIT: self.cerrar()

                if event.type == KEYDOWN and event.key == K_F1:
                    self.show_welcome = True
                if self.show_welcome:
                    if event.type == KEYDOWN and event.key in (K_RETURN, K_SPACE):
                        self.cerrar_welcome()
                    self.btn_continuar.handle(event)
                    continue

                if event.type == KEYDOWN and event.key == K_ESCAPE:
                    if self.bet_visible_for_me:
                        self.send_json({"tipo":"CANCELAR_APUESTA"})
                        self.bet_visible_for_me = False
                        self.esperando_apuesta = False
                        self.bet_error = ""
                    else:
                        self.cerrar()

                self.btn_conectar.handle(event)
                self.btn_iniciar.handle(event)
                self.btn_subir.handle(event)
                self.btn_quedar.handle(event)
                self.btn_doblar.handle(event)
                self.btn_dividir.handle(event)
                self.btn_salir.handle(event)

                res = self.nombre_input.handle(event)
                if res and res[0] == "enter": self.conectar(res[1])

                # --- Overlay de apuesta con validación local y espera de confirmación ---
                if self.bet_visible_for_me:
                    bet_res = self.bet_input.handle(event)
                    if bet_res and bet_res[0] == "enter" and not self.esperando_apuesta:
                        txt = bet_res[1].strip()
                        if txt.isdigit() and int(txt) > 0:
                            est = self.mi_estado()
                            saldo_actual = est.get("saldo", self.saldo_cache) if est else self.saldo_cache
                            if int(txt) > saldo_actual:
                                self.bet_error = "Fondos insuficientes."
                                self.log("Fondos insuficientes. Ingresa otra cantidad o ESC para cancelar.")
                            else:
                                self.esperando_apuesta = True
                                self.bet_error = ""
                                self.send_json({"tipo":"APOSTAR","monto":int(txt)})
                                self.log("Apuesta enviada. Esperando confirmación...")
                        else:
                            self.bet_error = "Monto inválido."
                            self.log("Monto inválido.")

            try:
                while True: self.handle_msg(self.rx_queue.get_nowait())
            except queue.Empty: pass

            self.screen.fill(COLOR_BG)

            if not self.conectado:
                self.btn_conectar.draw(self.screen, self.font)
                self.nombre_input.draw(self.screen, self.font)
            else:
                box_rect = pygame.Rect(MARGEN, MARGEN, BANNER_W, BANNER_H)
                pygame.draw.rect(self.screen, (0,0,0), box_rect, border_radius=10)
                name_surface = self.font_title.render(f"Jugador: {self.mi_nombre}", True, (255,255,255))
                self.screen.blit(name_surface, name_surface.get_rect(center=box_rect.center))

            self.draw_dealer_area()
            self.draw_players_list()

            for btn in (self.btn_iniciar, self.btn_subir, self.btn_quedar, self.btn_doblar, self.btn_dividir, self.btn_salir):
                btn.draw(self.screen, self.font)

            if self.bet_visible_for_me:
                overlay = pygame.Surface((W, H), pygame.SRCALPHA); overlay.fill((0,0,0,140))
                self.screen.blit(overlay, (0,0))
                panel_rect = self.centrar_rect(460, 210)
                pygame.draw.rect(self.screen, (40,45,54), panel_rect, border_radius=14)
                titulo = self.font.render("Tu apuesta", True, (240,240,240))
                subt   = self.font_small.render("(ENTER para confirmar, ESC para cancelar)", True, (180,180,180))
                self.screen.blit(titulo, titulo.get_rect(center=(panel_rect.centerx, panel_rect.top+40)))
                self.screen.blit(subt,   subt.get_rect(center=(panel_rect.centerx, panel_rect.top+70)))
                self.bet_input.rect.centerx = panel_rect.centerx
                self.bet_input.rect.y = panel_rect.top+100
                self.bet_input.draw(self.screen, self.font)

                # Mensaje de error (rojo) debajo del input
                if self.bet_error:
                    err = self.font_small.render(self.bet_error, True, (220, 80, 80))
                    err_rect = err.get_rect(center=(panel_rect.centerx, self.bet_input.rect.bottom + 18))
                    self.screen.blit(err, err_rect)

            if self.conectado:
                if not (self.mostrando_resultados or self.en_apuestas):
                    est = self.mi_estado()
                    if est and "saldo" in est: self.saldo_cache = est["saldo"]
                saldo_txt = self.font.render(f"${self.saldo_cache}", True, (255,255,255))
                pad_x, pad_y = 10, 6
                box_h = saldo_txt.get_height() + pad_y*2
                box_y = BTN_Y + (BTN_H - box_h) // 2
                box_rect = pygame.Rect(MARGEN, box_y, saldo_txt.get_width()+pad_x*2, box_h)
                pygame.draw.rect(self.screen, (0,0,0), box_rect, border_radius=8)
                self.screen.blit(saldo_txt, (box_rect.x+pad_x, box_rect.y+pad_y))

            estado = []
            estado.append("Conectado" if self.sock else "Desconectado")
            if self.en_apuestas: estado.append("En apuestas")
            elif self.en_ronda:  estado.append("En ronda")
            else:                estado.append("Esperando inicio")
            if self.turno_nombre: estado.append(f"Turno: {self.turno_nombre}")
            banner = self.font_small.render(" | ".join(estado), True, (200,200,200))
            self.screen.blit(banner, (W - banner.get_width() - MARGEN, H - 26))

            if self.show_welcome:
                self.draw_welcome_modal()

            pygame.display.flip(); self.clock.tick(FPS)

if __name__ == "__main__":
    cliente().run()
