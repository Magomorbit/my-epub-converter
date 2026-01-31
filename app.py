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
from charset_normalizer import from_bytes  # 인코딩 인식을 위해 꼭 필요!

# -------------------------
# 1. EPUB 생성 엔진
# -------------------------
def build_epub_buffer(chapters_to_include, title, font_type, cover_io=None):
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

    with zipfile.ZipFile(epub_stream, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        
        if has_font and font_type == "리디바탕":
            with open(font_filename, "rb") as f: zf.writestr(f"OEBPS/fonts/{font_filename}", f.read())
        zf.writestr("OEBPS/style.css", css_content)

        processed_chunks = []
        for ch_t, ch_l in chapters_to_include:
            chunk_size = 50 
            for i in range(0, len(ch_l), chunk_size):
                sub_l = ch_l[i:i+chunk_size]
                sub_t = ch_t if i == 0 else f"{ch_t} (계속)"
                processed_chunks.append((sub_t, sub_l))

        manifest_items, spine_items, nav_points = "", "", ""
        for i, (ch_t, ch_l) in enumerate(processed_chunks):
            fname = f"ch_{i}.xhtml"
            header = f"<h1>{html.escape(title)}</h1>" if i == 0 else ""
            display_title = "" if "(계속)" in ch_t else f"<h2>{html.escape(ch_t)}</h2>"
            xhtml = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" type="text/css" href="style.css"/></head><body>{header}{display_title}{"".join([f"<p>{l}</p>" for l in ch_l])}</body></html>'
            zf.writestr(f"OEBPS/{fname}", xhtml)
            manifest_items += f'<item id="c{i}" href="{fname}" media-type="application/xhtml+xml"/>\n'
            spine_items += f'<itemref idref="c{i}"/>\n'
            if "(계속)" not in ch_t:
                nav_points += f'<navPoint id="p{i}" playOrder="{i+1}"><navLabel><text>{ch_t}</text></navLabel><content src="{fname}"/></navPoint>'

        ncx = f'<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{book_id}"/></head><docTitle><text>{title}</text></docTitle><navMap>{nav_points}</navMap></ncx>'
        zf.writestr("OEBPS/toc.ncx", ncx)
        
        if cover_io:
            zf.writestr("OEBPS/cover.jpg", cover_io.getvalue())
        
        font_manifest = f'<item id="f" href="fonts/{font_filename}" media-type="application/vnd.ms-opentype"/>' if has_font else ""
        manifest_cover = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>' if cover_io else ""
        cover_tag = '<meta name="cover" content="cover"/>' if cover_io else ""
        
        opf = f'<?xml version="1.0" encoding="utf-8"?><package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{html.escape(title)}</dc:title><dc:language>ko</dc:language><dc:identifier id="uid">{book_id}</dc:identifier>{cover_tag}</metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="s" href="style.css" media-type="text/css"/>{manifest_items}{font_manifest}{manifest_cover}</manifest><spine toc="ncx">{spine_items}</spine></package>'
        zf.writestr("OEBPS/content.opf", opf)

    epub_stream.seek(0)
    return epub_stream

# -------------------------
# 2. UI 및 로직
# -------------------------
st.set_page_config(page_title="TXT to EPUB", layout="wide")
st.title("📚 스마트 EPUB 변환기 PRO")

if "search_results" not in st.session_state: st.session_state.search_results = []
if "final_cover_io" not in st.session_state: st.session_state.final_cover_io = None

col1, col2 = st.columns([1, 1])

with col1:
    st.header("1. 파일 및 챕터 설정")
    u_txt = st.file_uploader("TXT 파일 선택", type="txt")
    
    f_exists = os.path.exists("RIDIBatang.otf")
    font_options = ["리디바탕", "기본 명조체", "고딕체"] if f_exists else ["기본 명조체", "고딕체"]
    f_type = st.selectbox("📖 적용할 폰트 선택", font_options)
    
    use_split = st.radio("챕터 분할 모드", ["챕터분할 적용함", "안함"], horizontal=True)
    
    display_title = ""
    final_chapters = []

    if u_txt:
        raw_bytes = u_txt.getvalue()
        # [인코딩 인식] 깨짐 방지
        try:
            detected = from_bytes(raw_bytes).best()
            text = str(detected) if detected else raw_bytes.decode('utf-8', errors='ignore')
        except:
            text = raw_bytes.decode('cp949', errors='ignore')

        # [제목 정제] 하이픈(-)은 유지, +와 _만 공백으로
        raw_filename = Path(u_txt.name).stem
        clean_name = re.sub(r'[+_]', ' ', raw_filename)  # - 제거됨
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        
        display_title = st.text_input("책 제목", value=clean_name)
        
        raw_lines = text.splitlines()
        
        if use_split == "챕터분할 적용함":
            temp_chapters = []
            curr_t, curr_l = "시작", []
            for line in raw_lines:
                cl = line.strip()
                if not cl: continue
                is_ch = False
                if re.match(r'^제\s?\d+\s?[화장장편절]', cl): is_ch = True
                elif re.match(r'^\d+[\.\s]', cl) and len(cl) < 20 and not re.search(r'\d+\s?대\s?\d+', cl): is_ch = True
                elif re.match(r'^[[<].+[]>]', cl) and len(cl) < 15:
                    if not any(char in cl for char in ['.', '!', '?', ']', '>']): is_ch = True
                elif re.match(r'^\d+$', cl): is_ch = True

                if is_ch:
                    if curr_l: temp_chapters.append((curr_t, curr_l))
                    curr_t, curr_l = cl, []
                else: curr_l.append(html.escape(cl))
            if curr_l: temp_chapters.append((curr_t, curr_l))

            st.write("### 챕터 필터링")
            selected_indices = []
            with st.container(height=300):
                for idx, (t, _) in enumerate(temp_chapters):
                    if st.checkbox(t, value=True, key=f"ch_{idx}"):
                        selected_indices.append(idx)
            
            if temp_chapters:
                processed_ch = []
                for idx, (t, l) in enumerate(temp_chapters):
                    if idx in selected_indices: processed_ch.append([t, l])
                    else:
                        if processed_ch: processed_ch[-1][1].extend([f"[{t}]"] + l)
                        else: processed_ch.append(["본문", [f"[{t}]"] + l])
                final_chapters = processed_ch
        else:
            final_chapters = [("본문", [html.escape(l.strip()) for l in raw_lines if l.strip()])]
    else:
        display_title = st.text_input("책 제목", value="제목 없음")

with col2:
    st.header("2. 표지 설정")
    cover_mode = st.radio("표지 획득 방법", ["이미지 업로드", "이미지 검색"], horizontal=True)
    
    if cover_mode == "이미지 업로드":
        u_cover = st.file_uploader("표지 이미지 선택", type=["jpg", "jpeg", "png"])
        if u_cover:
            st.session_state.final_cover_io = io.BytesIO(u_cover.getvalue())
            st.image(u_cover, caption="미리보기", width=120)
    else:
        search_q = st.text_input("검색어", value=f"{display_title} 소설 표지")
        if st.button("🔍 이미지 검색", use_container_width=True):
            try:
                with DDGS() as ddgs:
                    st.session_state.search_results = [r['image'] for r in ddgs.images(search_q, max_results=6)]
            except: st.error("검색 제한입니다.")
        
        if st.session_state.search_results:
            grid = st.columns(3)
            for i, url in enumerate(st.session_state.search_results):
                with grid[i % 3]:
                    st.image(url, use_container_width=True)
                    if st.button(f"{i+1}번 선택", key=f"btn_{i}"):
                        try:
                            r = requests.get(url, timeout=10)
                            st.session_state.final_cover_io = io.BytesIO(r.content)
                            st.toast("이미지 선택됨!")
                        except: st.error("이미지 로드 실패")
        
        if st.session_state.final_cover_io:
            st.divider()
            st.image(st.session_state.final_cover_io, caption="선택된 이미지", width=120)

st.divider()

if u_txt and final_chapters:
    st.download_button(
        label="💾 EPUB 변환 및 지금 바로 저장",
        data=build_epub_buffer(final_chapters, display_title, f_type, st.session_state.final_cover_io),
        file_name=f"{display_title}.epub",
        mime="application/epub+zip",
        type="primary",
        use_container_width=True
    )