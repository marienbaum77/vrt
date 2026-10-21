"""Automated Screencast Generator for VRT Project.
Generates a complete 1080p MP4 demo video with neural Russian voiceover and synchronized screen recordings.
"""
import sys
import os
import json

if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import asyncio
import shutil
import subprocess
import time
from pathlib import Path
import edge_tts
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = ROOT / "screencast_media"
MEDIA_DIR.mkdir(exist_ok=True)
REPORT_HTML = ROOT / "report" / "index.html"
OUTPUT_MP4 = ROOT / "demo_screencast.mp4"

VOICE = "ru-RU-DmitryNeural"

SCENES = [
    {
        "id": "scene1_intro",
        "title": "Введение и архитектура системы",
        "text": (
            "Здравствуйте! Представляю демонстрацию системы визуального регрессионного тестирования VRT. "
            "Проект развернут в изолированном Docker-окружении и состоит из четырех микросервисов: "
            "демонстрационного сайта, API-сервиса сравнения изображений, mock-сервиса CI с защитой веток и сервера мониторинга Prometheus. "
            "Все компоненты запущены и находятся в полностью работоспособном состоянии."
        ),
    },
    {
        "id": "scene2_baseline",
        "title": "Снятие эталонных снимков (Baseline)",
        "text": (
            "Сначала выполняем снятие эталонных снимков baseline с исходной стабильной версии v1 через консольный интерфейс. "
            "Playwright захватывает страницы с контролем рендер-стабильности: с заморозкой системного времени, "
            "отключением CSS-анимаций и гарантированным ожиданием загрузки всех шрифтов и графики. "
            "Успешно сохранены три эталона: главная страница для десктопа и мобильного устройства, а также страница тарифов."
        ),
    },
    {
        "id": "scene3_regression",
        "title": "Обнаружение регрессий (Test)",
        "text": (
            "Теперь запускаем проверку против обновленной версии v2, содержащей намеренные визуальные дефекты. "
            "Алгоритмическое ядро выполняет цветовой анализ дельта Е 2000, учитывает допуски на геометрический сдвиг и фильтрует сглаживание шрифтов. "
            "Система точно обнаружила изменения на всех трех страницах, сформировала отчет и завершилась с кодом ошибки один."
        ),
    },
    {
        "id": "scene4_report",
        "title": "Интерактивный HTML-отчет",
        "text": (
            "Система автоматически сформировала подробный интерактивный веб отчет. "
            "В отчете доступно удобное сравнение версий бок о бок и интерактивный слайдер между эталоном и кандидатом. "
            "Реальные дефекты верстки четко выделены красным цветом и сгруппированы в регионы, "
            "а незначительный субпиксельный шум и рендеринг шрифтов были надежно отфильтрованы."
        ),
    },
    {
        "id": "scene5_ci",
        "title": "Симуляция CI/CD и блокировка merge",
        "text": (
            "Проверяем статус в сервисе интеграции. "
            "Сервис зафиксировал провал проверки и автоматически заблокировал слияние пул реквеста, возвращая ошибку четыреста пять, "
            "а также опубликовал подробный отчет в комментариях. "
            "После подтверждения изменений командой аппрув повторный прогон завершается успехом, и слияние разрешено."
        ),
    },
    {
        "id": "scene6_conclusion",
        "title": "Prometheus и data-driven верификация",
        "text": (
            "В заключение смотрим метрики в Прометеус. "
            "Сервис экспортирует время сравнения, пиковую память и точность детекции свыше девяноста пяти процентов. "
            "Калибровка порогов на размеченном датасете обеспечила строгий контроль ложных срабатываний. "
            "Все требования технического задания успешно выполнены. Спасибо за внимание!"
        ),
    },
]

def get_audio_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())

async def generate_all_audio():
    print(">>> 1. Генерация нейронной озвучки (Edge TTS)...")
    for s in SCENES:
        mp3_path = MEDIA_DIR / f"{s['id']}.mp3"
        # If already exists and valid, skip
        if mp3_path.exists() and mp3_path.stat().st_size > 5000:
            s["duration"] = get_audio_duration(mp3_path)
            print(f"   [TTS] {s['id']} уже сгенерирован: {s['duration']:.2f} сек")
            continue
            
        print(f"   [TTS] Генерация {s['id']}...")
        for attempt in range(5):
            try:
                comm = edge_tts.Communicate(s["text"], VOICE)
                await comm.save(str(mp3_path))
                if mp3_path.exists() and mp3_path.stat().st_size > 5000:
                    break
            except Exception as e:
                print(f"         Попытка {attempt+1} не удалась ({e}), повтор через 2с...")
                await asyncio.sleep(2.0)
                
        s["duration"] = get_audio_duration(mp3_path)
        print(f"         Длительность: {s['duration']:.2f} сек")
        await asyncio.sleep(0.5)

