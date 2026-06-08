import streamlit as st
from datetime import datetime, timedelta
import pytz
import requests
from supabase import create_client, Client

st.set_page_config(
    page_title="Registro Manual · Monitoreo de Liquidez",
    page_icon="🏦",
    layout="wide",
    
)

# ── Webhook n8n ──────────────────────────────────────────────────────────────
WEBHOOK_FLUJO      = st.secrets["webhooks"]["flujo"]
WEBHOOK_PENDIENTES = st.secrets["webhooks"]["pendientes"]

# ── Cliente Supabase (solo para lectura) ─────────────────────────────────────
@st.cache_resource
def init_supabase() -> Client:
    return create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["anon_key"],
    )

supabase = init_supabase()

USE_MOCK = False
DRY_RUN  = False   # El envío al webhook de n8n se mantiene en producción

WEBHOOK_HEADERS = {"Content-Type": "application/json"}

# ── Usuarios ─────────────────────────────────────────────────────────────────
USERS = {
    "operador_payments": {"password": "PAYments2026$.",    "tipo": "operador"},
    "admin_bpa":         {"password": "BPA2026$.",         "tipo": "admin"},
    "operador_support":  {"password": "Soporte2026$",      "tipo": "soporte"},
}

# ── Mapeo cuenta por proveedor ───────────────────────────────────────────────
CUENTA_MAP = {
    "BCP":    "0062",
    "BBVA":   "01348",
    "ALFIN":  "0196",
    "GMONEY": "3316",
}

# ── Mapeo visual de bancos ───────────────────────────────────────────────────
BANCO_INFO = {
    "BBVA":   {"sigla": "BB",  "color": "#1464A5", "descripcion": "Cuenta dispersora BBVA"},
    "BCP":    {"sigla": "BCP", "color": "#E8B100", "descripcion": "Cuenta operativa BCP"},
    "GMONEY": {"sigla": "GM",  "color": "#E94B3C", "descripcion": "Billetera GMoney"},
    "ALFIN":  {"sigla": "AL",  "color": "#5E2D91", "descripcion": "Cuenta recaudadora Alfin"},
}

SLA_MINUTOS = 5  # tiempo para registrar contingencia desde el fallo

# ── Timezone Lima ────────────────────────────────────────────────────────────
tz_lima = pytz.timezone("America/Lima")

# ── Session state defaults ───────────────────────────────────────────────────
for k, v in [
    ("autenticado", False),
    ("usuario", None),
    ("tipo_usuario", None),
    ("envio_ok", False),
    ("vista", "bandeja"),
    ("fallo_seleccionado", None),
    ("ids_resueltos", set()),
]:
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

def get_pendientes() -> list[dict]:
    ahora = datetime.now(tz_lima)

    if USE_MOCK:
        base = ahora.replace(minute=0, second=0, microsecond=0)
        return [
            {"id": "proc_001", "robot": "rpa_gmoney", "banco": "GMONEY",
             "fallo_detectado": ahora - timedelta(minutes=2),
             "cuenta": "3316", "moneda": "PEN"},
            {"id": "proc_002", "robot": "rpa_bcp", "banco": "BCP",
             "fallo_detectado": ahora - timedelta(minutes=1),
             "cuenta": "0062", "moneda": "PEN"},
        ]

    try:
        resp = (
            supabase.table("registros")
            .select("id, fecha, hora, proveedor, cuenta, moneda, created_at")
            .eq("estado", "Pendiente")
            .execute()
        )
        pendientes = resp.data or []

        data = []
        for p in pendientes:
            created_utc = datetime.fromisoformat(
                p["created_at"].replace("Z", "+00:00")
            )
            fallo_dt = created_utc.astimezone(tz_lima)
            data.append({
                "id":              p["id"],
                "robot":           f"rpa_{p['proveedor'].lower()}",
                "banco":           p["proveedor"],
                "fallo_detectado": fallo_dt,
                "cuenta":          p["cuenta"],
                "moneda":          p.get("moneda", "PEN"),
            })

    except Exception as e:
        st.error(f"❌ Error consultando Supabase: {e}")
        return []

    return [
        f for f in data
        if (ahora - f["fallo_detectado"]).total_seconds() < SLA_MINUTOS * 60
    ]


# ════════════════════════════════════════════════════════════════════════════
# COLABORADORES
# ════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def get_colaboradores() -> list[dict]:
    try:
        resp = (
            supabase.table("colaboradores")
            .select("id, nombre_completo, inicial")
            .eq("activo", True)
            .order("nombre_completo")
            .execute()
        )
        return resp.data or []
    except Exception as e:
        st.error(f"❌ Error cargando colaboradores: {e}")
        return []

# ════════════════════════════════════════════════════════════════════════════
# MODAL DE CONFIRMACIÓN
# ════════════════════════════════════════════════════════════════════════════

