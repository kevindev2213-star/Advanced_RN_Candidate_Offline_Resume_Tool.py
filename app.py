# Advanced_RN_Candidate_Offline_Resume_Tool.py
"""
Complete replacement — Recruiter-focused resume processor with:
- Fast parallel parsing of PDF/DOCX/DOC/ZIP
- Whole-word boolean matching (so 'RN' does not match 'RNN')
- Per-resume keyword counts, highlighted snippets
- Candidate Detail viewer that updates when you click the candidate's View button
- Optional JD input (paste text or upload) — computes per-resume JD match score
- Interactive charts (matplotlib) that update with the filtered results
- CSV / Excel / ZIP export
Notes:
- Run locally: `streamlit run Advanced_RN_Candidate_Offline_Resume_Tool.py`
- Requires: pdfminer.six, python-docx or docx2txt, pandas, streamlit, xlsxwriter (optional)
"""
import streamlit as st
import zipfile
import io
import os
import re
import pandas as pd
import tempfile
from pdfminer.high_level import extract_text
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import matplotlib.pyplot as plt
from collections import Counter

st.set_page_config(page_title="Advanced Local Resume Processor — Recruiter Edition", layout="wide")

# ---------------------- Config ----------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\-\(\) \.]{7,}\d")
LOCATION_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+),\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY)\b"
)
DEFAULT_CREDENTIALS = ["RN", "R.N.", "LPN", "LVN", "CNA", "RBT", "LCSW", "BSN", "MSN"]

# ---------------------- File extraction ----------------------
def extract_from_docx_bytes(doc_bytes: bytes) -> str:
    try:
        import docx2txt
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(doc_bytes)
            tmp_path = tmp.name
        try:
            text = docx2txt.process(tmp_path) or ""
        except Exception:
            text = ""
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return text
    except Exception:
        try:
            from docx import Document
            bio = io.BytesIO(doc_bytes)
            doc = Document(bio)
            paragraphs = [p.text for p in doc.paragraphs]
            return "\n".join(paragraphs)
        except Exception:
            return ""

def extract_from_pdf_bytes(pdf_bytes: bytes) -> Dict:
    result = {"text": "", "emails": [], "phones": [], "name": None, "location": None}
    try:
        text = extract_text(io.BytesIO(pdf_bytes)) or ""
    except Exception:
        text = ""
    result['text'] = text
    result['emails'] = list(dict.fromkeys(EMAIL_RE.findall(text)))
    result['phones'] = list(dict.fromkeys([re.sub(r"\s+", " ", p).strip() for p in PHONE_RE.findall(text)]))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[:8]:
        if 1 < len(ln.split()) <= 5 and re.search(r"[A-Za-z]", ln):
            result['name'] = ln
            break
    m = LOCATION_RE.search(text)
    if m:
        result['location'] = m.group(0)
    return result

def parse_zip(zip_bytes: bytes, base_name: str = None) -> List[Dict]:
    entries = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                low = name.lower()
                if low.endswith('.pdf') or low.endswith('.docx') or low.endswith('.doc'):
                    try:
                        data = z.read(name)
                    except Exception:
                        continue
                    if low.endswith('.pdf'):
                        parsed = extract_from_pdf_bytes(data)
                    else:
                        parsed_text = extract_from_docx_bytes(data)
                        parsed = {'text': parsed_text,
                                  'emails': list(dict.fromkeys(EMAIL_RE.findall(parsed_text))),
                                  'phones': list(dict.fromkeys([re.sub(r"\s+", " ", p).strip() for p in PHONE_RE.findall(parsed_text)]))}
                    entries.append({
                        'file': f"{base_name or 'zip'}::{name}",
                        'name': parsed.get('name'),
                        'emails': parsed.get('emails'),
                        'phones': parsed.get('phones'),
                        'location': parsed.get('location'),
                        'text': parsed.get('text')
                    })
    except Exception:
        pass
    return entries

def parse_folder(path: str) -> List[Dict]:
    entries = []
    for root, dirs, files in os.walk(path):
        for fname in files:
            low = fname.lower()
            if low.endswith('.pdf') or low.endswith('.docx') or low.endswith('.doc'):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'rb') as f:
                        data = f.read()
                except Exception:
                    continue
                if low.endswith('.pdf'):
                    parsed = extract_from_pdf_bytes(data)
                else:
                    txt = extract_from_docx_bytes(data)
                    parsed = {'text': txt,
                              'emails': list(dict.fromkeys(EMAIL_RE.findall(txt))),
                              'phones': list(dict.fromkeys([re.sub(r"\s+", " ", p).strip() for p in PHONE_RE.findall(txt)]))}
                rel_root = os.path.relpath(root, path)
                folder_loc = rel_root if rel_root != '.' else None
                entries.append({
                    'file': fpath,
                    'name': parsed.get('name'),
                    'emails': parsed.get('emails'),
                    'phones': parsed.get('phones'),
                    'location': parsed.get('location') or folder_loc,
                    'text': parsed.get('text')
                })
    return entries