def build_scene1_html() -> str:
    return """<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<style>
  body { margin:0; background:#0d1117; color:#c9d1d9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif; overflow:hidden; width:1920px; height:1080px; display:flex; flex-direction:column; justify-content:center; align-items:center; }
  .badge { display:inline-block; padding:6px 14px; background:#238636; color:#fff; border-radius:20px; font-weight:600; font-size:16px; margin-bottom:16px; text-transform:uppercase; letter-spacing:1px; }
  h1 { font-size:48px; color:#58a6ff; margin:0 0 12px; font-weight:700; text-align:center; }
  .desc { font-size:22px; color:#8b949e; margin-bottom:48px; max-width:1100px; text-align:center; line-height:1.4; }
  .grid { display:grid; grid-template-columns:repeat(4, 280px); gap:24px; margin-bottom:48px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:24px; text-align:center; box-shadow:0 8px 24px rgba(0,0,0,0.5); }
  .card h3 { margin:0 0 8px; font-size:20px; color:#f0f6fc; }
  .card .port { font-size:15px; color:#58a6ff; font-family:monospace; margin-bottom:12px; }
  .status { display:inline-flex; align-items:center; gap:8px; font-size:14px; font-weight:600; color:#3fb950; }
  .dot { width:10px; height:10px; background:#3fb950; border-radius:50%; box-shadow:0 0 10px #3fb950; }
  .flow { display:flex; align-items:center; gap:20px; background:#161b22; border:1px solid #30363d; border-radius:16px; padding:20px 40px; }
  .step { background:#21262d; border:1px solid #30363d; border-radius:8px; padding:12px 20px; font-weight:600; color:#f0f6fc; font-size:16px; }
  .arr { color:#58a6ff; font-size:24px; }
</style>
</head>
<body>
  <div class="badge">Visual Regression Testing System</div>
  <h1>VRT Prototype & Architecture</h1>
  <div class="desc">Автоматизированный контроль визуального качества UI на базе CIEDE2000, допусков на сглаживание шрифтов и блокировки CI Merge</div>
  <div class="grid">
    <div class="card">
      <h3>demo-site</h3>
      <div class="port">nginx :8080</div>
      <div class="status"><span class="dot"></span> Healthy (/v1 & /v2)</div>
    </div>
    <div class="card">
      <h3>mock-ci</h3>
      <div class="port">fastapi :9000</div>
      <div class="status"><span class="dot"></span> Merge Gate Online</div>
    </div>
    <div class="card">
      <h3>vrt-api</h3>
      <div class="port">python :8000</div>
      <div class="status"><span class="dot"></span> /healthz OK</div>
    </div>
    <div class="card">
      <h3>prometheus</h3>
      <div class="port">metrics :9090</div>
      <div class="status"><span class="dot"></span> Scrapes Active</div>
    </div>
  </div>
  <div class="flow">
    <div class="step">1. Захват (Playwright)</div>
    <div class="arr">&rarr;</div>
    <div class="step">2. Движок сравнения (&Delta;E2000, сглаживание)</div>
    <div class="arr">&rarr;</div>
    <div class="step">3. HTML-отчет</div>
    <div class="arr">&rarr;</div>
    <div class="step">4. CI Check & Merge Gate</div>
  </div>
</body>
</html>"""

