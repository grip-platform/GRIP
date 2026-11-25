"""Showcase HTML page renderer — brand-token substitution into a single template.

The template is intentionally inert HTML; brand-specific values live in the
project's `config.json`. Adding a new project = write a JSON, no code change.
"""
from __future__ import annotations

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>__FIRM_NAME__ &mdash; Showcase</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta property="og:title" content="__FIRM_NAME__ &mdash; Showcase">
  <meta property="og:description" content="__TAGLINE__">
  <meta property="og:type" content="video.other">
  <meta name="robots" content="noindex, nofollow">
  <style>
    *{box-sizing:border-box}
    body{margin:0;background:__INK_HEX__;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#1E293B;min-height:100vh}
    .wrap{max-width:1200px;margin:48px auto;background:__PAPER_HEX__;box-shadow:0 24px 64px rgba(0,0,0,0.35)}
    header{padding:36px 48px 0}
    h1{margin:0;color:__INK_HEX__;font-weight:700;letter-spacing:-1px;font-size:32px}
    .rule{width:72px;height:2px;background:__ACCENT_HEX__;margin:14px 0 22px}
    .meta{color:#475569;font-size:13px;letter-spacing:2.4px;text-transform:uppercase;margin:0 0 28px}
    video{width:100%;display:block;background:#000;outline:none}
    footer{padding:22px 48px 40px;color:#94A3B8;font-size:11px;letter-spacing:2px;text-transform:uppercase}
    @media (max-width:780px){header,footer{padding-left:24px;padding-right:24px}.wrap{margin:0}}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>__FIRM_NAME__</h1>
      <div class="rule"></div>
      <div class="meta">__TAGLINE__</div>
    </header>
    <video controls preload="metadata" id="v" playsinline>
      <source src="/v/__PROJECT__/__ID__/m.mp4" type="video/mp4">
    </video>
    <footer>__FOOTER__</footer>
  </div>
  <img src="/p/__PROJECT__/__ID__.png?t=__TS__" width="1" height="1" alt="" aria-hidden="true" style="position:absolute;left:-9999px;top:-9999px">
  <script>
    (function(){
      var v=document.getElementById('v'),proj="__PROJECT__",id="__ID__",last=-1;
      function track(e,t){
        try{
          var url='/e?p='+encodeURIComponent(proj)+'&id='+encodeURIComponent(id)+'&e='+encodeURIComponent(e)+'&t='+(t|0);
          if(navigator.sendBeacon){navigator.sendBeacon(url);}
          else{var x=new XMLHttpRequest();x.open('GET',url,true);x.send();}
        }catch(_){}
      }
      v.addEventListener('play',function(){track('play',v.currentTime|0);});
      v.addEventListener('pause',function(){track('pause',v.currentTime|0);});
      v.addEventListener('ended',function(){track('ended',v.currentTime|0);});
      setInterval(function(){
        if(v.paused)return;
        var t=v.currentTime|0;
        if(t!==last){last=t;track('progress',t);}
      },10000);
    })();
  </script>
</body>
</html>"""


def render_page(config: dict, project: str, recipient: str, ts: int = 0) -> str:
    """Substitute brand tokens into the page template.

    Falls back to sensible defaults if a brand key is missing.
    """
    brand = (config or {}).get("brand", {}) or {}
    repl = {
        "__FIRM_NAME__": brand.get("firm_name", project),
        "__TAGLINE__": brand.get("tagline", ""),
        "__FOOTER__": brand.get("footer", ""),
        "__ACCENT_HEX__": brand.get("accent_hex", "#C9A227"),
        "__INK_HEX__": brand.get("ink_hex", "#0F1B3D"),
        "__PAPER_HEX__": brand.get("paper_hex", "#FFFFFF"),
        "__PROJECT__": project,
        "__ID__": recipient,
        "__TS__": str(ts),
    }
    out = PAGE_TEMPLATE
    for k, v in repl.items():
        out = out.replace(k, v)
    return out
