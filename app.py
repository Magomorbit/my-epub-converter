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
# 1. EPUB 생성 엔진
# -------------------------
def build_epub_buffer(chapters_to_include, title, font_type, cover_io=None):
    """EPUB 파일 생성"""
    epub_stream = io.BytesIO()
    book_id = str(uuid.uuid4())
    font_filename = "RIDIBatang.otf"
    has_font = os.path.exists(font_filename)

    css_content = f'''
    @font-face {{ font-family: 'RIDIBatang'; src: url('fonts/{font_filename}'); }}
    body {{ 
        font-family: {'"RIDIBatang", serif' if has_font and font_type == "리디바탕" else '"Batang", "Noto Serif KR", serif'};
        line-height: 1.8; 
        margin: 5% 8%; 
        text-align: justify; 
        word-break: keep-all;
        hyphens: auto;
    }}
    p {{ 
        margin-top: 0; 
        margin-bottom: 1.5em; 
        text-indent: 1em; 
    }}
    h2 {{ 
        text-align: center; 
        margin-top: 3em; 
        margin-bottom: 2em; 
        font-size: 1.4em; 
        border-bottom: 1px solid #ccc; 
        padding-bottom: 0.5em; 
    }}
    h1 {{ 
        text-align: center; 
        margin-top: 4em; 
    }}
    @media (prefers-color-scheme: dark) {{
        body {{ background: #1a1a1a; color: #e0e0e0; }}
        h2 {{ border-bottom-color: #444; }}
    }}
    '''

    with zipfile.ZipFile(epub_stream, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", 
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            '</container>')
        
        if has_font and font_type == "리디바탕":
            with open(font_filename, "rb") as f: 
                zf.writestr(f"OEBPS/fonts/{font_filename}", f.read())
        
        zf.writestr("OEBPS/style.css", css_content)

        # 챕터 청크 분할 (100줄 단위로 증가)
        processed_chunks = []
        for ch_t, ch_l in chapters_to_include:
            chunk_size = 100
            for i in range(0, len(ch_l), chunk_size):
                sub_l = ch_l[i:i+chunk_size]
                sub_t = ch_t if i == 0 else f"{ch_t} (계속)"
                processed_chunks.append((sub_t, sub_l))

        manifest_items, spine_items, nav_points = "", "", ""
        for i, (ch_t, ch_l) in enumerate(processed_chunks):
            fname = f"ch_{i}.xhtml"
            header = f"<h1>{html.escape(title)}</h1>" if i == 0 else ""
            display_title_xhtml = "" if "(계속)" in ch_t else f"<h2>{html.escape(ch_t)}</h2>"
            
            xhtml = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                '<head><link rel="stylesheet" type="text/css" href="style.css"/></head>'
                f'<body>{header}{display_title_xhtml}'
                f'{"".join([f"<p>{l}</p>" for l in ch_l])}'
                '</body></html>'
            )
            
            zf.writestr(f"OEBPS/{fname}", xhtml)
            manifest_items += f'<item id="c{i}" href="{fname}" media-type="application/xhtml+xml"/>\n'
            spine_items += f'<itemref idref="c{i}"/>\n'
            
            if "(계속)" not in ch_t:
                nav_points += f'<navPoint id="p{i}" playOrder="{i+1}"><navLabel><text>{html.escape(ch_t)}</text></navLabel><content src="{fname}"/></navPoint>'

        ncx = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{book_id}"/></head>'
            f'<docTitle><text>{html.escape(title)}</text></docTitle>'
            f'<navMap>{nav_points}</navMap></ncx>'
        )
        zf.writestr("OEBPS/toc.ncx", ncx)
        
        if cover_io:
            zf.writestr("OEBPS/cover.jpg", cover_io.getvalue())
        
        font_manifest = f'<item id="f" href="fonts/{font_filename}" media-type="application/vnd.ms-opentype"/>' if has_font and font_type == "리디바탕" else ""
        manifest_cover = '<item id="cover" href="cover.jpg" media-type="image/jpeg"/>' if cover_io else ""
        cover_tag = '<meta name="cover" content="cover"/>' if cover_io else ""
        
        opf = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{html.escape(title)}</dc:title>'
            '<dc:language>ko</dc:language>'
            f'<dc:identifier id="uid">{book_id}</dc:identifier>'
            f'{cover_tag}</metadata>'
            f'<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'<item id="s" href="style.css" media-type="text/css"/>'
            f'{manifest_items}{font_manifest}{manifest_cover}</manifest>'
            f'<spine toc="ncx">{spine_items}</spine></package>'
        )
        zf.writestr("OEBPS/content.opf", opf)

    epub_stream.seek(0)
    return epub_stream


