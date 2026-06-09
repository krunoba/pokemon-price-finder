import streamlit as st
import anthropic
import requests
import base64
import json
import os
from pathlib import Path
from tavily import TavilyClient

st.set_page_config(
    page_title="Pokemon Price Finder",
    page_icon="🃏",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# iOS Home Screen Icon (Pokéball SVG als apple-touch-icon)
_pokeball_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<circle cx="50" cy="50" r="48" fill="white" stroke="#222" stroke-width="4"/>
<path d="M2 50 A48 48 0 0 1 98 50" fill="#e53935"/>
<rect x="2" y="46" width="96" height="8" fill="#222"/>
<circle cx="50" cy="50" r="13" fill="white" stroke="#222" stroke-width="4"/>
<circle cx="50" cy="50" r="6" fill="#e53935"/>
</svg>"""
_icon_b64 = base64.b64encode(_pokeball_svg.encode()).decode()
st.markdown(
    f'<link rel="apple-touch-icon" href="data:image/svg+xml;base64,{_icon_b64}">',
    unsafe_allow_html=True,
)

# Mobile CSS
st.markdown("""
<style>
    .main > div { padding-top: 1rem; }
    .stCameraInput > label { font-size: 1.1rem; font-weight: 600; }
    .price-box {
        background: #1a1a2e;
        border: 1px solid #e94560;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin: 0.5rem 0;
    }
    .price-row { display: flex; justify-content: space-between; padding: 2px 0; }
    .price-label { color: #aaa; font-size: 0.9rem; }
    .price-value { font-weight: 600; font-size: 0.95rem; }
    .highlight { color: #f5a623; font-size: 1.2rem; font-weight: 700; }
    .card-tag {
        display: inline-block;
        background: #e94560;
        color: white;
        border-radius: 6px;
        padding: 2px 8px;
        font-size: 0.75rem;
        margin: 2px;
    }
    .warn { color: #f5a623; font-size: 0.85rem; }
</style>
""", unsafe_allow_html=True)


for _p in [".env", "../../.env", "../../../.env"]:
    if os.path.exists(_p):
        with open(_p, encoding="utf-8") as _f:
            for _line in _f.read().splitlines():
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
        break


def get_api_key(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.getenv(key, "")


def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    pwd = st.text_input("Passwort", type="password", key="pwd_input")
    if st.button("Einloggen"):
        correct = get_api_key("APP_PASSWORD")
        if pwd == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Falsches Passwort")
    return False


def fix_orientation(image_bytes: bytes) -> bytes:
    from PIL import Image, ImageOps
    import io
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def crop_bottom_right(image_bytes: bytes) -> bytes:
    """Schneidet untere rechte Ecke aus — dort steht die Kartennummer."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    crop = img.crop((0, h * 0.82, w * 0.55, h))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _ask_vision(client, model: str, image_bytes: bytes, media_type: str, prompt: str) -> str:
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    r = client.messages.create(
        model=model, max_tokens=512,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
            {"type": "text", "text": prompt},
        ]}],
    )
    return r.content[0].text.strip()


def identify_card(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    client = anthropic.Anthropic(api_key=get_api_key("ANTHROPIC_API_KEY"))

    # Sonnet für Identifikation — V / VMAX / VSTAR / GX / EX werden exakt unterschieden
    raw = _ask_vision(client, "claude-sonnet-4-6", image_bytes, media_type, """Analyze this Pokemon card. Return ONLY JSON. Read EXACTLY what is printed — do NOT guess.

CRITICAL: The suffix after the Pokemon name is crucial and must be read precisely:
- "Charizard V" ≠ "Charizard VMAX" ≠ "Charizard VSTAR" ≠ "Charizard GX" ≠ "Charizard EX"
- Copy the exact suffix character by character from the card.

{
  "name": "EXACT name as printed, e.g. Charizard VSTAR or Gengar & Mimikyu-GX",
  "set_name": "set name or null",
  "card_number": "EXACTLY as printed e.g. 056/094 — read digit by digit, do NOT confuse 0/8, 5/6, 1/7",
  "set_number": "number before slash or null",
  "rarity": "exact rarity or null",
  "condition_estimate": "Near Mint/Lightly Played/Moderately Played/Heavily Played/Damaged",
  "is_holo": true or false,
  "is_first_edition": true or false,
  "is_shadowless": true or false,
  "language": "English/German/Japanese",
  "card_type": "Pokemon/Trainer/Energy"
}
Return ONLY JSON. null only for genuinely unreadable values.""")
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    result = json.loads(raw)

    # Haiku-Crop nur als Fallback wenn Sonnet keine Nummer gelesen hat
    if not result.get("card_number"):
        try:
            crop_bytes = crop_bottom_right(image_bytes)
            num_raw = _ask_vision(client, "claude-haiku-4-5-20251001", crop_bytes, "image/jpeg",
                "Pokemon card bottom-left corner. Read ONLY the card number (e.g. 056/094 or 165/181). "
                "Read each digit carefully. Return ONLY the number, nothing else.")
            num_raw = num_raw.strip().strip('"')
            if num_raw and num_raw.lower() != "null" and "/" in num_raw:
                result["card_number"] = num_raw
                result["set_number"] = num_raw.split("/")[0]
        except Exception:
            pass

    return result


def normalize_name(name: str) -> list[str]:
    variants = [name]
    for suffix in [" GX", " EX", " V", " VMAX", " VSTAR", " VStar"]:
        if name.endswith(suffix):
            hyphenated = name[: -len(suffix)] + suffix.replace(" ", "-")
            if hyphenated not in variants:
                variants.append(hyphenated)
    return variants


def _api_get(headers, query, pageSize=10):
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api.pokemontcg.io/v2/cards",
                headers=headers,
                params={"q": query, "select": "id,name,set,number,rarity,images,tcgplayer,cardmarket", "pageSize": pageSize},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except requests.exceptions.Timeout:
            if attempt == 2:
                raise
    return []


def lookup_prices(card_info: dict) -> list[dict]:
    api_key = get_api_key("POKEMON_TCG_API_KEY")
    headers = {"X-Api-Key": api_key} if api_key else {}
    name = card_info.get("name") or ""
    set_name = card_info.get("set_name") or ""

    # set_number aus card_number extrahieren falls null
    set_num = str(card_info.get("set_number") or "")
    if not set_num:
        card_number = card_info.get("card_number") or ""
        if "/" in card_number:
            set_num = card_number.split("/")[0].strip()

    # Japanische Sets existieren nicht in der englischen API → Set-Filter weglassen
    is_japanese = (card_info.get("language") or "").lower() == "japanese"
    if is_japanese:
        set_name = ""

    def _norm(n):
        try: return str(int(n))
        except: return (n or "").strip()

    norm_set_num = _norm(set_num)

    # Strategie 1: Name + Nummer (beide Schreibweisen)
    if set_num:
        for num_query in dict.fromkeys([set_num, norm_set_num]):
            for name_variant in normalize_name(name):
                cards = _api_get(headers, f'name:"{name_variant}" number:{num_query}')
                if cards:
                    return cards

    # Strategie 2: Name + Set
    if set_name:
        for name_variant in normalize_name(name):
            cards = _api_get(headers, f'name:"{name_variant}" set.name:"{set_name}"')
            if cards:
                if norm_set_num:
                    filtered = [c for c in cards if _norm(c.get("number")) == norm_set_num]
                    if filtered:
                        return filtered
                return cards

    # Strategie 3: Nur Name
    for name_variant in normalize_name(name):
        cards = _api_get(headers, f'name:"{name_variant}"')
        if cards:
            if norm_set_num:
                filtered = [c for c in cards if _norm(c.get("number")) == norm_set_num]
                if filtered:
                    return filtered
            return cards

    return []


def tavily_price_search(card_info: dict) -> str | None:
    """Sucht aktuelle Marktpreise via Tavily und gibt deutsche Zusammenfassung zurück."""
    tavily_key = get_api_key("TAVILY_API_KEY")
    if not tavily_key:
        return None

    name = card_info.get("name") or ""
    card_number = card_info.get("card_number") or ""
    set_name = card_info.get("set_name") or ""
    language = (card_info.get("language") or "English").lower()

    if language == "japanese":
        query = f'Pokemon card "{name}" {card_number} "{set_name}" Japanese price cardmarket ebay sold listings'
    elif language == "german":
        query = f'Pokemon Karte "{name}" {card_number} Deutsch Preis cardmarket.com'
    else:
        query = f'Pokemon card "{name}" {card_number} price cardmarket tcgplayer'

    try:
        tavily = TavilyClient(api_key=tavily_key)
        result = tavily.search(query=query, search_depth="basic", max_results=5, include_answer=True)
        raw_answer = result.get("answer") or ""
        sources = result.get("results", [])

        # Rohdaten für Claude zusammenstellen
        raw_text = raw_answer + "\n" + "\n".join(
            f"{s.get('title','')}: {s.get('content','')[:300]}" for s in sources[:4]
        )

        # Claude Haiku übersetzt und fasst auf Deutsch zusammen
        anthropic_client = anthropic.Anthropic(api_key=get_api_key("ANTHROPIC_API_KEY"))
        summary = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content":
                f"Fasse die folgenden Preisinformationen für die Pokemon-Karte '{name} {card_number}' "
                f"auf Deutsch in 3-4 Sätzen zusammen. Nenne konkrete Preise falls vorhanden. "
                f"Keine Einleitung, direkt zum Punkt:\n\n{raw_text}"
            }]
        )
        german_summary = summary.content[0].text.strip()

        lines = [german_summary]
        for s in sources[:3]:
            title = s.get("title", "")
            url = s.get("url", "")
            if title and url:
                lines.append(f"- [{title}]({url})")
        return "\n".join(lines)
    except Exception:
        return None