def normalizar_monto(valor: str):
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
        
@st.dialog("Confirmar registro")
def modal_confirmacion(registro: dict):
    st.caption("Revisa que todos los datos sean correctos antes de confirmar el envío.")

    if registro.get("tipo_registro") == "Imposibilidad":
            st.warning("⚠️ **Banco indisponible** — No se registrará monto. La falla quedará excluida del conteo de no atendidas.")
    else:
        st.markdown(
            f"""
            <div style="background:#1e3a5f; border:2px solid #2563eb; border-radius:10px;
                        padding:16px 22px; margin-bottom:14px;">
                <div style="color:#93c5fd; font-size:0.75rem; font-weight:600; letter-spacing:0.06em;">
                    MONTO A REGISTRAR ({registro['Moneda']})
                </div>
                <div style="color:#ffffff; font-size:2.2rem; font-weight:700; margin-top:4px;">
                    {registro['Moneda']} {registro['Monto']:,.2f}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("**Detalle del registro:**")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"- **ID Registro:** {registro['ID']}")
        st.markdown(f"- **Proveedor:** {registro['Proveedor']}")
        st.markdown(f"- **Cuenta:** {registro['Cuenta']}")
    with col2:
        st.markdown(f"- **Fecha:** {registro['Fecha']}")
        st.markdown(f"- **Hora:** {registro['Hora']}")
        st.markdown(f"- **Tipo:** {registro['tipo_registro']}")

    st.divider()

    col_ok, col_edit = st.columns(2)
    with col_ok:
        if st.button("✅ Confirmar y enviar", type="primary", use_container_width=True):
            if DRY_RUN:
                # Modo demo: no se envía al webhook real, solo se marca como resuelto en sesión
                st.session_state.ids_resueltos.add(registro["ID"])
                st.session_state.envio_ok = True
                st.session_state.vista = "bandeja"
                st.session_state.fallo_seleccionado = None
                st.rerun()
            else:
                with st.spinner("Enviando registro..."):
                    ok, msg = call_webhook(WEBHOOK_FLUJO, registro, "Webhook · Flujo n8n")
                if ok:
                    st.session_state.ids_resueltos.add(registro["ID"])
                    st.session_state.envio_ok = True
                    st.session_state.vista = "bandeja"
                    st.session_state.fallo_seleccionado = None
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")
                    st.warning("⚠️ No se envió. Reintenta o contacta al administrador.")
    with col_edit:
        if st.button("✏️ Corregir", use_container_width=True):
            st.rerun()


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
# BARRA SUPERIOR (común a bandeja y formulario)
# ════════════════════════════════════════════════════════════════════════════
def barra_superior(titulo: str):
    col_titulo, col_user, col_logout = st.columns([4, 2, 1])
    with col_titulo:
        st.title(titulo)
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
            st.session_state.vista = "bandeja"
            st.session_state.fallo_seleccionado = None
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# VISTA 1 · BANDEJA DE PENDIENTES
# ════════════════════════════════════════════════════════════════════════════
def pantalla_bandeja():
    barra_superior("💧 Monitoreo de Liquidez")

    if DRY_RUN:
        st.info("🧪 **MODO DEMO** — Los envíos no se mandan al webhook real. Los fallos se marcan como resueltos solo en esta sesión.")

    if st.session_state.envio_ok:
        st.success("✅ Registro enviado correctamente.")
        st.session_state.envio_ok = False

    pendientes = get_pendientes()
    ahora = datetime.now(tz_lima)

    # Métricas
    if pendientes:
        tiempos = [SLA_MINUTOS*60 - (ahora - p["fallo_detectado"]).total_seconds() for p in pendientes]
        max_restante = max(max(tiempos), 0)
        mins, secs = divmod(int(max_restante), 60)
        tiempo_str = f"{mins:02d}:{secs:02d}"
    else:
        tiempo_str = "--:--"

    c1, c2, c3 = st.columns(3)
    c1.metric("Fallos pendientes", len(pendientes))
    c2.metric("Resueltos hoy", "—")  # TODO: contar contingencias del día
    c3.metric("Tiempo máx. restante", tiempo_str)

    st.divider()

    if not pendientes:
        st.info("✅ No hay fallos pendientes de contingencia en este momento.")
        if st.button("🔄 Refrescar"):
            st.rerun()
        return

    st.markdown("**FALLOS ACTIVOS — ORDENADOS POR URGENCIA**")
    st.caption("Selecciona un fallo para registrar la contingencia. Solo deberás ingresar el saldo.")

    pendientes_ord = sorted(pendientes, key=lambda p: p["fallo_detectado"])

    for fallo in pendientes_ord:
        transcurrido = (ahora - fallo["fallo_detectado"]).total_seconds()
        restante = max(0, SLA_MINUTOS*60 - transcurrido)
        mins_r, secs_r = divmod(int(restante), 60)
        mins_pasados = int(transcurrido // 60)
        info = BANCO_INFO[fallo["banco"]]
        hm_minuto = 15 if fallo['fallo_detectado'].minute < 30 else 45
        hora_monitoreo = f"{fallo['fallo_detectado'].strftime('%H')}:{hm_minuto:02d}"
        fecha_str_card = fallo['fallo_detectado'].strftime('%d %b %Y').lower()
        color_tiempo = "#dc2626" if restante < 120 else "#f0b400"

        with st.container(border=True):
            col_content, col_btn = st.columns([6, 2])
            with col_content:
                st.markdown(
                    f"""<div style="display:flex;align-items:center;gap:14px;">
                        <div style="width:48px;height:48px;border-radius:10px;background:{info['color']};
                                flex-shrink:0;display:flex;align-items:center;justify-content:center;
                                color:white;font-weight:700;font-size:14px;">{info['sigla']}</div>
                        <div>
                            <div style="font-weight:600;font-size:1.05rem;">{fallo['banco']}
                                <span style="color:#888;font-size:0.85rem;font-weight:400;margin-left:8px;">
                                    · {BANCO_INFO[fallo['banco']]['descripcion']}</span></div>
                            <div style="color:#888;font-size:0.85rem;margin-top:2px;">Hora de monitoreo {hora_monitoreo} · {fecha_str_card}</div>
                            <div style="color:#888;font-size:0.8rem;">Fallo detectado hace {mins_pasados} min</div>
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with col_btn:
                st.markdown(
                    f"<div style='color:{color_tiempo};font-size:0.85rem;text-align:right;margin-bottom:4px;'>"
                    f"⏱ {mins_r:02d}:{secs_r:02d} restantes</div>",
                    unsafe_allow_html=True,
                )
                if st.button("Resolver ↗", key=f"btn_{fallo['id']}", use_container_width=True, type="primary"):
                    st.session_state.fallo_seleccionado = fallo
                    st.session_state.vista = "formulario"
                    st.rerun()

    st.divider()
    if st.button("🔄 Refrescar bandeja"):
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# VISTA 2 · FORMULARIO PRE-RELLENADO
# ════════════════════════════════════════════════════════════════════════════
def pantalla_formulario():
    fallo = st.session_state.fallo_seleccionado
    if fallo is None:
        st.session_state.vista = "bandeja"
        st.rerun()
    try:
        check = (
            supabase.table("registros")
            .select("estado")
            .eq("id", fallo["id"])
            .execute()
        )
        if check.data and check.data[0]['estado'] != "Pendiente":
            st.error(f" Este registros ya fue cerrado ({check.data[0]['estado']}). No se puede registrar")
            if st.button("Volver"):
                st.session_state.vista = "bandeja"
                st.session_state.fallo_seleccionado = None
                st.rerun()
            return
    except Exception:
        pass

    barra_superior("💧 Monitoreo de Liquidez")

    if st.button("← Volver a la bandeja"):
        st.session_state.vista = "bandeja"
        st.session_state.fallo_seleccionado = None
        st.rerun()

    st.warning(f"⚠️ **MODO CONTINGENCIA · {fallo['banco']}** — Registrando saldo para el fallo detectado a las {fallo['fallo_detectado'].strftime('%H:%M:%S')}.")
    st.divider()

    # ── Sección 1: Datos pre-cargados ─────────────────────────────────────────
    st.subheader("1 · Datos del registro")
    st.caption("Información traída desde base de datos. No editable.")

    cuenta_auto = fallo.get("cuenta", CUENTA_MAP[fallo["banco"]])
    fecha_str   = fallo.get("ventana_inicio", fallo["fallo_detectado"]).strftime("%Y-%m-%d")

    col1, col2 = st.columns(2)
    with col1:
        st.text_input("🆔 ID Registro",  value=fallo["id"],         disabled=True)
        st.text_input("🏦 Proveedor",    value=fallo["banco"],      disabled=True)
        st.text_input("🔢 Cuenta",       value=cuenta_auto,         disabled=True)
    with col2:
        hm_minuto_form = 15 if fallo['fallo_detectado'].minute < 30 else 45
        hora_monitoreo_form = f"{fallo['fallo_detectado'].strftime('%H')}:{hm_minuto_form:02d}"
        st.text_input("📅 Fecha",              value=fecha_str,            disabled=True)
        st.text_input("🕐 Hora de monitoreo",  value=hora_monitoreo_form,  disabled=True)
    st.divider()

    # ── Sección 2: Colaborador ────────────────────────────────────────────────
    st.subheader("2 · Colaborador a cargo")
    st.caption("Selecciona tu nombre. Campo obligatorio.")

    colaboradores = get_colaboradores()
    opciones_colab = ["— Selecciona tu nombre —"] + [
        f"{c['nombre_completo']} ({c['inicial']})" for c in colaboradores
    ]
    colaborador_sel = st.selectbox("👤 Colaborador", opciones_colab, index=0)

    st.divider()

    # ── Sección 3: Estado del portal ──────────────────────────────────────────
    st.subheader("3 · Estado del portal bancario")
    st.caption("Actívalo solo si no pudiste ingresar al portal al momento de la contingencia.")

    portal_caido = st.toggle("Portal no disponible", value=False)
    if portal_caido:
        st.warning("⚠️ Al reportar imposibilidad, no se registrará monto. El banco quedará marcado como **Banco indisponible**.")

    st.divider()

    # ── Sección 4: Monto del saldo (solo si portal disponible) ───────────────
    if not portal_caido:
        st.subheader("4 · Monto del saldo")
        st.caption("Único campo a completar. Ingresa el saldo tal como aparece en el portal.")

        moneda_default = fallo.get("moneda", "PEN")
        moneda = st.selectbox(
            "💱 Moneda",
            options=["PEN", "USD"],
            index=["PEN", "USD"].index(moneda_default) if moneda_default in ["PEN", "USD"] else 0,
        )

        monto_raw = st.text_input(
            f"💰 Monto ({moneda})",
            placeholder="Ej: 1500000.50  |  1,500,000.50  |  1.500.000,50",
            help="Se normalizará automáticamente. Acepta punto o coma como separador decimal.",
        )

        monto_float = normalizar_monto(monto_raw)

        if monto_raw:
            if monto_float is not None:
                st.markdown(
                    f"""
                    <div style="background:#14532d; border:1px solid #16a34a; border-radius:8px;
                                padding:12px 18px; margin:8px 0;">
                        <div style="color:#86efac; font-size:0.75rem; font-weight:600; letter-spacing:0.06em;">
                            MONTO INTERPRETADO · VERIFICA EL SEPARADOR DE MILES
                        </div>
                        <div style="color:#ffffff; font-size:1.8rem; font-weight:700; margin-top:4px;">
                            {moneda} {monto_float:,.2f}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.caption("⚠️ La coma (,) indica miles, NO es decimal. Confirma que el monto coincide con el portal bancario.")
            else:
                st.error("❌ No se pudo interpretar el monto. Verifica el formato ingresado.")
    else:
        moneda = fallo.get("moneda", "PEN")
        monto_raw = None
        monto_float = None
        

    # ── Botón envío ───────────────────────────────────────────────────────────
    st.caption("**Paso 1 de 2** · Al continuar se abrirá un resumen para confirmar antes de enviar.")
    enviar = st.button("➡️ Revisar y confirmar envío", type="primary", use_container_width=True)

    if enviar:
            errores = []

            if colaborador_sel == "— Selecciona tu nombre —":
                errores.append("Debes seleccionar tu nombre antes de continuar.")

            if not portal_caido:
                if not monto_raw or monto_raw.strip() == "":
                    errores.append("El monto es obligatorio.")
                elif monto_float is None:
                    errores.append("El monto ingresado no tiene un formato válido.")
                elif monto_float < 5000:
                    errores.append("El saldo debe ser ≥ S/ 5,000 (regla anti-data basura).")

            if errores:
                for e in errores:
                    st.error(f"⚠️ {e}")
                return

            hora_envio = datetime.now(tz_lima).strftime("%H:%M:%S")

            if portal_caido:
                registro = {
                    "ID":                   fallo["id"],
                    "Fecha":                fecha_str,
                    "Hora":                 hora_envio,
                    "Moneda":               moneda,
                    "Proveedor":            fallo["banco"],
                    "Cuenta":               fallo.get("cuenta", CUENTA_MAP[fallo["banco"]]),
                    "Estado":               "Fallido",
                    "Monto":                None,
                    "tipo_registro":        "Imposibilidad",
                    "motivo_imposibilidad": "Banco indisponible",
                    "colaborador":          colaborador_sel,
                }
            else:
                registro = {
                    "ID":            fallo["id"],
                    "Fecha":         fecha_str,
                    "Hora":          hora_envio,
                    "Moneda":        moneda,
                    "Proveedor":     fallo["banco"],
                    "Cuenta":        fallo.get("cuenta", CUENTA_MAP[fallo["banco"]]),
                    "Estado":        "Fallido",
                    "Monto":         float(f"{monto_float:.2f}"),
                    "tipo_registro": "Manual",
                    "colaborador":   colaborador_sel,
                }

            modal_confirmacion(registro)


# ════════════════════════════════════════════════════════════════════════════
# ROUTER
# ════════════════════════════════════════════════════════════════════════════
if not st.session_state.autenticado:
    pantalla_login()
elif st.session_state.vista == "formulario":
    pantalla_formulario()
else:
    pantalla_bandeja()