@st.cache_data
def detect_encoding(raw_bytes):
    """파일 인코딩 감지 (캐싱)"""
    try:
        detected = from_bytes(raw_bytes).best()
        return str(detected) if detected else raw_bytes.decode('utf-8', errors='ignore')
    except:
        return raw_bytes.decode('cp949', errors='ignore')


def is_chapter_title(line):
    """챕터 제목 감지 (개선된 패턴)"""
    cl = line.strip()
    if len(cl) == 0 or len(cl) > 50:
        return False
    
    patterns = [
        r'^제\s?\d+\s?[화장회절편부권]',  # 제1화, 제1장, 제1회 등
        r'^\d+\s*[.-]\s*\S',  # 1. 제목, 1- 제목
        r'^[Chapter|CHAPTER|chapter]\s+\d+',  # Chapter 1
        r'^[EP|ep|Ep]\s*\.?\s*\d+',  # EP.1, EP 1
        r'^\[\s*\d+\s*\]',  # [1], [01]
        r'^[프롤로그|에필로그|프olog|epilogue]',  # 프롤로그, 에필로그
        r'^\d+$',  # 숫자만 (짧은 경우)
    ]
    
    for pattern in patterns:
        if re.match(pattern, cl, re.IGNORECASE):
            # 추가 검증: 날짜나 점수 같은 것 제외
            if not re.search(r'\d+\s?대\s?\d+', cl):  # "3대1" 같은 점수
                if not re.search(r'\d{4}[-./]\d{1,2}[-./]\d{1,2}', cl):  # 날짜
                    return True
    
    # 괄호로 감싸진 짧은 텍스트 (부가 체크)
    if re.match(r'^[[<].+[]>]$', cl) and len(cl) < 20:
        if not any(char in cl for char in ['.', '!', '?', '…']):
            return True
    
    return False


