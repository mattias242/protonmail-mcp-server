"""HTML → läsbar text för e-post.

Ersätter den gamla regex-strippningen, som släppte igenom <style>/<script>,
MSO-villkorskommentarer och '>' i attributvärden, inte avkodade entiteter och
gjorde HTML på en rad till ett enda textblock. Bygger på stdlib html.parser.
"""
import html
import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .i18n import _

# Innehåll som aldrig visas för läsaren
_SKIP = {"style", "script", "template", "title"}
# Ger radbrytning före och efter
_BLOCK = {
    "address", "article", "aside", "caption", "center", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "header", "li", "main",
    "nav", "ol", "section", "table", "tbody", "tfoot", "thead", "tr", "ul",
}
# Ger tom rad före och efter
_PARAGRAPH = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "pre", "blockquote"}
# Hör hemma i <head>; allt annat stänger head, som i en webbläsare (K5)
_HEAD_TAGS = {
    "html", "head", "title", "meta", "link", "style", "script", "base", "noscript", "template",
}
_WS_RE = re.compile(r"\s+")
# Element utan innehåll (inget sluttagg), som aldrig kan gömma text
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
}
# Starttaggar som implicit stänger ett öppet element (HTML:s optional end tags)
_IMPLIED_END = {
    "p": _PARAGRAPH | _BLOCK,
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th", "tr"},
    "th": {"td", "th", "tr"},
    "tr": {"tr"},
    "option": {"option"},
}
# Starttagg → de öppna element den stänger (inversen av _IMPLIED_END)
_CLOSED_BY: dict[str, frozenset[str]] = {}
for _open, _closers in _IMPLIED_END.items():
    for _closer in _closers:
        _CLOSED_BY[_closer] = _CLOSED_BY.get(_closer, frozenset()) | {_open}
