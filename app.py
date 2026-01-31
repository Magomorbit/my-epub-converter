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
from charset_normalizer import from_bytes

# -------------------------
# 1. EPUB 생성 엔진 (표준 압축 버전)
# -------------------------
def build_epub_buffer(chapters_to_include, title, font_type, cover_io=None):
    try:
        epub_stream = io.BytesIO()
        book_id = str(uuid.uuid4())
        font_filename = "RIDIBatang.otf"
        
        # 폰트 포함 여부 설정
        embed_font = (font_type == "리디바탕" and os.path.exists(font_filename))

        css_content = f'''
        @font-face {{ font-family: 'RIDIBatang'; src: url('fonts/{font_filename}'); }}
        body {{ 
            font-family: {'"RIDIBatang", serif' if embed_font else 'serif'};
            line-height: 1.8; margin: 5% 8%; text-align: justify; word-break: break-all;
        }}
        p {{ margin-top: 0; margin-bottom: 1.5em; text-indent: 1em; }}
        h1, h2 {{ text-align: center; }}
        '''

        # 표준 ZIP 압축(DEFLATED)을 사용하여 용량과 속도의 균형을 맞춤
        with zipfile.ZipFile(epub_stream, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # mimetype은 규약상 압축 없이 저장
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr("META-INF/container.xml", '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            
            if embed_font:
                with open(font_filename, "rb") as f: 
                    zf.writestr(f"OEBPS/fonts/{font_filename}", f.read())
            
            zf.writestr("OEBPS/style.css", css_content)

            # 챕터 XHTML 생성
            for i, (ch_t, ch_l) in enumerate(chapters_to_include):
                fname = f"ch_{i}.xhtml"
                header = f"<h1>{html.escape(title)}</h1>" if i == 0 else ""
                display_title_xhtml = f"<h2>{html.escape(ch_t)}</h2>"
                body_content = "".join(f"<p>{line}</p>" for line in ch_l)
                
                xhtml = (
                    f'<?xml version="1.0" encoding="utf-8"?>'
                    f'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
                    f'<html xmlns="http://www.w3.org/1999/xhtml">'
                    f'<head><link rel="stylesheet" type="text/css" href="style.css"/></head>'
                    f'<body>{header}{display_title_xhtml}{body_content}</body>'
                    f'</html>'
                )
                zf.writestr(f"OEBPS/{fname}", xhtml)

            # 표지 이미지 저장
            cover_manifest, cover_meta = "", ""
            if cover_io:
                zf.writestr("OEBPS/cover.jpg", cover_io.getvalue())
                cover_manifest = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
                cover_meta = '<meta name="cover" content="cover"/>'

            manifest_items = "".join([f'<item id="c{i}" href="ch_{i}.xhtml" media-type="application/xhtml+xml"/>\n' for i in range(len(chapters_to_include))])
            spine_items = "".join([f'<itemref idref="c{i}"/>\n' for i in range(len(chapters_to_include))])
            
            ncx_content = [
                f'<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
                f'<head><meta name="dtb:uid" content="{book_id}"/></head><docTitle><text>{html.escape(title)}</text></docTitle><navMap>'
            ]
            for i, (ch_t, _) in enumerate(chapters_to_include):
                ncx_content.append(f'<navPoint id="p{i}" playOrder="{i+1}"><navLabel><text>{html.escape(ch_t)}</text></navLabel><content src="ch_{i}.xhtml"/></navPoint>')
            ncx_content.append('</navMap></ncx>')
            zf.writestr("OEBPS/toc.ncx", "".join(ncx_content))
            
            font_item = f'<item id="f" href="fonts/{font_filename}" media-type="application/vnd.ms-opentype"/>' if embed_font else ""
            opf = f'<?xml version="1.0" encoding="utf-8"?><package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{html.escape(title)}</dc:title><dc:language>ko</dc:language><dc:identifier id="uid">{book_id}</dc:identifier>{cover_meta}</metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="s" href="style.css" media-type="text/css"/>{manifest_items}{font_item}{cover_manifest}</manifest><spine toc="ncx">{spine_items}</spine></package>'
            zf.writestr("OEBPS/content.opf", opf)

        epub_stream.seek(0)
        return epub_stream
    except Exception as e:
        st.error(f"변환 에러: {e}")
        return None

# -------------------------
# 2. UI 및 세션 관리
# -------------------------
st.set_page_config(page_title="EPUB변환기", layout="wide")
st.title("📚 EPUB변환기")

if "u_key" not in st.session_state: st.session_state.u_key = 0
if "cover_data" not in st.session_state: st.session_state.cover_data = None
if "search_results" not in st.session_state: st.session_state.search_results = []

# 사이드바 초기화 버튼
if st.sidebar.button("♻️ 모든 데이터 초기화"):
    st.session_state.u_key += 1
    st.session_state.cover_data = None
    st.session_state.search_results = []
    st.rerun()

col1, col2 = st.columns([1, 1])

with col1:
    st.header("1. 텍스트 설정")
    u_txt = st.file_uploader("TXT 파일 선택", type="txt", key=f"txt_{st.session_state.u_key}")
    
    display_title = "제목 없음"
    final_chapters = []

    if u_txt:
        b = u_txt.getvalue()
        try:
            d = from_bytes(b).best()
            t = str(d) if d else b.decode('utf-8', errors='ignore')
        except:
            t = b.decode('cp949', errors='ignore')
        
        display_title = st.text_input("책 제목", value=Path(u_txt.name).stem)
        f_type = st.selectbox("적용 폰트", ["기본 명조체", "리디바탕"])
        split_mode = st.checkbox("자동 챕터 분할", value=True)
        
        lines = t.splitlines()
        if split_mode:
            temp = []
            c_t, c_l = "시작", []
            for line in lines:
                l = line.strip()
                if not l: continue
                if re.match(r'^제\s?\d+\s?[화장편]', l) or re.match(r'^[0-9]+\.', l):
                    if c_l: temp.append((c_t, c_l))
                    c_t, c_l = l, []
                else:
                    c_l.append(html.escape(l))
            if c_l: temp.append((c_t, c_l))
            final_chapters = temp
        else:
            final_chapters = [("본문", [html.escape(l.strip()) for l in lines if l.strip()])]

with col2:
    st.header("2. 표지 설정")
    mode = st.radio("표지 소스", ["업로드", "이미지 검색"], horizontal=True)
    if mode == "업로드":
        c_file = st.file_uploader("이미지 업로드", type=["jpg", "png"], key=f"cov_{st.session_state.u_key}")
        if c_file: st.session_state.cover_data = io.BytesIO(c_file.getvalue())
    else:
        q = st.text_input("검색어 입력", value=display_title)
        if st.button("🔍 검색"):
            with DDGS() as ddgs:
                try:
                    st.session_state.search_results = [r['image'] for r in ddgs.images(q, max_results=6)]
                except: st.error("검색 서비스 일시 제한")
        
        if st.session_state.search_results:
            grid = st.columns(3)
            for i, url in enumerate(st.session_state.search_results):
                with grid[i%3]:
                    st.image(url, use_container_width=True)
                    if st.button(f"선택 {i+1}", key=f"s_{i}"):
                        r = requests.get(url, timeout=10)
                        st.session_state.cover_data = io.BytesIO(r.content)
                        st.toast("표지가 적용되었습니다.")

    if st.session_state.cover_data:
        st.image(st.session_state.cover_data, caption="현재 선택된 표지", width=120)

st.divider()

# -------------------------
# 3. 변환 및 저장
# -------------------------
if u_txt and final_chapters:
    # 파일명 안전 필터링
    safe_name = re.sub(r'[\/:*?"<>|]', '', display_title).strip() or "ebook"

    if st.button("✨ EPUB 변환 시작", type="primary", use_container_width=True):
        with st.spinner("표준 압축으로 EPUB을 생성 중입니다..."):
            data = build_epub_buffer(final_chapters, display_title, f_type, st.session_state.cover_data)
            if data:
                st.download_button(
                    label=f"📥 {safe_name}.epub 저장하기",
                    data=data,
                    file_name=f"{safe_name}.epub",
                    mime="application/epub+zip",
                    use_container_width=True
                )
                st.success("준비가 완료되었습니다!")

# 하단 후원 정보
st.markdown(
    """
    <hr style="border:0.5px solid #f0f2f6">
    <div style="text-align: center;">
        <a href="https://buymeacoffee.com/goepark" target="_blank">
            <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 40px !important; width: 145px !important;" >
        </a>
    </div>
    """,
    unsafe_allow_html=True
)