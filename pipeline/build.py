"""Render dashboard.html: inline Plotly + embedded JSON payload into template.html."""
import json
from pathlib import Path
import plotly
from config import ROOT


def render(payload, out=ROOT / "dashboard.html"):
    tpl = (ROOT / "pipeline" / "template.html").read_text(encoding="utf-8")
    plotly_js = (Path(plotly.__file__).parent / "package_data" / "plotly.min.js").read_text(encoding="utf-8")
    # JSON is embedded in a <script>: neutralise "<" (</script>, <!--) and the JS line separators.
    data = (json.dumps(payload, separators=(",", ":"), allow_nan=False)
            .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    html = tpl.replace("/*__PLOTLY__*/", plotly_js.replace("</script", "<\\/script")).replace("/*__DATA__*/null", data)
    import base64
    uri = lambda f: "data:image/png;base64," + base64.b64encode((ROOT / "pipeline" / "assets" / f).read_bytes()).decode()
    html = html.replace("/*__LOGO__*/", uri("logo.png")).replace("/*__FAVICON__*/", uri("favicon.png"))
    out.write_text(html, encoding="utf-8")
    return out