# ---------------------- Whole-word/boolean helpers ----------------------
def make_word_pattern(term: str, whole_word: bool = True) -> str:
    """Return regex pattern for literal term. When whole_word=True, use negative lookbehind/lookahead."""
    if not term:
        return ''
    escaped = re.escape(term)
    if whole_word:
        return r'(?<!\w)' + escaped + r'(?!\w)'
    return escaped

def count_term_occurrences(text: str, term: str, whole_word: bool = True) -> int:
    if not text or not term:
        return 0
    pat = make_word_pattern(term, whole_word=whole_word)
    return len(re.findall(pat, text, flags=re.IGNORECASE))

def find_snippets(text: str, term: str, radius: int = 60, max_snips: int = 3, whole_word: bool = True) -> List[str]:
    out = []
    if not text or not term:
        return out
    pat = make_word_pattern(term, whole_word=whole_word)
    for m in re.finditer(pat, text, flags=re.IGNORECASE):
        if len(out) >= max_snips:
            break
        start = max(0, m.start() - radius)
        end = min(len(text), m.end() + radius)
        sn = text[start:end].replace('\n', ' ')
        highlighted = re.sub(pat, lambda mo: f"**{mo.group(0)}**", sn, flags=re.IGNORECASE)
        out.append(highlighted)
    return out

def extract_query_terms(query: str) -> List[str]:
    """Extract literal tokens/phrases from boolean query (remove operators)."""
    if not query or not query.strip():
        return []
    tokens = re.findall(r'\".*?\"|[^\s()]+', query, flags=re.IGNORECASE)
    terms = []
    for t in tokens:
        up = t.upper()
        if up in ("AND", "OR", "NOT", "TO"):
            continue
        if t.startswith('"') and t.endswith('"'):
            terms.append(t.strip('"'))
        else:
            cleaned = re.sub(r'[^\w\-\/\.]', '', t)
            if cleaned:
                terms.append(cleaned)
    # dedupe preserving order
    seen = set()
    out = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out

def eval_boolean_expression(text: str, query: str, whole_word: bool = True) -> bool:
    """Boolean evaluator using whole-word matching for terms."""
    if not query or not query.strip():
        return True
    tokens = re.findall(r'\(|\)|\".*?\"|AND|OR|NOT|[^\s()]+', query, flags=re.IGNORECASE)
    tokens = [t for t in tokens if t.strip()]
    def term_value(tok: str) -> bool:
        if tok.startswith('"') and tok.endswith('"'):
            phrase = tok.strip('"')
            pat = make_word_pattern(phrase, whole_word=whole_word)
            return re.search(pat, text, flags=re.IGNORECASE) is not None
        pat = make_word_pattern(tok, whole_word=whole_word)
        return re.search(pat, text, flags=re.IGNORECASE) is not None
    idx = 0
    def parse_factor():
        nonlocal idx
        if idx >= len(tokens): return False
        tok = tokens[idx]
        if tok.upper() == 'NOT':
            idx += 1
            return not parse_factor()
        if tok == '(':
            idx += 1
            val = parse_expr()
            if idx < len(tokens) and tokens[idx] == ')': idx += 1
            return val
        idx += 1
        return term_value(tok)
    def parse_term():
        nonlocal idx
        val = parse_factor()
        while idx < len(tokens) and tokens[idx].upper() == 'AND':
            idx += 1
            val = val and parse_factor()
        return val
    def parse_expr():
        nonlocal idx
        val = parse_term()
        while idx < len(tokens) and tokens[idx].upper() == 'OR':
            idx += 1
            val = val or parse_term()
        return val
    try:
        idx = 0
        return parse_expr()
    except Exception:
        return False

# ---------------------- Experience & credentials ----------------------
def estimate_years_experience(text: str) -> int:
    if not text:
        return 0
    m = re.findall(r'(\d{1,2})\+?\s+years', text, flags=re.IGNORECASE)
    if m:
        nums = [int(x) for x in m]
        return max(nums)
    yrs = re.findall(r'(19|20)\d{2}', text)
    yrs = sorted(set(int(y) for y in yrs))
    if len(yrs) >= 2:
        return max(yrs) - min(yrs)
    return 0

