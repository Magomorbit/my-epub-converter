import streamlit as st
import zipfile
import html
import io
import uuid
import os
import re
import requests
from pathlib import Path
from duckduckgo_search import DDGS

# -------------------------
# 1. EPUB 생성 및 엔진 (챕터 인식 강화 버전)
# -------------------------
def build_epub_buffer(txt_content, title, font_type, cover_io=None):
    epub_stream = io.BytesIO()
    book_id = str(uuid.uuid4())
    font_filename = "RIDIBatang.otf"
    has_font = os.path.exists(font_filename)

    css_content = f'''
    @font-face {{ font-family: 'RIDIBatang'; src: url('fonts/{font_filename}'); }}
    body {{ 
        font-family: {'"RIDIBatang", serif' if has_font and font_type == "리디바탕" else '"Batang", serif'};
        line-height: 1.8; margin: 5% 8%; text-align: justify; word-break: break-all;
    }}
    p {{ margin-top: 0; margin-bottom: 1.5em; text-indent: 1em; }}
    h2 {{ text-align: center; margin-top: 3em; margin-bottom: 2em; font-size: 1.4em; border-bottom: 1px solid #ccc; padding-bottom: 0.5em; }}
    h1 {{ text-align: center; margin-top: 4em; }}
    '''

    # --- 개선된 제목 인식 로직 ---
    raw_lines = txt_content.splitlines()
    chapters = []
    current_title, current_lines = "시작", []

    for line in raw_lines:
        clean_line = line.strip()
        if not clean_line: continue
        
        is_title = False
        # 규칙 1: '제 1화', '제 10장' 등 (가장 표준)
        if re.match(r'^제\s?\d+\s?[화장장편절]', clean_line):
            is_title = True
        # 규칙 2: '숫자.' 또는 '숫자 '로 시작하고 총 길이가 20자 미만인 경우
        elif re.match(r'^\d+[\.\s]', clean_line) and len(clean_line) < 20:
            is_title = True
        # 규칙 3: 대괄호나 꺽쇠로 시작하고 총 길이가 15자 미만인 경우 (대사 방지)
        elif re.match(r'^[[<].+[]>]', clean_line) and len(clean_line) < 15:
            is_title = True
        # 규칙 4: 숫자만 있는 줄
        elif re.match(r'^\d+$', clean_line):
            is_title = True

        if is_title:
            if current_lines: chapters.append((current_title, current_lines))
            current_title, current_lines = clean_line, []
        else:
            current_lines.append(html.escape(clean_line))
            
    if current_lines: chapters.append((current_title, current_lines))
    if not chapters: chapters.append(("본문", [html.escape(p) for p in raw_lines if p.strip()]))
    # ---------------------------

    with zipfile.ZipFile(epub_stream, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        
        if has_font and font_type == "리디바탕":
            with open(font_filename, "rb") as f: zf.writestr(f"OEBPS/fonts/{font_filename}", f.read())
        zf.writestr("OEBPS/style.css", css_content)

        manifest_items, spine_items, nav_points = "", "", ""
        for i, (ch_t, ch_l) in enumerate(chapters):
            fname = f"ch_{i}.xhtml"
            header = f"<h1>{html.escape(title)}</h1>" if i == 0 else ""
            xhtml = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" type="text/css" href="style.css"/></head><body>{header}<h2>{html.escape(ch_t)}</h2>{"".join([f"<p>{l}</p>" for l in ch_l])}</body></html>'
            zf.writestr(f"OEBPS/{fname}", xhtml)
            manifest_items += f'<item id="c{i}" href="{fname}" media-type="application/xhtml+xml"/>\n'
            spine_items += f'<itemref idref="c{i}"/>\n'
            nav_points += f'<navPoint id="p{i}" playOrder="{i+1}"><navLabel><text>{ch_t}</text></navLabel><content src="{fname}"/></navPoint>'

        ncx = f'<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{book_id}"/></head><docTitle><text>{title}</text></docTitle><navMap>{nav_points}</navMap></ncx>'
        zf.writestr("OEBPS/toc.ncx", ncx)

        font_manifest = f'<item id="f" href="fonts/{font_filename}" media-type="application/vnd.ms-opentype"/>' if has_font else ""
        cover_tag, manifest_cover = "", ""
        if cover_io:
            zf.writestr("OEBPS/cover.jpg", cover_io.getvalue())
            manifest_cover = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
            cover_tag = '<meta name="cover" content="cover"/>'

        opf = f'<?xml version="1.0" encoding="utf-8"?><package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{html.escape(title)}</dc:title><dc:language>ko</dc:language><dc:identifier id="uid">{book_id}</dc:identifier>{cover_tag}</metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="s" href="style.css" media-type="text/css"/>{manifest_items}{font_manifest}{manifest_cover}</manifest><spine toc="ncx">{spine_items}</spine></package>'
        zf.writestr("OEBPS/content.opf", opf)

    epub_stream.seek(0)
    return epub_stream

# -------------------------
# 2. UI 및 로직
# -------------------------
st.set_page_config(page_title="TXT to EPUB", layout="wide")
st.title("📚 스마트 EPUB 변환기")

if "results" not in st.session_state: st.session_state.results = []
if "selected_cover" not in st.session_state: st.session_state.selected_cover = None

col1, col2 = st.columns([1, 1])

with col1:
    st.header("1. 설정 및 챕터 확인")
    u_txt = st.file_uploader("TXT 파일 선택", type="txt", key="txt_loader")
    
    if u_txt:
        raw_data = u_txt.getvalue()
        try: text = raw_data.decode("utf-8")
        except: text = raw_data.decode("cp949", errors="ignore")
        
        # 제목 정제 및 분석
        raw_title = Path(u_txt.name).stem
        clean_title = re.sub(r'[\d\-]+.*$', '', raw_title).strip()
        title = st.text_input("책 제목", value=clean_title if clean_title else "제목 없음")

        # 실시간 챕터 확인 로직
        detected = []
        for line in text.splitlines():
            cl = line.strip()
            if not cl: continue
            if (re.match(r'^제\s?\d+\s?[화장장편절]', cl) or 
                (re.match(r'^\d+[\.\s]', cl) and len(cl) < 20) or 
                (re.match(r'^[[<].+[]>]', cl) and len(cl) < 15) or
                re.match(r'^\d+$', cl)):
                detected.append(cl)

        with st.expander(f"🔍 인식된 챕터 목록 ({len(detected)}개)", expanded=True):
            if detected:
                st.code("\n".join(detected[:50]) + ("\n..." if len(detected) > 50 else ""))
            else:
                st.warning("인식된 챕터가 없습니다.")

    st.sidebar.header("📖 디자인 설정")
    f_exists = os.path.exists("RIDIBatang.otf")
    f_type = st.sidebar.selectbox("폰트", ["리디바탕", "기본 명조체", "고딕체"] if f_exists else ["기본 명조체", "고딕체"])

with col2:
    st.header("2. 표지 선택")
    search_q = st.text_input("검색어", value=f"{title} 소설 표지")
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔍 이미지 검색", use_container_width=True):
            try:
                with DDGS() as ddgs:
                    st.session_state.results = [r['image'] for r in ddgs.images(search_q, max_results=6)]
            except:
                st.error("검색 제한입니다. 잠시 후 다시 시도하세요.")
    
    with c2:
        direct_url = st.text_input("직접 이미지 URL 입력")
        if direct_url: st.session_state.selected_cover = direct_url

    if st.session_state.results:
        grid = st.columns(3)
        for i, url in enumerate(st.session_state.results):
            with grid[i % 3]:
                st.image(url, use_container_width=True)
                if st.button(f"{i+1}번 선택", key=f"cover_{i}"):
                    st.session_state.selected_cover = url
                    st.toast(f"{i+1}번 표지 선택됨!")

st.divider()

if u_txt:
    if st.button("🚀 EPUB 변환 및 다운로드", type="primary", use_container_width=True):
        with st.spinner("최종 제작 중..."):
            u_txt.seek(0)
            data = u_txt.read()
            try: text = data.decode("utf-8")
            except: text = data.decode("cp949", errors="ignore")
            
            c_io = None
            if st.session_state.selected_cover:
                try:
                    r = requests.get(st.session_state.selected_cover, timeout=10)
                    c_io = io.BytesIO(r.content)
                except: pass
            
            final_epub = build_epub_buffer(text, title, f_type, c_io)
            st.success("변환 성공!")
            st.download_button("📥 파일 저장", data=final_epub, file_name=f"{title}.epub")