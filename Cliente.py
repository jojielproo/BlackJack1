# -*- coding: utf-8 -*-
"""Cliente de Blackjack con interfaz Pygame.

Este módulo implementa el cliente gráfico de un juego de Blackjack
conectado por sockets a un servidor. La interfaz está construida con
Pygame e incluye:

- Pantalla de bienvenida con reglas.
- Conexión al servidor (host/puerto desde `config.json`).
- Flujo de apuestas con validación local y mensajes de error.
- Render del crupier y hasta 4 jugadores con resultados.
- Controles: Iniciar, Subir, Quedarse, Doblar, Dividir y Salir.

Notas:
    - El nombre de la clase principal se mantiene como `cliente` por
      requerimiento explícito, aunque PEP 8 sugiera CapWords.
    - Este archivo asume que los assets de cartas están en
      `assets/cartas` (o empaquetados con PyInstaller dentro de `internal`).

Autor: Jorge Andres Garcia Dominguez/ Hugo Ivan Romero Duarte/ Luis Angel Antonio Franco
Fecha: 2025-10-15
"""
from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
from copy import deepcopy
from typing import Dict, List, Optional, Sequence, Tuple

import pygame
from PIL import Image
from pygame.locals import (
    K_BACKSPACE,
    K_ESCAPE,
    K_F1,
    K_RETURN,
    K_SPACE,
    KEYDOWN,
    MOUSEBUTTONDOWN,
    MOUSEMOTION,
    QUIT,
)

# ---------------------------------------------------------------------------
# Configuración de rutas y lectura de parámetros
# ---------------------------------------------------------------------------


def resource_path(*parts: str) -> str:
    """Resuelve rutas tanto en entorno normal como empaquetado (PyInstaller).

    Si se ejecuta como ejecutable creado con PyInstaller, usa la carpeta
    temporal `_MEIPASS`. En caso contrario, usa la ruta del archivo actual.

    Args:
        *parts: Partes de la ruta a unir.

    Returns:
        Ruta absoluta resultante.
    """
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, *parts)


def cargar_config() -> Tuple[str, int]:
    """Carga host/puerto desde `config.json` si existe.

    El archivo debe estar junto al ejecutable/script. Si no existe o falla
    la carga, se usan valores por defecto.

    Returns:
        Tuple (SERVIDOR, PUERTO).
    """
    default_ip = "172.20.10.6"
    default_port = 35001
    cfg_path = resource_path("config.json")

    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("SERVIDOR", default_ip), int(cfg.get("PUERTO", default_port))
        except Exception:
            # Si el archivo está corrupto o malformado, cae a defaults
            pass
    return default_ip, default_port


SERVIDOR, PUERTO = cargar_config()

# ---------------------------------------------------------------------------
# Constantes de UI y visual
# ---------------------------------------------------------------------------

# Ventana / estética
W, H = 1200, 720
BORDERLESS = False
FPS = 60

# Cartas: mapeo de palo unicode -> sufijo de archivo
PALOS_MAP: Dict[str, str] = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}

# Colores
COLOR_BG = (22, 26, 32)
COLOR_TEXT = (230, 230, 230)
COLOR_SUB = (200, 200, 200)
COLOR_BTN = (54, 60, 70)
COLOR_BTN_HOVER = (74, 80, 90)
COLOR_BTN_DISABLED = (45, 50, 58)
COLOR_PANEL = (30, 34, 40)

# Layout general
MARGEN = 24
BANNER_W, BANNER_H = 420, 52

# Botones
BTN_GAP = 10
BTN_W, BTN_H = 130, 40
BTN_Y = H - 64

# Zona de jugadores
AREA_JUGADORES_X = MARGEN
AREA_JUGADORES_Y = MARGEN + BANNER_H + 40
AREA_JUGADORES_W = int(W * 0.58)

# Zona del crupier (anclada a la derecha)
AREA_CRUPIER_Y = MARGEN + 2
AREA_CRUPIER_RIGHT = W - MARGEN

# Alturas de carta
CARD_H_DEALER = 120
CARD_H_PLAYER_MIN = 95

# ---------------------------------------------------------------------------
# Utilidades de cartas (lado cliente)
# ---------------------------------------------------------------------------


def valor_mano_local(cartas: Sequence[Tuple[str, str]]) -> int:
    """Calcula el total de una mano localmente aplicando reglas del As.

    El As cuenta como 11, pero si el total supera 21 se reduce a 1
    tantas veces como sea necesario.

    Args:
        cartas: Secuencia de tuplas (rango, palo), p. ej. ("K", "♠").

    Returns:
        Suma entera de la mano.
    """
    valores = {
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "8": 8,
        "9": 9,
        "10": 10,
        "J": 10,
        "Q": 10,
        "K": 10,
        "A": 11,
    }
    total = 0
    ases = 0
    for r, _ in cartas:
        total += valores[r]
        if r == "A":
            ases += 1
    while total > 21 and ases:
        total -= 10
        ases -= 1
    return total