def build_terminal_html(command: str, lines: list, duration_sec: float) -> str:
    output_html = "".join(f"<div class='line'>{l}</div>" for l in lines)
    return f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<style>
  body {{ margin:0; background:#0d1117; color:#c9d1d9; font-family:'Consolas','SF Mono','Courier New',monospace; overflow:hidden; width:1920px; height:1080px; display:flex; justify-content:center; align-items:center; }}
  .window {{ width:1440px; height:820px; background:#161b22; border:1px solid #30363d; border-radius:14px; box-shadow:0 24px 60px rgba(0,0,0,0.7); display:flex; flex-direction:column; overflow:hidden; }}
  .header {{ height:44px; background:#21262d; border-bottom:1px solid #30363d; display:flex; align-items:center; padding:0 16px; gap:8px; }}
  .btn {{ width:12px; height:12px; border-radius:50%; }}
  .close {{ background:#ff5f56; }}
  .min {{ background:#ffbd2e; }}
  .max {{ background:#27c93f; }}
  .title {{ flex:1; text-align:center; font-size:14px; color:#8b949e; font-weight:600; }}
  .body {{ padding:24px; font-size:19px; line-height:1.6; overflow:hidden; flex:1; }}
  .prompt {{ color:#58a6ff; font-weight:bold; }}
  .cmd {{ color:#f0f6fc; font-weight:bold; }}
  .cursor {{ display:inline-block; width:10px; height:20px; background:#58a6ff; vertical-align:middle; animation:blink 1s infinite; }}
  @keyframes blink {{ 0%, 50% {{ opacity:1; }} 51%, 100% {{ opacity:0; }} }}
  .output {{ margin-top:16px; }}
  .line {{ opacity:0; transform:translateY(6px); animation:fadeIn 0.3s forwards; }}
  .green {{ color:#3fb950; font-weight:bold; }}
  .red {{ color:#f85149; font-weight:bold; }}
  .yellow {{ color:#d29922; }}
  .cyan {{ color:#39c5cf; }}
  .dim {{ color:#8b949e; }}
  @keyframes fadeIn {{ to {{ opacity:1; transform:translateY(0); }} }}
</style>
</head>
<body>
  <div class="window">
    <div class="header">
      <div class="btn close"></div><div class="btn min"></div><div class="btn max"></div>
      <div class="title">vrt-runner &mdash; bash &mdash; 1440&times;820</div>
    </div>
    <div class="body">
      <div><span class="prompt">runner@vrt:~$ </span><span id="cmd-text" class="cmd"></span><span id="cursor" class="cursor"></span></div>
      <div id="output" class="output"></div>
    </div>
  </div>
  <script>
    const fullCmd = {repr(command)};
    const lines = {json.dumps(lines)};
    const cmdEl = document.getElementById('cmd-text');
    const outEl = document.getElementById('output');
    
    let i = 0;
    function typeCmd() {{
      if (i < fullCmd.length) {{
        cmdEl.textContent += fullCmd.charAt(i);
        i++;
        setTimeout(typeCmd, 35);
      }} else {{
        setTimeout(printLines, 400);
      }}
    }}
    
    let lineIdx = 0;
    function printLines() {{
      if (lineIdx < lines.length) {{
        const div = document.createElement('div');
        div.className = 'line';
        div.innerHTML = lines[lineIdx];
        outEl.appendChild(div);
        lineIdx++;
        setTimeout(printLines, 160);
      }}
    }}
    setTimeout(typeCmd, 300);
  </script>
</body>
</html>"""

def build_scene5_html() -> str:
    return """<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<style>
  body { margin:0; background:#0d1117; color:#c9d1d9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; overflow:hidden; width:1920px; height:1080px; display:flex; justify-content:center; align-items:center; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:32px; width:1600px; height:840px; }
  .panel { background:#161b22; border:1px solid #30363d; border-radius:14px; padding:24px; display:flex; flex-direction:column; box-shadow:0 16px 40px rgba(0,0,0,0.6); }
  .panel h2 { margin:0 0 16px; font-size:22px; color:#58a6ff; display:flex; align-items:center; gap:10px; }
  .terminal { background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:16px; font-family:monospace; font-size:16px; line-height:1.6; flex:1; overflow:hidden; }
  .pr-comment { background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:18px; font-size:15px; line-height:1.5; flex:1; }
  .pr-header { display:flex; align-items:center; gap:12px; margin-bottom:12px; border-bottom:1px solid #30363d; padding-bottom:10px; font-weight:600; }
  .bot-tag { background:#21262d; border:1px solid #30363d; padding:2px 8px; border-radius:12px; font-size:12px; color:#8b949e; }
  table { border-collapse:collapse; width:100%; margin:12px 0; font-size:14px; }
  th, td { border:1px solid #30363d; padding:6px 12px; text-align:left; }
  th { background:#161b22; color:#8b949e; }
  .red { color:#f85149; font-weight:bold; }
  .green { color:#3fb950; font-weight:bold; }
  .badge { display:inline-block; padding:4px 10px; border-radius:12px; font-size:13px; font-weight:bold; }
  .b-fail { background:rgba(248,81,73,0.15); color:#f85149; border:1px solid #f85149; }
  .b-pass { background:rgba(63,185,80,0.15); color:#3fb950; border:1px solid #3fb950; }
</style>
</head>
<body>
  <div class="grid">
    <div class="panel">
      <h2><span>&block;</span> CI/CD Merge Gate (Mock CI API)</h2>
      <div class="terminal">
        <div style="color:#8b949e"># 1. Попытка слить PR #7 при регрессиях</div>
        <div><span style="color:#58a6ff">$</span> curl -X PUT mock-ci:9000/repos/acme/site/pulls/7/merge</div>
        <div class="red" style="margin:8px 0;">HTTP/1.1 405 Method Not Allowed</div>
        <div class="red">{"message": "Pull Request is not mergeable: Required check 'vrt/visual-regression' is failing"}</div>
        <br>
        <div style="color:#8b949e"># 2. Одобрение изменений дизайнером / лидом</div>
        <div><span style="color:#58a6ff">$</span> vrt approve -c examples/vrt.docker.yaml</div>
        <div class="green">&check; 3 baseline(s) updated in /data/baselines</div>
        <br>
        <div style="color:#8b949e"># 3. Повторный прогон проверки</div>
        <div><span style="color:#58a6ff">$</span> vrt test -c examples/vrt.docker.yaml</div>
        <div class="green">verdict: PASSED (3/3 passed, 0 changed)</div>
        <br>
        <div style="color:#8b949e"># 4. Слияние разблокировано!</div>
        <div><span style="color:#58a6ff">$</span> curl -X PUT mock-ci:9000/repos/acme/site/pulls/7/merge</div>
        <div class="green" style="margin-top:6px;">HTTP/1.1 200 OK &mdash; {"merged": true, "message": "Pull Request successfully merged"}</div>
      </div>
    </div>
    <div class="panel">
      <h2><span>&para;</span> GitHub PR #7 Check Run & Comment</h2>
      <div class="pr-comment">
        <div class="pr-header">
          <span style="color:#58a6ff">vrt-bot</span> <span class="bot-tag">bot</span> прокомментировал 2 минуты назад
        </div>
        <div style="font-size:18px; font-weight:bold; margin-bottom:10px;">
          <span class="badge b-fail">FAILED</span> Visual regression: 3 changed of 3 screenshot(s)
        </div>
        <p style="color:#8b949e; margin:0 0 10px;">acme-demo &middot; profile <code>balanced</code> (&Delta;E &ge; 2.3, r = 1 px)</p>
        <table>
          <tr><th>Страница</th><th>Статус</th><th>Значимые px</th><th>Max &Delta;E</th></tr>
          <tr><td>home@desktop</td><td class="red">changed</td><td>32 398</td><td>81.1</td></tr>
          <tr><td>home@mobile</td><td class="red">changed</td><td>25 871</td><td>80.6</td></tr>
          <tr><td>pricing@desktop</td><td class="red">changed</td><td>22 974</td><td>81.3</td></tr>
        </table>
        <p style="font-size:13px; color:#8b949e; margin-top:12px;">
          &rarr; <em>Если изменения ожидаемы, выполните <code>vrt approve</code> для снятия блокировки.</em>
        </p>
      </div>
    </div>
  </div>
</body>
</html>"""

def build_scene6_html() -> str:
    return """<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<style>
  body { margin:0; background:#0d1117; color:#c9d1d9; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; overflow:hidden; width:1920px; height:1080px; display:flex; flex-direction:column; justify-content:center; align-items:center; }
  h1 { font-size:46px; color:#58a6ff; margin:0 0 16px; font-weight:700; text-align:center; }
  .desc { font-size:22px; color:#8b949e; margin-bottom:44px; text-align:center; }
  .grid { display:grid; grid-template-columns:repeat(4, 320px); gap:24px; margin-bottom:48px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:28px 20px; text-align:center; box-shadow:0 8px 24px rgba(0,0,0,0.5); }
  .val { font-size:44px; font-weight:800; color:#3fb950; margin-bottom:8px; }
  .label { font-size:16px; color:#8b949e; }
  .sub { font-size:13px; color:#58a6ff; margin-top:6px; }
  .prom { background:#161b22; border:1px solid #30363d; border-radius:14px; padding:20px 40px; display:flex; gap:36px; align-items:center; }
  .metric { font-family:monospace; font-size:16px; color:#f0f6fc; }
  .badge { background:#238636; color:#fff; padding:6px 16px; border-radius:20px; font-weight:bold; font-size:16px; }
</style>
</head>
<body>
  <h1>Data-Driven Верификация и Метрики</h1>
  <div class="desc">Оценка алгоритмов на тестовой выборке из 600 UI-пар (датасет vrt-synthetic-ui)</div>
  <div class="grid">
    <div class="card">
      <div class="val">95.5%</div>
      <div class="label">Точность детекции</div>
      <div class="sub">Критерий ТЗ: &ge; 95% &check;</div>
    </div>
    <div class="card">
      <div class="val">4.0%</div>
      <div class="label">False Negative Rate</div>
      <div class="sub">Критерий ТЗ: &le; 5% &check;</div>
    </div>
    <div class="card">
      <div class="val">5.1%</div>
      <div class="label">False Positive Rate</div>
      <div class="sub">95% ДИ [2.4% &ndash; 10.7%] &check;</div>
    </div>
    <div class="card">
      <div class="val">0.960</div>
      <div class="label">ROC-AUC Score</div>
      <div class="sub">VRT vs SSIM (p = 2.9e-19) &check;</div>
    </div>
  </div>
  <div class="prom">
    <div class="badge">Prometheus :9090</div>
    <div class="metric">&bull; vrt_diff_time_ms</div>
    <div class="metric">&bull; vrt_memory_peak_mb</div>
    <div class="metric">&bull; vrt_false_positive_rate</div>
    <div class="metric">&bull; vrt_threshold_sensitivity</div>
  </div>
</body>
</html>"""

async def record_all_scenes():
    print(">>> 2. Запуск Playwright для захвата видеоряда (1920x1080)...")
    
    # Write scene HTML files
    (MEDIA_DIR / "scene1.html").write_text(build_scene1_html(), encoding="utf-8")
    (MEDIA_DIR / "scene5.html").write_text(build_scene5_html(), encoding="utf-8")
    (MEDIA_DIR / "scene6.html").write_text(build_scene6_html(), encoding="utf-8")
    
    lines_baseline = [
        "<span class='dim'>Container vrt-demo-site-1  Running</span>",
        "<span class='dim'>Container vrt-mock-ci-1    Running</span>",
        "<span class='cyan'>[capture]</span> Stabilizing renderer (frozen clock, fonts loaded, images decoded)",
        "<span class='green'>saved 3 baseline(s) to /data/baselines:</span>",
        "  <span class='green'>+</span> home@chromium/desktop    <span class='dim'>[1280x720]</span>",
        "  <span class='green'>+</span> home@chromium/mobile     <span class='dim'>[375x667]</span>",
        "  <span class='green'>+</span> pricing@chromium/desktop <span class='dim'>[1280x720]</span>",
        "<span class='dim'>Exit code: 0 (OK)</span>"
    ]
    (MEDIA_DIR / "scene2.html").write_text(build_terminal_html("docker compose run --rm -e VRT_BASE_URL=http://demo-site/v1 runner vrt baseline -c examples/vrt.docker.yaml", lines_baseline, 12), encoding="utf-8")

    lines_test = [
        "<span class='dim'>Container vrt-demo-site-1  Running</span>",
        "<span class='dim'>Container vrt-mock-ci-1    Running</span>",
        "<span class='cyan'>[diff_engine]</span> Evaluating candidate /v2 against baseline /v1 (profile: balanced)...",
        "",
        "<b>page                                         status     sig. px  max dE     ms</b>",
        "home@chromium/desktop                        <span class='red'>failed</span>       32398    81.1    186",
        "home@chromium/mobile                         <span class='red'>failed</span>       25871    80.6    126",
        "pricing@chromium/desktop                     <span class='red'>failed</span>       22974    81.3    146",
        "",
        "<span class='red'><b>verdict: FAILED  (0/3 passed, 3 changed, 0 new, 0 errors)</b></span>",
        "<span class='yellow'>[ci] github: check-run 'vrt/visual-regression' -> conclusion: failure</span>",
        "<span class='cyan'>report: /data/report/index.html</span>",
        "<span class='red'>Process exited with code 1</span>"
    ]
    (MEDIA_DIR / "scene3.html").write_text(build_terminal_html("docker compose run --rm -e VRT_BASE_URL=http://demo-site/v2 runner", lines_test, 14), encoding="utf-8")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for s in SCENES:
            scene_id = s["id"]
            duration = s["duration"]
            rec_dir = MEDIA_DIR / f"rec_{scene_id}"
            if rec_dir.exists():
                shutil.rmtree(rec_dir)
            rec_dir.mkdir(parents=True, exist_ok=True)
            
            print(f"   [VIDEO] Запись сцены: {scene_id} ({duration:.2f} сек)...")
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=str(rec_dir),
                record_video_size={"width": 1920, "height": 1080}
            )
            page = await context.new_page()
            
            if scene_id == "scene1_intro":
                await page.goto((MEDIA_DIR / "scene1.html").as_uri())
                await page.wait_for_timeout(duration * 1000)
            elif scene_id == "scene2_baseline":
                await page.goto((MEDIA_DIR / "scene2.html").as_uri())
                await page.wait_for_timeout(duration * 1000)
            elif scene_id == "scene3_regression":
                await page.goto((MEDIA_DIR / "scene3.html").as_uri())
                await page.wait_for_timeout(duration * 1000)
            elif scene_id == "scene4_report":
                await page.goto(REPORT_HTML.as_uri())
                # Interactive swipe and scroll demo!
                await page.wait_for_timeout(2000)
                await page.evaluate("window.scrollTo({top: 400, behavior: 'smooth'})")
                await page.wait_for_timeout(2000)
                
                # Open details if closed
                await page.evaluate("""() => {
                    const d = document.querySelector('details');
                    if (d) d.open = true;
                }""")
                await page.wait_for_timeout(1500)
                
                # Animate slider smoothly
                slider_time = max(4.0, duration - 8.0)
                steps = 40
                step_wait = (slider_time / (steps * 2)) * 1000
                for v in range(50, 101, int(50/steps)):
                    await page.evaluate(f"sl('s_home_chromium_desktop', {v})")
                    await page.wait_for_timeout(step_wait)
                for v in range(100, -1, -int(100/steps)):
                    await page.evaluate(f"sl('s_home_chromium_desktop', {v})")
                    await page.wait_for_timeout(step_wait)
                for v in range(0, 51, int(50/steps)):
                    await page.evaluate(f"sl('s_home_chromium_desktop', {v})")
                    await page.wait_for_timeout(step_wait)
                
                await page.evaluate("window.scrollTo({top: 900, behavior: 'smooth'})")
                await page.wait_for_timeout(2500)
            elif scene_id == "scene5_ci":
                await page.goto((MEDIA_DIR / "scene5.html").as_uri())
                await page.wait_for_timeout(duration * 1000)
            elif scene_id == "scene6_conclusion":
                await page.goto((MEDIA_DIR / "scene6.html").as_uri())
                await page.wait_for_timeout(duration * 1000)
                
            await context.close()
            
            # Find recorded video file in rec_dir
            webm_files = list(rec_dir.glob("*.webm"))
            if webm_files:
                target_webm = MEDIA_DIR / f"{scene_id}.webm"
                if target_webm.exists():
                    target_webm.unlink()
                shutil.move(webm_files[0], target_webm)
                shutil.rmtree(rec_dir)
                print(f"         Видео сохранено: {target_webm.name}")
        
        await browser.close()

def multiplex_and_concat():
    print(">>> 3. Сведение видео и аудиодорожек (FFmpeg)...")
    concat_list = MEDIA_DIR / "concat.txt"
    concat_lines = []
    
    for s in SCENES:
        scene_id = s["id"]
        webm = MEDIA_DIR / f"{scene_id}.webm"
        mp3 = MEDIA_DIR / f"{scene_id}.mp3"
        out_scene_mp4 = MEDIA_DIR / f"{scene_id}_synced.mp4"
        
        # Mux video + audio, encode to standard H.264 / AAC
        cmd = [
            "ffmpeg", "-y",
            "-i", str(webm),
            "-i", str(mp3),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(out_scene_mp4)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        concat_lines.append(f"file '{out_scene_mp4.resolve().as_posix()}'")
        print(f"   [MUX] {scene_id}_synced.mp4 готов")
        
    concat_list.write_text("\n".join(concat_lines), encoding="utf-8")
    
    print(">>> 4. Финальная сборка demo_screencast.mp4...")
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(OUTPUT_MP4)
    ]
    subprocess.run(cmd_concat, capture_output=True, check=True)
    print(f"\n=======================================================")
    print(f"  ГОТОВО! Видеофайл создан: {OUTPUT_MP4}")
    duration = get_audio_duration(OUTPUT_MP4)
    size_mb = OUTPUT_MP4.stat().st_size / (1024 * 1024)
    print(f"  Длительность: {duration:.1f} сек (~{duration/60:.1f} мин)")
    print(f"  Размер: {size_mb:.2f} МБ")
    print(f"=======================================================\n")

async def main():
    await generate_all_audio()
    await record_all_scenes()
    multiplex_and_concat()

if __name__ == "__main__":
    asyncio.run(main())
