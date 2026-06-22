import os
import re
import sys
import urllib.parse
import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv()

# Map common language abbreviations to full names expected by Ray.so
LANG_MAP = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "rb": "ruby",
    "pl": "perl",
    "sh": "bash",
    "rs": "rust",
    "cs": "csharp",
    "cpp": "c++",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "md": "markdown",
    "sql": "sql",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "swift": "swift"
}

def create_code_card(code_text, lang, output_path):
    """
    Ray.so를 이용하여 코드 텍스트를 우아한 이미지 카드로 캡처합니다.
    URL 파라미터 길이 제한을 피하기 위해 Playwright로 에디터 영역에 직접 타이핑(입력)합니다.
    """
    print(f"Creating code card: {output_path}...")
    normalized_lang = LANG_MAP.get(lang.lower(), lang.lower())
    
    # URL hash parameters (theme: carbon, background: true, padding: 32)
    url = f"https://ray.so/#theme=carbon&background=true&darkMode=true&padding=32&language={normalized_lang}"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 800})
        page.goto(url)
        # 에디터 로딩 대기
        page.wait_for_timeout(2000)
        
        # 에디터의 textarea 타겟팅 및 포커스
        textarea = page.locator("textarea").first
        textarea.focus()
        
        # 기존 텍스트 전체 선택 후 삭제
        page.keyboard.press("Meta+A")
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        
        # 코드 내용 채우기
        textarea.fill(code_text)
        
        # 구문 강조 및 렌더링 완료 대기
        page.wait_for_timeout(1000)
        
        # Ray.so의 실제 카드 프레임(#frame) 영역만 캡처
        card_element = page.locator("#frame")
        if card_element.is_visible():
            card_element.screenshot(path=output_path)
        else:
            page.screenshot(path=output_path)
        browser.close()

def draw_fred_chart(series_id, output_path):
    """
    St. Louis Fed의 FRED 데이터를 실시간 다운로드하여
    블로그 톤앤매너에 어울리는 Sleek Dark Mode 차트를 그립니다.
    """
    print(f"Drawing FRED chart for {series_id} -> {output_path}...")
    # FRED 공개 CSV 다운로드 URL
    csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        df = pd.read_csv(csv_url, parse_dates=['observation_date'], index_col='observation_date')
        # 데이터가 수치형이 아닌 경우(결측치 '.' 등) 제거
        df = df[df[series_id] != '.']
        df[series_id] = pd.to_numeric(df[series_id])
        
        # 다크모드 차트 그리기
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
        
        ax.plot(df.index, df[series_id], color='#00ffcc', linewidth=2, label=series_id)
        ax.fill_between(df.index, df[series_id], color='#00ffcc', alpha=0.1)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#444444')
        ax.spines['bottom'].set_color('#444444')
        
        ax.tick_params(colors='#888888')
        ax.grid(True, linestyle='--', color='#222222')
        ax.set_title(f"FRED Indicator: {series_id}", fontsize=14, color='#ffffff', pad=15)
        
        plt.tight_layout()
        plt.savefig(output_path, facecolor='#131722', edgecolor='none')
        plt.close()
        print(f"FRED chart saved to {output_path}")
    except Exception as e:
        print(f"Failed to fetch or draw FRED chart for {series_id}: {e}")

