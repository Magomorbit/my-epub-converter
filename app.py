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
from PIL import Image  # 이미지 최적화용

# -------------------------
# 1. EPUB 생성 엔진 (최적화 버전)
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

        # ZIP_DEFLATED를 사용하여 내부 파일 압축 (용량 최적화)
        with zipfile.ZipFile(epub_stream, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # mimetype은 반드시 압축 없이(STORED) 처음에 위치해야 함
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            
            zf.writestr("META-INF/container.xml", '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            
            # 폰트 파일 추가 (선택 시에만)
            if embed_font:
                with open(font_filename, "rb") as f: 
                    zf.writestr(f"OEBPS/fonts/{font_filename}", f.read())
            
            zf.writestr("OEBPS/style.css", css_content)

            # 챕터 XHTML 생성
            for i, (ch_t, ch_l) in enumerate(chapters_to_include):
                fname = f"ch_{i}.xhtml"
                header = f"<h1>{html.escape(title)}</h1>" if i == 0 else ""
                display_title_xhtml = f"<h2>{html.escape(ch_t)}</h2>"
                # 리스트 컴프리헨션 대신 제너레이터를 사용하여 메모리 효율 증대
                content_html = "".join(f"<p>{line}</p>" for line in ch_l)
                
                xhtml = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" type="text/css" href="style.css"/></head><body>{header}{display_title_xhtml}{content_html}</body></html>'
                zf.writestr(f"OEBPS/{fname}", xhtml)

            # 표지 이미지 최적화
            cover_manifest, cover_meta = "", ""
            if cover_io:
                img = Image.open(cover_io)
                if img.mode != 'RGB': img = img.convert('RGB')
                # 해상도를 최대 800px로 조절하고 압축률 높임 (용량 절감)
                img.thumbnail((800, 1200))
                opt_cover = io.BytesIO()
                img.save(opt_cover, format="JPEG", quality=75, optimize=True)
                
                zf.writestr("OEBPS/cover.jpg", opt_cover.getvalue())
                cover_manifest = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
                cover_meta = '<meta name="cover" content="cover"/>'

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
# 2. UI 로직
# -------------------------
st.set_page_config(page_title="EPUB Optimizer", layout="wide")
st.title("📚 초경량 EPUB 변환기")

if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0
if "final_cover_io" not in st.session_state: st.session_state.final_cover_io = None

# 초기화 기능
if st.sidebar.button("♻️ 전체 초기화"):
    st.session_state.uploader_key += 1
    st.session_state.final_cover_io = None
    st.rerun()

u_txt = st.file_uploader("TXT 파일 (최대 200MB 지원)", type="txt", key=f"up_{st.session_state.uploader_key}")

if u_txt:
    # 대용량 파일 처리를 위한 메모리 관리
    with st.status("파일 분석 중...", expanded=True) as status:
        raw_bytes = u_txt.getvalue()
        try:
            detected = from_bytes(raw_bytes).best()
            text = str(detected) if detected else raw_bytes.decode('utf-8', errors='ignore')
        except:
            text = raw_bytes.decode('cp949', errors='ignore')
        
        raw_name = Path(u_txt.name).stem
        title = st.text_input("책 제목", value=raw_name)
        
        f_exists = os.path.exists("RIDIBatang.otf")
        f_type = st.selectbox("📖 서체 선택 (용량 절약하려면 '기본 폰트' 권장)", 
                             ["기본 명조체", "리디바탕"] if f_exists else ["기본 명조체"])

        # 챕터 분할 (대용량 대응 최적화)
        lines = text.splitlines()
        final_chapters = [("본문 전체", [html.escape(l.strip()) for l in lines if l.strip()])]
        
        status.update(label="분석 완료! 표지를 선택하고 저장하세요.", state="complete")

    # 표지 이미지 검색/업로드 섹션 (기존과 동일하되 용량 최적화 적용됨)
    u_cover = st.file_uploader("표지 이미지", type=["jpg", "png"], key=f"cov_{st.session_state.uploader_key}")
    if u_cover:
        st.session_state.final_cover_io = io.BytesIO(u_cover.getvalue())

    if st.button("💾 최적화하여 EPUB 저장하기", type="primary", use_container_width=True):
        with st.spinner("용량 최적화 및 압축 중..."):
            result = build_epub_buffer(final_chapters, title, f_type, st.session_state.final_cover_io)
            if result:
                st.download_button(
                    label="📥 변환된 파일 다운로드",
                    data=result,
                    file_name=f"{title}_optimized.epub",
                    mime="application/epub+zip",
                    use_container_width=True
                )