def carta_filename(rango: str, palo: str) -> str:
    """Devuelve el nombre de archivo para la imagen de una carta.

    Args:
        rango: Rango de la carta, p. ej. "10", "J", "A".
        palo: Palo unicode, p. ej. "♠", "♥", "♦", "♣".

    Returns:
        Nombre de archivo PNG correspondiente (sin ruta).
    """
    return f"{rango}{PALOS_MAP.get(palo, 'S')}.png"


def load_image(fname: str, height: int = 130) -> pygame.Surface:
    """Carga una imagen de carta, reescalada a una altura específica.

    Si el archivo no existe, se usa la carta posterior `BACK.png`.

    Args:
        fname: Nombre de archivo dentro de `assets/cartas`.
        height: Altura deseada en píxeles.

    Returns:
        Superficie de Pygame con la imagen cargada.
    """
    full = resource_path("assets", "cartas", fname)
    if not os.path.exists(full):
        full = resource_path("assets", "cartas", "BACK.png")
    img = Image.open(full).convert("RGBA")
    w, h = img.size
    scale = height / h
    img = img.resize((int(w * scale), int(h * scale)))
    return pygame.image.fromstring(img.tobytes(), img.size, img.mode)


# ---------------------------------------------------------------------------
# Controles de UI
# ---------------------------------------------------------------------------


class Button:
    """Botón rectangular con hover, habilitado/inhabilitado y callback."""

    def __init__(self, rect: Tuple[int, int, int, int], text: str, onclick) -> None:
        """Inicializa un botón.

        Args:
            rect: (x, y, w, h) del botón.
            text: Etiqueta a mostrar.
            onclick: Función a ejecutar al hacer clic (sin argumentos).
        """
        self.rect = pygame.Rect(rect)
        self.text = text
        self.onclick = onclick
        self.hover = False
        self.enabled = True

    def set_enabled(self, on: bool) -> None:
        """Activa/desactiva el botón visual y funcionalmente."""
        self.enabled = bool(on)

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        """Dibuja el botón en pantalla.

        Args:
            surf: Superficie de destino.
            font: Fuente para render del texto.
        """
        color = (
            COLOR_BTN_DISABLED
            if not self.enabled
            else (COLOR_BTN_HOVER if self.hover else COLOR_BTN)
        )
        pygame.draw.rect(surf, color, self.rect, border_radius=12)
        label = font.render(
            self.text, True, (200, 200, 200) if self.enabled else (150, 150, 150)
        )
        surf.blit(label, label.get_rect(center=self.rect.center))

    def handle(self, event: pygame.event.Event) -> None:
        """Maneja eventos de ratón sobre el botón.

        Args:
            event: Evento de pygame.
        """
        if event.type == MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos) and self.enabled
        elif event.type == MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos) and self.enabled:
                self.onclick()


