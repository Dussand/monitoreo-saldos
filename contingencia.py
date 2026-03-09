import streamlit as st
from datetime import datetime
import pytz
import requests

st.set_page_config(
    page_title="Registro Manual · Monitoreo de Liquidez",
    page_icon="🏦",
    layout="centered",
)

# ── Webhook n8n ──────────────────────────────────────────────────────────────
# Un solo webhook: recibe el bloque completo. n8n se encarga internamente
# de actualizar estado en 'procesamiento' y continuar hacia 'registros'.
WEBHOOK_FLUJO   = "https://operationskashio.app.n8n.cloud/webhook-test/uipath-data"

WEBHOOK_HEADERS = {"Content-Type": "application/json"}

# ── Usuarios ─────────────────────────────────────────────────────────────────
USERS = {
    "operador_payments": {"password": "PAYments2026$.",    "tipo": "operador"},
    "admin_bpa":      {"password": "BPA2026$.",     "tipo": "admin"},
    "operador_support":  {"password": "Soporte2026$", "tipo": "soporte"},
}

# ── Mapeo cuenta por proveedor ───────────────────────────────────────────────
CUENTA_MAP = {
    "BCP":    "01233",
    "BBVA":   "01348",
    "ALFIN":  "0196",
    "GMONEY": "3316",
}

# ── Timezone Lima ────────────────────────────────────────────────────────────
tz_lima = pytz.timezone("America/Lima")

# ── Session state defaults ───────────────────────────────────────────────────
for k, v in [("autenticado", False), ("usuario", None), ("tipo_usuario", None)]:
    if k not in st.session_state:
        st.session_state[k] = v