def is_valid_image_url(url):
    """이미지 URL 유효성 검증"""
    if not url or not isinstance(url, str):
        return False
    return (url.startswith(('http://', 'https://')) and 
            any(url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']))


# -------------------------
# 2. UI 및 메인 로직
# -------------------------
st.set_page_config(page_title="TXT to EPUB 변환기", layout="wide", page_icon="📚")

# 커스텀 CSS
st.markdown("""
    <style>
    .stButton>button {
        border-radius: 8px;
        font-weight: 600;
    }
    .success-box {
        padding: 1rem;
        border-radius: 8px;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
        margin: 1rem 0;
    }
    </style>
""", unsafe_allow_html=True)

st.title("📚 스마트 EPUB 변환기 PRO")
st.caption("TXT 소설 파일을 전문가급 EPUB 전자책으로 변환하세요")

# 세션 스테이트 초기화
if "search_results" not in st.session_state: 
    st.session_state.search_results = []
if "final_cover_io" not in st.session_state: 
    st.session_state.final_cover_io = None
if "refresh_needed" not in st.session_state: 
    st.session_state.refresh_needed = False
if "conversion_stats" not in st.session_state:
    st.session_state.conversion_stats = None

if st.session_state.refresh_needed:
    st.session_state.search_results = []
    st.session_state.final_cover_io = None
    st.session_state.refresh_needed = False
    st.rerun()

col1, col2 = st.columns([1, 1])

with col1:
    st.header("1️⃣ 설정 및 챕터 확인")
    
    u_txt = st.file_uploader("📄 TXT 파일 선택", type="txt", help="변환할 소설 파일을 선택하세요")
    
    f_exists = os.path.exists("RIDIBatang.otf")
    font_options = ["리디바탕", "기본 명조체", "고딕체"] if f_exists else ["기본 명조체", "고딕체"]
    f_type = st.selectbox("🔖 적용할 폰트 선택", font_options, help="전자책에서 사용할 폰트를 선택하세요")
    
    use_split = st.radio("📑 챕터 분할 모드", ["챕터분할 적용함", "안함"], horizontal=True, 
                         help="자동으로 챕터를 감지하여 분할할지 선택하세요")
    
    display_title = "제목 없음"
    final_chapters = []

    if u_txt:
        raw_bytes = u_txt.getvalue()
        
        # 파일 크기 체크
        file_size_mb = len(raw_bytes) / (1024 * 1024)
        if file_size_mb > 10:
            st.warning(f"⚠️ 파일이 큽니다 ({file_size_mb:.1f}MB). 처리 시간이 걸릴 수 있습니다.")
        
        with st.spinner('📖 파일 읽는 중...'):
            text = detect_encoding(raw_bytes)

        # 제목 정제 로직 강화
        raw_filename = Path(u_txt.name).stem
        clean_name = re.sub(r'[+_]', ' ', raw_filename)
        clean_name = re.sub(r'[\/:*?"<>|\\]', '', clean_name)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        
        display_title = st.text_input("📝 책 제목", value=clean_name, help="전자책에 표시될 제목을 입력하세요")
        
        raw_lines = text.splitlines()
        
        if use_split == "챕터분할 적용함":
            temp_chapters = []
            curr_t, curr_l = "시작", []
            
            for line in raw_lines:
                cl = line.strip()
                if not cl: 
                    continue
                
                if is_chapter_title(cl):
                    if curr_l: 
                        temp_chapters.append((curr_t, curr_l))
                    curr_t, curr_l = cl, []
                else: 
                    curr_l.append(html.escape(cl))
            
            if curr_l: 
                temp_chapters.append((curr_t, curr_l))

            st.write("### 📋 챕터 필터링")
            st.caption(f"총 {len(temp_chapters)}개 챕터 감지됨")
            
            selected_indices = []
            with st.container(height=300):
                for idx, (t, lines) in enumerate(temp_chapters):
                    preview = f"{t} ({len(lines)}줄)"
                    if st.checkbox(preview, value=True, key=f"ch_{idx}"):
                        selected_indices.append(idx)
            
            if temp_chapters:
                processed_ch = []
                for idx, (t, l) in enumerate(temp_chapters):
                    if idx in selected_indices: 
                        processed_ch.append([t, l])
                    else:
                        if processed_ch: 
                            processed_ch[-1][1].extend([f"[{t}]"] + l)
                        else: 
                            processed_ch.append(["본문", [f"[{t}]"] + l])
                final_chapters = processed_ch
                
                # 통계 계산
                total_lines = sum(len(c[1]) for c in final_chapters)
                st.session_state.conversion_stats = {
                    'chapters': len(final_chapters),
                    'lines': total_lines
                }
        else:
            final_chapters = [("본문", [html.escape(l.strip()) for l in raw_lines if l.strip()])]
            st.session_state.conversion_stats = {
                'chapters': 1,
                'lines': len(final_chapters[0][1])
            }
        
        # 통계 표시
        if st.session_state.conversion_stats:
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("챕터 수", f"{st.session_state.conversion_stats['chapters']}개")
            with col_b:
                st.metric("총 줄 수", f"{st.session_state.conversion_stats['lines']:,}줄")
    else:
        display_title = st.text_input("📝 책 제목", value="제목 없음")

with col2:
    st.header("2️⃣ 표지 선택")
    
    cover_mode = st.radio("🎨 표지 획득 방법", ["이미지 업로드", "이미지 검색"], horizontal=True)
    
    if cover_mode == "이미지 업로드":
        u_cover = st.file_uploader("🖼️ 표지 이미지 선택", type=["jpg", "jpeg", "png", "webp"], 
                                   help="전자책 표지로 사용할 이미지를 업로드하세요")
        if u_cover:
            st.session_state.final_cover_io = io.BytesIO(u_cover.getvalue())
            st.image(u_cover, caption="✅ 선택된 표지", width=200)
    else:
        search_q = st.text_input("🔍 검색어", value=f"{display_title} 소설 표지", 
                                help="표지 이미지를 검색할 키워드를 입력하세요")
        
        if st.button("🔎 이미지 검색", use_container_width=True, type="primary"):
            with st.spinner('검색 중...'):
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.images(search_q, max_results=6))
                        st.session_state.search_results = [r['image'] for r in results if is_valid_image_url(r.get('image'))]
                    
                    if st.session_state.search_results:
                        st.success(f"✅ {len(st.session_state.search_results)}개 이미지 발견!")
                    else:
                        st.warning("검색 결과가 없습니다. 다른 키워드를 시도해보세요.")
                except Exception as e:
                    st.error(f"⚠️ 검색 실패: {str(e)}")
                    st.info("💡 VPN 사용 중이라면 비활성화 후 재시도하세요.")
        
        if st.session_state.search_results:
            st.divider()
            st.write("#### 검색 결과")
            grid = st.columns(3)
            
            for i, url in enumerate(st.session_state.search_results):
                with grid[i % 3]:
                    try:
                        st.image(url, use_container_width=True)
                        if st.button(f"✓ {i+1}번 선택", key=f"btn_{i}", use_container_width=True):
                            with st.spinner('이미지 다운로드 중...'):
                                r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                                if r.status_code == 200:
                                    st.session_state.final_cover_io = io.BytesIO(r.content)
                                    st.toast("✅ 이미지 선택 완료!", icon="✅")
                                    st.rerun()
                                else:
                                    st.error("이미지 로드 실패")
                    except Exception as e:
                        st.caption(f"⚠️ 이미지 로드 실패")
        
        if st.session_state.final_cover_io:
            st.divider()
            st.write("#### 최종 선택 표지")
            st.image(st.session_state.final_cover_io, caption="✅ 적용될 표지", width=200)

st.divider()

# -------------------------
# 3. 안전한 다운로드 섹션
# -------------------------
if u_txt and final_chapters:
    # 파일명 안전성 확보
    safe_filename = re.sub(r'[\/:*?"<>|\\]', '', display_title)
    safe_filename = safe_filename[:50].strip()
    if not safe_filename: 
        safe_filename = "converted_ebook"

    def trigger_refresh():
        st.session_state.refresh_needed = True

    st.write("### 📥 변환 및 다운로드")
    
    with st.spinner('📚 EPUB 파일 생성 중...'):
        try:
            epub_buffer = build_epub_buffer(final_chapters, display_title, f_type, st.session_state.final_cover_io)
            epub_size_mb = len(epub_buffer.getvalue()) / (1024 * 1024)
            
            st.success(f"✅ EPUB 파일 생성 완료! (파일 크기: {epub_size_mb:.2f}MB)")
            
            st.download_button(
                label="💾 EPUB 다운로드",
                data=epub_buffer,
                file_name=f"{safe_filename}.epub",
                mime="application/epub+zip",
                type="primary",
                use_container_width=True,
                on_click=trigger_refresh
            )
            
            # 변환 정보
            with st.expander("ℹ️ 변환 정보"):
                st.write(f"- **제목**: {display_title}")
                st.write(f"- **챕터 수**: {st.session_state.conversion_stats['chapters']}개")
                st.write(f"- **총 줄 수**: {st.session_state.conversion_stats['lines']:,}줄")
                st.write(f"- **폰트**: {f_type}")
                st.write(f"- **표지**: {'있음' if st.session_state.final_cover_io else '없음'}")
                
        except Exception as e:
            st.error(f"❌ EPUB 생성 실패: {str(e)}")
            st.info("파일이 손상되었거나 형식이 올바르지 않을 수 있습니다.")

elif u_txt and not final_chapters:
    st.warning("⚠️ 변환할 내용이 없습니다. 파일을 확인해주세요.")

# 후원 배너
st.markdown("---")
st.markdown(
    """
    <div style="text-align: center; padding: 2rem 0;">
        <p style="color: #666; font-size: 0.95em; margin-bottom: 1rem;">
            이 도구가 도움이 되셨나요? ☕
        </p>
        <a href="https://buymeacoffee.com/goepark" target="_blank">
            <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" 
                 alt="Buy Me A Coffee" 
                 style="height: 50px !important; width: 180px !important; border-radius: 8px;" >
        </a>
        <p style="color: #999; font-size: 0.85em; margin-top: 1rem;">
            Made with ❤️ by Streamlit & Claude
        </p>
    </div>
    """,
    unsafe_allow_html=True
)
