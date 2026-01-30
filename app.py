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
from PIL import Image

# --- 1. EPUB 생성 엔진 (기존과 동일) ---
def build_epub_buffer(txt_content, title, font_type, cover_io=None):
    # (이전 코드와 동일하므로 지면 관계상 핵심 로직 유지)
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

    CHAPTER_PATTERN = r'^(제\s?\d+\s?[화장]|[\d\.]+\s|\[.+\])'
    raw_lines = txt_content.splitlines()
    chapters = []
    current_title, current_lines = "시작", []

    for line in raw_lines:
        line = line.strip()
        if not line: continue
        if re.match(CHAPTER_PATTERN, line):
            if current_lines: chapters.append((current_title, current_lines))
            current_title, current_lines = line, []
        else:
            current_lines.append(html.escape(line))
    if current_lines: chapters.append((current_title, current_lines))
    if not chapters: chapters.append(("본문", [html.escape(p) for p in raw_lines if p.strip()]))

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
# 2. UI 레이아웃
# -------------------------
st.set_page_config(page_title="TXT to EPUB", layout="wide")
st.title("📚 올인원 EPUB 변환기 (안정화 버전)")

if "results" not in st.session_state: st.session_state.results = []
if "selected_cover" not in st.session_state: st.session_state.selected_cover = None

col1, col2 = st.columns([1, 1])

with col1:
    st.header("1. 설정 및 파일")
    u_txt = st.file_uploader("TXT 파일 선택", type="txt", key="txt_loader")
    
    # 제목 자동 정제 (1-304 완 같은 꼬리표 제거 시도)
    raw_title = Path(u_txt.name).stem if u_txt else ""
    clean_title = re.sub(r'[\d\-]+.*$', '', raw_title).strip() # 숫자/기호 이후 제거
    title = st.text_input("책 제목", value=clean_title if clean_title else "제목 없음")
    
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
                    # 검색 결과 가져오기 (예외 처리 추가)
                    st.session_state.results = [r['image'] for r in ddgs.images(search_q, max_results=6)]
                    if not st.session_state.results:
                        st.warning("검색 결과가 없습니다.")
            except Exception as e:
                st.error("검색 제한에 걸렸습니다. 잠시 후 다시 시도하거나 URL을 직접 입력하세요.")
    
    with c2:
        direct_url = st.text_input("직접 이미지 URL 입력", placeholder="http://...")
        if direct_url:
            st.session_state.selected_cover = direct_url

    # 검색 결과 그리드
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
    if st.session_state.selected_cover:
        st.write(f"✅ 선택된 표지: {st.session_state.selected_cover[:60]}...")
    
    if st.button("🚀 EPUB 변환 및 다운로드", type="primary", use_container_width=True):
        with st.spinner("최종 제작 중..."):
            txt_data = u_txt.read()
            try: text = txt_data.decode("utf-8")
            except: text = txt_data.decode("cp949", errors="ignore")
            
            c_io = None
            if st.session_state.selected_cover:
                try:
                    r = requests.get(st.session_state.selected_cover, timeout=10)
                    c_io = io.BytesIO(r.content)
                except:
                    st.error("이미지를 다운로드할 수 없습니다. 다른 이미지를 선택해 보세요.")
            
            final_epub = build_epub_buffer(text, title, f_type, c_io)
            st.success("변환 성공!")
            st.download_button("📥 완성된 파일 저장", data=final_epub, file_name=f"{title}.epub")