# ════════════════════════════════════════════════════════════════════════════
# HELPERS — llamadas a webhooks
# ════════════════════════════════════════════════════════════════════════════
def call_webhook(url: str, payload: dict, nombre: str) -> tuple[bool, str]:
    """Hace POST al webhook y retorna (éxito, mensaje)."""
    try:
        resp = requests.post(url, headers=WEBHOOK_HEADERS, json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return True, f"{nombre}: OK ({resp.status_code})"
        else:
            try:
                detalle = resp.json().get("message", resp.text)
            except Exception:
                detalle = resp.text
            return False, f"{nombre}: Error {resp.status_code} — {detalle}"
    except requests.exceptions.Timeout:
        return False, f"{nombre}: Timeout (15s sin respuesta)."
    except requests.exceptions.ConnectionError:
        return False, f"{nombre}: Error de conexión."
    except Exception as e:
        return False, f"{nombre}: Error inesperado — {str(e)}"


# ════════════════════════════════════════════════════════════════════════════
# PANTALLA DE LOGIN
# ════════════════════════════════════════════════════════════════════════════
def pantalla_login():
    st.title("🏦 Monitoreo de Liquidez")
    st.caption("Sistema de registro manual · Modo contingencia")
    st.divider()

    st.subheader("🔐 Iniciar sesión")
    st.caption("Ingresa tus credenciales para acceder al formulario de registro.")

    usuario  = st.text_input("Usuario", placeholder="Tu nombre de usuario")
    password = st.text_input("Contraseña", type="password", placeholder="Tu contraseña")

    if st.button("Ingresar", type="primary", use_container_width=True):
        if not usuario or not password:
            st.error("⚠️ Completa usuario y contraseña.")
        elif usuario not in USERS:
            st.error("❌ Usuario no encontrado.")
        elif USERS[usuario]["password"] != password:
            st.error("❌ Contraseña incorrecta.")
        else:
            st.session_state.autenticado  = True
            st.session_state.usuario      = usuario
            st.session_state.tipo_usuario = USERS[usuario]["tipo"]
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# FORMULARIO PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════
def pantalla_formulario():

    # ── Barra superior ────────────────────────────────────────────────────────
    col_titulo, col_user, col_logout = st.columns([4, 2, 1])
    with col_titulo:
        st.title("💧 Monitoreo de Liquidez")
    with col_user:
        st.markdown(
            f"<div style='padding-top:18px; font-size:0.82rem; color:gray;'>"
            f"👤 <b>{st.session_state.usuario}</b> · {st.session_state.tipo_usuario}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_logout:
        if st.button("Salir", use_container_width=True):
            for k in ["autenticado", "usuario", "tipo_usuario"]:
                st.session_state[k] = None if k != "autenticado" else False
            st.rerun()

    st.warning("⚠️ **MODO CONTINGENCIA** — Completa este formulario cuando el robot de captura automática falle. El registro quedará en estado **Pendiente** hasta ser procesado.")
    st.divider()

    # ── Sección 1: Identificación ─────────────────────────────────────────────
    st.subheader("1 · Identificación del registro")
    st.caption("El robot te enviará el ID del registro fallido. Pégalo aquí para mantener la trazabilidad.")

    registro_id = st.text_input(
        "ID del registro",
        placeholder="Pega aquí el ID enviado por el robot",
        help="ID único del registro fallido. Lo recibes del robot por correo o Teams.",
    )

    col1, col2 = st.columns(2)
    with col1:
        fecha_input = st.date_input(
            "📅 Fecha",
            value=datetime.now(tz_lima).date(),
            format="DD/MM/YYYY",
            help="Fecha en que ocurrió el saldo. Por defecto: hoy (hora Lima).",
        )
    with col2:
        st.text_input(
            "🕐 Hora (Lima)",
            value=datetime.now(tz_lima).strftime("%H:%M:%S"),
            disabled=True,
            help="Se captura automáticamente al momento del envío (zona horaria Lima).",
        )

    st.divider()

    # ── Sección 2: Proveedor y cuenta ────────────────────────────────────────
    st.subheader("2 · Proveedor y cuenta")
    st.caption("Selecciona el proveedor. La cuenta operativa se asigna automáticamente según la configuración interna.")

    col3, col4 = st.columns(2)
    with col3:
        moneda = st.selectbox(
            "💱 Moneda",
            options=["PEN", "USD"],
            help="Moneda del saldo registrado.",
        )
    with col4:
        proveedor = st.selectbox(
            "🏦 Proveedor",
            options=["BCP", "BBVA", "ALFIN", "GMONEY"],
            help="Proveedor bancario o de pagos de la cuenta operativa.",
        )

    cuenta_auto = CUENTA_MAP[proveedor]
    st.text_input(
        "🔢 Cuenta operativa",
        value=cuenta_auto,
        disabled=True,
        help=f"Asignada automáticamente para {proveedor}. No editable.",
    )

    st.divider()

    # ── Sección 3: Monto ──────────────────────────────────────────────────────
    st.subheader("3 · Monto del saldo")
    st.caption("Ingresa el saldo tal como aparece en el portal del proveedor. Se aceptan distintos formatos de número.")

    monto_raw = st.text_input(
        f"💰 Monto ({moneda})",
        placeholder="Ej: 1500000.50  |  1,500,000.50  |  1.500.000,50",
        help="Se normalizará automáticamente. Acepta punto o coma como separador decimal.",
    )

    def normalizar_monto(valor: str) -> float | None:
        if not valor or valor.strip() == "":
            return None
        v = valor.strip().replace(" ", "")
        if "," in v and "." in v:
            if v.rfind(",") > v.rfind("."):
                v = v.replace(".", "").replace(",", ".")
            else:
                v = v.replace(",", "")
        elif "," in v:
            partes = v.split(",")
            if len(partes) == 2 and len(partes[1]) <= 2:
                v = v.replace(",", ".")
            else:
                v = v.replace(",", "")
        try:
            return float(v)
        except ValueError:
            return None

    monto_float = normalizar_monto(monto_raw)

    if monto_raw:
        if monto_float is not None:
            st.success(f"✅ Monto interpretado: **{moneda} {monto_float:.2f}**")
        else:
            st.error("❌ No se pudo interpretar el monto. Verifica el formato ingresado.")

    st.divider()

    # ── Sección 4: Campos del sistema ─────────────────────────────────────────
    st.subheader("4 · Campos del sistema")
    st.caption("Valores asignados automáticamente por el sistema. No son editables por el operador.")
    st.text_input(
        "📋 Tipo de registro",
        value="Manual",
        disabled=True,
        help="Indica que fue ingresado manualmente en modo contingencia.",
    )

    st.divider()

    # ── Botón envío ───────────────────────────────────────────────────────────
    enviar = st.button("⬆️ Registrar saldo manual", type="primary", use_container_width=True)

    if enviar:
        errores = []
        if not registro_id or registro_id.strip() == "":
            errores.append("El ID del registro es obligatorio.")
        if not monto_raw or monto_raw.strip() == "":
            errores.append("El monto es obligatorio.")
        elif monto_float is None:
            errores.append("El monto ingresado no tiene un formato válido.")

        if errores:
            for e in errores:
                st.error(f"⚠️ {e}")
            return

        hora_envio = datetime.now(tz_lima).strftime("%H:%M:%S")
        fecha_str  = fecha_input.strftime("%Y-%m-%d")

        # ── Payload completo ──────────────────────────────────────────────────
        registro = {
            "ID":            registro_id.strip(),
            "Fecha":         fecha_str,
            "Hora":          hora_envio,
            "Moneda":        moneda,
            "Proveedor":     proveedor,
            "Cuenta":        cuenta_auto,
            "Estado":        "Fallido",
            "Monto":         float(f"{monto_float:.2f}"),
            "tipo_registro": "Manual"
        }

        # ── Envío único al webhook — n8n maneja el resto internamente ─────────
        with st.spinner("Actualizando..."):
            ok, msg = call_webhook(WEBHOOK_FLUJO, registro, "Webhook · Flujo n8n")

        # ── Resultado ─────────────────────────────────────────────────────────
        st.subheader("Resultado del envío")

        if ok:
            st.success(f"✅ Registro actualizado")
            # with st.expander("Ver payload enviado", expanded=False):
            #     st.json(registro)
            # st.info("💡 Para ingresar un nuevo registro, recarga la página.")
        else:
            st.error(f"❌ {msg}")
            st.warning("⚠️ No se envió la información. Reintenta o contacta al administrador.")
            # with st.expander("Ver payload que se intentó enviar", expanded=True):
            #     st.json(registro)


# ════════════════════════════════════════════════════════════════════════════
# ROUTER
# ════════════════════════════════════════════════════════════════════════════
if not st.session_state.autenticado:
    pantalla_login()
else:
    pantalla_formulario()