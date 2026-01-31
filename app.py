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
from PIL import Image  # 이미지 최적화용 (pip install Pillow 필요)

# -------------------------
# 1. EPUB 생성 엔진 (용량 최적화 버전)
# -------------------------
def build_epub_buffer(chapters_to_include, title, font_type, cover_io=None):
    try:
        epub_stream = io.BytesIO()
        book_id = str(uuid.uuid4())
        font_filename = "RIDIBatang.otf"
        
        # 폰트 포함 여부 결정 (용량 절감 핵심)
        embed_font = (font_type == "리디바탕" and os.path.exists(font_filename))

        css_content = f'''
        @font-face {{ font-family: 'RIDIBatang'; src: url('fonts/{font_filename}'); }}
        body {{ 
            font-family: {'"RIDIBatang", serif' if embed_font else 'serif'};
            line-height: 1.6; margin: 5%; text-align: justify;
        }}
        p {{ margin: 0.8em 0; text-indent: 1em; }}
        h1, h2 {{ text-align: center; }}
        '''

        # [중요] compression=zipfile.ZIP_DEFLATED를 사용하여 내부 파일 압축
        with zipfile.ZipFile(epub_stream, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # mimetype은 반드시 압축 없이(STORED) 처음에 위치해야 함
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
                content_html = "".join(f"<p>{line}</p>" for line in ch_l)
                
                xhtml = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" type="text/css" href="style.css"/></head><body>{header}{display_title_xhtml}{content_html}</body></html>'
                zf.writestr(f"OEBPS/{fname}", xhtml)

            # 표지 이미지 최적화 (해상도 조절 및 압축)
            cover_manifest, cover_meta = "", ""
            if cover_io:
                try:
                    img = Image.open(cover_io)
                    if img.mode != 'RGB': img = img.convert('RGB')
                    img.thumbnail((800, 1200)) # 해상도 최적화
                    opt_cover = io.BytesIO()
                    img.save(opt_cover, format="JPEG", quality=75, optimize=True)
                    
                    zf.writestr("OEBPS/cover.jpg", opt_cover.getvalue())
                    cover_manifest = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
                    cover_meta = '<meta name="cover" content="cover"/>'
                except:
                    st.warning("표지 이미지 최적화 실패. 원본을 사용합니다.")
                    zf.writestr("OEBPS/cover.jpg", cover_io.getvalue())
                    cover_manifest = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'

            manifest_items = "".join([f'<item id="c{i}" href="ch_{i}.xhtml" media-type="application/xhtml+xml"/>\n' for i in range(len(chapters_to_include))])
            spine_items = "".join([f'<itemref idref="c{i}"/>\n' for i in range(len(chapters_to_include))])
            
            ncx = f'<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{book_id}"/></head><docTitle><text>{title}</text></docTitle><navMap>'
            for i, (ch_t, _) in enumerate(chapters_to_include):
                ncx += f'<navPoint id="p{i}" playOrder="{i+1}"><navLabel><text>{ch_t}</text></navLabel><content src="ch_{i}.xhtml"/></navPoint>'
            ncx += '</navMap></ncx>'
            zf.writestr("OEBPS/toc.ncx", ncx)
            
            font_item = f'<item id="f" href="fonts/{font_filename}" media-type="application/vnd.ms-opentype"/>' if embed_font else ""
            opf = f'<?xml version="1.0" encoding="utf-8"?><package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{html.escape(title)}</dc:title><dc:language>ko</dc:language><dc:identifier id="uid">{book_id}</dc:identifier>{cover_meta}</metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="s" href="style.css" media-type="text/css"/>{manifest_items}{font_item}{cover_manifest}</manifest><spine toc="ncx">{spine_items}</spine></package>'
            zf.writestr("OEBPS/content.opf", opf)

        epub_stream.seek(0)
        return epub_stream
    except Exception as e:
        st.error(f"생성 중 에러 발생: {e}")
        return None

# -------------------------
# 2. UI 로직 및 세션 관리
# -------------------------
st.set_page_config(page_title="EPUB Optimizer", layout="wide")
st.title("📚 초경량 EPUB 변환기 PRO")

if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0
if "final_cover_io" not in st.session_state: st.session_state.final_cover_io = None
if "search_results" not in st.session_state: st.session_state.search_results = []

# 초기화 기능 (파일 목록까지 완전 삭제)
if st.sidebar.button("♻️ 전체 초기화 (새 작업 시작)"):
    st.session_state.uploader_key += 1
    st.session_state.final_cover_io = None
    st.session_state.search_results = []
    st.rerun()

col1, col2 = st.columns([1, 1])

with col1:
    st.header("1. 파일 설정")
    u_txt = st.file_uploader("TXT 파일 선택", type="txt", key=f"up_{st.session_state.uploader_key}")
    
    display_title = "제목 없음"
    final_chapters = []

    if u_txt:
        raw_bytes = u_txt.getvalue()
        try:
            detected = from_bytes(raw_bytes).best()
            text = str(detected) if detected else raw_bytes.decode('utf-8', errors='ignore')
        except:
            text = raw_bytes.decode('cp949', errors='ignore')
        
        raw_name = Path(u_txt.name).stem
        display_title = st.text_input("책 제목", value=raw_name)
        
        f_exists = os.path.exists("RIDIBatang.otf")
        f_type = st.selectbox("📖 서체 선택 (용량 절약하려면 '기본 폰트' 권장)", 
                             ["기본 명조체", "리디바탕"] if f_exists else ["기본 명조체"])

        use_split = st.radio("챕터 분할", ["안함", "적용"], horizontal=True)
        
        lines = text.splitlines()
        if use_split == "적용":
            temp_chapters = []
            curr_t, curr_l = "시작", []
            for line in lines:
                cl = line.strip()
                if not cl: continue
                # 챕터 감지 로직 (강화됨)
                if re.match(r'^제\s?\d+\s?[화장장편절]', cl) or re.match(r'^[0-9]+\.\s?.+?(\([0-9]+\))?$', cl):
                    if curr_l: temp_chapters.append((curr_t, curr_l))
                    curr_t, curr_l = cl, []
                else:
                    curr_l.append(html.escape(cl))
            if curr_l: temp_chapters.append((curr_t, curr_l))
            final_chapters = temp_chapters
        else:
            final_chapters = [("본문", [html.escape(l.strip()) for l in lines if l.strip()])]

with col2:
    st.header("2. 표지 설정")
    cover_mode = st.radio("표지 획득", ["업로드", "검색"], horizontal=True)
    
    if cover_mode == "업로드":
        u_cover = st.file_uploader("이미지 선택", type=["jpg", "png"], key=f"cov_{st.session_state.uploader_key}")
        if u_cover:
            st.session_state.final_cover_io = io.BytesIO(u_cover.getvalue())
    else:
        search_q = st.text_input("검색어", value=f"{display_title} 소설")
        if st.button("🔍 검색"):
            with DDGS() as ddgs:
                st.session_state.search_results = [r['image'] for r in ddgs.images(search_q, max_results=6)]
        
        if st.session_state.search_results:
            cols = st.columns(3)
            for i, url in enumerate(st.session_state.search_results):
                with cols[i%3]:
                    st.image(url, use_container_width=True)
                    if st.button(f"선택 {i+1}", key=f"sel_{i}"):
                        r = requests.get(url, timeout=10)
                        st.session_state.final_cover_io = io.BytesIO(r.content)
                        st.toast("선택 완료")

    if st.session_state.final_cover_io:
        st.image(st.session_state.final_cover_io, caption="선택된 표지", width=150)

st.divider()

# -------------------------
# 3. 변환 및 다운로드 (안전한 파일명 처리)
# -------------------------
if u_txt and final_chapters:
    safe_fn = re.sub(r'[\/:*?"<>|]', '', display_title).strip()
    if not safe_fn: safe_fn = "converted_book"

    with st.spinner("최적화된 EPUB을 생성 중입니다..."):
        epub_data = build_epub_buffer(final_chapters, display_title, f_type, st.session_state.final_cover_io)
        
        if epub_data:
            st.download_button(
                label=f"💾 {safe_fn}.epub 저장하기",
                data=epub_data,
                file_name=f"{safe_fn}.epub",
                mime="application/epub+zip",
                type="primary",
                use_container_width=True
            )
            st.success("변환 완료! 버튼을 클릭하여 저장하세요.")

# 후원 배너
st.markdown(
    """
    <hr style="border:0.5px solid #f0f2f6">
    <div style="text-align: center;">
        <a href="https://buymeacoffee.com/goepark" target="_blank">
            <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 45px !important; width: 160px !important;" >
        </a>
    </div>
    """,
    unsafe_allow_html=True
)