def fmt(val, prefix="", fallback="—"):
    return f"{prefix}{val}" if val is not None else fallback


def render_price_card(card: dict, card_info: dict):
    c_set = card.get("set", {})
    tcg = card.get("tcgplayer", {})
    cm = card.get("cardmarket", {})
    cm_prices = cm.get("prices", {})
    tcg_prices = tcg.get("prices", {})
    img_url = (card.get("images") or {}).get("large") or (card.get("images") or {}).get("small")

    tcg_market = None
    for pd in tcg_prices.values():
        tcg_market = pd.get("market")
        if tcg_market:
            break

    cm_trend = cm_prices.get("trendPrice")
    cm_avg7 = cm_prices.get("avg7")
    cm_low = cm_prices.get("lowPrice")

    col_img, col_price = st.columns([1, 2])

    with col_img:
        if img_url:
            st.image(img_url, use_container_width=True)

    with col_price:
        st.markdown(f"""
        <div class="price-box">
            <div style="font-size:1rem; font-weight:700; margin-bottom:6px">{card.get('name')}</div>
            <div>
                <span class="card-tag">{c_set.get('name')}</span>
                <span class="card-tag">Nr. {card.get('number')}</span>
                <span class="card-tag">{card.get('rarity','')}</span>
            </div>
            <hr style="border-color:#333; margin:8px 0">
            <div style="font-size:0.75rem; color:#aaa; margin-bottom:6px">💶 CARDMARKET (EUR)</div>
            <div class="price-row">
                <span class="price-label">Trend</span>
                <span class="highlight">{fmt(cm_trend, 'EUR ')}</span>
            </div>
            <div class="price-row">
                <span class="price-label">Ø 7 Tage</span>
                <span class="price-value">{fmt(cm_avg7, 'EUR ')}</span>
            </div>
            <div class="price-row">
                <span class="price-label">Günstigster</span>
                <span class="price-value">{fmt(cm_low, 'EUR ')}</span>
            </div>
            <hr style="border-color:#333; margin:8px 0">
            <div style="font-size:0.75rem; color:#aaa; margin-bottom:6px">💵 TCGPLAYER (USD)</div>
            <div class="price-row">
                <span class="price-label">Market</span>
                <span class="highlight">{fmt(tcg_market, '$ ')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with st.expander("Alle TCGPlayer-Preise"):
        for cond, pd in tcg_prices.items():
            m, lo, hi = pd.get("market"), pd.get("low"), pd.get("high")
            if any([m, lo, hi]):
                lbl = cond.replace("Holofoil", "Holo").replace("ReverseHolofoil", "Rev.Holo")
                st.markdown(f"`{lbl}` — Market: **{fmt(m,'$')}** | Low: {fmt(lo,'$')} | High: {fmt(hi,'$')}")

    if tcg.get("url"):
        st.markdown(f"[→ TCGPlayer]({tcg['url']})  |  [→ Cardmarket]({cm.get('url','')})")


# ── UI ──────────────────────────────────────────────────────────────────────

if not check_password():
    st.stop()

st.title("🃏 Pokemon Price Finder")

if "upload_key" not in st.session_state:
    st.session_state["upload_key"] = 0

st.caption("Foto aufnehmen oder aus Galerie wählen")
img_file = st.file_uploader(
    "Kartenfoto",
    type=["jpg", "jpeg", "png", "webp"],
    label_visibility="collapsed",
    accept_multiple_files=False,
    key=f"uploader_{st.session_state['upload_key']}",
)

if img_file is not None:
    img_bytes = img_file.getvalue()

    ext = getattr(img_file, "name", "card.jpg").lower()
    media_type = "image/png" if ext.endswith(".png") else "image/webp" if ext.endswith(".webp") else "image/jpeg"

    with st.spinner("Karte wird analysiert..."):
        try:
            card_info = identify_card(img_bytes, media_type)
        except Exception as e:
            st.error(f"Fehler bei der Kartenerkennung: {e}")
            st.stop()

    # Karten-Info
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**{card_info.get('name', '—')}**")
        st.caption(f"{card_info.get('set_name') or '—'}  ·  Nr. {card_info.get('card_number') or '—'}")
    with col2:
        cond = card_info.get("condition_estimate", "—")
        cond_color = {"Near Mint": "🟢", "Lightly Played": "🟡", "Moderately Played": "🟠", "Heavily Played": "🔴", "Damaged": "🔴"}.get(cond, "⚪")
        st.markdown(f"**Zustand:** {cond_color} {cond}")
        flags = []
        if card_info.get("is_first_edition"): flags.append("1st Ed.")
        if card_info.get("is_shadowless"): flags.append("Shadowless")
        if card_info.get("is_holo"): flags.append("Holo")
        if flags: st.caption(" · ".join(flags))

    with st.spinner("Marktpreise werden geladen..."):
        try:
            cards = lookup_prices(card_info)
        except Exception as e:
            st.error(f"Fehler bei der Preisabfrage: {e}")
            st.stop()

    language = (card_info.get("language") or "English").lower()
    is_japanese = language == "japanese"
    is_german = language == "german"
    needs_web_search = is_japanese or is_german

    # Tavily-Preissuche für JP/DE Karten
    tavily_result = None
    if needs_web_search:
        with st.spinner("Suche aktuelle Marktpreise (Cardmarket / eBay)..."):
            tavily_result = tavily_price_search(card_info)

    if is_japanese:
        st.info("🇯🇵 Japanische Karte — Preise unten sind englische Äquivalente. Aktuelle JP-Marktpreise aus Websuche:")
    elif is_german:
        st.info("🇩🇪 Deutsche Karte — Cardmarket-Preise gelten auch für DE-Druck.")

    if tavily_result:
        with st.expander("🌐 Web-Marktpreise (Cardmarket / eBay)", expanded=True):
            st.markdown(tavily_result)
    elif needs_web_search:
        tavily_key = get_api_key("TAVILY_API_KEY")
        if not tavily_key:
            st.warning("TAVILY_API_KEY fehlt in .env — Web-Preissuche nicht verfügbar. Key auf tavily.com holen (kostenlos).")

    detected_name = card_info.get("name") or ""
    detected_num = (card_info.get("set_number") or "").strip()

    def norm_num(n):
        try: return str(int(n))
        except: return (n or "").strip()

    # Karten validieren: nur exakter Namens-Match anzeigen
    def name_matches(card):
        api_name = (card.get("name") or "").lower()
        for variant in normalize_name(detected_name):
            if api_name == variant.lower():
                return True
        return False

    valid_cards = [c for c in cards if name_matches(c)]

    if is_japanese:
        # Für JP-Karten: nur englische Karten mit gleichem Namen UND gleicher Nummer
        exact = [c for c in valid_cards if norm_num(c.get("number")) == norm_num(detected_num)]
        if exact:
            st.markdown("**Englische Version (gleiche Kartennummer)**")
            render_price_card(exact[0], card_info)
        else:
            if valid_cards:
                st.caption(f"ℹ️ Kein englisches Äquivalent mit Nr. {detected_num} gefunden — Tavily-Preise oben gelten für die japanische Version.")
            col_img, col_info = st.columns([1, 2])
            with col_img:
                st.image(fix_orientation(img_bytes), use_container_width=True)
            with col_info:
                st.markdown(f"**{detected_name}**")
                st.caption(f"Nr. {detected_num}")
        # Kein englisches Substitut mit anderer Nummer zeigen
    elif not valid_cards:
        col_img, col_info = st.columns([1, 2])
        with col_img:
            st.image(fix_orientation(img_bytes), use_container_width=True)
        with col_info:
            st.markdown(f"**{detected_name}**")
            st.caption(card_info.get("set_name") or "")
        if not tavily_result:
            st.warning(f"Keine Karte mit Name '{detected_name}' in der Datenbank gefunden.")
    elif len(valid_cards) > 1:
        exact = [c for c in valid_cards if norm_num(c.get("number")) == norm_num(detected_num)]
        if exact:
            render_price_card(exact[0], card_info)
        else:
            st.markdown(f"<p class='warn'>⚠️ {len(valid_cards)} Varianten — Nr. {detected_num} nicht eindeutig zuordenbar. Alle Varianten:</p>", unsafe_allow_html=True)
            for card in valid_cards:
                render_price_card(card, card_info)
    else:
        render_price_card(valid_cards[0], card_info)

    st.divider()
    if st.button("🔄 Neue Karte scannen"):
        st.session_state["upload_key"] += 1
        st.rerun()