def extract_credentials(text: str, credentials_list: List[str] = None, whole_word: bool = True) -> Dict[str,int]:
    """
    Return a mapping credential -> occurrence count.
    If credentials_list is None, uses DEFAULT_CREDENTIALS.
    """
    if credentials_list is None:
        credentials_list = DEFAULT_CREDENTIALS
    out = {}
    for cred in credentials_list:
        out[cred] = count_term_occurrences(text, cred, whole_word=whole_word)
    return out

# ---------------------- Parse single file pipeline ----------------------
def parse_single_file(name: str, content: bytes) -> Dict:
    low = name.lower()
    try:
        if low.endswith('.pdf'):
            parsed = extract_from_pdf_bytes(content)
        else:
            txt = extract_from_docx_bytes(content)
            parsed = {'text': txt,
                      'emails': list(dict.fromkeys(EMAIL_RE.findall(txt))),
                      'phones': list(dict.fromkeys([re.sub(r"\s+", " ", p).strip() for p in PHONE_RE.findall(txt)]))}
        return {'file': name, 'name': parsed.get('name'), 'emails': parsed.get('emails'),
                'phones': parsed.get('phones'), 'location': parsed.get('location'), 'text': parsed.get('text')}
    except Exception as e:
        return {'file': name, 'error': str(e)}

# ---------------------- UI ----------------------
st.title("Advanced Local Resume Processor — Recruiter Edition")
st.markdown("Upload PDF/DOCX/DOC/ZIP or provide a local folder path. Use boolean search + custom keywords. Click **View** on any candidate to load details.")

# Left inputs and right actions
left, right = st.columns([2,1])
with left:
    uploaded = st.file_uploader("Upload resumes (PDF/DOCX/DOC/ZIP)", accept_multiple_files=True, type=['pdf','docx','doc','zip'])
    folder_path = st.text_input("Local folder path (absolute) — optional")
    custom_keywords = st.text_area("Additional keywords (one per line)", value="RN\nLPN\nCNA\nRBT")
    boolean_query = st.text_input("Boolean search (e.g., psych AND RN AND NOT LPN)", value="RN AND psych")
    title_filter = st.text_input("Title substring filter (optional)")
    location_filter = st.text_input("Location filter (city or STATE)")
    min_matches = st.number_input("Minimum total keyword matches", min_value=0, value=0)
    top_n = st.number_input("Top N results to show (0 = all)", min_value=0, value=0)
    show_snippets = st.checkbox("Show snippets", value=True)
    use_session_cache = st.checkbox("Cache parsed results for session (faster re-query)", value=True)
    whole_word = st.checkbox("Match whole words only (avoid partial matches like 'RNN' matching 'RN')", value=True)
    # JD input area
    st.markdown("### Optional: Job Description (JD) — paste text or upload file")
    jd_text = st.text_area("Paste JD text here (optional)", height=120)
    jd_file = st.file_uploader("Or upload a JD text / docx / pdf (optional)", type=['pdf','docx','doc','txt'], accept_multiple_files=False)

with right:
    st.markdown("**Actions**")
    parse_btn = st.button("Parse / Index Resumes")
    clear_cache = st.button("Clear Session Cache")
    st.markdown("---")
    st.markdown("**Exports**")
    export_csv_btn = st.button("Export current table to CSV")
    export_excel_btn = st.button("Export to Excel (.xlsx)")
    export_zip_btn = st.button("Download selected resumes as ZIP")

# session init
if 'parsed_entries' not in st.session_state:
    st.session_state['parsed_entries'] = []
if 'parsed_index_time' not in st.session_state:
    st.session_state['parsed_index_time'] = None
if 'selected_file' not in st.session_state:
    st.session_state['selected_file'] = None
if 'orig_upload_map' not in st.session_state:
    st.session_state['orig_upload_map'] = {}  # filename -> binary (for zip export)

# clear cache
if clear_cache:
    st.session_state['parsed_entries'] = []
    st.session_state['parsed_index_time'] = None
    st.session_state['orig_upload_map'] = {}
    st.session_state['selected_file'] = None
    st.success("Session cache cleared")