def capture_tradingview_chart(symbol, output_path):
    """
    TradingView 위젯을 담은 임시 HTML을 빌드하여 Playwright로 실시간 캔들스틱 차트를 캡처합니다.
    """
    print(f"Capturing TradingView chart for {symbol} -> {output_path}...")
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        body, html {{ margin: 0; padding: 0; width: 800px; height: 500px; overflow: hidden; background-color: #131722; }}
        #widget-container {{ width: 100%; height: 100%; }}
      </style>
    </head>
    <body>
      <div id="widget-container"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "width": 800,
          "height": 500,
          "symbol": "{symbol}",
          "interval": "D",
          "timezone": "Etc/UTC",
          "theme": "dark",
          "style": "1",
          "locale": "en",
          "toolbar_bg": "#f1f3f6",
          "enable_publishing": false,
          "hide_side_toolbar": true,
          "allow_symbol_change": false,
          "container_id": "widget-container"
        }});
      </script>
    </body>
    </html>
    """
    temp_html_path = "temp_widget.html"
    with open(temp_html_path, "w", encoding="utf-8") as f:
        f.write(html_template)
        
    abs_html_path = "file://" + os.path.abspath(temp_html_path)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 500})
        page.goto(abs_html_path)
        # 차트 로딩 및 캔들 렌더링 대기
        page.wait_for_timeout(5000)
        page.screenshot(path=output_path)
        browser.close()
        
    if os.path.exists(temp_html_path):
        os.remove(temp_html_path)
    print(f"TradingView chart saved to {output_path}")

def download_unsplash_image(keyword, output_path):
    """
    Unsplash Developer API를 사용하여 키워드 매핑 고화질 사진을 동적으로 다운로드합니다.
    API Key가 없는 경우 백업 주소에서 다운로드합니다.
    """
    print(f"Downloading Unsplash image for keyword '{keyword}' -> {output_path}...")
    
    # 글로벌 스킬 blog_image_unsplash 폴더 하위의 전용 .env 로드 시도
    global_env = "/Users/jw/.gemini/skills/blog_image_unsplash/.env"
    if os.path.exists(global_env):
        load_dotenv(dotenv_path=global_env, override=True)
        
    access_key = os.getenv("UNSPLASH_ACCESS_KEY")
    
    if access_key:
        url = "https://api.unsplash.com/search/photos"
        headers = {
            "Authorization": f"Client-ID {access_key}"
        }
        params = {
            "query": keyword,
            "per_page": 1
        }
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                if results:
                    img_url = results[0].get("urls", {}).get("regular")
                    if img_url:
                        # w=800 파라미터를 추가하여 블로그에 최적화된 크기로 다운로드
                        download_url = img_url + "&w=800" if "?" in img_url else img_url + "?w=800"
                        res = requests.get(download_url, timeout=10)
                        if res.status_code == 200:
                            with open(output_path, "wb") as f:
                                f.write(res.content)
                            print(f"Unsplash image downloaded successfully using API: {output_path}")
                            return
                        else:
                            print(f"Failed to download image from API URL: status {res.status_code}")
                else:
                    print(f"No image results found for query '{keyword}'")
            else:
                print(f"Unsplash API returned error: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Unsplash API call failed: {e}")

    # Fallback (API 키가 없거나 실패한 경우)
    print("Using static fallback image...")
    url = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=800" # 금융 기본 백업
    if any(k in keyword.lower() for k in ["code", "program", "develop"]):
        url = "https://images.unsplash.com/photo-1542831371-29b0f74f9713?w=800" # 개발 기본 백업
        
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            print(f"Fallback Unsplash image saved: {output_path}")
    except Exception as e:
        print(f"Failed to download fallback unsplash image: {e}")

def create_html_card(yaml_text, output_path):
    """
    YAML로 정의된 콘텐츠 데이터를 미려한 HTML/CSS 템플릿에 주입하고 
    Playwright로 렌더링하여 고화질 인포그래픽 이미지(비교표, 인용구, 단계, 지표 등)로 저장합니다.
    """
    import yaml
    try:
        data = yaml.safe_load(yaml_text)
    except Exception as e:
        print(f"Failed to parse YAML for HTML card: {e}")
        return
        
    card_type = data.get("type", "compare")
    theme = data.get("theme", "dark").lower()
    print(f"Creating HTML card ({card_type}) with theme ({theme}): {output_path}...")
    
    # 공통 CSS 정의 (Modern UI/Aesthetics)
    css_base = f"""
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:ital,wght@1,600&family=Fira+Code:wght@400;500&display=swap');
    
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: 'Inter', -apple-system, sans-serif;
        background: {'linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%)' if theme == 'light' else 'linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%)'};
        display: flex;
        align-items: center;
        justify-content: center;
        width: 800px;
        height: 500px;
        overflow: hidden;
        color: {'#1e293b' if theme == 'light' else '#f1f5f9'};
        padding: 40px;
    }}
    .card-container {{
        width: 100%;
        height: 100%;
        background: {'#ffffff' if theme == 'light' else 'rgba(30, 41, 59, 0.7)'};
        border: 1px solid {'rgba(0, 0, 0, 0.08)' if theme == 'light' else 'rgba(255, 255, 255, 0.1)'};
        border-radius: 20px;
        padding: 32px;
        box-shadow: {'0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -4px rgba(0, 0, 0, 0.05)' if theme == 'light' else '0 20px 25px -5px rgba(0, 0, 0, 0.3), 0 10px 10px -5px rgba(0, 0, 0, 0.2)'};
        backdrop-filter: blur(12px);
        display: flex;
        flex-direction: column;
        justify-content: center;
        position: relative;
    }}
    .card-glow {{
        position: absolute;
        top: -10%;
        left: -10%;
        width: 120%;
        height: 120%;
        background: radial-gradient(circle, {'rgba(99, 102, 241, 0.06)' if theme == 'light' else 'rgba(99, 102, 241, 0.15)'} 0%, rgba(0,0,0,0) 70%);
        z-index: -1;
        pointer-events: none;
    }}
    """
    
    html_content = ""
    
    if card_type == "compare":
        title = data.get("title", "Comparison")
        headers = data.get("headers", ["Feature", "Option A", "Option B"])
        rows = data.get("rows", [])
        
        headers_html = "".join([f"<th>{h}</th>" for h in headers])
        rows_html = ""
        for r in rows:
            cols = "".join([f"<td>{c}</td>" for c in r])
            rows_html += f"<tr>{cols}</tr>"
            
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>{headers_html}</tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 24px;
                font-weight: 700;
                margin-bottom: 24px;
                background: {'linear-gradient(to right, #0284c7, #4f46e5)' if theme == 'light' else 'linear-gradient(to right, #38bdf8, #818cf8)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .table-wrapper {{ width: 100%; overflow-x: auto; }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th {{
                padding: 12px 16px;
                font-size: 14px;
                font-weight: 600;
                color: {'#475569' if theme == 'light' else '#94a3b8'};
                border-bottom: 2px solid {'rgba(0, 0, 0, 0.08)' if theme == 'light' else 'rgba(255, 255, 255, 0.1)'};
            }}
            td {{
                padding: 14px 16px;
                font-size: 14px;
                color: {'#334155' if theme == 'light' else '#e2e8f0'};
                border-bottom: 1px solid {'rgba(0, 0, 0, 0.05)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
            }}
            tr:hover td {{ background: {'rgba(0, 0, 0, 0.01)' if theme == 'light' else 'rgba(255, 255, 255, 0.02)'}; }}
            td:first-child {{ font-weight: 500; color: {'#0284c7' if theme == 'light' else '#38bdf8'}; }}
        </style>
        """
        
    elif card_type == "quote":
        text = data.get("text", "")
        author = data.get("author", "")
        author_html = f'<div class="author">— {author}</div>' if author else ""
        
        html_content = f"""
        <div class="card-container quote-card">
            <div class="card-glow"></div>
            <div class="quote-mark">“</div>
            <p class="quote-text">{text}</p>
            {author_html}
            <div class="quote-mark-bottom">”</div>
        </div>
        <style>
            {css_base}
            .quote-card {{ padding: 48px; align-items: center; text-align: center; }}
            .quote-mark {{
                font-family: 'Playfair Display', serif;
                font-size: 80px;
                line-height: 1;
                color: {'rgba(99, 102, 241, 0.15)' if theme == 'light' else 'rgba(99, 102, 241, 0.3)'};
                margin-bottom: -10px;
            }}
            .quote-mark-bottom {{
                font-family: 'Playfair Display', serif;
                font-size: 80px;
                line-height: 1;
                color: {'rgba(99, 102, 241, 0.15)' if theme == 'light' else 'rgba(99, 102, 241, 0.3)'};
                margin-top: 10px;
                align-self: flex-end;
            }}
            .quote-text {{
                font-family: 'Playfair Display', serif;
                font-size: 24px;
                font-style: italic;
                line-height: 1.6;
                color: {'#0f172a' if theme == 'light' else '#f8fafc'};
                z-index: 1;
            }}
            .author {{
                margin-top: 16px;
                font-size: 15px;
                font-weight: 500;
                color: {'#4f46e5' if theme == 'light' else '#a5b4fc'};
                z-index: 1;
            }}
        </style>
        """
        
    elif card_type == "steps":
        title = data.get("title", "Workflow Process")
        steps = data.get("steps", [])
        
        steps_html = ""
        for idx, step in enumerate(steps):
            steps_html += f"""
            <div class="step-item">
                <div class="step-badge">{idx + 1}</div>
                <div class="step-text">{step}</div>
            </div>
            """
            if idx < len(steps) - 1:
                steps_html += f'<div class="step-arrow">→</div>'
                
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="steps-flow">
                {steps_html}
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 32px;
                background: {'linear-gradient(to right, #7c3aed, #db2777)' if theme == 'light' else 'linear-gradient(to right, #a78bfa, #f472b6)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .steps-flow {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 16px;
                width: 100%;
            }}
            .step-item {{
                flex: 1;
                background: {'rgba(0, 0, 0, 0.02)' if theme == 'light' else 'rgba(255, 255, 255, 0.03)'};
                border: 1px solid {'rgba(0, 0, 0, 0.05)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
                border-radius: 16px;
                padding: 20px 16px;
                text-align: center;
                display: flex;
                flex-direction: column;
                align-items: center;
                min-height: 180px;
                justify-content: center;
                box-shadow: {'none' if theme == 'light' else 'inset 0 1px 1px 0 rgba(255, 255, 255, 0.05)'};
            }}
            .step-badge {{
                width: 36px;
                height: 36px;
                border-radius: 50%;
                background: linear-gradient(135deg, #8b5cf6 0%, #d946ef 100%);
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: 700;
                font-size: 16px;
                color: #ffffff;
                margin-bottom: 16px;
                box-shadow: 0 4px 6px -1px rgba(139, 92, 246, 0.3);
            }}
            .step-text {{ font-size: 14px; line-height: 1.5; font-weight: 500; color: {'#334155' if theme == 'light' else '#e2e8f0'}; }}
            .step-arrow {{ font-size: 24px; color: {'#7c3aed' if theme == 'light' else '#a78bfa'}; font-weight: bold; user-select: none; }}
        </style>
        """
        
    elif card_type == "metrics":
        title = data.get("title", "Key Metrics")
        metrics = data.get("metrics", [])
        
        metrics_html = ""
        for m in metrics:
            val = m.get("value", "0")
            label = m.get("label", "")
            trend = m.get("trend", "none")
            
            badge_html = ""
            val_class = "metric-value"
            if trend == "up":
                badge_html = '<div class="metric-badge up">▲ 상승</div>'
                val_class += " up"
            elif trend == "down":
                badge_html = '<div class="metric-badge down">▼ 하락</div>'
                val_class += " down"
                
            metrics_html += f"""
            <div class="metric-item">
                <div class="{val_class}">{val}</div>
                <div class="metric-label">{label}</div>
                {badge_html}
            </div>
            """
            
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="metrics-grid">
                {metrics_html}
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 32px;
                background: {'linear-gradient(to right, #059669, #047857)' if theme == 'light' else 'linear-gradient(to right, #34d399, #059669)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .metrics-grid {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 32px;
                width: 100%;
            }}
            .metric-item {{
                flex: 1;
                background: {'rgba(0, 0, 0, 0.02)' if theme == 'light' else 'rgba(255, 255, 255, 0.03)'};
                border: 1px solid {'rgba(0, 0, 0, 0.05)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
                border-radius: 16px;
                padding: 24px;
                text-align: center;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                box-shadow: {'none' if theme == 'light' else 'inset 0 1px 1px 0 rgba(255, 255, 255, 0.05)'};
            }}
            .metric-value {{
                font-size: 48px;
                font-weight: 800;
                line-height: 1;
                margin-bottom: 12px;
                background: {'linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%)' if theme == 'light' else 'linear-gradient(135deg, #60a5fa 0%, #2563eb 100%)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .metric-value.up {{
                background: {'linear-gradient(135deg, #059669 0%, #047857 100%)' if theme == 'light' else 'linear-gradient(135deg, #34d399 0%, #059669 100%)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .metric-value.down {{
                background: {'linear-gradient(135deg, #dc2626 0%, #b91c1c 100%)' if theme == 'light' else 'linear-gradient(135deg, #f87171 0%, #dc2626 100%)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .metric-label {{ font-size: 14px; font-weight: 500; color: {'#475569' if theme == 'light' else '#94a3b8'}; }}
            .metric-badge {{
                margin-top: 10px;
                padding: 4px 10px;
                border-radius: 9999px;
                font-size: 12px;
                font-weight: 600;
            }}
            .metric-badge.up {{ background: {'rgba(5, 150, 105, 0.1)' if theme == 'light' else 'rgba(52, 211, 153, 0.1)'}; color: {'#059669' if theme == 'light' else '#34d399'}; }}
            .metric-badge.down {{ background: {'rgba(220, 38, 38, 0.1)' if theme == 'light' else 'rgba(248, 113, 113, 0.1)'}; color: {'#dc2626' if theme == 'light' else '#f87171'}; }}
        </style>
        """
        
    elif card_type == "diff":
        title = data.get("title", "Code Diff")
        lines = data.get("lines", [])
        
        diff_lines_html = ""
        for line in lines:
            line_str = str(line)
            if line_str.startswith("-"):
                diff_lines_html += f'<span class="diff-line del">{line_str}</span>'
            elif line_str.startswith("+"):
                diff_lines_html += f'<span class="diff-line add">{line_str}</span>'
            else:
                diff_lines_html += f'<span class="diff-line normal">{line_str}</span>'
                
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="diff-wrapper">
                <pre><code>{diff_lines_html}</code></pre>
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 24px;
                background: {'linear-gradient(to right, #dc2626, #2563eb)' if theme == 'light' else 'linear-gradient(to right, #f87171, #60a5fa)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .diff-wrapper {{
                background: {'#f8fafc' if theme == 'light' else 'rgba(15, 23, 42, 0.6)'};
                border: 1px solid {'rgba(0, 0, 0, 0.06)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
                border-radius: 12px;
                padding: 16px;
                font-family: 'Fira Code', monospace;
                font-size: 13px;
                line-height: 1.6;
                width: 100%;
                overflow: auto;
                text-align: left;
            }}
            .diff-line {{ display: block; padding: 2px 8px; border-radius: 4px; }}
            .diff-line.del {{ background: {'rgba(239, 68, 68, 0.1)' if theme == 'light' else 'rgba(239, 68, 68, 0.15)'}; color: {'#b91c1c' if theme == 'light' else '#f87171'}; text-decoration: line-through; }}
            .diff-line.add {{ background: {'rgba(34, 197, 94, 0.1)' if theme == 'light' else 'rgba(34, 197, 94, 0.15)'}; color: {'#15803d' if theme == 'light' else '#4ade80'}; }}
            .diff-line.normal {{ color: {'#334155' if theme == 'light' else '#cbd5e1'}; }}
        </style>
        """

    elif card_type == "file_tree":
        title = data.get("title", "Project Structure")
        items = data.get("items", [])
        
        tree_lines_html = ""
        for item in items:
            name = item.get("name", "")
            indent = item.get("indent", 0)
            is_active = item.get("active", False)
            is_dir = item.get("dir", False)
            
            indent_spaces = "&nbsp;&nbsp;" * indent
            icon = "📁 " if is_dir else "📄 "
            line_class = "tree-line active" if is_active else "tree-line"
            
            tree_lines_html += f"""
            <div class="{line_class}">
                <span class="tree-indent">{indent_spaces}</span>
                <span class="tree-icon">{icon}</span>
                <span class="tree-name">{name}</span>
            </div>
            """
            
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="tree-wrapper">
                {tree_lines_html}
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 24px;
                background: {'linear-gradient(to right, #0284c7, #7c3aed)' if theme == 'light' else 'linear-gradient(to right, #38bdf8, #a78bfa)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .tree-wrapper {{
                background: {'#f8fafc' if theme == 'light' else 'rgba(15, 23, 42, 0.6)'};
                border: 1px solid {'rgba(0, 0, 0, 0.06)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
                border-radius: 12px;
                padding: 24px;
                font-family: 'Fira Code', monospace;
                font-size: 14px;
                line-height: 1.8;
                width: 100%;
                overflow: auto;
                text-align: left;
            }}
            .tree-line {{ color: {'#334155' if theme == 'light' else '#cbd5e1'}; display: flex; align-items: center; padding: 2px 8px; }}
            .tree-line.active {{
                color: {'#0284c7' if theme == 'light' else '#38bdf8'};
                font-weight: 600;
                background: {'rgba(2, 132, 199, 0.08)' if theme == 'light' else 'rgba(56, 189, 248, 0.08)'};
                border-radius: 6px;
            }}
            .tree-indent {{ color: {'#94a3b8' if theme == 'light' else '#475569'}; }}
            .tree-icon {{ margin-right: 6px; }}
        </style>
        """

    elif card_type == "chat":
        messages = data.get("messages", [])
        
        chat_messages_html = ""
        for msg in messages:
            sender = msg.get("sender", "Q")
            text = msg.get("text", "")
            
            sender_class = "q" if sender.lower() == "q" else "a"
            sender_label = "Q" if sender.lower() == "q" else "A"
            
            chat_messages_html += f"""
            <div class="chat-msg {sender_class}">
                <div class="avatar">{sender_label}</div>
                <div class="bubble">{text}</div>
            </div>
            """
            
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <div class="chat-wrapper">
                {chat_messages_html}
            </div>
        </div>
        <style>
            {css_base}
            .chat-wrapper {{
                display: flex;
                flex-direction: column;
                gap: 20px;
                width: 100%;
            }}
            .chat-msg {{
                display: flex;
                align-items: flex-start;
                gap: 12px;
                max-width: 85%;
            }}
            .chat-msg.q {{ align-self: flex-start; text-align: left; }}
            .chat-msg.a {{ align-self: flex-end; flex-direction: row-reverse; text-align: left; }}
            .avatar {{
                width: 36px;
                height: 36px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: 700;
                font-size: 14px;
                color: #ffffff;
                flex-shrink: 0;
            }}
            .chat-msg.q .avatar {{ background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); }}
            .chat-msg.a .avatar {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); }}
            .bubble {{
                background: {'rgba(0, 0, 0, 0.02)' if theme == 'light' else 'rgba(255, 255, 255, 0.03)'};
                border: 1px solid {'rgba(0, 0, 0, 0.05)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
                border-radius: 12px;
                padding: 12px 16px;
                font-size: 14px;
                line-height: 1.5;
                color: {'#334155' if theme == 'light' else '#e2e8f0'};
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            }}
            .chat-msg.q .bubble {{ border-left: 4px solid #f59e0b; border-top-left-radius: 2px; }}
            .chat-msg.a .bubble {{ border-right: 4px solid #10b981; border-top-right-radius: 2px; }}
        </style>
        """
 
    elif card_type == "dos_donts":
        title = data.get("title", "Do's & Don'ts")
        donts = data.get("donts", [])
        dos = data.get("dos", [])
        
        donts_html = "".join([f"<li>{item}</li>" for item in donts])
        dos_html = "".join([f"<li>{item}</li>" for item in dos])
        
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="dd-grid">
                <div class="dd-box dont">
                    <div class="dd-header">⚠️ DONT</div>
                    <ul>{donts_html}</ul>
                </div>
                <div class="dd-box do">
                    <div class="dd-header">✅ DO</div>
                    <ul>{dos_html}</ul>
                </div>
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 24px;
                background: {'linear-gradient(to right, #dc2626, #059669)' if theme == 'light' else 'linear-gradient(to right, #f87171, #34d399)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .dd-grid {{ display: flex; gap: 24px; width: 100%; text-align: left; }}
            .dd-box {{
                flex: 1;
                border-radius: 16px;
                padding: 20px;
                min-height: 220px;
                box-shadow: {'none' if theme == 'light' else 'inset 0 1px 1px 0 rgba(255, 255, 255, 0.05)'};
            }}
            .dd-box.dont {{
                background: {'rgba(239, 68, 68, 0.02)' if theme == 'light' else 'rgba(239, 68, 68, 0.04)'};
                border: 1px solid rgba(239, 68, 68, 0.2);
            }}
            .dd-box.do {{
                background: {'rgba(34, 197, 94, 0.02)' if theme == 'light' else 'rgba(34, 197, 94, 0.04)'};
                border: 1px solid rgba(34, 197, 94, 0.2);
            }}
            .dd-header {{
                font-size: 16px;
                font-weight: 700;
                margin-bottom: 16px;
            }}
            .dd-box.dont .dd-header {{ color: {'#b91c1c' if theme == 'light' else '#f87171'}; }}
            .dd-box.do .dd-header {{ color: {'#15803d' if theme == 'light' else '#4ade80'}; }}
            .dd-box ul {{ list-style: none; }}
            .dd-box li {{
                font-size: 13px;
                line-height: 1.6;
                margin-bottom: 8px;
                color: {'#334155' if theme == 'light' else '#e2e8f0'};
                position: relative;
                padding-left: 16px;
            }}
            .dd-box li::before {{
                content: "•";
                position: absolute;
                left: 0;
                font-weight: bold;
            }}
            .dd-box.dont li::before {{ color: {'#b91c1c' if theme == 'light' else '#f87171'}; }}
            .dd-box.do li::before {{ color: {'#15803d' if theme == 'light' else '#4ade80'}; }}
        </style>
        """

    elif card_type == "glossary":
        word = data.get("word", "Term")
        pronunciation = data.get("pronunciation", "")
        definition = data.get("definition", "")
        example = data.get("example", "")
        
        pronunciation_html = f'<span class="pronunciation">{pronunciation}</span>' if pronunciation else ""
        example_html = f"""
        <div class="example-box">
            <div class="ex-title">쉬운 설명 / 예시</div>
            <p class="example">{example}</p>
        </div>
        """ if example else ""
        
        html_content = f"""
        <div class="card-container glossary-card">
            <div class="card-glow"></div>
            <div class="word-header">
                <span class="word">{word}</span>
                {pronunciation_html}
            </div>
            <div class="definition-box">
                <div class="def-title">정의</div>
                <p class="definition">{definition}</p>
            </div>
            {example_html}
        </div>
        <style>
            {css_base}
            .glossary-card {{ padding: 40px; justify-content: flex-start; gap: 20px; text-align: left; }}
            .word-header {{ display: flex; align-items: baseline; gap: 12px; border-bottom: 1px solid {'rgba(0, 0, 0, 0.08)' if theme == 'light' else 'rgba(255,255,255,0.1)'}; padding-bottom: 12px; width: 100%; }}
            .word {{ font-size: 28px; font-weight: 700; color: #0284c7; }}
            .pronunciation {{ font-size: 14px; color: #64748b; font-style: italic; }}
            .definition-box, .example-box {{ width: 100%; }}
            .def-title, .ex-title {{ font-size: 12px; font-weight: 600; text-transform: uppercase; color: #94a3b8; margin-bottom: 6px; letter-spacing: 0.05em; }}
            .definition {{ font-size: 15px; line-height: 1.6; color: {'#0f172a' if theme == 'light' else '#f1f5f9'}; }}
            .example {{
                font-size: 14px;
                line-height: 1.6;
                color: {'#334155' if theme == 'light' else '#cbd5e1'};
                font-style: italic;
                background: {'rgba(0,0,0,0.01)' if theme == 'light' else 'rgba(255,255,255,0.02)'};
                border-left: 2px solid #6366f1;
                padding-left: 12px;
                padding-top: 4px;
                padding-bottom: 4px;
            }}
        </style>
        """

    elif card_type == "callout":
        title = data.get("title", "Caution")
        text = data.get("text", "")
        icon = data.get("icon", "⚠️")
        
        html_content = f"""
        <div class="card-container callout-card">
            <div class="card-glow"></div>
            <div class="callout-icon">{icon}</div>
            <div class="callout-body">
                <h3 class="callout-title">{title}</h3>
                <p class="callout-text">{text}</p>
            </div>
        </div>
        <style>
            {css_base}
            .callout-card {{
                flex-direction: row;
                align-items: center;
                padding: 48px;
                gap: 28px;
                border: 1px solid {'rgba(217, 119, 6, 0.25)' if theme == 'light' else 'rgba(245, 158, 11, 0.2)'};
                background: {'rgba(217, 119, 6, 0.04)' if theme == 'light' else 'rgba(245, 158, 11, 0.03)'};
                text-align: left;
            }}
            .callout-icon {{
                font-size: 56px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #f59e0b;
                text-shadow: {'none' if theme == 'light' else '0 0 16px rgba(245, 158, 11, 0.3)'};
                flex-shrink: 0;
            }}
            .callout-body {{ flex: 1; }}
            .callout-title {{ font-size: 22px; font-weight: 700; color: #d97706; margin-bottom: 10px; }}
            .callout-text {{ font-size: 14px; line-height: 1.6; color: {'#334155' if theme == 'light' else '#cbd5e1'}; }}
        </style>
        """

    elif card_type == "portfolio":
        title = data.get("title", "Portfolio Allocation")
        items = data.get("items", [])
        
        # Predefined premium colors
        color_palette = [
            "linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)",
            "linear-gradient(135deg, #f97316 0%, #c2410c 100%)",
            "linear-gradient(135deg, #ec4899 0%, #be185d 100%)",
            "linear-gradient(135deg, #10b981 0%, #047857 100%)",
            "linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%)",
            "linear-gradient(135deg, #f59e0b 0%, #b45309 100%)"
        ]
        color_palette_dots = ["#3b82f6", "#f97316", "#ec4899", "#10b981", "#8b5cf6", "#f59e0b"]
        
        bar_sections_html = ""
        legend_html = ""
        for idx, item in enumerate(items):
            name = item.get("name", "Asset")
            weight = item.get("weight", 0)
            
            grad_color = color_palette[idx % len(color_palette)]
            dot_color = color_palette_dots[idx % len(color_palette_dots)]
            
            if weight > 0:
                bar_sections_html += f"""
                <div class="bar-section" style="width: {weight}%; background: {grad_color};">
                    {weight}%
                </div>
                """
                legend_html += f"""
                <div class="legend-item">
                    <div class="legend-dot" style="background: {dot_color};"></div>
                    <div class="legend-text">{name}: {weight}%</div>
                </div>
                """
                
        html_content = f"""
        <div class="card-container">
            <div class="card-glow"></div>
            <h2 class="title">{title}</h2>
            <div class="portfolio-bar">
                {bar_sections_html}
            </div>
            <div class="portfolio-legend">
                {legend_html}
            </div>
        </div>
        <style>
            {css_base}
            .title {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 28px;
                background: {'linear-gradient(to right, #2563eb, #4f46e5)' if theme == 'light' else 'linear-gradient(to right, #60a5fa, #a5b4fc)'};
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-align: center;
            }}
            .portfolio-bar {{
                display: flex;
                height: 48px;
                width: 100%;
                border-radius: 24px;
                overflow: hidden;
                box-shadow: {'0 4px 6px -1px rgba(0, 0, 0, 0.05)' if theme == 'light' else '0 10px 15px -3px rgba(0, 0, 0, 0.3)'};
                border: 1px solid {'rgba(0, 0, 0, 0.06)' if theme == 'light' else 'rgba(255, 255, 255, 0.05)'};
                margin-bottom: 32px;
            }}
            .bar-section {{
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: 700;
                font-size: 14px;
                color: #ffffff;
                text-shadow: 0 2px 4px rgba(0,0,0,0.5);
            }}
            .portfolio-legend {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 16px;
                width: 100%;
                text-align: left;
            }}
            .legend-item {{ display: flex; align-items: center; gap: 8px; font-size: 14px; color: {'#334155' if theme == 'light' else '#cbd5e1'}; }}
            .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
        </style>
        """

    temp_html_path = "temp_infographic.html"
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body, html {{ margin:0; padding:0; width:800px; height:500px; overflow:hidden; }}
        </style>
    </head>
    <body>
        {html_content}
    </body>
    </html>
    """
    
    with open(temp_html_path, "w", encoding="utf-8") as f:
        f.write(full_html)
        
    abs_html_path = "file://" + os.path.abspath(temp_html_path)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={{"width": 800, "height": 500}})
        page.goto(abs_html_path)
        page.wait_for_timeout(1000)
        page.screenshot(path=output_path)
        browser.close()
        
    if os.path.exists(temp_html_path):
        os.remove(temp_html_path)
    print(f"HTML Infographic Card saved to {output_path}")

def process_markdown(file_path):
    """
    마크다운 원고를 파싱하여 이미지 태그와 코드 카드를 자동 생성 및 치환합니다.
    """
    if not os.path.exists(file_path):
        print(f"Error: File not found -> {file_path}")
        return
        
    print(f"Processing markdown file: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # 마크다운 디렉토리 기준 images 폴더 설정
    base_dir = os.path.dirname(os.path.abspath(file_path))
    images_dir = os.path.join(base_dir, "images")
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)
        
    # 1. FRED 지표 차트 감지 및 생성
    # 형식: [FRED:M2SL:fred_m2.png]
    fred_matches = re.findall(r'\[FRED:([a-zA-Z0-9_]+):([a-zA-Z0-9_\-\.]+?)\]', content)
    for series_id, file_name in fred_matches:
        out_img_path = os.path.join(images_dir, file_name)
        draw_fred_chart(series_id, out_img_path)
        # 본문 치환: 마크다운 이미지 링크로 변환
        rel_img_path = f"images/{file_name}"
        content = content.replace(f"[FRED:{series_id}:{file_name}]", f"![FRED 지표: {series_id}]({rel_img_path})")
        
    # 2. TradingView 차트 감지 및 생성
    # 형식: [TV:SPY:tv_spy.png]
    tv_matches = re.findall(r'\[TV:([a-zA-Z0-9_\-\.\:\s\(\)]+):([a-zA-Z0-9_\-\.]+?)\]', content)
    for symbol, file_name in tv_matches:
        out_img_path = os.path.join(images_dir, file_name)
        capture_tradingview_chart(symbol, out_img_path)
        rel_img_path = f"images/{file_name}"
        content = content.replace(f"[TV:{symbol}:{file_name}]", f"![TradingView 차트: {symbol}]({rel_img_path})")
        
    # 3. Unsplash 이미지 감지 및 다운로드
    # 형식: [UNSPLASH:keyword:file_name.png]
    unsplash_matches = re.findall(r'\[UNSPLASH:([a-zA-Z0-9_\-\s]+):([a-zA-Z0-9_\-\.]+?)\]', content)
    for keyword, file_name in unsplash_matches:
        out_img_path = os.path.join(images_dir, file_name)
        download_unsplash_image(keyword, out_img_path)
        rel_img_path = f"images/{file_name}"
        content = content.replace(f"[UNSPLASH:{keyword}:{file_name}]", f"![감성 스톡 이미지: {keyword}]({rel_img_path})")
        
    # 4. HTML 인포그래픽 카드 감지 및 생성
    # 형식:
    # [HTML_CARD: comparison.png]
    # ```yaml
    # type: compare
    # ...
    # ```
    html_card_pattern = r'\[HTML_CARD:\s*([a-zA-Z0-9_\-\.]+?)\]\s*```(?:yaml|json)\n(.*?)```'
    html_matches = re.findall(html_card_pattern, content, re.DOTALL)
    for file_name, card_data_body in html_matches:
        out_img_path = os.path.join(images_dir, file_name)
        create_html_card(card_data_body.strip(), out_img_path)
        
        # 원래 선언 영역을 이미지 링크로 치환
        rel_img_path = f"images/{file_name}"
        target_pattern = r'\[HTML_CARD:\s*' + re.escape(file_name) + r'\]\s*```(?:yaml|json)\n' + re.escape(card_data_body) + r'```'
        content = re.sub(target_pattern, f"![인포그래픽 카드: {file_name}]({rel_img_path})", content, flags=re.DOTALL)

    # 5. 코드 카드 감지 및 생성
    # 형식: 
    # [CODE_CARD: card_name.png]
    # ```java
    # public class Foo {}
    # ```
    code_card_pattern = r'\[CODE_CARD:\s*([a-zA-Z0-9_\-\.]+?)\]\s*```([a-zA-Z0-9_\+\-\#\s]*)\n(.*?)```'
    code_matches = re.findall(code_card_pattern, content, re.DOTALL)
    for file_name, lang, code_body in code_matches:
        out_img_path = os.path.join(images_dir, file_name)
        create_code_card(code_body.strip(), lang.strip(), out_img_path)
        
        # 원래 선언 영역을 이미지 링크로 치환하기 위한 regex 치환
        rel_img_path = f"images/{file_name}"
        target_pattern = r'\[CODE_CARD:\s*' + re.escape(file_name) + r'\]\s*```' + re.escape(lang) + r'\n' + re.escape(code_body) + r'```'
        content = re.sub(target_pattern, f"![코드 카드: {file_name}]({rel_img_path})", content, flags=re.DOTALL)

    # 결과를 원본 파일에 덮어씁니다.
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Successfully processed and updated {file_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/image_pipeline.py [path_to_markdown_file]")
        sys.exit(1)
        
    target_md = sys.argv[1]
    process_markdown(target_md)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/image_pipeline.py [path_to_markdown_file]")
        sys.exit(1)
        
    target_md = sys.argv[1]
    process_markdown(target_md)