class TextInput:
    """Campo de texto simple con placeholder y envío con ENTER."""

    def __init__(self, rect: Tuple[int, int, int, int], placeholder: str = "") -> None:
        """Inicializa el campo de texto.

        Args:
            rect: (x, y, w, h) del control.
            placeholder: Texto guía cuando está vacío e inactivo.
        """
        self.rect = pygame.Rect(rect)
        self.text = ""
        self.placeholder = placeholder
        self.active = False

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        """Dibuja el campo de texto con borde y placeholder.

        Args:
            surf: Superficie de destino.
            font: Fuente para render del texto.
        """
        pygame.draw.rect(
            surf, (255, 255, 255), self.rect, 2 if self.active else 1, border_radius=8
        )
        show = self.text if (self.text or self.active) else self.placeholder
        color = COLOR_TEXT if (self.text or self.active) else (180, 180, 180)
        label = font.render(show, True, color)
        surf.blit(
            label, (self.rect.x + 8, self.rect.y + (self.rect.h - label.get_height()) // 2)
        )

    def handle(self, event: pygame.event.Event) -> Optional[Tuple[str, str]]:
        """Maneja foco y entrada de texto.

        ENTER devuelve un evento ('enter', texto) y limpia el contenido.

        Args:
            event: Evento de pygame.

        Returns:
            Tupla ('enter', texto) si se presionó ENTER; de lo contrario None.
        """
        if event.type == MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
        if self.active and event.type == KEYDOWN:
            if event.key == K_RETURN:
                txt = self.text.strip()
                self.text = ""
                return ("enter", txt)
            elif event.key == K_BACKSPACE:
                self.text = self.text[:-1]
            else:
                if event.unicode and len(self.text) < 20:
                    self.text += event.unicode
        return None


class NumeroInput(TextInput):
    """Campo de texto que solo acepta números enteros positivos."""

    def handle(self, event: pygame.event.Event) -> Optional[Tuple[str, str]]:
        """Igual que TextInput, pero restringido a dígitos.

        Args:
            event: Evento de pygame.

        Returns:
            Tupla ('enter', texto) si se presionó ENTER; de lo contrario None.
        """
        if event.type == MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
        if self.active and event.type == KEYDOWN:
            if event.key == K_RETURN:
                txt = self.text.strip()
                self.text = ""
                return ("enter", txt)
            elif event.key == K_BACKSPACE:
                self.text = self.text[:-1]
            else:
                if event.unicode.isdigit() and len(self.text) < 8:
                    self.text += event.unicode
        return None


# ---------------------------------------------------------------------------
# Cliente Pygame (nombre de clase conservado como 'cliente' por tu petición)
# ---------------------------------------------------------------------------


class cliente:
    """Cliente gráfico de Blackjack con Pygame.

    Gestiona:
        - Conexión a servidor vía TCP.
        - Recepción y envío de mensajes JSON terminados en newline.
        - Estados de mesa: apuestas abiertas, ronda, resultados.
        - Render del crupier y hasta 4 paneles de jugadores.
        - Flujo de apuesta local con overlay y validación básica.

    Atributos principales:
        sock: Socket conectado al servidor o None.
        rx_queue: Cola de mensajes JSON entrantes (hilo receptor).
        mi_nombre: Nombre local del jugador.
        conectado: Si ya se estableció conexión.
        en_apuestas: Flag de fase de apuestas.
        en_ronda: Flag de ronda activa.
        turno_nombre: Nombre del jugador que tiene el turno.
        mano_turno_idx: Índice de mano activa cuando hay split.
        saldo_cache: Copia visual del saldo (refresca con ESTADO/RESULTADOS).
        mostrando_resultados: True si se está mostrando el cierre de ronda.
        detalle_resultados: Lista de (nombre, idx_mano, resultado, delta).
        total_banca_final: Total de banca al cierre (para mostrar).
        players: Estado reciente de jugadores (del último ESTADO).
        players_snapshot: Copia congelada del estado al inicio de RESULTADOS.
        bet_visible_for_me: Overlay de apuesta visible para este cliente.
        esperando_apuesta: Bloquea ENTER mientras esperamos respuesta del server.
        bet_error: Texto de error visible en overlay de apuestas.
    """

    def __init__(self) -> None:
        """Inicializa recursos, fuentes, botones y estado."""
        pygame.init()
        flags = pygame.NOFRAME if BORDERLESS else 0
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("Blackjack 😎")
        self.clock = pygame.time.Clock()

        # Fuentes
        self.font = pygame.font.SysFont("arial", 20)
        self.font_bold = pygame.font.SysFont("arial", 24, bold=True)
        self.font_title = pygame.font.SysFont("arial", 28, bold=True)
        self.font_small = pygame.font.SysFont("arial", 16)

        # Títulos renderizados (static)
        self.title_crupier_surf = self.font_bold.render("Crupier:", True, COLOR_TEXT)
        self.title_jugadores_surf = self.font_title.render("Jugadores:", True, COLOR_TEXT)

        # Red
        self.sock: Optional[socket.socket] = None
        self.rx_queue: "queue.Queue[Dict]" = queue.Queue()
        self.mi_nombre: Optional[str] = None
        self.conectado: bool = False

        # Juego
        self.en_apuestas: bool = False
        self.en_ronda: bool = False
        self.turno_nombre: Optional[str] = None
        self.mano_turno_idx: Optional[int] = None

        # Saldo
        self.saldo_cache: int = 500

        # Resultados
        self.mostrando_resultados: bool = False
        self.detalle_resultados: List[Tuple[str, int, str, int]] = []
        self.total_banca_final: Optional[int] = None
        self.players_snapshot: Optional[List[Dict]] = None

        # UI dinámica
        self.nombre_input = TextInput((MARGEN, MARGEN, 240, 36), "Tu nombre…")
        self.btn_conectar = Button((MARGEN + 250, MARGEN, 120, 36), "Conectar", self.conectar)

        x0 = (W - (BTN_W * 6 + BTN_GAP * 5)) // 2
        self.btn_iniciar = Button(
            (x0 + 0 * (BTN_W + BTN_GAP), BTN_Y, BTN_W, BTN_H),
            "Iniciar",
            lambda: self.enviar_cmd("INICIAR"),
        )
        self.btn_subir = Button(
            (x0 + 1 * (BTN_W + BTN_GAP), BTN_Y, BTN_W, BTN_H),
            "Subir",
            lambda: self.enviar_cmd("SUBIR"),
        )
        self.btn_quedar = Button(
            (x0 + 2 * (BTN_W + BTN_GAP), BTN_Y, BTN_W, BTN_H),
            "Quedarse",
            lambda: self.enviar_cmd("QUEDARSE"),
        )
        self.btn_doblar = Button(
            (x0 + 3 * (BTN_W + BTN_GAP), BTN_Y, BTN_W, BTN_H),
            "Doblar",
            lambda: self.enviar_cmd("DOBLAR"),
        )
        self.btn_dividir = Button(
            (x0 + 4 * (BTN_W + BTN_GAP), BTN_Y, BTN_W, BTN_H),
            "Dividir",
            lambda: self.enviar_cmd("DIVIDIR"),
        )
        self.btn_salir = Button(
            (x0 + 5 * (BTN_W + BTN_GAP), BTN_Y, BTN_W, BTN_H),
            "Salir",
            self.cerrar,
        )

        # Apuestas
        self.bet_input = NumeroInput((W - 260, BTN_Y, 160, 36), "Apuesta…")
        self.bet_visible_for_me: bool = False
        self.esperando_apuesta: bool = False
        self.bet_error: str = ""

        # Buffers visuales
        self.log_msgs: List[str] = []
        self.dealer_cards: List[Tuple[str, str]] = []
        self.players: List[Dict] = []
        self.image_cache: Dict[Tuple[str, int], pygame.Surface] = {}

        # Modal de bienvenida
        self.show_welcome: bool = True
        self.btn_continuar = Button((0, 0, 170, 44), "Continuar", self.cerrar_welcome)

        # Estado inicial de botones
        self.actualizar_botones()

    # --------------------- Utilidades de instancia ---------------------

    def centrar_rect(self, w: int, h: int) -> pygame.Rect:
        """Crea un rectángulo centrado en la ventana."""
        return pygame.Rect((W - w) // 2, (H - h) // 2, w, h)

    def card_sprite(self, rango: str, palo: str, height: int = 120) -> pygame.Surface:
        """Recupera (y cachea) la superficie de una carta a cierta altura."""
        fname = "BACK.png" if (rango == "?" or palo == "?") else carta_filename(rango, palo)
        key = (fname, height)
        if key not in self.image_cache:
            self.image_cache[key] = load_image(fname, height=height)
        return self.image_cache[key]

    def mi_estado(self) -> Optional[Dict]:
        """Devuelve el dict del jugador local dentro de `self.players`."""
        for j in self.players:
            if j.get("nombre") == self.mi_nombre:
                return j
        return None

    # --------------------------- Red / Sockets --------------------------

    def conectar(self, nombre_override: Optional[str] = None) -> None:
        """Conecta con el servidor y envía el nombre del jugador.

        Args:
            nombre_override: Nombre a usar en vez del del input (opcional).
        """
        if self.sock:
            return
        nombre = (nombre_override or (self.nombre_input.text or "").strip()) or "Jugador"
        self.mi_nombre = nombre
        try:
            self.sock = socket.create_connection((SERVIDOR, PUERTO))
        except Exception as e:
            self.log(f"ERROR: {e}")
            return

        threading.Thread(target=self.receptor, daemon=True).start()
        self.send_json({"tipo": "CONFIGURAR_NOMBRE", "nombre": self.mi_nombre})
        self.log(f"Bienvenido, {self.mi_nombre} 👋")
        self.btn_conectar.set_enabled(False)
        self.conectado = True
        self.actualizar_botones()

    def receptor(self) -> None:
        """Hilo receptor: lee líneas JSON y las mete a la cola."""
        assert self.sock is not None
        f = self.sock.makefile("rb")
        for line in f:
            try:
                msg = json.loads(line.decode().strip())
                self.rx_queue.put(msg)
            except Exception as e:
                self.rx_queue.put({"tipo": "ERROR", "mensaje": f"Decodificación: {e}"})
                break

    def send_json(self, obj: Dict) -> None:
        """Envía un objeto JSON (con newline final) al servidor."""
        if not self.sock:
            return
        try:
            self.sock.sendall((json.dumps(obj) + "\n").encode())
        except Exception as e:
            self.log(f"ERROR al enviar: {e}")

    def enviar_cmd(self, cmd: str) -> None:
        """Atajo para enviar un comando simple con campo 'tipo'."""
        self.send_json({"tipo": cmd})

    # ----------------------------- UI helpers --------------------------

    def log(self, text: str) -> None:
        """Agrega una línea al log inferior derecho (cola circular)."""
        self.log_msgs.append(text)
        if len(self.log_msgs) > 12:
            self.log_msgs = self.log_msgs[-12:]

    def puedo_doblar(self) -> bool:
        """Determina si el jugador local puede doblar en este instante."""
        if not (self.en_ronda and self.turno_nombre == self.mi_nombre):
            return False
        j = self.mi_estado()
        if not j:
            return False
        mi = self.mano_turno_idx if self.mano_turno_idx is not None else 0
        manos = j.get("manos", [])
        apuestas = j.get("apuestas", [])
        if mi < 0 or mi >= len(manos) or mi >= len(apuestas):
            return False
        mano = manos[mi].get("cartas", [])
        apuesta = apuestas[mi]
        saldo = j.get("saldo", 0)
        return len(mano) == 2 and saldo >= apuesta

    def puedo_dividir(self) -> bool:
        """Determina si el jugador local puede dividir su mano."""
        if not (self.en_ronda and self.turno_nombre == self.mi_nombre):
            return False
        j = self.mi_estado()
        if not j:
            return False
        manos = j.get("manos", [])
        apuestas = j.get("apuestas", [])
        saldo = j.get("saldo", 0)
        if len(manos) != 1 or len(apuestas) != 1:
            return False
        cartas = manos[0].get("cartas", [])
        if len(cartas) != 2:
            return False
        r1, _ = cartas[0]
        r2, _ = cartas[1]
        return (r1 == r2) and (saldo >= apuestas[0])

    def actualizar_botones(self) -> None:
        """Actualiza el estado de habilitación de los botones principales."""
        self.btn_salir.set_enabled(True)
        self.btn_conectar.set_enabled(self.sock is None)
        self.btn_iniciar.set_enabled(self.sock is not None and (not self.en_ronda) and (not self.en_apuestas))
        soy_turno = self.en_ronda and (self.turno_nombre == self.mi_nombre)
        self.btn_subir.set_enabled(soy_turno)
        self.btn_quedar.set_enabled(soy_turno)
        self.btn_doblar.set_enabled(self.puedo_doblar())
        self.btn_dividir.set_enabled(self.puedo_dividir())

    # ------------------------ Manejo de mensajes ------------------------

    def handle_msg(self, msg: Dict) -> None:
        """Procesa un mensaje JSON recibido del servidor.

        Args:
            msg: Diccionario del mensaje.
        """
        t = msg.get("tipo")

        if t == "UNIDO":
            self.log(f"<< {msg['nombre']} se unió a la mesa.")

        elif t == "RENOMBRADO":
            self.log(f"<< {msg['antes']} ahora es {msg['ahora']}.")

        elif t == "INFO":
            self.log(f"<< {msg['mensaje']}")

        elif t == "APUESTAS_ABIERTAS":
            self.log("<< Apuestas abiertas.")
            self.en_apuestas = True
            self.en_ronda = False
            self.turno_nombre = None
            self.mano_turno_idx = None
            self.mostrando_resultados = False
            self.actualizar_botones()

        elif t == "PREGUNTAR_APUESTA":
            if msg.get("nombre") == self.mi_nombre:
                # Abre overlay y resetea flags de error/espera
                self.bet_visible_for_me = True
                self.esperando_apuesta = False
                self.bet_error = ""
                self.bet_input.rect = self.centrar_rect(220, 40)
                self.bet_input.rect.y += 30
                self.log("Escribe tu apuesta y presiona ENTER. (ESC cancela)")
            else:
                self.log(f"<< {msg['nombre']} está apostando…")

        elif t == "APUESTA_OK":
            self.log(f"<< {msg['nombre']} apostó ${msg['monto']}.")
            if msg.get("nombre") == self.mi_nombre:
                # Actualiza saldo visual inmediatamente (se restó en servidor)
                try:
                    self.saldo_cache -= int(msg.get("monto", 0))
                except Exception:
                    pass
                self.esperando_apuesta = False
                self.bet_visible_for_me = False
                self.bet_error = ""

        elif t == "RONDA_INICIADA":
            self.log("<< ¡Ronda iniciada!")
            self.en_apuestas = False
            self.en_ronda = True
            self.mostrando_resultados = False
            self.actualizar_botones()

        elif t == "ESTADO":
            # Si estamos en resultados y no hay turno, usa el saldo del estado final
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
            if me and "saldo" in me:
                self.saldo_cache = me["saldo"]

            self.actualizar_botones()

        elif t == "RESULTADOS":
            # Mostrar banca completa, totales y resultados
            self.dealer_cards = msg["banca"]
            self.total_banca_final = msg.get("total_banca")
            self.detalle_resultados = msg.get("detalle", [])
            self.mostrando_resultados = True

            # Actualización visual inmediata del saldo a partir del snapshot
            if not self.players_snapshot:
                self.players_snapshot = deepcopy(self.players)
            me_snap = None
            for j in (self.players_snapshot or []):
                if j.get("nombre") == self.mi_nombre:
                    me_snap = j
                    break
            if me_snap:
                abono = 0
                apuestas = me_snap.get("apuestas", [])
                for idx, _mano in enumerate(me_snap.get("manos", [])):
                    ap = apuestas[idx] if idx < len(apuestas) else 0
                    outcome = None
                    for (nom, i, outc, _delta) in self.detalle_resultados:
                        if nom == self.mi_nombre and i == idx:
                            outcome = outc
                            break
                    if outcome == "gana":
                        abono += ap * 2
                    elif outcome == "empata":
                        abono += ap
                self.saldo_cache += abono

            # Cierra fase
            self.bet_visible_for_me = False
            self.en_ronda = False
            self.turno_nombre = None
            self.mano_turno_idx = None
            self.actualizar_botones()
            self.log("<< Fin de ronda. Pulsa INICIAR para abrir apuestas.")

        elif t == "SALIO":
            self.log(f"<< {msg['nombre']} salió.")

        elif t == "ERROR":
            # Muestra el error y, si estamos en overlay de apuesta, mantenlo visible
            self.log(f"<< ERROR: {msg['mensaje']}")
            if self.en_apuestas:
                self.esperando_apuesta = False
                self.bet_error = str(msg.get("mensaje", "Error"))
                if not self.bet_visible_for_me and self.mi_nombre:
                    self.bet_visible_for_me = True
                self.log("Corrige la apuesta o presiona ESC para cancelar.")

    # ------------------------------ Cierre ------------------------------

    def cerrar(self) -> None:
        """Cierra socket, Pygame y termina el proceso."""
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        pygame.quit()
        sys.exit()

    # ------------------------- Dibujo: Crupier -------------------------

    def draw_dealer_area(self) -> None:
        """Dibuja el encabezado del crupier, total (si aplica) y sus cartas."""
        y0 = AREA_CRUPIER_Y
        right_edge = AREA_CRUPIER_RIGHT

        title_surf = self.title_crupier_surf
        title_rect = title_surf.get_rect()
        title_rect.topright = (right_edge, y0)

        space = 12
        group_left = title_rect.left
        group_right = title_rect.right

        # En resultados, muestra el total de la banca a la derecha del título
        if self.mostrando_resultados and self.total_banca_final is not None:
            total_surf = self.font_title.render(str(self.total_banca_final), True, COLOR_TEXT)
            total_rect = total_surf.get_rect()
            total_rect.midleft = (title_rect.right + space, title_rect.centery)

            # Ajuste si se desborda el margen derecho
            overflow = total_rect.right - right_edge
            if overflow > 0:
                title_rect.move_ip(-overflow, 0)
                total_rect.move_ip(-overflow, 0)

            self.screen.blit(total_surf, total_rect)
            group_left = title_rect.left
            group_right = total_rect.right

        self.screen.blit(title_surf, title_rect)

        # Centra cartas bajo el encabezado (entre group_left y group_right)
        header_center_x = (group_left + group_right) // 2
        cy = y0 + 30
        for (r, p) in self.dealer_cards:
            spr = self.card_sprite(r, p, height=CARD_H_DEALER)
            cx = header_center_x - spr.get_width() // 2
            self.screen.blit(spr, (cx, cy))
            cy += spr.get_height() + 12

    # ------------------------ Dibujo: Jugadores ------------------------

    def draw_players_list(self) -> None:
        """Dibuja hasta 4 paneles de jugadores sin chocar con los botones."""
        x0 = AREA_JUGADORES_X
        y0 = AREA_JUGADORES_Y

        self.screen.blit(self.title_jugadores_surf, (x0, y0 - 36))

        jugadores_dib = self.players_snapshot if (self.mostrando_resultados and self.players_snapshot) else self.players

        # Cálculo de alto disponible hasta los botones
        bottom_lim = BTN_Y - 14
        disponible = max(120, bottom_lim - y0)
        max_paneles = 4
        sep = 12
        panel_h = int((disponible - (max_paneles - 1) * sep) / max_paneles)
        panel_h = max(140, min(panel_h, 190))

        header_h = 26
        card_h_local = max(CARD_H_PLAYER_MIN, panel_h - header_h - 34)

        py = y0
        panel_radius = 10
        panel_w = AREA_JUGADORES_W - 40

        for j in jugadores_dib[:4]:
            # Panel de fondo
            panel_rect = pygame.Rect(x0 - 8, py - 6, panel_w, panel_h)
            pygame.draw.rect(self.screen, COLOR_PANEL, panel_rect, border_radius=panel_radius)

            # Encabezado: nombre y apuesta(s)
            apuestas_str = ", ".join(map(str, j.get("apuestas", []))) or "0"
            header = f"{j['nombre']} | Apuesta(s): {apuestas_str}"
            hdr_surface = self.font.render(header, True, COLOR_TEXT)
            self.screen.blit(hdr_surface, (x0 + 10, py))

            # Cartas y (en resultados) texto centrado verticalmente
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
                        if nom == j["nombre"] and idx == mano_idx:
                            outcome = outc
                            break
                    mapa = {"gana": "Ganaste", "empata": "Empate", "pierde": "Perdiste"}
                    s = f"{total}  {mapa.get(outcome, '')}".strip()
                    txt = self.font.render(s, True, COLOR_SUB)
                    txt_rect = txt.get_rect()
                    center_y = py_cards + (spr_h_local or card_h_local) // 2
                    txt_rect.midleft = (start_x + (px - start_x) + 14, center_y)
                    self.screen.blit(txt, txt_rect)

                px += 18

            py += panel_h + sep

    # ---------------------- Modal de reglas / Bienvenida ---------------------

    def cerrar_welcome(self) -> None:
        """Cierra el modal de bienvenida."""
        self.show_welcome = False

    def draw_wrapped_text(
        self,
        surf: pygame.Surface,
        text: str,
        font: pygame.font.Font,
        color: Tuple[int, int, int],
        rect: pygame.Rect,
        line_gap: int = 4,
    ) -> None:
        """Dibuja texto multilínea envuelto a un rectángulo.

        Args:
            surf: Superficie de destino.
            text: Texto completo.
            font: Fuente para renderizado.
            color: Color RGB.
            rect: Área donde envolver y dibujar.
            line_gap: Espacio entre líneas en píxeles.
        """
        words = text.split()
        lines: List[str] = []
        cur = ""

        for w in words:
            tentative = (cur + " " + w).strip()
            if font.size(tentative)[0] <= rect.width:
                cur = tentative
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)

        y = rect.top
        for ln in lines:
            img = font.render(ln, True, color)
            surf.blit(img, (rect.left, y))
            y += img.get_height() + line_gap

    def draw_welcome_modal(self) -> None:
        """Dibuja el modal con reglas básicas y botón 'Continuar'."""
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        panel = self.centrar_rect(680, 420)
        pygame.draw.rect(self.screen, (40, 45, 54), panel, border_radius=16)

        title = self.font_title.render("¡Bienvenido al Blackjack!", True, (240, 240, 240))
        self.screen.blit(title, title.get_rect(center=(panel.centerx, panel.top + 50)))

        reglas = (
            "Objetivo: acercarte a 21 sin pasarte. Figuras = 10; As = 11 ó 1.\n\n"
            "Tu turno: SUBIR (pedir), QUEDARSE (plantarse), "
            "DOBLAR (solo con 2 cartas), DIVIDIR (si tienes par).\n\n"
            "Crupier: roba hasta 17 (se planta en 17+).\n"
            "Pagos: victoria 1:1, empate devuelve la apuesta."
        )
        text_rect = pygame.Rect(panel.left + 30, panel.top + 100, panel.width - 60, panel.height - 170)
        self.draw_wrapped_text(self.screen, reglas, self.font, (220, 220, 220), text_rect, line_gap=6)

        self.btn_continuar.rect = pygame.Rect(0, 0, 170, 44)
        self.btn_continuar.rect.centerx = panel.centerx
        self.btn_continuar.rect.bottom = panel.bottom - 24
        self.btn_continuar.draw(self.screen, self.font)

    # ---------------------------- Bucle principal ----------------------------

    def run(self) -> None:
        """Bucle principal del juego: eventos, red, render."""
        while True:
            # Eventos
            for event in pygame.event.get():
                if event.type == QUIT:
                    self.cerrar()

                # Abrir reglas con F1
                if event.type == KEYDOWN and event.key == K_F1:
                    self.show_welcome = True

                # Si el modal está abierto, solo procesa su interacción
                if self.show_welcome:
                    if event.type == KEYDOWN and event.key in (K_RETURN, K_SPACE):
                        self.cerrar_welcome()
                    self.btn_continuar.handle(event)
                    continue

                # ESC: cierra o cancela apuesta
                if event.type == KEYDOWN and event.key == K_ESCAPE:
                    if self.bet_visible_for_me:
                        self.send_json({"tipo": "CANCELAR_APUESTA"})
                        self.bet_visible_for_me = False
                        self.esperando_apuesta = False
                        self.bet_error = ""
                    else:
                        self.cerrar()

                # Botones
                self.btn_conectar.handle(event)
                self.btn_iniciar.handle(event)
                self.btn_subir.handle(event)
                self.btn_quedar.handle(event)
                self.btn_doblar.handle(event)
                self.btn_dividir.handle(event)
                self.btn_salir.handle(event)

                # Nombre (ENTER conecta)
                res = self.nombre_input.handle(event)
                if res and res[0] == "enter":
                    self.conectar(res[1])

                # Overlay de apuesta (ENTER envía, con validación y lock de espera)
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
                                self.send_json({"tipo": "APOSTAR", "monto": int(txt)})
                                self.log("Apuesta enviada. Esperando confirmación...")
                        else:
                            self.bet_error = "Monto inválido."
                            self.log("Monto inválido.")

            # Mensajes de red pendientes
            try:
                while True:
                    self.handle_msg(self.rx_queue.get_nowait())
            except queue.Empty:
                pass

            # ---------------------------- Render ----------------------------
            self.screen.fill(COLOR_BG)

            # Banner de jugador o controles de conexión
            if not self.conectado:
                self.btn_conectar.draw(self.screen, self.font)
                self.nombre_input.draw(self.screen, self.font)
            else:
                box_rect = pygame.Rect(MARGEN, MARGEN, BANNER_W, BANNER_H)
                pygame.draw.rect(self.screen, (0, 0, 0), box_rect, border_radius=10)
                name_surface = self.font_title.render(f"Jugador: {self.mi_nombre}", True, (255, 255, 255))
                self.screen.blit(name_surface, name_surface.get_rect(center=box_rect.center))

            # Crupier y jugadores
            self.draw_dealer_area()
            self.draw_players_list()

            # Botones inferiores
            for btn in (
                self.btn_iniciar,
                self.btn_subir,
                self.btn_quedar,
                self.btn_doblar,
                self.btn_dividir,
                self.btn_salir,
            ):
                btn.draw(self.screen, self.font)

            # Overlay de apuesta (con error si aplica)
            if self.bet_visible_for_me:
                overlay = pygame.Surface((W, H), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 140))
                self.screen.blit(overlay, (0, 0))

                panel_rect = self.centrar_rect(460, 210)
                pygame.draw.rect(self.screen, (40, 45, 54), panel_rect, border_radius=14)

                titulo = self.font.render("Tu apuesta", True, (240, 240, 240))
                subt = self.font_small.render("(ENTER para confirmar, ESC para cancelar)", True, (180, 180, 180))
                self.screen.blit(titulo, titulo.get_rect(center=(panel_rect.centerx, panel_rect.top + 40)))
                self.screen.blit(subt, subt.get_rect(center=(panel_rect.centerx, panel_rect.top + 70)))

                self.bet_input.rect.centerx = panel_rect.centerx
                self.bet_input.rect.y = panel_rect.top + 100
                self.bet_input.draw(self.screen, self.font)

                if self.bet_error:
                    err = self.font_small.render(self.bet_error, True, (220, 80, 80))
                    err_rect = err.get_rect(center=(panel_rect.centerx, self.bet_input.rect.bottom + 18))
                    self.screen.blit(err, err_rect)

            # Saldo (abajo-izq, centrado vertical con los botones)
            if self.conectado:
                if not (self.mostrando_resultados or self.en_apuestas):
                    est = self.mi_estado()
                    if est and "saldo" in est:
                        self.saldo_cache = est["saldo"]

                saldo_txt = self.font.render(f"${self.saldo_cache}", True, (255, 255, 255))
                pad_x, pad_y = 10, 6
                box_h = saldo_txt.get_height() + pad_y * 2
                box_y = BTN_Y + (BTN_H - box_h) // 2
                box_rect = pygame.Rect(MARGEN, box_y, saldo_txt.get_width() + pad_x * 2, box_h)
                pygame.draw.rect(self.screen, (0, 0, 0), box_rect, border_radius=8)
                self.screen.blit(saldo_txt, (box_rect.x + pad_x, box_rect.y + pad_y))

            # Banner de estado (abajo-dcha)
            estado = ["Conectado" if self.sock else "Desconectado"]
            if self.en_apuestas:
                estado.append("En apuestas")
            elif self.en_ronda:
                estado.append("En ronda")
            else:
                estado.append("Esperando inicio")
            if self.turno_nombre:
                estado.append(f"Turno: {self.turno_nombre}")
            banner = self.font_small.render(" | ".join(estado), True, (200, 200, 200))
            self.screen.blit(banner, (W - banner.get_width() - MARGEN, H - 26))

            # Modal de bienvenida al final para que tape todo
            if self.show_welcome:
                self.draw_welcome_modal()

            pygame.display.flip()
            self.clock.tick(FPS)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cliente().run()