# Element som avgränsar sökningen efter ett implicit stängt element: ett <li>
# i en inre lista stänger inte den yttre listans <li>, en <td> i en inre
# tabell inte den yttre tabellens cell (HTML:s "scope")
_SCOPE_BOUNDARY = frozenset({"table", "ul", "ol", "dl", "td", "th", "caption", "button", "html", "body"})
# Så djupt i stacken söks ett implicit stängt element — längre ned räknas det
# som inte öppet, så att varje starttagg kostar högst så mycket
_MAX_IMPLIED_SCAN = 64
_SIZE_RE = re.compile(r"(-?[0-9]*\.?[0-9]+)(px|pt|em|rem|%)?$")
# Längd i px för enheterna (em/rem räknas mot 16px, som webbläsarens standard)
_PX_PER = {"px": 1.0, "pt": 4 / 3, "em": 16.0, "rem": 16.0}
_HEX_RE = re.compile(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_RGB_RE = re.compile(r"rgba?\(([^()]*)\)$")
_NAMED_COLORS = {"white": (255, 255, 255), "black": (0, 0, 0)}
# Så nära varandra (per kanal) får text- och bakgrundsfärg vara innan texten
# räknas som osynlig (#fdfdfd på #fff; #111 på #000)
_SAME_COLOR = 16
# Vitt nog för att inte synas på en vit sida (per kanal)
_WHITE_MIN = 0xF0
# Positionering så här långt utanför skärmen (px) gömmer elementet
_OFFSCREEN = -1000
_IMPORTANT_RE = re.compile(r"!\s*important\s*$")


def _strip_comments(text: str) -> str:
    """Ta bort /* … */ (en oavslutad kommentar slukar resten). Linjär tid."""
    if "/*" not in text:
        return text
    out: list[str] = []
    pos = 0
    while (start := text.find("/*", pos)) != -1:
        out.append(text[pos:start])
        end = text.find("*/", start + 2)
        if end == -1:
            return "".join(out)
        pos = end + 2
    out.append(text[pos:])
    return "".join(out)


def _declarations(style: str) -> dict[str, tuple[str, bool]]:
    """CSS-deklarationer → {egenskap: (värde, !important)}, i gemener och
    med blanktecken hopslagna. Som i en webbläsare gäller den sista
    deklarationen, utom att !important vinner över en senare utan."""
    out: dict[str, tuple[str, bool]] = {}
    for decl in _strip_comments(style.lower()).split(";"):
        name, sep, value = decl.partition(":")
        name = name.strip()
        if not sep or not name:
            continue
        value, important = _IMPORTANT_RE.subn("", value)
        value = " ".join(value.split())
        if name not in out or important or not out[name][1]:
            out[name] = (value, bool(important))
    return out


def _inline(attrs) -> dict[str, tuple[str, bool]]:
    for name, value in attrs:
        if name == "style" and value:
            return _declarations(value)
    return {}


def _style(attrs, rules: "_Rules | None" = None, tag: str = "") -> dict[str, str]:
    """Elementets stil som {egenskap: värde}: style-attributet (se
    _declarations), och display/visibility ur enkla <style>-regler när
    style-attributet inte sätter dem (eller regeln är !important)."""
    inline = _inline(attrs)
    if rules:
        for prop, (value, important) in rules.match(tag, attrs).items():
            if prop not in inline or (important and not inline[prop][1]):
                inline[prop] = (value, important)
                inline[_FROM_RULE] = ("1", False)
    return {k: v for k, (v, _) in inline.items()}


# Markerar i stil-dicten att display/visibility kom från en <style>-regel
_FROM_RULE = " regel"
# Egenskaper som tas med ur <style>-regler
_RULE_PROPS = ("display", "visibility")
# Enkel väljare: element, .klass eller element.klass
_SIMPLE_SELECTOR_RE = re.compile(r"([a-z][a-z0-9]*)?(?:\.([a-z0-9_-]+))?$")
_BRACE_RE = re.compile(r"[{}]")


class _Rules:
    """Enkla regler ur <style>: element-, klass- och element.klass-väljare
    med display eller visibility, UTANFÖR @-block (@media gäller bara vissa
    skärmar och tolkas inte). Värden som (värde, !important), med
    specificitet: klass före element. Linjär tid."""

    def __init__(self) -> None:
        self.by_tag: dict[str, dict] = {}
        self.by_class: dict[str, dict] = {}
        self.by_tag_class: dict[tuple[str, str], dict] = {}

    def __bool__(self) -> bool:
        return bool(self.by_tag or self.by_class or self.by_tag_class)

    def add_stylesheet(self, css: str) -> None:
        css = _strip_comments(css.lower())
        pos, n = 0, len(css)
        while pos < n:
            m = _BRACE_RE.search(css, pos)
            if m is None:
                return
            if m.group() == "}":  # herrelös: hoppa över
                pos = m.end()
                continue
            prelude = css[pos:m.start()].rsplit(";", 1)[-1].strip()
            if prelude.startswith("@"):
                pos = self._skip_block(css, m.end())
                continue
            close = css.find("}", m.end())
            body = css[m.end(): close if close != -1 else n]
            pos = close + 1 if close != -1 else n
            decls = {p: v for p, v in _declarations(body).items() if p in _RULE_PROPS}
            if decls:
                for selector in prelude.split(","):
                    self._add(selector.strip(), decls)

    @staticmethod
    def _skip_block(css: str, pos: int) -> int:
        depth = 1
        while depth:
            m = _BRACE_RE.search(css, pos)
            if m is None:
                return len(css)
            depth += 1 if m.group() == "{" else -1
            pos = m.end()
        return pos

    def _add(self, selector: str, decls: dict) -> None:
        m = _SIMPLE_SELECTOR_RE.match(selector)
        if not selector or not m:
            return
        tag, cls = m.group(1), m.group(2)
        if tag and cls:
            target = self.by_tag_class.setdefault((tag, cls), {})
        elif cls:
            target = self.by_class.setdefault(cls, {})
        else:
            target = self.by_tag.setdefault(tag, {})
        for prop, value in decls.items():
            if prop not in target or value[1] or not target[prop][1]:
                target[prop] = value

    def match(self, tag: str, attrs) -> dict[str, tuple[str, bool]]:
        """display/visibility för elementet: !important först, sedan
        specificitet (element.klass och klass före element)."""
        found: dict[str, tuple[tuple[str, bool], tuple[bool, int]]] = {}

        def offer(decls: dict | None, specificity: int) -> None:
            for prop, value in (decls or {}).items():
                rank = (value[1], specificity)
                if prop not in found or rank >= found[prop][1]:
                    found[prop] = (value, rank)

        offer(self.by_tag.get(tag), 1)
        if self.by_class or self.by_tag_class:
            for name, value in attrs:
                if name == "class" and value:
                    for cls in value.lower().split():
                        offer(self.by_class.get(cls), 10)
                        offer(self.by_tag_class.get((tag, cls)), 11)
                    break
        return {prop: value for prop, (value, _) in found.items()}


def _compact(value: str) -> str:
    return value.replace(" ", "")


def _number(value: str) -> float | None:
    try:
        number = float(value.rstrip("%"))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _length_px(value: str) -> float | None:
    """En CSS-längd i px, eller None. Enhetslös bara för 0; % räknas inte."""
    m = _SIZE_RE.match(value)
    if not m or m.group(2) == "%":
        return None
    number, unit = float(m.group(1)), m.group(2)
    if unit is None:
        return 0.0 if number == 0 else None
    return number * _PX_PER[unit]


_TRANSPARENT = "transparent"
_UNKNOWN = "unknown"


def _color(value: str | None):
    """En CSS-färg som (r, g, b), _TRANSPARENT, eller None om okänd."""
    if not value:
        return None
    value = _compact(value)
    if value == _TRANSPARENT:
        return _TRANSPARENT
    if value in _NAMED_COLORS:
        return _NAMED_COLORS[value]
    if m := _HEX_RE.match(value):
        digits = m.group(1)
        if len(digits) <= 4:
            digits = "".join(ch * 2 for ch in digits)
        if len(digits) == 8 and digits[6:] == "00":
            return _TRANSPARENT
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    if m := _RGB_RE.match(value):
        parts = [p for p in re.split(r"[,/]", m.group(1)) if p]
        if len(parts) in (3, 4):
            try:
                channels = [
                    float(p[:-1]) * 2.55 if p.endswith("%") else float(p) for p in parts[:3]
                ]
                alpha = 1.0 if len(parts) == 3 else (
                    float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
                )
            except ValueError:
                return None
            if not all(math.isfinite(c) for c in (*channels, alpha)):
                return None  # nan/inf: ingen känd färg
            if alpha == 0:
                return _TRANSPARENT
            return tuple(max(0, min(255, round(c))) for c in channels)
    return None


def _background(value: str | None):
    """Bakgrund ur background/background-color: (r, g, b), _UNKNOWN (bild,
    gradient eller okänd färg) eller None (genomskinlig: förälderns syns)."""
    if value is None:
        return None
    if "url(" in value or "gradient(" in value or "image-set(" in value:
        return _UNKNOWN
    for token in value.split(" "):
        color = _color(token)
        if color == _TRANSPARENT:
            return None
        if color is not None:
            return color
        if token in ("none", "initial", "inherit", "unset"):
            return None
    return _UNKNOWN if value else None


def _font_zero(style: dict[str, str]) -> bool | None:
    """Är teckenstorleken osynligt liten (≤ 1px, ≤ 0.1em/rem, ≤ 10 %)?
    None om elementet inte sätter någon storlek."""
    size = style.get("font-size")
    if size is None and (font := style.get("font")):
        # font: [stil] [vikt] storlek[/radhöjd] familj — storleken har enhet (eller är 0)
        for token in font.split(" "):
            candidate = token.split("/", 1)[0]
            m = _SIZE_RE.match(candidate)
            if m and (m.group(2) or float(m.group(1)) == 0):
                size = candidate
                break
    if size is None or not (m := _SIZE_RE.match(size)):
        return None
    number, unit = float(m.group(1)), m.group(2)
    if unit in ("em", "rem"):
        return number <= 0.1
    if unit == "%":
        return number <= 10
    return number <= 1


def _offscreen(style: dict[str, str]) -> str | None:
    if style.get("position") in ("absolute", "fixed"):
        for prop in ("left", "top"):
            if (px := _length_px(style.get(prop, ""))) is not None and px <= _OFFSCREEN:
                return _("utanför skärmen")
        clip = _compact(style.get("clip", ""))
        if clip.startswith("rect(") and clip.endswith(")"):
            values = [v for v in re.split(r"[,\s]+", style["clip"][5:-1].strip()) if v]
            lengths = [_length_px(v) for v in values]
            if len(values) == 4 and all(px is not None and px <= 1 for px in lengths):
                return "clip"
    if (px := _length_px(style.get("text-indent", ""))) is not None and px <= _OFFSCREEN:
        return _("utanför skärmen")
    clip_path = _compact(style.get("clip-path", ""))
    if m := re.match(r"inset\(([0-9.]+)%", clip_path):
        if float(m.group(1)) >= 50:
            return "clip"
    if re.match(r"(?:circle|ellipse)\(0(?:px|%)?(?:[)a-z ]|0)", clip_path):
        return "clip"
    return None


def _hard_hidden(attrs, style: dict[str, str]) -> str | None:
    """Skälet till att elementet och allt i det är osynligt, som inget
    barnelement kan ändra på (display:none, opacity under 0.1, utanför
    skärmen …), eller None."""
    if any(name == "hidden" for name, _ in attrs):
        return "hidden"
    if not style:
        return None
    if style.get("display") == "none":
        return "<style>-regel" if _FROM_RULE in style and not _inline(attrs).get("display") else "display:none"
    if style.get("mso-hide") == "all":
        return "mso-hide:all"
    if (opacity := style.get("opacity")) is not None:
        number = _number(opacity)
        if number is not None and (number / 100 if opacity.endswith("%") else number) < 0.1:
            return "opacity:0"
    if style.get("overflow") == "hidden":
        if any(_length_px(style.get(prop, "x")) == 0 for prop in ("height", "max-height")):
            return "max-height:0"
        if any(_length_px(style.get(prop, "x")) == 0 for prop in ("width", "max-width")):
            return "width:0"
    return _offscreen(style)


def _soft_style(attrs, style: dict[str, str]) -> dict:
    """Ärvda egenskaper som ett barnelement kan ändra: font_zero,
    vis_hidden, color ((r, g, b) eller _TRANSPARENT) och bg (bakgrund, se
    _background). Bara de som elementet sätter finns med."""
    props: dict = {}
    if style:
        if (zero := _font_zero(style)) is not None:
            props["font_zero"] = zero
        if (visibility := style.get("visibility")) in ("hidden", "collapse", "visible"):
            props["vis_hidden"] = visibility != "visible"
        if (color := _color(style.get("color"))) is not None:
            props["color"] = color
        elif style.get("color") not in (None, "inherit", "currentcolor"):
            props["color"] = None  # okänd färg: kan inte bedömas
        background = style.get("background-color", style.get("background"))
        if background is not None and (bg := _background(background)) is not None:
            props["bg"] = bg
    for name, value in attrs:
        if name == "bgcolor" and value:
            props["bg"] = _color(value) or _UNKNOWN
        elif name == "color" and value and "color" not in props:  # <font color=…>
            props["color"] = _color(value)
    return props


def _close(a: tuple, b: tuple) -> bool:
    return all(abs(x - y) <= _SAME_COLOR for x, y in zip(a, b))


def _soft_reason(font_zero: bool, vis_hidden: bool, color, bg, color_over_bg: bool = True) -> str | None:
    """Varför ärvda stilar gör texten osynlig, eller None.

    color_over_bg: textfärgen sattes på samma element som bakgrunden eller
    längre in. En färg som ärvs från långt ovanför en bakgrund räknas inte —
    i nyhetsbrev färgas texten då nästan alltid av en klassregel som inte
    tolkas (mätt: annars falsklarm i 32 av 300 riktiga mejl)."""
    if font_zero:
        return "font-size:0"
    if vis_hidden:
        return "visibility:hidden"
    if color == _TRANSPARENT:
        return "color:transparent"
    if color_over_bg and isinstance(color, tuple) and isinstance(bg, tuple) and _close(color, bg):
        return _("färg=bakgrund")
    return None


def _is_white(color, bg) -> bool:
    """Vit text där bakgrunden inte är satt (syns inte på en vit sida)."""
    return isinstance(color, tuple) and bg is None and min(color) >= _WHITE_MIN


# Ärvt tillstånd för ett öppet element:
# (gömt för gott, font-size 0, visibility:hidden, textfärg, bakgrund, osynlig,
#  vit text, textfärgen satt på/innanför bakgrundens element)
_ROOT = (False, False, False, None, None, False, False, True)


def _visible_state(state: tuple) -> bool:
    return not state[5]


@dataclass
class HtmlText:
    """Resultatet av html_to_text_report.

    text: den synliga texten. hidden_text: text i gömda element (visas inte
    för läsaren och ingår inte i text). hidden_elements: antal gömda element
    (yttersta), hidden_reasons: antal per skäl. white_text: vit text utan
    bakgrundsfärg (syns inte på vit bakgrund men ingår i text).
    """

    text: str
    hidden_text: str = ""
    hidden_elements: int = 0
    hidden_reasons: dict[str, int] = field(default_factory=dict)
    white_text: str = ""
    unsafe: bool = False

# Fler citatnivåer än så visas som tio — annars kan djupt nästlade
# <blockquote> blåsa upp utdata kvadratiskt mot indata
_MAX_QUOTE_PREFIX = 10


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # entiteter avkodas i handle_data
        self.lines: list[tuple[int, list[str]]] = []  # (citatdjup, textfragment)
        self.open = False  # är lines[-1] en pågående rad?
        self.blank_before_next = False
        self.skip = 0
        self.in_head = False
        self.pre = 0
        self.quote = 0
        # Öppna element som (taggnamn, tillstånd); tillståndet är det ärvda
        # (se _State) och beräknas ur förälderns när elementet öppnas, så en
        # tagg kostar O(1) oavsett djup. stack_tags räknar taggnamn i stacken.
        self.stack: list[tuple[str, tuple]] = []
        self.stack_tags: dict[str, int] = {}
        self.state = _ROOT
        # Enkla regler ur <style> (se _Rules) och texten i pågående <style>
        self.rules = _Rules()
        self.css_parts: list[str] | None = None
        # Något gick inte att tolka säkert (se html_to_text_report)
        self.unsafe = False
        self.hidden_parts: list[str] = []
        self.white_parts: list[str] = []
        self.hidden_elements = 0
        self.hidden_reasons: dict[str, int] = {}

    # --- radhantering ---------------------------------------------------
    def _start_line(self) -> None:
        if self.blank_before_next and self.lines:
            depth = min(self.lines[-1][0], self.quote)
            self.lines.append((depth, []))
        self.blank_before_next = False
        self.lines.append((self.quote, []))
        self.open = True

    def _end_line(self) -> None:
        self.open = False

    def _paragraph(self) -> None:
        self._end_line()
        self.blank_before_next = True

    def _append(self, text: str) -> None:
        if not self.open:
            if not text.strip():
                return  # blanktecken mellan block öppnar ingen rad
            self._start_line()
            if not self.pre:
                text = text.lstrip()
        fragments = self.lines[-1][1]
        if not self.pre and text.startswith(" ") and fragments and fragments[-1].endswith(" "):
            text = text[1:]
        if text:
            fragments.append(text)  # lista + join: += på str blir kvadratiskt

    # --- gömt innehåll ----------------------------------------------------
    def _hide(self, reason: str) -> None:
        self.hidden_elements += 1
        self.hidden_reasons[reason] = self.hidden_reasons.get(reason, 0) + 1

    def _pop_to(self, index: int) -> None:
        """Stäng elementet på plats index och allt innanför det."""
        for tag, _ in self.stack[index:]:
            self.stack_tags[tag] -= 1
        del self.stack[index:]
        self.state = self.stack[-1][1] if self.stack else _ROOT

    def _close_tracked(self, tag: str) -> None:
        """Ett sluttagg: stäng närmaste öppna element med namnet och allt
        innanför (som en webbläsare gör med ostängda barn). Ett sluttagg
        utan öppet element ignoreras. Amorterat O(1): varje element stängs
        en gång."""
        if not self.stack_tags.get(tag):
            return
        index = len(self.stack) - 1
        while self.stack[index][0] != tag:
            index -= 1
        self._pop_to(index)

    def _close_implied(self, tag: str) -> None:
        """Starttaggar som stänger öppna element (<p> före ett block, <td>
        före nästa cell …), inom samma "scope" och högst _MAX_IMPLIED_SCAN
        element ned."""
        closes = _CLOSED_BY.get(tag)
        if not closes or not any(self.stack_tags.get(t) for t in closes):
            return
        target = None
        for index in range(len(self.stack) - 1, max(-1, len(self.stack) - 1 - _MAX_IMPLIED_SCAN), -1):
            open_tag = self.stack[index][0]
            if open_tag in closes:
                target = index
            elif open_tag in _SCOPE_BOUNDARY:
                break
        if target is not None:
            self._pop_to(target)

    def _track_start(self, tag: str, attrs) -> None:
        self._close_implied(tag)
        if tag in _VOID:
            return
        hard, font_zero, vis_hidden, color, bg, hidden, _, color_over_bg = self.state
        if not hard:
            try:
                style = _style(attrs, self.rules, tag)
                if reason := _hard_hidden(attrs, style):
                    hard = True
                elif props := _soft_style(attrs, style):
                    font_zero = props.get("font_zero", font_zero)
                    vis_hidden = props.get("vis_hidden", vis_hidden)
                    color = props.get("color", color)
                    if "bg" in props:
                        bg = props["bg"]
                        color_over_bg = "color" in props
                    elif "color" in props:
                        color_over_bg = True
                    reason = _soft_reason(font_zero, vis_hidden, color, bg, color_over_bg)
            except Exception:
                # Fail-closed: en stil som inte går att tolka gömmer elementet
                reason, hard = "otolkbar stil", True
                self.unsafe = True
            if reason and not hidden:  # räkna bara det yttersta gömda elementet
                self._hide(reason)
        now_hidden = hard or _soft_reason(font_zero, vis_hidden, color, bg, color_over_bg) is not None
        state = (hard, font_zero, vis_hidden, color, bg, now_hidden, _is_white(color, bg), color_over_bg)
        self.stack.append((tag, state))
        self.stack_tags[tag] = self.stack_tags.get(tag, 0) + 1
        self.state = state

    def _visible(self) -> bool:
        return _visible_state(self.state)

    # --- HTMLParser-hooks -------------------------------------------------
    def handle_startendtag(self, tag, attrs):
        # "/>" gäller bara tomma element (och SVG/MathML); <div/> är en
        # vanlig starttagg i HTML, som webbläsaren håller öppen
        self.handle_starttag(tag, attrs)
        if tag in _VOID:
            self.handle_endtag(tag)

    def handle_starttag(self, tag, attrs):
        self._track_start(tag, attrs)
        if tag == "head":
            self.in_head = True
        elif tag == "body" or tag not in _HEAD_TAGS:
            self.in_head = False  # även när </head> och <body> saknas
        if tag in _SKIP:
            self.skip += 1
            if tag == "style":
                self.css_parts = []
        if self.skip or self.in_head:
            return
        if tag == "br":
            if not self.open:
                self._start_line()
            self._end_line()
        elif tag in _PARAGRAPH:
            self._paragraph()
            if tag == "blockquote":
                self.quote += 1
            elif tag == "pre":
                self.pre += 1
        elif tag in _BLOCK:
            self._end_line()
            if tag == "li":
                self._append("- ")
        elif tag in ("td", "th") and self.open:
            self._append(" ")

    def handle_endtag(self, tag):
        self._close_tracked(tag)
        if tag == "head":
            self.in_head = False
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            if tag == "style" and self.css_parts is not None:
                try:
                    self.rules.add_stylesheet("".join(self.css_parts))
                except Exception:
                    self.unsafe = True  # reglerna kan vara ofullständiga: flagga
                self.css_parts = None
            return
        if self.skip or self.in_head:
            return
        if tag in _PARAGRAPH:
            if tag == "blockquote":
                self.quote = max(0, self.quote - 1)
            elif tag == "pre":
                self.pre = max(0, self.pre - 1)
            self._paragraph()
        elif tag in _BLOCK:
            self._end_line()

    def handle_data(self, data):
        if self.css_parts is not None:
            self.css_parts.append(data)
        if self.in_head and not self.skip and data.strip():
            self.in_head = False  # text stänger också ett ostängt head
        if self.skip or self.in_head:
            return
        if not self._visible():
            if data.strip():
                self.hidden_parts.append(data)
            return
        if self.state[6] and data.strip():
            self.white_parts.append(data)
        if self.pre:
            first, *rest = data.split("\n")
            self._append(first)
            for piece in rest:
                self._end_line()
                self._start_line()
                self._append(piece)
            return
        self._append(_WS_RE.sub(" ", data))

    # Kommentarer (inkl. <!--[if mso]>…<![endif]-->) och deklarationer ignoreras
    # av HTMLParser som standard.

    def text(self) -> str:
        rendered: list[str] = []
        for depth, fragments in self.lines:
            line = "".join(fragments)
            if line != "-- ":  # signaturavgränsaren (RFC 3676) behåller sitt blanksteg
                line = line.rstrip()
            prefix = "> " * min(depth, _MAX_QUOTE_PREFIX)
            rendered.append(prefix + line if line else prefix.rstrip())
        # Inga inledande/avslutande tomrader och högst en tomrad i följd
        out: list[str] = []
        for line in rendered:
            if not line.strip("> ") and (not out or not out[-1].strip("> ")):
                continue
            out.append(line)
        while out and not out[-1].strip("> "):
            out.pop()
        return "\n".join(out)


def _joined(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def html_to_text_report(markup: str) -> HtmlText:
    """Som html_to_text, men rapporterar också gömt innehåll och vit text.

    Gömt är element med display:none, visibility:hidden, teckenstorlek under
    det läsbara (≤ 1px/pt, ≤ 0.1em/rem, ≤ 10 %, även i font-förkortningen),
    opacity under 0.1, mso-hide:all, höjd eller bredd 0 med overflow:hidden,
    positionering långt utanför skärmen (left/top ≤ -1000px när elementet är
    absolut positionerat, text-indent ≤ -1000px), clip/clip-path som klipper
    bort allt, genomskinlig textfärg, textfärg (även <font color>) nästan lika
    med en känd bakgrundsfärg, och hidden-attributet. Gömd text ingår inte i
    text — läsaren ser den inte, och då ska inte heller modellen läsa den.

    Enkla regler i <style> (element, .klass, element.klass) med display:none
    eller visibility:hidden utanför @-block gäller också; regler i @media och
    sammansatta väljare tolkas inte.

    Stilen tolkas som i en webbläsare: sista deklarationen gäller (utom mot
    !important), CSS-kommentarer räknas inte, och öppna element följs i en
    stack så att förälderns sluttagg stänger allt innanför. Linjär tid.
    """
    if not markup:
        return HtmlText("")
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        # Fail-closed: bara den synliga text som redan tolkats; resten av
        # HTML:en (som kan vara gömd) tas inte med, och resultatet flaggas
        try:
            text = parser.text()
        except Exception:
            text = ""
        return HtmlText(
            text=text,
            hidden_elements=parser.hidden_elements,
            hidden_reasons=dict(parser.hidden_reasons),
            unsafe=True,
        )
    return HtmlText(
        text=parser.text(),
        hidden_text=_joined(parser.hidden_parts),
        hidden_elements=parser.hidden_elements,
        hidden_reasons=dict(parser.hidden_reasons),
        white_text=_joined(parser.white_parts),
        unsafe=parser.unsafe,
    )


def html_to_text(markup: str) -> str:
    """Gör om HTML till läsbar text: hoppar över style/script/head,
    kommentarer och gömda element, ger radbrytningar för blockelement,
    citerar blockquote med "> " och avkodar entiteter."""
    return html_to_text_report(markup).text