# Parse action
if parse_btn:
    progress = st.progress(0)
    file_objs = []
    # collect uploads
    if uploaded:
        for f in uploaded:
            data = f.read()
            file_objs.append((f.name, data))
            # store original binary for possible zip export
            st.session_state['orig_upload_map'][f.name] = data
    # collect folder files
    if folder_path:
        if os.path.exists(folder_path):
            st.info("Scanning folder...")
            for root, dirs, files in os.walk(folder_path):
                for fname in files:
                    low = fname.lower()
                    if low.endswith('.pdf') or low.endswith('.docx') or low.endswith('.doc'):
                        fpath = os.path.join(root, fname)
                        try:
                            with open(fpath, 'rb') as fh:
                                data = fh.read()
                            file_objs.append((fpath, data))
                        except Exception:
                            continue
        else:
            st.error("Provided folder path does not exist on this machine.")
    # expand zips
    expanded = []
    for name, data in file_objs:
        if name.lower().endswith('.zip'):
            zip_entries = parse_zip(data, base_name=name)
            for ze in zip_entries:
                expanded.append((ze['file'], ze['text'].encode('utf-8') if ze.get('text') else b''))
        else:
            expanded.append((name, data))
    if not expanded:
        st.warning("No files to parse. Upload or provide folder path.")
    else:
        results = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(parse_single_file, name, data): name for name, data in expanded}
            completed = 0
            for fut in as_completed(futures):
                completed += 1
                try:
                    r = fut.result()
                except Exception as e:
                    r = {'file': futures[fut], 'error': str(e)}
                results.append(r)
                progress.progress(int(completed / len(futures) * 100))
        st.session_state['parsed_entries'] = results
        st.session_state['parsed_index_time'] = datetime.datetime.utcnow().isoformat()
        st.success(f"Parsed {len(results)} files and indexed in session cache.")

# If JD file uploaded, try to extract text (small helper)
def load_jd_from_file(jdf) -> str:
    if not jdf:
        return ""
    raw = jdf.read()
    low = jdf.name.lower()
    if low.endswith('.pdf'):
        parsed = extract_from_pdf_bytes(raw)
        return parsed.get('text') or ""
    elif low.endswith('.doc') or low.endswith('.docx'):
        return extract_from_docx_bytes(raw)
    else:
        try:
            return raw.decode('utf-8', errors='ignore')
        except Exception:
            return ""

# compute JD text final
jd_text_final = jd_text.strip()
if jd_file and not jd_text_final:
    jd_text_final = load_jd_from_file(jd_file)

# Build DataFrame if parsed
entries = st.session_state.get('parsed_entries', [])
if entries:
    # keywords
    custom_terms = [t.strip() for t in custom_keywords.splitlines() if t.strip()]
    boolean_terms = extract_query_terms(boolean_query)
    query_terms = list(dict.fromkeys(custom_terms + boolean_terms))

    # JD terms (for scoring)
    jd_terms = extract_query_terms(jd_text_final) if jd_text_final else []

    rows = []
    # Construct the credential list that will be searched for each resume:
    # combine defaults with any custom terms provided by user (de-duped)
    combined_creds = []
    for c in DEFAULT_CREDENTIALS + custom_terms:
        key = c.strip()
        if key and key.lower() not in [x.lower() for x in combined_creds]:
            combined_creds.append(key)

    for e in entries:
        if 'error' in e:
            continue
        text = e.get('text') or ""
        # use whole-word matching for credentials and counts; credentials_list defaults to DEFAULT_CREDENTIALS if None
        credentials_counts = extract_credentials(text, credentials_list=combined_creds, whole_word=whole_word)
        # estimate years
        exp = estimate_years_experience(text)
        # match counts
        match_counts = {t: count_term_occurrences(text, t, whole_word=whole_word) for t in query_terms}
        total_matches = sum(match_counts.values())
        # top terms (only show terms with count > 0)
        top_terms = sorted(match_counts.items(), key=lambda x: -x[1])[:6]
        # snippets
        snippet_map = {}
        if show_snippets and total_matches > 0:
            for t, c in match_counts.items():
                if c > 0:
                    snippet_map[t] = find_snippets(text, t, whole_word=whole_word)
        # JD scoring: simple normalized match count (Jaccard-like)
        jd_matches = 0
        if jd_terms:
            jd_matches = sum([count_term_occurrences(text, jt, whole_word=whole_word) for jt in jd_terms])
            # Normalize by JD length (tokens) to form a relative score
            jd_score = round(jd_matches / max(1, len(jd_terms)), 3)
        else:
            jd_score = None

        rows.append({
            'File': e.get('file'),
            'Name': e.get('name'),
            'Email': ', '.join(e.get('emails') or []),
            'Phone': ', '.join(e.get('phones') or []),
            'Location': e.get('location'),
            'YearsEst': exp,
            'Total_Matches': total_matches,
            'Top_Terms': ", ".join([f"{t}:{c}" for t, c in top_terms if c > 0]),
            'Credentials': ", ".join([f"{k}({v})" for k, v in credentials_counts.items() if v > 0]),
            'Snippets': snippet_map,
            'Text': text,
            'JD_Score': jd_score or 0.0
        })

    df = pd.DataFrame(rows)

    # Filters
    if title_filter:
        df = df[df['Text'].str.contains(title_filter, case=False, na=False) | df['File'].str.contains(title_filter, case=False, na=False)]
    if location_filter:
        df = df[df['Location'].str.contains(location_filter, case=False, na=False) | df['Text'].str.contains(location_filter, case=False, na=False)]
    if boolean_query:
        mask = df['Text'].apply(lambda t: eval_boolean_expression(t or '', boolean_query, whole_word=whole_word))
        df = df[mask]
    if min_matches > 0:
        df = df[df['Total_Matches'] >= int(min_matches)]
    df = df.sort_values(by=['Total_Matches', 'YearsEst', 'JD_Score'], ascending=[False, False, False]).reset_index(drop=True)
    if top_n and top_n > 0:
        df = df.head(int(top_n))

    st.success(f"Showing {len(df)} candidates (indexed at {st.session_state.get('parsed_index_time')})")

    # Show table (without the full text) + provide View buttons per row (so clicking View selects that resume)
    display_cols = ['File', 'Name', 'Email', 'Phone', 'Location', 'YearsEst', 'Total_Matches', 'Top_Terms', 'Credentials', 'JD_Score']
    display_df = df[display_cols].copy()
    # render table
    st.dataframe(display_df, height=300)

    # Create a clickable list (View button) for each visible candidate (limit to 200 to avoid UI explosion)
    st.markdown("### Quick Actions — click **View** to open candidate details")
    max_show = min(len(df), 200)
    for i in range(max_show):
        row = df.iloc[i]
        col1, col2, col3 = st.columns([4, 12, 2])
        with col1:
            st.write(f"**{row['Name'] or row['File']}**")
        with col2:
            st.write(f"{row['Top_Terms']} — {row['Credentials']}")
        with col3:
            # unique button key
            btn_key = f"view_btn_{i}_{hash(row['File'])}"
            if st.button("View", key=btn_key):
                st.session_state['selected_file'] = row['File']

    # If user selected via button or previously, show details for that selection
    selected = st.session_state.get('selected_file')
    if selected:
        # find matching row
        if selected in df['File'].values:
            sel_row = df[df['File'] == selected].iloc[0]
        else:
            # fallback: if selected not present (filtered out), clear selection
            st.warning("Selected resume no longer in current filtered set — clearing selection.")
            st.session_state['selected_file'] = None
            sel_row = None

        if sel_row is not None:
            st.markdown('---')
            st.subheader('Candidate Detail & Actions')
            st.markdown(f"**File:** {sel_row['File']}")
            st.markdown(f"**Name:** {sel_row['Name']}")
            st.markdown(f"**Email:** {sel_row['Email']}")
            st.markdown(f"**Phone:** {sel_row['Phone']}")
            st.markdown(f"**Location:** {sel_row['Location']}")
            st.markdown(f"**Estimated Years Experience:** {sel_row['YearsEst']}")
            st.markdown(f"**Credentials:** {sel_row['Credentials']}")
            st.markdown(f"**Total Matches:** {sel_row['Total_Matches']}")
            st.markdown(f"**JD Score:** {sel_row['JD_Score']:.3f}" if sel_row['JD_Score'] is not None else "**JD Score:** N/A")
            st.markdown('**Top matched terms:**')
            st.write(sel_row['Top_Terms'] or "None")
            if show_snippets and sel_row['Snippets']:
                st.markdown('**Snippets (first matches):**')
                for k, v in sel_row['Snippets'].items():
                    for s in v:
                        st.markdown(f"- **{k}**: {s}")
            st.markdown('**Full extracted text:**')
            st.text_area('Full text', value=sel_row['Text'], height=400)

            # Candidate-level exports
            st.markdown("#### Candidate Actions")
            if st.button("Copy candidate details to clipboard (Text)"):
                # prepare a text blob
                blob = f"Name: {sel_row['Name']}\nEmail: {sel_row['Email']}\nPhone: {sel_row['Phone']}\nLocation: {sel_row['Location']}\nCredentials: {sel_row['Credentials']}\nJD_Score: {sel_row['JD_Score']}\n\nResume text:\n{sel_row['Text']}"
                st.write("Candidate details (copy from box):")
                st.code(blob)
            if st.button("Download candidate text file"):
                fname = os.path.basename(sel_row['File'])
                data = sel_row['Text'].encode('utf-8')
                st.download_button("Download .txt", data=data, file_name=f"{fname}.txt", mime='text/plain')

    # Exports for table
    if export_csv_btn:
        out = df.drop(columns=['Text', 'Snippets'])
        csv = out.to_csv(index=False).encode('utf-8')
        st.download_button('Download CSV', data=csv, file_name='candidates.csv', mime='text/csv')

    if export_excel_btn:
        out = df.drop(columns=['Text', 'Snippets'])
        with io.BytesIO() as buffer:
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                out.to_excel(writer, index=False, sheet_name='candidates')
                # writer.save() not required; context manager will save
            data = buffer.getvalue()
        st.download_button('Download Excel', data=data, file_name='candidates.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    if export_zip_btn:
        selected_for_zip = st.multiselect('Select files to include in ZIP (choose from displayed results)', options=df['File'].tolist())
        if st.button('Create ZIP of selected'):
            if not selected_for_zip:
                st.warning('No files selected')
            else:
                memory_zip = io.BytesIO()
                with zipfile.ZipFile(memory_zip, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                    for fname in selected_for_zip:
                        # try original uploaded binary
                        orig = st.session_state['orig_upload_map'].get(fname)
                        if orig:
                            zf.writestr(os.path.basename(fname), orig)
                        else:
                            # fallback: write text extracted
                            found = next((p for p in st.session_state['parsed_entries'] if p.get('file') == fname), None)
                            if found and found.get('text'):
                                zf.writestr(os.path.basename(fname) + '.txt', found.get('text'))
                memory_zip.seek(0)
                st.download_button('Download ZIP of selected', data=memory_zip.getvalue(), file_name='selected_resumes.zip', mime='application/zip')

    # ---------------- Charts ----------------
    st.markdown('---')
    st.subheader('Profile Analytics (charts update for current filtered set)')
    chart_col1, chart_col2, chart_col3 = st.columns([1,1,1])

    # Credential frequency bar chart
    with chart_col1:
        st.markdown("**Credential counts**")
        # aggregate credential occurrences across filtered df
        cred_counter = Counter()
        for txt in df['Text']:
            for cred in combined_creds:
                cnt = count_term_occurrences(txt, cred, whole_word=whole_word)
                if cnt > 0:
                    cred_counter[cred] += cnt
        if cred_counter:
            creds, vals = zip(*cred_counter.most_common())
            fig, ax = plt.subplots(figsize=(4,3))
            ax.bar(creds, vals)
            ax.set_ylabel("Occurrences")
            ax.set_xticklabels(creds, rotation=45, ha='right')
            st.pyplot(fig)
        else:
            st.info("No credentials found in current set.")

    # Years of experience histogram
    with chart_col2:
        st.markdown("**Years of experience distribution**")
        years = df['YearsEst'].fillna(0).astype(int).tolist()
        if years and sum(years) > 0:
            fig2, ax2 = plt.subplots(figsize=(4,3))
            ax2.hist(years, bins=range(0, max(years)+3, 2))
            ax2.set_xlabel("Years")
            ax2.set_ylabel("Count")
            st.pyplot(fig2)
        else:
            st.info("No experience data available in current set.")

    # Location pie/top counts
    with chart_col3:
        st.markdown("**Top locations**")
        locs = df['Location'].fillna('Unknown').tolist()
        loc_counter = Counter(locs)
        most_common = loc_counter.most_common(8)
        if most_common:
            labels, sizes = zip(*most_common)
            fig3, ax3 = plt.subplots(figsize=(4,3))
            ax3.pie(sizes, labels=labels, autopct='%1.0f%%', startangle=140)
            ax3.axis('equal')
            st.pyplot(fig3)
        else:
            st.info("No location data.")

else:
    st.info('No parsed resumes in session. Click "Parse / Index Resumes" after uploading or using a folder path.')

# Footer
st.markdown('---')
st.write('Local processing only — resumes remain on your machine. For big archives, run this app locally and enable session caching.')

